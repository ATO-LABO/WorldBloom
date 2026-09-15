"""Desktop launcher for WorldBloom, built as two separate exes from this
one script (see build_exe.cmd):

- WorldBloom.exe (viewer mode): read-only, browses the bundled samples/
  (momotaro/detective/romance). No Ollama/LLM setup needed on the
  reviewer's machine; every POST is rejected so it can never touch the
  bundled samples on disk.
- WorldBloom-Studio.exe (studio mode): the real, writable app -- runs GA
  experiments and (with an LLM backend configured from the in-app
  Settings screen: Ollama locally, or a cloud API key) generates
  synopses/prose. Same --control/--runs wiring viewer/server.py's CLI
  already supports; this launcher just points it at two folders next to
  the exe instead of ones passed on a command line.

Which mode a given build runs in is decided by the exe's own filename
(see _studio_mode()), not a runtime flag -- a GUI exe launched by
double-click has no convenient way to receive one.

This module's own packages (viewer/execution/gapengine/engine) and their
data (projects/templates/samples) ship as plain files next to the exe, not
frozen inside it -- see build_exe.cmd. They are imported here via
importlib with a runtime string, not a static `import`/`from ... import`,
so PyInstaller's analyzer never bundles them; that keeps every
Path(__file__)-based lookup inside those modules resolving to the real,
on-disk app folder, exactly as when running `python viewer/server.py`
unfrozen.
"""

from __future__ import annotations

import html
import importlib
import os
import socket
import sys
import threading
from pathlib import Path

try:
    import webview
except Exception:  # pragma: no cover - defense in depth, see _show_error_native
    webview = None

APP_NAME = "WorldBloom"


def _show_error_native(message: str) -> None:
    """Last-resort error path when pywebview itself failed to import --
    _show_error() needs webview, so it can't report that particular
    failure. A native MessageBoxW has no other dependency."""

    import ctypes

    ctypes.windll.user32.MessageBoxW(0, message, APP_NAME, 0x10)


def _exe_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _looks_like_app_root(path: Path) -> bool:
    return (path / "samples").is_dir() and (path / "viewer").is_dir()


def _studio_mode() -> bool:
    """Which exe this is: decided by filename, with an env var to force it
    for development testing (a plain `python app_desktop.py` isn't frozen,
    so it has no exe filename to read)."""

    if os.environ.get("WORLDBLOOM_STUDIO") == "1":
        return True
    if getattr(sys, "frozen", False):
        return "studio" in Path(sys.executable).stem.lower()
    return False


def _baked_default_root() -> Path | None:
    """Dev-machine fallback baked in at build time (see build_exe.cmd)."""

    if not getattr(sys, "frozen", False):
        return None
    baked = Path(getattr(sys, "_MEIPASS", "")) / "default_root.txt"
    if not baked.is_file():
        return None
    try:
        text = baked.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = baked.read_text(encoding="mbcs")
    return Path(text.strip())


def resolve_app_root() -> Path | None:
    """Data root resolution order: env var -> folder next to the exe ->
    baked dev-machine checkout. Returns None if nothing looks like a real
    app folder (must contain both viewer/ and samples/)."""

    candidates: list[Path] = []
    env = os.environ.get("WORLDBLOOM_APP_ROOT")
    if env:
        candidates.append(Path(env))
    candidates.append(_exe_dir() / "app")
    baked = _baked_default_root()
    if baked is not None:
        candidates.append(baked)

    for candidate in candidates:
        if _looks_like_app_root(candidate):
            return candidate.resolve()
    return None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _show_error(message: str) -> None:
    """windowed builds have no console; report failures in a webview window."""

    body = html.escape(message).replace("\n", "<br>")
    webview.create_window(
        APP_NAME,
        html=f"<div style='font-family:sans-serif;padding:24px;line-height:1.6'>{body}</div>",
        width=760,
        height=380,
    )
    webview.start()


def _setup_viewer(root: Path, server_module, data_module):
    """Read-only mode: browse the bundled samples/, nothing else."""

    repository = data_module.RunRepository(root / "samples")

    class _ReadOnlyHandler(server_module.ViewerHandler):
        """No --control means no job_store, but one POST route (selection
        toggling) still falls through to a legacy on-disk write path that
        doesn't check for it -- see viewer/data.py's _write_selection_legacy.
        Block every POST here rather than patch shared server code, so this
        build can never touch the bundled samples on a reviewer's disk."""

        def do_POST(self) -> None:
            self.send_error(403, "read-only build")

    return _ReadOnlyHandler, repository, None, None


def _find_system_python() -> str | None:
    """GA jobs run as `<interpreter> -I -B <script> <args>` subprocesses
    (execution/jobs.py, execution/worker.py, execution/generation.py,
    execution/configs.py, execution/output_requests.py). In a frozen exe,
    sys.executable is the packaged GUI app itself -- it doesn't understand
    those flags -- so studio mode needs a real system Python and points
    every such subprocess at it via WORLDBLOOM_PYTHON (see
    execution/provenance.py's python_executable()).

    A candidate must actually work under -I (isolated mode, which also
    ignores user-site installs) with PyYAML importable and Python >= 3.11:
    shutil.which() alone would happily accept a Microsoft Store execution
    alias stub (present even with no Python installed at all) or a real but
    too-old/PyYAML-less interpreter, both of which would only surface later
    as an opaque "preparation_failed" on every GA run."""

    import shutil
    import subprocess

    probe = "import sys,yaml; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
    for name in ("python", "python3", "py"):
        found = shutil.which(name)
        if not found:
            continue
        try:
            result = subprocess.run(
                [found, "-I", "-B", "-c", probe],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except OSError:
            continue
        if result.returncode == 0:
            return found
    return None


def _setup_studio(root: Path, server_module, data_module):
    """Studio mode: the real app. Wires up the same job_store/settings_path/
    control_root triple viewer/server.py's own --control CLI flag does,
    just pointed at two writable folders next to the exe instead of ones
    passed on a command line. GA experiments need a system Python (see
    _find_system_python()); the あらすじ/本文 generation step additionally
    needs a backend picked from the in-app ⚙ 設定 screen (a local Ollama
    install, or a cloud API key)."""

    system_python = _find_system_python()
    if system_python is None:
        raise RuntimeError(
            "GA実験の実行にはPython 3.11以上（PyYAML導入済み）が別途必要です。\n\n"
            "1. https://www.python.org/downloads/ からインストール\n"
            "   （インストーラーで「Add python.exe to PATH」に必ずチェック）\n"
            "2. インストール後、コマンドプロンプトで次を実行:\n"
            "   pip install pyyaml\n\n"
            "済んだら再起動してください。"
        )
    os.environ["WORLDBLOOM_PYTHON"] = system_python

    exe_dir = _exe_dir()
    runs_root = exe_dir / "runs"
    control_root = exe_dir / "control"
    runs_root.mkdir(parents=True, exist_ok=True)
    control_root.mkdir(parents=True, exist_ok=True)

    configs_module = importlib.import_module("execution.configs")
    jobs_module = importlib.import_module("execution.jobs")
    job_store = jobs_module.JobStore(
        configs_module.ConfigStore(root, control_root, runs_root)
    )
    repository = data_module.RunRepository(
        runs_root, control_root=control_root, jobs=job_store
    )
    settings_path = root / "settings.json"
    return server_module.ViewerHandler, repository, job_store, settings_path


def main() -> int:
    if webview is None:
        _show_error_native(
            "pywebview の読み込みに失敗しました。\n"
            "配布フォルダが壊れている可能性があります。展開し直してください。"
        )
        return 1

    studio = _studio_mode()
    global APP_NAME
    if studio:
        APP_NAME = "WorldBloom Studio"

    root = resolve_app_root()
    if root is None:
        _show_error(
            f"{APP_NAME}.exe と同じフォルダに app フォルダが見つかりません。\n\n"
            "配布フォルダの構成:\n"
            f"  {APP_NAME}.exe\n"
            "  app/\n\n"
            "この2つを同じ場所に置いてから起動してください。"
        )
        return 1

    sys.path.insert(0, str(root))
    try:
        server_module = importlib.import_module("viewer.server")
        data_module = importlib.import_module("viewer.data")
    except Exception as exc:  # pragma: no cover - surfaced to the user, not tested
        _show_error(f"起動に失敗しました。\n\n{exc!r}")
        return 1

    try:
        if studio:
            handler_cls, repository, job_store, settings_path = _setup_studio(
                root, server_module, data_module
            )
        else:
            handler_cls, repository, job_store, settings_path = _setup_viewer(
                root, server_module, data_module
            )
    except RuntimeError as exc:  # anticipated, user-actionable (e.g. no system Python)
        _show_error(str(exc))
        return 1
    except Exception as exc:  # pragma: no cover - surfaced to the user, not tested
        _show_error(f"起動に失敗しました。\n\n{exc!r}")
        return 1

    port = _free_port()
    server = server_module.ViewerServer(("127.0.0.1", port), handler_cls)
    server.repository = repository
    if job_store is not None:
        server.job_store = job_store
    if settings_path is not None:
        server.settings_path = settings_path

    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.1},
        daemon=True,
    )
    thread.start()

    # ViewerServer (a TCPServer) binds and listens synchronously in its own
    # constructor above, before this thread even starts, so there is
    # nothing left here to poll for.

    webview.create_window(APP_NAME, f"http://127.0.0.1:{port}/", width=1440, height=920)
    webview.start()

    server.shutdown()
    server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
