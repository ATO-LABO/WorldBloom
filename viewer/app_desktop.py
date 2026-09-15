"""Desktop launcher for the WorldBloom sample viewer.

Wraps viewer/server.py's stdlib HTTP server in a pywebview window so the
packaged WorldBloom.exe opens directly into the app on double-click -- no
browser tab, no command line. Always read-only (never passes --control):
the reviewer's machine needs no Ollama/LLM setup to browse the bundled
sample runs.

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


def main() -> int:
    if webview is None:
        _show_error_native(
            "pywebview の読み込みに失敗しました。\n"
            "配布フォルダが壊れている可能性があります。展開し直してください。"
        )
        return 1

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
        repository = data_module.RunRepository(root / "samples")
    except Exception as exc:  # pragma: no cover - surfaced to the user, not tested
        _show_error(f"samples の読み込みに失敗しました。\n\n{exc!r}")
        return 1

    class _ReadOnlyHandler(server_module.ViewerHandler):
        """No --control means no job_store, but one POST route (selection
        toggling) still falls through to a legacy on-disk write path that
        doesn't check for it -- see viewer/data.py's _write_selection_legacy.
        Block every POST here rather than patch shared server code, so this
        build can never touch the bundled samples on a reviewer's disk."""

        def do_POST(self) -> None:
            self.send_error(403, "read-only build")

    port = _free_port()
    server = server_module.ViewerServer(("127.0.0.1", port), _ReadOnlyHandler)
    server.repository = repository

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
