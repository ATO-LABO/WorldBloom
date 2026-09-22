"""Deterministic day/slot simulation and policy hook for implementation plan §3.8."""

from __future__ import annotations

import hashlib
import random
from dataclasses import asdict, is_dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from engine.actions import Action, candidates
from engine.contest import believed_strength, strength
from engine.decision_record import VERSION, RULE, record_distribution
from engine.log import (
    LayersWriter,
    delta_effective,
    make_delta,
    relation_diff,
)
from engine.subject import Subject
from engine.phase2 import (
    apply_effect,
    dangling_effect_count,
    phase2_configured,
    ready_auto_effects,
)
from engine.verbs import VerbEngine
from engine.vitality import tick
from engine.world import World


def _clip(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return min(upper, max(lower, value))


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if is_dataclass(value):
        return _plain(asdict(value))
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, set):
        return [_plain(item) for item in sorted(value, key=str)]
    return value


@lru_cache(maxsize=1)
def _engine_source_hash(engine_dir: Path) -> str:
    """Hash engine sources in deterministic name order."""

    paths = tuple(
        sorted(
            engine_dir.glob("*.py"),
            key=lambda value: value.name,
        )
    )
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


class Simulation:
    """Run one reproducible world trajectory and write layers.jsonl."""

    def __init__(
        self,
        seed: int,
        world: World,
        subjects: dict[str, Subject],
        out_dir: Path,
        policies: dict[str, Any] | None = None,
        precedent: Any | None = None,
        record_explanations: bool = False,
    ) -> None:
        self.seed = int(seed)
        self.world = world
        self.subjects = {
            subject_id: subjects[subject_id]
            for subject_id in sorted(subjects)
        }
        self.out_dir = Path(out_dir)
        self.precedent = precedent
        self.record_explanations = bool(record_explanations)
        self._decision_context: dict[str, Any] | None = None
        self.rng = random.Random(self.seed)
        self.turn = 0
        self.day = 0
        self.slot: str | None = None
        self._last_fact_turn: dict[str, int] = {}
        self._previous_snapshot_layers: dict[str, Any] | None = None

        for subject_id in sorted(self.subjects):
            subject = self.subjects[subject_id]
            if subject.stamina_max <= 0.0:
                subject.stamina_max = self.world.stamina["default_max"]
                subject.stamina = subject.stamina_max
            if subject.stamina_recover <= 0.0:
                subject.stamina_recover = self.world.stamina[
                    "default_recover_per_slot"
                ]
            subject.policy = (
                policies.get(subject_id)
                if policies is not None
                else None
            )

        self.world.resolve_truth(self.seed)
        self.world.bind_subjects(self.subjects)
        self.verb_engine = VerbEngine(self.world, self.rng)

    def _present_for(self, subject: Subject) -> list[Subject]:
        return self.world.present_subjects(subject.zone)

    def _layer_snapshots(
        self,
        subject_ids: set[str] | None = None,
        objective: dict[str, str | None] | None = None,
    ) -> dict[str, dict[str, Any]]:
        selected = (
            sorted(self.subjects)
            if subject_ids is None
            else sorted(subject_ids)
        )
        return {
            subject_id: self.subjects[subject_id].layer_snapshot(
                self.world,
                self._present_for(self.subjects[subject_id]),
                objective=objective,
            )
            for subject_id in selected
        }

    def _objective_snapshot(self) -> dict[str, str | None]:
        return {
            item: self.world.holder(item)
            for item in sorted(self.world.objectives)
        }

    def _capture(
        self,
        subject_ids: set[str] | None = None,
    ) -> tuple[
        dict[str, dict[str, Any]],
        dict[str, dict[str, dict[str, float]]],
        dict[str, str | None],
    ]:
        objective = self._objective_snapshot()
        return (
            self._layer_snapshots(subject_ids, objective),
            self.world.relations.snapshot(),
            objective,
        )

    def _action_capture_ids(
        self,
        subject: Subject,
        action: Action,
    ) -> set[str]:
        """Return subjects whose layer snapshots can change synchronously."""

        all_subjects = set(self.subjects)
        if action.verb == "fight" or action.meta.get("betrayal", False):
            # Fight loot/vitality and betrayal witnesses can change globally.
            return all_subjects

        if action.verb == "move":
            destination = action.meta.get("dest")
            if not isinstance(destination, str):
                return all_subjects
            affected_zones = {subject.zone, destination}
            return {
                peer.id
                for peer in self.subjects.values()
                if peer.id == subject.id
                or peer.zone in affected_zones
            }

        if action.verb == "rescue":
            return {
                peer.id
                for peer in self.subjects.values()
                if peer.id == subject.id
                or peer.zone == subject.zone
            }

        if action.verb in {
            "share_knowledge",
            "give_item",
            "neutralize",
            "sabotage",
            "mislead",
            "confront",
            "persuade",
            "pledge",
            "negotiate",
            "concede",
        }:
            target = action.meta.get("target")
            if isinstance(target, str) and target in self.subjects:
                return {subject.id, target}
            return {subject.id}

        if action.verb == "sacrifice":
            target = action.meta.get("target")
            if isinstance(target, str) and target in self.subjects:
                return {subject.id, target}
            return {subject.id}

        if action.verb in {
            "rest",
            "investigate",
            "observe",
            "rethink",
            "craft",
            "train",
            "withdraw",
            "guard",
        }:
            return {subject.id}

        # Unknown future verbs retain the conservative full-capture behavior.
        return all_subjects

    def _delta(
        self,
        actor: str | None,
        before: tuple[
            dict[str, dict[str, Any]],
            dict[str, dict[str, dict[str, float]]],
            dict[str, str | None],
        ],
        after: tuple[
            dict[str, dict[str, Any]],
            dict[str, dict[str, dict[str, float]]],
            dict[str, str | None],
        ],
    ) -> dict[str, Any]:
        return make_delta(
            actor,
            before[0],
            after[0],
            before[1],
            after[1],
            before[2],
            after[2],
        )

    def _engine_hash(self) -> str:
        return _engine_source_hash(Path(__file__).resolve().parent)

    def _protagonist_policy(self) -> Any | None:
        return self.subjects[self.world.protagonist].policy

    # WB-JEV-001 Stage 2 (Opus review R1): the header is written before the
    # run happens, so a "judge_calls"/"budget_exhausted"/"judge_disabled"
    # here would always read as 0/absent regardless of what actually
    # happened during the run -- misleadingly implying nothing was ever
    # called. Those belong on the per-seed run_result and the generation
    # summary (gapengine.evolve), not the header.
    _RATIONALITY_HEADER_KEYS = (
        "kappa",
        "method",
        "backend",
        "model",
        "num_ctx",
        "table_hash_at_start",
    )

    def _rationality_meta(self) -> dict[str, Any] | None:
        """WB-JEV-001 Stage 2: the protagonist policy's Rationality.meta
        (filtered to the static config subset above), or None when there is
        no policy, no Rationality, or it is disabled (kappa<=0) -- in every
        one of those cases the header must omit the "rationality" key
        entirely so pre-Stage-2 runs stay byte-identical."""

        policy = self._protagonist_policy()
        if policy is None:
            return None
        rationality = getattr(policy, "rationality", None)
        if rationality is None or not getattr(rationality, "enabled", False):
            return None
        meta = getattr(rationality, "meta", None)
        if not isinstance(meta, dict):
            return None
        return {
            key: meta[key]
            for key in self._RATIONALITY_HEADER_KEYS
            if key in meta
        }

    def _genome_dict(self) -> dict[str, Any] | None:
        policy = self._protagonist_policy()
        if policy is None:
            return None
        genome = getattr(policy, "genome", None)
        if genome is None:
            return None
        to_dict = getattr(genome, "to_dict", None)
        if callable(to_dict):
            return _plain(to_dict())
        if is_dataclass(genome):
            return _plain(asdict(genome))
        if isinstance(genome, dict):
            return _plain(genome)
        return None

    def _precedent_hash(self) -> str | None:
        if self.precedent is None:
            policy = self._protagonist_policy()
            if policy is not None:
                value = getattr(policy, "precedent_hash", None)
                if isinstance(value, str):
                    return value
                precedent = getattr(policy, "precedent", None)
            else:
                precedent = None
        else:
            precedent = self.precedent

        if precedent is None:
            return None
        value = getattr(precedent, "hash", None)
        if isinstance(value, str):
            return value
        to_json = getattr(precedent, "to_json", None)
        if callable(to_json):
            serialized = to_json()
            if not isinstance(serialized, str):
                serialized = str(serialized)
            return hashlib.sha256(
                serialized.encode("utf-8")
            ).hexdigest()
        return None

    def _header(self) -> dict[str, Any]:
        header = {
            "kind": "header",
            "seed": self.seed,
            "world": self.world.name,
            "protagonist": self.world.protagonist,
            "antagonist": self.world.antagonist,
            "genome": self._genome_dict(),
            "precedent_hash": self._precedent_hash(),
            "engine_hash": self._engine_hash(),
        }
        if self.record_explanations:
            header["explanation_recording"] = {"version": VERSION, "rule": RULE, "cost_baseline_version": 1}
        if self.world.drawn_truth:
            header["truth"] = _plain(self.world.drawn_truth)
        rationality_meta = self._rationality_meta()
        if rationality_meta is not None:
            header["rationality"] = rationality_meta
        return header

    def _event_row(
        self,
        *,
        verb: str,
        subject: str | None,
        delta: dict[str, Any],
        details: dict[str, Any],
        result: str = "applied",
        event_id: str | None = None,
    ) -> dict[str, Any]:
        if not delta:
            delta = {
                "actor": {},
                "targets": {},
                "relations": [],
                "objective": None,
            }
        row: dict[str, Any] = {
            "kind": "event",
            "turn": self.turn,
            "day": self.day,
            "slot": self.slot,
            "subject": subject,
            "verb": verb,
            "args": [],
            "result": result,
            "delta": delta,
            "details": _plain(details),
        }
        if event_id is not None:
            row["id"] = event_id
        return row

    def _write_markers(
        self,
        writer: LayersWriter,
        markers: list[dict[str, Any]],
    ) -> None:
        for marker in markers:
            if marker.get("verb") == "learn_fact":
                subject_id = marker.get("subject")
                if isinstance(subject_id, str):
                    self._last_fact_turn[subject_id] = self.turn
            writer.write(
                self._event_row(
                    verb=str(marker["verb"]),
                    subject=marker.get("subject"),
                    delta={},
                    details=dict(marker.get("details", {})),
                )
            )

    def _apply_event_effects(
        self,
        event: dict[str, Any],
        targets: list[Subject],
    ) -> None:
        grant = event.get("grants_item")
        grant_fact = event.get("grants_fact")
        move_to = event.get("move_to")
        stress_delta = float(event.get("stress_delta", 0.0))

        for target in targets:
            if grant:
                target.add_item(
                    str(grant["name"]),
                    int(grant.get("count", 1)),
                )
            if grant_fact is not None:
                fact_id = str(grant_fact)
                definition = self.world.facts.get(fact_id)
                if definition is None:
                    raise ValueError(
                        f"Unknown event fact: {fact_id}"
                    )
                if definition.get("values") is not None:
                    raise ValueError(
                        f"Event cannot directly grant valued fact: "
                        f"{fact_id}"
                    )
                target.knowledge.add(fact_id)
                target.apply_evidence(fact_id, self.world)
            if move_to is not None:
                destination = str(move_to)
                if destination not in self.world.zones:
                    raise ValueError(
                        f"Unknown event destination: {destination}"
                    )
                target.zone = destination
            if stress_delta:
                target.change_stress(stress_delta)

    def _apply_scheduled(
        self,
        writer: LayersWriter,
        *,
        slot: str | None,
    ) -> None:
        for event in self.world.scheduled_for(self.day, slot):
            target_ids = sorted(str(value) for value in event.get("targets", []))
            targets: list[Subject] = []
            for target_id in target_ids:
                if target_id not in self.subjects:
                    raise ValueError(
                        f"Unknown scheduled event target: {target_id}"
                    )
                target = self.subjects[target_id]
                if target.vitality != "dead":
                    targets.append(target)

            before = self._capture()
            self._apply_event_effects(event, targets)
            after = self._capture()
            writer.write(
                self._event_row(
                    verb="scheduled_event",
                    subject=None,
                    delta=self._delta(None, before, after),
                    details={
                        "event_id": event.get("id"),
                        "label": event.get("label"),
                        "targets": [target.id for target in targets],
                    },
                    event_id=str(event["id"]),
                )
            )

    def _apply_daily(self, writer: LayersWriter) -> None:
        if not self.world.daily_events:
            return
        weights = [
            float(event.get("weight", 1.0))
            for event in self.world.daily_events
        ]
        for subject_id in sorted(self.subjects):
            subject = self.subjects[subject_id]
            if subject.vitality == "dead":
                continue
            if self.rng.random() >= self.world.daily_event_chance:
                continue
            event = self.rng.choices(
                self.world.daily_events,
                weights=weights,
                k=1,
            )[0]
            before = self._capture()
            self._apply_event_effects(event, [subject])
            after = self._capture()
            writer.write(
                self._event_row(
                    verb="daily_event",
                    subject=subject.id,
                    delta=self._delta(subject.id, before, after),
                    details={
                        "event_id": event.get("id"),
                        "label": event.get("label"),
                    },
                    event_id=str(event["id"]),
                )
            )

    def _record_encounters(self, writer: LayersWriter) -> None:
        before_relations = self.world.relations.snapshot()
        pairs: list[dict[str, str]] = []
        for zone in sorted(self.world.zones):
            subjects = self.world.present_subjects(zone)
            names = sorted(subject.id for subject in subjects)
            for index, first in enumerate(names):
                for second in names[index + 1 :]:
                    self.world.relations.change(
                        first,
                        second,
                        awareness=self.world.awareness_per_encounter,
                    )
                    self.world.relations.change(
                        second,
                        first,
                        awareness=self.world.awareness_per_encounter,
                    )
                    pairs.append(
                        {
                            "first": first,
                            "second": second,
                            "zone": zone,
                        }
                    )

        after_relations = self.world.relations.snapshot()
        changed_relations = relation_diff(
            before_relations,
            after_relations,
        )
        if not changed_relations:
            return

        writer.write(
            self._event_row(
                verb="encounters",
                subject=None,
                delta={
                    "actor": {},
                    "targets": {},
                    "relations": changed_relations,
                    "objective": None,
                },
                details={"pairs": pairs},
            )
        )

    def _leadership(self, subject: Subject) -> float:
        present = self._present_for(subject)
        followers = sum(
            1
            for peer in present
            if peer.id != subject.id
            and peer.vitality != "dead"
            and self.world.relations.stance(peer.id, subject.id)
            >= self.world.companionship["threshold"]
        )
        temperament = (
            subject.traits["social"]
            + subject.traits["curiosity"]
            + subject.traits["temper"]
        ) / 3.0
        return followers + 0.5 * temperament

    def _action_order(self) -> list[str]:
        active = [
            subject
            for subject in self.subjects.values()
            if subject.vitality != "dead"
        ]
        return [
            subject.id
            for subject in sorted(
                active,
                key=lambda value: (-self._leadership(value), value.id),
            )
        ]

    def choose_action(
        self,
        subject: Subject,
    ) -> tuple[Action, float | None]:
        self._decision_context = None
        if self.record_explanations:
            self._decision_context = {"zone": subject.zone,
                                      "present": [s.id for s in self._present_for(subject)],
                                      "selection": record_distribution([], [], None, fallback="no_candidates")}
        weighted = candidates(subject, self.world, self)
        if not weighted:
            return Action("rest"), None

        policy = subject.policy
        if policy is not None:
            reweight = getattr(policy, "reweight", None)
            if not callable(reweight):
                raise TypeError(
                    f"Policy for {subject.id} has no reweight method"
                )
            weighted = reweight(
                subject,
                self.world,
                self._present_for(subject),
                weighted,
                turn=self.turn,
                day=self.day,
            )

        if not weighted:
            return Action("rest"), None
        weights = [max(0.0, float(weight)) for _, weight in weighted]
        total = sum(weights)
        if total <= 0.0:
            if self.record_explanations:
                self._decision_context["selection"] = record_distribution(
                    weighted, weights, None, fallback="non_positive_total")
            return Action("rest"), None

        index = self.rng.choices(
            range(len(weighted)),
            weights=weights,
            k=1,
        )[0]
        action = weighted[index][0]
        probability = weights[index] / total
        if self.record_explanations:
            self._decision_context["selection"] = record_distribution(weighted, weights, index)

        if policy is not None:
            record = getattr(policy, "record", None)
            if not callable(record):
                raise TypeError(
                    f"Policy for {subject.id} has no record method"
                )
            record(subject, action)

        return action, probability

    def _decision_row(
        self,
        subject: Subject,
        action: Action,
        result: str,
        details: dict[str, Any],
        probability: float | None,
        delta: dict[str, Any],
    ) -> dict[str, Any]:
        policy_meta = action.meta.get("policy")
        classification = action.meta.get("classification")
        row = {
            "kind": "decision",
            "turn": self.turn,
            "day": self.day,
            "slot": self.slot,
            "subject": subject.id,
            "verb": action.verb,
            "args": _plain(action.args),
            "result": result,
            "choice_prob": probability,
            "policy": _plain(policy_meta) if policy_meta is not None else None,
            "classification": (
                _plain(classification)
                if classification is not None
                else None
            ),
            "effective": delta_effective(delta),
            "delta": delta,
            "details": _plain(details),
        }

        if self.record_explanations:
            row["explanation"] = _plain(self._decision_context)
        return row

    def _strength_details(self, subject: Subject) -> dict[str, float]:
        if subject.id != self.world.protagonist:
            return {}
        antagonist = self.subjects[self.world.antagonist]
        if (
            antagonist.vitality == "dead"
            or antagonist.zone != subject.zone
        ):
            return {}
        present = self._present_for(subject)
        protagonist_strength = strength(subject, self.world, present)
        antagonist_strength = strength(antagonist, self.world, present)
        believed = believed_strength(
            subject,
            antagonist,
            self.world,
            present,
        )
        return {
            "strength_diff": round(
                protagonist_strength - antagonist_strength,
                6,
            ),
            "believed_diff": round(
                protagonist_strength - believed,
                6,
            ),
        }

    def _write_aborted(self, writer: LayersWriter) -> None:
        details: dict[str, Any] = {
            "reason": "protagonist_dead"
        }
        if phase2_configured(self.world):
            details["dangling_effects"] = (
                dangling_effect_count(self.world)
            )
        writer.write(
            self._event_row(
                verb="aborted",
                subject=self.world.protagonist,
                delta={},
                details=details,
                event_id="aborted",
            )
        )

    def _tick_vitality(self, writer: LayersWriter) -> None:
        for subject_id in sorted(self.subjects):
            subject = self.subjects[subject_id]
            if subject.vitality != "downed":
                continue
            before = self._capture()
            markers = tick(
                subject,
                self.world,
                self.turn,
                self._present_for(subject),
            )
            after = self._capture()
            if not markers:
                continue
            delta = self._delta(subject.id, before, after)
            for index, marker in enumerate(markers):
                writer.write(
                    self._event_row(
                        verb=str(marker["verb"]),
                        subject=marker.get("subject"),
                        delta=delta if index == 0 else {},
                        details=dict(marker.get("details", {})),
                    )
                )

    def _recover_stamina(self) -> None:
        for subject_id in sorted(self.subjects):
            subject = self.subjects[subject_id]
            if subject.vitality == "dead":
                continue
            subject.change_stamina(subject.stamina_recover)
            threshold = (
                subject.stamina_max
                * self.world.stamina["exhausted_ratio"]
            )
            if subject.exhausted and subject.stamina >= threshold:
                subject.exhausted = False

    def _evaluate_thresholds(self, writer: LayersWriter) -> None:
        for subject_id in sorted(self.subjects):
            subject = self.subjects[subject_id]
            if subject.vitality == "dead":
                continue
            present = self._present_for(subject)
            crossed = self.world.crossed_thresholds(
                subject,
                present,
                turn=self.turn,
                day=self.day,
            )
            for threshold_id in crossed:
                before = self._capture()
                subject.phase.add(threshold_id)
                after = self._capture()
                writer.write(
                    self._event_row(
                        verb="threshold_crossed",
                        subject=subject.id,
                        delta=self._delta(subject.id, before, after),
                        details={
                            "threshold": threshold_id,
                            "zone": subject.zone,
                        },
                    )
                )

    def _resolve_auto_effects(
        self,
        writer: LayersWriter,
    ) -> None:
        for pending in ready_auto_effects(
            self.world,
            turn=self.turn,
            day=self.day,
        ):
            before = self._capture()
            details = apply_effect(
                self.world,
                pending,
                turn=self.turn,
            )
            after = self._capture()
            writer.write(
                self._event_row(
                    verb="payoff",
                    subject=str(pending["planted_by"]),
                    delta=self._delta(
                        str(pending["planted_by"]),
                        before,
                        after,
                    ),
                    details=details,
                )
            )

    def _ending(self) -> dict[str, Any] | None:
        protagonist = self.subjects[self.world.protagonist]
        present = self._present_for(protagonist)
        configured = self.world.target_ending
        target_ids = (
            {configured}
            if isinstance(configured, str)
            else set(configured)
        )

        for ending in self.world.endings:
            if str(ending["id"]) not in target_ids:
                continue
            if self.world.ending_reached(
                ending,
                protagonist,
                present,
                turn=self.turn,
                day=self.day,
            ):
                return ending
        return None

    def _write_ending(
        self,
        writer: LayersWriter,
        ending: dict[str, Any],
    ) -> None:
        before = self._capture()
        event_subject = self.world.protagonist
        delivered: dict[str, str] = {}

        delivery = ending.get("deliver")
        if isinstance(delivery, dict):
            subject_id = str(delivery["subject"])
            item = str(delivery["item"])
            zone = str(delivery["zone"])
            delivery_subject = self.subjects[subject_id]
            event_subject = subject_id
            if (
                delivery_subject.has_item(item)
                and delivery_subject.zone == zone
            ):
                self.world.delivered[item] = zone
                delivered[item] = zone

        after = self._capture()
        details: dict[str, Any] = {
            "label": ending.get("label"),
            "delivered": delivered,
        }
        if self.world.effect_library:
            details["dangling_effects"] = (
                dangling_effect_count(self.world)
            )

        writer.write(
            self._event_row(
                verb="ending",
                subject=event_subject,
                delta=self._delta(
                    event_subject,
                    before,
                    after,
                ),
                details=details,
                event_id=str(ending["id"]),
            )
        )

    def _write_time_limit(
        self,
        writer: LayersWriter,
    ) -> None:
        writer.write(
            self._event_row(
                verb="ending",
                subject=self.world.protagonist,
                delta={},
                details={
                    "label": "time_limit",
                    "delivered": {},
                    "dangling_effects": (
                        dangling_effect_count(self.world)
                    ),
                },
                result="expired",
                event_id="time_limit",
            )
        )

    def _objective_vector_value(self, subject: Subject) -> float:
        if subject.goal.target is None:
            return 0.5
        holder = self.world.holder(subject.goal.target)
        if holder is None:
            return 0.5
        if holder == subject.id:
            return 1.0
        if holder not in self.subjects:
            return 0.33
        if (
            self.world.relations.stance(subject.id, holder)
            >= self.world.companionship["threshold"]
        ):
            return 0.66

        perceived_holder = self.world.perceived_name(
            subject.id,
            holder,
        )
        if (
            self.world.relations.stance(subject.id, holder) < -0.2
            or perceived_holder in subject.goal.obstacles
        ):
            return 0.0
        return 0.33

    def _layer_vector(
        self,
        subject: Subject,
        layers: dict[str, Any],
    ) -> list[float]:
        modifiers = layers["ability"]["modifiers"]
        modifier_total = sum(
            float(modifier["value"])
            for modifier in modifiers
            if modifier["active"]
        )
        belief_changed = (
            0.0
            if self._previous_snapshot_layers is None
            or self._previous_snapshot_layers.get("belief")
            == layers.get("belief")
            else 1.0
        )
        vitality_value = {
            "alive": 3.0,
            "revived": 2.0,
            "downed": 1.0,
            "dead": 0.0,
        }[subject.vitality]
        values = [
            subject.base / 100.0,
            modifier_total / 100.0,
            self.world.relations.bonds(subject.id)
            / (3.0 * max(1, len(self.subjects))),
            (
                self.world.relations.stance(
                    subject.id,
                    self.world.antagonist,
                )
                + 1.0
            )
            / 2.0,
            sum(
                count
                for count in subject.inventory.values()
                if count > 0
            )
            / 10.0,
            subject.reputation,
            len(subject.phase) / max(1, len(self.world.thresholds)),
            belief_changed,
            float(subject.identity_displayed != subject.id),  # disguise active (same notion as phase2/ctx; Claude-side fix, D6 review A-1)
            self._objective_vector_value(subject),
            vitality_value / 3.0,
        ]
        return [round(_clip(float(value)), 6) for value in values]

    def _write_snapshot(self, writer: LayersWriter) -> None:
        subject = self.subjects[self.world.protagonist]
        layers = subject.layer_snapshot(
            self.world,
            self._present_for(subject),
        )
        vector = self._layer_vector(subject, layers)
        writer.write(
            {
                "kind": "snapshot",
                "turn": self.turn,
                "day": self.day,
                "subject": subject.id,
                "vector": vector,
                "layers": layers,
                "relations": self.world.relations.flat_rows(),
            }
        )
        self._previous_snapshot_layers = layers

    def run(self) -> Path:
        path = self.out_dir / "layers.jsonl"
        with LayersWriter(path) as writer:
            writer.write(self._header())

            for day in range(1, self.world.days + 1):
                self.day = day
                self.turn += 1
                self.slot = None
                self._apply_scheduled(writer, slot=None)
                self._apply_daily(writer)

                for slot_index, slot in enumerate(self.world.slots):
                    self.slot = slot
                    if slot_index > 0:
                        self.turn += 1
                    self._apply_scheduled(writer, slot=slot)
                    self._record_encounters(writer)

                    for subject_id in self._action_order():
                        subject = self.subjects[subject_id]
                        if subject.vitality == "dead":
                            continue

                        action, probability = self.choose_action(
                            subject
                        )
                        capture_ids = self._action_capture_ids(
                            subject,
                            action,
                        )
                        before = self._capture(capture_ids)
                        if self.record_explanations and self._decision_context is not None:
                            # Reuse the execution snapshot, including unlogged passive recovery.
                            # Keep cost accounting separate from the actor's knowledge.
                            actor_before = before[0][subject.id]
                            self._decision_context["cost_baseline"] = {
                                "version": 1, "timing": "before_execute",
                                "stamina": actor_before["stamina"],
                                "resources": _plain(actor_before["resources"]),
                            }
                        result, details, markers = (
                            self.verb_engine.execute(
                                subject,
                                action,
                                turn=self.turn,
                                day=self.day,
                            )
                        )
                        details.update(
                            self._strength_details(subject)
                        )
                        after = self._capture(capture_ids)

                        if (
                            before[2] != after[2]
                            and capture_ids != set(self.subjects)
                        ):
                            after_all = self._capture()
                            before_layers = dict(before[0])
                            missing = (
                                set(self.subjects)
                                - set(before_layers)
                            )
                            for missing_id in sorted(missing):
                                reconstructed = dict(
                                    after_all[0][missing_id]
                                )
                                reconstructed["objective"] = dict(
                                    before[2]
                                )
                                before_layers[missing_id] = (
                                    reconstructed
                                )
                            before = (
                                before_layers,
                                before[1],
                                before[2],
                            )
                            after = after_all

                        delta = self._delta(
                            subject.id,
                            before,
                            after,
                        )
                        writer.write(
                            self._decision_row(
                                subject,
                                action,
                                result,
                                details,
                                probability,
                                delta,
                            )
                        )
                        self._write_markers(writer, markers)

                        if (
                            self.subjects[
                                self.world.protagonist
                            ].vitality
                            == "dead"
                        ):
                            self._write_aborted(writer)
                            return path

                    self._tick_vitality(writer)
                    self._recover_stamina()
                    self._evaluate_thresholds(writer)
                    self._resolve_auto_effects(writer)

                    ending = self._ending()
                    if ending is not None:
                        self._write_ending(writer, ending)
                        return path

                self._write_snapshot(writer)

            if phase2_configured(self.world):
                self._write_time_limit(writer)

        return path
