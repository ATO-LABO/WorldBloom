"""Single-source generation settings: repo settings.json's "output" section.

WB-UI-021: run configs no longer carry their own frozen "generation" spec.
Sifting always generates with *today's* settings, resolved here and checked
again at admission time (execution/output_requests.py) against whatever the
request was built with.
"""
from __future__ import annotations

from copy import deepcopy
import json
import urllib.error
import urllib.request

from execution.configs import _integer, _model, generation_availability
from execution.provenance import ConfigError, atomic_json, read_json
from gapengine.synopsis import BACKENDS

DEFAULT_BACKEND = "codex-cli"
LIMIT_FIELDS = ("max_calls", "call_timeout_seconds", "wall_seconds", "max_saved_response_bytes")
VERIFIED_MODELS_LIMIT = 20

# generation_availability() only checks that an api_key string is non-empty --
# cheap enough to run on every /configs render and every admission check. A
# 疎通テスト click is rare and explicit, so it can afford one real read-only
# call to confirm the *exact model name* is actually visible to that key.
_MODEL_LIST_ENDPOINTS = {
    "anthropic": ("https://api.anthropic.com/v1/models",
                  lambda key: {"x-api-key": key, "anthropic-version": "2023-06-01"}),
    "openai": ("https://api.openai.com/v1/models",
               lambda key: {"Authorization": f"Bearer {key}"}),
}

# The only backends that authenticate with a bare api_key -- the /configs
# card shows its APIキー field for exactly these.
API_KEY_BACKENDS = frozenset(_MODEL_LIST_ENDPOINTS)


def _fetch_remote_models(backend, section, *, timeout=6.0):
    """anthropic/openai's own model list for this api_key. (names, reason)."""
    url, headers_for = _MODEL_LIST_ENDPOINTS[backend]
    key = str(section.get("api_key", "")).strip()
    if not key:
        return [], "credentials_missing"
    request = urllib.request.Request(url, headers=headers_for(key))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, ValueError):
        return [], "server_unreachable"
    names = sorted({entry.get("id") for entry in data.get("data", []) if isinstance(entry, dict) and entry.get("id")})
    return names, None


def _model_reachable(backend, section, model, *, timeout=6.0):
    """Real round trip for anthropic/openai: is `model` in this key's model list?

    codex-cli/claude-cli already get a genuine check (the executable exists);
    verifying their exact model name would mean actually invoking the CLI,
    which costs real time/tokens -- out of scope for a quick test button.
    Ollama's own generation_availability() probe already lists real models.
    """
    if backend not in _MODEL_LIST_ENDPOINTS:
        return True, None
    names, reason = _fetch_remote_models(backend, section, timeout=timeout)
    if reason is not None:
        return False, reason
    return (model in names), (None if model in names else "model_missing")


def list_models(settings_path, backend):
    """Models actually selectable for `backend` right now.

    ollama/anthropic/openai have a real "list models" API, so this is a live
    catalog (live=True) from the configured server/api_key. codex-cli and
    claude-cli have no such API -- there is no way to enumerate what models
    they accept short of invoking the CLI itself, so this falls back to
    whatever 疎通テスト has already confirmed (live=False). "none" needs no
    model at all.
    """
    if not isinstance(backend, str) or backend not in BACKENDS:
        raise ConfigError("backend", "未対応の生成方式です")
    section = _section(_read_settings(settings_path), backend)
    if backend == "ollama":
        from gapengine import ollama
        names, reason = ollama.list_models(section)
        return {"models": names, "live": True, "reason": reason}
    if backend in _MODEL_LIST_ENDPOINTS:
        names, reason = _fetch_remote_models(backend, section)
        return {"models": names, "live": True, "reason": reason}
    return {"models": _verified_models(section), "live": False, "reason": None}


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


def _verified_models(section):
    """Models a 疎通テスト has actually confirmed reachable, most-recent first."""
    raw = section.get("verified_models")
    if not isinstance(raw, list):
        return []
    models = []
    for item in raw:
        if isinstance(item, str) and item.strip() and item not in models:
            models.append(item.strip())
    return models[:VERIFIED_MODELS_LIMIT]


def _backend_view(settings, backend):
    """One backend's {model, limits, verified_models}, defaulted and validated leniently.

    This feeds display (the /configs card) as well as resolution, so a
    malformed value quietly falls back to the default instead of failing the
    whole read -- only a broken settings.json itself (_read_settings) raises.
    """
    section = _section(settings, backend)
    try:
        model = _model(section.get("model"))
    except ConfigError:
        model = None
    if model is None and backend == "claude-cli":
        # Keep in sync with gapengine/synopsis.py's own claude-cli fallback --
        # this is what actually gets used once nothing is configured, so the
        # /configs card should show it rather than an empty field.
        model = "claude-sonnet-5"
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
    has_api_key = bool(str(section.get("api_key", "")).strip())
    return {"model": model, "limits": limits, "verified_models": _verified_models(section),
            "has_api_key": has_api_key}


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


def _remember_verified_model(settings_path, backend, model):
    settings = _read_settings(settings_path)
    output = settings.get("output")
    output = deepcopy(output) if isinstance(output, dict) else {}
    section = output.get(backend)
    section = dict(section) if isinstance(section, dict) else {}
    models = _verified_models(section)
    if model in models:
        models.remove(model)
    section["verified_models"] = [model] + models[:VERIFIED_MODELS_LIMIT - 1]
    output[backend] = section
    settings["output"] = output
    atomic_json(settings_path, settings)


def write_api_key(settings_path, backend, api_key):
    """Store an api_key for a backend that needs one (anthropic/openai only).

    Write-only: read_output_settings()/_backend_view() only ever expose
    whether a key is set (has_api_key), never the value itself.
    """
    if settings_path is None:
        raise ConfigError("settings", "設定ファイルの場所が未設定です")
    if not isinstance(backend, str) or backend not in _MODEL_LIST_ENDPOINTS:
        raise ConfigError("backend", "この生成方式は api_key を使いません")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ConfigError("api_key", "APIキーを入力してください")
    settings = _read_settings(settings_path)
    output = settings.get("output")
    output = deepcopy(output) if isinstance(output, dict) else {}
    section = output.get(backend)
    section = dict(section) if isinstance(section, dict) else {}
    section["api_key"] = api_key.strip()
    output[backend] = section
    settings["output"] = output
    atomic_json(settings_path, settings)


def test_generation(settings_path, backend, model):
    """Probe backend/model connectivity without changing the active selection.

    On success the model is remembered as "verified" for this backend, so it
    shows up as a pickable option next time (the /configs card's datalist).
    """
    if settings_path is None:
        raise ConfigError("settings", "設定ファイルの場所が未設定です")
    if not isinstance(backend, str) or backend not in BACKENDS:
        raise ConfigError("backend", "未対応の生成方式です")
    if backend == "none":
        model = None
    else:
        try:
            model = _model(model)
        except ConfigError as error:
            raise ConfigError("model", "モデル名を明示してください") from error
        if model is None:
            raise ConfigError("model", "モデルを指定してください")
    generation = {"backend": backend, "model": model, "limits": default_limits(backend)}
    availability = generation_availability(generation, settings_path)
    if availability["available"] and model and backend in _MODEL_LIST_ENDPOINTS:
        section = _section(_read_settings(settings_path), backend)
        reachable, reason = _model_reachable(backend, section, model)
        if not reachable:
            availability = {**availability, "available": False, "reason": reason}
    if availability["available"] and model:
        _remember_verified_model(settings_path, backend, model)
    return availability


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
