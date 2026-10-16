"""Load preserved comparison inputs without depending on Git commit IDs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "historical"


def load_historical_fixture(name: str) -> bytes:
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    expected = manifest[name]["sha256"]
    content = (FIXTURES / name).read_bytes()
    if hashlib.sha256(content).hexdigest() != expected:
        raise ValueError(f"Historical comparison fixture changed: {name}")
    return content
