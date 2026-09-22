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
import threading
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


# Reentrant per calling *thread*, not just per process: the viewer process
# now has two genuinely concurrent callers of gpu_lease() (a preload worker
# thread mid-acquire, and ThreadingHTTPServer request threads calling
# stop_preloaded_llama_server()/unload_preloaded_ollama()). A plain process
# -global counter would let one thread's held lease silently no-op another
# thread's acquire; threading.local() scopes reentrancy to the thread that
# actually holds it.
_lease_local = threading.local()


def _lease_depth() -> int:
    return getattr(_lease_local, "depth", 0)


@contextmanager
def gpu_lease(
    owner: str,
    *,
    wait_seconds: float,
    poll_seconds: float = 5.0,
    sleep=time.sleep,
    clock=time.monotonic,
):
    """OS-level, non-blocking file lock. Reentrant within the same thread.

    Raises GpuBusy(holder=...) if not acquired within wait_seconds. The lock
    is released by the OS if this process dies, so a crashed holder never
    leaves the GPU stuck.
    """

    if _lease_depth() > 0:
        _lease_local.depth += 1
        try:
            yield
        finally:
            _lease_local.depth -= 1
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
        _lease_local.depth = 1
        yield
    finally:
        _lease_local.depth = 0
        try:
            _unlock_file(handle)
        finally:
            handle.close()


# Serializes lease_state() probes within this process -- two concurrent HTTP
# requests (e.g. two browser tabs open on the status dialog) would otherwise
# contend with *each other*'s probe attempt and report a false "busy" against
# stale holder.json data, since holder.json is never cleared on release.
_lease_state_lock = threading.Lock()


def lease_state() -> dict[str, Any]:
    """Best-effort, non-blocking snapshot of whether the lease is held right now.

    holder.json alone can't answer this: it records the *last* acquirer and
    is never cleared on release. This grabs and immediately releases the lock
    file to actually test it, for diagnostic display only.

    # ponytail: a real gpu_lease() acquire in progress with wait_seconds=0
    # left at its deadline could, in a vanishingly small window, have this
    # probe's momentary hold make it miss the lock and raise GpuBusy one poll
    # early. Narrow enough (a single OS-level lock/unlock) not to be worth a
    # retry protocol; revisit if a real GpuBusy is ever traced back to it.
    """

    with _lease_state_lock:
        directory = lease_dir()
        lock_path = directory / "gpu.lock"
        if not lock_path.exists():
            return {"busy": False}
        holder = _read_holder(directory)
        try:
            handle = lock_path.open("a+b")
        except OSError:
            return {"busy": False}
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                _lock_file(handle)
            except OSError:
                return {"busy": True, **holder}
            _unlock_file(handle)
            return {"busy": False}
        finally:
            handle.close()


def _record_server(kind: str, pid: int | None, image: str | None = None) -> None:
    """Record (or clear) the pid+image of a llama-server under holder.json's
    "<kind>_server_*" keys. "managed" is a local_gpu_session()-owned server
    (stopped by whoever started it, at the end of its own session); "preloaded"
    is a warm-started one meant to outlive that (see preload_llama_server()).
    Keeping them under separate keys is what lets reap_orphan_server() (which
    only ever looks at "managed") leave a preloaded server alone.
    """

    directory = lease_dir()
    holder = _read_holder(directory)
    holder[f"{kind}_server_pid"] = pid
    holder[f"{kind}_server_image"] = None if pid is None else image
    _write_holder(directory, holder)


def record_managed_server(pid: int | None, image: str | None = None) -> None:
    """Record (or clear) the pid+image of a llama-server this machine's lease holder started.

    The image (executable basename) is what reap_orphan_server() checks before
    ever terminating a recorded pid -- a bare pid is not trustworthy evidence
    on its own, since pids get reused by unrelated processes.
    """

    _record_server("managed", pid, image)


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


def _reap_server(kind: str) -> None:
    """Terminate the "<kind>_server_*"-recorded process if it's still the same image.

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
    pid = holder.get(f"{kind}_server_pid")
    if not isinstance(pid, int):
        return
    recorded_image = holder.get(f"{kind}_server_image")
    if isinstance(recorded_image, str) and _process_alive(pid):
        current_image = _process_image(pid)
        if isinstance(current_image, str) and current_image.casefold() == recorded_image.casefold():
            _terminate_process(pid)
    _record_server(kind, None)


def has_preloaded_llama_server() -> bool:
    """True if holder.json still has a preload_llama_server() pid on record."""

    return isinstance(_read_holder(lease_dir()).get("preloaded_server_pid"), int)


def reap_orphan_server() -> None:
    """Terminate a previous lease holder's llama-server if it never cleaned up.

    Only ever looks at the "managed" (local_gpu_session()-owned) server --
    a preload_llama_server() one is deliberately recorded under a different
    key so this never mistakes it for a crashed leftover and kills it.
    """

    _reap_server("managed")


def stop_preloaded_llama_server(*, wait_seconds: float = 0.0) -> None:
    """Stop a llama-server preload_llama_server() started and left running.

    Takes the GPU lease first (like preload_llama_server()) so a real
    generation session currently borrowing this same server -- via
    managed_server()'s is_ready() reuse path, which never re-records it --
    isn't killed out from under it; raises GpuBusy if it's held.
    """

    with gpu_lease("preload:llama-server:stop", wait_seconds=wait_seconds):
        _reap_server("preloaded")


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


def _ollama_keep_alive(base_url: str, names: list[str], keep_alive: Any) -> None:
    """Best-effort POST /api/generate with no prompt -- loads (or, at keep_alive=0,
    immediately unloads) each named model without generating any text."""

    for name in names:
        try:
            request = urllib.request.Request(
                f"{base_url.rstrip('/')}/api/generate",
                data=json.dumps({"model": name, "keep_alive": keep_alive}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5):
                pass
        except (OSError, urllib.error.URLError):
            pass


def unload_ollama(base_url: str, names: list[str]) -> None:
    """Best-effort POST /api/generate keep_alive=0 for each model name."""

    _ollama_keep_alive(base_url, names, 0)


# How long a preloaded Ollama model stays warm before Ollama's own idle
# timeout would unload it again -- just "long enough to press 実行 after
# pressing 読み込んでおく", not a setting: a real generation request's own
# keep_alive (server default) takes over the moment generation starts.
PRELOAD_OLLAMA_KEEP_ALIVE = "30m"


def _clear_ollama_for_llama_server(guard: Mapping[str, Any], deadline: float) -> None:
    """Wait for Ollama to go idle (or the deadline), then unload whatever it has loaded.

    Shared by local_gpu_session()'s llama-server branch and preload_llama_server() --
    both need the same "don't steal VRAM out from under an active Ollama call" wait.
    """

    ollama_url = str(guard.get("ollama_base_url", ollama.DEFAULT_BASE_URL))
    observe_seconds = max(1.0, float(guard.get("observe_seconds", 15)))
    while ollama_is_active(ollama_url, observe_seconds=observe_seconds):
        if time.monotonic() >= deadline:
            raise GpuBusy({"owner": "ollama (in use)"})
    loaded = ollama_models(ollama_url)
    names = [model.get("name") for model in loaded if model.get("name")]
    if names:
        unload_ollama(ollama_url, names)


def preload_llama_server(output_settings: Mapping[str, Any], *, wait_seconds: float = 0.0) -> int | None:
    """Start llama-server and leave it running once /health answers, instead of
    stopping it on exit like managed_server() -- as if a person had launched it
    by hand. Recorded under the "preloaded" holder key, not "managed", so the
    next real session's reap_orphan_server() leaves it alone.

    Returns the new process's pid, or None if a server was already reachable
    (nothing to do -- not an error, same as managed_server()'s own convention).
    Raises ValueError if llama-server isn't configured for local generation,
    GpuBusy if the GPU is currently leased by someone else, and RuntimeError
    (from llama_server.wait_ready()) if the process fails to come up in time.
    """

    settings = output_settings if isinstance(output_settings, Mapping) else {}
    guard = settings.get("gpu_guard")
    if not isinstance(guard, Mapping):
        raise ValueError("gpu_guardが設定されていません")
    config = settings.get("llama-server")
    config = config if isinstance(config, Mapping) else {}
    if not llama_server.has_launch_command(config):
        raise ValueError("llama-serverの起動コマンドが設定されていません")
    if llama_server.is_ready(config):
        return None

    deadline = time.monotonic() + wait_seconds
    with gpu_lease("preload:llama-server", wait_seconds=wait_seconds):
        reap_orphan_server()
        _clear_ollama_for_llama_server(guard, deadline)
        if llama_server.is_ready(config):
            # Someone else (a real session, or a previous preload) started it
            # while we were waiting for the lease/Ollama -- nothing to do.
            return None
        process = llama_server.spawn_server(config)
        try:
            llama_server.wait_ready(config, process)
        except BaseException:
            llama_server.stop_server(process)
            raise
        launch = config["launch"]
        image = os.path.basename(launch[0])
        _record_server("preloaded", process.pid, image)
        return process.pid


def unload_preloaded_ollama(output_settings: Mapping[str, Any], *, wait_seconds: float = 0.0) -> None:
    """Unload whatever preload_ollama() (or a real Ollama call) left loaded.

    Takes the GPU lease first, same reasoning as stop_preloaded_llama_server():
    don't unload a model an active generation session is mid-call with.
    """

    settings = output_settings if isinstance(output_settings, Mapping) else {}
    guard = settings.get("gpu_guard")
    guard = guard if isinstance(guard, Mapping) else {}
    config = settings.get("ollama")
    config = config if isinstance(config, Mapping) else {}
    base_url = str(config.get("base_url") or guard.get("ollama_base_url", ollama.DEFAULT_BASE_URL))
    with gpu_lease("preload:ollama:stop", wait_seconds=wait_seconds):
        loaded = ollama_models(base_url)
        names = [model.get("name") for model in loaded if model.get("name")]
        if names:
            unload_ollama(base_url, names)


def preload_ollama(output_settings: Mapping[str, Any], *, wait_seconds: float = 0.0) -> None:
    """Load the configured Ollama model into VRAM ahead of a real generation call.

    Raises ValueError if gpu_guard isn't enabled, GpuBusy if llama-server is
    currently running (VRAM can't hold both) or the GPU lease is held.
    """

    settings = output_settings if isinstance(output_settings, Mapping) else {}
    guard = settings.get("gpu_guard")
    if not isinstance(guard, Mapping):
        raise ValueError("gpu_guardが設定されていません")
    config = settings.get("ollama")
    config = config if isinstance(config, Mapping) else {}
    model = str(config.get("model", ollama.DEFAULT_MODEL))
    base_url = str(config.get("base_url") or guard.get("ollama_base_url", ollama.DEFAULT_BASE_URL))

    with gpu_lease("preload:ollama", wait_seconds=wait_seconds):
        reap_orphan_server()
        llama_config = settings.get("llama-server")
        if isinstance(llama_config, Mapping) and llama_server.is_ready(llama_config):
            raise GpuBusy({"owner": "llama-server (running)"})
        _ollama_keep_alive(base_url, [model], PRELOAD_OLLAMA_KEEP_ALIVE)


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

    if _lease_depth() > 0:
        # An outer local_gpu_session (or a bare gpu_lease) in this same
        # thread already holds the lease and owns the server's lifecycle.
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

        _clear_ollama_for_llama_server(guard, deadline)

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
