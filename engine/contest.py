"""Logistic strength and contest resolution for implementation plan §3.5."""

from __future__ import annotations

import math
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.subject import Subject
    from engine.world import World


def strength(
    subject: Subject,
    world: World,
    present: list[Subject],
) -> float:
    modifier_total = sum(
        modifier.value
        for modifier in subject.all_modifiers(world, present)
        if modifier.active
    )
    traits = subject.traits
    temperament = (
        0.65 * traits["stubbornness"]
        + 0.20 * traits["social"]
        + 0.15 * traits["curiosity"]
    )
    return round(
        subject.base
        + modifier_total
        + world.contest["epsilon"] * temperament,
        6,
    )


def believed_strength(
    observer: Subject,
    target: Subject,
    world: World,
    present: list[Subject],
) -> float:
    belief = observer.beliefs_about.get(target.id)
    base_estimate = (
        belief.base_estimate
        if belief is not None
        else world.default_strength_prior
    )
    known_modifiers = (
        belief.known_modifiers if belief is not None else set()
    )
    modifier_total = sum(
        modifier.value
        for modifier in target.all_modifiers(world, present)
        if modifier.active
        and (modifier.visible or modifier.source in known_modifiers)
    )
    return round(base_estimate + modifier_total, 6)


def resolve(
    actor: Subject,
    rival: Subject,
    world: World,
    present: list[Subject],
    rng: random.Random,
) -> tuple[Subject, Subject, float, float]:
    difference = strength(actor, world, present) - strength(
        rival, world, present
    )
    scaled = max(-700.0, min(700.0, difference / world.contest["tau"]))
    probability = 1.0 / (1.0 + math.exp(-scaled))
    roll = rng.random()
    if roll < probability:
        return actor, rival, probability, roll
    return rival, actor, probability, roll
