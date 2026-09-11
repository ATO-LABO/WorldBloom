"""Canonical, archive, and self-history precedent tables."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, TYPE_CHECKING

import yaml

from gapengine.classify import Classification

if TYPE_CHECKING:
    from engine.actions import Action
    from engine.subject import Subject
    from engine.world import World


ContextKey = tuple[tuple[str, ...], bool, str, str, str, bool]
ActionKey = tuple[str, str, str]


def _is_hostile(
    subject: Subject,
    target_id: str,
    world: World,
) -> bool:
    return (
        world.relations.stance(subject.id, target_id) < -0.2
        or target_id in subject.goal.obstacles
    )


def ctx_key(
    subject: Subject,
    world: World,
    present: Iterable[Subject],
) -> ContextKey:
    hostile_present = any(
        peer.id != subject.id
        and peer.vitality != "dead"
        and _is_hostile(subject, peer.id, world)
        for peer in present
    )

    if subject.goal.target is None:
        objective_state = "none"
    else:
        holder = world.holder(subject.goal.target)
        if holder is None:
            objective_state = "none"
        elif holder == subject.id:
            objective_state = "self"
        elif holder not in world.subjects:
            objective_state = "other"
        elif (
            world.relations.stance(subject.id, holder)
            >= world.companionship["threshold"]
        ):
            objective_state = "ally"
        elif _is_hostile(subject, holder, world):
            objective_state = "hostile"
        else:
            objective_state = "other"

    counterpart = (
        world.protagonist
        if subject.id == world.antagonist
        else world.antagonist
    )
    stance = world.relations.stance(subject.id, counterpart)
    if stance < -0.3:
        stance_bucket = "hostile"
    elif stance > 0.3:
        stance_bucket = "friendly"
    else:
        stance_bucket = "neutral"

    return (
        tuple(sorted(subject.phase)),
        hostile_present,
        objective_state,
        subject.vitality,
        stance_bucket,
        subject.identity_displayed != subject.id,
    )


def act_key(
    classification: Classification,
    action: Action,
) -> ActionKey:
    return (
        classification.category or "-",
        action.verb,
        classification.target_role,
    )


def normalize_ctx(value: Any) -> ContextKey:
    if (
        not isinstance(value, (list, tuple))
        or len(value) not in {5, 6}
    ):
        raise ValueError(f"Invalid precedent context: {value!r}")
    phase = value[0]
    if not isinstance(phase, (list, tuple)):
        raise ValueError(f"Invalid precedent phase: {phase!r}")
    return (
        tuple(sorted(str(item) for item in phase)),
        bool(value[1]),
        str(value[2]),
        str(value[3]),
        str(value[4]),
        bool(value[5]) if len(value) == 6 else False,
    )


def normalize_act(value: Any) -> ActionKey:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"Invalid precedent action: {value!r}")
    return (str(value[0]), str(value[1]), str(value[2]))


def _ctx_text(context: ContextKey) -> str:
    normalized = normalize_ctx(context)
    phase = json.dumps(
        list(normalized[0]),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    hostile = "true" if normalized[1] else "false"
    disguised = "true" if normalized[5] else "false"
    return "|".join(
        (
            phase,
            hostile,
            normalized[2],
            normalized[3],
            normalized[4],
            disguised,
        )
    )


def _parse_ctx(value: str) -> ContextKey:
    parts = value.split("|")
    if len(parts) not in {5, 6}:
        raise ValueError(f"Invalid serialized context: {value!r}")
    phase = json.loads(parts[0])
    return normalize_ctx(
        (
            phase,
            parts[1] == "true",
            parts[2],
            parts[3],
            parts[4],
            parts[5] == "true" if len(parts) == 6 else False,
        )
    )


def _act_text(action: ActionKey) -> str:
    return "|".join(action)


def _parse_act(value: str) -> ActionKey:
    parts = value.split("|")
    if len(parts) != 3:
        raise ValueError(f"Invalid serialized action: {value!r}")
    return normalize_act(parts)


class PrecedentTable:
    def __init__(
        self,
        counts: Mapping[ContextKey, Mapping[ActionKey, float]]
        | None = None,
    ) -> None:
        self.counts: dict[ContextKey, Counter[ActionKey]] = {}
        for context in sorted(counts or {}, key=_ctx_text):
            normalized_context = normalize_ctx(context)
            counter: Counter[ActionKey] = Counter()
            for action, count in sorted(
                counts[context].items(),
                key=lambda pair: _act_text(normalize_act(pair[0])),
            ):
                numeric = float(count)
                if numeric > 0.0:
                    counter[normalize_act(action)] = numeric
            if counter:
                self.counts[normalized_context] = counter

    def add(
        self,
        context: ContextKey,
        action: ActionKey,
        n: float = 1,
    ) -> None:
        amount = float(n)
        if amount <= 0.0:
            return
        normalized_context = normalize_ctx(context)
        normalized_action = normalize_act(action)
        self.counts.setdefault(
            normalized_context,
            Counter(),
        )[normalized_action] += amount

    def p(
        self,
        context: ContextKey,
        action: ActionKey,
        candidate_acts: set[ActionKey],
    ) -> float:
        normalized_context = normalize_ctx(context)
        normalized_action = normalize_act(action)
        counter = self.counts.get(normalized_context, Counter())
        alternatives = set(counter)
        alternatives.update(normalize_act(value) for value in candidate_acts)
        alternatives.add(normalized_action)
        denominator = float(sum(counter.values())) + len(alternatives)
        return (float(counter.get(normalized_action, 0.0)) + 1.0) / denominator

    def merge(
        self,
        other: PrecedentTable,
        weight: float = 1.0,
    ) -> PrecedentTable:
        merged = PrecedentTable(self.counts)
        multiplier = float(weight)
        if multiplier <= 0.0:
            return merged
        for context in sorted(other.counts, key=_ctx_text):
            for action in sorted(
                other.counts[context],
                key=_act_text,
            ):
                merged.add(
                    context,
                    action,
                    other.counts[context][action] * multiplier,
                )
        return merged

    def to_json(self) -> str:
        raw = {
            "counts": {
                _ctx_text(context): {
                    _act_text(action): self.counts[context][action]
                    for action in sorted(
                        self.counts[context],
                        key=_act_text,
                    )
                }
                for context in sorted(self.counts, key=_ctx_text)
            }
        }
        return json.dumps(
            raw,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(
        cls,
        source: str | bytes | Mapping[str, Any],
    ) -> PrecedentTable:
        if isinstance(source, bytes):
            raw = json.loads(source.decode("utf-8"))
        elif isinstance(source, str):
            raw = json.loads(source)
        else:
            raw = dict(source)

        table = cls()
        counts = raw.get("counts", {})
        if not isinstance(counts, Mapping):
            raise ValueError("Precedent JSON counts must be a mapping")
        for context_text in sorted(counts):
            context = _parse_ctx(str(context_text))
            actions = counts[context_text]
            if not isinstance(actions, Mapping):
                raise ValueError("Precedent context value must be a mapping")
            for action_text in sorted(actions):
                table.add(
                    context,
                    _parse_act(str(action_text)),
                    float(actions[action_text]),
                )
        return table

    @property
    def hash(self) -> str:
        return hashlib.sha256(
            self.to_json().encode("utf-8")
        ).hexdigest()


def load_canon(path: str | Path) -> PrecedentTable:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    weight = float(raw.get("weight", 1.0))
    table = PrecedentTable()
    for entry in raw.get("entries", []) or []:
        raw_context = entry["ctx"]
        raw_action = entry["act"]
        context: ContextKey = (
            tuple(sorted(str(value) for value in raw_context.get("phase", []))),
            bool(raw_context.get("hostile_present", False)),
            str(raw_context.get("objective", "none")),
            str(raw_context.get("vitality", "alive")),
            str(raw_context.get("stance", "neutral")),
            bool(raw_context.get("disguised", False)),
        )
        action: ActionKey = (
            (
                str(raw_action["category"])
                if raw_action.get("category") is not None
                else "-"
            ),
            str(raw_action["verb"]),
            str(raw_action.get("role", "none")),
        )
        table.add(context, action, float(entry.get("n", 1)) * weight)
    return table


def from_runs(
    layer_files: list[Path],
    protagonist: str,
) -> PrecedentTable:
    table = PrecedentTable()
    for path in sorted((Path(value) for value in layer_files), key=str):
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if (
                row.get("kind") != "decision"
                or row.get("subject") != protagonist
            ):
                continue
            classification = row.get("classification")
            policy = row.get("policy")
            if not isinstance(classification, Mapping):
                continue
            if not isinstance(policy, Mapping) or "ctx" not in policy:
                continue
            context = normalize_ctx(policy["ctx"])
            action: ActionKey = (
                str(classification.get("category") or "-"),
                str(row["verb"]),
                str(classification.get("target_role", "none")),
            )
            table.add(context, action)
    return table
