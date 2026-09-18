"""Request building and response parsing for the local llama-server backend."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Mapping

DEFAULT_MODEL = "bonsai2-27b"
DEFAULT_BASE_URL = "http://127.0.0.1:8089"
DEFAULT_OPTIONS = {
    "temperature": 0.7,
    "top_p": 0.8,
    "top_k": 20,
    "min_p": 0,
    "presence_penalty": 1.0,
    "max_tokens": 8192,
}


def build_request(
    config: Mapping[str, Any],
    prompt: str,
) -> tuple[str, dict[str, Any]]:
    """Build the (url, payload) pair for a llama-server /v1/chat/completions call."""

    base_url = str(config.get("base_url", DEFAULT_BASE_URL)).rstrip("/")
    model = str(config.get("model", DEFAULT_MODEL))
    think = bool(config.get("think", True))

    raw_options = config.get("options")
    options = dict(DEFAULT_OPTIONS)
    if isinstance(raw_options, Mapping):
        options.update(raw_options)

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": think},
        **options,
    }
    seed = config.get("seed")
    if isinstance(seed, int):
        payload["seed"] = seed

    return f"{base_url}/v1/chat/completions", payload


def extract_text(data: Mapping[str, Any]) -> str:
    """Pull the message body out of a /v1/chat/completions response."""

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("invalid response content")
    first = choices[0]
    if not isinstance(first, Mapping):
        raise ValueError("invalid response content")
    if first.get("finish_reason") != "stop":
        raise ValueError("incomplete response")
    content = _as_mapping(first.get("message")).get("content")
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
    """The local server's model names via /v1/models, sorted. (names, reason)."""

    base_url = str(config.get("base_url", DEFAULT_BASE_URL)).rstrip("/")
    try:
        with urllib.request.urlopen(
            f"{base_url}/v1/models",
            timeout=timeout,
        ) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, ValueError):
        return [], "server_unreachable"

    names: set[str] = set()
    for entry in _as_mapping(data).get("data", []) or []:
        if not isinstance(entry, Mapping):
            continue
        value = entry.get("id")
        if isinstance(value, str):
            names.add(value)
    return sorted(names), None


def availability(
    config: Mapping[str, Any],
    *,
    timeout: float = 2.0,
) -> dict[str, Any]:
    """Check whether the configured llama-server and model are reachable."""

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
