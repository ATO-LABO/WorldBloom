"""GPU/local-AI status snapshot for the topbar's info dialog (WB-INFO-001).

Everything here is best-effort: a missing settings.json, an unreachable
server or an absent GPU all render as an "off" row rather than an error --
this view answers "what's running right now", and "nothing" is a valid
answer, not a failure.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Mapping

from execution.output_settings import read_output_settings
from execution.provenance import read_json
from gapengine import gpu_guard, llama_server, ollama

# Which row a preload/unload targets, keyed by the resolved backend name
# (settings.json's default_backend), since VRAM can't hold both at once --
# there's only ever one local backend worth warming up at a time.
_ROW_ID_FOR_BACKEND = {"llama-server": "llama_server", "ollama": "ollama"}

# In-memory only: a preload survives exactly as long as the viewer process
# that started it, same as the server it warms up (see gpu_guard.preload_llama_server()).
# Guarded by _preload_lock since ThreadingHTTPServer can call start_preload()/
# snapshot() from different request threads at once.
_preload_lock = threading.Lock()
_preload_state: dict[str, Any] = {"phase": "idle", "backend": None, "error": None}

_BACKEND_LABELS = {
    "llama-server": "llama-server",
    "ollama": "Ollama",
    "claude-cli": "クラウド（claude-cli）— GPUは使いません",
    "codex-cli": "クラウド（codex-cli）— GPUは使いません",
    "anthropic": "クラウド（anthropic）— GPUは使いません",
    "openai": "クラウド（openai）— GPUは使いません",
    "none": "未設定",
}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _temperature_row(
    thermal: Mapping[str, Any], guard_enabled: bool, *, read=gpu_guard.read_gpu_temperature
) -> dict[str, Any]:
    pause_at = thermal.get("pause_at", gpu_guard.DEFAULT_THERMAL["pause_at"])
    resume_at = thermal.get("resume_at", gpu_guard.DEFAULT_THERMAL["resume_at"])
    temperature = read()
    if temperature is None:
        return {"id": "gpu_temp", "label": "GPU温度",
                "value": "取得できません（NVIDIA GPU / nvidia-smi なし）", "level": "off"}
    if temperature >= pause_at:
        # Only local_gpu_session() (gpu_guard enabled) actually pauses generation
        # on heat -- without it this number is informational only.
        value = (f"{temperature:.0f}℃ 高温 — 生成は一時停止されます（{pause_at}℃で停止）" if guard_enabled
                  else f"{temperature:.0f}℃ 高温（{pause_at}℃、自動停止は無効）")
        level = "warn"
    elif temperature >= resume_at:
        value = (f"{temperature:.0f}℃ やや高温（{resume_at}℃未満で再開）" if guard_enabled
                  else f"{temperature:.0f}℃ やや高温")
        level = "warn"
    else:
        value, level = f"{temperature:.0f}℃ 通常", "ok"
    return {"id": "gpu_temp", "label": "GPU温度", "value": value, "level": level}


def _lease_row(guard: Mapping[str, Any], *, enabled: bool, state=gpu_guard.lease_state) -> dict[str, Any]:
    if not enabled:
        return {"id": "gpu_lease", "label": "GPUの使用",
                "value": "調停なし（設定の gpu_guard が無効）", "level": "off"}
    info = state()
    if not info.get("busy"):
        return {"id": "gpu_lease", "label": "GPUの使用", "value": "空き", "level": "ok"}
    owner = info.get("owner") or "unknown"
    since = info.get("since")
    elapsed = ""
    if isinstance(since, (int, float)):
        minutes = max(0, int((time.time() - since) / 60))
        elapsed = f"（{minutes}分前から）" if minutes else "（たった今から）"
    return {"id": "gpu_lease", "label": "GPUの使用", "value": f"使用中: {owner}{elapsed}", "level": "warn"}


def _llama_row(config: Mapping[str, Any], *, is_ready=llama_server.is_ready) -> dict[str, Any]:
    # Probed at its (possibly default) base_url even with no explicit config,
    # same as _ollama_row -- a server already listening there is real signal.
    # "ready"/"has_launch" are internal-only: snapshot() uses them to gate the
    # preload/unload buttons and strips them before the row goes out as JSON.
    ready = is_ready(config)
    has_launch = llama_server.has_launch_command(config)
    if ready:
        model = config.get("model") or "?"
        return {"id": "llama_server", "label": "llama-server", "value": f"起動中 — {model}", "level": "ok",
                "ready": True, "has_launch": has_launch}
    if has_launch:
        value = "停止中（生成時に自動起動）"
    elif not config:
        value = "未設定"
    else:
        value = "停止中"
    return {"id": "llama_server", "label": "llama-server", "value": value, "level": "off",
            "ready": False, "has_launch": has_launch}


def _ollama_row(
    guard: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    list_models=ollama.list_models,
    loaded_models=gpu_guard.ollama_models,
) -> dict[str, Any]:
    # The base_url that actually matters here is the one generation itself
    # would use (config.base_url, per gapengine/ollama.py); gpu_guard's
    # ollama_base_url is only for arbitration and is a fallback for it.
    # "has_model_loaded" is internal-only, same reason as _llama_row's "ready".
    base_url = str(config.get("base_url") or guard.get("ollama_base_url") or ollama.DEFAULT_BASE_URL)
    _names, reason = list_models({"base_url": base_url})
    if reason is not None:
        return {"id": "ollama", "label": "Ollama", "value": "応答なし（未起動）", "level": "off",
                "has_model_loaded": False}
    loaded = loaded_models(base_url)
    if not loaded:
        return {"id": "ollama", "label": "Ollama", "value": "起動中 — モデル未読み込み", "level": "ok",
                "has_model_loaded": False}
    running = ", ".join(str(model.get("name")) for model in loaded if model.get("name"))
    return {"id": "ollama", "label": "Ollama", "value": f"起動中 — {running} を読み込み中", "level": "ok",
            "has_model_loaded": True}


def _read_settings(settings_path) -> Mapping[str, Any]:
    if settings_path is None:
        return {}
    try:
        settings = read_json(settings_path)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return {}
    return settings if isinstance(settings, Mapping) else {}


def _resolved_backend(settings_path) -> str | None:
    if settings_path is None:
        return None
    try:
        return read_output_settings(settings_path)["backend"]
    except (OSError, ValueError):
        return None


def snapshot(
    settings_path,
    *,
    temperature_read=gpu_guard.read_gpu_temperature,
    lease_state=gpu_guard.lease_state,
    llama_is_ready=llama_server.is_ready,
    ollama_list_models=ollama.list_models,
    ollama_loaded_models=gpu_guard.ollama_models,
    has_preloaded_llama_server=gpu_guard.has_preloaded_llama_server,
) -> dict[str, Any]:
    """A diagnostic snapshot for the topbar's GPU/AI status dialog."""

    settings = _read_settings(settings_path)
    output = _mapping(settings.get("output"))
    raw_guard = output.get("gpu_guard")
    # isinstance(..., Mapping), not truthiness -- an empty {"gpu_guard": {}}
    # still enables the guard (see local_gpu_session()), same as a non-empty one.
    guard_enabled = isinstance(raw_guard, Mapping)
    guard = raw_guard if guard_enabled else {}
    thermal = _mapping(guard.get("thermal"))
    llama_config = _mapping(output.get("llama-server"))
    ollama_config = _mapping(output.get("ollama"))
    backend = _resolved_backend(settings_path)

    with _preload_lock:
        preload_state = dict(_preload_state)

    rows = [
        _temperature_row(thermal, guard_enabled, read=temperature_read),
        _lease_row(guard, enabled=guard_enabled, state=lease_state),
        _llama_row(llama_config, is_ready=llama_is_ready),
        _ollama_row(guard, ollama_config, list_models=ollama_list_models, loaded_models=ollama_loaded_models),
    ]
    for row in rows:
        row["in_use"] = (
            (row["id"] == "llama_server" and backend == "llama-server")
            or (row["id"] == "ollama" and backend == "ollama")
        )

    starting = preload_state["phase"] == "starting" and _ROW_ID_FOR_BACKEND.get(preload_state["backend"])
    for row in rows:
        row["can_preload"] = False
        row["can_unload"] = False
        # Preloading only ever targets the row for the currently-configured
        # backend -- VRAM can't hold both, so warming the other one would
        # just fail with GpuBusy the moment this one is in use. Unloading is
        # NOT gated on in_use: a config change after preloading must not
        # strand the release button on a row that's no longer "in use"
        # (review M1) -- whatever this app actually warmed up stays releasable.
        if row["id"] == "llama_server":
            if row["id"] == starting and not row["ready"]:
                row["value"] = "起動中…（モデル読み込み中、最長180秒）"
                row["level"] = "warn"
            if row["in_use"]:
                row["can_preload"] = guard_enabled and row["has_launch"] and not row["ready"] and not starting
            row["can_unload"] = guard_enabled and row["ready"] and has_preloaded_llama_server()
        elif row["id"] == "ollama":
            # has_model_loaded, not level != "ok" -- level is "ok" both when
            # idle (nothing loaded, the state we DO want to offer preload for)
            # and when a model is already loaded (review H1).
            if row["id"] == starting and not row["has_model_loaded"]:
                row["value"] = "起動中…（モデル読み込み中）"
                row["level"] = "warn"
            if row["in_use"]:
                row["can_preload"] = guard_enabled and not row["has_model_loaded"] and not starting
            row["can_unload"] = guard_enabled and bool(row["has_model_loaded"])
        row.pop("ready", None)
        row.pop("has_launch", None)
        row.pop("has_model_loaded", None)

    result = {
        "backend": backend,
        "backend_label": f"文章生成の送り先: {_BACKEND_LABELS.get(backend, backend or '未設定')}",
        "checked_at": time.strftime("%H:%M:%S"),
        "rows": rows,
        # So the client can decide whether to keep polling without parsing
        # the (Japanese, display-only) row text.
        "preloading": preload_state["phase"] == "starting",
    }
    if preload_state["error"]:
        result["preload_error"] = preload_state["error"]
    return result


def start_preload(settings_path) -> dict[str, Any]:
    """Kick off a background warm-up of the currently configured local backend.

    Non-blocking: returns immediately once a background thread is dispatched;
    snapshot()'s "starting" row reflects progress. Raises ValueError if the
    current backend isn't local, or a preload is already in progress.
    """

    settings = _read_settings(settings_path)
    output = _mapping(settings.get("output"))
    backend = _resolved_backend(settings_path)
    if backend not in _ROW_ID_FOR_BACKEND:
        raise ValueError("ローカルのバックエンドではありません")

    with _preload_lock:
        if _preload_state["phase"] == "starting":
            raise ValueError("すでに読み込み中です")
        _preload_state["phase"] = "starting"
        _preload_state["backend"] = backend
        _preload_state["error"] = None

    def worker() -> None:
        try:
            if backend == "llama-server":
                gpu_guard.preload_llama_server(output)
            else:
                gpu_guard.preload_ollama(output)
        except Exception as error:  # GpuBusy, ValueError, RuntimeError -- surfaced to the dialog, not raised here
            with _preload_lock:
                _preload_state["error"] = str(error)
        finally:
            with _preload_lock:
                _preload_state["phase"] = "idle"

    try:
        threading.Thread(target=worker, daemon=True).start()
    except Exception:
        # If the thread itself never got to start, nothing will ever flip
        # phase back to idle -- don't leave every future preload rejected.
        with _preload_lock:
            _preload_state["phase"] = "idle"
        raise
    return {"backend": backend}


def stop_preload(settings_path, backend: str) -> dict[str, Any]:
    """Stop/unload the given backend's warmed-up model.

    Takes an explicit target rather than inferring it from the currently
    configured backend: a config change after preloading (e.g. llama-server
    -> ollama) must not strand the release button with no way to free what
    this app actually warmed up (review M1). The caller (the dialog row's own
    "解放する" button) already knows which row -- and thus which backend --
    it belongs to.
    """

    if backend not in _ROW_ID_FOR_BACKEND:
        raise ValueError("ローカルのバックエンドではありません")
    with _preload_lock:
        if _preload_state["error"]:
            _preload_state["error"] = None
    if backend == "llama-server":
        gpu_guard.stop_preloaded_llama_server()
    else:
        settings = _read_settings(settings_path)
        output = _mapping(settings.get("output"))
        gpu_guard.unload_preloaded_ollama(output)
    return {"backend": backend}
