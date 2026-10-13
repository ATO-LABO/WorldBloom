"""Typed, finite survey goals; independent of delivery planning and Policy."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping


def _keys(raw: Any, allowed: set[str], required: set[str]) -> None:
    if not isinstance(raw, dict) or set(raw) - allowed or required - set(raw):
        raise ValueError(f"Expected fields {sorted(required)}; allowed {sorted(allowed)}")


def _name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected a non-empty name")
    return value


def _positive_int(value: Any) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("Expected a positive integer")
    return value


def clock_minutes(value: Any) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
        raise ValueError("Clock time must be HH:MM")
    hours, minutes = map(int, value.split(":"))
    return hours * 60 + minutes


@dataclass(frozen=True)
class Condition:
    kind: str
    subject: str
    value: str
    quantity: int = 1

    def satisfied(self, subjects: Mapping[str, Any]) -> bool:
        person = subjects[self.subject]
        if self.kind == "at":
            return person.zone == self.value
        if self.kind == "holds":
            return person.inventory.get(self.value, 0) >= self.quantity
        if self.kind == "knows":
            return self.value in person.knowledge
        raise ValueError(f"Unsupported goal predicate: {self.kind}")


@dataclass(frozen=True)
class GoalSpec:
    actor: str
    conditions: tuple[Condition, ...]
    mode: str = "any"
    day: int = 1
    slot: str | None = None
    legacy: bool = False

    def satisfied(self, subjects: Mapping[str, Any], day: int = 1,
                  slot: str | None = None) -> bool:
        if self.mode != "any":
            moment = (day, clock_minutes(slot))
            target = (self.day, clock_minutes(self.slot))
            if self.mode == "at" and moment != target:
                return False
            if self.mode == "by" and moment > target:
                return False
        return all(condition.satisfied(subjects) for condition in self.conditions)

    def as_dict(self) -> dict[str, Any]:
        return {"actor": self.actor, "all": [
            {"predicate": c.kind, "subject": c.subject, "value": c.value,
             **({"quantity": c.quantity} if c.kind == "holds" else {})}
            for c in self.conditions
        ], "time": {"mode": self.mode, "day": self.day, "slot": self.slot},
                "legacy": self.legacy}

    def to_input_dict(self) -> dict[str, Any]:
        """Serialize an explicit goal to the input YAML schema, not a report.

        Legacy delivery is represented by absence of ``goal`` and must never
        be silently promoted to an explicit survey goal by an editor.
        """
        if self.legacy:
            raise ValueError("Legacy delivery must retain an absent goal")
        predicates = []
        for condition in self.conditions:
            field = {"at": "zone", "holds": "item", "knows": "fact"}.get(condition.kind)
            if field is None:
                raise ValueError("Unsupported goal predicate")
            row = {"predicate": condition.kind, "subject": condition.subject,
                   field: condition.value}
            if condition.kind == "holds":
                row["quantity"] = condition.quantity
            predicates.append(row)
        timing = {"mode": self.mode}
        if self.mode != "any":
            timing.update(day=self.day, slot=self.slot)
        return {"actor": self.actor, "all": predicates, "time": timing}


def resolve_goal(raw: Any, world: Any, subjects: Mapping[str, Any]) -> GoalSpec | None:
    """Absent config adapts target/deliver_to without changing Subject.goal.

    Delivery retains the existing semantics: possess the item at its delivery
    zone. A recipient handover/donate is a separate engine operation.
    """
    if raw is None:
        actor = subjects[world.protagonist]
        if actor.goal.target is None:
            return None
        conditions = [Condition("holds", actor.id, actor.goal.target)]
        if actor.goal.deliver_to is not None:
            conditions.append(Condition("at", actor.id, actor.goal.deliver_to))
        return GoalSpec(actor.id, tuple(conditions), legacy=True)

    _keys(raw, {"actor", "all", "time"}, {"all"})
    actor = _name(raw.get("actor", world.protagonist))
    if actor not in subjects:
        raise ValueError(f"Unknown goal actor: {actor}")
    predicates = raw["all"]
    if not isinstance(predicates, list) or not predicates:
        raise ValueError("goal.all must be a non-empty list")
    conditions = []
    for predicate in predicates:
        if not isinstance(predicate, dict):
            raise ValueError("Goal condition must be a mapping")
        kind = predicate.get("predicate")
        if not isinstance(kind, str):
            raise ValueError("Goal predicate must be a name")
        field = {"at": "zone", "holds": "item", "knows": "fact"}.get(kind)
        if field is None:
            raise ValueError(f"Unsupported goal predicate: {kind}")
        allowed = {"predicate", "subject", field} | ({"quantity"} if kind == "holds" else set())
        _keys(predicate, allowed, {"predicate", field})
        subject = _name(predicate.get("subject", actor))
        value = _name(predicate[field])
        if subject not in subjects:
            raise ValueError(f"Unknown condition subject: {subject}")
        catalog = {"at": world.zones, "holds": world.items, "knows": world.facts}[kind]
        if value not in catalog:
            raise ValueError(f"Unknown {field}: {value}")
        quantity = _positive_int(predicate.get("quantity", 1))
        conditions.append(Condition(kind, subject, value, quantity))
    timing = raw.get("time", {"mode": "any"})
    _keys(timing, {"mode", "day", "slot"}, {"mode"})
    mode = timing["mode"]
    if not isinstance(mode, str) or mode not in {"any", "at", "by"}:
        raise ValueError(f"Unsupported time mode: {mode}")
    if mode == "any" and set(timing) != {"mode"}:
        raise ValueError("any does not take a day or slot")
    day = _positive_int(timing.get("day", 1))
    slot = timing.get("slot")
    if mode != "any":
        clock_minutes(slot)
        if day > world.days or slot not in world.slots:
            raise ValueError("Goal time is outside the configured world calendar")
    return GoalSpec(actor, tuple(conditions), mode, day, slot)
