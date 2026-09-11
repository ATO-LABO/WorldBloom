"""Deterministic four-multiplier policy."""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence, TYPE_CHECKING

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
    ) -> None:
        self.genome = genome
        self.precedent = precedent
        self.rules = tuple(dict(rule) for rule in rules)
        self.lam = min(1.0, max(0.0, float(lam)))
        self.eps = max(0.0, float(eps))
        self.self_table = self_table or PrecedentTable()
        self.cfg = dict(cfg or {"nodes": [], "edges": []})
        self.annotate_only = bool(annotate_only)

    @property
    def precedent_hash(self) -> str | None:
        return self.precedent.hash if self.precedent is not None else None

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
        context: ContextKey,
        action: ActionKey,
        candidate_acts: set[ActionKey],
        self_table: PrecedentTable,
    ) -> float:
        p_self = self_table.p(context, action, candidate_acts)
        if self.precedent is None:
            p_ref = p_self
        else:
            p_ref = self.precedent.p(context, action, candidate_acts)
        return self.lam * p_ref + (1.0 - self.lam) * p_self

    def reweight(
        self,
        subject: Subject,
        world: World,
        present: list[Subject],
        weighted: list[tuple[Action, float]],
    ) -> list[tuple[Action, float]]:
        context = ctx_key(subject, world, present)
        classified: list[tuple[Action, float, Classification]] = [
            (
                action,
                weight,
                classify(action, subject, world, present, self.cfg),
            )
            for action, weight in weighted
        ]
        candidate_acts = {
            act_key(classification, action)
            for action, _, classification in classified
        }
        self_history = self._history_table(subject)
        annotation_only = self.annotate_only or self.genome.is_neutral()

        active = self._active_categories()
        category_mean = sum(
            self.genome.category_weight[category]
            for category in active
        ) / len(active)

        output: list[tuple[Action, float]] = []
        for action, weight, classification in classified:
            action_key = act_key(classification, action)
            p_prec = self._precedent_probability(
                context,
                action_key,
                candidate_acts,
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
                        self.genome.category_weight[classification.category]
                        / category_mean
                    )

                if classification.risk_class == "risky":
                    m_risk = self.genome.risk_tolerance / 0.5
                elif classification.risk_class == "safe_under_threat":
                    m_risk = (1.0 - self.genome.risk_tolerance) / 0.5
                else:
                    m_risk = 1.0

                m_stance = (
                    1.0
                    + self.genome.stance_shift_bias
                    * classification.stance_sign
                )
                m_nov = (
                    1.0 - p_prec + self.eps
                ) ** self.genome.novelty_drive

            action.meta["classification"] = classification.to_dict()
            action.meta["policy"] = {
                "ctx": [
                    list(context[0]),
                    context[1],
                    context[2],
                    context[3],
                    context[4],
                ],
                "effective_genome": self.genome.to_dict(),
                "m_cat": round(m_cat, 12),
                "m_nov": round(m_nov, 12),
                "m_risk": round(m_risk, 12),
                "m_stance": round(m_stance, 12),
                "p_prec": round(p_prec, 12),
            }

            if not annotation_only:
                output.append(
                    (
                        action,
                        weight * m_cat * m_risk * m_stance * m_nov,
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
