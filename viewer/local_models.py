"""Model residency facts; never infer generation from residency or GPU load."""
from __future__ import annotations

import json
from collections.abc import Mapping
import urllib.request

from viewer.local_telemetry import number


def read_loaded_models(base_url, *, timeout=2.0):
    """Unlike gpu_guard's arbitration helper, an unavailable /ps is not []."""
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/api/ps", timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(body, Mapping) or not isinstance(body.get("models"), list):
        return None
    if any(not isinstance(m, Mapping) or not isinstance(m.get("name") or m.get("model"), str)
           or not (m.get("name") or m.get("model")) for m in body["models"]):
        return None
    return [dict(m) for m in body["models"] if isinstance(m, Mapping)]


def model_key(name):
    name = str(name)
    return name[:-7] if name.endswith(":latest") else name


def placement(model):
    size = number(model.get("size"))
    vram = number(model.get("size_vram"))
    if size is None or size <= 0 or vram is None:
        return "unknown"
    if vram == 0:
        return "cpu"
    return "gpu" if vram >= size else "mixed"


def ollama_items(names, loaded, configured, *, available=True):
    installed = {model_key(n) for n in names if isinstance(n, str) and n}
    resident = {model_key(m.get("name") or m.get("model")): m for m in (loaded or [])
                if m.get("name") or m.get("model")}
    ordered = [configured] + [m.get("name") or m.get("model") for m in (loaded or [])] + list(names)
    result, seen = [], set()
    for name in ordered:
        if not name or model_key(name) in seen:
            continue
        key = model_key(name)
        seen.add(key)
        model = resident.get(key)
        if not available or loaded is None:
            state = "unknown"
        elif model:
            state = "loaded"
        else:
            state = "unloaded" if key in installed else "not_installed"
        result.append({"name": str(name), "configured": key == model_key(configured),
                       "state": state, "placement": placement(model) if model else "unknown",
                       "vram_bytes": number(model.get("size_vram")) if model else None,
                       "size_bytes": number(model.get("size")) if model else None,
                       "installed": key in installed, "execution": "unknown"})
    return result
