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
        affinity_cap_resolver: (
            Callable[[str, str], float | None] | None
        ) = None,
    ) -> None:
        self._target_resolver = target_resolver
        self._affinity_cap_resolver = affinity_cap_resolver
        self._values: dict[str, dict[str, dict[str, float]]] = {}
        for observer in sorted(values or {}):
            targets = values[observer]
            self._values[observer] = {}
            for target in sorted(targets):
                relation = targets[target]
                cap = (
                    self._affinity_cap_resolver(observer, target)
                    if self._affinity_cap_resolver is not None
                    else None
                )
                affinity_upper = (
                    min(1.0, float(cap))
                    if cap is not None
                    else 1.0
                )
                self._values[observer][target] = {
                    "affinity": round(
                        _clamp(
                            float(relation.get("affinity", 0.0)),
                            -1.0,
                            affinity_upper,
                        ),
                        4,
                    ),
                    "awareness": round(
                        _clamp(
                            float(relation.get("awareness", 0.0)),
                            0.0,
                            1.0,
                        ),
                        4,
                    ),
                }

    def _lookup(self, observer: str, target: str) -> dict[str, float] | None:
        resolved = (
            self._target_resolver(observer, target)
            if self._target_resolver is not None
            else target
        )
        return self._values.get(observer, {}).get(resolved)

    def relation(self, observer: str, target: str) -> dict[str, float]:
        relation = self._lookup(observer, target)
        if relation is None:
            return {"affinity": 0.0, "awareness": 0.0}
        return dict(relation)

    def stance(self, observer: str, target: str) -> float:
        relation = self._lookup(observer, target)
        return 0.0 if relation is None else relation["affinity"]

    def awareness(self, observer: str, target: str) -> float:
        relation = self._lookup(observer, target)
        return 0.0 if relation is None else relation["awareness"]

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
        cap = (
            self._affinity_cap_resolver(observer, resolved)
            if self._affinity_cap_resolver is not None
            else None
        )
        affinity_upper = (
            max(
                current["affinity"],
                min(1.0, float(cap)),
            )
            if cap is not None
            else 1.0
        )
        updated = {
            "affinity": round(
                _clamp(
                    current["affinity"] + float(affinity),
                    -1.0,
                    affinity_upper,
                ),
                4,
            ),
            "awareness": round(
                _clamp(
                    current["awareness"] + float(awareness),
                    0.0,
                    1.0,
                ),
                4,
            ),
        }
        self._values.setdefault(observer, {})[resolved] = updated
        return {
            "affinity": round(
                updated["affinity"] - current["affinity"],
                4,
            ),
            "awareness": round(
                updated["awareness"] - current["awareness"],
                4,
            ),
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
            affinity_cap_resolver=self._affinity_cap_resolver,
        )

    def discard(self, observer: str, target: str) -> None:
        targets = self._values.get(observer)
        if targets is None:
            return
        targets.pop(target, None)
