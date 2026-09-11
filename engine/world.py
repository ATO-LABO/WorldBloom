"""World loading, validation, predicates, and paths for implementation plan §3.1."""

from __future__ import annotations

import heapq
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

import yaml

from engine.predicate import (
    PREDICATE_NAMES,
    Namespace,
    Predicate,
    compile_predicate,
    compile_predicate_syntax,
)
from engine.relations import Relations
from engine.phase2 import (
    bind_phase2,
    configure_phase2,
    disguise_aliases,
    perceived_name as phase2_perceived_name,
)


def _load_action_graph(
    definition: dict[str, Any],
    source: Path,
    action_graph_path: str | Path | None = None,
) -> tuple[dict[str, Any], bool]:
    raw_path: str | Path | None = action_graph_path
    if raw_path is None:
        raw_gapengine = definition.get("gapengine")
        if raw_gapengine is None:
            return {"nodes": [], "edges": []}, False
        if not isinstance(raw_gapengine, dict):
            raise ValueError("world.gapengine must be a mapping")
        raw_path = raw_gapengine.get("action_graph")

    if raw_path is None:
        return {"nodes": [], "edges": []}, False
    if not isinstance(raw_path, (str, Path)) or not str(raw_path):
        raise ValueError(
            "world.gapengine.action_graph must be a path"
        )

    configured = Path(raw_path)
    project_root = Path(__file__).resolve().parents[1]
    if configured.is_absolute():
        candidates = [configured]
    else:
        candidates = [
            source.parent / configured,
            project_root / configured,
        ]

    checked: list[Path] = []
    graph_path: Path | None = None
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in checked:
            continue
        checked.append(resolved)
        if resolved.is_file():
            graph_path = resolved
            break

    if graph_path is None:
        attempted = ", ".join(str(path) for path in checked)
        raise ValueError(
            f"Action graph does not exist; tried: {attempted}"
        )

    raw_graph = yaml.safe_load(
        graph_path.read_text(encoding="utf-8")
    )
    if not isinstance(raw_graph, dict):
        raise ValueError(
            f"Action graph must contain a mapping: {graph_path}"
        )
    graph = dict(raw_graph)
    graph["nodes"] = list(graph.get("nodes", []) or [])
    graph["edges"] = list(graph.get("edges", []) or [])
    graph["_source"] = str(graph_path)
    return graph, True

if TYPE_CHECKING:
    from engine.subject import Subject




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

    def __init__(
        self,
        definition: dict[str, Any],
        source: Path,
        *,
        action_graph_path: str | Path | None = None,
    ) -> None:
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
            for zone in sorted(
                raw_zones,
                key=lambda value: str(value["name"]),
            )
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
                        f"Unknown route destination: "
                        f"{origin}->{destination}"
                    )
                cost = float(raw_route.get("cost", 1.0))
                if cost <= 0:
                    raise ValueError(
                        f"Route cost must be positive: "
                        f"{origin}->{destination}"
                    )
                routes.append(
                    Route(
                        origin=str(origin),
                        destination=destination,
                        cost=cost,
                        requires_item=raw_route.get(
                            "requires_item"
                        ),
                    )
                )
            self.routes[str(origin)] = tuple(
                sorted(
                    routes,
                    key=lambda route: route.destination,
                )
            )

        self.movement = {
            "action_weight": float(
                definition.get("movement", {}).get(
                    "action_weight",
                    1.0,
                )
            ),
            "hop_decay": float(
                definition.get("movement", {}).get(
                    "hop_decay",
                    1.0,
                )
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
                definition.get("stamina", {}).get(
                    "default_max",
                    10.0,
                )
            ),
            "default_recover_per_slot": float(
                definition.get("stamina", {}).get(
                    "default_recover_per_slot",
                    1.0,
                )
            ),
            "exhausted_ratio": float(
                definition.get("stamina", {}).get(
                    "exhausted_ratio",
                    0.2,
                )
            ),
        }
        self.companionship = {
            "threshold": float(
                definition.get("companionship", {}).get(
                    "threshold",
                    0.6,
                )
            ),
            "weight": float(
                definition.get("companionship", {}).get(
                    "weight",
                    1.0,
                )
            ),
        }

        self.action_graph, self.action_graph_enabled = (
            _load_action_graph(
                definition,
                source,
                action_graph_path,
            )
        )
        raw_genre = self.action_graph.get("genre")
        if raw_genre is None:
            self.genres = frozenset()
        elif isinstance(raw_genre, str):
            if not raw_genre:
                raise ValueError("action_graph.genre must not be empty")
            self.genres = frozenset((raw_genre,))
        elif isinstance(raw_genre, (list, tuple, set)):
            if any(
                not isinstance(value, str) or not value
                for value in raw_genre
            ):
                raise ValueError(
                    "action_graph.genre entries must be non-empty strings"
                )
            self.genres = frozenset(raw_genre)
        else:
            raise ValueError(
                "action_graph.genre must be a string or sequence"
            )

        self.rethink_stagnation_slots = max(
            1,
            int(self.action_graph.get("rethink_stagnation_slots", 3)),
        )
        raw_permission = (
            self.action_graph.get("permission", {}) or {}
        )
        if not isinstance(raw_permission, dict):
            raise ValueError(
                "action_graph.permission must be a mapping"
            )
        self.action_permissions = {
            str(verb): {
                str(role): str(level)
                for role, level in sorted((roles or {}).items())
            }
            for verb, roles in sorted(raw_permission.items())
            if isinstance(roles, dict)
        }
        self.permission_restricted_weight = float(
            self.action_graph.get(
                "restricted_weight",
                definition.get("permission", {}).get(
                    "restricted_weight",
                    0.15,
                ),
            )
        )
        if not 0.0 <= self.permission_restricted_weight <= 1.0:
            raise ValueError(
                "restricted_weight must be within [0, 1]"
            )

        raw_phase1 = definition.get("phase1", {}) or {}
        if not isinstance(raw_phase1, dict):
            raise ValueError("world.phase1 must be a mapping")
        self.open_bonus = float(
            raw_phase1.get("open_bonus", 0.5)
        )
        if self.open_bonus < 0.0:
            raise ValueError(
                "phase1.open_bonus must not be negative"
            )
        self.negotiate_threshold = float(
            raw_phase1.get("negotiate_threshold", 0.3)
        )
        raw_rewards = (
            raw_phase1.get("sacrifice_rewards", {}) or {}
        )
        if not isinstance(raw_rewards, dict):
            raise ValueError(
                "phase1.sacrifice_rewards must be a mapping"
            )
        self.sacrifice_rewards = {
            "asset_base": float(
                raw_rewards.get("asset_base", 8.0)
            ),
            "bond_stress": float(
                raw_rewards.get("bond_stress", -3.0)
            ),
            "bond_phase": str(
                raw_rewards.get("bond_phase", "決意")
            ),
        }

        self.awareness_per_encounter = float(
            definition.get("awareness_per_encounter", 0.0)
        )
        self.pulls = {
            str(key): float(value)
            for key, value in sorted(
                (definition.get("pulls", {}) or {}).items()
            )
        }

        raw_items = definition.get("items", [])
        item_names = [str(item["name"]) for item in raw_items]
        if len(item_names) != len(set(item_names)):
            raise ValueError("World item names must be unique")
        self.items = {
            str(item["name"]): dict(item)
            for item in sorted(
                raw_items,
                key=lambda value: str(value["name"]),
            )
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
            for fact in sorted(
                raw_facts,
                key=lambda value: str(value["id"]),
            )
        }

        raw_truth = definition.get("truth", {}) or {}
        if not isinstance(raw_truth, dict):
            raise ValueError("world.truth must be a mapping")

        self.truth: dict[str, str] = {}
        self.truth_candidates: dict[
            str,
            tuple[tuple[str, float], ...],
        ] = {}
        self.truth_known_by: dict[str, tuple[str, ...]] = {}
        self.drawn_truth: dict[str, str] = {}

        for raw_fact_id, raw_value in sorted(
            raw_truth.items(),
            key=lambda pair: str(pair[0]),
        ):
            fact_id = str(raw_fact_id)
            if not isinstance(raw_value, dict):
                self.truth[fact_id] = str(raw_value)
                continue

            raw_candidates = raw_value.get("candidates")
            if not isinstance(raw_candidates, dict) or not raw_candidates:
                raise ValueError(
                    f"Truth candidates must be a non-empty mapping: {fact_id}"
                )

            candidates: list[tuple[str, float]] = []
            for candidate, raw_weight in sorted(
                raw_candidates.items(),
                key=lambda pair: str(pair[0]),
            ):
                if (
                    not isinstance(raw_weight, (int, float))
                    or isinstance(raw_weight, bool)
                ):
                    raise ValueError(
                        f"Truth candidate weight must be numeric: "
                        f"{fact_id}:{candidate}"
                    )
                weight = float(raw_weight)
                if weight < 0.0:
                    raise ValueError(
                        f"Truth candidate weight must not be negative: "
                        f"{fact_id}:{candidate}"
                    )
                candidates.append((str(candidate), weight))
            if sum(weight for _, weight in candidates) <= 0.0:
                raise ValueError(
                    f"Truth candidate weights must contain a positive value: "
                    f"{fact_id}"
                )

            raw_known_by = raw_value.get("known_by", ())
            if isinstance(raw_known_by, str):
                known_by = (raw_known_by,)
            elif isinstance(raw_known_by, (list, tuple, set)):
                known_by = tuple(
                    sorted(str(value) for value in raw_known_by)
                )
            else:
                raise ValueError(
                    f"Truth known_by must be a string or sequence: {fact_id}"
                )

            self.truth_candidates[fact_id] = tuple(candidates)
            self.truth_known_by[fact_id] = known_by

        self.default_strength_prior = float(
            definition.get("default_strength_prior", 50.0)
        )
        self.contest = {
            "tau": float(
                definition.get("contest", {}).get(
                    "tau",
                    10.0,
                )
            ),
            "epsilon": float(
                definition.get("contest", {}).get(
                    "epsilon",
                    5.0,
                )
            ),
        }
        if self.contest["tau"] <= 0:
            raise ValueError("contest.tau must be positive")

        self.vitality = {
            "revive_after": int(
                definition.get("vitality", {}).get(
                    "revive_after",
                    4,
                )
            ),
            "ally_speedup": int(
                definition.get("vitality", {}).get(
                    "ally_speedup",
                    1,
                )
            ),
            "revive_base_penalty": float(
                definition.get("vitality", {}).get(
                    "revive_base_penalty",
                    2.0,
                )
            ),
            "lethal_exempt": {
                str(value)
                for value in definition.get(
                    "vitality",
                    {},
                ).get("lethal_exempt", [])
            },
        }
        self.grief = {
            "stress": float(
                definition.get("grief", {}).get(
                    "stress",
                    1.0,
                )
            ),
            "affinity_to_killer": float(
                definition.get("grief", {}).get(
                    "affinity_to_killer",
                    -0.6,
                )
            ),
        }

        self.thresholds: list[dict[str, Any]] = []
        for raw_threshold in (
            definition.get("thresholds", []) or []
        ):
            threshold = dict(raw_threshold)
            threshold["id"] = str(threshold["id"])
            threshold["predicate_source"] = str(
                threshold["when"]
            )
            threshold["predicate"] = compile_predicate_syntax(
                threshold["predicate_source"]
            )
            self.thresholds.append(threshold)
        self.thresholds.sort(key=lambda value: value["id"])

        self.endings: list[dict[str, Any]] = []
        for raw_ending in definition.get("ending", []) or []:
            ending = dict(raw_ending)
            ending["id"] = str(ending["id"])
            raw_delivery = ending.get("deliver")
            if raw_delivery is not None:
                if not isinstance(raw_delivery, dict):
                    raise ValueError(
                        f"Ending deliver must be a mapping: "
                        f"{ending['id']}"
                    )
                if set(raw_delivery) != {
                    "subject",
                    "item",
                    "zone",
                }:
                    raise ValueError(
                        f"Ending deliver requires subject/item/zone: "
                        f"{ending['id']}"
                    )
                ending["configured_deliver"] = {
                    "subject": str(raw_delivery["subject"]),
                    "item": str(raw_delivery["item"]),
                    "zone": str(raw_delivery["zone"]),
                }
            source_when = ending["when"]
            if isinstance(source_when, dict):
                if (
                    set(source_when) != {"agent", "goal"}
                    or source_when["goal"] != "attained"
                ):
                    raise ValueError(
                        f"Unsupported ending sugar: "
                        f"{source_when!r}"
                    )
                ending["sugar_agent"] = str(
                    source_when["agent"]
                )
                ending["predicate_source"] = None
                ending["predicate"] = None
            else:
                ending["predicate_source"] = str(source_when)
                ending["predicate"] = compile_predicate_syntax(
                    ending["predicate_source"]
                )
            self.endings.append(ending)
        self.set_target_ending(definition["target_ending"])

        self.scheduled_events = tuple(
            sorted(
                (
                    dict(event)
                    for event in definition.get(
                        "scheduled_events",
                        [],
                    )
                ),
                key=lambda event: (
                    int(event.get("day", 0)),
                    str(event.get("slot", "")),
                    str(event.get("id", "")),
                ),
            )
        )
        raw_daily = definition.get("daily_events")
        if raw_daily:
            self.daily_event_chance = float(
                raw_daily.get("chance", 0.0)
            )
            self.daily_events = tuple(
                sorted(
                    (
                        dict(event)
                        for event in raw_daily.get(
                            "events",
                            [],
                        )
                    ),
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
        self.confront_successes: set[tuple[str, str]] = set()
        self.pending_effects: list[dict[str, Any]] = []
        configure_phase2(self, definition, source)
        self.offers: dict[
            tuple[str, str],
            dict[str, Any],
        ] = {}
        self.pledges: dict[
            tuple[str, str],
            dict[str, Any],
        ] = {}

        self._validate_item_references()
        self._validate_recipe_cycles()
        self._validate_fact_sources()

    @classmethod
    def from_yaml(
        cls,
        path: str | Path,
        *,
        action_graph_path: str | Path | None = None,
    ) -> World:
        source = Path(path)
        raw = yaml.safe_load(
            source.read_text(encoding="utf-8")
        )
        if not isinstance(raw, dict):
            raise ValueError(
                f"World YAML must contain a mapping: {source}"
            )
        return cls(
            raw,
            source,
            action_graph_path=action_graph_path,
        )

    def resolve_truth(self, seed: int) -> dict[str, str]:
        """Resolve configured truth choices without touching the main RNG."""

        self.drawn_truth = {}
        for fact_id in sorted(self.truth_candidates):
            candidates = self.truth_candidates[fact_id]
            values = [value for value, _ in candidates]
            weights = [weight for _, weight in candidates]
            rng = random.Random(f"{int(seed)}:{fact_id}")
            selected = rng.choices(values, weights=weights, k=1)[0]
            self.truth[fact_id] = selected
            self.drawn_truth[fact_id] = selected
        return dict(self.drawn_truth)

    def set_target_ending(
        self,
        value: str | list[str] | tuple[str, ...],
    ) -> None:
        if isinstance(value, str):
            if not value:
                raise ValueError("Target ending must not be empty")
            targets = (value,)
            stored: str | tuple[str, ...] = value
        elif isinstance(value, (list, tuple)):
            if not value:
                raise ValueError("Target ending list must not be empty")
            if any(
                not isinstance(target, str) or not target
                for target in value
            ):
                raise ValueError(
                    "Target ending entries must be non-empty strings"
                )
            targets = tuple(value)
            if len(targets) != len(set(targets)):
                raise ValueError("Target endings must be unique")
            stored = targets
        else:
            raise ValueError(
                "target_ending must be a string or sequence of strings"
            )

        known = {
            str(ending["id"])
            for ending in self.endings
        }
        unknown = [
            target for target in targets if target not in known
        ]
        if unknown:
            raise ValueError(
                f"Unknown target ending: {unknown}"
            )

        self.target_ending = stored

    def target_endings(self) -> tuple[dict[str, Any], ...]:
        target_ids = (
            {self.target_ending}
            if isinstance(self.target_ending, str)
            else set(self.target_ending)
        )
        return tuple(
            ending
            for ending in self.endings
            if str(ending["id"]) in target_ids
        )

    def genre_allows(self, verb: str) -> bool:
        """Apply node genre tags only when the template declares a genre."""

        if not self.genres:
            return True

        nodes = [
            node
            for node in self.action_graph.get("nodes", []) or []
            if isinstance(node, dict) and node.get("verb") == verb
        ]
        if not nodes:
            return True

        for node in nodes:
            raw_genres = node.get("genres")
            if raw_genres is None:
                return True
            if isinstance(raw_genres, str):
                node_genres = {raw_genres}
            elif isinstance(raw_genres, (list, tuple, set)):
                node_genres = {str(value) for value in raw_genres}
            else:
                raise ValueError(
                    f"Action node genres must be a string or sequence: {verb}"
                )
            if self.genres & node_genres:
                return True
        return False

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
            raw_values = definition.get("values")
            if raw_values is not None:
                if (
                    not isinstance(raw_values, list)
                    or not raw_values
                    or len(raw_values)
                    != len({str(value) for value in raw_values})
                ):
                    raise ValueError(
                        f"Valued fact requires unique values: {fact}"
                    )
                definition["values"] = [
                    str(value) for value in raw_values
                ]
                if definition.get("sources"):
                    raise ValueError(
                        f"Valued fact cannot have direct sources: {fact}"
                    )

            for source in definition.get("sources", []) or []:
                zone = source.get("zone")
                if zone is not None and zone not in self.zones:
                    raise ValueError(
                        f"Unknown fact source zone: {fact}:{zone}"
                    )

            for relation in ("implies", "refutes"):
                raw_update = definition.get(relation)
                if raw_update is None:
                    continue
                if not isinstance(raw_update, dict):
                    raise ValueError(
                        f"Fact {relation} must be a mapping: {fact}"
                    )
                target_fact = str(raw_update.get("fact", ""))
                target = self.facts.get(target_fact)
                if target is None:
                    raise ValueError(
                        f"Unknown {relation} fact: "
                        f"{fact}:{target_fact}"
                    )
                allowed = {
                    str(value)
                    for value in target.get("values", []) or []
                }
                if not allowed:
                    raise ValueError(
                        f"{relation} target is not a valued fact: "
                        f"{fact}:{target_fact}"
                    )
                value = str(raw_update.get("value", ""))
                if value not in allowed:
                    raise ValueError(
                        f"Unknown {relation} value: "
                        f"{fact}:{target_fact}:{value}"
                    )
                confidence = float(
                    raw_update.get("confidence", 0.5)
                )
                if not 0.0 <= confidence <= 1.0:
                    raise ValueError(
                        f"{relation} confidence must be within [0, 1]: "
                        f"{fact}"
                    )

        for fact_id, value in sorted(self.truth.items()):
            definition = self.facts.get(fact_id)
            if definition is None:
                raise ValueError(f"Unknown truth fact: {fact_id}")
            allowed = {
                str(candidate)
                for candidate in definition.get("values", []) or []
            }
            if value not in allowed:
                raise ValueError(
                    f"Truth is not an allowed fact value: "
                    f"{fact_id}:{value}"
                )

        allowed_requirements = {
            "known_modifier",
            "observed",
            "pledged",
        }
        for raw_edge in self.action_graph.get("edges", []) or []:
            if not isinstance(raw_edge, dict):
                raise ValueError("Action graph edge must be a mapping")
            source = raw_edge.get("from")
            destination = raw_edge.get("to")
            requirement = raw_edge.get("requires")
            if not isinstance(source, str) or not source:
                raise ValueError("Action graph edge requires from")
            if not isinstance(destination, str) or not destination:
                raise ValueError("Action graph edge requires to")
            if requirement not in allowed_requirements:
                raise ValueError(
                    f"Unknown action prerequisite: {requirement}"
                )

        allowed_levels = {"allow", "restricted", "deny"}
        allowed_roles = {"hostile", "neutral", "ally", "self"}
        for verb, roles in sorted(self.action_permissions.items()):
            unknown_roles = set(roles) - allowed_roles
            if unknown_roles:
                raise ValueError(
                    f"Unknown permission roles for {verb}: "
                    f"{sorted(unknown_roles)}"
                )
            unknown_levels = set(roles.values()) - allowed_levels
            if unknown_levels:
                raise ValueError(
                    f"Unknown permission levels for {verb}: "
                    f"{sorted(unknown_levels)}"
                )

    def bind_subjects(self, subjects: dict[str, Subject]) -> None:
        self.delivered.clear()
        self.confront_successes.clear()
        self.pending_effects.clear()
        self.offers.clear()
        self.pledges.clear()

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
        zone_collisions = subject_ids & set(self.zones)
        if zone_collisions:
            raise ValueError(
                "Subject ids collide with zone names: "
                f"{sorted(zone_collisions)}"
            )

        if self.protagonist not in subjects:
            raise ValueError(f"Unknown protagonist: {self.protagonist}")
        if self.antagonist not in subjects:
            raise ValueError(f"Unknown antagonist: {self.antagonist}")

        relation_values: dict[str, dict[str, dict[str, float]]] = {}
        relation_aliases = disguise_aliases(self)
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
                if self.facts[fact].get("values") is not None:
                    raise ValueError(
                        f"Valued fact cannot be boolean knowledge: "
                        f"{subject.id}:{fact}"
                    )
            for fact_id, belief in sorted(subject.beliefs.items()):
                definition = self.facts.get(fact_id)
                if definition is None:
                    raise ValueError(
                        f"Unknown subject belief: "
                        f"{subject.id}:{fact_id}"
                    )
                allowed = {
                    str(value)
                    for value in definition.get("values", []) or []
                }
                if not allowed:
                    raise ValueError(
                        f"Subject belief is not a valued fact: "
                        f"{subject.id}:{fact_id}"
                    )
                if belief.value not in allowed:
                    raise ValueError(
                        f"Unknown subject belief value: "
                        f"{subject.id}:{fact_id}:{belief.value}"
                    )
                if not 0.0 <= belief.confidence <= 1.0:
                    raise ValueError(
                        f"Subject belief confidence must be within "
                        f"[0, 1]: {subject.id}:{fact_id}"
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
                if (
                    target not in subjects
                    and target not in relation_aliases
                ):
                    raise ValueError(
                        f"Unknown relation target: {subject.id}:{target}"
                    )
            relation_values[subject.id] = subject.initial_relations

        for fact, definition in sorted(self.facts.items()):
            raw_known_by = definition.get("known_by", ())
            if isinstance(raw_known_by, str):
                definition_known_by = (raw_known_by,)
            elif isinstance(raw_known_by, (list, tuple, set)):
                definition_known_by = tuple(
                    str(value) for value in raw_known_by
                )
            else:
                raise ValueError(
                    f"Fact known_by must be a string or sequence: {fact}"
                )

            owners = list(definition_known_by)
            secret_of = definition.get("secret_of")
            if secret_of is not None:
                owners.append(str(secret_of))
            owners.extend(self.truth_known_by.get(fact, ()))

            unknown_owners = sorted(set(owners) - subject_ids)
            if unknown_owners:
                raise ValueError(
                    f"Unknown fact owner subject: "
                    f"{fact}:{unknown_owners}"
                )

            for source in definition.get("sources", []) or []:
                agent = source.get("agent")
                if agent is not None and agent not in subjects:
                    raise ValueError(
                        f"Unknown fact source agent: {fact}:{agent}"
                    )

        self.subjects = {
            subject_id: subjects[subject_id]
            for subject_id in sorted(subjects)
        }

        from engine.subject import Belief

        for fact_id, truth in sorted(self.truth.items()):
            definition = self.facts.get(fact_id, {})
            if not definition.get("values"):
                continue

            raw_known_by = definition.get("known_by", ())
            if isinstance(raw_known_by, str):
                known_by = {raw_known_by}
            else:
                known_by = {
                    str(value)
                    for value in raw_known_by or ()
                }
            known_by.update(self.truth_known_by.get(fact_id, ()))

            secret_of = definition.get("secret_of")
            if secret_of is not None:
                known_by.add(str(secret_of))

            for subject_id in sorted(known_by):
                self.subjects[subject_id].beliefs[fact_id] = Belief(
                    value=truth,
                    confidence=1.0,
                )
            if (
                secret_of is not None
                and str(secret_of) not in subjects
            ):
                raise ValueError(
                    f"Unknown fact secret_of subject: "
                    f"{fact}:{secret_of}"
                )
            for source in definition.get("sources", []) or []:
                agent = source.get("agent")
                if agent is not None and agent not in subjects:
                    raise ValueError(
                        f"Unknown fact source agent: {fact}:{agent}"
                    )

        self.subjects = {
            subject_id: subjects[subject_id] for subject_id in sorted(subjects)
        }
        self.relations = Relations(
            relation_values,
            target_resolver=self.perceived_name,
        )

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
            set(PREDICATE_NAMES)
            | set(self.subjects)
            | set(self.items)
            | set(self.facts)
            | set(self.zones)
            | relation_aliases
        )
        bind_phase2(self, predicate_names)

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
            configured_delivery = ending.get(
                "configured_deliver"
            )
            if configured_delivery is not None:
                subject_id = str(
                    configured_delivery["subject"]
                )
                item = str(configured_delivery["item"])
                zone = str(configured_delivery["zone"])
                if subject_id not in self.subjects:
                    raise ValueError(
                        f"Unknown ending delivery subject: "
                        f"{ending['id']}:{subject_id}"
                    )
                if item not in self.items:
                    raise ValueError(
                        f"Unknown ending delivery item: "
                        f"{ending['id']}:{item}"
                    )
                if zone not in self.zones:
                    raise ValueError(
                        f"Unknown ending delivery zone: "
                        f"{ending['id']}:{zone}"
                    )
                ending["deliver"] = {
                    "subject": subject_id,
                    "item": item,
                    "zone": zone,
                }
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

    def perceived_name(
        self,
        observer: str,
        target: str,
    ) -> str:
        return phase2_perceived_name(
            self,
            observer,
            target,
        )

    def target_role(
        self,
        actor: Subject,
        target: Subject,
    ) -> str:
        if actor.id == target.id:
            return "self"

        perceived = self.perceived_name(
            actor.id,
            target.id,
        )
        if (
            self.relations.stance(actor.id, target.id)
            >= self.companionship["threshold"]
        ):
            return "ally"
        if (
            self.relations.stance(actor.id, target.id) < -0.2
            or perceived in actor.goal.obstacles
        ):
            return "hostile"
        return "neutral"

    def permission(
        self,
        verb: str,
        role: str,
    ) -> float:
        if not self.action_graph_enabled:
            return 1.0
        level = self.action_permissions.get(verb, {}).get(
            role,
            "allow",
        )
        if level == "allow":
            return 1.0
        if level == "restricted":
            return self.permission_restricted_weight
        if level == "deny":
            return 0.0
        raise ValueError(f"Unknown permission level: {verb}:{role}")

    @staticmethod
    def pledge_key(first: str, second: str) -> tuple[str, str]:
        return tuple(sorted((first, second)))

    def is_pledged(self, first: str, second: str) -> bool:
        return self.pledge_key(first, second) in self.pledges

    def prerequisite_ok(
        self,
        verb: str,
        actor: Subject,
        target: Subject | None,
        *,
        source: str | None = None,
    ) -> bool:
        if not self.action_graph_enabled:
            return True

        relevant = [
            edge
            for edge in self.action_graph.get("edges", []) or []
            if str(edge.get("to", "")) == verb
        ]
        for edge in relevant:
            requirement = str(edge["requires"])
            if target is None:
                return False
            if requirement == "known_modifier":
                belief = actor.beliefs_about.get(target.id)
                if (
                    source is None
                    or belief is None
                    or source not in belief.known_modifiers
                ):
                    return False
            elif requirement == "observed":
                belief = actor.beliefs_about.get(target.id)
                if belief is None or not (
                    belief.identity_seen or belief.known_modifiers
                ):
                    return False
            elif requirement == "pledged":
                if not self.is_pledged(actor.id, target.id):
                    return False
            else:
                return False
        return True

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
        bindings: dict[str, Any] | None = None,
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
                perceived = self.perceived_name(
                    actor.id,
                    other.id,
                )
                if (
                    self.relations.stance(actor.id, other.id) < -0.2
                    or perceived in actor.goal.obstacles
                ):
                    return True
            return False

        namespace = Namespace()
        identifiers = (
            set(self.subjects)
            | set(self.items)
            | set(self.facts)
            | set(self.zones)
            | disguise_aliases(self)
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
                "confront_success": (
                    lambda actor, fact: (actor, fact)
                    in self.confront_successes
                ),
            }
        )
        if bindings:
            namespace.update(
                {
                    str(name): value
                    for name, value in sorted(bindings.items())
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
