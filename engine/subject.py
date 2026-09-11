"""Seven-layer Subject state model for implementation plan §3.2 and §3.3."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from engine.world import World


def _sorted_dict(values: dict[str, Any]) -> dict[str, Any]:
    return {key: values[key] for key in sorted(values)}


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))


@dataclass
class Modifier:
    id: str
    source: str
    value: float
    kind: str
    visible: bool = True
    active: bool = True
    lethal: bool = False
    lethal_chance: float = 0.0
    affinity_cap: float | None = None
    affinity_cap_targets: tuple[str, ...] = ()


@dataclass
class BeliefAbout:
    known_modifiers: set[str] = field(default_factory=set)
    base_estimate: float = 50.0
    identity_seen: bool = False
    observe_progress: float = 0.0
    misled_by: str | None = None
@dataclass
class Belief:
    value: str
    confidence: float
    derived: bool = False


@dataclass
class Goal:
    target: str | None = None
    deliver_to: str | None = None
    obstacles: list[str] = field(default_factory=list)
    outcome: str | None = None


@dataclass
class Subject:
    id: str
    traits: dict[str, float]
    base: float
    modifiers: list[Modifier]
    beliefs_about: dict[str, BeliefAbout]
    beliefs: dict[str, Belief]
    knowledge: set[str]
    inventory: dict[str, int]
    reputation: float
    phase: set[str]
    verbs: set[str]
    identity_true: str
    identity_displayed: str
    goal: Goal
    stamina: float
    stamina_max: float
    stamina_recover: float
    exhausted: bool
    stress: float
    vitality: str
    downed_since: int | None
    zone: str
    range_zones: set[str]
    range_exclude: list[dict[str, Any]]
    companions: list[str] | None
    ally_value: float
    objective_claimant: bool = True
    decision_history: Counter[Any] = field(default_factory=Counter)
    gather_progress: dict[str, int] = field(default_factory=dict)
    gathered: dict[str, int] = field(default_factory=dict)
    policy: Any | None = None
    initial_relations: dict[str, dict[str, float]] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: str | Path) -> Subject:
        source = Path(path)
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(
                f"Subject YAML must contain a mapping: {source}"
            )

        subject_id = raw.get("id")
        if not isinstance(subject_id, str) or not subject_id:
            raise ValueError(f"Subject id is required: {source}")

        raw_traits = raw.get("traits", {})
        required_traits = {
            "social",
            "stubbornness",
            "curiosity",
            "diligence",
            "temper",
        }
        if not isinstance(raw_traits, dict) or not required_traits.issubset(
            raw_traits
        ):
            raise ValueError(
                f"Subject {subject_id} is missing required traits"
            )
        traits = {
            name: float(raw_traits[name])
            for name in sorted(required_traits)
        }

        modifiers: list[Modifier] = []
        for raw_modifier in raw.get("modifiers", []) or []:
            raw_affinity_cap = raw_modifier.get("affinity_cap")
            affinity_cap = (
                float(raw_affinity_cap)
                if raw_affinity_cap is not None
                else None
            )
            if (
                affinity_cap is not None
                and not -1.0 <= affinity_cap <= 1.0
            ):
                raise ValueError(
                    f"Modifier affinity_cap must be within [-1, 1]: "
                    f"{subject_id}:{raw_modifier.get('id')}"
                )

            raw_cap_targets = (
                raw_modifier.get("affinity_cap_targets", ()) or ()
            )
            if isinstance(raw_cap_targets, str):
                cap_targets = (raw_cap_targets,)
            elif isinstance(raw_cap_targets, (list, tuple, set)):
                cap_targets = tuple(
                    sorted({str(value) for value in raw_cap_targets})
                )
            else:
                raise ValueError(
                    f"Modifier affinity_cap_targets must be a string "
                    f"or sequence: {subject_id}:{raw_modifier.get('id')}"
                )

            modifiers.append(
                Modifier(
                    id=str(raw_modifier["id"]),
                    source=str(raw_modifier["source"]),
                    value=float(raw_modifier["value"]),
                    kind=str(raw_modifier["kind"]),
                    visible=bool(raw_modifier.get("visible", True)),
                    active=bool(raw_modifier.get("active", True)),
                    lethal=bool(raw_modifier.get("lethal", False)),
                    lethal_chance=float(
                        raw_modifier.get("lethal_chance", 0.0)
                    ),
                    affinity_cap=affinity_cap,
                    affinity_cap_targets=cap_targets,
                )
            )

        beliefs_about: dict[str, BeliefAbout] = {}
        for target in sorted(raw.get("beliefs_about", {})):
            belief = raw["beliefs_about"][target] or {}
            raw_misled_by = belief.get("misled_by")
            beliefs_about[str(target)] = BeliefAbout(
                known_modifiers={
                    str(value)
                    for value in belief.get("known_modifiers", [])
                },
                base_estimate=float(
                    belief.get("base_estimate", 50.0)
                ),
                identity_seen=bool(
                    belief.get("identity_seen", False)
                ),
                observe_progress=float(
                    belief.get("observe_progress", 0.0)
                ),
                misled_by=(
                    str(raw_misled_by)
                    if raw_misled_by is not None
                    else None
                ),
            )

        beliefs: dict[str, Belief] = {}
        raw_beliefs = raw.get("beliefs", {}) or {}
        if not isinstance(raw_beliefs, dict):
            raise ValueError(
                f"Subject {subject_id} beliefs must be a mapping"
            )
        for fact_id in sorted(raw_beliefs):
            raw_belief = raw_beliefs[fact_id] or {}
            if not isinstance(raw_belief, dict):
                raise ValueError(
                    f"Subject belief must be a mapping: "
                    f"{subject_id}:{fact_id}"
                )
            confidence = float(
                raw_belief.get("confidence", 0.0)
            )
            if not 0.0 <= confidence <= 1.0:
                raise ValueError(
                    f"Subject belief confidence must be within [0, 1]: "
                    f"{subject_id}:{fact_id}"
                )
            beliefs[str(fact_id)] = Belief(
                value=str(raw_belief["value"]),
                confidence=confidence,
            )

        raw_goal = raw.get("goal", {}) or {}
        goal = Goal(
            target=raw_goal.get("target"),
            deliver_to=raw_goal.get("deliver_to"),
            obstacles=sorted(
                str(value)
                for value in raw_goal.get("obstacles", [])
            ),
            outcome=raw_goal.get("outcome"),
        )

        raw_identity = raw.get("identity", {}) or {}
        raw_stamina = raw.get("stamina", {}) or {}
        stamina_max = float(raw_stamina.get("max", 0.0))
        stamina = float(
            raw_stamina.get("current", stamina_max)
        )
        raw_range = raw.get("range", {}) or {}
        range_zones = {
            str(value)
            for value in raw_range.get("zones", [])
        }
        entry = raw_range.get("entry")
        if not isinstance(entry, str) or not entry:
            raise ValueError(
                f"Subject {subject_id} requires range.entry"
            )

        initial_relations: dict[
            str,
            dict[str, float],
        ] = {}
        for target in sorted(raw.get("relations", {})):
            relation = raw["relations"][target] or {}
            initial_relations[str(target)] = {
                "affinity": float(
                    relation.get("affinity", 0.0)
                ),
                "awareness": float(
                    relation.get("awareness", 0.0)
                ),
            }

        inventory = {
            str(item): int(count)
            for item, count in sorted(
                (raw.get("inventory", {}) or {}).items()
            )
        }
        if any(count < 0 for count in inventory.values()):
            raise ValueError(
                f"Subject {subject_id} has a negative inventory count"
            )

        return cls(
            id=subject_id,
            traits=traits,
            base=float(raw.get("base", 0.0)),
            modifiers=sorted(
                modifiers,
                key=lambda modifier: modifier.id,
            ),
            beliefs_about=beliefs_about,
            beliefs=beliefs,
            knowledge={
                str(value)
                for value in raw.get("knowledge", [])
            },
            inventory=inventory,
            reputation=float(raw.get("reputation", 0.0)),
            phase={
                str(value)
                for value in raw.get("phase", [])
            },
            verbs={
                str(value)
                for value in raw.get("verbs", [])
            },
            identity_true=str(
                raw_identity.get("true", subject_id)
            ),
            identity_displayed=str(
                raw_identity.get("displayed", subject_id)
            ),
            goal=goal,
            stamina=_clamp(stamina, 0.0, stamina_max),
            stamina_max=stamina_max,
            stamina_recover=float(
                raw_stamina.get("recover_per_slot", 0.0)
            ),
            exhausted=bool(raw.get("exhausted", False)),
            stress=_clamp(
                float(raw.get("stress", 0.0)),
                0.0,
                10.0,
            ),
            vitality=str(raw.get("vitality", "alive")),
            downed_since=raw.get("downed_since"),
            zone=entry,
            range_zones=range_zones,
            range_exclude=list(
                raw_range.get("exclude", []) or []
            ),
            companions=(
                [
                    str(value)
                    for value in raw["companions"]
                ]
                if raw.get("companions") is not None
                else None
            ),
            ally_value=float(raw.get("ally_value", 0.0)),
            objective_claimant=bool(
                raw.get("objective_claimant", True)
            ),
            initial_relations=initial_relations,
        )

    def update_belief(
        self,
        fact_id: str,
        value: str,
        confidence: float,
        *,
        refute: bool = False,
        derived: bool = False,
    ) -> dict[str, Any]:
        confidence = round(_clamp(float(confidence), 0.0, 1.0), 4)
        current = self.beliefs.get(fact_id)
        before = (
            {
                "value": current.value,
                "confidence": current.confidence,
            }
            if current is not None
            else None
        )

        if refute:
            if current is None or current.value != value:
                outcome = "unchanged"
            else:
                current.confidence = round(
                    _clamp(
                        current.confidence * (1.0 - confidence),
                        0.0,
                        1.0,
                    ),
                    4,
                )
                outcome = "refuted"
        elif current is None:
            self.beliefs[fact_id] = Belief(
                value=value,
                confidence=confidence,
                derived=derived,
            )
            outcome = "adopted"
        elif current.value == value:
            current.confidence = round(
                max(current.confidence, confidence),
                4,
            )
            outcome = "reinforced"
        elif confidence > (
            current.confidence
            * (1.0 + self.traits["stubbornness"])
        ):
            self.beliefs[fact_id] = Belief(
                value=value,
                confidence=confidence,
                derived=derived,
            )
            outcome = "adopted"
        else:
            outcome = "rejected"

        updated = self.beliefs.get(fact_id)
        if (
            updated is not None
            and outcome in {"adopted", "reinforced", "refuted"}
        ):
            updated.derived = bool(derived)

        after = (
            {
                "value": updated.value,
                "confidence": updated.confidence,
            }
            if updated is not None
            else None
        )
        return {
            "fact": fact_id,
            "value": value,
            "confidence": confidence,
            "outcome": outcome,
            "before": before,
            "after": after,
        }

    def apply_evidence(
        self,
        fact_id: str,
        world: World,
    ) -> list[dict[str, Any]]:
        definition = world.facts.get(fact_id)
        if definition is None:
            return []

        updates: list[dict[str, Any]] = []
        for key, refute in (("implies", False), ("refutes", True)):
            raw_update = definition.get(key)
            if not isinstance(raw_update, dict):
                continue
            update = self.update_belief(
                str(raw_update["fact"]),
                str(raw_update["value"]),
                float(raw_update.get("confidence", 0.5)),
                refute=refute,
                derived=True,
            )
            update["evidence"] = fact_id
            updates.append(update)
        return updates

    def change_stamina(self, amount: float) -> float:
        before = self.stamina
        self.stamina = round(
            _clamp(self.stamina + float(amount), 0.0, self.stamina_max),
            4,
        )
        return round(self.stamina - before, 4)

    def change_stress(self, amount: float) -> float:
        before = self.stress
        self.stress = round(_clamp(self.stress + float(amount), 0.0, 10.0), 4)
        return round(self.stress - before, 4)

    def has_item(self, item: str, count: int = 1) -> bool:
        return self.inventory.get(item, 0) >= count

    def add_item(self, item: str, count: int = 1) -> None:
        if count <= 0:
            return
        self.inventory[item] = self.inventory.get(item, 0) + count
        self.inventory = _sorted_dict(self.inventory)

    def remove_item(self, item: str, count: int = 1) -> bool:
        if count <= 0 or not self.has_item(item, count):
            return False
        remaining = self.inventory[item] - count
        if remaining:
            self.inventory[item] = remaining
        else:
            del self.inventory[item]
        self.inventory = _sorted_dict(self.inventory)
        return True

    def derived_modifiers(
        self,
        world: World,
        present: list[Subject],
    ) -> list[Modifier]:
        derived: list[Modifier] = []
        disabled_derived = {
            (modifier.source, modifier.kind)
            for modifier in self.modifiers
            if not modifier.active
        }

        for item in sorted(self.inventory):
            if self.inventory[item] <= 0:
                continue
            definition = world.items.get(item, {})
            raw_modifier = definition.get("modifier")
            if not raw_modifier:
                continue
            kind = str(raw_modifier.get("kind", "item"))
            if (item, kind) in disabled_derived:
                continue

            raw_affinity_cap = raw_modifier.get("affinity_cap")
            affinity_cap = (
                float(raw_affinity_cap)
                if raw_affinity_cap is not None
                else None
            )
            raw_cap_targets = (
                raw_modifier.get("affinity_cap_targets", ()) or ()
            )
            if isinstance(raw_cap_targets, str):
                cap_targets = (raw_cap_targets,)
            elif isinstance(raw_cap_targets, (list, tuple, set)):
                cap_targets = tuple(
                    sorted({str(value) for value in raw_cap_targets})
                )
            else:
                raise ValueError(
                    f"Item modifier affinity_cap_targets must be "
                    f"a string or sequence: {item}"
                )

            derived.append(
                Modifier(
                    id=str(
                        raw_modifier.get("id", f"item:{item}")
                    ),
                    source=item,
                    value=float(
                        raw_modifier.get("value", 0.0)
                    ),
                    kind=kind,
                    visible=bool(
                        raw_modifier.get("visible", True)
                    ),
                    active=bool(
                        raw_modifier.get("active", True)
                    ),
                    lethal=bool(
                        raw_modifier.get("lethal", False)
                    ),
                    lethal_chance=float(
                        raw_modifier.get("lethal_chance", 0.0)
                    ),
                    affinity_cap=affinity_cap,
                    affinity_cap_targets=cap_targets,
                )
            )

        threshold = world.companionship["threshold"]
        for peer in sorted(
            present,
            key=lambda subject: subject.id,
        ):
            if (
                peer.id == self.id
                or peer.vitality not in {"alive", "revived"}
            ):
                continue
            if (
                world.relations.stance(peer.id, self.id)
                < threshold
            ):
                continue
            if (peer.id, "ally") in disabled_derived:
                continue
            derived.append(
                Modifier(
                    id=f"ally:{peer.id}",
                    source=peer.id,
                    value=peer.ally_value,
                    kind="ally",
                )
            )
        return sorted(
            derived,
            key=lambda modifier: modifier.id,
        )

    def all_modifiers(
        self,
        world: World,
        present: list[Subject],
    ) -> list[Modifier]:
        return sorted(
            [*self.modifiers, *self.derived_modifiers(world, present)],
            key=lambda modifier: modifier.id,
        )

    def layer_snapshot(
        self,
        world: World,
        present: list[Subject],
    ) -> dict[str, Any]:
        modifiers = [
            {
                "id": modifier.id,
                "value": round(modifier.value, 4),
                "active": modifier.active,
                "visible": modifier.visible,
            }
            for modifier in self.all_modifiers(world, present)
        ]
        belief = {
            target: {
                "known_modifiers": sorted(value.known_modifiers),
                "base_estimate": round(value.base_estimate, 4),
                "identity_seen": value.identity_seen,
                **(
                    {"misled_by": value.misled_by}
                    if value.misled_by is not None
                    else {}
                ),
            }
            for target, value in sorted(self.beliefs_about.items())
        }
        objective = {
            item: world.holder(item)
            for item in sorted(world.objectives)
        }
        snapshot: dict[str, Any] = {
            "ability": {
                "base": round(self.base, 4),
                "modifiers": modifiers,
            },
            "belief": belief,
            "resources": {
                "assets": _sorted_dict(
                    {
                        item: count
                        for item, count in self.inventory.items()
                        if count > 0
                    }
                ),
                "reputation": round(self.reputation, 4),
                "bonds": world.relations.bonds(self.id),
            },
            "phase": sorted(self.phase),
            "identity": {
                "true": self.identity_true,
                "displayed": self.identity_displayed,
            },
            "objective": objective,
            "pending": [
                {
                    key: (
                        effect[key].source
                        if key == "condition"
                        and hasattr(effect[key], "source")
                        else effect[key]
                    )
                    for key in sorted(effect)
                }
                for effect in sorted(
                    world.pending_effects,
                    key=lambda value: str(value.get("id", "")),
                )
                if effect.get("planted_by") == self.id
            ],
            "vitality": self.vitality,
            "zone": self.zone,
            "stress": round(self.stress, 4),
            "stamina": round(self.stamina, 4),
        }
        if self.beliefs:
            snapshot["valued_beliefs"] = {
                fact_id: {
                    "value": value.value,
                    "confidence": round(value.confidence, 4),
                }
                for fact_id, value in sorted(self.beliefs.items())
            }
        return snapshot
