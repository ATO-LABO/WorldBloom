"""Command-line entry point for deterministic GapEngine evolution."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Sequence

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gapengine.evolve import evolve
from gapengine.qd import Archive
from gapengine.world_patch import PatchError, apply_patches, approved_patches


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
        "--world-expansion",
        choices=("off", "detect", "expand"),
        default="off",
        help=("Off leaves the world unchanged; detect aggregates zone/verb whiff "
              "triggers after evolution ends; expand applies this project's approved "
              "patches (projects/<name>/patches/*.yaml) before the run, then detects."),
    )
    return parser


def _rebase_gapengine_reference(world: dict, field: str, project_dir: Path, repo_root: Path) -> None:
    """Point world["gapengine"][field] at an absolute path so it still
    resolves once the patched world.yaml is written under <out>/expanded-project
    (a different directory than `project_dir`). Mirrors the two candidates
    engine/world.py and engine/phase2.py already try (project-relative, then
    repo-root-relative); left untouched if neither exists, so the engine's own
    error message still fires later."""
    gapengine = world.get("gapengine")
    if not isinstance(gapengine, dict):
        return
    value = gapengine.get(field)
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        return
    for candidate in (project_dir / value, repo_root / value):
        if candidate.is_file():
            gapengine[field] = str(candidate.resolve())
            return


def _expanded_project(project: Path, out: Path) -> Path:
    """Materialize <out>/expanded-project: `project`'s approved patches
    (WB-WORLDGROW-001 stage 3a) applied to world.yaml, plus an unchanged copy
    of subjects/. Returns `project` unchanged when there are no approved
    patches -- callers then run directly off the original project."""
    try:
        patches = approved_patches(project)
    except PatchError as error:
        raise SystemExit(f"world-expansion patches invalid: {error}") from error
    if not patches:
        return project
    world = yaml.safe_load((project / "world.yaml").read_text(encoding="utf-8"))
    subject_ids = []
    for path in sorted((project / "subjects").glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("id"), str):
            subject_ids.append(data["id"])
    try:
        world = apply_patches(world, patches, subject_ids=subject_ids)
    except PatchError as error:
        raise SystemExit(f"world-expansion patches invalid: {error}") from error
    for field in ("action_graph", "effects"):
        _rebase_gapengine_reference(world, field, project, ROOT)
    expanded = out / "expanded-project"
    expanded.mkdir(parents=True, exist_ok=True)
    (expanded / "world.yaml").write_text(
        yaml.safe_dump(world, allow_unicode=True, sort_keys=False), encoding="utf-8")
    shutil.copytree(project / "subjects", expanded / "subjects", dirs_exist_ok=True)
    return expanded


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project = args.project
    if args.world_expansion == "expand":
        project = _expanded_project(args.project, args.out)
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
            "project": project,
            "seed_base": args.seed_base,
            "seeds": args.seeds,
            "target_ending": args.target_ending,
            "template": args.template,
            "world_expansion": args.world_expansion,
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
