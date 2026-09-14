"""Process-local cache of parsed YAML documents keyed by path and mtime."""
from __future__ import annotations
import copy
from pathlib import Path
from typing import Any
import yaml

# ponytail: keyed by (path, mtime_ns, size), unbounded. A same-size rewrite
# inside one mtime tick returns the stale parse; hash the text if that bites.
_CACHE: dict[tuple[str, int, int], Any] = {}


def load_yaml(path: Path) -> Any:
    """Return a deep copy of the parsed document; re-parse only when the file changes."""
    resolved = Path(path).resolve()
    stat = resolved.stat()
    key = (str(resolved), stat.st_mtime_ns, stat.st_size)
    if key not in _CACHE:
        _CACHE[key] = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    return copy.deepcopy(_CACHE[key])
