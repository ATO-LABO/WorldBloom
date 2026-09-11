"""World loading, validation, predicates, and paths for implementation plan §3.1."""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

import yaml

from engine.predicate import (
    Namespace,
    Predicate,
    compile_predicate,
    compile_predicate_syntax,
)
from engine.relations import Relations

if TYPE_CHECKING:
    from engine.subject import Subject


_PREDICATE_NAMES = {
    "stance",
    "bonds",
    "awareness",
    "holds",
    "holder",
    "zone",
    "present",
    "vitality",
    "known",
    "knows_modifier",
    "strength",
    "believed_strength",
    "hostile_present",
    "turn",
    "day",
    "phase",
    "self",
}


@dataclass(frozen=True)
class Route:
    origin: str
    destination: str
    cost: float = 1.0
    requires_item: str | None = None


@dataclass(frozen=True)
class PathInfo:
    destination: str
    cost: float
    hops: int
    route: tuple[str, ...]


class World:
    """Validated immutable definitions plus simulation-owned shared state."""

    def __init__(self, definition: dict[str, Any], source: Path) -> None:
        self.source = source
        self.name = str(definition["name"])
        time = definition.get("time", {})
        self.days = int(time.get("days", 1))
        self.slots = tuple(str(slot) for slot in time.get("slots", []))
        if self.days < 1 or not self.slots:
            raise ValueError("World time requires positive days and slots")

        self.protagonist = str(definition["protagonist"])
        self.antagonist = str(definition["antagonist"])

        raw_zones = definition.get("zones", [])
        zone_names = [str(zone["name"]) for zone in raw_zones]
        if len(zone_names) != len(set(zone_names)):
            raise ValueError("World zone names must be unique")
        self.zones = {
            str(zone["name"]): dict(zone)
            for zone in sorted(raw_zones, key=lambda value: str(value["name"]))
        }

        self.routes: dict[str, tuple[Route, ...]] = {}
        for origin in sorted(definition.get("routes", {})):
            if origin not in self.zones:
                raise ValueError(f"Unknown route origin: {origin}")
            routes: list[Route] = []
            for raw_route in definition["routes"][origin] or []:
                destination = str(raw_route["to"])
                if destination not in self.zones:
                    raise ValueError(
                        f"Unknown route destination: {origin}->{destination}"
                    )
                cost = float(raw_route.get("cost", 1.0))
                if cost <= 0:
                    raise ValueError(
                        f"Route cost must be positive: {origin}->{destination}"
                    )
                routes.append(
                    Route(
                        origin=str(origin),
                        destination=destination,
                        cost=cost,
                        requires_item=raw_route.get("requires_item"),
                    )
                )
            self.routes[str(origin)] = tuple(
                sorted(routes, key=lambda route: route.destination)
            )

        self.movement = {
            "action_weight": float(
                definition.get("movement", {}).get("action_weight", 1.0)
            ),
            "hop_decay": float(
                definition.get("movement", {}).get("hop_decay", 1.0)
            ),
            "destination_weights": {
                str(key): float(value)
                for key, value in sorted(
                    definition.get("movement", {})
                    .get("destination_weights", {})
                    .items()
                )
            },
        }
        self.stamina = {
            "default_max": float(
                definition.get("stamina", {}).get("default_max", 10.0)
            ),
            "default_recover_per_slot": float(
                definition.get("stamina", {})
                .get("default_recover_per_slot", 1.0)
            ),
            "exhausted_ratio": float(
                definition.get("stamina", {}).get("exhausted_ratio", 0.2)
            ),
        }
        self.companionship = {
            "threshold": float(
                definition.get("companionship", {}).get("threshold", 0.6)
            ),
            "weight": float(
                definition.get("companionship", {}).get("weight", 1.0)
            ),
        }
        self.permission_restricted_weight = float(
            definition.get("permission", {}).get(
                "restricted_weight",
                0.15,
            )
        )
        self.awareness_per_encounter = float(
            definition.get("awareness_per_encounter", 0.0)
        )
        self.pulls = {
            str(key): float(value)
            for key, value in sorted((definition.get("pulls", {}) or {}).items())
        }

        raw_items = definition.get("items", [])
        item_names = [str(item["name"]) for item in raw_items]
        if len(item_names) != len(set(item_names)):
            raise ValueError("World item names must be unique")
        self.items = {
            str(item["name"]): dict(item)
            for item in sorted(raw_items, key=lambda value: str(value["name"]))
        }
        self.recipes = {
            name: dict(item["made_from"])
            for name, item in self.items.items()
            if item.get("made_from")
        }

        raw_facts = definition.get("facts", [])
        fact_ids = [str(fact["id"]) for fact in raw_facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("World fact ids must be unique")
        self.facts = {
            str(fact["id"]): dict(fact)
            for fact in sorted(raw_facts, key=lambda value: str(value["id"]))
        }

        self.default_strength_prior = float(
            definition.get("default_strength_prior", 50.0)
        )
        self.contest = {
            "tau": float(definition.get("contest", {}).get("tau", 10.0)),
            "epsilon": float(definition.get("contest", {}).get("epsilon", 5.0)),
        }
        if self.contest["tau"] <= 0:
            raise ValueError("contest.tau must be positive")

        self.vitality = {
            "revive_after": int(
                definition.get("vitality", {}).get("revive_after", 4)
            ),
            "ally_speedup": int(
                definition.get("vitality", {}).get("ally_speedup", 1)
            ),
            "revive_base_penalty": float(
                definition.get("vitality", {})
                .get("revive_base_penalty", 2.0)
            ),
            "lethal_exempt": {
                str(value)
                for value in definition.get("vitality", {}).get(
                    "lethal_exempt", []
                )
            },
        }
        self.grief = {
            "stress": float(definition.get("grief", {}).get("stress", 1.0)),
            "affinity_to_killer": float(
                definition.get("grief", {}).get("affinity_to_killer", -0.6)
            ),
        }

        self.thresholds: list[dict[str, Any]] = []
        for raw_threshold in definition.get("thresholds", []) or []:
            threshold = dict(raw_threshold)
            threshold["id"] = str(threshold["id"])
            threshold["predicate_source"] = str(threshold["when"])
            threshold["predicate"] = compile_predicate_syntax(
                threshold["predicate_source"]
            )
            self.thresholds.append(threshold)
        self.thresholds.sort(key=lambda value: value["id"])

        self.endings: list[dict[str, Any]] = []
        for raw_ending in definition.get("ending", []) or []:
            ending = dict(raw_ending)
            ending["id"] = str(ending["id"])
            source_when = ending["when"]
            if isinstance(source_when, dict):
                if (
                    set(source_when) != {"agent", "goal"}
                    or source_when["goal"] != "attained"
                ):
                    raise ValueError(f"Unsupported ending sugar: {source_when!r}")
                ending["sugar_agent"] = str(source_when["agent"])
                ending["predicate_source"] = None
                ending["predicate"] = None
            else:
                ending["predicate_source"] = str(source_when)
                ending["predicate"] = compile_predicate_syntax(
                    ending["predicate_source"]
                )
            self.endings.append(ending)
        self.endings.sort(key=lambda value: value["id"])
        self.target_ending = str(definition["target_ending"])
        if self.target_ending not in {
            ending["id"] for ending in self.endings
        }:
            raise ValueError(f"Unknown target ending: {self.target_ending}")

        self.scheduled_events = tuple(
            sorted(
                (dict(event) for event in definition.get("scheduled_events", [])),
                key=lambda event: (
                    int(event.get("day", 0)),
                    str(event.get("slot", "")),
                    str(event.get("id", "")),
                ),
            )
        )
        raw_daily = definition.get("daily_events")
        if raw_daily:
            self.daily_event_chance = float(raw_daily.get("chance", 0.0))
            self.daily_events = tuple(
                sorted(
                    (dict(event) for event in raw_daily.get("events", [])),
                    key=lambda event: str(event["id"]),
                )
            )
        else:
            self.daily_event_chance = 0.0
            self.daily_events = ()

        self.relations = Relations()
        self.subjects: dict[str, Subject] = {}
        self.objectives: dict[str, dict[str, Any]] = {}
        self.delivered: dict[str, str] = {}
        self.pending_effects: list[dict[str, Any]] = []

        self._validate_item_references()
        self._validate_recipe_cycles()
        self._validate_fact_sources()

    @classmethod
    def from_yaml(cls, path: str | Path) -> World:
        source = Path(path)
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"World YAML must contain a mapping: {source}")
        return cls(raw, source)

    def _validate_item_references(self) -> None:
        for origin in sorted(self.routes):
            for route in self.routes[origin]:
                if (
                    route.requires_item is not None
                    and route.requires_item not in self.items
                ):
                    raise ValueError(
                        f"Unknown route item: "
                        f"{origin}->{route.destination}:{route.requires_item}"
                    )
        for product in sorted(self.recipes):
            definition = self.items[product]
            for material in sorted(self.recipes[product]):
                if material not in self.items:
                    raise ValueError(
                        f"Unknown recipe material: {product}:{material}"
                    )
            required_fact = (definition.get("requires") or {}).get("knowledge")
            if required_fact is not None and required_fact not in self.facts:
                raise ValueError(
                    f"Unknown recipe knowledge: {product}:{required_fact}"
                )
            craft_zone = definition.get("craft_zone")
            if craft_zone is not None and craft_zone not in self.zones:
                raise ValueError(
                    f"Unknown craft zone: {product}:{craft_zone}"
                )
        for item, definition in sorted(self.items.items()):
            for source in definition.get("sources", []) or []:
                zone = source.get("zone")
                if zone is not None and zone not in self.zones:
                    raise ValueError(f"Unknown item source zone: {item}:{zone}")

    def _validate_recipe_cycles(self) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(item: str) -> None:
            if item in visiting:
                raise ValueError(f"Cyclic recipe dependency involving {item}")
            if item in visited:
                return
            visiting.add(item)
            for material in sorted(self.recipes.get(item, {})):
                if material in self.recipes:
                    visit(material)
            visiting.remove(item)
            visited.add(item)

        for item in sorted(self.recipes):
            visit(item)

    def _validate_fact_sources(self) -> None:
        for fact, definition in sorted(self.facts.items()):
            for source in definition.get("sources", []) or []:
                zone = source.get("zone")
                if zone is not None and zone not in self.zones:
                    raise ValueError(f"Unknown fact source zone: {fact}:{zone}")

    def bind_subjects(self, subjects: dict[str, Subject]) -> None:
        self.delivered.clear()
        self.pending_effects.clear()

        if set(subjects) != {subject.id for subject in subjects.values()}:
            raise ValueError("Subject dictionary keys must match Subject.id")

        subject_ids = set(subjects)
        item_collisions = subject_ids & set(self.items)
        if item_collisions:
            raise ValueError(
                "Subject ids collide with item names: "
                f"{sorted(item_collisions)}"
            )
        fact_collisions = subject_ids & set(self.facts)
        if fact_collisions:
            raise ValueError(
                "Subject ids collide with fact ids: "
                f"{sorted(fact_collisions)}"
            )

        if self.protagonist not in subjects:
            raise ValueError(f"Unknown protagonist: {self.protagonist}")
        if self.antagonist not in subjects:
            raise ValueError(f"Unknown antagonist: {self.antagonist}")

        relation_values: dict[str, dict[str, dict[str, float]]] = {}
        for subject_id in sorted(subjects):
            subject = subjects[subject_id]
            if subject.zone not in self.zones:
                raise ValueError(
                    f"Unknown subject entry zone: {subject.id}:{subject.zone}"
                )
            unknown_range = subject.range_zones - set(self.zones)
            if unknown_range:
                raise ValueError(
                    f"Unknown subject range zones: "
                    f"{subject.id}:{sorted(unknown_range)}"
                )
            for exclusion in subject.range_exclude:
                unknown = set(exclusion.get("zones", [])) - set(self.zones)
                if unknown:
                    raise ValueError(
                        f"Unknown excluded zones: {subject.id}:{sorted(unknown)}"
                    )
                item = exclusion.get("until_item")
                if item is not None and item not in self.items:
                    raise ValueError(
                        f"Unknown exclusion item: {subject.id}:{item}"
                    )
            for item in sorted(subject.inventory):
                if item not in self.items:
                    raise ValueError(
                        f"Unknown inventory item: {subject.id}:{item}"
                    )
            for fact in sorted(subject.knowledge):
                if fact not in self.facts:
                    raise ValueError(
                        f"Unknown subject fact: {subject.id}:{fact}"
                    )
            if (
                subject.goal.target is not None
                and subject.goal.target not in self.items
            ):
                raise ValueError(
                    f"Unknown goal target: {subject.id}:{subject.goal.target}"
                )
            if (
                subject.goal.deliver_to is not None
                and subject.goal.deliver_to not in self.zones
            ):
                raise ValueError(
                    f"Unknown delivery zone: "
                    f"{subject.id}:{subject.goal.deliver_to}"
                )
            for obstacle in subject.goal.obstacles:
                if obstacle not in subjects:
                    raise ValueError(
                        f"Unknown goal obstacle: {subject.id}:{obstacle}"
                    )
            for target in sorted(subject.initial_relations):
                if target not in subjects:
                    raise ValueError(
                        f"Unknown relation target: {subject.id}:{target}"
                    )
            relation_values[subject.id] = subject.initial_relations

        for fact, definition in sorted(self.facts.items()):
            for source in definition.get("sources", []) or []:
                agent = source.get("agent")
                if agent is not None and agent not in subjects:
                    raise ValueError(f"Unknown fact source agent: {fact}:{agent}")

        self.subjects = {
            subject_id: subjects[subject_id] for subject_id in sorted(subjects)
        }
        self.relations = Relations(relation_values)

        objectives: dict[str, dict[str, Any]] = {}
        for item, definition in sorted(self.items.items()):
            if not definition.get("objective", False):
                continue
            claimants = sorted(
                subject.id
                for subject in self.subjects.values()
                if subject.objective_claimant and subject.goal.target == item
            )
            objectives[item] = {"claimants": claimants}
        self.objectives = objectives

        predicate_names = (
            set(_PREDICATE_NAMES)
            | set(self.subjects)
            | set(self.items)
            | set(self.facts)
            | set(self.zones)
        )

        for threshold in self.thresholds:
            source = threshold.get("predicate_source")
            if not isinstance(source, str):
                raise ValueError(
                    f"Threshold predicate is unresolved: {threshold['id']}"
                )
            threshold["predicate"] = compile_predicate(
                source,
                predicate_names,
            )

        for ending in self.endings:
            ending.pop("deliver", None)
            sugar_agent = ending.get("sugar_agent")
            if sugar_agent is not None:
                if sugar_agent not in self.subjects:
                    raise ValueError(
                        f"Unknown ending agent: {sugar_agent}"
                    )
                goal = self.subjects[sugar_agent].goal
                if goal.target is None or goal.deliver_to is None:
                    raise ValueError(
                        f"Ending goal is not deliverable: {sugar_agent}"
                    )
                source = (
                    f"holds({sugar_agent}, {goal.target}) "
                    f"and zone({sugar_agent}) == {goal.deliver_to!r}"
                )
                ending["when"] = source
                ending["predicate_source"] = source
                ending["deliver"] = {
                    "subject": sugar_agent,
                    "item": goal.target,
                    "zone": goal.deliver_to,
                }

            source = ending.get("predicate_source")
            if not isinstance(source, str):
                raise ValueError(
                    f"Ending predicate is unresolved: {ending['id']}"
                )
            ending["predicate"] = compile_predicate(
                source,
                predicate_names,
            )

    def holder(self, item: str) -> str | None:
        if item in self.delivered:
            return self.delivered[item]
        holders = sorted(
            subject.id
            for subject in self.subjects.values()
            if subject.inventory.get(item, 0) > 0
        )
        return holders[0] if holders else None

    def present_subjects(
        self,
        zone: str,
        *,
        include_downed: bool = True,
    ) -> list[Subject]:
        allowed = {"alive", "revived", "downed"} if include_downed else {
            "alive",
            "revived",
        }
        return [
            subject
            for subject in self.subjects.values()
            if subject.zone == zone and subject.vitality in allowed
        ]

    def scheduled_for(self, day: int, slot: str | None) -> list[dict[str, Any]]:
        return [
            event
            for event in self.scheduled_events
            if int(event.get("day", 0)) == day
            and event.get("slot") == slot
        ]

    def _route_allowed(self, subject: Subject, route: Route) -> bool:
        item = route.requires_item
        if item is None:
            return True
        if subject.has_item(item):
            return True
        definition = self.items[item]
        access = definition.get("access", {}) or {}
        if access.get("mode") != "owner":
            return False
        if "companions" not in access.get("allow", []):
            return False
        for owner in self.present_subjects(subject.zone):
            if not owner.has_item(item):
                continue
            if owner.companions is not None and subject.id in owner.companions:
                return True
        return False

    def _excluded_zones(self, subject: Subject) -> set[str]:
        excluded: set[str] = set()
        for rule in subject.range_exclude:
            item = rule.get("until_item")
            if item is None or not subject.has_item(str(item)):
                excluded.update(str(zone) for zone in rule.get("zones", []))
        excluded.discard(subject.zone)
        return excluded

    def reachable_paths(self, subject: Subject) -> dict[str, PathInfo]:
        excluded = self._excluded_zones(subject)
        allowed_zones = subject.range_zones - excluded
        allowed_zones.add(subject.zone)
        best: dict[str, tuple[float, int, tuple[str, ...]]] = {
            subject.zone: (0.0, 0, (subject.zone,))
        }
        queue: list[tuple[float, int, tuple[str, ...], str]] = [
            (0.0, 0, (subject.zone,), subject.zone)
        ]

        while queue:
            cost, hops, route_names, origin = heapq.heappop(queue)
            if best.get(origin) != (cost, hops, route_names):
                continue
            for route in self.routes.get(origin, ()):
                destination = route.destination
                if destination not in allowed_zones:
                    continue
                if not self._route_allowed(subject, route):
                    continue
                next_cost = round(cost + route.cost, 4)
                if next_cost > subject.stamina:
                    continue
                candidate = (
                    next_cost,
                    hops + 1,
                    (*route_names, destination),
                )
                current = best.get(destination)
                if current is None or candidate < current:
                    best[destination] = candidate
                    heapq.heappush(
                        queue,
                        (
                            candidate[0],
                            candidate[1],
                            candidate[2],
                            destination,
                        ),
                    )

        return {
            destination: PathInfo(
                destination=destination,
                cost=value[0],
                hops=value[1],
                route=value[2],
            )
            for destination, value in sorted(best.items())
            if destination != subject.zone
        }

    def namespace(
        self,
        subject: Subject,
        present: list[Subject],
        *,
        turn: int,
        day: int,
        zone_override: str | None = None,
    ) -> Namespace:
        from engine.contest import believed_strength, strength

        present_by_id = {
            value.id: value
            for value in present
            if value.vitality != "dead"
        }

        def zone_of(subject_id: str) -> str:
            if subject_id == subject.id and zone_override is not None:
                return zone_override
            return self.subjects[subject_id].zone

        def is_present(subject_id: str) -> bool:
            return subject_id in present_by_id

        def hostile_present(subject_id: str) -> bool:
            actor = self.subjects[subject_id]
            for other in present:
                if other.id == actor.id or other.vitality == "dead":
                    continue
                if (
                    self.relations.stance(actor.id, other.id) < -0.2
                    or other.id in actor.goal.obstacles
                ):
                    return True
            return False

        namespace = Namespace()
        identifiers = (
            set(self.subjects)
            | set(self.items)
            | set(self.facts)
            | set(self.zones)
        )
        for identifier in sorted(identifiers):
            namespace[identifier] = identifier
        namespace.update(
            {
                "self": subject.id,
                "phase": set(subject.phase),
                "turn": turn,
                "day": day,
                "stance": self.relations.stance,
                "bonds": self.relations.bonds,
                "awareness": self.relations.awareness,
                "holds": lambda actor, item: self.subjects[actor].has_item(item),
                "holder": self.holder,
                "zone": zone_of,
                "present": is_present,
                "vitality": lambda actor: self.subjects[actor].vitality,
                "known": lambda actor, fact: fact
                in self.subjects[actor].knowledge,
                "knows_modifier": (
                    lambda actor, target, source: source
                    in self.subjects[actor]
                    .beliefs_about.get(target, _empty_belief(self))
                    .known_modifiers
                ),
                "strength": lambda actor: strength(
                    self.subjects[actor], self, present
                ),
                "believed_strength": lambda actor, target: believed_strength(
                    self.subjects[actor],
                    self.subjects[target],
                    self,
                    present,
                ),
                "hostile_present": hostile_present,
            }
        )
        return namespace

    def crossed_thresholds(
        self,
        subject: Subject,
        present: list[Subject],
        *,
        turn: int,
        day: int,
        zone_override: str | None = None,
    ) -> list[str]:
        namespace = self.namespace(
            subject,
            present,
            turn=turn,
            day=day,
            zone_override=zone_override,
        )
        crossed: list[str] = []
        for threshold in self.thresholds:
            threshold_id = threshold["id"]
            if threshold_id in subject.phase:
                continue
            predicate: Predicate = threshold["predicate"]
            if predicate.evaluate(namespace):
                crossed.append(threshold_id)
        return crossed

    def ending_reached(
        self,
        ending: dict[str, Any],
        subject: Subject,
        present: list[Subject],
        *,
        turn: int,
        day: int,
    ) -> bool:
        predicate: Predicate | None = ending.get("predicate")
        if predicate is None:
            raise ValueError(
                f"Ending predicate is unresolved: {ending['id']}"
            )
        namespace = self.namespace(
            subject,
            present,
            turn=turn,
            day=day,
        )
        return predicate.evaluate(namespace)


def _empty_belief(world: World) -> Any:
    from engine.subject import BeliefAbout

    return BeliefAbout(base_estimate=world.default_strength_prior)
