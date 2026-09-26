"""TypeSafe Jev API key storage/verification (WB-JEV-005).

settings.json["rationality"]["jev"] = {"api_key": ..., "model": ...,
"verified_at": <iso>} -- kept apart from settings.json["output"] (a
different concern: this key authenticates a rationality *judge*, not text
generation). The key value is never returned by any read function here --
read_jev_settings()'s public view is has_api_key/model/verified_at/available
only, same has_api_key-only contract as execution/output_settings.py's own
public view. Only execution/evolution_worker.py (via read_jev_api_key) ever
sees the raw value, and only to set the TYPESAFE_API_KEY env var for the GA
subprocess -- it never enters config.json/manifest.json/argv/logs.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import http.client
import json
import urllib.error
import urllib.request

from execution.output_settings import _read_settings
from execution.provenance import ConfigError, atomic_json
from gapengine.rationality import JEV_BASE_URL, JEV_DEFAULT_MODEL


def _section(settings):
    value = settings.get("rationality")
    value = value.get("jev") if isinstance(value, dict) else None
    return value if isinstance(value, dict) else {}


def read_jev_settings(settings_path):
    """Public view: has_api_key/model/verified_at/available. Never the key."""
    section = _section(_read_settings(settings_path))
    has_api_key = bool(str(section.get("api_key", "")).strip())
    model = section.get("model")
    model = model if isinstance(model, str) and model.strip() else JEV_DEFAULT_MODEL
    verified_at = section.get("verified_at")
    verified_at = verified_at if isinstance(verified_at, str) else None
    return {
        "has_api_key": has_api_key,
        "model": model,
        "verified_at": verified_at,
        "available": has_api_key and verified_at is not None,
    }


def read_jev_api_key(settings_path):
    """Worker-only: the raw key value, or "" when unset/unreadable."""
    try:
        section = _section(_read_settings(settings_path))
    except ConfigError:
        return ""
    return str(section.get("api_key", "")).strip()


def _error_detail(error):
    """TypeSafe's own error message from an HTTPError body (never the key --
    the request's Authorization header is not part of the response)."""
    try:
        body = json.loads(error.read().decode("utf-8"))
    except (OSError, ValueError, AttributeError):
        return ""
    detail = body.get("detail") if isinstance(body, dict) else None
    message = detail.get("message") if isinstance(detail, dict) else detail
    return str(message)[:200] if message else ""


def write_jev_api_key(settings_path, api_key, *, base_url=JEV_BASE_URL, timeout=15.0):
    """Verify `api_key` with one minimal POST /v1/systemone against
    JEV_DEFAULT_MODEL -- the exact endpoint and model JevJudge uses -- before
    storing it. (GET /v1/models' response shape is undocumented, so a listing
    check could reject a working key.) The probe costs a few dozen input
    tokens.

    200 with a choice answer -> stored, verified_at set to now. 401/403 ->
    "invalid key"; other 4xx -> "can't use this model" (with TypeSafe's own
    message); anything else -> "can't connect". settings.json is untouched on
    every failure.
    """
    if settings_path is None:
        raise ConfigError("settings", "設定ファイルの場所が未設定です")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ConfigError("api_key", "APIキーを入力してください")
    key = api_key.strip()
    payload = {
        "state": "WorldBloom key check",
        "model": JEV_DEFAULT_MODEL,
        "questions": {"q": {
            "type": "choice",
            "instructions": "Pick one.",
            "criteria": {"a": "option a", "b": "option b"},
        }},
    }
    request = urllib.request.Request(
        base_url.rstrip("/") + "/v1/systemone",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise ConfigError("api_key", "APIキーが無効です") from error
        if 400 <= error.code < 500 and error.code != 429:
            detail = _error_detail(error)
            message = f"このキーでは {JEV_DEFAULT_MODEL} を使えません"
            raise ConfigError("api_key", f"{message}（{detail}）" if detail else message) from error
        raise ConfigError("api_key", "TypeSafe に接続できません") from error
    except (OSError, urllib.error.URLError, http.client.HTTPException, ValueError) as error:
        raise ConfigError("api_key", "TypeSafe に接続できません") from error

    answer = (data.get("answers") or {}).get("q") if isinstance(data, dict) else None
    if not isinstance(answer, dict) or not isinstance(answer.get("probabilities"), dict):
        raise ConfigError("api_key", "TypeSafe の応答形式が想定と違います")

    settings = _read_settings(settings_path)
    rationality = settings.get("rationality")
    rationality = deepcopy(rationality) if isinstance(rationality, dict) else {}
    rationality["jev"] = {
        "api_key": key,
        "model": JEV_DEFAULT_MODEL,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    settings["rationality"] = rationality
    atomic_json(settings_path, settings)
    return read_jev_settings(settings_path)
