"""Read-only GPU telemetry and bounded, server-owned history (AIT-91)."""
from __future__ import annotations

from collections import deque
from copy import deepcopy
import csv
import io
import math
import os
import subprocess
import threading
import time


def number(value, *, maximum=None):
    """N/A, non-finite and impossible counters stay missing, never zero."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result) or result < 0 or (maximum is not None and result > maximum):
        return None
    return result


def parse_gpu_csv(text):
    devices = []
    seen = set()
    for row in csv.reader(io.StringIO(text), skipinitialspace=True):
        if len(row) != 7:
            continue
        index, uuid, name, temperature, utilization, used, total = (part.strip() for part in row)
        if not index.isdigit() or not uuid or uuid in seen:
            continue
        seen.add(uuid)
        memory_total = number(total)
        memory_used = number(used, maximum=memory_total)
        if not memory_total:
            memory_total = None
        devices.append({
            "id": uuid, "index": int(index), "name": name or f"GPU {index}",
            "temperature_c": number(temperature, maximum=150),
            "utilization_percent": number(utilization, maximum=100),
            "memory_used_mib": memory_used, "memory_total_mib": memory_total,
        })
    return sorted(devices, key=lambda d: d["index"])


def read_gpu_metrics(*, timeout=3.0, run=subprocess.run):
    try:
        result = run(
            ["nvidia-smi", "--query-gpu=index,uuid,name,temperature.gpu,utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, errors="replace", timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"devices": [], "reason": "unavailable"}
    if result.returncode != 0:
        return {"devices": [], "reason": "unavailable"}
    devices = parse_gpu_csv(result.stdout)
    return {"devices": devices, "reason": None if devices else "unavailable"}


class TelemetryMonitor:
    """Lazy reader; no model operations, files, or process-global background tasks."""

    def __init__(self, *, read=None, clock=time.time, interval=2.0, retention=3600.0):
        self.read = read or read_gpu_metrics
        self.clock = clock
        self.interval = interval
        self.retention = retention
        self._lock = threading.Lock()
        self._sample_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._started = False
        self._closed = False
        self._started_at = None
        self._history = deque(maxlen=max(2, int(retention / interval) + 2))
        self._events = deque(maxlen=100)
        self._context = None

    def sample(self):
        with self._sample_lock:
            try:
                reading = self.read()
            except Exception:
                reading = {"devices": [], "reason": "unavailable"}
            at = self.clock()
            reading = deepcopy(reading)
            reading["at"] = at
            with self._lock:
                if self._history and at < self._history[-1]["at"]:
                    self._history.clear()
                    self._events.clear()
                    self._started_at = at
                if self._started_at is None:
                    self._started_at = at
                self._history.append(reading)
                while self._history and self._history[0]["at"] < at - self.retention:
                    self._history.popleft()
                while self._events and self._events[0]["at"] < at - self.retention:
                    self._events.popleft()
            return reading

    def _run(self):
        while not self._stop.wait(self.interval):
            self.sample()

    def start(self):
        with self._lock:
            if self._started or self._closed:
                return
            self._started = True
        self.sample()
        with self._lock:
            if self._closed:
                return
            self._thread = threading.Thread(target=self._run, daemon=True, name="wb-gpu-telemetry")
            self._thread.start()

    def snapshot(self, *, start=True):
        if start:
            self.start()
        with self._lock:
            history = deepcopy(list(self._history))
            events = deepcopy(list(self._events))
            started_at = self._started_at
        latest = history[-1] if history else {"devices": [], "at": None, "reason": "collecting"}
        return {**deepcopy(latest), "started_at": started_at, "interval_seconds": self.interval,
                "retention_seconds": self.retention, "history": history, "events": events}

    def record_context(self, *, busy=False, preloading=False, error=None):
        """Observed app state transitions, not inferred GPU/model activity."""
        state = (bool(busy), bool(preloading))
        with self._lock:
            before = self._context
            self._context = state
            if before is None:
                return
            labels = []
            if state[0] != before[0]:
                labels.append("WorldBloom処理開始" if state[0] else "WorldBloom処理終了")
            if state[1] != before[1]:
                labels.append("モデルロード開始" if state[1] else ("ロード失敗" if error else "ロード操作終了"))
            for label in labels:
                self._events.append({"at": self.clock(), "label": label})

    def close(self):
        with self._lock:
            self._closed = True
            thread = self._thread
        self._stop.set()
        if thread and thread is not threading.current_thread():
            thread.join(timeout=4.0)
