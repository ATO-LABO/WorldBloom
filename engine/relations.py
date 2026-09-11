"""Directed relationship matrix for implementation plan §3.4."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))


class Relations:
    """Own directed affinity and awareness values."""

    def __init__(
        self,
        values: Mapping[str, Mapping[str, Mapping[str, float]]] | None = None,
        *,
        target_resolver: Callable[[str, str], str] | None = None,
    ) -> None:
        self._target_resolver = target_resolver
        self._values: dict[str, dict[str, dict[str, float]]] = {}
        for observer in sorted(values or {}):
            targets = values[observer]
            self._values[observer] = {}
            for target in sorted(targets):
                relation = targets[target]
                self._values[observer][target] = {
                    "affinity": round(
                        _clamp(float(relation.get("affinity", 0.0)), -1.0, 1.0),
                        4,
                    ),
                    "awareness": round(
                        _clamp(float(relation.get("awareness", 0.0)), 0.0, 1.0),
                        4,
                    ),
                }

    def relation(self, observer: str, target: str) -> dict[str, float]:
        resolved = (
            self._target_resolver(observer, target)
            if self._target_resolver is not None
            else target
        )
        relation = self._values.get(observer, {}).get(resolved)
        if relation is None:
            return {"affinity": 0.0, "awareness": 0.0}
        return dict(relation)

    def stance(self, observer: str, target: str) -> float:
        return self.relation(observer, target)["affinity"]

    def awareness(self, observer: str, target: str) -> float:
        return self.relation(observer, target)["awareness"]

    def bonds(self, subject: str) -> float:
        return round(
            sum(
                max(0.0, self.stance(observer, subject))
                for observer in sorted(self._values)
                if observer != subject
            ),
            4,
        )

    def change(
        self,
        observer: str,
        target: str,
        *,
        affinity: float = 0.0,
        awareness: float = 0.0,
    ) -> dict[str, float]:
        resolved = (
            self._target_resolver(observer, target)
            if self._target_resolver is not None
            else target
        )
        current = self.relation(observer, target)
        updated = {
            "affinity": round(
                _clamp(current["affinity"] + float(affinity), -1.0, 1.0),
                4,
            ),
            "awareness": round(
                _clamp(current["awareness"] + float(awareness), 0.0, 1.0),
                4,
            ),
        }
        self._values.setdefault(observer, {})[resolved] = updated
        return {
            "affinity": round(updated["affinity"] - current["affinity"], 4),
            "awareness": round(updated["awareness"] - current["awareness"], 4),
        }

    def snapshot(self) -> dict[str, dict[str, dict[str, float]]]:
        return {
            observer: {
                target: dict(self._values[observer][target])
                for target in sorted(self._values[observer])
            }
            for observer in sorted(self._values)
        }

    def flat_rows(self) -> list[dict[str, str | float]]:
        rows: list[dict[str, str | float]] = []
        for observer in sorted(self._values):
            for target in sorted(self._values[observer]):
                relation = self._values[observer][target]
                rows.append(
                    {
                        "observer": observer,
                        "target": target,
                        "affinity": relation["affinity"],
                        "awareness": relation["awareness"],
                    }
                )
        return rows

    def copy(self) -> Relations:
        return Relations(
            deepcopy(self._values),
            target_resolver=self._target_resolver,
        )

    def discard(self, observer: str, target: str) -> None:
        targets = self._values.get(observer)
        if targets is None:
            return
        targets.pop(target, None)
