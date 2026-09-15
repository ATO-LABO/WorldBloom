"""Request building and response parsing for the local Ollama backend."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Mapping

DEFAULT_MODEL = "qwen3.6:35b"
DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_OPTIONS = {"num_ctx": 16384, "num_predict": 4096}


def build_request(
    config: Mapping[str, Any],
    prompt: str,
) -> tuple[str, dict[str, Any]]:
    """Build the (url, payload) pair for an Ollama /api/chat call."""

    base_url = str(config.get("base_url", DEFAULT_BASE_URL)).rstrip("/")
    model = str(config.get("model", DEFAULT_MODEL))
    think = bool(config.get("think", False))

    raw_options = config.get("options")
    options = dict(DEFAULT_OPTIONS)
    if isinstance(raw_options, Mapping):
        options.update(raw_options)
    seed = config.get("seed")
    if isinstance(seed, int):
        options["seed"] = seed

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": think,
        "options": options,
    }
    return f"{base_url}/api/chat", payload


def extract_text(data: Mapping[str, Any]) -> str:
    """Pull the message body out of an /api/chat response."""

    if data.get("done_reason") != "stop":
        raise ValueError("incomplete response")
    content = _as_mapping(data.get("message")).get("content")
    if not isinstance(content, str):
        raise ValueError("invalid response content")
    return content


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def list_models(
    config: Mapping[str, Any],
    *,
    timeout: float = 2.0,
) -> tuple[list[str], str | None]:
    """The local server's model names via /api/tags, sorted. (names, reason)."""

    base_url = str(config.get("base_url", DEFAULT_BASE_URL)).rstrip("/")
    try:
        with urllib.request.urlopen(
            f"{base_url}/api/tags",
            timeout=timeout,
        ) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, ValueError):
        return [], "server_unreachable"

    names: set[str] = set()
    for entry in _as_mapping(data).get("models", []) or []:
        if not isinstance(entry, Mapping):
            continue
        for key in ("name", "model"):
            value = entry.get(key)
            if isinstance(value, str):
                names.add(value)
    return sorted(names), None


def availability(
    config: Mapping[str, Any],
    *,
    timeout: float = 2.0,
) -> dict[str, Any]:
    """Check whether the configured Ollama server and model are reachable."""

    model = str(config.get("model", DEFAULT_MODEL))
    result: dict[str, Any] = {
        "available": False,
        "reason": None,
        "model": model,
    }
    names, reason = list_models(config, timeout=timeout)
    if reason is not None:
        result["reason"] = reason
        return result
    if model not in names:
        result["reason"] = "model_missing"
        return result
    result["available"] = True
    return result
