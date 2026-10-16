"""Bounded, allowlisted evidence collected only after an HTTP test times out."""
import hashlib
import json
import math
from pathlib import Path
import time

from execution.provenance import open_atomic_read

JSON_LIMIT = 65536
EVENT_LIMIT = 16384
OUTPUT_LIMIT = 16384
STATES = frozenset("queued running stopping finishing succeeded partial failed cancelled interrupted".split())
PHASES = frozenset("queued preparing evaluating publishing finishing finished stopping cancelled".split())
NUMBERS = ("schema_version", "revision", "created_at", "updated_at", "started_at", "finished_at",
           "heartbeat", "cancel_requested_at", "exit_code", "publication_revision")
PROGRESS = ("generation", "completed_generations", "total_generations", "completed_individuals",
            "total_individuals", "completed_seeds", "total_seeds", "elapsed_seconds", "publication_revision")


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and abs(value) <= 10**15


def progress(value):
    if not isinstance(value, dict):
        return {}
    result = {k: value[k] for k in PROGRESS if number(value.get(k))}
    result["phase"] = value.get("phase") if value.get("phase") in PHASES else "other"
    seeds = value.get("active_seeds")
    if isinstance(seeds, list):
        result["active_seeds"] = [
            {k: row[k] for k in ("generation", "individual_index", "seed") if number(row.get(k))}
            for row in seeds[:8] if isinstance(row, dict)]
    return result


def snapshot(value):
    if not isinstance(value, dict):
        return {}
    result = {k: value[k] for k in NUMBERS if number(value.get(k))}
    for key, allowed in (("state", STATES), ("phase", PHASES)):
        result[key] = value.get(key) if value.get(key) in allowed else "other"
    if "progress" in value:
        result["progress"] = progress(value["progress"])
    return result


def read_object(path):
    try:
        with open_atomic_read(path) as text:
            raw = text.buffer.read(JSON_LIMIT + 1)
        if len(raw) > JSON_LIMIT:
            return {}, "size_limit"
        value = json.loads(raw)
        return (value, "read") if isinstance(value, dict) else ({}, "invalid")
    except FileNotFoundError:
        return {}, "missing"
    except (OSError, ValueError, RecursionError):
        return {}, "unreadable"


def recent_events(path):
    try:
        with open_atomic_read(path) as text:
            stream = text.buffer
            size = stream.seek(0, 2)
            offset = max(0, size - EVENT_LIMIT)
            stream.seek(offset)
            raw = stream.read(EVENT_LIMIT)
        lines = raw.splitlines()
        if offset and lines:
            lines = lines[1:]  # The bounded tail may start halfway through a row.
        rows = []
        for line in lines[-16:]:
            try:
                rows.append(snapshot(json.loads(line)))
            except (ValueError, RecursionError):
                continue  # A concurrent append may leave an incomplete last row.
        return rows, "bounded_tail" if offset else "read"
    except FileNotFoundError:
        return [], "missing"
    except OSError:
        return [], "unreadable"


def record_http_timeout(*, evidence, job_folder, run_root, last_http, http_status, probe):
    """Never reconcile/stop a job or replace the test's original assertion."""
    try:
        job, job_status = read_object(Path(job_folder) / "job.json")
        ga, ga_status = read_object(Path(run_root) / "evaluation/progress.json")
        events, events_status = recent_events(Path(job_folder) / "events.jsonl")
        processes = {}
        receipt = job.get("receipt")
        identities = {"worker": (receipt.get("identity") if isinstance(receipt, dict) else None)
                      or job.get("launch_identity"), "child": job.get("child_identity")}
        for label, identity in identities.items():
            try:
                state = probe(identity) if isinstance(identity, dict) else "unknown"
            except Exception:
                state = "unknown"
            processes[label] = state if state in ("alive", "dead", "unknown") else "unknown"
        payload = {"schema": 1, "captured_at": time.time(), "http_status": http_status if number(http_status) else None,
                   "last_http": snapshot(last_http), "disk_job": snapshot(job), "ga_progress": progress(ga),
                   "processes": processes, "recent_events": events,
                   "read_status": {"job": job_status, "ga": ga_status, "events": events_status}}
        encoded = json.dumps(payload, sort_keys=True)
        if len(encoded.encode("utf-8")) > OUTPUT_LIMIT:
            payload = {"schema": 1, "capture": "output_limit"}
            encoded = json.dumps(payload)
        if evidence:
            try:
                folder = Path(evidence) / "http-timeouts"
                folder.mkdir(parents=True, exist_ok=True)
                # Fixture-created random job IDs are used only for collision-safe filenames.
                name = hashlib.sha256(str(job_folder).encode()).hexdigest()[:16]
                (folder / (name + ".json")).write_text(encoded + "\n", encoding="utf-8")
            except OSError:
                pass  # The bounded console record still survives in full-suite.log.
        print("WB_HTTP_TIMEOUT_DIAGNOSTIC " + encoded, flush=True)
        return payload
    except Exception:
        # Diagnostic failures must not obscure the existing self.fail() below it.
        try:
            print("WB_HTTP_TIMEOUT_DIAGNOSTIC unavailable", flush=True)
        except OSError:
            pass
        return {"schema": 1, "capture": "unavailable"}
