"""Deterministic layers.jsonl rows and layer diffs for plan §3.9."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TextIO


def nested_diff(before: Any, after: Any) -> Any:
    """Return changed leaves, or None when values are equal."""

    if isinstance(before, dict) and isinstance(after, dict):
        result: dict[str, Any] = {}
        for key in sorted(set(before) | set(after)):
            if key not in before:
                result[key] = after[key]
                continue
            if key not in after:
                result[key] = None
                continue
            changed = nested_diff(before[key], after[key])
            if changed is not None:
                result[key] = changed
        return result or None
    if before == after:
        return None
    return after


def relation_diff(
    before: dict[str, dict[str, dict[str, float]]],
    after: dict[str, dict[str, dict[str, float]]],
) -> list[dict[str, str | float]]:
    rows: list[dict[str, str | float]] = []
    observers = sorted(set(before) | set(after))
    for observer in observers:
        before_targets = before.get(observer, {})
        after_targets = after.get(observer, {})
        for target in sorted(set(before_targets) | set(after_targets)):
            old = before_targets.get(
                target,
                {"affinity": 0.0, "awareness": 0.0},
            )
            new = after_targets.get(
                target,
                {"affinity": 0.0, "awareness": 0.0},
            )
            affinity = round(
                float(new.get("affinity", 0.0))
                - float(old.get("affinity", 0.0)),
                4,
            )
            awareness = round(
                float(new.get("awareness", 0.0))
                - float(old.get("awareness", 0.0)),
                4,
            )
            if affinity == 0.0 and awareness == 0.0:
                continue
            rows.append(
                {
                    "observer": observer,
                    "target": target,
                    "affinity": affinity,
                    "awareness": awareness,
                }
            )
    return rows


def objective_diff(
    before: dict[str, str | None],
    after: dict[str, str | None],
) -> dict[str, str | None] | None:
    changed = {
        item: after.get(item)
        for item in sorted(set(before) | set(after))
        if before.get(item) != after.get(item)
    }
    return changed or None


def make_delta(
    actor: str | None,
    before_layers: dict[str, dict[str, Any]],
    after_layers: dict[str, dict[str, Any]],
    before_relations: dict[str, dict[str, dict[str, float]]],
    after_relations: dict[str, dict[str, dict[str, float]]],
    before_objectives: dict[str, str | None],
    after_objectives: dict[str, str | None],
) -> dict[str, Any]:
    actor_delta: dict[str, Any] = {}
    targets: dict[str, Any] = {}

    for subject_id in sorted(set(before_layers) | set(after_layers)):
        before = before_layers.get(subject_id, {})
        after = after_layers.get(subject_id, {})
        changed = nested_diff(before, after)
        if not changed:
            continue
        if subject_id == actor:
            actor_delta = changed
        else:
            targets[subject_id] = changed

    return {
        "actor": actor_delta,
        "targets": targets,
        "relations": relation_diff(before_relations, after_relations),
        "objective": objective_diff(before_objectives, after_objectives),
    }


def delta_effective(delta: dict[str, Any]) -> bool:
    return bool(
        delta.get("actor")
        or delta.get("targets")
        or delta.get("objective")
    )


class LayersWriter:
    """Write stable UTF-8 JSONL with sorted object keys."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: TextIO | None = None

    def __enter__(self) -> LayersWriter:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("w", encoding="utf-8", newline="\n")
        return self

    def write(self, row: dict[str, Any]) -> None:
        if self._handle is None:
            raise RuntimeError("LayersWriter is not open")
        serialized = json.dumps(
            row,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        self._handle.write(serialized)
        self._handle.write("\n")

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: Any,
    ) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None
