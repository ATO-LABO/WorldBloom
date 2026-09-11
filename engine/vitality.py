"""Down, revive, rescue, death, and grief transitions for plan §3.7."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from engine.subject import Subject
    from engine.world import World


def _marker(
    verb: str,
    subject: str,
    **details: Any,
) -> dict[str, Any]:
    return {
        "verb": verb,
        "subject": subject,
        "details": details,
    }


def down(
    subject: Subject,
    world: World,
    turn: int,
) -> list[dict[str, Any]]:
    if subject.vitality not in {"alive", "revived"}:
        return []
    subject.vitality = "downed"
    subject.downed_since = turn
    subject.change_stress(1.0)
    return [
        _marker(
            "downed",
            subject.id,
            downed_since=turn,
        )
    ]


def revive(
    subject: Subject,
    world: World,
    by: str | None,
) -> list[dict[str, Any]]:
    if subject.vitality != "downed":
        return []
    subject.vitality = "revived"
    subject.downed_since = None
    subject.base = round(
        max(1.0, subject.base - world.vitality["revive_base_penalty"]),
        2,
    )
    return [
        _marker(
            "revived",
            subject.id,
            by=by,
            base=subject.base,
        )
    ]


def kill(
    subject: Subject,
    world: World,
    killer: Subject,
) -> list[dict[str, Any]]:
    if subject.vitality == "dead":
        return []

    subject.vitality = "dead"
    subject.downed_since = None
    transferred: dict[str, int] = {}
    for item in sorted(tuple(subject.inventory)):
        definition = world.items.get(item, {})
        count = subject.inventory.get(item, 0)
        if count <= 0 or not definition.get("lootable", False):
            continue
        subject.remove_item(item, count)
        killer.add_item(item, count)
        transferred[item] = count

    events = [
        _marker(
            "dead",
            subject.id,
            killer=killer.id,
            transferred=transferred,
        )
    ]
    threshold = world.companionship["threshold"]
    for survivor in world.subjects.values():
        if survivor.id in {subject.id, killer.id}:
            continue
        if survivor.vitality == "dead":
            continue
        if world.relations.stance(survivor.id, subject.id) < threshold:
            continue
        survivor.change_stress(world.grief["stress"])
        relation_delta = world.relations.change(
            survivor.id,
            killer.id,
            affinity=world.grief["affinity_to_killer"],
        )
        events.append(
            _marker(
                "grief",
                survivor.id,
                dead=subject.id,
                killer=killer.id,
                relation_delta=relation_delta,
            )
        )
    return events


def tick(
    subject: Subject,
    world: World,
    turn: int,
    present: list[Subject],
) -> list[dict[str, Any]]:
    if subject.vitality != "downed" or subject.downed_since is None:
        return []

    threshold = world.companionship["threshold"]
    allies = sum(
        1
        for peer in present
        if peer.id != subject.id
        and peer.vitality in {"alive", "revived"}
        and world.relations.stance(peer.id, subject.id) >= threshold
    )
    delay = max(
        0,
        world.vitality["revive_after"]
        - world.vitality["ally_speedup"] * allies,
    )
    if turn - subject.downed_since < delay:
        return []
    return revive(subject, world, by=None)
