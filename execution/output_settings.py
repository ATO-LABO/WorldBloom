"""Single-source generation settings: repo settings.json's "output" section.

WB-UI-021: run configs no longer carry their own frozen "generation" spec.
Sifting always generates with *today's* settings, resolved here and checked
again at admission time (execution/output_requests.py) against whatever the
request was built with.
"""
from __future__ import annotations

from copy import deepcopy

from execution.configs import _integer, _model, generation_availability
from execution.provenance import ConfigError, atomic_json, read_json
from gapengine.synopsis import BACKENDS

DEFAULT_BACKEND = "codex-cli"
LIMIT_FIELDS = ("max_calls", "call_timeout_seconds", "wall_seconds", "max_saved_response_bytes")


def default_limits(backend):
    return {"max_calls": 0 if backend == "none" else 1,
            # A local 9B model narrates in minutes, not seconds.
            "call_timeout_seconds": 900 if backend == "ollama" else 180,
            "wall_seconds": 3600 if backend == "ollama" else 240,
            "max_saved_response_bytes": 128000}


def _read_settings(settings_path):
    if settings_path is None:
        return {}
    try:
        settings = read_json(settings_path)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        raise ConfigError("settings", "settings.json を読めません") from error
    if not isinstance(settings, dict):
        raise ConfigError("settings", "settings.json を読めません")
    return settings


def _section(settings, backend):
    output = settings.get("output")
    value = output.get(backend) if isinstance(output, dict) else None
    return value if isinstance(value, dict) else {}


def _backend_view(settings, backend):
    """One backend's {model, limits}, defaulted and validated leniently.

    This feeds display (the /configs card) as well as resolution, so a
    malformed value quietly falls back to the default instead of failing the
    whole read -- only a broken settings.json itself (_read_settings) raises.
    """
    section = _section(settings, backend)
    try:
        model = _model(section.get("model"))
    except ConfigError:
        model = None
    raw_limits = section.get("limits")
    raw_limits = raw_limits if isinstance(raw_limits, dict) else {}
    defaults = default_limits(backend)
    limits = {}
    for key, default in defaults.items():
        minimum = 0 if key == "max_calls" and backend == "none" else 1
        try:
            limits[key] = _integer(raw_limits.get(key, default), "limits." + key, minimum)
        except ConfigError:
            limits[key] = default
    return {"model": model, "limits": limits}


def _default_backend(settings):
    output = settings.get("output")
    backend = output.get("default_backend") if isinstance(output, dict) else None
    return backend if isinstance(backend, str) and backend in BACKENDS else DEFAULT_BACKEND


def read_output_settings(settings_path):
    """Public view: never includes credentials (api_key/command/base_url/options)."""
    settings = _read_settings(settings_path)
    backend = _default_backend(settings)
    backends = {b: _backend_view(settings, b) for b in BACKENDS}
    return {"backend": backend, "model": backends[backend]["model"],
            "limits": backends[backend]["limits"], "backends": backends}


def resolve_generation(settings_path):
    """The generation an output request built *right now* must match."""
    view = read_output_settings(settings_path)
    return {"backend": view["backend"], "model": view["model"], "limits": view["limits"]}


def current_generation(settings_path):
    generation = resolve_generation(settings_path)
    return {**generation, "availability": generation_availability(generation, settings_path)}


def write_output_settings(settings_path, changes):
    if settings_path is None:
        raise ConfigError("settings", "設定ファイルの場所が未設定です")
    if not isinstance(changes, dict) or set(changes) - {"backend", "model", "limits"}:
        raise ConfigError("settings", "未対応の設定項目があります")
    settings = _read_settings(settings_path)
    output = settings.get("output")
    output = deepcopy(output) if isinstance(output, dict) else {}
    backend = changes.get("backend", _default_backend(settings))
    if not isinstance(backend, str) or backend not in BACKENDS:
        raise ConfigError("backend", "未対応の生成方式です")
    current = _backend_view(settings, backend)
    if "model" in changes:
        try:
            model = _model(changes["model"])
        except ConfigError as error:
            raise ConfigError("model", "モデル名を明示してください") from error
    else:
        model = current["model"]
    if backend == "none":
        model = None
    elif model is None:
        raise ConfigError("model", "モデルを指定してください")
    limits_in = changes.get("limits", {})
    if not isinstance(limits_in, dict) or set(limits_in) - set(LIMIT_FIELDS):
        raise ConfigError("limits", "未対応の上限項目があります")
    limits = dict(current["limits"])
    for key, value in limits_in.items():
        minimum = 0 if key == "max_calls" and backend == "none" else 1
        limits[key] = _integer(value, "limits." + key, minimum)
    section = output.get(backend)
    section = dict(section) if isinstance(section, dict) else {}
    section["model"] = model
    section["limits"] = limits
    output[backend] = section
    output["default_backend"] = backend
    settings["output"] = output
    atomic_json(settings_path, settings)
    return read_output_settings(settings_path)
