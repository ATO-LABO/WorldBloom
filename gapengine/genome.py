"""Deterministic nine-scalar policy genome."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


CATEGORIES = ("I", "II", "III", "IV", "V", "VI")
CATEGORY_MIN = 0.05
CATEGORY_MAX = 1.0


def _clip(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, float(value)))


@dataclass(frozen=True)
class Genome:
    category_weight: dict[str, float]
    risk_tolerance: float
    stance_shift_bias: float
    novelty_drive: float
    rule_bits: dict[str, bool] = field(default_factory=dict)

    @classmethod
    def neutral(
        cls,
        *,
        rule_ids: Iterable[str] = (),
    ) -> Genome:
        return cls(
            category_weight={category: 0.5 for category in CATEGORIES},
            risk_tolerance=0.5,
            stance_shift_bias=0.0,
            novelty_drive=0.0,
            rule_bits={
                rule_id: True
                for rule_id in sorted(set(map(str, rule_ids)))
            },
        )

    @classmethod
    def random(
        cls,
        rng: random.Random,
        *,
        rule_ids: Iterable[str] = (),
    ) -> Genome:
        return cls(
            category_weight={
                category: rng.uniform(CATEGORY_MIN, CATEGORY_MAX)
                for category in CATEGORIES
            },
            risk_tolerance=rng.uniform(0.0, 1.0),
            stance_shift_bias=rng.uniform(-1.0, 1.0),
            novelty_drive=rng.uniform(0.0, 1.0),
            rule_bits={
                rule_id: True
                for rule_id in sorted(set(map(str, rule_ids)))
            },
        )

    @classmethod
    def crossover(
        cls,
        first: Genome,
        second: Genome,
        rng: random.Random,
    ) -> Genome:
        rule_ids = sorted(
            set(first.rule_bits) | set(second.rule_bits)
        )
        return cls(
            category_weight={
                category: (
                    first.category_weight[category]
                    if rng.random() < 0.5
                    else second.category_weight[category]
                )
                for category in CATEGORIES
            },
            risk_tolerance=(
                first.risk_tolerance
                if rng.random() < 0.5
                else second.risk_tolerance
            ),
            stance_shift_bias=(
                first.stance_shift_bias
                if rng.random() < 0.5
                else second.stance_shift_bias
            ),
            novelty_drive=(
                first.novelty_drive
                if rng.random() < 0.5
                else second.novelty_drive
            ),
            rule_bits={
                rule_id: (
                    first.rule_bits.get(rule_id, True)
                    if rng.random() < 0.5
                    else second.rule_bits.get(rule_id, True)
                )
                for rule_id in rule_ids
            },
        )

    @classmethod
    def mutate(
        cls,
        genome: Genome,
        rng: random.Random,
        *,
        p: float = 0.3,
        sigma: Mapping[str, float] | None = None,
        rule_p: float = 0.1,
    ) -> Genome:
        deviations = {
            "cw": 0.1,
            "risk": 0.1,
            "bias": 0.2,
            "novelty": 0.1,
        }
        if sigma is not None:
            deviations.update(
                {str(key): float(value) for key, value in sigma.items()}
            )

        category_weight: dict[str, float] = {}
        for category in CATEGORIES:
            value = genome.category_weight[category]
            if rng.random() < p:
                value += rng.gauss(0.0, deviations["cw"])
            category_weight[category] = value

        risk = genome.risk_tolerance
        if rng.random() < p:
            risk += rng.gauss(0.0, deviations["risk"])

        bias = genome.stance_shift_bias
        if rng.random() < p:
            bias += rng.gauss(0.0, deviations["bias"])

        novelty = genome.novelty_drive
        if rng.random() < p:
            novelty += rng.gauss(0.0, deviations["novelty"])

        rule_bits: dict[str, bool] = {}
        for rule_id in sorted(genome.rule_bits):
            enabled = genome.rule_bits[rule_id]
            if rng.random() < rule_p:
                enabled = not enabled
            rule_bits[rule_id] = enabled

        return cls(
            category_weight=category_weight,
            risk_tolerance=risk,
            stance_shift_bias=bias,
            novelty_drive=novelty,
            rule_bits=rule_bits,
        ).clip()

    def clip(self) -> Genome:
        return Genome(
            category_weight={
                category: _clip(
                    self.category_weight.get(category, 0.5),
                    CATEGORY_MIN,
                    CATEGORY_MAX,
                )
                for category in CATEGORIES
            },
            risk_tolerance=_clip(self.risk_tolerance, 0.0, 1.0),
            stance_shift_bias=_clip(
                self.stance_shift_bias,
                -1.0,
                1.0,
            ),
            novelty_drive=_clip(self.novelty_drive, 0.0, 1.0),
            rule_bits=dict(sorted(self.rule_bits.items())),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "category_weight": {
                category: float(self.category_weight[category])
                for category in CATEGORIES
            },
            "novelty_drive": float(self.novelty_drive),
            "risk_tolerance": float(self.risk_tolerance),
            "stance_shift_bias": float(self.stance_shift_bias),
        }
        if self.rule_bits:
            result["rule_bits"] = dict(sorted(self.rule_bits.items()))
        return result

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Genome:
        weights = raw.get("category_weight")
        if not isinstance(weights, Mapping):
            raise ValueError("Genome.category_weight must be a mapping")
        missing = [
            category for category in CATEGORIES if category not in weights
        ]
        if missing:
            raise ValueError(f"Genome is missing categories: {missing}")

        raw_rule_bits = raw.get("rule_bits", {})
        if not isinstance(raw_rule_bits, Mapping):
            raise ValueError("Genome.rule_bits must be a mapping")
        if any(
            not isinstance(enabled, bool)
            for enabled in raw_rule_bits.values()
        ):
            raise ValueError(
                "Genome.rule_bits values must be booleans"
            )

        return cls(
            category_weight={
                category: float(weights[category])
                for category in CATEGORIES
            },
            risk_tolerance=float(raw["risk_tolerance"]),
            stance_shift_bias=float(raw["stance_shift_bias"]),
            novelty_drive=float(raw["novelty_drive"]),
            rule_bits={
                str(rule_id): enabled
                for rule_id, enabled in sorted(
                    raw_rule_bits.items(),
                    key=lambda pair: str(pair[0]),
                )
            },
        ).clip()

    def is_neutral(self, tolerance: float = 1e-9) -> bool:
        neutral = Genome.neutral()
        return (
            all(
                abs(
                    self.category_weight[category]
                    - neutral.category_weight[category]
                )
                <= tolerance
                for category in CATEGORIES
            )
            and abs(self.risk_tolerance - neutral.risk_tolerance)
            <= tolerance
            and abs(self.stance_shift_bias - neutral.stance_shift_bias)
            <= tolerance
            and abs(self.novelty_drive - neutral.novelty_drive)
            <= tolerance
            and all(self.rule_bits.values())
        )
