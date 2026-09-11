"""Deterministic MAP-Elites evolution around the simulation engine."""

from __future__ import annotations

import json
import multiprocessing
import random
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from engine.sim import Simulation
from engine.subject import Subject
from engine.world import World
from gapengine.genome import Genome
from gapengine.policy import Policy
from gapengine.precedent import PrecedentTable, from_runs, load_canon
from gapengine.qd import (
    Archive,
    Descriptor,
    Elite,
    descriptor,
    effective_sequence,
    quality,
    read_rows,
    reached,
    sequence_dissimilarity,
    shaped,
)


def _json_write(path: Path, value: Any) -> None:
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


def _world_meta(world: World) -> dict[str, Any]:
    return {
        "antagonist": world.antagonist,
        "protagonist": world.protagonist,
        "target_ending": world.target_ending,
    }


def run_individual(job: Mapping[str, Any]) -> dict[str, Any]:
    genome = Genome.from_dict(job["genome"])
    seeds = [int(value) for value in job["seeds"]]
    world_path = Path(str(job["world_path"]))
    subjects_dir = Path(str(job["subjects_dir"]))
    out_dir = Path(str(job["out_dir"]))
    logical_root = Path(str(job["logical_root"]))
    protagonist = str(job["protagonist"])
    action_cfg = dict(job["action_cfg"])
    rules = list(job.get("rules", []))
    qd_cfg = dict(job["qd_cfg"])
    precedent = (
        PrecedentTable.from_json(str(job["precedent_json"]))
        if job.get("precedent_json") is not None
        else None
    )

    runs: list[dict[str, Any]] = []
    for seed in seeds:
        world = World.from_yaml(world_path)
        subjects = _load_subjects(subjects_dir)
        seed_dir = out_dir / f"seed-{seed}"
        policy = Policy(
            genome,
            precedent,
            rules,
            cfg=action_cfg,
        )
        layer_path = Simulation(
            seed,
            world,
            subjects,
            seed_dir,
            policies={protagonist: policy},
            precedent=precedent,
        ).run()
        rows = read_rows(layer_path)
        run_descriptor = descriptor(rows, qd_cfg)
        header = rows[0]
        relative_path = layer_path.relative_to(logical_root).as_posix()
        runs.append(
            {
                "category": run_descriptor.category,
                "effective_sequence": [
                    list(value) for value in effective_sequence(rows)
                ],
                "engine_hash": header.get("engine_hash"),
                "layers_path": relative_path,
                "precedent_hash": header.get("precedent_hash"),
                "quality": quality(rows, _world_meta(world)),
                "reached": reached(rows, world.target_ending),
                "seed": seed,
                "shaped": shaped(rows, world),
                "volatility": run_descriptor.volatility,
            }
        )

    return {
        "genome": genome.to_dict(),
        "index": int(job["index"]),
        "parents": list(job.get("parents", [])),
        "runs": runs,
        "shaped": round(
            sum(float(run["shaped"]) for run in runs) / len(runs),
            12,
        ),
    }


def _evaluate_jobs(
    jobs: list[dict[str, Any]],
    processes: int,
) -> list[dict[str, Any]]:
    if processes <= 1:
        return [run_individual(job) for job in jobs]
    context = multiprocessing.get_context("spawn")
    with context.Pool(processes=processes) as pool:
        return list(pool.imap(run_individual, jobs))


def _best_reached(
    result: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    candidates = [
        run for run in result["runs"] if bool(run["reached"])
    ]
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda run: (-float(run["quality"]), int(run["seed"])),
    )[0]


def _cell_for_run(
    run: Mapping[str, Any],
    archive: Archive,
) -> tuple[str, str] | None:
    category = run.get("category")
    if category is None:
        return None
    return (
        str(category),
        archive.bin_for(float(run["volatility"])),
    )


def _result_summary(
    result: Mapping[str, Any],
    archive: Archive,
) -> dict[str, Any]:
    best = _best_reached(result)
    cell = _cell_for_run(best, archive) if best is not None else None
    return {
        "cell": list(cell) if cell is not None else None,
        "classification_status": (
            "unclassified"
            if best is not None and best.get("category") is None
            else "classified"
            if best is not None
            else "not_reached"
        ),
        "genome": result["genome"],
        "index": int(result["index"]),
        "parents": list(result["parents"]),
        "reach_rate": (
            sum(bool(run["reached"]) for run in result["runs"])
            / len(result["runs"])
        ),
        "runs": list(result["runs"]),
        "shaped": float(result["shaped"]),
    }


def _insert_result(
    result: Mapping[str, Any],
    archive: Archive,
    generation: int,
) -> bool:
    best = _best_reached(result)
    if best is None or best.get("category") is None:
        return False

    reach_rate = (
        sum(bool(run["reached"]) for run in result["runs"])
        / len(result["runs"])
    )
    descriptor_value = Descriptor(
        category=str(best["category"]),
        volatility=float(best["volatility"]),
        volatility_bin=archive.bin_for(float(best["volatility"])),
    )
    elite = Elite(
        genome=Genome.from_dict(result["genome"]),
        quality=float(best["quality"]),
        descriptor=descriptor_value,
        reach_rate=reach_rate,
        exemplar={
            "engine_hash": best.get("engine_hash"),
            "layers_path": str(best["layers_path"]),
            "precedent_hash": best.get("precedent_hash"),
            "seed": int(best["seed"]),
        },
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
) -> list[tuple[Genome, list[str]]]:
    pool = _parent_pool(archive, previous)
    population: list[tuple[Genome, list[str]]] = []
    for _ in range(size):
        if not pool or rng.random() < 0.1:
            population.append((Genome.random(rng), []))
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
) -> float | None:
    sequences = [
        effective_sequence(
            read_rows(out_dir / str(archive.cells[cell].exemplar["layers_path"]))
        )
        for cell in sorted(archive.cells)
    ]
    return sequence_dissimilarity(sequences)


def _prune_layers(
    results: list[dict[str, Any]],
    out_dir: Path,
    keep: str,
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
    elif keep == "exemplar":
        for result in results:
            exemplar = _best_reached(result)
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


def evolve(cfg: Mapping[str, Any]) -> Archive:
    project_dir = Path(str(cfg["project"])).resolve()
    template_dir = Path(str(cfg["template"])).resolve()
    out_dir = Path(str(cfg["out"])).resolve()
    world_path = project_dir / "world.yaml"
    subjects_dir = project_dir / "subjects"

    generations = int(cfg.get("generations", 20))
    population_size = int(cfg.get("population", 100))
    seed_count = int(cfg.get("seeds", 3))
    seed_base = int(cfg.get("seed_base", 0))
    ga_seed = int(cfg.get("ga_seed", 1))
    processes = int(cfg.get("processes", 1))
    keep = str(cfg.get("keep", "reached"))
    if generations < 1 or population_size < 1 or seed_count < 1:
        raise ValueError("generations, population, and seeds must be positive")
    if processes < 1:
        raise ValueError("processes must be positive")
    if keep not in {"all", "reached", "exemplar"}:
        raise ValueError("keep must be one of: all, reached, exemplar")

    seeds = list(range(seed_base, seed_base + seed_count))
    ga_rng = random.Random(ga_seed)
    action_cfg = dict(
        _load_yaml(template_dir / "action_graph.yaml", {"nodes": [], "edges": []})
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
    canon = load_canon(template_dir / "canon.yaml")

    archive = Archive()
    summaries: list[dict[str, Any]] = []
    previous_results: list[dict[str, Any]] = []
    population = [
        (Genome.random(ga_rng), [])
        for _ in range(population_size)
    ]

    for generation in range(generations):
        generation_dir = out_dir / f"g{generation}"
        archive_runs = (
            _archive_precedent_paths(archive, out_dir)
            if generation > 0
            else []
        )
        precedent = canon.merge(
            from_runs(archive_runs, protagonist=World.from_yaml(world_path).protagonist)
        )
        precedent_path = generation_dir / "precedent.json"
        _json_write(precedent_path, json.loads(precedent.to_json()))

        if generation > 0:
            population = _next_population(
                population_size,
                archive,
                previous_results,
                ga_rng,
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

        protagonist = World.from_yaml(world_path).protagonist
        jobs = [
            {
                "action_cfg": action_cfg,
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
        raw_results = _evaluate_jobs(jobs, processes)

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
            _insert_result(result, archive, generation)

        _json_write(
            generation_dir / "results.json",
            generation_results,
        )
        archive.save(out_dir / "archive.json")

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
        total_runs = sum(len(result["runs"]) for result in raw_results)
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
        summaries.append(
            {
                "archive_dissimilarity": archive_dissimilarity,
                "average_archive_quality": average_quality,
                "generation": generation,
                "generation_dissimilarity": generation_dissimilarity,
                "occupied_cells": len(archive.cells),
                "reach_rate": reached_runs / total_runs,
            }
        )

        _prune_layers(raw_results, out_dir, keep)

        _json_write(
            out_dir / "summary.json",
            {
                "final_archive_dissimilarity": archive_dissimilarity,
                "generations": summaries,
                "keep": keep,
                "seed_base": seed_base,
                "seed_count": seed_count,
                "seeds": seeds,
            },
        )
        previous_results = generation_results

    return archive
