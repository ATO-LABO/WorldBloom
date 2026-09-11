"""Execution of the 13 Phase 0 and D1b verbs from implementation plan §3.6."""

from __future__ import annotations

import random
from typing import Any, TYPE_CHECKING

from engine import vitality
from engine.actions import Action
from engine.contest import resolve, strength
from engine.subject import BeliefAbout

if TYPE_CHECKING:
    from engine.subject import Subject
    from engine.world import World


class VerbEngine:
    """Apply one selected action and return marker events."""

    def __init__(self, world: World, rng: random.Random) -> None:
        self.world = world
        self.rng = rng
        self._ally_gained: set[tuple[str, str]] = set()

    def execute(
        self,
        actor: Subject,
        action: Action,
        *,
        turn: int,
        day: int,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        handlers = {
            "move": self._move,
            "rest": self._rest,
            "investigate": self._investigate,
            "observe": self._observe,
            "neutralize": self._neutralize,
            "share_knowledge": self._share_knowledge,
            "give_item": self._give_item,
            "craft": self._craft,
            "fight": self._fight,
            "train": self._train,
            "rescue": self._rescue,
            "withdraw": self._withdraw,
            "guard": self._guard,
        }
        handler = handlers.get(action.verb)
        if handler is None:
            return "invalid", {"reason": "unknown_verb"}, []
        return handler(actor, action, turn=turn, day=day)

    def _present(self, actor: Subject) -> list[Subject]:
        return self.world.present_subjects(actor.zone)

    def _target(
        self,
        actor: Subject,
        action: Action,
        *,
        allow_downed: bool = False,
    ) -> Subject | None:
        if not action.args:
            return None
        target = self.world.subjects.get(str(action.args[0]))
        if target is None or target.id == actor.id or target.zone != actor.zone:
            return None
        allowed = {"alive", "revived", "downed"} if allow_downed else {
            "alive",
            "revived",
        }
        return target if target.vitality in allowed else None

    def _update_exhausted(self, actor: Subject) -> None:
        threshold = (
            actor.stamina_max * self.world.stamina["exhausted_ratio"]
        )
        if actor.stamina < threshold:
            actor.exhausted = True

    def _move(
        self,
        actor: Subject,
        action: Action,
        *,
        turn: int,
        day: int,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        destination = str(action.args[0])
        path = self.world.reachable_paths(actor).get(destination)
        if path is None:
            return "invalid", {"reason": "unreachable", "dest": destination}, []

        origin = actor.zone
        actor.zone = destination
        actor.change_stamina(-path.cost)
        self._update_exhausted(actor)

        present = self._present(actor)
        crossed = self.world.crossed_thresholds(
            actor,
            present,
            turn=turn,
            day=day,
        )
        markers: list[dict[str, Any]] = []
        for threshold_id in crossed:
            actor.phase.add(threshold_id)
            markers.append(
                {
                    "verb": "threshold_crossed",
                    "subject": actor.id,
                    "details": {
                        "threshold": threshold_id,
                        "zone": destination,
                    },
                }
            )
        return (
            "moved",
            {
                "origin": origin,
                "dest": destination,
                "cost": path.cost,
                "route": list(path.route),
            },
            markers,
        )

    def _rest(
        self,
        actor: Subject,
        action: Action,
        *,
        turn: int,
        day: int,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        recovered = actor.change_stamina(actor.stamina_recover)
        return "rested", {"stamina_delta": recovered}, []

    def _gather_items(
        self,
        actor: Subject,
    ) -> list[dict[str, Any]]:
        gathered: list[dict[str, Any]] = []
        for item, definition in sorted(self.world.items.items()):
            for index, source in enumerate(definition.get("sources", []) or []):
                if source.get("type") != "investigate":
                    continue
                if source.get("zone") != actor.zone:
                    continue
                maximum = int(source.get("max", 1))
                gathered_key = f"item:{item}:{actor.zone}:{index}"
                already = actor.gathered.get(gathered_key, 0)
                if already >= maximum:
                    continue
                needed = max(1, int(source.get("count", 1)))
                progress_key = f"progress:{gathered_key}"
                progress = actor.gather_progress.get(progress_key, 0) + 1
                actor.gather_progress[progress_key] = progress
                if progress < needed:
                    continue
                actor.gather_progress[progress_key] = 0
                actor.gathered[gathered_key] = already + 1
                actor.add_item(item, 1)
                gathered.append(
                    {
                        "item": item,
                        "count": 1,
                        "source": actor.zone,
                    }
                )
        return gathered

    def _learn_from_investigation(
        self,
        actor: Subject,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        learned: list[str] = []
        markers: list[dict[str, Any]] = []
        present_ids = {
            subject.id for subject in self.world.present_subjects(actor.zone)
        }
        for fact, definition in sorted(self.world.facts.items()):
            if fact in actor.knowledge:
                continue
            for index, source in enumerate(definition.get("sources", []) or []):
                if source.get("type") != "investigate":
                    continue
                zone_matches = (
                    source.get("zone") is not None
                    and source.get("zone") == actor.zone
                )
                agent_matches = (
                    source.get("agent") is not None
                    and source.get("agent") in present_ids
                )
                if not zone_matches and not agent_matches:
                    continue
                needed = max(1, int(source.get("count", 1)))
                progress_key = f"fact:{fact}:{index}"
                progress = actor.gather_progress.get(progress_key, 0) + 1
                actor.gather_progress[progress_key] = progress
                if progress < needed:
                    continue
                actor.knowledge.add(fact)
                actor.gather_progress[progress_key] = 0
                learned.append(fact)
                markers.append(
                    {
                        "verb": "learn_fact",
                        "subject": actor.id,
                        "details": {
                            "fact": fact,
                            "source": source.get("zone")
                            or source.get("agent"),
                        },
                    }
                )
                break
        return learned, markers

    def _investigate(
        self,
        actor: Subject,
        action: Action,
        *,
        turn: int,
        day: int,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        zone = str(action.args[0])
        if zone != actor.zone:
            return "invalid", {"reason": "not_present", "zone": zone}, []
        gathered = self._gather_items(actor)
        learned, markers = self._learn_from_investigation(actor)
        return (
            "investigated",
            {
                "zone": zone,
                "gathered": gathered,
                "learned": learned,
            },
            markers,
        )

    def _observe(
        self,
        actor: Subject,
        action: Action,
        *,
        turn: int,
        day: int,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        target = self._target(actor, action)
        if target is None:
            return "invalid", {"reason": "target_not_present"}, []

        present = self._present(actor)
        belief = actor.beliefs_about.setdefault(
            target.id,
            BeliefAbout(base_estimate=self.world.default_strength_prior),
        )
        belief.observe_progress = round(
            belief.observe_progress
            + 0.5
            + actor.traits["curiosity"] * 0.5,
            4,
        )
        revealed: list[str] = []
        if belief.observe_progress >= 1.0:
            hidden_sources = sorted(
                {
                    modifier.source
                    for modifier in target.all_modifiers(self.world, present)
                    if modifier.active
                    and not modifier.visible
                    and modifier.source not in belief.known_modifiers
                }
            )
            if hidden_sources:
                source = hidden_sources[0]
                belief.known_modifiers.add(source)
                revealed.append(source)
            belief.base_estimate = target.base
            belief.identity_seen = True
            belief.observe_progress = 0.0

        return (
            "observed",
            {
                "target": target.id,
                "revealed": revealed,
                "identity_seen": belief.identity_seen,
            },
            [],
        )

    def _neutralize(
        self,
        actor: Subject,
        action: Action,
        *,
        turn: int,
        day: int,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        target = self._target(actor, action, allow_downed=True)
        source = str(action.args[1])
        if target is None:
            return "invalid", {"reason": "target_not_present"}, []

        belief = actor.beliefs_about.get(target.id)
        if belief is None or source not in belief.known_modifiers:
            return (
                "invalid",
                {
                    "reason": "modifier_unknown",
                    "target": target.id,
                    "source": source,
                },
                [],
            )

        present = self._present(actor)
        matching = sorted(
            (
                modifier
                for modifier in target.all_modifiers(self.world, present)
                if modifier.active and modifier.source == source
            ),
            key=lambda modifier: (modifier.id, modifier.kind),
        )
        if not matching:
            return (
                "invalid",
                {
                    "reason": "modifier_inactive",
                    "target": target.id,
                    "source": source,
                },
                [],
            )

        for modifier in matching:
            modifier.active = False
            if not any(
                existing is modifier for existing in target.modifiers
            ):
                target.modifiers.append(modifier)
        target.modifiers.sort(key=lambda modifier: modifier.id)

        transferred = False
        if any(modifier.kind == "item" for modifier in matching):
            item = self.world.items.get(source)
            if (
                item is not None
                and item.get("lootable", False)
                and target.remove_item(source, 1)
            ):
                actor.add_item(source, 1)
                transferred = True

        self.world.relations.change(
            target.id,
            actor.id,
            affinity=-0.2,
            awareness=0.3,
        )
        return (
            "neutralized",
            {
                "neutralized": {
                    "target": target.id,
                    "source": source,
                    "transferred": transferred,
                }
            },
            [],
        )

    def _share_knowledge(
        self,
        actor: Subject,
        action: Action,
        *,
        turn: int,
        day: int,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        target = self._target(actor, action)
        if target is None:
            return "invalid", {"reason": "target_not_present"}, []
        topic = str(action.args[1])
        if topic != "雑談":
            if topic not in actor.knowledge or topic in target.knowledge:
                return "invalid", {"reason": "fact_unavailable", "fact": topic}, []
            definition = self.world.facts.get(topic)
            if definition is None:
                return "invalid", {"reason": "unknown_fact", "fact": topic}, []
            minimum = float(definition.get("share_min_affinity", 0.0))
            if self.world.relations.stance(actor.id, target.id) < minimum:
                return "invalid", {"reason": "affinity_too_low", "fact": topic}, []

        self.world.relations.change(
            actor.id,
            target.id,
            affinity=0.12,
            awareness=0.2,
        )
        self.world.relations.change(
            target.id,
            actor.id,
            affinity=0.06,
            awareness=0.2,
        )
        markers: list[dict[str, Any]] = []
        if topic != "雑談":
            target.knowledge.add(topic)
            markers.append(
                {
                    "verb": "learn_fact",
                    "subject": target.id,
                    "details": {
                        "fact": topic,
                        "from": actor.id,
                    },
                }
            )
        return (
            "shared",
            {"target": target.id, "topic": topic},
            markers,
        )

    def _give_item(
        self,
        actor: Subject,
        action: Action,
        *,
        turn: int,
        day: int,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        target = self._target(actor, action)
        item = str(action.args[1])
        if target is None:
            return "invalid", {"reason": "target_not_present"}, []
        if not actor.remove_item(item, 1):
            return "invalid", {"reason": "item_unavailable", "item": item}, []

        before = self.world.relations.stance(target.id, actor.id)
        target.add_item(item, 1)
        give = self.world.items[item].get("give", {}) or {}
        receiver_delta = float(give.get("receiver_affinity", 0.2))
        giver_delta = float(give.get("giver_affinity", 0.05))
        self.world.relations.change(
            target.id,
            actor.id,
            affinity=receiver_delta,
            awareness=0.3,
        )
        self.world.relations.change(
            actor.id,
            target.id,
            affinity=giver_delta,
            awareness=0.1,
        )
        after = self.world.relations.stance(target.id, actor.id)

        markers: list[dict[str, Any]] = []
        pair = (target.id, actor.id)
        threshold = self.world.companionship["threshold"]
        if before < threshold <= after and pair not in self._ally_gained:
            self._ally_gained.add(pair)
            markers.append(
                {
                    "verb": "ally_gained",
                    "subject": target.id,
                    "details": {
                        "ally": actor.id,
                        "item": item,
                    },
                }
            )
        return (
            "given",
            {"target": target.id, "item": item, "count": 1},
            markers,
        )

    def _craft(
        self,
        actor: Subject,
        action: Action,
        *,
        turn: int,
        day: int,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        item = str(action.args[0])
        if item not in self.world.recipes:
            return "invalid", {"reason": "not_a_recipe", "item": item}, []
        definition = self.world.items[item]
        required_fact = (definition.get("requires") or {}).get("knowledge")
        if required_fact is not None and required_fact not in actor.knowledge:
            return "invalid", {"reason": "missing_knowledge", "fact": required_fact}, []
        craft_zone = definition.get("craft_zone")
        if craft_zone is not None and actor.zone != craft_zone:
            return "invalid", {"reason": "wrong_zone", "zone": craft_zone}, []

        recipe = self.world.recipes[item]
        if not all(
            actor.inventory.get(material, 0) >= int(required)
            for material, required in recipe.items()
        ):
            return "invalid", {"reason": "missing_materials", "item": item}, []

        consumed: dict[str, int] = {}
        for material, required in sorted(recipe.items()):
            count = int(required)
            actor.remove_item(material, count)
            consumed[material] = count
        actor.add_item(item, 1)
        return (
            "crafted",
            {"item": item, "count": 1, "consumed": consumed},
            [],
        )

    def _fight(
        self,
        actor: Subject,
        action: Action,
        *,
        turn: int,
        day: int,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        target = self._target(actor, action)
        if target is None:
            return "invalid", {"reason": "target_not_present"}, []

        present = self._present(actor)
        winner, loser, probability, roll = resolve(
            actor,
            target,
            self.world,
            present,
            self.rng,
        )
        lethal_modifiers = [
            modifier
            for modifier in winner.all_modifiers(self.world, present)
            if modifier.active and modifier.lethal
        ]

        transferred: dict[str, int] = {}
        for item in sorted(tuple(loser.inventory)):
            definition = self.world.items[item]
            count = loser.inventory.get(item, 0)
            if (
                count <= 0
                or not definition.get("lootable", False)
                or definition.get("vehicle", False)
            ):
                continue
            loser.remove_item(item, count)
            winner.add_item(item, count)
            transferred[item] = count

        self.world.relations.change(
            winner.id,
            loser.id,
            awareness=0.2,
        )
        self.world.relations.change(
            loser.id,
            winner.id,
            affinity=-0.3,
        )
        loser.change_stress(1.2)
        markers = vitality.down(loser, self.world, turn)

        lethal_roll: float | None = None
        lethal_chance: float | None = None
        if (
            lethal_modifiers
            and loser.id not in self.world.vitality["lethal_exempt"]
        ):
            lethal_chance = max(
                modifier.lethal_chance for modifier in lethal_modifiers
            )
            lethal_roll = self.rng.random()
            if lethal_roll < lethal_chance:
                markers.extend(vitality.kill(loser, self.world, winner))

        return (
            "won" if winner.id == actor.id else "lost",
            {
                "target": target.id,
                "winner": winner.id,
                "loser": loser.id,
                "p_actor": round(probability, 8),
                "roll": round(roll, 8),
                "loot": transferred,
                "lethal_chance": lethal_chance,
                "lethal_roll": (
                    round(lethal_roll, 8)
                    if lethal_roll is not None
                    else None
                ),
                "actor_strength": strength(actor, self.world, present),
                "target_strength": strength(target, self.world, present),
            },
            markers,
        )

    def _train(
        self,
        actor: Subject,
        action: Action,
        *,
        turn: int,
        day: int,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        before = actor.base
        actor.base = round(actor.base + 4.0 * (1.0 - actor.base / 100.0), 2)
        actor.change_stamina(-1.0)
        self._update_exhausted(actor)
        return (
            "trained",
            {
                "base_before": before,
                "base_after": actor.base,
                "stamina_cost": 1.0,
            },
            [],
        )

    def _rescue(
        self,
        actor: Subject,
        action: Action,
        *,
        turn: int,
        day: int,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        target = self._target(actor, action, allow_downed=True)
        if target is None or target.vitality != "downed":
            return "invalid", {"reason": "target_not_downed"}, []
        affinity = self.world.relations.stance(actor.id, target.id)
        if affinity < 0.3:
            return "invalid", {"reason": "affinity_too_low"}, []
        markers = vitality.revive(target, self.world, by=actor.id)
        self.world.relations.change(
            target.id,
            actor.id,
            affinity=0.2,
        )
        return "rescued", {"target": target.id}, markers

    def _withdraw(
        self,
        actor: Subject,
        action: Action,
        *,
        turn: int,
        day: int,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        changed = actor.change_stress(-0.5)
        return "withdrew", {"stress_delta": changed}, []

    def _guard(
        self,
        actor: Subject,
        action: Action,
        *,
        turn: int,
        day: int,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        guarded = [
            item for item in sorted(self.world.objectives) if actor.has_item(item)
        ]
        if not guarded:
            return "invalid", {"reason": "objective_unavailable"}, []
        return "guarded", {"items": guarded}, []
