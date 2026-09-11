"""Command-line entry point for deterministic GapEngine evolution."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gapengine.evolve import evolve


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run deterministic MAP-Elites evolution.",
    )
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--generations", type=int, default=20)
    parser.add_argument("--population", type=int, default=100)
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--ga-seed", type=int, default=1)
    parser.add_argument("--processes", type=int, default=1)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    archive = evolve(
        {
            "ga_seed": args.ga_seed,
            "generations": args.generations,
            "out": args.out,
            "population": args.population,
            "processes": args.processes,
            "project": args.project,
            "seeds": args.seeds,
            "template": args.template,
        }
    )
    print(
        f"archive={args.out / 'archive.json'} "
        f"cells={len(archive.cells)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
