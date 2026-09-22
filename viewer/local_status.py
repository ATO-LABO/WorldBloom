"""GPU/local-AI status snapshot for the topbar's info dialog (WB-INFO-001).

Everything here is best-effort: a missing settings.json, an unreachable
server or an absent GPU all render as an "off" row rather than an error --
this view answers "what's running right now", and "nothing" is a valid
answer, not a failure.
"""

from __future__ import annotations

import time
from typing import Any, Mapping

from execution.output_settings import read_output_settings
from execution.provenance import read_json
from gapengine import gpu_guard, llama_server, ollama

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
    if is_ready(config):
        model = config.get("model") or "?"
        return {"id": "llama_server", "label": "llama-server", "value": f"起動中 — {model}", "level": "ok"}
    launch = config.get("launch")
    if isinstance(launch, list) and launch:
        value = "停止中（生成時に自動起動）"
    elif not config:
        value = "未設定"
    else:
        value = "停止中"
    return {"id": "llama_server", "label": "llama-server", "value": value, "level": "off"}


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
    base_url = str(config.get("base_url") or guard.get("ollama_base_url") or ollama.DEFAULT_BASE_URL)
    _names, reason = list_models({"base_url": base_url})
    if reason is not None:
        return {"id": "ollama", "label": "Ollama", "value": "応答なし（未起動）", "level": "off"}
    loaded = loaded_models(base_url)
    if not loaded:
        return {"id": "ollama", "label": "Ollama", "value": "起動中 — モデル未読み込み", "level": "ok"}
    running = ", ".join(str(model.get("name")) for model in loaded if model.get("name"))
    return {"id": "ollama", "label": "Ollama", "value": f"起動中 — {running} を読み込み中", "level": "ok"}


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

    return {
        "backend": backend,
        "backend_label": f"文章生成の送り先: {_BACKEND_LABELS.get(backend, backend or '未設定')}",
        "checked_at": time.strftime("%H:%M:%S"),
        "rows": rows,
    }
