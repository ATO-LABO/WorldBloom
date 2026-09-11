"""Run policy-free seeds and report reached-route diversity."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

import yaml


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.sim import Simulation
from engine.subject import Subject
from engine.world import World
from gapengine.genome import Genome
from gapengine.policy import Policy
from gapengine.qd import (
    descriptor,
    effective_sequence,
    read_rows,
    reached,
    sequence_dissimilarity,
)


def _load_subjects(directory: Path) -> dict[str, Subject]:
    values = [
        Subject.from_yaml(path)
        for path in sorted(directory.glob("*.yaml"), key=lambda value: value.name)
    ]
    return {subject.id: subject for subject in values}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def build_parser() -> argparse.ArgumentParser:
    def non_negative_int(value: str) -> int:
        parsed = int(value)
        if parsed < 0:
            raise argparse.ArgumentTypeError(
                "value must not be negative"
            )
        return parsed

    parser = argparse.ArgumentParser(
        description="Run a policy-free random-seed baseline.",
    )
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seeds", type=int, default=300)
    parser.add_argument(
        "--seed-base",
        type=non_negative_int,
        default=0,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.seeds < 1:
        raise ValueError("--seeds must be positive")

    project = args.project.resolve()
    template = args.template.resolve()
    output = args.out.resolve()
    action_graph_path = template / "action_graph.yaml"
    qd_cfg = yaml.safe_load(
        (template / "qd.yaml").read_text(encoding="utf-8")
    )
    action_cfg = yaml.safe_load(
        action_graph_path.read_text(encoding="utf-8")
    )
    results: list[dict[str, Any]] = []
    reached_sequences: list[list[tuple[str, str, str]]] = []
    distribution: Counter[str] = Counter()
    all_distribution: Counter[str] = Counter()

    for seed in range(
        args.seed_base,
        args.seed_base + args.seeds,
    ):
        world = World.from_yaml(
            project / "world.yaml",
            action_graph_path=action_graph_path,
        )
        subjects = _load_subjects(project / "subjects")
        policy = Policy(
            Genome.neutral(),
            precedent=None,
            cfg=action_cfg,
            annotate_only=True,
        )
        layer_path = Simulation(
            seed,
            world,
            subjects,
            output / f"seed-{seed}",
            policies={world.protagonist: policy},
        ).run()
        rows = read_rows(layer_path)
        run_descriptor = descriptor(rows, qd_cfg)
        category = run_descriptor.category or "unclassified"
        all_distribution[category] += 1
        did_reach = reached(rows, world.target_ending)
        if did_reach:
            reached_sequences.append(effective_sequence(rows))
            distribution[category] += 1
        results.append(
            {
                "category": run_descriptor.category,
                "classification_status": (
                    "classified"
                    if run_descriptor.category is not None
                    else "unclassified"
                ),
                "layers_path": layer_path.relative_to(output).as_posix(),
                "reached": did_reach,
                "seed": seed,
                "volatility": run_descriptor.volatility,
            }
        )

    summary = {
        "all_descriptor_distribution": {
            key: all_distribution[key]
            for key in sorted(all_distribution)
        },
        "descriptor_distribution": {
            key: distribution[key] for key in sorted(distribution)
        },
        "reached": len(reached_sequences),
        "route_dissimilarity": sequence_dissimilarity(
            reached_sequences
        ),
        "runs": results,
        "seed_base": args.seed_base,
        "seeds": args.seeds,
        "success_rate": len(reached_sequences) / args.seeds,
    }
    destination = output / "summary.json"
    _write_json(destination, summary)
    print(
        f"summary={destination} reached={len(reached_sequences)} "
        f"dissimilarity={summary['route_dissimilarity']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
