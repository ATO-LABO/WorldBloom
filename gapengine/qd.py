"""Quality-diversity descriptors, scoring, archive, and route diversity."""

from __future__ import annotations

import ast
import json
import statistics
from collections import Counter, deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, TYPE_CHECKING
from engine.predicate import Namespace, Predicate, conjuncts

from gapengine.genome import Genome

if TYPE_CHECKING:
    from engine.world import World


@dataclass(frozen=True)
class Descriptor:
    category: str | None
    volatility: float
    volatility_bin: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "volatility": float(self.volatility),
            "volatility_bin": self.volatility_bin,
        }


@dataclass(frozen=True)
class Elite:
    genome: Genome
    quality: float
    descriptor: Descriptor
    reach_rate: float
    exemplar: dict[str, Any]
    generation: int
    parents: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "descriptor": self.descriptor.to_dict(),
            "exemplar": {
                str(key): self.exemplar[key]
                for key in sorted(self.exemplar)
            },
            "generation": int(self.generation),
            "genome": self.genome.to_dict(),
            "parents": list(self.parents),
            "quality": float(self.quality),
            "reach_rate": float(self.reach_rate),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Elite:
        descriptor_raw = raw["descriptor"]
        return cls(
            genome=Genome.from_dict(raw["genome"]),
            quality=float(raw["quality"]),
            descriptor=Descriptor(
                category=(
                    str(descriptor_raw["category"])
                    if descriptor_raw.get("category") is not None
                    else None
                ),
                volatility=float(descriptor_raw["volatility"]),
                volatility_bin=(
                    str(descriptor_raw["volatility_bin"])
                    if descriptor_raw.get("volatility_bin") is not None
                    else None
                ),
            ),
            reach_rate=float(raw["reach_rate"]),
            exemplar=dict(raw["exemplar"]),
            generation=int(raw["generation"]),
            parents=tuple(str(value) for value in raw.get("parents", [])),
        )


def read_rows(path: str | Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _protagonist(rows: Sequence[Mapping[str, Any]]) -> str | None:
    for row in rows:
        if row.get("kind") == "header":
            value = row.get("protagonist")
            return str(value) if value is not None else None
    return None


def _l1(first: Sequence[float], second: Sequence[float]) -> float:
    return sum(
        abs(float(left) - float(right))
        for left, right in zip(first, second, strict=True)
    )


def _volatility_bin(
    value: float,
    thresholds: Mapping[str, Any] | Sequence[float] | None,
) -> str | None:
    if thresholds is None:
        return None
    if isinstance(thresholds, Mapping):
        low_max = float(thresholds["low_max"])
        mid_max = float(thresholds["mid_max"])
    else:
        if len(thresholds) != 2:
            raise ValueError("Volatility thresholds require two values")
        low_max = float(thresholds[0])
        mid_max = float(thresholds[1])
    if value <= low_max:
        return "low"
    if value <= mid_max:
        return "mid"
    return "high"


def descriptor(
    rows: Sequence[Mapping[str, Any]],
    cfg: Mapping[str, Any],
    *,
    subject: str | None = None,
) -> Descriptor:
    protagonist = _protagonist(rows)
    decision_subject = subject or protagonist
    categories = tuple(
        str(value)
        for value in cfg.get(
            "categories",
            ("I", "II", "III", "IV", "V", "VI"),
        )
    )
    if not categories:
        raise ValueError("QD categories must not be empty")

    counts: Counter[str] = Counter()
    for row in rows:
        if (
            row.get("kind") != "decision"
            or row.get("subject") != decision_subject
            or not row.get("effective", False)
        ):
            continue
        classification = row.get("classification")
        if not isinstance(classification, Mapping):
            continue
        category = classification.get("category")
        if category is not None and str(category) in categories:
            counts[str(category)] += 1

    leading: str | None
    if counts:
        leading = max(
            categories,
            key=lambda category: (
                counts[category],
                -categories.index(category),
            ),
        )
    else:
        leading = None

    vectors = [
        [float(value) for value in row["vector"]]
        for row in rows
        if row.get("kind") == "snapshot"
        and row.get("subject") == protagonist
    ]
    deltas = [
        _l1(previous, current)
        for previous, current in zip(vectors, vectors[1:])
    ]
    volatility = (
        statistics.pvariance(deltas)
        if len(deltas) >= 2
        else 0.0
    )
    volatility = round(float(volatility), 12)
    return Descriptor(
        category=leading,
        volatility=volatility,
        volatility_bin=_volatility_bin(
            volatility,
            cfg.get("thresholds"),
        ),
    )


def _sign_flips(values: Iterable[float]) -> int:
    prior: int | None = None
    flips = 0
    for value in values:
        current = 1 if value > 0.0 else -1 if value < 0.0 else 0
        if current == 0:
            continue
        if prior is not None and current != prior:
            flips += 1
        prior = current
    return flips


def _decision_target(row: Mapping[str, Any]) -> str:
    args = row.get("args", [])
    if isinstance(args, list) and args:
        return str(args[0])
    return "-"


def _repetition_penalty(
    decisions: Sequence[Mapping[str, Any]],
) -> float:
    penalty_count = 0
    run_key: tuple[str, str] | None = None
    run_length = 0
    for row in decisions:
        key = (str(row.get("verb")), _decision_target(row))
        if key == run_key:
            run_length += 1
        else:
            run_key = key
            run_length = 1
        if run_length == 3:
            penalty_count += 1
    return 0.05 * penalty_count


def _completed_prerequisite_chains(
    rows: Sequence[Mapping[str, Any]],
) -> int:
    protagonist = _protagonist(rows)
    if protagonist is None:
        return 0

    observed: set[tuple[str, str]] = set()
    completed_observations: set[tuple[str, str]] = set()
    pledged: set[frozenset[str]] = set()
    negotiated: set[tuple[str, str]] = set()
    completed_pledges: set[frozenset[str]] = set()
    completed_negotiations: set[tuple[str, str]] = set()
    completed = 0

    for row in rows:
        kind = row.get("kind")
        verb = str(row.get("verb", ""))
        subject = str(row.get("subject", ""))
        details = row.get("details")
        if not isinstance(details, Mapping):
            details = {}

        if kind == "decision":
            if row.get("result") == "invalid":
                continue
            target = _decision_target(row)

            if verb == "observe" and subject == protagonist:
                observed.add((subject, target))
            elif verb == "neutralize" and subject == protagonist:
                pair = (subject, target)
                if (
                    pair in observed
                    and pair not in completed_observations
                ):
                    completed += 1
                    completed_observations.add(pair)
            elif verb == "pledge" and subject == protagonist:
                pledged.add(frozenset((subject, target)))
            elif verb == "negotiate" and subject == protagonist:
                negotiated.add((subject, target))
            elif verb == "concede" and target == protagonist:
                pair = (target, subject)
                if (
                    pair in negotiated
                    and pair not in completed_negotiations
                ):
                    completed += 1
                    completed_negotiations.add(pair)

        elif (
            kind == "event"
            and verb == "betrayal"
            and subject == protagonist
        ):
            target = details.get("target")
            if not isinstance(target, str):
                continue
            pair = frozenset((subject, target))
            if pair in pledged and pair not in completed_pledges:
                completed += 1
                completed_pledges.add(pair)

    return completed


def _dangling_effects(
    rows: Sequence[Mapping[str, Any]],
) -> int:
    for row in reversed(rows):
        details = row.get("details")
        if isinstance(details, Mapping):
            value = details.get("dangling_effects")
            if value is not None:
                return max(0, int(value))

        value = row.get("dangling_effects")
        if value is not None:
            return max(0, int(value))

    return 0


def quality(
    rows: Sequence[Mapping[str, Any]],
    world_meta: Mapping[str, Any],
) -> float:
    protagonist = str(
        world_meta.get("protagonist") or _protagonist(rows) or ""
    )
    objective_changes = 0
    affinity_change = 0.0
    revived = 0
    learning = 0
    dramatic_turns = 0
    strength_diffs: list[float] = []

    protagonist_decisions = [
        row
        for row in rows
        if row.get("kind") == "decision"
        and row.get("subject") == protagonist
    ]

    for row in rows:
        delta = row.get("delta")
        if isinstance(delta, Mapping):
            objective = delta.get("objective")
            if isinstance(objective, Mapping):
                objective_changes += len(objective)
            relations = delta.get("relations")
            if isinstance(relations, list):
                for relation in relations:
                    if not isinstance(relation, Mapping):
                        continue
                    if protagonist in {
                        str(relation.get("observer")),
                        str(relation.get("target")),
                    }:
                        affinity_change += abs(
                            float(relation.get("affinity", 0.0))
                        )

        if (
            row.get("kind") == "event"
            and row.get("verb") == "revived"
            and row.get("subject") == protagonist
        ):
            revived += 1

        if (
            row.get("kind") == "event"
            and row.get("verb") in {"betrayal", "exposure"}
        ):
            dramatic_turns += 1

        if (
            row.get("kind") == "event"
            and row.get("verb") == "learn_fact"
            and row.get("subject") == protagonist
        ):
            learning += 1

        if (
            row.get("kind") == "decision"
            and row.get("verb") == "observe"
            and row.get("subject") == protagonist
        ):
            learning += 1

        details = row.get("details")
        if isinstance(details, Mapping):
            if (
                row.get("kind") == "event"
                and row.get("verb") == "payoff"
                and details.get("mode") == "chosen"
            ):
                dramatic_turns += 1
            if (
                row.get("kind") == "decision"
                and row.get("verb") == "concede"
                and details.get("mode") == "goodwill"
                and row.get("result") != "invalid"
            ):
                dramatic_turns += 1
            if (
                row.get("kind") == "event"
                and row.get("verb") == "rethink"
                and row.get("subject") == protagonist
            ):
                before = details.get("before")
                after = details.get("after")
                if isinstance(before, Mapping) and isinstance(after, Mapping):
                    for fact_id in sorted(set(before) | set(after)):
                        before_belief = before.get(fact_id)
                        after_belief = after.get(fact_id)
                        before_value = (
                            before_belief.get("value")
                            if isinstance(before_belief, Mapping)
                            else None
                        )
                        after_value = (
                            after_belief.get("value")
                            if isinstance(after_belief, Mapping)
                            else None
                        )
                        if before_value != after_value:
                            dramatic_turns += 1
            if "strength_diff" in details:
                strength_diffs.append(
                    float(details["strength_diff"])
                )

    completed_chains = _completed_prerequisite_chains(rows)
    metrics = (
        min(1.0, objective_changes / 2.0),
        min(1.0, affinity_change / 3.0),
        min(1.0, revived / 2.0),
        min(1.0, _sign_flips(strength_diffs) / 2.0),
        min(1.0, learning / 3.0),
        min(1.0, dramatic_turns / 2.0),
        min(1.0, completed_chains / 2.0),
    )
    score = sum(metrics) / len(metrics)

    score -= _repetition_penalty(protagonist_decisions)
    if protagonist_decisions:
        stalls = sum(
            row.get("verb") in {"rest", "withdraw"}
            for row in protagonist_decisions
        )
        score -= (stalls / len(protagonist_decisions)) * 0.3

    score -= 0.05 * _dangling_effects(rows)
    return round(min(1.0, max(0.0, score)), 12)


def antagonist_quality(
    rows: Sequence[Mapping[str, Any]],
    world_meta: Mapping[str, Any],
) -> float:
    target_ending = world_meta.get("target_ending")
    if target_ending is None or not reached(rows, target_ending):
        return 0.0

    turns = max(
        (
            int(row.get("turn", 0) or 0)
            for row in rows
        ),
        default=0,
    )
    max_turns = max(1, int(world_meta.get("max_turns", 1)))

    protagonist = str(
        world_meta.get("protagonist") or _protagonist(rows) or ""
    )
    vectors = [
        [float(value) for value in row["vector"]]
        for row in rows
        if row.get("kind") == "snapshot"
        and row.get("subject") == protagonist
    ]
    deltas = [
        _l1(previous, current)
        for previous, current in zip(vectors, vectors[1:])
    ]
    volatility = (
        statistics.pvariance(deltas)
        if len(deltas) >= 2
        else 0.0
    )
    volatility = float(volatility)

    vol_high = float(world_meta.get("vol_high", 1.0))
    if vol_high > 0.0:
        volatility_ratio = min(1.0, volatility / vol_high)
    else:
        volatility_ratio = float(volatility > 0.0)

    strength_diffs: list[float] = []
    for row in rows:
        details = row.get("details")
        if (
            isinstance(details, Mapping)
            and "strength_diff" in details
        ):
            strength_diffs.append(float(details["strength_diff"]))

    reversals = _sign_flips(strength_diffs)
    score = (
        0.4 * min(1.0, turns / max_turns)
        + 0.3 * volatility_ratio
        + 0.3 * min(1.0, reversals / 2.0)
    )
    return round(min(1.0, max(0.0, score)), 12)


def reached(
    rows: Sequence[Mapping[str, Any]],
    target_ending: str | Sequence[str],
) -> bool:
    target_ids = (
        {target_ending}
        if isinstance(target_ending, str)
        else {
            str(value)
            for value in target_ending
        }
    )
    if not target_ids:
        return False

    return any(
        row.get("kind") == "event"
        and row.get("verb") == "ending"
        and row.get("id") in target_ids
        for row in rows
    )


def _shortest_hops(
    world: World,
    origin: str,
    destination: str,
) -> int | None:
    if origin == destination:
        return 0
    queue: deque[tuple[str, int]] = deque([(origin, 0)])
    visited = {origin}
    while queue:
        zone, distance = queue.popleft()
        for route in sorted(
            world.routes.get(zone, ()),
            key=lambda value: value.destination,
        ):
            target = route.destination
            if target in visited:
                continue
            if target == destination:
                return distance + 1
            visited.add(target)
            queue.append((target, distance + 1))
    return None


def _maximum_hops(world: World) -> int:
    maximum = 1
    for origin in sorted(world.zones):
        for destination in sorted(world.zones):
            hops = _shortest_hops(world, origin, destination)
            if hops is not None:
                maximum = max(maximum, hops)
    return maximum


def _clamp_unit(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def _predicate_value(
    node: ast.expr,
    namespace: Namespace,
) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return namespace[node.id]
    raise ValueError(
        f"Unsupported shaped predicate value: {type(node).__name__}"
    )


def _call_arguments(
    node: ast.Call,
    namespace: Namespace,
) -> tuple[Any, ...]:
    return tuple(
        _predicate_value(argument, namespace)
        for argument in node.args
    )


def _zone_proximity(
    world: World,
    subject_id: str,
    destination: str,
) -> float:
    subject = world.subjects.get(subject_id)
    if subject is None or destination not in world.zones:
        return 0.0
    hops = _shortest_hops(
        world,
        subject.zone,
        destination,
    )
    if hops is None:
        return 0.0
    return _clamp_unit(
        1.0 - hops / _maximum_hops(world)
    )


def _stance_proximity(
    world: World,
    observer: str,
    target: str,
    threshold: float,
) -> float:
    if observer not in world.subjects or target not in world.subjects:
        return 0.0
    stance = world.relations.stance(observer, target)
    if stance >= threshold:
        return 1.0
    denominator = threshold + 1.0
    if denominator <= 0.0:
        return 0.0
    return _clamp_unit((stance + 1.0) / denominator)


def _conjunct_distance(
    predicate: Predicate,
    namespace: Namespace,
    world: World,
) -> float | None:
    if predicate.evaluate(namespace):
        return 1.0

    node = predicate.tree.body
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id in {
            "holds",
            "known",
            "knows_modifier",
            "confront_success",
        }:
            return 0.0
        return None

    if (
        not isinstance(node, ast.Compare)
        or len(node.ops) != 1
        or len(node.comparators) != 1
        or not isinstance(node.left, ast.Call)
        or not isinstance(node.left.func, ast.Name)
    ):
        return None

    function = node.left.func.id
    comparator = node.comparators[0]
    try:
        arguments = _call_arguments(node.left, namespace)
        expected = _predicate_value(comparator, namespace)
    except (KeyError, ValueError):
        return None

    if (
        function == "zone"
        and isinstance(node.ops[0], ast.Eq)
        and len(arguments) == 1
    ):
        return _zone_proximity(
            world,
            str(arguments[0]),
            str(expected),
        )

    if (
        function == "stance"
        and isinstance(node.ops[0], (ast.Gt, ast.GtE))
        and len(arguments) == 2
        and isinstance(expected, (int, float))
        and not isinstance(expected, bool)
    ):
        return _stance_proximity(
            world,
            str(arguments[0]),
            str(arguments[1]),
            float(expected),
        )

    if function in {
        "holds",
        "known",
        "knows_modifier",
        "confront_success",
    }:
        return 0.0

    return None


def _generic_ending_score(
    rows: Sequence[Mapping[str, Any]],
    world: World,
    ending: Mapping[str, Any],
    namespace: Namespace,
) -> float:
    ending_id = str(ending["id"])
    predicate = ending.get("predicate")
    if not isinstance(predicate, Predicate):
        return float(reached(rows, ending_id))

    parts = conjuncts(predicate)
    if not parts:
        return float(reached(rows, ending_id))

    distances: list[float] = []
    for part in parts:
        distance = _conjunct_distance(
            part,
            namespace,
            world,
        )
        if distance is None:
            distance = float(reached(rows, ending_id))
        distances.append(distance)
    return sum(distances) / len(distances)


def _delivery_ending_score(
    rows: Sequence[Mapping[str, Any]],
    world: World,
    delivery: Mapping[str, Any],
) -> float:
    subject_id = str(delivery["subject"])
    item = str(delivery["item"])
    destination = str(delivery["zone"])
    subject = world.subjects.get(subject_id)

    has_objective = (
        1.0
        if subject is not None and subject.has_item(item)
        else 0.0
    )
    proximity = _zone_proximity(
        world,
        subject_id,
        destination,
    )
    is_reached = reached(rows, world.target_ending)
    return (
        0.4 * has_objective
        + 0.3 * proximity
        + 0.3 * float(is_reached)
    )


def shaped(
    rows: Sequence[Mapping[str, Any]],
    world: World,
) -> float:
    protagonist = world.subjects[world.protagonist]
    final_turn = max(
        (
            int(row.get("turn", 0) or 0)
            for row in rows
        ),
        default=0,
    )
    final_day = max(
        (
            int(row.get("day", 0) or 0)
            for row in rows
        ),
        default=0,
    )
    namespace = world.namespace(
        protagonist,
        world.present_subjects(protagonist.zone),
        turn=final_turn,
        day=final_day,
    )

    scores: list[float] = []
    for ending in world.target_endings():
        delivery = ending.get("deliver")
        if isinstance(delivery, Mapping):
            scores.append(
                _delivery_ending_score(
                    rows,
                    world,
                    delivery,
                )
            )
        else:
            scores.append(
                _generic_ending_score(
                    rows,
                    world,
                    ending,
                    namespace,
                )
            )

    if not scores:
        return float(reached(rows, world.target_ending))
    return round(max(scores), 12)


def effective_sequence(
    rows: Sequence[Mapping[str, Any]],
    *,
    subject: str | None = None,
) -> list[tuple[str, str, str]]:
    decision_subject = subject or _protagonist(rows)
    sequence: list[tuple[str, str, str]] = []
    for row in rows:
        if (
            row.get("kind") != "decision"
            or row.get("subject") != decision_subject
            or not row.get("effective", False)
        ):
            continue
        classification = row.get("classification")
        if not isinstance(classification, Mapping):
            continue
        category = classification.get("category")
        if category is None:
            continue
        sequence.append(
            (
                str(category),
                str(row.get("verb")),
                str(classification.get("target_role", "none")),
            )
        )
    return sequence


def _levenshtein(
    first: Sequence[tuple[str, str, str]],
    second: Sequence[tuple[str, str, str]],
) -> int:
    if len(first) > len(second):
        first, second = second, first
    previous = list(range(len(first) + 1))
    for second_index, second_value in enumerate(second, start=1):
        current = [second_index]
        for first_index, first_value in enumerate(first, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[first_index] + 1,
                    previous[first_index - 1]
                    + int(first_value != second_value),
                )
            )
        previous = current
    return previous[-1]


def sequence_dissimilarity(
    sequences: Sequence[Sequence[tuple[str, str, str]]],
) -> float | None:
    if len(sequences) <= 1:
        return None
    distances: list[float] = []
    for index, first in enumerate(sequences):
        for second in sequences[index + 1:]:
            denominator = max(len(first), len(second))
            if denominator == 0:
                distances.append(0.0)
            else:
                distances.append(
                    _levenshtein(first, second) / denominator
                )
    return round(sum(distances) / len(distances), 12)


def _quantile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("Cannot calculate quantiles from no values")
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    remainder = position - lower
    return ordered[lower] * (1.0 - remainder) + ordered[upper] * remainder


class Archive:
    def __init__(self) -> None:
        self.cells: dict[tuple[str, str], Elite] = {}
        self.volatility_thresholds: dict[str, float] | None = None

    def freeze_thresholds(
        self,
        volatilities: Sequence[float],
    ) -> dict[str, float]:
        if self.volatility_thresholds is None:
            self.volatility_thresholds = {
                "low_max": round(_quantile(volatilities, 1.0 / 3.0), 12),
                "mid_max": round(_quantile(volatilities, 2.0 / 3.0), 12),
            }
        return dict(self.volatility_thresholds)

    def bin_for(self, volatility: float) -> str:
        value = _volatility_bin(
            volatility,
            self.volatility_thresholds,
        )
        if value is None:
            raise RuntimeError("Archive volatility thresholds are not frozen")
        return value

    def insert(self, elite: Elite) -> bool:
        category = elite.descriptor.category
        volatility_bin = elite.descriptor.volatility_bin
        if category is None:
            return False
        if volatility_bin is None:
            raise ValueError("Elite descriptor has no volatility bin")

        cell = (category, volatility_bin)
        existing = self.cells.get(cell)
        if existing is not None:
            if elite.quality < existing.quality:
                return False
            if elite.quality == existing.quality:
                if elite.reach_rate <= existing.reach_rate:
                    return False
        self.cells[cell] = elite
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "cells": {
                "|".join(cell): self.cells[cell].to_dict()
                for cell in sorted(self.cells)
            },
            "volatility_thresholds": self.volatility_thresholds,
        }

    def save(self, path: str | Path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(
                self.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return destination

    @classmethod
    def load(cls, path: str | Path) -> Archive:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        archive = cls()
        thresholds = raw.get("volatility_thresholds")
        if thresholds is not None:
            archive.volatility_thresholds = {
                "low_max": float(thresholds["low_max"]),
                "mid_max": float(thresholds["mid_max"]),
            }
        for cell_text in sorted(raw.get("cells", {})):
            parts = cell_text.split("|")
            if len(parts) != 2:
                raise ValueError(f"Invalid archive cell: {cell_text}")
            archive.cells[(parts[0], parts[1])] = Elite.from_dict(
                raw["cells"][cell_text]
            )
        return archive
