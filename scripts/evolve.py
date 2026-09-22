"""Command-line entry point for deterministic GapEngine evolution."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gapengine.evolve import evolve
from gapengine.qd import Archive


def build_parser() -> argparse.ArgumentParser:
    def non_negative_int(value: str) -> int:
        parsed = int(value)
        if parsed < 0:
            raise argparse.ArgumentTypeError(
                "value must not be negative"
            )
        return parsed

    parser = argparse.ArgumentParser(
        description="Run deterministic MAP-Elites evolution.",
    )
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--generations", type=int, default=20)
    parser.add_argument("--population", type=int, default=100)
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument(
        "--seed-base",
        type=non_negative_int,
        default=0,
    )
    parser.add_argument("--ga-seed", type=int, default=1)
    parser.add_argument("--processes", type=int, default=1)
    parser.add_argument(
        "--keep",
        choices=("all", "reached", "exemplar"),
        default="reached",
    )
    parser.add_argument(
        "--coevolve",
        action="store_true",
        help="Coevolve a separate antagonist population and archive.",
    )
    parser.add_argument(
        "--meta-evolution",
        action="store_true",
        help=(
            "Evolve per-rule enable bits in addition to the nine "
            "scalar genes."
        ),
    )
    parser.add_argument(
        "--target-ending",
        action="extend",
        nargs="+",
        default=None,
        help=(
            "Override target ending ids; any listed ending counts as reached."
        ),
    )
    parser.add_argument("--record-explanations", action=argparse.BooleanOptionalAction, default=True,
                        help="Record bounded choice candidates for the explanation viewer (default: on).")
    parser.add_argument(
        "--kappa",
        type=float,
        default=None,
        help=(
            "WB-JEV-001 Stage 2: override templates/<genre>/rationality.yaml's "
            "kappa (0 disables the rationality layer; that is the template "
            "default)."
        ),
    )
    parser.add_argument(
        "--rationality-backend",
        choices=("ollama", "none"),
        default=None,
        help="Override rationality.yaml's judge backend.",
    )
    parser.add_argument(
        "--rationality-method",
        choices=("noul", "choice"),
        default=None,
        help="Override rationality.yaml's judge method (yes/no vs multiple-choice).",
    )
    parser.add_argument(
        "--rationality-model",
        type=str,
        default=None,
        help="Override rationality.yaml's judge model name.",
    )
    parser.add_argument(
        "--rationality-num-ctx",
        type=int,
        default=None,
        help=(
            "WB-JEV-003: override the Ollama judge's context window size "
            "(num_ctx). Default: rationality.yaml's backend.num_ctx, or "
            "Ollama's own default (16384) if that is unset too."
        ),
    )
    parser.add_argument(
        "--rationality-table",
        type=Path,
        default=None,
        help=(
            "Path to the persisted rationality table JSON (default: "
            "<out>/rationality.json)."
        ),
    )
    parser.add_argument(
        "--rationality-max-calls",
        type=non_negative_int,
        default=None,
        help=(
            "Cap on judge calls (candidates for noul, chunks for choice) "
            "per simulation run; overrides rationality.yaml's "
            "max_judge_calls_per_run (default: unlimited)."
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "WB-GA-RESUME: resume from the last generation completed in "
            "<out>/ga_state.json instead of starting over."
        ),
    )
    parser.add_argument(
        "--resume-allow-code-change",
        action="store_true",
        help=(
            "WB-GA-RESUME: with --resume, downgrade an engine_hash/"
            "gapengine_hash mismatch to a warning instead of refusing "
            "(for an intentional mid-run code change). cfg_fingerprint "
            "mismatches -- a different experiment -- are always refused."
        ),
    )
    return parser


def _print_resume_status(out: Path, generations: int) -> None:
    """A CLI-only preview of scripts/evolve.py's ``--resume``: reads
    ga_state.json (if any) just to report where a resumed run will pick up.
    evolve() does its own, authoritative fingerprint check regardless of
    what is printed here."""

    state_path = out / "ga_state.json"
    if not state_path.is_file():
        return
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        completed = int(state["completed_generations"])
    except (OSError, ValueError, KeyError, TypeError):
        return
    if completed >= generations:
        print(
            f"already completed {completed} generation(s) (of {generations})",
            file=sys.stderr,
        )
    else:
        print(
            f"resuming from generation {completed} (of {generations})",
            file=sys.stderr,
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.resume:
        _print_resume_status(args.out, args.generations)
    archive = evolve(
        {
            "record_explanations": args.record_explanations,
            "coevolve": args.coevolve,
            "ga_seed": args.ga_seed,
            "generations": args.generations,
            "keep": args.keep,
            "meta_evolution": args.meta_evolution,
            "out": args.out,
            "population": args.population,
            "processes": args.processes,
            "project": args.project,
            "rationality": {
                "kappa": args.kappa,
                "backend": args.rationality_backend,
                "method": args.rationality_method,
                "model": args.rationality_model,
                "num_ctx": args.rationality_num_ctx,
                "table": args.rationality_table,
                "max_judge_calls_per_run": args.rationality_max_calls,
            },
            "resume": args.resume,
            "resume_allow_code_change": args.resume_allow_code_change,
            "seed_base": args.seed_base,
            "seeds": args.seeds,
            "target_ending": args.target_ending,
            "template": args.template,
        }
    )
    message = (
        f"archive={args.out / 'archive.json'} "
        f"cells={len(archive.cells)}"
    )
    if args.coevolve:
        antagonist_archive = Archive.load(
            args.out / "archive_antagonist.json"
        )
        message += (
            f" antagonist_archive="
            f"{args.out / 'archive_antagonist.json'} "
            f"antagonist_cells={len(antagonist_archive.cells)}"
        )
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
