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


def write_jev_api_key(settings_path, api_key, *, base_url=JEV_BASE_URL, timeout=6.0):
    """Verify `api_key` against a read-only GET /v1/models before storing it.

    200 with JEV_DEFAULT_MODEL listed (by "id" or "alias") -> stored,
    verified_at set to now. 200 without it, 401/403, or any other failure ->
    ConfigError, settings.json left untouched.
    """
    if settings_path is None:
        raise ConfigError("settings", "設定ファイルの場所が未設定です")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ConfigError("api_key", "APIキーを入力してください")
    key = api_key.strip()
    request = urllib.request.Request(
        base_url.rstrip("/") + "/v1/models",
        headers={"Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise ConfigError("api_key", "APIキーが無効です") from error
        raise ConfigError("api_key", "TypeSafe に接続できません") from error
    except (OSError, urllib.error.URLError, http.client.HTTPException, ValueError) as error:
        raise ConfigError("api_key", "TypeSafe に接続できません") from error

    # docs.typesafe.ai's /v1/models response shape wasn't confirmed at
    # implementation time -- accept both {"data":[{"id":...}]} (OpenAI
    # convention) and a bare list of strings/objects, matching by either
    # "id" or "alias" (plan §"Jev API").
    entries = data.get("data") if isinstance(data, dict) else data
    ids = set()
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, str):
                ids.add(entry)
            elif isinstance(entry, dict):
                for field in ("id", "alias"):
                    value = entry.get(field)
                    if isinstance(value, str):
                        ids.add(value)
    if JEV_DEFAULT_MODEL not in ids:
        raise ConfigError("api_key", f"このキーでは {JEV_DEFAULT_MODEL} を使えません")

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
