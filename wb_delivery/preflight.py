"""Fail-closed runtime probing and validation against copied user definitions."""
from pathlib import Path
import json
import os
import subprocess
import sys
import uuid

from .contract import DATA_ADAPTER, digest, is_link, safe_child, strict_json
from .errors import DeliveryError

TERMINAL = {"succeeded", "partial", "failed", "cancelled", "interrupted"}
IS_WINDOWS = os.name == "nt"


class WindowsRuntimeProbe:
    def __init__(self, *, runner=subprocess.run):
        self.runner = runner

    def __call__(self, target):
        if not IS_WINDOWS:
            raise DeliveryError("runtime_platform")
        script = r"""
$ErrorActionPreference = 'Stop'
$root = $env:WB_DELIVERY_TARGET_PATH
$exe = [System.IO.Path]::Combine($root, 'WorldBloom-Studio.exe')
$items = @(Get-CimInstance Win32_Process -Filter "Name = 'WorldBloom-Studio.exe' OR Name = 'python.exe' OR Name = 'pythonw.exe' OR Name = 'py.exe'" -ErrorAction Stop | Where-Object {
  ($_.Name -eq 'WorldBloom-Studio.exe' -and (-not $_.ExecutablePath -or $_.ExecutablePath.Equals($exe,[System.StringComparison]::OrdinalIgnoreCase))) -or
  ($_.Name -ne 'WorldBloom-Studio.exe' -and $_.CommandLine -and $_.CommandLine.IndexOf($root,[System.StringComparison]::OrdinalIgnoreCase) -ge 0)
} | Select-Object Name,ProcessId)
ConvertTo-Json -InputObject $items -Compress
"""
        result = self.runner(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            env={**os.environ, "WB_DELIVERY_TARGET_PATH": str(Path(target).absolute())},
            capture_output=True, text=True, encoding="utf8", timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode != 0:
            raise DeliveryError("runtime_unknown")
        try:
            processes = strict_json(result.stdout)
        except DeliveryError as exc:
            raise DeliveryError("runtime_unknown") from exc
        if not isinstance(processes, list) or any(not isinstance(p, dict) or type(p.get("ProcessId")) is not int or not isinstance(p.get("Name"), str) for p in processes):
            raise DeliveryError("runtime_unknown")
        app = any(p["Name"] == "WorldBloom-Studio.exe" for p in processes)
        jobs = any(p["Name"] != "WorldBloom-Studio.exe" for p in processes)
        job_root = safe_child(target, "control/jobs", managed=False)
        if job_root.exists():
            for path in job_root.glob("*/job.json"):
                safe_child(target, path.relative_to(target).as_posix(), managed=False)
                try:
                    job = strict_json(path.read_bytes())
                except (OSError, DeliveryError) as exc:
                    raise DeliveryError("jobs_unknown") from exc
                if not isinstance(job, dict) or not isinstance(job.get("state"), str):
                    raise DeliveryError("jobs_unknown")
                if job["state"] not in TERMINAL:
                    jobs = True
        return {"app_running": app, "jobs_running": jobs}


VALIDATOR = r"""
import sys, json, socket
from pathlib import Path
sys.dont_write_bytecode = True
def deny(*args, **kwargs): raise RuntimeError('network disabled during data preflight')
socket.socket.connect = deny
socket.socket.connect_ex = deny
socket.create_connection = deny
try:
    sys.path.insert(0, sys.argv[1])
    from execution.library import LibraryStore
    from execution import world_editor, genre_editor
    store = LibraryStore(Path(sys.argv[2]))
    worlds = store.worlds()
    genres = store.genres()
    for w in worlds: world_editor.snapshot(store, w['id'])
    for g in genres: genre_editor.snapshot(store, g['id'])
    print(json.dumps({'valid': True, 'worlds': len(worlds), 'genres': len(genres)}))
except BaseException:
    print(json.dumps({'valid': False}))
    sys.exit(1)
"""


def _definition_hashes(root):
    result = {}
    for p in root.rglob("*"):
        if is_link(p):
            raise DeliveryError("linked_definition")
        if p.is_file():
            result[p.relative_to(root).as_posix()] = digest(p.read_bytes())
    return result


class CopiedDataValidator:
    """Candidate code reads copies, never paths to the live definitions/settings.

    This isolates accidental migrations; it is not a sandbox for malicious code.
    Only API-verified code from the owner's merged CI may reach this adapter.
    """

    def __init__(self, workspace, *, python=sys.executable, runner=subprocess.run, max_bytes=256 * 1024 * 1024):
        self.workspace = Path(workspace).absolute()
        self.python = str(python)
        self.runner = runner
        self.max_bytes = max_bytes

    def __call__(self, stage, target, manifest):
        if manifest.get("data") != {"adapter": DATA_ADAPTER, "requires_migration": False}:
            return False
        workspace = self.workspace / uuid.uuid4().hex
        for p in [workspace, *workspace.parents]:
            if is_link(p):
                raise DeliveryError("linked_validation_root")
        workspace.mkdir(parents=True, exist_ok=False)
        data = workspace / "definitions"
        data.mkdir()
        total = 0
        for name in ("projects", "templates"):
            src = safe_child(target, "app/" + name, managed=False)
            dst = data / name
            dst.mkdir()
            if not src.exists():
                continue
            for path in src.rglob("*"):
                if is_link(path):
                    raise DeliveryError("linked_definition")
                if not path.is_file():
                    continue
                rel = name + "/" + path.relative_to(src).as_posix()
                safe_child(target, "app/" + rel, managed=False)
                if path.stat().st_size > self.max_bytes - total:
                    raise DeliveryError("data_preflight_limit")
                blob = path.read_bytes()
                total += len(blob)
                if total > self.max_bytes:
                    raise DeliveryError("data_preflight_limit")
                dest = safe_child(data, rel, managed=False)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(blob)
        before = _definition_hashes(data)
        environment = {k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP"}}
        result = self.runner([self.python, "-I", "-B", "-c", VALIDATOR, str(Path(stage) / "app"), str(data)],
            cwd=workspace, env=environment, capture_output=True, text=True, encoding="utf8", timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode != 0 or _definition_hashes(data) != before:
            return False
        try:
            outcome = strict_json(result.stdout)
        except DeliveryError:
            return False
        return (isinstance(outcome, dict) and set(outcome) == {"valid", "worlds", "genres"}
                and outcome["valid"] is True and all(type(outcome[k]) is int and outcome[k] >= 0 for k in ("worlds", "genres")))
