"""Request building and response parsing for the local llama-server backend."""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
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

    # Fixed keys come last so a settings.json "options" entry can never
    # override the recorded model or the request shape.
    payload = {
        **options,
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": think},
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


def is_ready(config: Mapping[str, Any], *, timeout: float = 2.0) -> bool:
    """True if the configured server answers GET /health with 200."""

    base_url = str(config.get("base_url", DEFAULT_BASE_URL)).rstrip("/")
    try:
        with urllib.request.urlopen(f"{base_url}/health", timeout=timeout) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError, ValueError):
        return False


def has_launch_command(config: Mapping[str, Any]) -> bool:
    launch = config.get("launch")
    return isinstance(launch, list) and bool(launch) and all(isinstance(part, str) and part for part in launch)


def spawn_server(config: Mapping[str, Any]) -> subprocess.Popen:
    """Start the configured llama-server. Caller owns the process (stop it, wait on it)."""

    launch = config.get("launch")
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(list(launch), **kwargs)


def wait_ready(
    config: Mapping[str, Any], process: subprocess.Popen, *, sleep=time.sleep, clock=time.monotonic,
) -> None:
    """Block until /health answers or the process dies/times out. Raises RuntimeError on failure."""

    startup_seconds = float(config.get("startup_seconds", 180))
    deadline = clock() + startup_seconds
    while True:
        if is_ready(config):
            return
        if process.poll() is not None:
            raise RuntimeError("llama-server failed to start")
        if clock() >= deadline:
            raise RuntimeError("llama-server failed to start")
        sleep(2.0)


def stop_server(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


@contextmanager
def managed_server(config: Mapping[str, Any], *, on_started=None, sleep=time.sleep, clock=time.monotonic):
    """Start the configured llama-server if needed, and stop it again on exit.

    Yields False without spawning anything if the server is already reachable,
    or if config has no (non-empty) "launch" command list. Yields True once
    the freshly launched server answers /health, and terminates it on exit.
    """

    if is_ready(config):
        yield False
        return

    if not has_launch_command(config):
        yield False
        return

    process = spawn_server(config)
    if on_started is not None:
        on_started(process.pid)
    try:
        wait_ready(config, process, sleep=sleep, clock=clock)
        yield True
    finally:
        stop_server(process)


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
