"""Execution of the 13 Phase 0 and D1b verbs from implementation plan §3.6."""

from __future__ import annotations

import random
from typing import Any, TYPE_CHECKING

from engine import vitality
from engine.actions import Action, evidence_replay_order
from engine.contest import resolve, strength
from engine.phase2 import (
    apply_effect,
    dedicated_plant_options,
    disguise_options,
    expose_identity,
    grand_gesture_asset,
    grand_gesture_fact,
    mark_trial_completed,
    plant_from_action,
    ready_chosen_effects,
    trial_options,
)
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
            "sabotage": self._sabotage,
            "sacrifice": self._sacrifice,
            "mislead": self._mislead,
            "rethink": self._rethink,
            "confront": self._confront,
            "share_knowledge": self._share_knowledge,
            "give_item": self._give_item,
            "persuade": self._persuade,
            "pledge": self._pledge,
            "negotiate": self._negotiate,
            "concede": self._concede,
            "craft": self._craft,
            "fight": self._fight,
            "train": self._train,
            "rescue": self._rescue,
            "withdraw": self._withdraw,
            "guard": self._guard,
            "plant": self._plant,
            "payoff": self._payoff,
            "disguise": self._disguise,
            "grand_gesture": self._grand_gesture,
            "trial": self._trial,
            "donate": self._donate,
        }
        handler = handlers.get(action.verb)
        if handler is None:
            return "invalid", {"reason": "unknown_verb"}, []

        if self.world.action_graph_enabled and action.args:
            target = self.world.subjects.get(str(action.args[0]))
            if target is not None and target.id != actor.id:
                role = self.world.target_role(actor, target)
                if self.world.permission(action.verb, role) <= 0.0:
                    return (
                        "invalid",
                        {
                            "reason": "permission_denied",
                            "target": target.id,
                            "role": role,
                        },
                        [],
                    )
                source = (
                    str(action.args[1])
                    if action.verb == "neutralize"
                    and len(action.args) > 1
                    else None
                )
                if not self.world.prerequisite_ok(
                    action.verb,
                    actor,
                    target,
                    source=source,
                ):
                    return (
                        "invalid",
                        {
                            "reason": "prerequisite_unsatisfied",
                            "target": target.id,
                        },
                        [],
                    )

        result, details, markers = handler(
            actor,
            action,
            turn=turn,
            day=day,
        )
        markers.extend(
            plant_from_action(
                self.world,
                actor,
                action,
                result,
                details,
                turn=turn,
            )
        )
        return result, details, markers

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

    def _trust(
        self,
        receiver: Subject,
        speaker: Subject,
    ) -> float:
        return min(
            1.0,
            max(
                0.0,
                0.5
                + 0.5
                * self.world.relations.stance(
                    receiver.id,
                    speaker.id,
                ),
            ),
        )

    def _betrayal(
        self,
        actor: Subject,
        target: Subject,
        *,
        verb: str,
    ) -> list[dict[str, Any]]:
        if not self.world.is_pledged(actor.id, target.id):
            return []

        actor.reputation = round(actor.reputation - 0.5, 4)
        witnesses: list[str] = []
        for witness in self._present(actor):
            if witness.id == actor.id:
                continue
            self.world.relations.change(
                witness.id,
                actor.id,
                affinity=-0.3,
            )
            witnesses.append(witness.id)

        key = self.world.pledge_key(actor.id, target.id)
        pledge = self.world.pledges[key]
        pledge["broken_by"] = actor.id
        pledge["broken_with"] = verb
        return [
            {
                "verb": "betrayal",
                "subject": actor.id,
                "details": {
                    "target": target.id,
                    "pledge": list(key),
                    "broken_with": verb,
                    "subtype": "betray",
                    "witnesses": sorted(witnesses),
                },
            }
        ]

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
            subject.id
            for subject in self.world.present_subjects(actor.zone)
        }
        for fact, definition in sorted(self.world.facts.items()):
            if definition.get("values") is not None:
                continue
            if fact in actor.knowledge:
                continue
            for index, source in enumerate(
                definition.get("sources", []) or []
            ):
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
                progress = (
                    actor.gather_progress.get(progress_key, 0) + 1
                )
                actor.gather_progress[progress_key] = progress
                if progress < needed:
                    continue

                actor.knowledge.add(fact)
                actor.gather_progress[progress_key] = 0
                learned.append(fact)
                details: dict[str, Any] = {
                    "fact": fact,
                    "source": (
                        source.get("zone")
                        or source.get("agent")
                    ),
                }
                belief_updates = actor.apply_evidence(
                    fact,
                    self.world,
                )
                if belief_updates:
                    details["beliefs"] = belief_updates
                markers.append(
                    {
                        "verb": "learn_fact",
                        "subject": actor.id,
                        "details": details,
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
        target = self._target(
            actor,
            action,
            allow_downed=True,
        )
        if target is None:
            return "invalid", {"reason": "target_not_present"}, []

        present = self._present(actor)
        belief = actor.beliefs_about.setdefault(
            target.id,
            BeliefAbout(
                base_estimate=self.world.default_strength_prior
            ),
        )
        belief.observe_progress = round(
            belief.observe_progress
            + 0.5
            + actor.traits["curiosity"] * 0.5,
            4,
        )
        revealed: list[str] = []
        markers: list[dict[str, Any]] = []
        exposed_estimate: float | None = None

        if belief.observe_progress >= 1.0:
            hidden_sources = sorted(
                {
                    modifier.source
                    for modifier in target.all_modifiers(
                        self.world,
                        present,
                    )
                    if modifier.active
                    and not modifier.visible
                    and modifier.source
                    not in belief.known_modifiers
                }
            )
            if hidden_sources:
                source = hidden_sources[0]
                belief.known_modifiers.add(source)
                revealed.append(source)

            previous_estimate = belief.base_estimate
            misled_by = belief.misled_by
            identity_exposed = expose_identity(
                self.world,
                actor,
                target,
            )
            belief.base_estimate = target.base
            belief.observe_progress = 0.0

            if identity_exposed:
                markers.append(
                    {
                        "verb": "exposure",
                        "subject": actor.id,
                        "details": {
                            "target": target.id,
                            "displayed": target.identity_displayed,
                            "actual": target.id,
                            "source": "observe",
                        },
                    }
                )

            if misled_by is not None:
                belief.misled_by = None
                if previous_estimate != target.base:
                    exposed_estimate = previous_estimate
                    markers.append(
                        {
                            "verb": "exposure",
                            "subject": actor.id,
                            "details": {
                                "target": target.id,
                                "about": target.id,
                                "reported": previous_estimate,
                                "actual": target.base,
                                "misled_by": misled_by,
                                "source": "observe",
                            },
                        }
                    )

        details: dict[str, Any] = {
            "target": target.id,
            "revealed": revealed,
            "identity_seen": belief.identity_seen,
        }
        if self.world.action_graph_enabled:
            details["exposed_estimate"] = exposed_estimate
        return "observed", details, markers

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
                for modifier in target.all_modifiers(
                    self.world,
                    present,
                )
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

        markers = self._betrayal(
            actor,
            target,
            verb="neutralize",
        )
        for modifier in matching:
            modifier.active = False
            if not any(
                existing is modifier
                for existing in target.modifiers
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
        details: dict[str, Any] = {
            "neutralized": {
                "target": target.id,
                "source": source,
                "transferred": transferred,
            }
        }
        if action.meta.get("betrayal"):
            details["subtype"] = "betray"
        return "neutralized", details, markers


    def _sabotage(
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
        belief = actor.beliefs_about.get(target.id)
        if belief is None or not (
            belief.identity_seen or belief.known_modifiers
        ):
            return "invalid", {"reason": "target_unobserved"}, []

        markers = self._betrayal(
            actor,
            target,
            verb="sabotage",
        )
        before = target.base
        target.base = round(target.base - 6.0, 4)
        target.change_stress(1.0)
        self.world.relations.change(
            target.id,
            actor.id,
            affinity=-0.4,
            awareness=0.3,
        )
        return (
            "sabotaged",
            {
                "target": target.id,
                "base_before": before,
                "base_after": target.base,
                "subtype": (
                    "betray"
                    if action.meta.get("betrayal")
                    else None
                ),
            },
            markers,
        )


    def _sacrifice(
        self,
        actor: Subject,
        action: Action,
        *,
        turn: int,
        day: int,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        kind = str(action.args[0])
        if kind == "asset":
            item = action.meta.get("item")
            if not isinstance(item, str):
                return "invalid", {"reason": "asset_unspecified"}, []
            definition = self.world.items.get(item)
            valuable = (
                definition is not None
                and (
                    bool(definition.get("modifier"))
                    or (
                        definition.get("lootable", False)
                        and item not in self.world.objectives
                    )
                )
            )
            if not valuable or not actor.remove_item(item, 1):
                return (
                    "invalid",
                    {"reason": "asset_unavailable", "item": item},
                    [],
                )
            before = actor.base
            actor.base = round(
                actor.base
                + self.world.sacrifice_rewards["asset_base"],
                4,
            )
            return (
                "sacrificed",
                {
                    "kind": "asset",
                    "item": item,
                    "base_before": before,
                    "base_after": actor.base,
                },
                [],
            )

        if kind == "bond":
            target_id = action.meta.get("target")
            if not isinstance(target_id, str):
                return "invalid", {"reason": "bond_unspecified"}, []
            target = self.world.subjects.get(target_id)
            if (
                target is None
                or target.zone != actor.zone
                or target.vitality not in {"alive", "revived"}
                or self.world.relations.stance(
                    target.id,
                    actor.id,
                )
                < self.world.companionship["threshold"]
            ):
                return "invalid", {"reason": "bond_unavailable"}, []

            self.world.relations.change(
                target.id,
                actor.id,
                affinity=-0.5,
            )
            phase = str(
                self.world.sacrifice_rewards["bond_phase"]
            )
            actor.phase.add(phase)
            stress_delta = actor.change_stress(
                self.world.sacrifice_rewards["bond_stress"]
            )
            return (
                "sacrificed",
                {
                    "kind": "bond",
                    "target": target.id,
                    "phase": phase,
                    "stress_delta": stress_delta,
                },
                [],
            )

        return "invalid", {"reason": "unknown_sacrifice_kind"}, []


    def _mislead(
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

        about = str(action.args[1])
        reported = action.args[2]
        affinity = self.world.relations.stance(
            target.id,
            actor.id,
        )
        gate = 0.2 * target.traits["stubbornness"]
        if affinity < gate:
            return (
                "ignored",
                {
                    "target": target.id,
                    "about": about,
                    "value": reported,
                    "accurate": False,
                    "reason": "stubbornness_gate",
                },
                [],
            )

        trust = self._trust(target, actor)
        if action.meta.get("belief_kind") == "strength":
            value = float(reported)
            belief = target.beliefs_about.setdefault(
                about,
                BeliefAbout(
                    base_estimate=self.world.default_strength_prior
                ),
            )
            before = belief.base_estimate
            belief.base_estimate = round(
                before + (value - before) * trust,
                4,
            )
            belief.misled_by = actor.id
            return (
                "misled",
                {
                    "target": target.id,
                    "about": about,
                    "value": value,
                    "before": before,
                    "after": belief.base_estimate,
                    "trust": round(trust, 4),
                    "accurate": False,
                },
                [],
            )

        definition = self.world.facts.get(about)
        if definition is None or not definition.get("values"):
            return "invalid", {"reason": "unknown_valued_fact"}, []

        value = str(reported)
        if value not in {
            str(candidate)
            for candidate in definition["values"]
        }:
            return "invalid", {"reason": "unknown_fact_value"}, []

        confidence = float(action.meta.get("confidence", 0.5))
        update = target.update_belief(
            about,
            value,
            confidence * trust,
        )
        return (
            "misled",
            {
                "target": target.id,
                "about": about,
                "value": value,
                "confidence": round(confidence * trust, 4),
                "trust": round(trust, 4),
                "outcome": update["outcome"],
                "accurate": False,
            },
            [],
        )

    def _rethink(
        self,
        actor: Subject,
        action: Action,
        *,
        turn: int,
        day: int,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        before = {
            fact_id: {
                "value": belief.value,
                "confidence": belief.confidence,
            }
            for fact_id, belief in sorted(actor.beliefs.items())
        }

        evidence = [
            fact_id
            for fact_id in actor.knowledge
            if any(
                isinstance(
                    self.world.facts.get(fact_id, {}).get(
                        relation
                    ),
                    dict,
                )
                for relation in ("implies", "refutes")
            )
        ]
        evidence.sort(
            key=lambda fact_id: evidence_replay_order(
                self.world, fact_id
            )
        )

        touched_facts = {
            str(update["fact"])
            for evidence_id in evidence
            for relation in ("refutes", "implies")
            for update in [
                self.world.facts[
                    evidence_id
                ].get(relation)
            ]
            if isinstance(update, dict)
        }
        protected_facts = {
            fact_id
            for fact_id in touched_facts
            if actor.id
            in getattr(
                self.world,
                "fact_owners",
                {},
            ).get(fact_id, frozenset())
        }
        recomputed_facts = (
            touched_facts - protected_facts
        )

        for fact_id in sorted(recomputed_facts):
            actor.beliefs.pop(fact_id, None)

        refutations: dict[
            str,
            dict[str, float],
        ] = {}
        for evidence_id in evidence:
            definition = self.world.facts[evidence_id]
            for relation, refute in (
                ("refutes", True),
                ("implies", False),
            ):
                update = definition.get(relation)
                if not isinstance(update, dict):
                    continue

                fact_id = str(update["fact"])
                if fact_id in protected_facts:
                    continue

                value = str(update["value"])
                confidence = float(
                    update.get("confidence", 0.5)
                )
                if refute:
                    values = refutations.setdefault(
                        fact_id,
                        {},
                    )
                    values[value] = max(
                        confidence,
                        values.get(value, 0.0),
                    )
                    continue

                if value in refutations.get(fact_id, {}):
                    continue

                actor.update_belief(
                    fact_id,
                    value,
                    confidence,
                    derived=True,
                )

        for fact_id, excluded in sorted(
            refutations.items()
        ):
            definition = self.world.facts.get(
                fact_id,
                {},
            )
            candidates = sorted(
                str(value)
                for value in (
                    definition.get("values", []) or []
                )
            )
            remaining = [
                value
                for value in candidates
                if value not in excluded
            ]
            if (
                len(remaining) != 1
                or len(excluded)
                != len(candidates) - 1
            ):
                continue

            confidence = min(
                excluded[value]
                for value in candidates
                if value != remaining[0]
            )
            actor.update_belief(
                fact_id,
                remaining[0],
                confidence,
                derived=True,
            )

        after = {
            fact_id: {
                "value": belief.value,
                "confidence": belief.confidence,
            }
            for fact_id, belief in sorted(actor.beliefs.items())
        }
        changed = before != after
        details = {
            "before": before,
            "after": after,
            "evidence": evidence,
            "protected_facts": sorted(protected_facts),
        }
        markers = (
            [
                {
                    "verb": "rethink",
                    "subject": actor.id,
                    "details": details,
                }
            ]
            if changed
            else []
        )
        return (
            "rethought" if changed else "unchanged",
            details,
            markers,
        )

    def _confront(
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

        fact_id = str(action.args[1])
        belief = actor.beliefs.get(fact_id)
        definition = self.world.facts.get(fact_id)
        if belief is None or definition is None:
            return "invalid", {"reason": "belief_unavailable"}, []
        threshold = float(definition.get("act_threshold", 0.6))
        if (
            belief.confidence < threshold
            or belief.value != target.id
        ):
            return "invalid", {"reason": "belief_below_threshold"}, []

        truth = self.world.truth.get(fact_id)
        if truth is None:
            return "invalid", {"reason": "truth_unavailable"}, []
        correct = truth == belief.value
        markers: list[dict[str, Any]] = []
        identity_observers: list[str] = []

        if correct:
            self.world.confront_successes.add((actor.id, fact_id))
            if (
                target.identity_displayed != target.id
                and expose_identity(
                    self.world,
                    actor,
                    target,
                )
            ):
                identity_observers.append(actor.id)

            self.world.relations.change(
                target.id,
                actor.id,
                affinity=-0.4,
            )
            witnesses: list[str] = []
            for witness in self._present(actor):
                if witness.id in {actor.id, target.id}:
                    continue
                if (
                    target.identity_displayed != target.id
                    and expose_identity(
                        self.world,
                        witness,
                        target,
                    )
                ):
                    identity_observers.append(witness.id)
                self.world.relations.change(
                    witness.id,
                    target.id,
                    awareness=0.2,
                )
                witnesses.append(witness.id)

            markers.append(
                {
                    "verb": "exposure",
                    "subject": actor.id,
                    "details": {
                        "target": target.id,
                        "fact": fact_id,
                        "value": belief.value,
                        "witnesses": sorted(witnesses),
                        "identity_observers": sorted(
                            identity_observers
                        ),
                    },
                }
            )
            result = "exposed"
        else:
            actor.reputation = round(
                actor.reputation - 0.1,
                4,
            )
            self.world.relations.change(
                target.id,
                actor.id,
                affinity=-0.3,
            )
            belief.confidence = round(
                belief.confidence * 0.5,
                4,
            )
            markers.append(
                {
                    "verb": "misjudged",
                    "subject": actor.id,
                    "details": {
                        "target": target.id,
                        "fact": fact_id,
                        "value": belief.value,
                    },
                }
            )
            result = "misjudged"

        return (
            result,
            {
                "target": target.id,
                "fact": fact_id,
                "value": belief.value,
                "confidence": belief.confidence,
                "correct": correct,
                "identity_observers": sorted(
                    identity_observers
                ),
            },
            markers,
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
        definition = self.world.facts.get(topic)
        valued = bool(
            definition is not None
            and definition.get("values") is not None
        )
        if topic != "雑談":
            if definition is None:
                return (
                    "invalid",
                    {"reason": "unknown_fact", "fact": topic},
                    [],
                )
            minimum = float(
                definition.get("share_min_affinity", 0.0)
            )
            if (
                self.world.relations.stance(actor.id, target.id)
                < minimum
            ):
                return (
                    "invalid",
                    {
                        "reason": "affinity_too_low",
                        "fact": topic,
                    },
                    [],
                )
            if valued:
                if topic not in actor.beliefs:
                    return (
                        "invalid",
                        {
                            "reason": "belief_unavailable",
                            "fact": topic,
                        },
                        [],
                    )
            elif (
                topic not in actor.knowledge
                or topic in target.knowledge
            ):
                return (
                    "invalid",
                    {
                        "reason": "fact_unavailable",
                        "fact": topic,
                    },
                    [],
                )

        # Hearsay confidence uses the relationship before this action changes it.
        trust = self._trust(target, actor)

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
        details: dict[str, Any] = {
            "target": target.id,
            "topic": topic,
        }
        if valued:
            speaker_belief = actor.beliefs[topic]
            confidence = round(
                speaker_belief.confidence * trust,
                4,
            )
            update = target.update_belief(
                topic,
                speaker_belief.value,
                confidence,
            )
            details.update(
                {
                    "fact": topic,
                    "value": speaker_belief.value,
                    "confidence": confidence,
                    "outcome": update["outcome"],
                }
            )
            markers.append(
                {
                    "verb": "learn_fact",
                    "subject": target.id,
                    "details": {
                        "fact": topic,
                        "from": actor.id,
                        "belief": update,
                    },
                }
            )
        elif topic != "雑談":
            target.knowledge.add(topic)
            marker_details: dict[str, Any] = {
                "fact": topic,
                "from": actor.id,
            }
            belief_updates = target.apply_evidence(
                topic,
                self.world,
            )
            if belief_updates:
                marker_details["beliefs"] = belief_updates
            markers.append(
                {
                    "verb": "learn_fact",
                    "subject": target.id,
                    "details": marker_details,
                }
            )
        return "shared", details, markers

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

    def _persuade(
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

        receiver_delta = round(
            0.08 * (1.0 + actor.traits["social"]),
            4,
        )
        self.world.relations.change(
            target.id,
            actor.id,
            affinity=receiver_delta,
        )
        self.world.relations.change(
            actor.id,
            target.id,
            affinity=0.04,
        )
        return (
            "persuaded",
            {
                "target": target.id,
                "receiver_affinity": receiver_delta,
                "giver_affinity": 0.04,
            },
            [],
        )


    def _pledge(
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
        if (
            self.world.relations.stance(actor.id, target.id) < 0.4
            or self.world.relations.stance(target.id, actor.id) < 0.4
        ):
            return "invalid", {"reason": "stance_too_low"}, []
        if self.world.is_pledged(actor.id, target.id):
            return "invalid", {"reason": "already_pledged"}, []

        actor.phase.add(f"誓約:{target.id}")
        target.phase.add(f"誓約:{actor.id}")
        self.world.relations.change(
            actor.id,
            target.id,
            affinity=0.15,
        )
        self.world.relations.change(
            target.id,
            actor.id,
            affinity=0.15,
        )
        key = self.world.pledge_key(actor.id, target.id)
        self.world.pledges[key] = {
            "turn": turn,
            "parties": list(key),
            "reputation": {
                actor.id: actor.reputation,
                target.id: target.reputation,
            },
        }
        return (
            "pledged",
            {
                "target": target.id,
                "pledge": list(key),
            },
            [],
        )


    def _negotiate(
        self,
        actor: Subject,
        action: Action,
        *,
        turn: int,
        day: int,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        holder = self._target(actor, action)
        objective = actor.goal.target
        if holder is None:
            return "invalid", {"reason": "target_not_present"}, []
        if (
            objective is None
            or not actor.objective_claimant
            or self.world.holder(objective) != holder.id
        ):
            return "invalid", {"reason": "objective_unavailable"}, []

        assets = {
            item: count
            for item, count in sorted(actor.inventory.items())
            if count > 0
            and item != objective
            and self.world.items[item].get("lootable", False)
        }
        self.world.offers[(actor.id, holder.id)] = {
            "turn": turn,
            "objective": objective,
            "assets": assets,
        }
        self.world.relations.change(
            holder.id,
            actor.id,
            affinity=0.05,
        )
        return (
            "offered",
            {
                "holder": holder.id,
                "objective": objective,
                "assets": assets,
            },
            [],
        )


    def _concede(
        self,
        actor: Subject,
        action: Action,
        *,
        turn: int,
        day: int,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        claimant = self._target(actor, action)
        if claimant is None:
            return "invalid", {"reason": "target_not_present"}, []

        key = (claimant.id, actor.id)
        offer = self.world.offers.get(key)
        if offer is None:
            return "invalid", {"reason": "offer_unavailable"}, []
        objective = str(offer.get("objective", ""))
        if (
            not objective
            or self.world.holder(objective) != actor.id
            or claimant.goal.target != objective
        ):
            return "invalid", {"reason": "objective_unavailable"}, []

        raw_assets = offer.get("assets", {}) or {}
        attractive = [
            item
            for item in sorted(raw_assets)
            if int(raw_assets[item]) > 0
            and claimant.inventory.get(item, 0) > 0
            and not actor.has_item(item)
            and bool(self.world.items[item].get("modifier"))
        ]
        stance = self.world.relations.stance(
            actor.id,
            claimant.id,
        )
        if stance < self.world.negotiate_threshold and not attractive:
            return "invalid", {"reason": "offer_insufficient"}, []

        mode = "trade" if attractive else "goodwill"
        if not actor.remove_item(objective, 1):
            return "invalid", {"reason": "objective_unavailable"}, []
        claimant.add_item(objective, 1)

        transferred: dict[str, int] = {}
        if mode == "trade":
            for item, offered_count in sorted(raw_assets.items()):
                count = min(
                    int(offered_count),
                    claimant.inventory.get(item, 0),
                )
                if count <= 0:
                    continue
                claimant.remove_item(item, count)
                actor.add_item(item, count)
                transferred[item] = count

        self.world.relations.change(
            actor.id,
            claimant.id,
            affinity=0.2,
        )
        self.world.relations.change(
            claimant.id,
            actor.id,
            affinity=0.2,
        )
        del self.world.offers[key]
        return (
            "conceded",
            {
                "claimant": claimant.id,
                "objective": objective,
                "mode": mode,
                "assets": transferred,
            },
            [],
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
            return (
                "invalid",
                {"reason": "not_a_recipe", "item": item},
                [],
            )
        definition = self.world.items[item]
        required_fact = (
            definition.get("requires") or {}
        ).get("knowledge")
        if (
            required_fact is not None
            and required_fact not in actor.knowledge
        ):
            return (
                "invalid",
                {
                    "reason": "missing_knowledge",
                    "fact": required_fact,
                },
                [],
            )
        craft_zone = definition.get("craft_zone")
        if craft_zone is not None and actor.zone != craft_zone:
            return (
                "invalid",
                {"reason": "wrong_zone", "zone": craft_zone},
                [],
            )

        recipe = self.world.recipes[item]
        if not all(
            actor.inventory.get(material, 0) >= int(required)
            for material, required in recipe.items()
        ):
            return (
                "invalid",
                {"reason": "missing_materials", "item": item},
                [],
            )

        consumed: dict[str, int] = {}
        for material, required in sorted(recipe.items()):
            count = int(required)
            actor.remove_item(material, count)
            consumed[material] = count
        actor.add_item(item, 1)
        return (
            "crafted",
            {
                "item": item,
                "count": 1,
                "consumed": consumed,
            },
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

        hostile_at_start = (
            self.world.relations.stance(actor.id, target.id) < -0.2
            or self.world.perceived_name(
                actor.id,
                target.id,
            )
            in actor.goal.obstacles
        )
        markers = self._betrayal(
            actor,
            target,
            verb="fight",
        )

        for observer, revealed in (
            (actor, target),
            (target, actor),
        ):
            if (
                revealed.identity_displayed == revealed.id
                or not expose_identity(
                    self.world,
                    observer,
                    revealed,
                )
            ):
                continue
            markers.append(
                {
                    "verb": "exposure",
                    "subject": observer.id,
                    "details": {
                        "target": revealed.id,
                        "displayed": revealed.identity_displayed,
                        "actual": revealed.id,
                        "source": "fight",
                    },
                }
            )

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
            for modifier in winner.all_modifiers(
                self.world,
                present,
            )
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
        markers.extend(vitality.down(loser, self.world, turn))

        lethal_roll: float | None = None
        lethal_chance: float | None = None
        if (
            hostile_at_start
            and lethal_modifiers
            and loser.id not in self.world.vitality["lethal_exempt"]
        ):
            lethal_chance = max(
                modifier.lethal_chance
                for modifier in lethal_modifiers
            )
            lethal_roll = self.rng.random()
            if lethal_roll < lethal_chance:
                markers.extend(
                    vitality.kill(
                        loser,
                        self.world,
                        winner,
                    )
                )

        details: dict[str, Any] = {
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
            "actor_strength": strength(
                actor,
                self.world,
                present,
            ),
            "target_strength": strength(
                target,
                self.world,
                present,
            ),
        }
        if action.meta.get("betrayal"):
            details["subtype"] = "betray"
        return (
            "won" if winner.id == actor.id else "lost",
            details,
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

    def _plant(
        self,
        actor: Subject,
        action: Action,
        *,
        turn: int,
        day: int,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        effect_id = action.meta.get("effect_id")
        if not isinstance(effect_id, str):
            return "invalid", {"reason": "effect_unspecified"}, []

        available = {
            str(effect["id"])
            for effect in dedicated_plant_options(
                self.world,
                actor,
                turn=turn,
                day=day,
            )
        }
        if effect_id not in available:
            return (
                "invalid",
                {
                    "reason": "effect_unavailable",
                    "effect_id": effect_id,
                },
                [],
            )

        return (
            "planted",
            {
                "effect_id": effect_id,
                "target": actor.id,
            },
            [],
        )

    def _payoff(
        self,
        actor: Subject,
        action: Action,
        *,
        turn: int,
        day: int,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        pending_id = action.meta.get("effect_id")
        if not isinstance(pending_id, str):
            return "invalid", {"reason": "effect_unspecified"}, []

        ready = {
            str(pending["id"]): pending
            for pending in ready_chosen_effects(
                self.world,
                actor,
                turn=turn,
                day=day,
            )
        }
        pending = ready.get(pending_id)
        if pending is None:
            return (
                "invalid",
                {
                    "reason": "effect_unavailable",
                    "effect_id": pending_id,
                },
                [],
            )

        details = apply_effect(
            self.world,
            pending,
            turn=turn,
        )
        return (
            "paid_off",
            details,
            [
                {
                    "verb": "payoff",
                    "subject": actor.id,
                    "details": details,
                }
            ],
        )

    def _disguise(
        self,
        actor: Subject,
        action: Action,
        *,
        turn: int,
        day: int,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        disguise_id = action.meta.get("disguise_id")
        displayed = action.meta.get("displayed")
        if (
            not isinstance(disguise_id, str)
            or not isinstance(displayed, str)
        ):
            return "invalid", {"reason": "disguise_unspecified"}, []

        available = {
            str(disguise["id"]): disguise
            for disguise in disguise_options(
                self.world,
                actor,
                turn=turn,
                day=day,
            )
        }
        disguise = available.get(disguise_id)
        if (
            disguise is None
            or str(disguise["as"]) != displayed
        ):
            return "invalid", {"reason": "disguise_unavailable"}, []

        unaware: list[str] = []
        for observer in sorted(
            self._present(actor),
            key=lambda value: value.id,
        ):
            if observer.id == actor.id:
                continue
            if (
                self.world.relations.awareness(
                    observer.id,
                    actor.id,
                )
                >= 0.5
            ):
                continue
            belief = observer.beliefs_about.setdefault(
                actor.id,
                BeliefAbout(
                    base_estimate=self.world.default_strength_prior
                ),
            )
            belief.identity_seen = False
            unaware.append(observer.id)

        before = actor.identity_displayed
        actor.identity_displayed = displayed
        return (
            "disguised",
            {
                "before": before,
                "displayed": displayed,
                "unaware": unaware,
            },
            [],
        )

    def _grand_gesture(
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

        fact = grand_gesture_fact(
            self.world,
            actor,
            target,
        )
        if fact is None:
            return "invalid", {"reason": "wound_unknown"}, []

        item = grand_gesture_asset(self.world, actor)
        if item is None or not actor.remove_item(item, 1):
            return "invalid", {"reason": "asset_unavailable"}, []

        present = sorted(
            self._present(actor),
            key=lambda value: value.id,
        )
        allies = [
            witness
            for witness in present
            if witness.id != actor.id
            and self.world.target_role(actor, witness) == "ally"
        ]
        for ally in allies:
            self.world.relations.change(
                actor.id,
                ally.id,
                affinity=-0.2,
            )

        self.world.relations.change(
            target.id,
            actor.id,
            affinity=0.6,
        )

        witnesses: list[str] = []
        for witness in present:
            if witness.id == actor.id:
                continue
            self.world.relations.change(
                witness.id,
                actor.id,
                awareness=0.3,
            )
            witnesses.append(witness.id)

        actor.reputation = round(
            actor.reputation + 0.1,
            4,
        )
        return (
            "grand_gesture",
            {
                "target": target.id,
                "fact": fact,
                "item": item,
                "allies": [ally.id for ally in allies],
                "witnesses": witnesses,
            },
            [],
        )

    def _trial(
        self,
        actor: Subject,
        action: Action,
        *,
        turn: int,
        day: int,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        trial_id = action.meta.get("trial_id")
        if not isinstance(trial_id, str):
            return "invalid", {"reason": "trial_unspecified"}, []

        available = {
            str(trial["id"]): trial
            for trial in trial_options(self.world, actor)
        }
        trial = available.get(trial_id)
        if trial is None:
            return "invalid", {"reason": "trial_unavailable"}, []

        giver = self.world.subjects[str(trial["giver"])]
        grants = trial["grants"]
        granted: dict[str, str] = {}

        granted_fact = grants.get("fact")
        if granted_fact is not None:
            fact = str(granted_fact)
            actor.knowledge.add(fact)
            actor.apply_evidence(fact, self.world)
            granted["fact"] = fact

        granted_item = grants.get("item")
        if granted_item is not None:
            item = str(granted_item)
            actor.add_item(item, 1)
            granted["item"] = item

        self.world.relations.change(
            actor.id,
            giver.id,
            affinity=0.1,
        )
        self.world.relations.change(
            giver.id,
            actor.id,
            affinity=0.1,
        )
        mark_trial_completed(actor, trial)
        return (
            "trial_completed",
            {
                "trial_id": trial_id,
                "giver": giver.id,
                "granted": granted,
            },
            [],
        )

    def _donate(
        self,
        actor: Subject,
        action: Action,
        *,
        turn: int,
        day: int,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        item = action.meta.get("item")
        if (
            not isinstance(item, str)
            or actor.goal.target != item
            or not actor.has_item(item)
        ):
            return "invalid", {"reason": "objective_unavailable"}, []
        if "還元" in actor.phase:
            return "invalid", {"reason": "already_pledged"}, []

        actor.phase.add("還元")
        actor.reputation = round(
            actor.reputation + 0.3,
            4,
        )
        return (
            "donated",
            {
                "item": item,
                "phase": "還元",
                "reputation_delta": 0.3,
                "holder": actor.id,
            },
            [],
        )
