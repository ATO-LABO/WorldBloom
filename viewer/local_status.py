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
from viewer.local_models import read_loaded_models, ollama_items
from viewer.local_telemetry import number

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


def _llama_row(config: Mapping[str, Any], *, is_ready=llama_server.is_ready, list_models=None) -> dict[str, Any]:
    ready = is_ready(config)
    has_launch = llama_server.has_launch_command(config)
    model = str(config.get("model") or "モデル名未取得")
    names, reason = list_models(config) if ready and list_models else ([], "unavailable")
    names = [n for n in names if isinstance(n, str) and n]
    known = ready and reason is None and bool(names)
    models = [{"name": name, "configured": name == model,
               "state": "loaded", "placement": "unknown", "vram_bytes": None,
               "size_bytes": None, "execution": "unknown"} for name in names] if known else []
    if config and not any(m["configured"] for m in models):
        models.insert(0, {"name": model, "configured": True, "state": "unknown",
                          "placement": "unknown", "vram_bytes": None,
                          "size_bytes": None, "execution": "unknown"})
    if ready and not models:
        models.append({"name": "モデル名未取得", "configured": False, "state": "unknown",
                       "placement": "unknown", "vram_bytes": None,
                       "size_bytes": None, "execution": "unknown"})
    value = f"起動中 — {model}" if ready else (
        "停止中（生成時に自動起動）" if has_launch else ("未設定" if not config else "停止中"))
    return {"id": "llama_server", "label": "llama-server", "value": value,
            "level": "ok" if ready else "off", "ready": ready, "has_launch": has_launch,
            "server_state": "up" if ready else ("unconfigured" if not config else "down"),
            "models_known": known, "models": models}


def _ollama_row(
    guard: Mapping[str, Any], config: Mapping[str, Any], *,
    list_models=ollama.list_models, loaded_models=read_loaded_models,
) -> dict[str, Any]:
    base_url = str(config.get("base_url") or guard.get("ollama_base_url") or ollama.DEFAULT_BASE_URL)
    names, reason = list_models({"base_url": base_url})
    configured = str(config.get("model") or ollama.DEFAULT_MODEL)
    loaded = loaded_models(base_url) if reason is None else None
    items = ollama_items(names, loaded, configured, available=reason is None)
    if reason is not None:
        value, level = "応答なし（接続できません）", "off"
    elif loaded is None:
        value, level = "起動中 — モデル状態取得不可", "warn"
    elif not loaded:
        value, level = "起動中 — モデル未読み込み", "ok"
    else:
        running = ", ".join(str(m.get("name") or m.get("model")) for m in loaded if m.get("name") or m.get("model"))
        value, level = f"起動中 — {running} ロード済み", "ok"
    return {"id": "ollama", "label": "Ollama", "value": value, "level": level,
            "has_model_loaded": bool(loaded), "server_state": "up" if reason is None else "down",
            "models_known": loaded is not None, "models": items}


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
    llama_list_models=llama_server.list_models,
    ollama_list_models=ollama.list_models,
    ollama_loaded_models=read_loaded_models,
    has_preloaded_llama_server=gpu_guard.has_preloaded_llama_server,
    telemetry=None,
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

    gpu = telemetry.snapshot() if telemetry is not None else None
    if gpu is not None:
        first = next((d for d in gpu["devices"] if d["index"] == 0), {})
        temperature_read = lambda: first.get("temperature_c")
    lease = lease_state() if guard_enabled else {"busy": False}
    thermal = {**thermal,
               "pause_at": number(thermal.get("pause_at")) if number(thermal.get("pause_at")) is not None else gpu_guard.DEFAULT_THERMAL["pause_at"],
               "resume_at": number(thermal.get("resume_at")) if number(thermal.get("resume_at")) is not None else gpu_guard.DEFAULT_THERMAL["resume_at"]}
    rows = [
        _temperature_row(thermal, guard_enabled and thermal.get("enabled") is not False, read=temperature_read),
        _lease_row(guard, enabled=guard_enabled, state=lambda: lease),
        _llama_row(llama_config, is_ready=llama_is_ready, list_models=llama_list_models),
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
        if row["id"] in ("llama_server", "ollama"):
            configured_model = next((m for m in row["models"] if m["configured"]), None)
            row["can_preload_selected"] = (row["can_preload"] or (
                guard_enabled and row["in_use"] and row["id"] == "ollama" and not starting
                and row["models_known"] and configured_model is not None
                and configured_model["state"] == "unloaded"))
            if row["id"] == "ollama":
                row["can_preload_selected"] = bool(row["can_preload_selected"] and row["models_known"]
                    and configured_model and configured_model["state"] == "unloaded")
            for model in row["models"]:
                model["selected"] = row["in_use"] and model["configured"]
                if model["configured"] and row["id"] == starting:
                    model["state"] = "loading"
                elif (model["configured"] and preload_state["error"] and
                      row["id"] == _ROW_ID_FOR_BACKEND.get(preload_state["backend"]) and model["state"] != "loaded"):
                    model["state"] = "error"
            row["actions_disabled"] = bool(lease.get("busy") or preload_state["phase"] == "starting"
                                           or (row["id"] == "ollama" and not row["models_known"])
                                           or (row["id"] == "llama_server" and row["ready"] and not row["models_known"]))
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
    result["lease"] = {"enabled": guard_enabled, "busy": bool(lease.get("busy")), "owner": lease.get("owner")}
    result["thermal"] = {"enabled": guard_enabled and thermal.get("enabled") is not False,
                         "pause_at": thermal["pause_at"], "resume_at": thermal["resume_at"], "gpu_index": 0}
    result["checked_at_epoch"] = time.time()
    if gpu is not None:
        telemetry.record_context(busy=bool(lease.get("busy")) and str(lease.get("owner", "")).startswith("output:"),
                                 preloading=preload_state["phase"] == "starting", error=preload_state["error"])
        gpu["events"] = telemetry.snapshot(start=False)["events"]
        result["gpu"] = gpu
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
