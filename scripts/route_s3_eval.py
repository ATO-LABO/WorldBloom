"""WB-ROUTE-001 S3 evaluation: does handing the LLM the route layer's own
recorded reason (policy.route, via gapengine.scenes/synopsis's new
"motives") actually help, without letting it invent a reason where the
engine explicitly recorded none?

Reuses S2's own fixed-gene evaluation runs (rho=1.0,
``C:\\Projects\\WorldBloom-local\\runs\\route-s2\\eval3\\sweep\\rho-1.0``) --
no new simulation is run here, only prompts built from existing
layers.jsonl. For each selected run this writes:

- ``prompts/<genome>/seed-<n>/with_motives.txt`` -- the real synopsis prompt
  (scenes carry "motives" wherever a decision had a route).
- ``prompts/<genome>/seed-<n>/without_motives.txt`` -- the same scenes with
  every "motives" key stripped first (the S2 baseline prompt shape).

Plan §3.1 (mechanical): reason-attached rate and cause counts among the
notable-turn protagonist decisions, written to ``mechanical_report.json``.

Plan §3.2 (generation, best-effort): both prompts get sent to
``gapengine.synopsis.generate_text`` using settings.json's own backend/GPU
guard. A run that fails or exceeds --gen-timeout is recorded as a failure
and generation stops there -- prompts already written are kept either way.
On success, a blind pair (A/B randomly swapped per a fixed seed) plus the
run's own fact log (its "注目ターン" scene lines) is written to
``blind/<genome>-seed-<n>.json`` for a separate (Opus) judge to score;
this script never scores anything itself.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gapengine.qd import read_rows
from gapengine.scenes import extract_scenes
from gapengine.synopsis import (
    GenerationError,
    build_synopsis_prompt,
    generate_text,
    load_world_meta,
)

PROJECT = ROOT / "projects" / "momotaro_plus2"
TEMPLATE = ROOT / "templates" / "momotaro_plus2"
SETTINGS_PATH = Path(r"G:\マイドライブ\Projects\WorldBloom_v2\settings.json")
SWEEP_DIR = Path(
    r"C:\Projects\WorldBloom-local\runs\route-s2\eval3\sweep\rho-1.0"
)
REACHED_ENDINGS = frozenset({"homecoming", "homecoming_shared"})


def _reached(rows: list[dict[str, Any]]) -> bool:
    return any(
        row.get("kind") == "event"
        and row.get("verb") == "ending"
        and row.get("id") in REACHED_ENDINGS
        for row in rows
    )


def select_runs(
    sweep_dir: Path, *, per_genome: int = 2, max_total: int = 10
) -> list[tuple[str, int, Path]]:
    """Deterministic selection: for each genome directory (sorted by name),
    the first ``per_genome`` reached seeds in ascending seed order, capped
    at ``max_total`` overall (genomes processed in sorted order, so a cap
    trims the tail genomes' second pick first, never an earlier genome's
    first pick)."""

    selected: list[tuple[str, int, Path]] = []
    for genome_dir in sorted(p for p in sweep_dir.iterdir() if p.is_dir()):
        picked = 0
        for seed_dir in sorted(
            (p for p in genome_dir.iterdir() if p.is_dir()),
            key=lambda p: int(p.name.split("-")[-1]),
        ):
            if picked >= per_genome or len(selected) >= max_total:
                break
            layers_path = seed_dir / "layers.jsonl"
            if not layers_path.is_file():
                continue
            rows = read_rows(layers_path)
            if not _reached(rows):
                continue
            seed = int(seed_dir.name.split("-")[-1])
            selected.append((genome_dir.name, seed, layers_path))
            picked += 1
        if len(selected) >= max_total:
            break
    return selected


def _strip_motives(scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stripped = []
    for scene in scenes:
        scene = dict(scene)
        scene.pop("motives", None)
        stripped.append(scene)
    return stripped


def _mechanical_stats(scenes: list[dict[str, Any]]) -> dict[str, Any]:
    total = 0
    with_reason = 0
    cause_counts: dict[str, int] = {}
    for scene in scenes:
        for motive in scene.get("motives") or []:
            total += 1
            cause = str(motive.get("cause"))
            cause_counts[cause] = cause_counts.get(cause, 0) + 1
            if motive.get("why"):
                with_reason += 1
    return {
        "total_motive_decisions": total,
        "with_reason": with_reason,
        "with_reason_rate": (with_reason / total) if total else None,
        "cause_counts": cause_counts,
    }


def build_prompts(
    selected: list[tuple[str, int, Path]], out_dir: Path
) -> dict[str, Any]:
    world_meta = load_world_meta(PROJECT, TEMPLATE)
    prompts_dir = out_dir / "prompts"
    per_run: list[dict[str, Any]] = []
    aggregate_total = 0
    aggregate_with_reason = 0
    aggregate_causes: dict[str, int] = {}

    for genome, seed, layers_path in selected:
        rows = read_rows(layers_path)
        scenes = extract_scenes(rows, world_meta)
        scenes_no_motive = _strip_motives(scenes)
        elite = {"cell": f"{genome}/seed-{seed}", "quality": None, "reach_rate": 1.0}

        with_prompt = build_synopsis_prompt(elite, scenes, world_meta)
        without_prompt = build_synopsis_prompt(elite, scenes_no_motive, world_meta)

        run_dir = prompts_dir / genome / f"seed-{seed}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "with_motives.txt").write_text(with_prompt, encoding="utf-8")
        (run_dir / "without_motives.txt").write_text(without_prompt, encoding="utf-8")

        stats = _mechanical_stats(scenes)
        aggregate_total += stats["total_motive_decisions"]
        aggregate_with_reason += stats["with_reason"]
        for cause, count in stats["cause_counts"].items():
            aggregate_causes[cause] = aggregate_causes.get(cause, 0) + count

        per_run.append(
            {
                "genome": genome,
                "seed": seed,
                "layers_path": str(layers_path),
                "with_motives_prompt": str(run_dir / "with_motives.txt"),
                "without_motives_prompt": str(run_dir / "without_motives.txt"),
                **stats,
            }
        )

    report = {
        "runs": per_run,
        "aggregate": {
            "total_motive_decisions": aggregate_total,
            "with_reason": aggregate_with_reason,
            "with_reason_rate": (
                aggregate_with_reason / aggregate_total if aggregate_total else None
            ),
            "cause_counts": aggregate_causes,
        },
    }
    (out_dir / "mechanical_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def try_generate(
    per_run: list[dict[str, Any]],
    out_dir: Path,
    *,
    backend: str,
    gen_timeout: int,
    limit: int | None,
) -> dict[str, Any]:
    """Best-effort generation for up to ``limit`` runs (None = all selected).
    Stops at the first failure/timeout and reports it -- never partially
    retries. Returns a summary dict; writes ``blind/<genome>-seed-<n>.json``
    per successfully generated pair."""

    blind_dir = out_dir / "blind"
    blind_dir.mkdir(parents=True, exist_ok=True)
    swap_rng = random.Random("WB-ROUTE-001-S3-blind")

    attempted: list[dict[str, Any]] = []
    runs = per_run if limit is None else per_run[:limit]
    for entry in runs:
        genome = entry["genome"]
        seed = entry["seed"]
        with_prompt = Path(entry["with_motives_prompt"]).read_text(encoding="utf-8")
        without_prompt = Path(entry["without_motives_prompt"]).read_text(
            encoding="utf-8"
        )

        record: dict[str, Any] = {"genome": genome, "seed": seed}
        started = time.monotonic()
        try:
            with_result = generate_text(
                backend, with_prompt, settings_path=SETTINGS_PATH, timeout=gen_timeout
            )
            without_result = generate_text(
                backend,
                without_prompt,
                settings_path=SETTINGS_PATH,
                timeout=gen_timeout,
            )
        except GenerationError as error:
            record["status"] = "failed"
            record["error"] = str(error)
            record["elapsed_seconds"] = round(time.monotonic() - started, 1)
            attempted.append(record)
            break
        elapsed = round(time.monotonic() - started, 1)
        record["elapsed_seconds"] = elapsed

        if with_result.status != "ok" or without_result.status != "ok":
            record["status"] = "prompt_only"
            record["with_warning"] = with_result.warning
            record["without_warning"] = without_result.warning
            attempted.append(record)
            break

        record["status"] = "ok"
        attempted.append(record)

        # A/B blind swap, fixed per (genome, seed) draw order.
        swap = swap_rng.random() < 0.5
        texts = (
            (with_result.text, without_result.text)
            if not swap
            else (without_result.text, with_result.text)
        )
        labels = ("with_motives", "without_motives") if not swap else (
            "without_motives",
            "with_motives",
        )
        blind_payload = {
            "genome": genome,
            "seed": seed,
            "backend": backend,
            "fact_log": [
                line
                for line in with_prompt.split("## 注目ターン\n", 1)[-1].split(
                    "\n\n## 指示", 1
                )[0].splitlines()
                if line.strip()
            ],
            "candidate_a": texts[0],
            "candidate_b": texts[1],
            "answer_key": {"candidate_a": labels[0], "candidate_b": labels[1]},
        }
        (blind_dir / f"{genome}-seed-{seed}.json").write_text(
            json.dumps(blind_payload, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )

    summary = {"backend": backend, "gen_timeout": gen_timeout, "attempts": attempted}
    (out_dir / "generation_report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=Path(r"C:\Projects\WorldBloom-local\runs\route-s3")
    )
    parser.add_argument("--sweep-dir", type=Path, default=SWEEP_DIR)
    parser.add_argument("--per-genome", type=int, default=2)
    parser.add_argument("--max-total", type=int, default=10)
    parser.add_argument(
        "--skip-generation",
        action="store_true",
        help="Only build prompts and the mechanical report.",
    )
    parser.add_argument("--backend", default="ollama")
    parser.add_argument(
        "--gen-timeout",
        type=int,
        default=600,
        help="Per-call timeout in seconds (plan: abort past 10 minutes/run).",
    )
    parser.add_argument(
        "--gen-limit",
        type=int,
        default=None,
        help="Cap the number of runs sent to the LLM (default: all selected).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_dir = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    selected = select_runs(
        args.sweep_dir, per_genome=args.per_genome, max_total=args.max_total
    )
    if not selected:
        print("no reached rho=1.0 runs found under", args.sweep_dir)
        return 1

    report = build_prompts(selected, out_dir)
    print(json.dumps(report["aggregate"], ensure_ascii=False, indent=2, sort_keys=True))

    if not args.skip_generation:
        gen_summary = try_generate(
            report["runs"],
            out_dir,
            backend=args.backend,
            gen_timeout=args.gen_timeout,
            limit=args.gen_limit,
        )
        print(
            json.dumps(
                {
                    "backend": gen_summary["backend"],
                    "attempts": len(gen_summary["attempts"]),
                    "last_status": (
                        gen_summary["attempts"][-1]["status"]
                        if gen_summary["attempts"]
                        else None
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
