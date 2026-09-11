"""Action values and deterministic candidate generation for plan §3.6."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from engine.contest import believed_strength, strength
from engine.phase2 import (
    available_verbs,
    dedicated_plant_options,
    disguise_options,
    grand_gesture_asset,
    grand_gesture_fact,
    ready_chosen_effects,
    trial_options,
)

if TYPE_CHECKING:
    from engine.subject import Subject
    from engine.world import World


@dataclass
class Action:
    verb: str
    args: tuple[Any, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)


def _action_key(candidate: tuple[Action, float]) -> tuple[str, tuple[str, ...]]:
    action = candidate[0]
    return action.verb, tuple(str(value) for value in action.args)


def _present(subject: Subject, world: World) -> list[Subject]:
    return world.present_subjects(subject.zone)


def _is_hostile(actor: Subject, target: Subject, world: World) -> bool:
    return world.target_role(actor, target) == "hostile"


def _hostiles(
    subject: Subject,
    world: World,
    present: list[Subject],
) -> list[Subject]:
    return [
        target
        for target in present
        if target.id != subject.id
        and target.vitality in {"alive", "revived"}
        and _is_hostile(subject, target, world)
    ]


def _living_targets(
    subject: Subject,
    present: list[Subject],
) -> list[Subject]:
    return [
        target
        for target in sorted(present, key=lambda value: value.id)
        if target.id != subject.id
        and target.vitality in {"alive", "revived"}
    ]


def _permission_weight(
    subject: Subject,
    target: Subject,
    verb: str,
    world: World,
) -> float:
    return world.permission(
        verb,
        world.target_role(subject, target),
    )


def _normalize_opened(
    weighted: list[tuple[Action, float]],
    subject: Subject,
    world: World,
    single_weight: float,
) -> list[tuple[Action, float]]:
    """Normalize an opened single-target verb, then apply permission.

    Candidate weights supplied by callers are ``prior * permission``.
    Permission is divided out when calculating the prior denominator so a
    restricted target reduces the resulting verb mass instead of causing the
    remaining candidates to absorb that mass.
    """

    if not world.action_graph_enabled or not weighted:
        return weighted

    candidates_with_priors: list[
        tuple[Action, float, float]
    ] = []
    prior_total = 0.0

    for action, weight in weighted:
        permission = 1.0
        target_id = action.meta.get("target")
        if isinstance(target_id, str) and target_id in world.subjects:
            permission = _permission_weight(
                subject,
                world.subjects[target_id],
                action.verb,
                world,
            )
        if permission <= 0.0:
            continue

        prior = max(0.0, float(weight)) / permission
        if prior <= 0.0:
            continue
        candidates_with_priors.append(
            (action, prior, permission)
        )
        prior_total += prior

    if prior_total <= 0.0:
        return []

    mass = max(
        0.0,
        float(single_weight) * (1.0 + world.open_bonus),
    )
    return [
        (
            action,
            mass * prior / prior_total * permission,
        )
        for action, prior, permission in candidates_with_priors
    ]


def _observed(
    subject: Subject,
    target: Subject,
) -> bool:
    belief = subject.beliefs_about.get(target.id)
    return belief is not None and bool(
        belief.identity_seen or belief.known_modifiers
    )


def _betrayal_meta(
    subject: Subject,
    target: Subject,
    world: World,
) -> dict[str, Any]:
    if not world.is_pledged(subject.id, target.id):
        return {"betrayal": False}
    return {
        "betrayal": True,
        "subtype": "betray",
    }


def _under_threat(
    subject: Subject,
    world: World,
    present: list[Subject],
) -> bool:
    actor_strength = strength(subject, world, present)
    return any(
        believed_strength(subject, target, world, present)
        >= actor_strength
        for target in _hostiles(subject, world, present)
    )


def _missing_recipe_materials(
    subject: Subject,
    world: World,
) -> set[str]:
    missing: set[str] = set()
    for product in sorted(world.recipes):
        if subject.has_item(product):
            continue
        definition = world.items[product]
        required_fact = (definition.get("requires") or {}).get("knowledge")
        if (
            required_fact is not None
            and required_fact not in subject.knowledge
        ):
            continue
        recipe = world.recipes[product]
        for material, required in sorted(recipe.items()):
            if subject.inventory.get(material, 0) < int(required):
                missing.add(material)
    return missing


def _gather_zones(subject: Subject, world: World) -> set[str]:
    zones: set[str] = set()
    missing = _missing_recipe_materials(subject, world)
    for item in sorted(missing):
        for source in world.items[item].get("sources", []) or []:
            if source.get("type") == "investigate" and source.get("zone"):
                zones.add(str(source["zone"]))
    return zones


def _can_craft(subject: Subject, item: str, world: World) -> bool:
    definition = world.items[item]
    recipe = world.recipes[item]
    required_fact = (definition.get("requires") or {}).get("knowledge")
    if required_fact is not None and required_fact not in subject.knowledge:
        return False
    craft_zone = definition.get("craft_zone")
    if craft_zone is not None and subject.zone != craft_zone:
        return False
    return all(
        subject.inventory.get(material, 0) >= int(required)
        for material, required in recipe.items()
    )


def _craft_ready_destinations(
    subject: Subject,
    world: World,
) -> set[str]:
    destinations: set[str] = set()
    for item in sorted(world.recipes):
        definition = world.items[item]
        required_fact = (definition.get("requires") or {}).get("knowledge")
        if required_fact is not None and required_fact not in subject.knowledge:
            continue
        recipe = world.recipes[item]
        if not all(
            subject.inventory.get(material, 0) >= int(required)
            for material, required in recipe.items()
        ):
            continue
        craft_zone = definition.get("craft_zone")
        if craft_zone is not None:
            destinations.add(str(craft_zone))
    return destinations


def _movement_candidates(
    subject: Subject,
    world: World,
    sim: Any,
    present: list[Subject],
) -> list[tuple[Action, float]]:
    if "move" not in subject.verbs:
        return []

    paths = world.reachable_paths(subject)
    if not paths:
        return []

    destination_weights: dict[str, float] = {}
    objective_holder = (
        world.holder(subject.goal.target)
        if subject.goal.target is not None
        else None
    )
    gather_zones = _gather_zones(subject, world)
    craft_zones = _craft_ready_destinations(subject, world)

    companion_destination: str | None = None
    reachable_zones = set(paths)
    companions = [
        peer
        for peer in world.subjects.values()
        if peer.id != subject.id
        and peer.vitality != "dead"
        and peer.zone in reachable_zones
        and world.relations.stance(subject.id, peer.id)
        >= world.companionship["threshold"]
    ]
    if companions:
        companion_destination = sorted(
            companions,
            key=lambda peer: peer.id,
        )[0].zone

    for destination, path in sorted(paths.items()):
        weight = world.movement["destination_weights"].get(destination, 1.0)
        weight *= world.movement["hop_decay"] ** max(0, path.hops - 1)

        if (
            subject.goal.target is not None
            and not subject.has_item(subject.goal.target)
            and objective_holder is not None
            and objective_holder in world.subjects
            and world.subjects[objective_holder].zone == destination
            and sim.day >= int(world.pulls.get("pursue_from_day", 1))
        ):
            weight *= world.pulls.get("pursue", 1.0)

        if (
            subject.goal.target is not None
            and subject.has_item(subject.goal.target)
            and subject.goal.deliver_to == destination
        ):
            weight *= world.pulls.get("deliver", 1.0)

        if destination in gather_zones:
            weight *= world.pulls.get("gather", 1.0)

        if destination in craft_zones:
            weight *= world.pulls.get("craft", 1.0)

        if destination == companion_destination:
            weight *= world.companionship["weight"]

        destination_weights[destination] = max(0.0, weight)

    total = sum(destination_weights.values())
    if total <= 0.0:
        return []

    candidates_out: list[tuple[Action, float]] = []
    for destination in sorted(destination_weights):
        path = paths[destination]
        crossed = world.crossed_thresholds(
            subject,
            present,
            turn=sim.turn,
            day=sim.day,
            zone_override=destination,
        )
        weight = (
            world.movement["action_weight"]
            * destination_weights[destination]
            / total
        )
        if subject.exhausted:
            weight *= 0.3
        candidates_out.append(
            (
                Action(
                    "move",
                    (destination,),
                    {
                        "dest": destination,
                        "cost": path.cost,
                        "hops": path.hops,
                        "route": list(path.route),
                        "crossing": bool(crossed),
                    },
                ),
                weight,
            )
        )
    return candidates_out


def _investigate_candidates(
    subject: Subject,
    world: World,
) -> list[tuple[Action, float]]:
    if "investigate" not in subject.verbs:
        return []
    missing = _missing_recipe_materials(subject, world)
    gather_here = False
    for item in sorted(missing):
        for source in world.items[item].get("sources", []) or []:
            if (
                source.get("type") == "investigate"
                and source.get("zone") == subject.zone
            ):
                gather_here = True
                break
        if gather_here:
            break
    weight = 0.35 + subject.traits["curiosity"]
    if gather_here:
        weight += 1.0 + subject.traits["diligence"]
    return [
        (
            Action(
                "investigate",
                (subject.zone,),
                {"target": subject.zone, "gather": gather_here},
            ),
            weight,
        )
    ]


def _observe_candidates(
    subject: Subject,
    world: World,
    present: list[Subject],
) -> list[tuple[Action, float]]:
    if "observe" not in subject.verbs:
        return []

    result: list[tuple[Action, float]] = []
    single_weight = 0.3 + subject.traits["curiosity"] * 0.6
    for target in sorted(present, key=lambda value: value.id):
        if target.id == subject.id or target.vitality == "dead":
            continue

        permission = _permission_weight(
            subject,
            target,
            "observe",
            world,
        )
        if permission <= 0.0:
            continue

        belief = subject.beliefs_about.get(target.id)
        known = belief.known_modifiers if belief is not None else set()
        identity_seen = (
            belief.identity_seen if belief is not None else False
        )
        misled_by = (
            belief.misled_by if belief is not None else None
        )
        hidden = any(
            modifier.active
            and not modifier.visible
            and modifier.source not in known
            for modifier in target.all_modifiers(world, present)
        )
        if not hidden and identity_seen and misled_by is None:
            continue

        result.append(
            (
                Action(
                    "observe",
                    (target.id,),
                    {"target": target.id},
                ),
                single_weight * permission,
            )
        )
    return result


def _neutralize_candidates(
    subject: Subject,
    world: World,
    present: list[Subject],
) -> list[tuple[Action, float]]:
    if "neutralize" not in subject.verbs:
        return []

    result: list[tuple[Action, float]] = []
    single_weight = (
        0.2
        + subject.traits["curiosity"] * 0.4
        + subject.traits["stubbornness"] * 0.3
    )
    for target in _living_targets(subject, present):
        permission = _permission_weight(
            subject,
            target,
            "neutralize",
            world,
        )
        if permission <= 0.0:
            continue
        belief = subject.beliefs_about.get(target.id)
        if belief is None:
            continue
        active_sources = {
            modifier.source
            for modifier in target.all_modifiers(world, present)
            if modifier.active
            and modifier.source in belief.known_modifiers
        }
        for source in sorted(active_sources):
            if not world.prerequisite_ok(
                "neutralize",
                subject,
                target,
                source=source,
            ):
                continue
            meta = {
                "target": target.id,
                "source": source,
                "stance_sign": -1,
                "risk": "risky",
            }
            meta.update(_betrayal_meta(subject, target, world))
            result.append(
                (
                    Action(
                        "neutralize",
                        (target.id, source),
                        meta,
                    ),
                    single_weight * permission,
                )
            )
    return _normalize_opened(
        result,
        subject,
        world,
        single_weight,
    )


def _sabotage_candidates(
    subject: Subject,
    world: World,
    present: list[Subject],
) -> list[tuple[Action, float]]:
    if "sabotage" not in subject.verbs:
        return []

    single_weight = (
        0.1 + subject.traits["stubbornness"] * 0.3
    )
    result: list[tuple[Action, float]] = []
    for target in _living_targets(subject, present):
        if not _observed(subject, target):
            continue
        if not world.prerequisite_ok(
            "sabotage",
            subject,
            target,
        ):
            continue
        permission = _permission_weight(
            subject,
            target,
            "sabotage",
            world,
        )
        if permission <= 0.0:
            continue
        meta = {
            "target": target.id,
            "stance_sign": -1,
            "risk": "risky",
        }
        meta.update(_betrayal_meta(subject, target, world))
        result.append(
            (
                Action("sabotage", (target.id,), meta),
                single_weight * permission,
            )
        )
    return _normalize_opened(
        result,
        subject,
        world,
        single_weight,
    )


def _sacrifice_candidates(
    subject: Subject,
    world: World,
) -> list[tuple[Action, float]]:
    if "sacrifice" not in subject.verbs:
        return []

    result: list[tuple[Action, float]] = []
    valuable_assets = [
        item
        for item in sorted(subject.inventory)
        if subject.inventory[item] > 0
        and not world.items[item].get("keepsake", False)
        and (
            bool(world.items[item].get("modifier"))
            or (
                world.items[item].get("lootable", False)
                and item not in world.objectives
            )
        )
    ]
    if valuable_assets:
        result.append(
            (
                Action(
                    "sacrifice",
                    ("asset",),
                    {
                        "kind": "asset",
                        "item": valuable_assets[0],
                        "risk": "risky",
                    },
                ),
                0.05,
            )
        )

    allies = [
        target
        for target in world.present_subjects(subject.zone)
        if target.id != subject.id
        and target.vitality in {"alive", "revived"}
        and world.relations.stance(target.id, subject.id)
        >= world.companionship["threshold"]
    ]
    if allies:
        selected = sorted(
            allies,
            key=lambda target: (
                -world.relations.stance(target.id, subject.id),
                target.id,
            ),
        )[0]
        result.append(
            (
                Action(
                    "sacrifice",
                    ("bond",),
                    {
                        "kind": "bond",
                        "target": selected.id,
                        "risk": "risky",
                    },
                ),
                0.05,
            )
        )
    return result


def _mislead_candidates(
    subject: Subject,
    world: World,
    present: list[Subject],
) -> list[tuple[Action, float]]:
    if "mislead" not in subject.verbs:
        return []

    single_weight = (
        0.1
        + (1.0 - subject.traits["social"]) * 0.3
        + subject.traits["curiosity"] * 0.2
    )
    true_strength = strength(subject, world, present)
    result: list[tuple[Action, float]] = []

    for target in _living_targets(subject, present):
        permission = _permission_weight(
            subject,
            target,
            "mislead",
            world,
        )
        if permission <= 0.0:
            continue

        for factor in (0.6, 1.4):
            value = round(true_strength * factor, 6)
            result.append(
                (
                    Action(
                        "mislead",
                        (target.id, subject.id, value),
                        {
                            "target": target.id,
                            "about": subject.id,
                            "value": value,
                            "belief_kind": "strength",
                            "stance_sign": -1,
                        },
                    ),
                    single_weight * permission,
                )
            )

        for fact_id, belief in sorted(subject.beliefs.items()):
            definition = world.facts.get(fact_id, {})
            values = [
                str(value)
                for value in definition.get("values", []) or []
            ]
            truth = world.truth.get(fact_id)
            for value in sorted(values):
                if value == truth:
                    continue
                result.append(
                    (
                        Action(
                            "mislead",
                            (target.id, fact_id, value),
                            {
                                "target": target.id,
                                "about": fact_id,
                                "value": value,
                                "confidence": belief.confidence,
                                "belief_kind": "valued_fact",
                                "stance_sign": -1,
                            },
                        ),
                        single_weight * permission,
                    )
                )
    return _normalize_opened(
        result,
        subject,
        world,
        single_weight,
    )


def _confront_candidates(
    subject: Subject,
    world: World,
    present: list[Subject],
) -> list[tuple[Action, float]]:
    if "confront" not in subject.verbs:
        return []

    present_by_id = {
        target.id: target
        for target in _living_targets(subject, present)
    }
    result: list[tuple[Action, float]] = []
    single_weights: list[float] = []

    for fact_id, belief in sorted(subject.beliefs.items()):
        definition = world.facts.get(fact_id, {})
        if not definition.get("values"):
            continue
        threshold = float(definition.get("act_threshold", 0.6))
        target = present_by_id.get(belief.value)
        if target is None or belief.confidence < threshold:
            continue

        permission = _permission_weight(
            subject,
            target,
            "confront",
            world,
        )
        if permission <= 0.0:
            continue

        single_weight = (
            0.15
            + belief.confidence * 1.2
            + subject.traits["temper"] * 0.3
        )
        single_weights.append(single_weight)
        result.append(
            (
                Action(
                    "confront",
                    (target.id, fact_id),
                    {
                        "target": target.id,
                        "fact": fact_id,
                        "value": belief.value,
                        "confidence": belief.confidence,
                        "risk": "risky",
                        "stance_sign": -1,
                    },
                ),
                single_weight * permission,
            )
        )

    mean_weight = (
        sum(single_weights) / len(single_weights)
        if single_weights
        else 0.0
    )
    return _normalize_opened(
        result,
        subject,
        world,
        mean_weight,
    )


def evidence_replay_order(
    world: World,
    fact_id: str,
) -> tuple[int, float, str]:
    """Rethink replay order: refutes first, then implies, confidence desc, id.

    Shared by the rethink candidate gate and VerbEngine._rethink so the
    order recorded in the action meta always matches the order replayed.
    """

    definition = world.facts.get(fact_id, {})
    for rank, relation in ((0, "refutes"), (1, "implies")):
        update = definition.get(relation)
        if isinstance(update, dict):
            return (
                rank,
                -float(update.get("confidence", 0.5)),
                fact_id,
            )
    return (2, 0.0, fact_id)


def _rethink_candidates(
    subject: Subject,
    world: World,
    sim: Any,
) -> list[tuple[Action, float]]:
    if "rethink" not in subject.verbs:
        return []

    evidence = [
        fact_id
        for fact_id in subject.knowledge
        if any(
            isinstance(
                world.facts.get(fact_id, {}).get(relation),
                dict,
            )
            for relation in ("implies", "refutes")
        )
    ]
    if len(evidence) < 2:
        return []
    evidence.sort(
        key=lambda fact_id: evidence_replay_order(world, fact_id)
    )

    last_fact_turn = int(
        getattr(sim, "_last_fact_turn", {}).get(subject.id, 0)
    )
    if int(sim.turn) - last_fact_turn < world.rethink_stagnation_slots:
        return []

    return [
        (
            Action(
                "rethink",
                meta={
                    "evidence": evidence,
                    "risk": "neutral",
                    "stance_sign": 0,
                    "subtype": "rethink",
                },
            ),
            0.1 + subject.traits["curiosity"] * 0.4,
        )
    ]


def _share_candidates(
    subject: Subject,
    world: World,
    present: list[Subject],
) -> list[tuple[Action, float]]:
    if "share_knowledge" not in subject.verbs:
        return []

    result: list[tuple[Action, float]] = []
    social = subject.traits["social"]
    single_weight = 0.2 + social
    for target in _living_targets(subject, present):
        if world.action_graph_enabled:
            permission = _permission_weight(
                subject,
                target,
                "share_knowledge",
                world,
            )
        else:
            permission = (
                world.permission_restricted_weight
                if _is_hostile(subject, target, world)
                else 1.0
            )
        if permission <= 0.0:
            continue

        affinity = world.relations.stance(subject.id, target.id)
        for fact in sorted(subject.knowledge - target.knowledge):
            definition = world.facts[fact]
            minimum = float(
                definition.get("share_min_affinity", 0.0)
            )
            if affinity < minimum:
                continue
            secrecy = float(definition.get("secrecy", 0.0))
            result.append(
                (
                    Action(
                        "share_knowledge",
                        (target.id, fact),
                        {
                            "target": target.id,
                            "fact": fact,
                            "stance_sign": 1,
                        },
                    ),
                    single_weight * (1.0 - secrecy) * permission,
                )
            )

        for fact_id, belief in sorted(subject.beliefs.items()):
            target_belief = target.beliefs.get(fact_id)
            if (
                target_belief is not None
                and target_belief.value == belief.value
                and target_belief.confidence >= belief.confidence
            ):
                continue
            definition = world.facts[fact_id]
            minimum = float(
                definition.get("share_min_affinity", 0.0)
            )
            if affinity < minimum:
                continue
            secrecy = float(definition.get("secrecy", 0.0))
            result.append(
                (
                    Action(
                        "share_knowledge",
                        (target.id, fact_id),
                        {
                            "target": target.id,
                            "fact": fact_id,
                            "value": belief.value,
                            "confidence": belief.confidence,
                            "valued_fact": True,
                            "stance_sign": 1,
                        },
                    ),
                    single_weight * (1.0 - secrecy) * permission,
                )
            )

        result.append(
            (
                Action(
                    "share_knowledge",
                    (target.id, "雑談"),
                    {
                        "target": target.id,
                        "topic": "雑談",
                        "stance_sign": 1,
                    },
                ),
                single_weight * permission,
            )
        )
    return result


def _material_products(item: str, world: World) -> list[str]:
    return [
        product
        for product in sorted(world.recipes)
        if item in world.recipes[product]
    ]


def _give_candidates(
    subject: Subject,
    world: World,
    present: list[Subject],
) -> list[tuple[Action, float]]:
    if "give_item" not in subject.verbs:
        return []

    result: list[tuple[Action, float]] = []
    single_weight = 0.25 + subject.traits["social"]
    excluded = set(world.objectives) | set(world.recipes)
    excluded.update(
        item
        for item, definition in world.items.items()
        if definition.get("vehicle", False)
        or definition.get("keepsake", False)
    )

    for target in _living_targets(subject, present):
        if world.action_graph_enabled:
            permission = _permission_weight(
                subject,
                target,
                "give_item",
                world,
            )
        else:
            permission = (
                world.permission_restricted_weight
                if _is_hostile(subject, target, world)
                else 1.0
            )
        if permission <= 0.0:
            continue

        for item in sorted(subject.inventory):
            if subject.inventory[item] <= 0 or item in excluded:
                continue
            products = _material_products(item, world)
            multiplier = 1.0
            if products:
                target_is_maker = False
                for product in products:
                    requirement = (
                        world.items[product].get("requires") or {}
                    ).get("knowledge")
                    if (
                        requirement is None
                        or requirement in target.knowledge
                    ):
                        target_is_maker = True
                        break
                if not target_is_maker:
                    continue
                multiplier = 2.0
            result.append(
                (
                    Action(
                        "give_item",
                        (target.id, item),
                        {
                            "target": target.id,
                            "item": item,
                            "stance_sign": 1,
                        },
                    ),
                    (
                        single_weight
                        * multiplier
                        * permission
                    ),
                )
            )
    return result


def _persuade_candidates(
    subject: Subject,
    world: World,
    present: list[Subject],
) -> list[tuple[Action, float]]:
    if "persuade" not in subject.verbs:
        return []

    single_weight = 0.15 + subject.traits["social"] * 0.6
    result: list[tuple[Action, float]] = []
    for target in _living_targets(subject, present):
        permission = _permission_weight(
            subject,
            target,
            "persuade",
            world,
        )
        if permission <= 0.0:
            continue
        result.append(
            (
                Action(
                    "persuade",
                    (target.id,),
                    {
                        "target": target.id,
                        "stance_sign": 1,
                    },
                ),
                single_weight * permission,
            )
        )
    return _normalize_opened(
        result,
        subject,
        world,
        single_weight,
    )


def _pledge_candidates(
    subject: Subject,
    world: World,
    present: list[Subject],
) -> list[tuple[Action, float]]:
    if "pledge" not in subject.verbs:
        return []

    result: list[tuple[Action, float]] = []
    single_weight = (
        0.1 + subject.traits["stubbornness"] * 0.3
    )
    for target in _living_targets(subject, present):
        if world.is_pledged(subject.id, target.id):
            continue
        if (
            world.relations.stance(subject.id, target.id) < 0.4
            or world.relations.stance(target.id, subject.id) < 0.4
        ):
            continue

        permission = _permission_weight(
            subject,
            target,
            "pledge",
            world,
        )
        if permission <= 0.0:
            continue

        result.append(
            (
                Action(
                    "pledge",
                    (target.id,),
                    {
                        "target": target.id,
                        "stance_sign": 1,
                    },
                ),
                single_weight * permission,
            )
        )
    return result


def _negotiate_candidates(
    subject: Subject,
    world: World,
    present: list[Subject],
) -> list[tuple[Action, float]]:
    if (
        "negotiate" not in subject.verbs
        or not subject.objective_claimant
        or subject.goal.target is None
        or subject.has_item(subject.goal.target)
    ):
        return []

    holder_id = world.holder(subject.goal.target)
    if holder_id is None or holder_id == subject.id:
        return []
    holder = next(
        (
            target
            for target in _living_targets(subject, present)
            if target.id == holder_id
        ),
        None,
    )
    if holder is None:
        return []

    permission = _permission_weight(
        subject,
        holder,
        "negotiate",
        world,
    )
    if permission <= 0.0:
        return []

    return [
        (
            Action(
                "negotiate",
                (holder.id,),
                {
                    "target": holder.id,
                    "objective": subject.goal.target,
                    "stance_sign": 1,
                },
            ),
            (
                0.2
                + subject.traits["social"] * 0.5
            )
            * permission,
        )
    ]


def _concede_candidates(
    subject: Subject,
    world: World,
    present: list[Subject],
) -> list[tuple[Action, float]]:
    if "concede" not in subject.verbs:
        return []

    present_ids = {
        target.id
        for target in _living_targets(subject, present)
    }
    result: list[tuple[Action, float]] = []
    for (claimant_id, holder_id), offer in sorted(
        world.offers.items()
    ):
        if holder_id != subject.id or claimant_id not in present_ids:
            continue
        claimant = world.subjects[claimant_id]

        permission = _permission_weight(
            subject,
            claimant,
            "concede",
            world,
        )
        if permission <= 0.0:
            continue

        objective = str(offer.get("objective", ""))
        if (
            not objective
            or world.holder(objective) != subject.id
            or claimant.goal.target != objective
        ):
            continue

        assets = offer.get("assets", {}) or {}
        attractive = [
            item
            for item in sorted(assets)
            if int(assets[item]) > 0
            and claimant.inventory.get(item, 0) > 0
            and not subject.has_item(item)
            and bool(world.items[item].get("modifier"))
        ]
        stance = world.relations.stance(
            subject.id,
            claimant.id,
        )
        if stance < world.negotiate_threshold and not attractive:
            continue

        mode = "trade" if attractive else "goodwill"
        result.append(
            (
                Action(
                    "concede",
                    (claimant.id,),
                    {
                        "target": claimant.id,
                        "objective": objective,
                        "mode": mode,
                        "trade_assets": attractive,
                        "stance_sign": 1,
                    },
                ),
                (
                    0.1
                    + subject.traits["social"] * 0.4
                    + max(0.0, stance) * 0.6
                )
                * permission,
            )
        )
    return result


def _craft_candidates(
    subject: Subject,
    world: World,
) -> list[tuple[Action, float]]:
    if "craft" not in subject.verbs:
        return []
    return [
        (
            Action("craft", (item,), {"item": item}),
            1.0 + subject.traits["diligence"],
        )
        for item in sorted(world.recipes)
        if _can_craft(subject, item, world)
    ]


def _fight_candidates(
    subject: Subject,
    world: World,
    present: list[Subject],
) -> list[tuple[Action, float]]:
    if "fight" not in subject.verbs:
        return []

    actor_strength = strength(subject, world, present)
    single_weight = (
        0.1 + subject.traits["stubbornness"] * 0.4
    ) * (2.0 * subject.traits["temper"])
    targets = (
        _living_targets(subject, present)
        if world.action_graph_enabled
        else _hostiles(subject, world, present)
    )

    result: list[tuple[Action, float]] = []
    for target in sorted(targets, key=lambda value: value.id):
        permission = (
            _permission_weight(
                subject,
                target,
                "fight",
                world,
            )
            if world.action_graph_enabled
            else 1.0
        )
        if permission <= 0.0:
            continue

        perceived = believed_strength(
            subject,
            target,
            world,
            present,
        )
        advantage = 1.0
        if world.action_graph_enabled and subject.policy is None:
            strength_margin = (
                actor_strength - perceived
            ) / max(
                1.0,
                abs(actor_strength),
            )
            advantage = min(
                1.5,
                max(0.5, 1.0 + strength_margin),
            )

        meta = {
            "target": target.id,
            "outmatched": perceived >= actor_strength,
            "under_threat": perceived >= actor_strength,
        }
        meta.update(_betrayal_meta(subject, target, world))
        result.append(
            (
                Action("fight", (target.id,), meta),
                single_weight * advantage * permission,
            )
        )
    return _normalize_opened(
        result,
        subject,
        world,
        single_weight,
    )


def _train_candidates(
    subject: Subject,
    hostiles: list[Subject],
) -> list[tuple[Action, float]]:
    if "train" not in subject.verbs or hostiles:
        return []
    return [
        (
            Action("train"),
            0.15 + subject.traits["diligence"] * 0.5,
        )
    ]


def _rescue_candidates(
    subject: Subject,
    world: World,
    present: list[Subject],
) -> list[tuple[Action, float]]:
    if "rescue" not in subject.verbs:
        return []

    result: list[tuple[Action, float]] = []
    for target in sorted(present, key=lambda value: value.id):
        if target.id == subject.id or target.vitality != "downed":
            continue
        affinity = world.relations.stance(subject.id, target.id)
        if affinity < 0.3:
            continue

        permission = _permission_weight(
            subject,
            target,
            "rescue",
            world,
        )
        if permission <= 0.0:
            continue

        result.append(
            (
                Action(
                    "rescue",
                    (target.id,),
                    {"target": target.id},
                ),
                (
                    0.3
                    + subject.traits["social"]
                    + affinity
                )
                * permission,
            )
        )
    return result


def _withdraw_candidates(
    subject: Subject,
    world: World,
    present: list[Subject],
    hostiles: list[Subject],
) -> list[tuple[Action, float]]:
    if "withdraw" not in subject.verbs:
        return []
    weight = 0.08 + max(0.0, subject.stress - 4.0) * 0.35
    if hostiles:
        weight += max(
            0.0,
            0.5 - subject.traits["temper"],
        ) * 0.6
    under_threat = _under_threat(subject, world, present)
    return [
        (
            Action(
                "withdraw",
                meta={"under_threat": under_threat},
            ),
            weight,
        )
    ]


def _guard_candidates(
    subject: Subject,
    world: World,
) -> list[tuple[Action, float]]:
    if "guard" not in subject.verbs:
        return []
    if not any(subject.has_item(item) for item in sorted(world.objectives)):
        return []
    return [
        (
            Action("guard"),
            0.25 + subject.traits["stubbornness"],
        )
    ]


def _plant_candidates(
    subject: Subject,
    world: World,
    sim: Any,
) -> list[tuple[Action, float]]:
    if "plant" not in subject.verbs:
        return []

    weight = 0.15 + subject.traits["curiosity"] * 0.3
    return [
        (
            Action(
                "plant",
                (str(effect["id"]),),
                {
                    "effect_id": str(effect["id"]),
                    "target": subject.id,
                },
            ),
            weight,
        )
        for effect in dedicated_plant_options(
            world,
            subject,
            turn=int(sim.turn),
            day=int(sim.day),
        )
    ]


def _payoff_candidates(
    subject: Subject,
    world: World,
    sim: Any,
) -> list[tuple[Action, float]]:
    if "payoff" not in subject.verbs:
        return []

    weight = 0.3 + subject.traits["stubbornness"] * 0.3
    return [
        (
            Action(
                "payoff",
                (str(pending["library_id"]),),
                {
                    "effect_id": str(pending["id"]),
                    "library_id": str(pending["library_id"]),
                    "target": str(pending["target"]),
                },
            ),
            weight,
        )
        for pending in ready_chosen_effects(
            world,
            subject,
            turn=int(sim.turn),
            day=int(sim.day),
        )
    ]


def _disguise_candidates(
    subject: Subject,
    world: World,
    sim: Any,
) -> list[tuple[Action, float]]:
    if "disguise" not in subject.verbs:
        return []

    weight = (
        0.1
        + (1.0 - subject.traits["social"]) * 0.3
    )
    return [
        (
            Action(
                "disguise",
                (str(disguise["as"]),),
                {
                    "disguise_id": str(disguise["id"]),
                    "displayed": str(disguise["as"]),
                },
            ),
            weight,
        )
        for disguise in disguise_options(
            world,
            subject,
            turn=int(sim.turn),
            day=int(sim.day),
        )
    ]


def _grand_gesture_candidates(
    subject: Subject,
    world: World,
    present: list[Subject],
) -> list[tuple[Action, float]]:
    if "grand_gesture" not in subject.verbs:
        return []

    item = grand_gesture_asset(world, subject)
    if item is None:
        return []

    single_weight = 0.05 + subject.traits["social"] * 0.2
    result: list[tuple[Action, float]] = []
    for target in _living_targets(subject, present):
        fact = grand_gesture_fact(
            world,
            subject,
            target,
        )
        if fact is None:
            continue

        permission = _permission_weight(
            subject,
            target,
            "grand_gesture",
            world,
        )
        if permission <= 0.0:
            continue

        result.append(
            (
                Action(
                    "grand_gesture",
                    (target.id,),
                    {
                        "target": target.id,
                        "fact": fact,
                        "item": item,
                        "risk": "risky",
                        "stance_sign": 1,
                    },
                ),
                single_weight * permission,
            )
        )

    return _normalize_opened(
        result,
        subject,
        world,
        single_weight,
    )


def _trial_candidates(
    subject: Subject,
    world: World,
) -> list[tuple[Action, float]]:
    if "trial" not in subject.verbs:
        return []

    single_weight = 0.2 + subject.traits["diligence"] * 0.3
    result: list[tuple[Action, float]] = []
    for trial in trial_options(world, subject):
        giver = world.subjects[str(trial["giver"])]
        permission = _permission_weight(
            subject,
            giver,
            "trial",
            world,
        )
        if permission <= 0.0:
            continue
        result.append(
            (
                Action(
                    "trial",
                    (giver.id,),
                    {
                        "target": giver.id,
                        "trial_id": str(trial["id"]),
                        "stance_sign": 1,
                    },
                ),
                single_weight * permission,
            )
        )
    return result


def _donate_candidates(
    subject: Subject,
    world: World,
) -> list[tuple[Action, float]]:
    if (
        "donate" not in subject.verbs
        or subject.goal.target is None
        or not subject.has_item(subject.goal.target)
        or "還元" in subject.phase
    ):
        return []

    return [
        (
            Action(
                "donate",
                (subject.goal.target,),
                {
                    "item": subject.goal.target,
                    "phase": "還元",
                    "stance_sign": 1,
                },
            ),
            0.1 + subject.traits["social"] * 0.3,
        )
    ]


def candidates(
    subject: Subject,
    world: World,
    sim: Any,
) -> list[tuple[Action, float]]:
    """Return deterministically ordered weighted candidates without RNG."""

    if subject.vitality == "dead":
        return []
    if subject.vitality == "downed":
        return [(Action("rest"), 1.0)]

    present = _present(subject, world)
    hostile_targets = _hostiles(subject, world, present)
    allowed_verbs = available_verbs(
        world,
        subject,
        turn=int(sim.turn),
        day=int(sim.day),
    )
    weighted: list[tuple[Action, float]] = []
    weighted.extend(_movement_candidates(subject, world, sim, present))

    if "rest" in subject.verbs and subject.stamina_max > 0.0:
        ratio = subject.stamina / subject.stamina_max
        rest_weight = max(
            0.05,
            world.movement["action_weight"]
            * 2.0
            * (1.0 - ratio) ** 2,
        )
        if subject.exhausted:
            rest_weight *= 2.0
        weighted.append(
            (
                Action(
                    "rest",
                    meta={
                        "under_threat": _under_threat(
                            subject,
                            world,
                            present,
                        )
                    },
                ),
                rest_weight,
            )
        )

    weighted.extend(_investigate_candidates(subject, world))
    weighted.extend(_observe_candidates(subject, world, present))
    weighted.extend(_neutralize_candidates(subject, world, present))
    weighted.extend(_sabotage_candidates(subject, world, present))
    weighted.extend(_sacrifice_candidates(subject, world))
    weighted.extend(_mislead_candidates(subject, world, present))
    weighted.extend(_rethink_candidates(subject, world, sim))
    weighted.extend(_confront_candidates(subject, world, present))
    weighted.extend(_share_candidates(subject, world, present))
    weighted.extend(_give_candidates(subject, world, present))
    weighted.extend(_persuade_candidates(subject, world, present))
    weighted.extend(_pledge_candidates(subject, world, present))
    weighted.extend(_negotiate_candidates(subject, world, present))
    weighted.extend(_concede_candidates(subject, world, present))
    weighted.extend(_craft_candidates(subject, world))
    weighted.extend(_fight_candidates(subject, world, present))
    weighted.extend(_train_candidates(subject, hostile_targets))
    weighted.extend(_rescue_candidates(subject, world, present))
    weighted.extend(
        _withdraw_candidates(
            subject,
            world,
            present,
            hostile_targets,
        )
    )
    weighted.extend(_guard_candidates(subject, world))
    weighted.extend(_plant_candidates(subject, world, sim))
    weighted.extend(_payoff_candidates(subject, world, sim))
    weighted.extend(_disguise_candidates(subject, world, sim))
    weighted.extend(
        _grand_gesture_candidates(
            subject,
            world,
            present,
        )
    )
    weighted.extend(_trial_candidates(subject, world))
    weighted.extend(_donate_candidates(subject, world))

    return sorted(
        (
            (action, float(weight))
            for action, weight in weighted
            if weight > 0.0
            and action.verb in allowed_verbs
            and world.genre_allows(action.verb)
        ),
        key=_action_key,
    )
