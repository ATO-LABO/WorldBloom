"""Deterministic four-multiplier policy."""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence, TYPE_CHECKING

from engine.log import delta_effective
from engine.predicate import compile_predicate_syntax

from gapengine.classify import Classification, classify
from gapengine.genome import CATEGORIES, Genome
from gapengine.precedent import (
    ActionKey,
    ContextKey,
    PrecedentTable,
    act_key,
    ctx_key,
    normalize_act,
    normalize_ctx,
)

if TYPE_CHECKING:
    from engine.actions import Action
    from engine.subject import Subject
    from engine.world import World


_RULE_ADJUST_KEYS = frozenset(
    {
        "risk_tolerance",
        "stance_shift_bias",
        "novelty_drive",
    }
    | {
        f"category_weight.{category}"
        for category in CATEGORIES
    }
)


def _compile_rules(
    rules: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    compiled: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for index, source in enumerate(rules):
        if not isinstance(source, Mapping):
            raise ValueError(
                f"Policy rule at index {index} must be a mapping"
            )

        rule = dict(source)
        rule_id = rule.get("id")
        if not isinstance(rule_id, str) or not rule_id:
            raise ValueError(
                f"Policy rule id is required at index {index}"
            )
        if rule_id in seen_ids:
            raise ValueError(f"Policy rule ids must be unique: {rule_id}")
        seen_ids.add(rule_id)

        scope = rule.get("scope")
        if scope not in {"turn", "candidate", "outcome"}:
            raise ValueError(
                f"Unknown policy rule scope: {rule_id}:{scope}"
            )

        when = rule.get("when")
        if not isinstance(when, str) or not when.strip():
            raise ValueError(
                f"Policy rule predicate is required: {rule_id}"
            )

        raw_adjust = rule.get("adjust", {})
        if not isinstance(raw_adjust, Mapping):
            raise ValueError(
                f"Policy rule adjust must be a mapping: {rule_id}"
            )

        adjust: dict[str, float] = {}
        for key, value in sorted(
            raw_adjust.items(),
            key=lambda pair: str(pair[0]),
        ):
            name = str(key)
            if name not in _RULE_ADJUST_KEYS:
                raise ValueError(
                    f"Unknown policy rule adjustment: {rule_id}:{name}"
                )
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
            ):
                raise ValueError(
                    f"Policy rule adjustment must be numeric: "
                    f"{rule_id}:{name}"
                )
            adjust[name] = float(value)

        compiled.append(
            {
                **rule,
                "id": rule_id,
                "scope": scope,
                "adjust": adjust,
                "predicate": compile_predicate_syntax(when),
            }
        )

    return tuple(compiled)


def _adjust_genome(
    genome: Genome,
    adjustments: Sequence[Mapping[str, float]],
) -> Genome:
    category_weight = dict(genome.category_weight)
    risk_tolerance = genome.risk_tolerance
    stance_shift_bias = genome.stance_shift_bias
    novelty_drive = genome.novelty_drive

    for adjust in adjustments:
        for key, value in sorted(adjust.items()):
            if key.startswith("category_weight."):
                category = key.removeprefix("category_weight.")
                category_weight[category] += float(value)
            elif key == "risk_tolerance":
                risk_tolerance += float(value)
            elif key == "stance_shift_bias":
                stance_shift_bias += float(value)
            elif key == "novelty_drive":
                novelty_drive += float(value)

    return Genome(
        category_weight=category_weight,
        risk_tolerance=risk_tolerance,
        stance_shift_bias=stance_shift_bias,
        novelty_drive=novelty_drive,
        rule_bits=dict(genome.rule_bits),
        plasticity=genome.plasticity,
    ).clip()


def _candidate_target(
    action: Action,
    subject: Subject,
    world: World,
) -> str:
    target = action.meta.get("target")
    if isinstance(target, str) and target in world.subjects:
        return target

    if (
        action.args
        and isinstance(action.args[0], str)
        and action.args[0] in world.subjects
    ):
        return action.args[0]

    return subject.id


class Policy:
    def __init__(
        self,
        genome: Genome,
        precedent: PrecedentTable | None,
        rules: Sequence[Mapping[str, Any]] = (),
        *,
        lam: float = 0.7,
        eps: float = 0.02,
        self_table: PrecedentTable | None = None,
        cfg: Mapping[str, Any] | None = None,
        annotate_only: bool = False,
        rationality: Any = None,
        route: Any = None,
    ) -> None:
        self.genome = genome
        self.precedent = precedent
        _enabled_rules = tuple(
            rule
            for rule in _compile_rules(rules)
            if genome.rule_bits.get(str(rule["id"]), True)
        )
        # WB-GROWTH-001 S0: outcome-scope rules are kept out of self.rules
        # entirely so the existing turn/candidate reweight loop (and the
        # neutral-genome/annotation_only check) never sees them -- they are
        # evaluated only by observe(), below.
        self.rules = tuple(
            rule for rule in _enabled_rules if rule["scope"] != "outcome"
        )
        self.outcome_rules = tuple(
            rule for rule in _enabled_rules if rule["scope"] == "outcome"
        )
        # WB-GROWTH-001 S0: cumulative post-birth shift per adjustable gene
        # key, written by observe() below. Never persisted/inherited -- a
        # fresh Policy (one per seed) always starts at {}.
        self.acquired: dict[str, float] = {}
        self.lam = min(1.0, max(0.0, float(lam)))
        self.eps = max(0.0, float(eps))
        self.self_table = self_table or PrecedentTable()
        self.cfg = dict(cfg or {"nodes": [], "edges": []})
        self.annotate_only = bool(annotate_only)
        # WB-JEV-001 Stage 2: an optional gapengine.rationality.Rationality
        # (duck-typed via .enabled/.multipliers -- no import here, to keep
        # policy.py free of a dependency on the rationality module). Only the
        # protagonist's Policy ever gets one; antagonists are out of scope.
        self.rationality = rationality
        # WB-ROUTE-001 S0: an optional gapengine.route.Route (duck-typed via
        # .annotate -- no import here, same reasoning as .rationality above).
        # Measurement only: it never changes a weight, only adds
        # action.meta["policy"]["route"] below.
        self.route = route

    @property
    def precedent_hash(self) -> str | None:
        return self.precedent.hash if self.precedent is not None else None

    def current_genome(self) -> Genome:
        """WB-GROWTH-001 S1: ``clip(genome + acquired)`` -- the personality
        reweight() actually steers by. Acquired shifts accumulate for the
        run's lifetime with no decay (observe() below never subtracts
        anything on its own, only what a matching outcome rule adds), and
        clipping to each scalar's valid range happens only here, never on
        ``self.acquired`` itself. Acquired shifts are never inherited
        (self.acquired resets to {} for every fresh Policy); this only ever
        reflects what *this run* has experienced so far. Returns self.genome
        unchanged (no new Genome allocated) when nothing has shifted yet, so
        plasticity=0 (or a run with no outcome rules at all) stays exactly
        as before this feature existed."""

        if not any(self.acquired.values()):
            return self.genome
        return _adjust_genome(self.genome, [self.acquired])

    def _active_categories(self) -> tuple[str, ...]:
        configured = {
            str(node["category"])
            for node in self.cfg.get("nodes", []) or []
            if node.get("category") is not None
        }
        active = tuple(
            category for category in CATEGORIES if category in configured
        )
        return active or CATEGORIES

    def _history_table(self, subject: Subject) -> PrecedentTable:
        table = PrecedentTable(self.self_table.counts)
        for key, count in sorted(
            subject.decision_history.items(),
            key=lambda pair: repr(pair[0]),
        ):
            if not isinstance(key, tuple) or len(key) != 2:
                continue
            try:
                context = normalize_ctx(key[0])
                action = normalize_act(key[1])
            except (TypeError, ValueError):
                continue
            table.add(context, action, float(count))
        return table

    def _precedent_probability(
        self,
        normalized_context: ContextKey,
        normalized_action: ActionKey,
        normalized_candidates: frozenset[ActionKey],
        self_table: PrecedentTable,
    ) -> float:
        p_self = self_table.p_normalized(
            normalized_context, normalized_action, normalized_candidates
        )
        if self.precedent is None:
            p_ref = p_self
        else:
            p_ref = self.precedent.p_normalized(
                normalized_context, normalized_action, normalized_candidates
            )
        return self.lam * p_ref + (1.0 - self.lam) * p_self

    def reweight(
        self,
        subject: Subject,
        world: World,
        present: list[Subject],
        weighted: list[tuple[Action, float]],
        *,
        turn: int = 0,
        day: int = 0,
    ) -> list[tuple[Action, float]]:
        context = ctx_key(subject, world, present)
        normalized_context = normalize_ctx(context)
        classified: list[tuple[Action, float, Classification]] = [
            (
                action,
                weight,
                classify(action, subject, world, present, self.cfg),
            )
            for action, weight in weighted
        ]

        # WB-JEV-001 Stage 2 (Opus review P2): a genuinely neutral, rule-free
        # genome is annotation-only *unless* rationality is enabled --
        # rationality is a constraint on plausibility, not a personality
        # trait, so it must still steer even a personality-less genome. An
        # explicit annotate_only=True (e.g. the probe's read-only recording
        # policy) always wins and, per the multipliers() gate just below,
        # never spends a judge call either.
        # WB-ROUTE-001 S1 §2: route is treated the same way -- it is a
        # constraint on the plan, not a personality trait, so at rho>0 it
        # must still steer even a personality-less genome.
        # WB-GROWTH-001 S1: current_genome() (genome + acquired) decides
        # neutrality here, not the birth genome -- an acquired shift from
        # outcome rules must end annotation-only steering exactly like a
        # turn/candidate rule would, even if the birth genome was neutral.
        base_genome = self.current_genome()
        annotation_only = self.annotate_only or (
            base_genome.is_neutral()
            and not self.rules
            and not (self.rationality is not None and self.rationality.enabled)
            and not (self.route is not None and self.route.enabled)
        )

        if (
            self.rationality is not None
            and self.rationality.enabled
            and not annotation_only
        ):
            m_rats, p_rats = self.rationality.multipliers(
                subject,
                world,
                present,
                [action for action, _, _ in classified],
            )
        else:
            m_rats = [1.0] * len(classified)
            p_rats = [None] * len(classified)

        turn_namespace = world.namespace(
            subject,
            present,
            turn=int(turn),
            day=int(day),
        )
        turn_adjustments = [
            rule["adjust"]
            for rule in self.rules
            if rule["scope"] == "turn"
            and rule["predicate"].evaluate(turn_namespace)
        ]
        turn_genome = _adjust_genome(
            base_genome,
            turn_adjustments,
        )
        active = self._active_categories()
        category_mean = sum(
            turn_genome.category_weight[category]
            for category in active
        ) / len(active)
        candidate_rules = [
            rule for rule in self.rules if rule["scope"] == "candidate"
        ]

        # WB-ROUTE-001 S2 §2: route's motive matching needs the same
        # candidate-target binding and turn+candidate-rule-adjusted genome
        # a candidate-scope policy rule's own `when` uses -- computed once
        # here (not per motive) and reused below for m_cat/m_risk/etc, so
        # the two never disagree about "this candidate's effective genome".
        actions_only = [action for action, _, _ in classified]
        targets = [
            _candidate_target(action, subject, world) for action in actions_only
        ]
        effective_genomes: list[Genome] = []
        for action, target in zip(actions_only, targets):
            if candidate_rules:
                candidate_namespace = world.namespace(
                    subject,
                    present,
                    turn=int(turn),
                    day=int(day),
                    bindings={"target": target},
                )
                candidate_adjustments = [
                    rule["adjust"]
                    for rule in candidate_rules
                    if rule["predicate"].evaluate(candidate_namespace)
                ]
            else:
                candidate_adjustments = []
            effective_genomes.append(
                turn_genome
                if not candidate_adjustments
                else _adjust_genome(
                    base_genome,
                    [*turn_adjustments, *candidate_adjustments],
                )
            )

        # WB-ROUTE-001 S0/S1/S2: annotate every candidate (including
        # annotation-only/neutral-genome decisions) before any weighting.
        # At rho<=0 this only ever *records* (m_routes below stays all
        # 1.0s -- see Route.multiplier); at rho>0 it also modulates weight.
        # S2 §4's gene_affinity route selection is a once-per-decision
        # choice (not per candidate), so it gets the turn-scope genome, not
        # any one candidate's target-adjusted one.
        route_annotations = (
            self.route.annotate(
                subject,
                world,
                present,
                actions_only,
                turn=int(turn),
                day=int(day),
                genome=turn_genome,
                category_mean=category_mean,
                targets=targets,
                candidate_genomes=effective_genomes,
            )
            if self.route is not None
            else None
        )
        if self.route is not None and self.route.enabled:
            m_routes = [
                self.route.multiplier(
                    ann["kind"], ann.get("cause"), ann.get("gene_s")
                )
                for ann in route_annotations
            ]
        else:
            m_routes = [1.0] * len(classified)

        candidate_acts = {
            act_key(classification, action)
            for action, _, classification in classified
        }
        normalized_candidates = frozenset(
            normalize_act(value) for value in candidate_acts
        )
        self_history = self._history_table(subject)

        output: list[tuple[Action, float]] = []
        for index, (action, weight, classification) in enumerate(classified):
            m_rat = m_rats[index]
            p_rat = p_rats[index]
            m_route = m_routes[index]
            effective_genome = effective_genomes[index]

            action_key = act_key(classification, action)
            normalized_action = normalize_act(action_key)
            p_prec = self._precedent_probability(
                normalized_context,
                normalized_action,
                normalized_candidates,
                self_history,
            )

            if annotation_only:
                m_cat = 1.0
                m_risk = 1.0
                m_stance = 1.0
                m_nov = 1.0
            else:
                if classification.category is None:
                    m_cat = 1.0
                else:
                    m_cat = (
                        effective_genome.category_weight[
                            classification.category
                        ]
                        / category_mean
                    )

                if classification.risk_class == "risky":
                    m_risk = (
                        effective_genome.risk_tolerance / 0.5
                    )
                elif classification.risk_class == "safe_under_threat":
                    m_risk = (
                        1.0 - effective_genome.risk_tolerance
                    ) / 0.5
                else:
                    m_risk = 1.0

                m_stance = (
                    1.0
                    + effective_genome.stance_shift_bias
                    * classification.stance_sign
                )
                m_nov = (
                    1.0 - p_prec + self.eps
                ) ** effective_genome.novelty_drive

            action.meta["classification"] = classification.to_dict()
            action.meta["policy"] = {
                "ctx": [
                    list(context[0]),
                    *context[1:],
                ],
                "effective_genome": effective_genome.to_dict(),
                "m_cat": round(m_cat, 12),
                "m_nov": round(m_nov, 12),
                "m_risk": round(m_risk, 12),
                "m_stance": round(m_stance, 12),
                "p_prec": round(p_prec, 12),
            }
            # κ=0 (or no Rationality at all) must not add these keys: existing
            # fixed-hash/golden tests depend on the meta dict staying exactly
            # as it was before Stage 2 (WB-JEV-001 plan §1.4/§4).
            if self.rationality is not None and self.rationality.enabled:
                action.meta["policy"]["m_rat"] = round(m_rat, 12)
                action.meta["policy"]["p_rat"] = (
                    round(p_rat, 12) if p_rat is not None else None
                )
            if route_annotations is not None:
                action.meta["policy"]["route"] = route_annotations[index]
                # S1 §2: m_route is recorded only at rho>0 -- at rho<=0 it is
                # always exactly 1.0 (b**0), and the plan requires ρ=0 to add
                # no meta key at all (byte-identity with a route-free run).
                if self.route is not None and self.route.enabled:
                    action.meta["policy"]["m_route"] = round(m_route, 12)
            # WB-GROWTH-001 S1: only added when something has actually
            # shifted -- a run with plasticity=0 (or no outcome rules) never
            # gains this key, staying byte-identical to before S1.
            if any(self.acquired.values()):
                action.meta["policy"]["acquired"] = {
                    key: round(value, 12)
                    for key, value in sorted(self.acquired.items())
                }

            if not annotation_only:
                output.append(
                    (
                        action,
                        weight * m_route * m_rat * m_cat * m_risk * m_stance * m_nov,
                    )
                )

        if annotation_only:
            return weighted
        return output

    def record(
        self,
        subject: Subject,
        action: Action,
    ) -> None:
        classification = action.meta.get("classification")
        metadata = action.meta.get("policy")
        if not isinstance(classification, Mapping):
            return
        if not isinstance(metadata, Mapping) or "ctx" not in metadata:
            return

        context = normalize_ctx(metadata["ctx"])
        action_key: ActionKey = (
            str(classification.get("category") or "-"),
            action.verb,
            str(classification.get("target_role", "none")),
        )
        subject.decision_history[(context, action_key)] += 1

    def _outcome_target(
        self,
        world: World,
        args: Sequence[Any],
        details: Mapping[str, Any],
    ) -> str | None:
        for candidate in (
            args[0] if args else None,
            details.get("target"),
            details.get("ally"),
        ):
            if isinstance(candidate, str) and candidate in world.subjects:
                return candidate
        return None

    def observe(
        self,
        subject: Subject,
        world: World,
        present: list[Subject],
        row: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        """WB-GROWTH-001: react to one just-written decision/event row with
        this subject's outcome-scope rules, accumulating any shift into
        ``self.acquired`` and returning a "growth" marker per matched rule
        (or [] when nothing matched or nothing actually shifted).

        S1: the shift is genome.plasticity x adjust, and reweight() reads
        self.acquired back via current_genome() -- so plasticity=0 (the
        default) makes every shift exactly 0 and this stays record-only,
        same as S0."""

        # Review fix (must #4): plasticity=0 (the default) always yields an
        # empty shift regardless of what follows, so skip the binding/
        # namespace/rule-evaluation work entirely instead of doing it and
        # discarding the (always-zero) result -- output is unchanged.
        if not self.outcome_rules or not self.genome.plasticity:
            return []
        verb = row.get("verb")
        if not isinstance(verb, str) or verb == "growth":
            return []

        details = row.get("details") or {}
        args = row.get("args") or []
        row_subject = row.get("subject")
        actor_is_self = row_subject == subject.id
        # Review fix (should #2): "" (never a real subject id), not
        # subject.id, when no target can be resolved -- an untargeted row
        # (e.g. "rest") must not make involves_self true for every subject.
        target = self._outcome_target(world, args, details) or ""

        delta = row.get("delta") or {}
        delta_targets = delta.get("targets") or {}
        involves_self = (
            actor_is_self
            or target == subject.id
            or subject.id in delta_targets
        )

        classification = row.get("classification") or {}
        # Review fix (should #3): renamed from "stress_delta" -- despite the
        # name, engine.log.nested_diff's leaf convention means this was
        # already the stress value *after* the change, not a difference.
        # None (not 0.0) when there is nothing to report -- always the case
        # on a marker/event row, since those always log delta={}.
        #
        # Review fix (nice #6): a rule's "when" that reads this binding must
        # guard it first (e.g. "stress_after is not None and stress_after
        # > 0.5") -- comparing None with a number raises TypeError, which
        # this method (like an unknown binding name) does not catch, so it
        # propagates straight out of observe().
        if row.get("kind") == "decision":
            if actor_is_self:
                raw_stress = (delta.get("actor") or {}).get("stress")
            else:
                raw_stress = (delta_targets.get(subject.id) or {}).get(
                    "stress"
                )
        else:
            raw_stress = None
        stress_after = float(raw_stress) if raw_stress is not None else None

        effective = row.get("effective")
        if effective is None:
            effective = delta_effective(delta)

        bindings = {
            "kind": row.get("kind"),
            "verb": verb,
            "result": row.get("result"),
            "actor_is_self": actor_is_self,
            "target": target,
            "category": classification.get("category"),
            "risk_class": classification.get("risk_class"),
            "stance_sign": classification.get("stance_sign"),
            "target_role": classification.get("target_role"),
            "effective": bool(effective),
            "stress_after": stress_after,
            "involves_self": involves_self,
        }
        namespace = world.namespace(
            subject,
            present,
            turn=int(row.get("turn", 0)),
            day=int(row.get("day", 0)),
            bindings=bindings,
        )

        plasticity = self.genome.plasticity
        markers: list[dict[str, Any]] = []
        for rule in self.outcome_rules:
            if not rule["predicate"].evaluate(namespace):
                continue
            shift = {
                key: plasticity * value
                for key, value in rule["adjust"].items()
            }
            if not any(shift.values()):
                continue
            for key, value in shift.items():
                # nice #10: no decay -- shifts accumulate for the run's
                # lifetime -- but a key that nets back to exactly 0 (e.g. a
                # win cancelling a prior loss) is dropped rather than kept
                # as a stray 0.0 entry. clip() only ever happens on the
                # combined current_genome(), never here.
                updated = self.acquired.get(key, 0.0) + value
                if updated:
                    self.acquired[key] = updated
                else:
                    self.acquired.pop(key, None)
            markers.append(
                {
                    "verb": "growth",
                    "subject": subject.id,
                    "details": {
                        "rule": rule["id"],
                        "description": rule.get("description", ""),
                        "trigger": {
                            "kind": row.get("kind"),
                            "verb": verb,
                            "result": row.get("result"),
                            "turn": row.get("turn"),
                        },
                        "shift": dict(sorted(shift.items())),
                        "plasticity": plasticity,
                        "acquired_after": dict(sorted(self.acquired.items())),
                    },
                }
            )
        return markers
