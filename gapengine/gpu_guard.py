"""GPU lease, Ollama arbitration and thermal guard for local LLM backends.

Standard library only. Everything here is a no-op unless a caller opts in
by passing a settings.json ``output`` section that has a ``gpu_guard`` key
(see ``local_gpu_session``); nothing in this module touches the network or
the lease directory unless asked to.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

from gapengine import llama_server, ollama

DEFAULT_THERMAL = {
    "pause_at": 78,
    "resume_at": 70,
    "poll_seconds": 15,
    "max_wait_seconds": 600,
}


class GpuBusy(Exception):
    """Raised when the GPU lease or a running Ollama model cannot be freed in time."""

    def __init__(self, holder: Mapping[str, Any] | None = None) -> None:
        self.holder: dict[str, Any] = dict(holder) if holder else {}
        super().__init__(f"GPU is busy: held by {self.holder.get('owner', 'unknown')}")


def read_gpu_temperature(*, timeout: float = 5.0) -> float | None:
    """The current GPU temperature in Celsius via nvidia-smi, or None on any failure."""

    try:
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    first_line = completed.stdout.strip().splitlines()
    if not first_line:
        return None
    try:
        return float(first_line[0].strip())
    except ValueError:
        return None


def wait_until_cool(
    thermal: Mapping[str, Any] | None = None,
    *,
    read=read_gpu_temperature,
    sleep=time.sleep,
    clock=time.monotonic,
) -> float:
    """Block while the GPU is at or above pause_at. Returns seconds waited."""

    settings = {**DEFAULT_THERMAL, **(thermal or {})}
    temperature = read()
    if temperature is None or temperature < settings["pause_at"]:
        return 0.0
    start = clock()
    deadline = start + settings["max_wait_seconds"]
    while True:
        sleep(settings["poll_seconds"])
        temperature = read()
        if temperature is None or temperature <= settings["resume_at"] or clock() >= deadline:
            return clock() - start


def lease_dir() -> Path:
    """The single machine-wide directory holding the GPU lock and holder file."""

    override = os.environ.get("WORLDBLOOM_GPU_LEASE_DIR")
    if override:
        return Path(override)
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        return Path(local_appdata) / "WorldBloom"
    return Path(tempfile.gettempdir()) / "worldbloom"


def _holder_path(directory: Path) -> Path:
    return directory / "gpu.holder.json"


def _read_holder(directory: Path) -> dict[str, Any]:
    try:
        return json.loads(_holder_path(directory).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_holder(directory: Path, holder: Mapping[str, Any]) -> None:
    # Best effort: the OS lock is the source of truth, this is only for
    # GpuBusy's diagnostic message and orphan-server bookkeeping.
    try:
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / f".gpu.holder.json-{uuid.uuid4().hex}"
        temporary.write_text(json.dumps(dict(holder), ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, _holder_path(directory))
    except OSError:
        pass


def _lock_file(handle) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(handle) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle, fcntl.LOCK_UN)


# ponytail: a process-global depth counter, not thread-safe. Every caller in
# this codebase (output_worker's single-threaded candidate loop, generate_text's
# single call) takes the lease from one thread at a time. Add a threading.Lock
# around the counter if a concurrent caller shows up.
_lease_depth = 0


@contextmanager
def gpu_lease(
    owner: str,
    *,
    wait_seconds: float,
    poll_seconds: float = 5.0,
    sleep=time.sleep,
    clock=time.monotonic,
):
    """OS-level, non-blocking file lock. Reentrant within the same process.

    Raises GpuBusy(holder=...) if not acquired within wait_seconds. The lock
    is released by the OS if this process dies, so a crashed holder never
    leaves the GPU stuck.
    """

    global _lease_depth
    if _lease_depth > 0:
        _lease_depth += 1
        try:
            yield
        finally:
            _lease_depth -= 1
        return

    directory = lease_dir()
    directory.mkdir(parents=True, exist_ok=True)
    handle = (directory / "gpu.lock").open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        deadline = clock() + wait_seconds
        while True:
            handle.seek(0)
            try:
                _lock_file(handle)
                break
            except OSError:
                if clock() >= deadline:
                    raise GpuBusy(_read_holder(directory))
                sleep(poll_seconds)
    except BaseException:
        handle.close()
        raise

    holder = _read_holder(directory)
    holder.update(owner=owner, pid=os.getpid(), since=time.time())
    _write_holder(directory, holder)
    try:
        _lease_depth = 1
        yield
    finally:
        _lease_depth = 0
        try:
            _unlock_file(handle)
        finally:
            handle.close()


def record_managed_server(pid: int | None, image: str | None = None) -> None:
    """Record (or clear) the pid+image of a llama-server this machine's lease holder started.

    The image (executable basename) is what reap_orphan_server() checks before
    ever terminating a recorded pid -- a bare pid is not trustworthy evidence
    on its own, since pids get reused by unrelated processes.
    """

    directory = lease_dir()
    holder = _read_holder(directory)
    holder["managed_server_pid"] = pid
    holder["managed_server_image"] = None if pid is None else image
    _write_holder(directory, holder)


def _tasklist_row(pid: int) -> str | None:
    """The raw `tasklist /FO CSV /NH` line for pid, or None if not found/unavailable."""

    try:
        completed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    lines = completed.stdout.strip().splitlines()
    if not lines or str(pid) not in lines[0]:
        return None
    return lines[0]


def _process_alive(pid: int) -> bool:
    if pid == os.getpid():
        # Never mistake our own process for an orphan to reap.
        return True
    if os.name == "nt":
        return _tasklist_row(pid) is not None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _process_image(pid: int) -> str | None:
    """The basename of the executable currently running as pid, or None if it
    cannot be confirmed (process gone, tasklist/proc unavailable, ...)."""

    if pid == os.getpid():
        return None
    if os.name == "nt":
        row = _tasklist_row(pid)
        if row is None:
            return None
        import csv

        try:
            fields = next(csv.reader([row]))
        except (StopIteration, csv.Error):
            return None
        return fields[0] if fields else None
    try:
        return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _terminate_process(pid: int) -> None:
    if pid == os.getpid():
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        import signal

        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def reap_orphan_server() -> None:
    """Terminate a previous lease holder's llama-server if it never cleaned up.

    Only terminates when the process currently running as the recorded pid
    still has the recorded executable image -- a pid alone can be reused by
    an unrelated process, so an unconfirmed or mismatched image is never
    killed, only forgotten.

    An image match does not fully rule out pid reuse (a same-named exe that
    got the same pid would still be hit). That window is tiny and the damage
    is bounded to one llama.cpp server being stopped.
    """

    directory = lease_dir()
    holder = _read_holder(directory)
    pid = holder.get("managed_server_pid")
    if not isinstance(pid, int):
        return
    recorded_image = holder.get("managed_server_image")
    if isinstance(recorded_image, str) and _process_alive(pid):
        current_image = _process_image(pid)
        if isinstance(current_image, str) and current_image.casefold() == recorded_image.casefold():
            _terminate_process(pid)
    record_managed_server(None)


def ollama_models(base_url: str, *, timeout: float = 2.0) -> list[dict[str, Any]]:
    """The currently loaded models via GET /api/ps. [] on any failure."""

    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/api/ps", timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, ValueError):
        return []
    if not isinstance(data, Mapping):
        return []
    models = data.get("models")
    if not isinstance(models, list):
        return []
    return [model for model in models if isinstance(model, Mapping)]


def ollama_is_active(base_url: str, *, observe_seconds: float, sleep=time.sleep) -> bool:
    """True if a loaded model's expiry keeps moving (someone is actively calling it)."""

    first = ollama_models(base_url)
    if not first:
        return False
    sleep(observe_seconds)
    second = {model.get("name"): model.get("expires_at") for model in ollama_models(base_url)}
    for model in first:
        name = model.get("name")
        if name in second and second[name] != model.get("expires_at"):
            return True
    return False


def unload_ollama(base_url: str, names: list[str]) -> None:
    """Best-effort POST /api/generate keep_alive=0 for each model name."""

    for name in names:
        try:
            request = urllib.request.Request(
                f"{base_url.rstrip('/')}/api/generate",
                data=json.dumps({"model": name, "keep_alive": 0}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5):
                pass
        except (OSError, urllib.error.URLError):
            pass


@contextmanager
def local_gpu_session(
    backend: str,
    output_settings: Mapping[str, Any],
    *,
    owner: str,
    wait_seconds: float,
    on_status=None,
):
    """Lease the GPU, arbitrate with Ollama, and start/stop llama-server as needed.

    A complete no-op (no lease, no network calls) unless backend is a local
    backend AND output_settings["gpu_guard"] is a mapping.
    """

    settings = output_settings if isinstance(output_settings, Mapping) else {}
    guard = settings.get("gpu_guard")
    if backend not in ("ollama", "llama-server") or not isinstance(guard, Mapping):
        yield
        return

    if _lease_depth > 0:
        # An outer local_gpu_session (or a bare gpu_lease) in this same
        # process already holds the lease and owns the server's lifecycle.
        # Re-running reap/Ollama-arbitration/managed_server here would treat
        # the outer session's own server as an orphan and restart it on
        # every nested call (e.g. once per cell in scripts/synopsize.py).
        yield
        return

    def status(name: str) -> None:
        if on_status is not None:
            on_status(name)

    status("gpu")
    deadline = time.monotonic() + wait_seconds
    with gpu_lease(owner, wait_seconds=max(0.0, deadline - time.monotonic())):
        reap_orphan_server()

        if backend == "ollama":
            llama_config = settings.get("llama-server")
            if isinstance(llama_config, Mapping) and llama_server.is_ready(llama_config):
                raise GpuBusy({"owner": "llama-server (running)"})
            yield
            return

        ollama_url = str(guard.get("ollama_base_url", ollama.DEFAULT_BASE_URL))
        observe_seconds = max(1.0, float(guard.get("observe_seconds", 15)))
        while ollama_is_active(ollama_url, observe_seconds=observe_seconds):
            if time.monotonic() >= deadline:
                raise GpuBusy({"owner": "ollama (in use)"})
        loaded = ollama_models(ollama_url)
        names = [model.get("name") for model in loaded if model.get("name")]
        if names:
            unload_ollama(ollama_url, names)

        status("server_start")
        config = settings.get("llama-server", {})
        launch = config.get("launch") if isinstance(config, Mapping) else None
        image = (os.path.basename(launch[0])
                 if isinstance(launch, list) and launch and isinstance(launch[0], str) else None)
        try:
            with llama_server.managed_server(
                config, on_started=lambda pid: record_managed_server(pid, image=image),
            ):
                yield
        finally:
            record_managed_server(None)
