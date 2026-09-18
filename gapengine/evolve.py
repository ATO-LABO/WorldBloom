"""Deterministic MAP-Elites evolution around the simulation engine."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
import multiprocessing
import random
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

from engine.sim import Simulation
from engine.subject import Subject
from engine.world import World
from gapengine.genome import Genome
from gapengine.knowledge_text import load_common_knowledge, load_key_items
from gapengine.ollama import DEFAULT_BASE_URL as RATIONALITY_DEFAULT_BASE_URL
from gapengine.ollama import DEFAULT_MODEL as RATIONALITY_DEFAULT_MODEL
from gapengine.policy import Policy
from gapengine.precedent import PrecedentTable, from_runs, load_canon
from gapengine.qd import (
    Archive,
    Descriptor,
    Elite,
    antagonist_quality,
    descriptor,
    effective_sequence,
    quality,
    read_rows,
    reached,
    sequence_dissimilarity,
    shaped,
)
from gapengine.rationality import NullJudge, OllamaLogprobJudge, Rationality, RationalityTable


def _json_write(path: Path, value: Any) -> None:
    """Keep legacy bytes, but never expose a partially written JSON document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")
    temporary = path.with_name("." + path.name + "-" + uuid.uuid4().hex)
    with temporary.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class EvolutionCancelled(Exception):
    """Cooperative stop at a seed, individual or generation boundary."""


def _build_rationality_judge(rationality_cfg: Mapping[str, Any]) -> Any:
    """WB-JEV-001 Stage 2: the judge for a run_individual job's Rationality,
    picked by ``rationality_cfg["backend"]`` ("ollama" or "none")."""

    backend = str(rationality_cfg.get("backend", "none"))
    if backend == "ollama":
        return OllamaLogprobJudge(
            model=str(rationality_cfg.get("model", RATIONALITY_DEFAULT_MODEL)),
            base_url=str(rationality_cfg.get("base_url", RATIONALITY_DEFAULT_BASE_URL)),
            timeout=float(rationality_cfg.get("timeout", 300.0)),
            method=str(rationality_cfg.get("method", "noul")),
        )
    return NullJudge()


def _append_rationality_entries(path: Path, entries: Mapping[str, float]) -> None:
    """Append this seed's newly-learned (key, p) rows -- one job/individual's
    file accumulates across its own seeds; never touched by another
    individual (WB-JEV-001 Stage 2 plan §1.6)."""

    if not entries:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for key, value in sorted(entries.items()):
            handle.write(json.dumps({"key": key, "p": value}, ensure_ascii=False) + "\n")


def _merge_rationality_table(
    generation_dir: Path,
    table_path: Path,
) -> RationalityTable:
    """Fold every individual's ``rationality-new.jsonl`` from one generation
    into the experiment-wide table at ``table_path`` (the "正本"), then save
    it back so the next generation's jobs read the merged table. Same key
    appearing in more than one file is harmless -- the judge runs at
    temperature 0, so every writer computes the same value for it."""

    table = RationalityTable.load(table_path)
    for path in sorted(generation_dir.glob("ind-*/rationality-new.jsonl")):
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        table.update({str(row["key"]): float(row["p"]) for row in rows})
    table.save(table_path)
    return table


def _seed_event(job, seed, kind, run=None):
    observation = job.get("observation")
    if observation is None:
        return
    event_id = f"{observation['generation']}-{observation['role']}-{job['index']}-{seed}"
    event = {"schema_version": 1, "event_id": event_id, "kind": kind,
             "generation": observation["generation"], "role": observation["role"],
             "individual_index": job["index"], "seed": seed}
    if run is not None:
        path = Path(job["logical_root"]) / run["layers_path"]
        event.update(run=dict(run), source_log_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                     genome=job.get("genome"), antagonist_genome=job.get("antagonist_genome"),
                     parents=list(job.get("parents", [])))
    _json_write(Path(observation["events"]) / (event_id + "-" + kind + ".json"), event)


def _seed_checkpoint(job):
    observation = job.get("observation")
    if observation is not None and Path(observation["cancel"]).exists():
        raise EvolutionCancelled("cancel requested")


def _load_subjects(directory: Path) -> dict[str, Subject]:
    subjects = [
        Subject.from_yaml(path)
        for path in sorted(directory.glob("*.yaml"), key=lambda value: value.name)
    ]
    return {subject.id: subject for subject in subjects}


def _load_yaml(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    return default if value is None else value


def _rule_ids(
    rules: Iterable[Mapping[str, Any]],
) -> tuple[str, ...]:
    identifiers: list[str] = []
    for index, rule in enumerate(rules):
        if not isinstance(rule, Mapping):
            raise ValueError(
                f"Policy rule at index {index} must be a mapping"
            )
        rule_id = rule.get("id")
        if not isinstance(rule_id, str) or not rule_id:
            raise ValueError(
                f"Policy rule id is required at index {index}"
            )
        identifiers.append(rule_id)

    if len(identifiers) != len(set(identifiers)):
        duplicates = sorted(
            rule_id
            for rule_id in set(identifiers)
            if identifiers.count(rule_id) > 1
        )
        raise ValueError(
            f"Policy rule ids must be unique: {duplicates}"
        )
    return tuple(sorted(identifiers))


def _world_meta(
    world: World,
    *,
    vol_high: float | None = None,
) -> dict[str, Any]:
    value = {
        "antagonist": world.antagonist,
        "max_turns": world.days * len(world.slots),
        "protagonist": world.protagonist,
        "target_ending": world.target_ending,
    }
    if vol_high is not None:
        value["vol_high"] = float(vol_high)
    return value


def _lineage_stats(
    rows: Sequence[Mapping[str, Any]],
    protagonist: str,
    antagonist: str,
) -> dict[str, Any]:
    """Ally count and the antagonist contest turn, read back from the layer
    rows a run already wrote. Read-only: never touches sim state, rng, or the
    layer file itself (WB-LINEAGE-001)."""
    allies: set[str] = set()
    allies_at_contest: int | None = None
    contest_turn: int | None = None
    for row in rows:
        if (
            row.get("kind") == "event"
            and row.get("verb") == "ally_gained"
            and row.get("subject") == protagonist
        ):
            details = row.get("details")
            ally = details.get("ally") if isinstance(details, Mapping) else None
            if ally is not None:
                allies.add(str(ally))
            continue
        if contest_turn is not None:
            continue
        if (
            row.get("kind") != "decision"
            or row.get("verb") != "fight"
            or row.get("result") not in ("won", "lost")
        ):
            continue
        # Either side may be the fight's subject: resolve(actor, target, ...)
        # decides the winner symmetrically and just relabels `result` from
        # the actor's view, so who initiated is not part of "decisive contest".
        args = row.get("args")
        target = args[0] if isinstance(args, list) and args else None
        pair = {row.get("subject"), target}
        if pair != {protagonist, antagonist}:
            continue
        allies_at_contest = len(allies)
        contest_turn = row.get("turn")
    return {
        "allies_at_contest": allies_at_contest,
        "allies_final": len(allies),
        "contest_turn": contest_turn,
    }


def run_individual(job: Mapping[str, Any]) -> dict[str, Any]:
    raw_genome = job.get("genome")
    genome = (
        Genome.from_dict(raw_genome)
        if raw_genome is not None
        else None
    )
    raw_antagonist_genome = job.get("antagonist_genome")
    antagonist_genome = (
        Genome.from_dict(raw_antagonist_genome)
        if raw_antagonist_genome is not None
        else None
    )
    seeds = [int(value) for value in job["seeds"]]
    world_path = Path(str(job["world_path"]))
    subjects_dir = Path(str(job["subjects_dir"]))
    out_dir = Path(str(job["out_dir"]))
    logical_root = Path(str(job["logical_root"]))
    protagonist = str(job["protagonist"])
    antagonist = str(job["antagonist"])
    action_cfg = dict(job["action_cfg"])
    antagonist_action_cfg = dict(
        job.get("antagonist_action_cfg", action_cfg)
    )
    raw_action_graph_path = job.get("action_graph_path")
    action_graph_path = (
        Path(str(raw_action_graph_path))
        if raw_action_graph_path is not None
        else None
    )
    rules = list(job.get("rules", []))
    target_ending_override = job.get("target_ending")
    qd_cfg = dict(job["qd_cfg"])
    precedent = (
        PrecedentTable.from_json(str(job["precedent_json"]))
        if job.get("precedent_json") is not None
        else None
    )
    antagonist_precedent = (
        PrecedentTable.from_json(
            str(job["antagonist_precedent_json"])
        )
        if job.get("antagonist_precedent_json") is not None
        else None
    )
    vol_high = (
        float(job["vol_high"])
        if job.get("vol_high") is not None
        else None
    )

    # WB-JEV-001 Stage 2: only the protagonist ever gets a Rationality (the
    # antagonist is out of scope). Table and judge are built once per job so
    # entries learned in an earlier seed of this same job are reused by a
    # later one; the judge's own caches (e.g. choice mode's measured
    # top_logprobs limit) likewise persist across this job's seeds.
    rationality_cfg = job.get("rationality_cfg")
    rationality_table: RationalityTable | None = None
    rationality_judge: Any = None
    rationality_new_path: Path | None = None
    rationality_total_judge_calls = 0
    if rationality_cfg is not None:
        rationality_table = RationalityTable.load(
            Path(str(job["rationality_table_path"]))
        )
        rationality_judge = _build_rationality_judge(rationality_cfg)
        rationality_new_path = out_dir / "rationality-new.jsonl"

    runs: list[dict[str, Any]] = []
    for seed in seeds:
        _seed_checkpoint(job)
        _seed_event(job, seed, "started")
        world = World.from_yaml(
            world_path,
            action_graph_path=action_graph_path,
        )
        if target_ending_override is not None:
            world.set_target_ending(target_ending_override)
        subjects = _load_subjects(subjects_dir)
        seed_dir = out_dir / f"seed-{seed}"

        rationality = None
        if rationality_cfg is not None and genome is not None:
            assert rationality_table is not None and rationality_judge is not None
            rationality = Rationality(
                kappa=float(rationality_cfg["kappa"]),
                table=rationality_table,
                judge=rationality_judge,
                common_knowledge=rationality_cfg.get("common_knowledge", []),
                method=str(rationality_cfg.get("method", "noul")),
                key_items=rationality_cfg.get("key_items", []),
                max_judge_calls=rationality_cfg.get("max_judge_calls"),
            )

        policies: dict[str, Policy] = {}
        if genome is not None:
            policies[protagonist] = Policy(
                genome,
                precedent,
                rules,
                cfg=action_cfg,
                rationality=rationality,
            )
        if antagonist_genome is not None:
            policies[antagonist] = Policy(
                antagonist_genome,
                antagonist_precedent,
                rules,
                cfg=antagonist_action_cfg,
            )

        layer_path = Simulation(
            seed,
            world,
            subjects,
            seed_dir,
            policies=policies or None,
            precedent=precedent,
            record_explanations=bool(job.get("record_explanations", False)),
        ).run()
        rows = read_rows(layer_path)

        if rationality is not None:
            assert rationality_new_path is not None
            _append_rationality_entries(rationality_new_path, rationality.new_entries)
            rationality_total_judge_calls += rationality.meta["judge_calls"]

        if antagonist_genome is not None:
            rows[0]["antagonist_genome"] = antagonist_genome.to_dict()
            layer_path.write_text(
                "".join(
                    json.dumps(
                        row,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                    for row in rows
                ),
                encoding="utf-8",
                newline="\n",
            )

        run_descriptor = descriptor(rows, qd_cfg)
        is_reached = reached(rows, world.target_ending)
        lineage = _lineage_stats(rows, protagonist, antagonist)
        header = rows[0]
        relative_path = layer_path.relative_to(logical_root).as_posix()
        run_result: dict[str, Any] = {
            "allies_at_contest": lineage["allies_at_contest"],
            "allies_final": lineage["allies_final"],
            "category": run_descriptor.category,
            "contest_turn": lineage["contest_turn"],
            "effective_sequence": [
                list(value) for value in effective_sequence(rows)
            ],
            "engine_hash": header.get("engine_hash"),
            "layers_path": relative_path,
            "precedent_hash": header.get("precedent_hash"),
            "quality": quality(rows, _world_meta(world)),
            "reached": is_reached,
            "seed": seed,
            "shaped": shaped(rows, world),
            "volatility": run_descriptor.volatility,
        }

        if antagonist_genome is not None:
            antagonist_descriptor = descriptor(
                rows,
                qd_cfg,
                subject=antagonist,
            )
            run_result.update(
                {
                    "antagonist_category": (
                        antagonist_descriptor.category
                    ),
                    "antagonist_effective_sequence": [
                        list(value)
                        for value in effective_sequence(
                            rows,
                            subject=antagonist,
                        )
                    ],
                    "antagonist_quality": antagonist_quality(
                        rows,
                        _world_meta(world, vol_high=vol_high),
                    ),
                }
            )

        runs.append(run_result)
        _seed_event(job, seed, "completed", run_result)
        _seed_checkpoint(job)

    result: dict[str, Any] = {
        "genome": genome.to_dict() if genome is not None else None,
        "index": int(job["index"]),
        "parents": list(job.get("parents", [])),
        "runs": runs,
        "shaped": round(
            sum(float(run["shaped"]) for run in runs) / len(runs),
            12,
        ),
    }
    if antagonist_genome is not None:
        result["antagonist_genome"] = antagonist_genome.to_dict()
        result["antagonist_shaped"] = round(
            sum(
                float(run["antagonist_quality"])
                for run in runs
            )
            / len(runs),
            12,
        )
    if rationality_cfg is not None:
        result["rationality_judge_calls"] = rationality_total_judge_calls
    return result


def _evaluate_jobs(jobs, processes, observer=None):
    # imap yields index order. Seed progress may arrive out of order, but never
    # participates in selection, threshold freezing, mutation or random draws.
    def collected(result):
        if observer is not None:
            observer.individual_completed(result["index"])
            observer.checkpoint()
        return result

    if processes <= 1:
        results = []
        for job in jobs:
            if observer is not None:
                observer.checkpoint()
            results.append(collected(run_individual(job)))
        return results
    context = multiprocessing.get_context("spawn")
    with context.Pool(processes=processes) as pool:
        iterator = pool.imap(run_individual, jobs)
        results = []
        while len(results) < len(jobs):
            try:
                result = iterator.next(timeout=0.1)
            except multiprocessing.TimeoutError:
                if observer is not None:
                    observer.poll()
                continue
            results.append(collected(result))
        return results


def _best_reached(
    result: Mapping[str, Any],
    *,
    role: str = "protagonist",
) -> Mapping[str, Any] | None:
    quality_key = (
        "antagonist_quality"
        if role == "antagonist"
        else "quality"
    )
    candidates = [
        run for run in result["runs"] if bool(run["reached"])
    ]
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda run: (
            -float(run[quality_key]),
            int(run["seed"]),
        ),
    )[0]


def _cell_for_run(
    run: Mapping[str, Any],
    archive: Archive,
    *,
    role: str = "protagonist",
) -> tuple[str, str] | None:
    category_key = (
        "antagonist_category"
        if role == "antagonist"
        else "category"
    )
    category = run.get(category_key)
    if category is None:
        return None
    return (
        str(category),
        archive.bin_for(float(run["volatility"])),
    )


def _result_summary(
    result: Mapping[str, Any],
    archive: Archive,
    *,
    role: str = "protagonist",
) -> dict[str, Any]:
    best = _best_reached(result, role=role)
    cell = (
        _cell_for_run(best, archive, role=role)
        if best is not None
        else None
    )
    category_key = (
        "antagonist_category"
        if role == "antagonist"
        else "category"
    )
    genome_key = (
        "antagonist_genome"
        if role == "antagonist"
        else "genome"
    )
    shaped_key = (
        "antagonist_shaped"
        if role == "antagonist"
        else "shaped"
    )
    summary = {
        "cell": list(cell) if cell is not None else None,
        "classification_status": (
            "unclassified"
            if best is not None and best.get(category_key) is None
            else "classified"
            if best is not None
            else "not_reached"
        ),
        "genome": result[genome_key],
        "index": int(result["index"]),
        "parents": list(result["parents"]),
        "reach_rate": (
            sum(bool(run["reached"]) for run in result["runs"])
            / len(result["runs"])
        ),
        "runs": list(result["runs"]),
        "shaped": float(result[shaped_key]),
    }
    if role == "antagonist":
        summary["opponent_genome"] = result.get("genome")
    elif "antagonist_genome" in result:
        summary["opponent_genome"] = result["antagonist_genome"]
    return summary


def _insert_result(
    result: Mapping[str, Any],
    archive: Archive,
    generation: int,
    *,
    role: str = "protagonist",
    include_matchup: bool = False,
) -> bool:
    best = _best_reached(result, role=role)
    category_key = (
        "antagonist_category"
        if role == "antagonist"
        else "category"
    )
    quality_key = (
        "antagonist_quality"
        if role == "antagonist"
        else "quality"
    )
    genome_key = (
        "antagonist_genome"
        if role == "antagonist"
        else "genome"
    )
    if best is None or best.get(category_key) is None:
        return False

    reach_rate = (
        sum(bool(run["reached"]) for run in result["runs"])
        / len(result["runs"])
    )
    descriptor_value = Descriptor(
        category=str(best[category_key]),
        volatility=float(best["volatility"]),
        volatility_bin=archive.bin_for(float(best["volatility"])),
    )
    exemplar: dict[str, Any] = {
        "engine_hash": best.get("engine_hash"),
        "layers_path": str(best["layers_path"]),
        "precedent_hash": best.get("precedent_hash"),
        "seed": int(best["seed"]),
    }
    if include_matchup:
        exemplar["antagonist_genome"] = result.get(
            "antagonist_genome"
        )
        exemplar["genome"] = result.get("genome")

    elite = Elite(
        genome=Genome.from_dict(result[genome_key]),
        quality=float(best[quality_key]),
        descriptor=descriptor_value,
        reach_rate=reach_rate,
        exemplar=exemplar,
        generation=generation,
        parents=tuple(str(value) for value in result["parents"]),
    )
    return archive.insert(elite)


def _parent_pool(
    archive: Archive,
    previous: list[dict[str, Any]],
) -> list[tuple[Genome, str, tuple[str, str] | None]]:
    pool: list[tuple[Genome, str, tuple[str, str] | None]] = []
    for cell in sorted(archive.cells):
        elite = archive.cells[cell]
        label = f"g{elite.generation}/archive/{cell[0]}-{cell[1]}"
        entry = (elite.genome, label, cell)
        pool.extend((entry, entry, entry))

    count = max(1, (len(previous) + 3) // 4)
    shaped_top = sorted(
        previous,
        key=lambda value: (
            -float(value["shaped"]),
            int(value["index"]),
        ),
    )[:count]
    for value in shaped_top:
        cell_raw = value.get("cell")
        cell = (
            (str(cell_raw[0]), str(cell_raw[1]))
            if cell_raw is not None
            else None
        )
        pool.append(
            (
                Genome.from_dict(value["genome"]),
                f"g{int(value['generation'])}/ind-{int(value['index'])}",
                cell,
            )
        )
    return pool


def _choose_parents(
    pool: list[tuple[Genome, str, tuple[str, str] | None]],
    rng: random.Random,
) -> tuple[
    tuple[Genome, str, tuple[str, str] | None],
    tuple[Genome, str, tuple[str, str] | None],
]:
    if not pool:
        raise ValueError("Parent pool is empty")
    first = pool[rng.randrange(len(pool))]
    second = pool[rng.randrange(len(pool))]
    for _ in range(5):
        if first[2] is None or second[2] is None or first[2] != second[2]:
            break
        if rng.random() >= 0.8:
            break
        second = pool[rng.randrange(len(pool))]
    return first, second


def _next_population(
    size: int,
    archive: Archive,
    previous: list[dict[str, Any]],
    rng: random.Random,
    *,
    rule_ids: tuple[str, ...] = (),
) -> list[tuple[Genome, list[str]]]:
    pool = _parent_pool(archive, previous)
    population: list[tuple[Genome, list[str]]] = []
    for _ in range(size):
        if not pool or rng.random() < 0.1:
            population.append(
                (
                    Genome.random(
                        rng,
                        rule_ids=rule_ids,
                    ),
                    [],
                )
            )
            continue
        first, second = _choose_parents(pool, rng)
        child = Genome.crossover(first[0], second[0], rng)
        child = Genome.mutate(child, rng)
        population.append((child, [first[1], second[1]]))
    return population


def _archive_precedent_paths(
    archive: Archive,
    out_dir: Path,
) -> list[Path]:
    return [
        out_dir / str(archive.cells[cell].exemplar["layers_path"])
        for cell in sorted(archive.cells)
    ]


def _archive_dissimilarity(
    archive: Archive,
    out_dir: Path,
    *,
    subject: str | None = None,
) -> float | None:
    sequences = [
        effective_sequence(
            read_rows(
                out_dir
                / str(archive.cells[cell].exemplar["layers_path"])
            ),
            subject=subject,
        )
        for cell in sorted(archive.cells)
    ]
    return sequence_dissimilarity(sequences)


def _prune_layers(
    results: list[dict[str, Any]],
    out_dir: Path,
    keep: str,
    *,
    role: str = "protagonist",
    observer=None,
) -> None:
    if keep == "all":
        return

    retained: set[str] = set()
    if keep == "reached":
        retained.update(
            str(run["layers_path"])
            for result in results
            for run in result["runs"]
            if bool(run["reached"])
        )
        if not retained and results:
            shaped_key = (
                "antagonist_shaped"
                if role == "antagonist"
                else "shaped"
            )
            shaped_best = sorted(
                results,
                key=lambda value: (
                    -float(value[shaped_key]),
                    int(value["index"]),
                ),
            )[0]
            retained.update(
                str(run["layers_path"])
                for run in shaped_best["runs"]
            )
    elif keep == "exemplar":
        for result in results:
            exemplar = _best_reached(result, role=role)
            if exemplar is not None:
                retained.add(str(exemplar["layers_path"]))
    else:
        raise ValueError(
            "keep must be one of: all, reached, exemplar"
        )

    for result in results:
        for run in result["runs"]:
            relative_path = str(run["layers_path"])
            if relative_path in retained:
                continue
            layer_path = out_dir / relative_path
            if layer_path.exists():
                layer_path.unlink()
                if observer is not None:
                    observer.pruned(relative_path)
            seed_dir = layer_path.parent
            if seed_dir.is_dir() and not any(seed_dir.iterdir()):
                seed_dir.rmdir()


def _generation_lineage_summary(
    raw_results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Action-share and ally/contest aggregates for one generation's raw
    (pre-pruning) protagonist results (WB-LINEAGE-001). Every action
    category/verb/role combination is counted mechanically -- nothing is
    singled out by name."""
    runs = [run for result in raw_results for run in result["runs"]]
    total_runs = len(runs)

    action_counts: dict[str, int] = {}
    for run in runs:
        keys = {
            "/".join(str(part) for part in triple)
            for triple in run.get("effective_sequence", [])
        }
        for key in keys:
            action_counts[key] = action_counts.get(key, 0) + 1
    action_share = {
        key: action_counts[key] / total_runs for key in sorted(action_counts)
    }

    contest_runs = [run for run in runs if run.get("contest_turn") is not None]
    allies_mean_at_contest = (
        sum(float(run["allies_at_contest"]) for run in contest_runs)
        / len(contest_runs)
        if contest_runs
        else None
    )
    allies_mean_final = (
        sum(float(run["allies_final"]) for run in runs) / total_runs
        if total_runs
        else None
    )
    contest_rate = len(contest_runs) / total_runs if total_runs else None

    return {
        "action_share": action_share,
        "allies_mean_at_contest": allies_mean_at_contest,
        "allies_mean_final": allies_mean_final,
        "contest_rate": contest_rate,
    }


def evolve(cfg: Mapping[str, Any], *, observer=None) -> Archive:
    project_dir = Path(str(cfg["project"])).resolve()
    template_dir = Path(str(cfg["template"])).resolve()
    out_dir = Path(str(cfg["out"])).resolve()
    world_path = project_dir / "world.yaml"
    subjects_dir = project_dir / "subjects"
    configured_action_graph_path = (
        template_dir / "action_graph.yaml"
    )
    action_graph_path: Path | None = (
        configured_action_graph_path
        if configured_action_graph_path.is_file()
        else None
    )

    generations = int(cfg.get("generations", 20))
    population_size = int(cfg.get("population", 100))
    seed_count = int(cfg.get("seeds", 3))
    seed_base = int(cfg.get("seed_base", 0))
    ga_seed = int(cfg.get("ga_seed", 1))
    processes = int(cfg.get("processes", 1))
    keep = str(cfg.get("keep", "reached"))
    coevolve = bool(cfg.get("coevolve", False))
    meta_evolution = bool(cfg.get("meta_evolution", False))
    if generations < 1 or population_size < 1 or seed_count < 1:
        raise ValueError(
            "generations, population, and seeds must be positive"
        )
    if seed_base < 0:
        raise ValueError("seed_base must not be negative")
    if processes < 1:
        raise ValueError("processes must be positive")
    if keep not in {"all", "reached", "exemplar"}:
        raise ValueError(
            "keep must be one of: all, reached, exemplar"
        )

    if observer is not None:
        observer.checkpoint(phase="preparing")
    seeds = list(range(seed_base, seed_base + seed_count))
    ga_rng = random.Random(ga_seed)
    action_cfg = dict(
        _load_yaml(
            configured_action_graph_path,
            {"nodes": [], "edges": []},
        )
    )
    qd_cfg = dict(
        _load_yaml(
            template_dir / "qd.yaml",
            {
                "categories": ["I", "II", "III", "IV", "V", "VI"],
                "volatility_bins": ["low", "mid", "high"],
            },
        )
    )
    rules = list(_load_yaml(template_dir / "rules.yaml", []))
    rule_ids = _rule_ids(rules) if meta_evolution else ()
    canon = load_canon(template_dir / "canon.yaml")

    # WB-JEV-001 Stage 2: rationality.yaml's kappa/method/backend are the
    # template's defaults; cfg["rationality"] (scripts/evolve.py's
    # --kappa/--rationality-backend/... CLI flags) overrides individual
    # fields. kappa<=0 (the momotaro template's default) disables the whole
    # apparatus -- no table file, no per-job rationality_cfg, no judge calls,
    # no "rationality" header key, no p_rat/m_rat meta -- so pre-Stage-2 runs
    # stay byte-identical (plan §0/§1.6).
    rationality_yaml = dict(_load_yaml(template_dir / "rationality.yaml", {}) or {})
    rationality_yaml_backend = dict(rationality_yaml.get("backend") or {})
    rationality_override = dict(cfg.get("rationality") or {})

    def _rationality_pick(name: str, default: Any) -> Any:
        value = rationality_override.get(name)
        return default if value is None else value

    rationality_kappa = float(
        _rationality_pick("kappa", rationality_yaml.get("kappa", 0.0)) or 0.0
    )
    rationality_enabled = rationality_kappa > 0.0
    rationality_cfg: dict[str, Any] | None = None
    rationality_table_path: Path | None = None
    if rationality_enabled:
        rationality_cfg = {
            "kappa": rationality_kappa,
            "method": str(
                _rationality_pick("method", rationality_yaml.get("method", "noul"))
            ),
            "backend": str(
                _rationality_pick("backend", rationality_yaml_backend.get("type", "none"))
            ),
            "model": str(
                _rationality_pick(
                    "model", rationality_yaml_backend.get("model", RATIONALITY_DEFAULT_MODEL)
                )
            ),
            "base_url": str(
                _rationality_pick(
                    "base_url",
                    rationality_yaml_backend.get("base_url", RATIONALITY_DEFAULT_BASE_URL),
                )
            ),
            "timeout": float(
                _rationality_pick("timeout", rationality_yaml_backend.get("timeout", 300.0))
            ),
            "max_judge_calls": _rationality_pick("max_judge_calls_per_run", None),
            "common_knowledge": load_common_knowledge(template_dir),
            "key_items": load_key_items(template_dir),
        }
        rationality_table_path = Path(
            str(rationality_override.get("table") or (out_dir / "rationality.json"))
        )

    world_model = World.from_yaml(
        world_path,
        action_graph_path=action_graph_path,
    )
    target_ending_override = cfg.get("target_ending")
    if target_ending_override is not None:
        world_model.set_target_ending(target_ending_override)
    protagonist = world_model.protagonist
    antagonist = world_model.antagonist

    antagonist_action_cfg = action_cfg
    antagonist_canon = PrecedentTable()
    if coevolve:
        antagonist_action_graph_path = (
            template_dir / "action_graph.antagonist.yaml"
        )
        if antagonist_action_graph_path.is_file():
            antagonist_action_cfg = dict(
                _load_yaml(
                    antagonist_action_graph_path,
                    action_cfg,
                )
            )
        antagonist_canon_path = (
            template_dir / "canon.antagonist.yaml"
        )
        if antagonist_canon_path.is_file():
            antagonist_canon = load_canon(antagonist_canon_path)

    archive = Archive()
    antagonist_archive = Archive() if coevolve else None
    summaries: list[dict[str, Any]] = []
    previous_results: list[dict[str, Any]] = []
    previous_antagonist_results: list[dict[str, Any]] = []
    population = [
        (
            Genome.random(
                ga_rng,
                rule_ids=rule_ids,
            ),
            [],
        )
        for _ in range(population_size)
    ]
    antagonist_population = (
        [
            (
                Genome.random(
                    ga_rng,
                    rule_ids=rule_ids,
                ),
                [],
            )
            for _ in range(population_size)
        ]
        if coevolve
        else []
    )

    for generation in range(generations):
        if observer is not None:
            observer.checkpoint(phase="preparing", generation=generation, role=None)
        generation_dir = out_dir / f"g{generation}"
        archive_runs = (
            _archive_precedent_paths(archive, out_dir)
            if generation > 0
            else []
        )
        precedent = canon.merge(
            from_runs(
                archive_runs,
                protagonist=protagonist,
            )
        )
        precedent_path = generation_dir / "precedent.json"
        _json_write(precedent_path, json.loads(precedent.to_json()))

        antagonist_precedent: PrecedentTable | None = None
        if coevolve:
            assert antagonist_archive is not None
            antagonist_archive_runs = (
                _archive_precedent_paths(
                    antagonist_archive,
                    out_dir,
                )
                if generation > 0
                else []
            )
            antagonist_precedent = antagonist_canon.merge(
                from_runs(
                    antagonist_archive_runs,
                    protagonist=antagonist,
                )
            )
            _json_write(
                generation_dir / "precedent.antagonist.json",
                json.loads(antagonist_precedent.to_json()),
            )

        if generation > 0:
            population = _next_population(
                population_size,
                archive,
                previous_results,
                ga_rng,
                rule_ids=rule_ids,
            )
            if coevolve:
                assert antagonist_archive is not None
                antagonist_population = _next_population(
                    population_size,
                    antagonist_archive,
                    previous_antagonist_results,
                    ga_rng,
                    rule_ids=rule_ids,
                )

        _json_write(
            generation_dir / "population.json",
            [
                {
                    "genome": genome.to_dict(),
                    "index": index,
                    "parents": parents,
                }
                for index, (genome, parents) in enumerate(population)
            ],
        )
        if coevolve:
            _json_write(
                generation_dir / "population.antagonist.json",
                [
                    {
                        "genome": genome.to_dict(),
                        "index": index,
                        "parents": parents,
                    }
                    for index, (genome, parents) in enumerate(
                        antagonist_population
                    )
                ],
            )

        antagonist_samples = (
            [
                antagonist_archive.cells[cell].genome
                for cell in sorted(antagonist_archive.cells)
            ]
            if antagonist_archive is not None
            else []
        )
        jobs = [
            {
                "action_cfg": action_cfg,
                "action_graph_path": (
                    str(action_graph_path)
                    if action_graph_path is not None
                    else None
                ),
                "antagonist": antagonist,
                "antagonist_action_cfg": antagonist_action_cfg,
                "antagonist_genome": (
                    antagonist_samples[
                        index % len(antagonist_samples)
                    ].to_dict()
                    if antagonist_samples
                    else None
                ),
                "antagonist_precedent_json": (
                    antagonist_precedent.to_json()
                    if antagonist_precedent is not None
                    else None
                ),
                "genome": genome.to_dict(),
                "index": index,
                "logical_root": str(out_dir),
                "out_dir": str(generation_dir / f"ind-{index}"),
                "parents": parents,
                "precedent_json": precedent.to_json(),
                "protagonist": protagonist,
                "qd_cfg": qd_cfg,
                "rules": rules,
                "seeds": seeds,
                "subjects_dir": str(subjects_dir),
                "world_path": str(world_path),
            }
            for index, (genome, parents) in enumerate(population)
        ]
        for job in jobs:
            job["record_explanations"] = bool(cfg.get("record_explanations", False))
            job["target_ending"] = world_model.target_ending
            if rationality_enabled:
                job["rationality_cfg"] = rationality_cfg
                job["rationality_table_path"] = str(rationality_table_path)
        if observer is not None:
            observer.bind(jobs, generation, "protagonist")
        raw_results = _evaluate_jobs(jobs, processes, observer)

        rationality_table_size: int | None = None
        rationality_generation_judge_calls: int | None = None
        if rationality_enabled:
            assert rationality_table_path is not None
            rationality_generation_judge_calls = sum(
                int(result.get("rationality_judge_calls", 0))
                for result in raw_results
            )
            merged_table = _merge_rationality_table(
                generation_dir, rationality_table_path
            )
            rationality_table_size = len(merged_table)

        if archive.volatility_thresholds is None:
            archive.freeze_thresholds(
                [
                    float(run["volatility"])
                    for result in raw_results
                    for run in result["runs"]
                ]
            )

        generation_results = [
            _result_summary(result, archive)
            for result in raw_results
        ]
        for result in generation_results:
            result["generation"] = generation
        for result in raw_results:
            _insert_result(
                result,
                archive,
                generation,
                include_matchup=coevolve,
            )

        _json_write(
            generation_dir / "results.json",
            generation_results,
        )
        _json_write(out_dir / "archive.json", archive.to_dict())

        antagonist_raw_results: list[dict[str, Any]] = []
        antagonist_generation_results: list[dict[str, Any]] = []
        if coevolve:
            assert antagonist_archive is not None
            assert antagonist_precedent is not None
            protagonist_samples = [
                archive.cells[cell].genome
                for cell in sorted(archive.cells)
            ]
            vol_high = (
                archive.volatility_thresholds["mid_max"]
                if archive.volatility_thresholds is not None
                else 0.0
            )
            antagonist_jobs = [
                {
                    "action_cfg": action_cfg,
                    "action_graph_path": (
                        str(action_graph_path)
                        if action_graph_path is not None
                        else None
                    ),
                    "antagonist": antagonist,
                    "antagonist_action_cfg": antagonist_action_cfg,
                    "antagonist_genome": genome.to_dict(),
                    "antagonist_precedent_json": (
                        antagonist_precedent.to_json()
                    ),
                    "genome": (
                        protagonist_samples[
                            index % len(protagonist_samples)
                        ].to_dict()
                        if protagonist_samples
                        else None
                    ),
                    "index": index,
                    "logical_root": str(out_dir),
                    "out_dir": str(
                        generation_dir
                        / "antagonist"
                        / f"ind-{index}"
                    ),
                    "parents": parents,
                    "precedent_json": precedent.to_json(),
                    "protagonist": protagonist,
                    "qd_cfg": qd_cfg,
                    "rules": rules,
                    "seeds": seeds,
                    "subjects_dir": str(subjects_dir),
                    "vol_high": vol_high,
                    "world_path": str(world_path),
                }
                for index, (genome, parents) in enumerate(
                    antagonist_population
                )
            ]
            for job in antagonist_jobs:
                job["record_explanations"] = bool(cfg.get("record_explanations", False))
                job["target_ending"] = world_model.target_ending
            if observer is not None:
                observer.bind(antagonist_jobs, generation, "antagonist")
            antagonist_raw_results = _evaluate_jobs(antagonist_jobs, processes, observer)

            if antagonist_archive.volatility_thresholds is None:
                antagonist_archive.freeze_thresholds(
                    [
                        float(run["volatility"])
                        for result in antagonist_raw_results
                        for run in result["runs"]
                    ]
                )

            antagonist_generation_results = [
                _result_summary(
                    result,
                    antagonist_archive,
                    role="antagonist",
                )
                for result in antagonist_raw_results
            ]
            for result in antagonist_generation_results:
                result["generation"] = generation
            for result in antagonist_raw_results:
                _insert_result(
                    result,
                    antagonist_archive,
                    generation,
                    role="antagonist",
                    include_matchup=True,
                )

            _json_write(
                generation_dir / "results.antagonist.json",
                antagonist_generation_results,
            )
            _json_write(out_dir / "archive_antagonist.json", antagonist_archive.to_dict())

        reached_best = [
            _best_reached(result) for result in raw_results
        ]
        reached_best = [
            value for value in reached_best if value is not None
        ]
        generation_dissimilarity = sequence_dissimilarity(
            [
                [
                    tuple(str(part) for part in value)
                    for value in run["effective_sequence"]
                ]
                for run in reached_best
            ]
        )
        reached_runs = sum(
            bool(run["reached"])
            for result in raw_results
            for run in result["runs"]
        )
        total_runs = sum(
            len(result["runs"]) for result in raw_results
        )
        average_quality = (
            sum(elite.quality for elite in archive.cells.values())
            / len(archive.cells)
            if archive.cells
            else None
        )
        archive_dissimilarity = _archive_dissimilarity(
            archive,
            out_dir,
        )
        lineage_summary = _generation_lineage_summary(raw_results)
        generation_summary: dict[str, Any] = {
            "action_share": lineage_summary["action_share"],
            "allies_mean_at_contest": lineage_summary["allies_mean_at_contest"],
            "allies_mean_final": lineage_summary["allies_mean_final"],
            "archive_dissimilarity": archive_dissimilarity,
            "average_archive_quality": average_quality,
            "contest_rate": lineage_summary["contest_rate"],
            "generation": generation,
            "generation_dissimilarity": generation_dissimilarity,
            "occupied_cells": len(archive.cells),
            "reach_rate": reached_runs / total_runs,
        }
        if rationality_enabled:
            generation_summary["rationality_judge_calls"] = rationality_generation_judge_calls
            generation_summary["rationality_table_size"] = rationality_table_size

        antagonist_archive_dissimilarity: float | None = None
        if coevolve:
            assert antagonist_archive is not None
            antagonist_best = [
                _best_reached(result, role="antagonist")
                for result in antagonist_raw_results
            ]
            antagonist_best = [
                value
                for value in antagonist_best
                if value is not None
            ]
            antagonist_generation_dissimilarity = (
                sequence_dissimilarity(
                    [
                        [
                            tuple(str(part) for part in value)
                            for value in run[
                                "antagonist_effective_sequence"
                            ]
                        ]
                        for run in antagonist_best
                    ]
                )
            )
            antagonist_reached_runs = sum(
                bool(run["reached"])
                for result in antagonist_raw_results
                for run in result["runs"]
            )
            antagonist_total_runs = sum(
                len(result["runs"])
                for result in antagonist_raw_results
            )
            antagonist_average_quality = (
                sum(
                    elite.quality
                    for elite in antagonist_archive.cells.values()
                )
                / len(antagonist_archive.cells)
                if antagonist_archive.cells
                else None
            )
            antagonist_archive_dissimilarity = (
                _archive_dissimilarity(
                    antagonist_archive,
                    out_dir,
                    subject=antagonist,
                )
            )
            generation_summary.update(
                {
                    "antagonist_archive_dissimilarity": (
                        antagonist_archive_dissimilarity
                    ),
                    "antagonist_average_archive_quality": (
                        antagonist_average_quality
                    ),
                    "antagonist_generation_dissimilarity": (
                        antagonist_generation_dissimilarity
                    ),
                    "antagonist_occupied_cells": len(
                        antagonist_archive.cells
                    ),
                    "antagonist_reach_rate": (
                        antagonist_reached_runs
                        / antagonist_total_runs
                    ),
                }
            )

        summaries.append(generation_summary)

        if observer is not None:
            observer.checkpoint(phase="publishing")
        _prune_layers(raw_results, out_dir, keep, observer=observer)
        if coevolve:
            _prune_layers(
                antagonist_raw_results,
                out_dir,
                keep,
                role="antagonist",
                observer=observer,
            )

        summary_payload: dict[str, Any] = {
            "final_archive_dissimilarity": archive_dissimilarity,
            "generations": summaries,
            "keep": keep,
            "seed_base": seed_base,
            "seed_count": seed_count,
            "seeds": seeds,
            "target_ending": world_model.target_ending,
        }
        if meta_evolution:
            summary_payload["meta_evolution"] = True
        if coevolve:
            summary_payload.update(
                {
                    "coevolve": True,
                    "final_antagonist_archive_dissimilarity": (
                        antagonist_archive_dissimilarity
                    ),
                }
            )
        _json_write(out_dir / "summary.json", summary_payload)
        if observer is not None:
            observer.publish(generation, archive, antagonist_archive, summary_payload)

        previous_results = generation_results
        if coevolve:
            previous_antagonist_results = (
                antagonist_generation_results
            )

    return archive
