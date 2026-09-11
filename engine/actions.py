"""Action values and deterministic candidate generation for plan §3.6."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from engine.contest import believed_strength, strength

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
    return (
        world.relations.stance(actor.id, target.id) < -0.2
        or target.id in actor.goal.obstacles
    )


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
        companion_destination = sorted(companions, key=lambda peer: peer.id)[0].zone

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
    for target in sorted(present, key=lambda value: value.id):
        if target.id == subject.id or target.vitality == "dead":
            continue
        belief = subject.beliefs_about.get(target.id)
        known = belief.known_modifiers if belief is not None else set()
        identity_seen = belief.identity_seen if belief is not None else False
        hidden = any(
            modifier.active
            and not modifier.visible
            and modifier.source not in known
            for modifier in target.all_modifiers(world, present)
        )
        if not hidden and identity_seen:
            continue
        result.append(
            (
                Action(
                    "observe",
                    (target.id,),
                    {"target": target.id},
                ),
                0.3 + subject.traits["curiosity"] * 0.6,
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
    weight = (
        0.2
        + subject.traits["curiosity"] * 0.4
        + subject.traits["stubbornness"] * 0.3
    )
    for target in sorted(present, key=lambda value: value.id):
        if target.id == subject.id or target.vitality == "dead":
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
            result.append(
                (
                    Action(
                        "neutralize",
                        (target.id, source),
                        {
                            "target": target.id,
                            "source": source,
                            "stance_sign": -1,
                            "risk": "risky",
                        },
                    ),
                    weight,
                )
            )
    return result


def _share_candidates(
    subject: Subject,
    world: World,
    present: list[Subject],
) -> list[tuple[Action, float]]:
    if "share_knowledge" not in subject.verbs:
        return []
    result: list[tuple[Action, float]] = []
    social = subject.traits["social"]
    for target in sorted(present, key=lambda value: value.id):
        if target.id == subject.id or target.vitality == "dead":
            continue
        affinity = world.relations.stance(subject.id, target.id)
        permission = (
            world.permission_restricted_weight
            if _is_hostile(subject, target, world)
            else 1.0
        )
        for fact in sorted(subject.knowledge - target.knowledge):
            definition = world.facts[fact]
            minimum = float(definition.get("share_min_affinity", 0.0))
            if affinity < minimum:
                continue
            secrecy = float(definition.get("secrecy", 0.0))
            result.append(
                (
                    Action(
                        "share_knowledge",
                        (target.id, fact),
                        {"target": target.id, "fact": fact},
                    ),
                    (0.2 + social) * (1.0 - secrecy) * permission,
                )
            )
        result.append(
            (
                Action(
                    "share_knowledge",
                    (target.id, "雑談"),
                    {"target": target.id, "topic": "雑談"},
                ),
                (0.2 + social) * permission,
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
    excluded = set(world.objectives) | set(world.recipes)
    excluded.update(
        item
        for item, definition in world.items.items()
        if definition.get("vehicle", False)
    )

    for target in sorted(present, key=lambda value: value.id):
        if target.id == subject.id or target.vitality == "dead":
            continue
        permission = (
            world.permission_restricted_weight
            if _is_hostile(subject, target, world)
            else 1.0
        )
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
                    if requirement is None or requirement in target.knowledge:
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
                        {"target": target.id, "item": item},
                    ),
                    (
                        (0.25 + subject.traits["social"])
                        * multiplier
                        * permission
                    ),
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
    weight = (
        0.1 + subject.traits["stubbornness"] * 0.4
    ) * (2.0 * subject.traits["temper"])
    return [
        (
            Action(
                "fight",
                (target.id,),
                {
                    "target": target.id,
                    "outmatched": believed_strength(
                        subject,
                        target,
                        world,
                        present,
                    )
                    >= actor_strength,
                },
            ),
            weight,
        )
        for target in sorted(present, key=lambda value: value.id)
        if target.id != subject.id
        and target.vitality in {"alive", "revived"}
        and _is_hostile(subject, target, world)
    ]


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
        result.append(
            (
                Action("rescue", (target.id,), {"target": target.id}),
                0.3 + subject.traits["social"] + affinity,
            )
        )
    return result


def _withdraw_candidates(
    subject: Subject,
    hostiles: list[Subject],
) -> list[tuple[Action, float]]:
    if "withdraw" not in subject.verbs:
        return []
    weight = 0.08 + max(0.0, subject.stress - 4.0) * 0.35
    if hostiles:
        weight += max(0.0, 0.5 - subject.traits["temper"]) * 0.6
    return [(Action("withdraw"), weight)]


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
    weighted: list[tuple[Action, float]] = []
    weighted.extend(_movement_candidates(subject, world, sim, present))

    if "rest" in subject.verbs and subject.stamina_max > 0.0:
        ratio = subject.stamina / subject.stamina_max
        rest_weight = max(
            0.05,
            world.movement["action_weight"] * 2.0 * (1.0 - ratio) ** 2,
        )
        if subject.exhausted:
            rest_weight *= 2.0
        weighted.append((Action("rest"), rest_weight))

    weighted.extend(_investigate_candidates(subject, world))
    weighted.extend(_observe_candidates(subject, world, present))
    weighted.extend(_neutralize_candidates(subject, world, present))
    weighted.extend(_share_candidates(subject, world, present))
    weighted.extend(_give_candidates(subject, world, present))
    weighted.extend(_craft_candidates(subject, world))
    weighted.extend(_fight_candidates(subject, world, present))
    weighted.extend(_train_candidates(subject, hostile_targets))
    weighted.extend(_rescue_candidates(subject, world, present))
    weighted.extend(_withdraw_candidates(subject, hostile_targets))
    weighted.extend(_guard_candidates(subject, world))

    return sorted(
        (
            (action, float(weight))
            for action, weight in weighted
            if weight > 0.0
        ),
        key=_action_key,
    )
