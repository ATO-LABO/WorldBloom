"""Deterministic MAP-Elites evolution around the simulation engine."""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
import re
import shutil
import sys
import uuid
import multiprocessing
import random
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

from engine.sim import Simulation, _engine_source_hash
from engine.subject import Subject
from engine.world import World
from gapengine import gpu_guard
from gapengine.genome import Genome
from gapengine.knowledge_text import (
    load_candidate_labels,
    load_common_knowledge,
    load_describe_negotiate_offer,
    load_describe_trial_grants,
    load_key_items,
)
from gapengine.ollama import DEFAULT_BASE_URL as RATIONALITY_DEFAULT_BASE_URL
from gapengine.ollama import DEFAULT_MODEL as RATIONALITY_DEFAULT_MODEL
from gapengine.policy import Policy
from gapengine.precedent import PrecedentTable, from_runs, load_canon
from gapengine.world_demand import build_report as build_world_demand_report
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
from gapengine.rationality import (
    FakeJudge,
    NullJudge,
    OllamaLogprobJudge,
    Rationality,
    RationalityTable,
)
from gapengine.route import Route, load_route_config
from gapengine.seed_genomes import load as load_seed_genomes, reconcile as reconcile_seed_genome


_ENGINE_DIR = Path(__file__).resolve().parents[1] / "engine"
_GAPENGINE_DIR = Path(__file__).resolve().parent


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
    picked by ``rationality_cfg["backend"]`` ("ollama" or "none" -- "fake"
    is a network-free deterministic judge, reachable only by constructing a
    cfg dict directly (not exposed on scripts/evolve.py's CLI), for tests
    that need to exercise this wiring without Ollama/a GPU -- Opus review
    WB-JEV-001 Stage 2 P3/P4 item 4)."""

    backend = str(rationality_cfg.get("backend", "none"))
    if backend == "ollama":
        return OllamaLogprobJudge(
            model=str(rationality_cfg.get("model", RATIONALITY_DEFAULT_MODEL)),
            base_url=str(rationality_cfg.get("base_url", RATIONALITY_DEFAULT_BASE_URL)),
            timeout=float(rationality_cfg.get("timeout", 300.0)),
            method=str(rationality_cfg.get("method", "noul")),
            thermal_guard=rationality_cfg.get("thermal_guard"),
            num_ctx=rationality_cfg.get("num_ctx"),
        )
    if backend == "fake":
        return FakeJudge()
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
    temperature 0, so every writer computes the same value for it.

    Scans both ``ind-*/`` (protagonist-evaluation jobs) and
    ``antagonist/ind-*/`` (coevolve's antagonist-evaluation jobs, which
    reuse the same protagonist genomes under a *different* Policy instance
    -- WB-JEV-001 Stage 2 Opus review R4). Safe to call more than once per
    generation (idempotent) -- coevolve calls it again after the antagonist
    pass so that pass's own new entries land in the master table too.

    A malformed line (partial write, corrupt JSON, a row missing "key"/"p")
    is skipped rather than aborting the whole merge (Opus review P5) --
    losing one row just means that one (context, candidate) pair gets
    re-scored next time it comes up, which is harmless at temperature 0."""

    table = RationalityTable.load(table_path)
    paths = sorted(generation_dir.glob("ind-*/rationality-new.jsonl")) + sorted(
        generation_dir.glob("antagonist/ind-*/rationality-new.jsonl")
    )
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                table.update({str(row["key"]): float(row["p"])})
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
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


def _rationality_backend_cfg(
    cfg: Mapping[str, Any], template_dir: Path
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """The shared read of rationality.yaml's defaults plus cfg["rationality"]'s
    per-field overrides (WB-JEV-001): (rationality_yaml, rationality_yaml_backend,
    rationality_override). Used by both _evolve() (to build the per-job
    rationality_cfg) and _rationality_lease_params() (to decide whether this
    run's judge will call Ollama and so needs the GPU lease), so the two can
    never disagree about what "kappa>0, backend=ollama" means."""

    rationality_yaml = dict(_load_yaml(template_dir / "rationality.yaml", {}) or {})
    rationality_yaml_backend = dict(rationality_yaml.get("backend") or {})
    rationality_override = dict(cfg.get("rationality") or {})
    return rationality_yaml, rationality_yaml_backend, rationality_override


def route_cfg_override(route_rho: float | None) -> dict[str, Any]:
    """Builds the nested ``cfg["route"]`` override ``_route_cfg()`` reads
    (``{"rho": ...}``) from the flat ``route_rho`` key shared by
    scripts/evolve.py's ``--route-rho`` argument and the UI's evolution
    config (execution/configs.py's ``route_rho``). One place to do this
    nesting so every caller agrees on it -- the frozen UI adapter
    (execution/evolution_worker.py) used to pass its flat manifest straight
    through, so _route_cfg() never saw the override and a run's route_rho
    setting was silently ignored (WB-ROUTE-001 S4 bugfix).

    See ``rationality_cfg_override`` below for kappa/rationality_*, which had
    the identical bug and is fixed the same way (scope widened by user
    decision after the route_rho fix landed)."""

    return {"rho": route_rho}


def rationality_cfg_override(flat: Mapping[str, Any]) -> dict[str, Any]:
    """Builds the nested ``cfg["rationality"]`` override
    ``_rationality_backend_cfg()`` reads, from the flat keys shared by
    scripts/evolve.py's argparse dests (pass ``vars(args)``) and the UI's
    evolution config (execution/configs.py's ``normalize()``, pass
    ``manifest["evolution"]`` or ``config["evolution"]``): ``kappa``,
    ``rationality_backend``, ``rationality_method``, ``rationality_model``,
    ``rationality_num_ctx``, ``rationality_table``, ``rationality_max_calls``.

    Same bug and same fix shape as ``route_cfg_override``: the frozen UI
    adapter (execution/evolution_worker.py) used to spread its flat manifest
    straight into cfg, so a job's kappa/rationality_* settings never reached
    the nested shape _rationality_backend_cfg() reads and were silently
    ignored (WB-ROUTE-001 S4 follow-up). ``rationality_table`` from the flat
    source is always None here (execution/configs.py's normalize() never
    accepts it from the API) -- callers that computed a real persisted-table
    path themselves (execution/configs.py's prepare_run(), for the shared
    Ollama judgment table) must overwrite the returned dict's "table" key."""

    return {
        "kappa": flat.get("kappa"),
        "backend": flat.get("rationality_backend"),
        "method": flat.get("rationality_method"),
        "model": flat.get("rationality_model"),
        "num_ctx": flat.get("rationality_num_ctx"),
        "table": flat.get("rationality_table"),
        "max_judge_calls_per_run": flat.get("rationality_max_calls"),
    }


def _route_cfg(
    cfg: Mapping[str, Any], template_dir: Path
) -> dict[str, Any] | None:
    """WB-ROUTE-001 S1 §3: templates/<genre>/route.yaml's own ``rho`` (S0
    default 0.0), overridden by ``cfg["route"]["rho"]``
    (scripts/evolve.py's ``--route-rho``) -- mirrors
    ``_rationality_backend_cfg``'s "template default, cfg override" shape,
    but route has no backend/table to build, just the multiplier config
    ``gapengine.route.Route.from_config`` already knows how to read.

    Returns None only when rho<=0 (route disabled -- byte-identical to a
    route-free run, plan §2). Raises when rho>0 is requested for a template
    with no route.yaml at all: silently ignoring a rho request would leave
    an experimenter believing route was applied when it never ran."""

    route_yaml = load_route_config(template_dir)
    override = dict(cfg.get("route") or {})
    rho_override = override.get("rho")
    if rho_override is not None:
        rho = float(rho_override)
        # S1 review 1 recommended fix: an explicit --route-rho 0 (disabling
        # route, the template default) must never error just because the
        # template has no route.yaml -- only an actual rho>0 request needs
        # one to apply.
        if rho > 0.0 and route_yaml is None:
            raise ValueError(
                f"route.rho={rho} requested but {template_dir} has no "
                "route.yaml (route stays disabled for this template)"
            )
    elif route_yaml is not None:
        rho = float(route_yaml["rho"])
    else:
        rho = 0.0
    if rho <= 0.0:
        return None
    base = dict(route_yaml or {})
    base["rho"] = rho
    return base


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
    rationality_budget_exhausted_runs = 0
    rationality_judge_disabled_runs = 0
    rationality_total_thermal_wait_seconds = 0.0
    if rationality_cfg is not None:
        rationality_table = RationalityTable.load(
            Path(str(job["rationality_table_path"]))
        )
        rationality_judge = _build_rationality_judge(rationality_cfg)
        rationality_new_path = out_dir / "rationality-new.jsonl"

    # WB-ROUTE-001 S1 §3: unlike rationality, route carries no per-run
    # state (no table, no judge calls) -- one Route built once per job is
    # reused unchanged across every seed.
    route_cfg = job.get("route_cfg")
    route = Route.from_config(route_cfg) if route_cfg is not None else None

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
                describe_trial_grants=bool(
                    rationality_cfg.get("describe_trial_grants", False)
                ),
                candidate_labels=rationality_cfg.get("candidate_labels", {}),
                describe_negotiate_offer=bool(
                    rationality_cfg.get("describe_negotiate_offer", False)
                ),
            )

        policies: dict[str, Policy] = {}
        if genome is not None:
            policies[protagonist] = Policy(
                genome,
                precedent,
                rules,
                cfg=action_cfg,
                rationality=rationality,
                route=route,
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

        rationality_seed_meta: dict[str, Any] | None = None
        if rationality is not None:
            assert rationality_new_path is not None
            _append_rationality_entries(rationality_new_path, rationality.new_entries)
            rationality_seed_meta = rationality.meta
            rationality_total_judge_calls += rationality_seed_meta["judge_calls"]
            if rationality_seed_meta.get("budget_exhausted"):
                rationality_budget_exhausted_runs += 1
            if rationality_seed_meta.get("judge_disabled"):
                rationality_judge_disabled_runs += 1
            rationality_total_thermal_wait_seconds += float(
                rationality_seed_meta.get("thermal_wait_seconds", 0.0)
            )

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
        # Only added when rationality actually ran this seed (Opus review
        # R1/P1): kappa<=0 (the template default) must leave results.json
        # byte-identical to a pre-Stage-2 run.
        if rationality_seed_meta is not None:
            run_result["rationality_judge_calls"] = rationality_seed_meta["judge_calls"]
            if rationality_seed_meta.get("budget_exhausted"):
                run_result["rationality_budget_exhausted"] = True
            if rationality_seed_meta.get("judge_disabled"):
                run_result["rationality_judge_disabled"] = True
            if rationality_seed_meta.get("thermal_wait_seconds"):
                run_result["rationality_thermal_wait_seconds"] = rationality_seed_meta[
                    "thermal_wait_seconds"
                ]

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
        result["rationality_budget_exhausted_runs"] = rationality_budget_exhausted_runs
        result["rationality_judge_disabled_runs"] = rationality_judge_disabled_runs
        result["rationality_thermal_wait_seconds"] = rationality_total_thermal_wait_seconds
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


def _rng_state_to_json(rng: random.Random) -> Any:
    """``random.Random.getstate()`` is a tuple of (possibly nested) tuples;
    JSON has no tuple type, so recursively turn every tuple into a list for
    ``_json_write`` (WB-GA-RESUME). ``_rng_state_from_json`` is the inverse."""

    def convert(value: Any) -> Any:
        return [convert(item) for item in value] if isinstance(value, tuple) else value

    return convert(rng.getstate())


def _rng_state_from_json(value: Any) -> tuple[Any, ...]:
    def convert(item: Any) -> Any:
        return tuple(convert(x) for x in item) if isinstance(item, list) else item

    return convert(value)


def _content_fingerprint(project_dir: Path, template_dir: Path) -> str:
    """Hash of every project/template file's *content* (WB-GA-RESUME) --
    moving the project/template directory elsewhere must not change this,
    only editing a project.yaml/subject/template file should. Covers
    project_dir/world.yaml, project_dir/subjects/*.yaml, and every file
    under template_dir (rules.yaml, qd.yaml, rationality.yaml, canon.yaml,
    action graphs, ...)."""

    entries: list[tuple[str, bytes]] = []
    world_path = project_dir / "world.yaml"
    if world_path.is_file():
        entries.append(("project/world.yaml", world_path.read_bytes()))
    subjects_dir = project_dir / "subjects"
    if subjects_dir.is_dir():
        for path in sorted(subjects_dir.glob("*.yaml"), key=lambda value: value.name):
            entries.append((f"project/subjects/{path.name}", path.read_bytes()))
    if template_dir.is_dir():
        for path in sorted(
            (candidate for candidate in template_dir.rglob("*") if candidate.is_file()),
            key=lambda value: value.relative_to(template_dir).as_posix(),
        ):
            label = f"template/{path.relative_to(template_dir).as_posix()}"
            entries.append((label, path.read_bytes()))

    digest = hashlib.sha256()
    for label, data in entries:
        digest.update(label.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(hashlib.sha256(data).digest())
    return digest.hexdigest()


def _cfg_fingerprint(
    *,
    project_dir: Path,
    template_dir: Path,
    population_size: int,
    seed_count: int,
    seed_base: int,
    ga_seed: int,
    keep: str,
    coevolve: bool,
    meta_evolution: bool,
    target_ending: Any,
    record_explanations: bool,
    rationality_cfg: Mapping[str, Any] | None,
    route_cfg: Mapping[str, Any] | None = None,
    seed_genomes_sha256: str | None = None,
) -> str:
    """WB-GA-RESUME: sha256 of every setting that changes what the GA
    computes -- resuming with a different value here is a bug (or a
    different experiment), so it raises rather than silently continuing.
    Deliberately excludes generations/out/processes and rationality's
    table path/gpu_lease_wait_seconds/thermal_guard: those are operational
    knobs that never change a run's results (plan §2.1)."""

    payload: dict[str, Any] = {
        "content_hash": _content_fingerprint(project_dir, template_dir),
        "coevolve": coevolve,
        "ga_seed": ga_seed,
        "keep": keep,
        "meta_evolution": meta_evolution,
        "population": population_size,
        "record_explanations": record_explanations,
        "seed_base": seed_base,
        "seeds": seed_count,
        "target_ending": target_ending,
    }
    if rationality_cfg is not None:
        payload["rationality"] = {
            "backend": rationality_cfg["backend"],
            "kappa": rationality_cfg["kappa"],
            "max_judge_calls": rationality_cfg.get("max_judge_calls"),
            "method": rationality_cfg["method"],
            "model": rationality_cfg["model"],
        }
        # WB-JEV-003: only included when set, so a run with no num_ctx
        # override (the common case, and every pre-WB-JEV-003 experiment)
        # fingerprints identically to before this field existed -- an
        # in-flight experiment's ga_state.json must keep resuming.
        num_ctx = rationality_cfg.get("num_ctx")
        if num_ctx is not None:
            payload["rationality"]["num_ctx"] = num_ctx
    if route_cfg is not None:
        route_obj = Route.from_config(route_cfg)
        payload["route"] = {
            "rho": route_cfg["rho"],
            "multipliers_hash": route_obj.multipliers_hash(),
            # WB-ROUTE-001 S2 §5: distinguishes two rho>0 runs whose
            # motive table/gene_affinity differ but whose multipliers_hash
            # happens to match.
            "gene_affinity": route_obj.gene_affinity,
            "motives_hash": route_obj.motives_hash(),
        }
    if seed_genomes_sha256 is not None:
        payload["seed_genomes"] = seed_genomes_sha256
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _clear_stale_generations(out_dir: Path, start_generation: int) -> None:
    """WB-GA-RESUME: a run that crashed mid-generation leaves ``g<N>``
    (N == the last recorded ``completed_generations``, or later) half
    written -- delete just those directories before recomputing them.
    Every candidate is resolved and checked to still be out_dir's own
    direct child before it is ever removed."""

    if not out_dir.is_dir():
        return
    resolved_out = out_dir.resolve()
    for path in sorted(out_dir.iterdir()):
        if not path.is_dir():
            continue
        match = re.fullmatch(r"g(\d+)", path.name)
        if match is None or int(match.group(1)) < start_generation:
            continue
        resolved_path = path.resolve()
        if resolved_path.parent != resolved_out:
            continue
        shutil.rmtree(resolved_path)


def _refuse_stale_archive(
    archive: Archive,
    start_generation: int,
    *,
    out_dir: Path,
    state_path: Path,
    role: str = "protagonist",
) -> None:
    """WB-GA-RESUME backward-compat path (a version-1 ga_state.json has no
    embedded archive, so the archive is loaded from archive.json instead):
    each generation writes archive.json *before* ga_state.json, so a crash
    between those two writes can leave archive.json holding elites from a
    generation the checkpoint never recorded as completed -- exactly the
    generation whose layers.jsonl _clear_stale_generations() is about to
    delete. Loading that archive silently would keep cells pointing at
    files that no longer exist; refuse instead (Opus review)."""

    ahead = sorted(
        {
            elite.generation
            for elite in archive.cells.values()
            if elite.generation >= start_generation
        }
    )
    if not ahead:
        return
    label = "archive_antagonist.json" if role == "antagonist" else "archive.json"
    raise ValueError(
        f"resume失敗: {out_dir} の {label} に、チェックポイント"
        f"（{state_path.name} の completed_generations={start_generation}）"
        f"より先に進んだ世代{ahead}のエリートが残っています。"
        "<out> を作り直すか、その世代を巻き戻すこと。"
    )


def _evolve(cfg: Mapping[str, Any], *, observer=None) -> Archive:
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
    world_expansion = str(cfg.get("world_expansion", "off"))
    resume = bool(cfg.get("resume", False))
    resume_allow_code_change = bool(cfg.get("resume_allow_code_change", False))
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
    if world_expansion not in {"off", "detect", "expand"}:
        raise ValueError("world_expansion must be one of: off, detect, expand")

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

    # WB-WORLDGROW-001 stage 5b: seed_genomes (--seed-genomes) carries only
    # a prior experiment's final-archive genomes into this run's generation
    # 0 -- no precedent, no scores, no volatility thresholds. Off (the
    # default, None) must stay byte-identical to a pre-5b run: no fingerprint
    # payload key, no seed_cell, no summary.json key.
    seed_genomes_path = cfg.get("seed_genomes")
    seed_pool: list[Genome] = []
    seed_sha256: str | None = None
    seed_source: dict[str, Any] | None = None
    if seed_genomes_path is not None:
        raw_seed_bytes = Path(str(seed_genomes_path)).read_bytes()
        seed_sha256 = hashlib.sha256(raw_seed_bytes).hexdigest()
        seed_doc = load_seed_genomes(seed_genomes_path)
        seed_source = dict(seed_doc.get("source") or {})
        seed_cells_order = [str(entry["cell"]) for entry in seed_doc["genomes"]]
        seed_pool = [
            reconcile_seed_genome(entry["genome"], rule_ids=rule_ids)
            for entry in seed_doc["genomes"]
        ]

    # Read once, raw: World.from_yaml below parses world_path into typed
    # attributes and drops any "expansion" key -- summary_payload needs the
    # raw applied-patch ids (WB-WORLDGROW-001 stage 3a), not engine state.
    raw_world_patches = (
        (_load_yaml(world_path, {}).get("expansion") or {}).get("patches") or []
    )
    world_patch_ids = [
        p["id"] for p in raw_world_patches if isinstance(p, dict) and "id" in p
    ]

    # WB-JEV-001 Stage 2: rationality.yaml's kappa/method/backend are the
    # template's defaults; cfg["rationality"] (scripts/evolve.py's
    # --kappa/--rationality-backend/... CLI flags) overrides individual
    # fields. kappa<=0 (the momotaro template's default) disables the whole
    # apparatus -- no table file, no per-job rationality_cfg, no judge calls,
    # no "rationality" header key, no p_rat/m_rat meta -- so pre-Stage-2 runs
    # stay byte-identical (plan §0/§1.6).
    rationality_yaml, rationality_yaml_backend, rationality_override = (
        _rationality_backend_cfg(cfg, template_dir)
    )

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
            "max_judge_calls": _rationality_pick(
                "max_judge_calls_per_run",
                rationality_yaml.get("max_judge_calls_per_run"),
            ),
            # WB-JEV-003: cfg override (--rationality-num-ctx) -> rationality.yaml's
            # backend.num_ctx -> None (Ollama's own default). Lives under
            # rationality.yaml's backend, like model/base_url/timeout.
            "num_ctx": _rationality_pick(
                "num_ctx", rationality_yaml_backend.get("num_ctx")
            ),
            # 2026-09-19 thermal-guard addendum: {"max_temp",
            # "cooldown_seconds", "check_every"} or None (disabled). Lives
            # under rationality.yaml's backend: like model/base_url/timeout.
            "thermal_guard": _rationality_pick(
                "thermal_guard", rationality_yaml_backend.get("thermal_guard")
            ),
            "common_knowledge": load_common_knowledge(template_dir),
            "key_items": load_key_items(template_dir),
            "describe_trial_grants": load_describe_trial_grants(template_dir),
            "candidate_labels": load_candidate_labels(template_dir),
            "describe_negotiate_offer": load_describe_negotiate_offer(template_dir),
        }
        rationality_table_path = Path(
            str(rationality_override.get("table") or (out_dir / "rationality.json"))
        )

    # WB-ROUTE-001 S1 §3: rho<=0 (the template default) is None here, so a
    # rho=0 run attaches no route_cfg to any job and stays byte-identical
    # to a pre-S1 run (plan §2).
    route_cfg = _route_cfg(cfg, template_dir)
    route_enabled = route_cfg is not None
    route_object = Route.from_config(route_cfg) if route_cfg is not None else None
    route_multipliers_hash = route_object.multipliers_hash() if route_object is not None else None
    # WB-ROUTE-001 S2 §5: motives.yaml hash + gene_affinity alongside
    # multipliers_hash, both None/0.0 when the template has no motive table
    # (S1-identical).
    route_motives_hash = route_object.motives_hash() if route_object is not None else None
    route_gene_affinity = route_object.gene_affinity if route_object is not None else None

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

    # WB-GA-RESUME: the fingerprint covers every setting that changes what
    # the GA computes (plan §2.1); resuming a run whose fingerprint or
    # engine differs from the one recorded in ga_state.json is refused
    # below rather than silently continuing with mismatched assumptions.
    record_explanations = bool(cfg.get("record_explanations", False))
    cfg_fingerprint = _cfg_fingerprint(
        project_dir=project_dir,
        template_dir=template_dir,
        population_size=population_size,
        seed_count=seed_count,
        seed_base=seed_base,
        ga_seed=ga_seed,
        keep=keep,
        coevolve=coevolve,
        meta_evolution=meta_evolution,
        target_ending=world_model.target_ending,
        record_explanations=record_explanations,
        rationality_cfg=rationality_cfg,
        route_cfg=route_cfg,
        seed_genomes_sha256=seed_sha256,
    )
    engine_hash = _engine_source_hash(_ENGINE_DIR)
    gapengine_hash = _engine_source_hash(_GAPENGINE_DIR)

    state_path = out_dir / "ga_state.json"
    start_generation = 0
    archive = Archive()
    antagonist_archive = Archive() if coevolve else None
    summaries: list[dict[str, Any]] = []
    previous_results: list[dict[str, Any]] = []
    previous_antagonist_results: list[dict[str, Any]] = []
    if resume:
        if state_path.is_file():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state_version = int(state.get("version", 1))
            if state_version not in (1, 2):
                raise ValueError(
                    "Cannot resume "
                    + str(out_dir)
                    + f": unknown {state_path.name} version {state_version}"
                )
            if state.get("cfg_fingerprint") != cfg_fingerprint:
                raise ValueError(
                    "Cannot resume "
                    + str(out_dir)
                    + ": cfg_fingerprint no longer matches the run recorded in "
                    + str(state_path)
                )
            # engine_hash/gapengine_hash: absent from a version-1 state
            # (WB-GA-RESUME follow-up) means "not checked" rather than
            # "mismatched" -- a version-1 checkpoint never recorded
            # gapengine_hash at all. resume_allow_code_change downgrades
            # both to a warning for an intentional mid-run code change.
            code_mismatches = [
                name
                for name, expected in (
                    ("engine_hash", engine_hash),
                    ("gapengine_hash", gapengine_hash),
                )
                if name in state and state[name] != expected
            ]
            if code_mismatches:
                message = (
                    "Cannot resume "
                    + str(out_dir)
                    + ": "
                    + ", ".join(code_mismatches)
                    + " no longer match the run recorded in "
                    + str(state_path)
                )
                if resume_allow_code_change:
                    print(
                        "WB-GA-RESUME: continuing despite changed "
                        + ", ".join(code_mismatches)
                        + " (resume_allow_code_change=True): "
                        + str(out_dir),
                        file=sys.stderr,
                    )
                else:
                    raise ValueError(message)
            start_generation = int(state["completed_generations"])
            if state_version >= 2 and "archive" in state:
                # The checkpoint's own embedded archive is the ground
                # truth (written atomically with everything else here) --
                # archive.json is kept only as a separate, human-facing
                # display copy and is never read back.
                archive = Archive.from_dict(state["archive"])
                if coevolve:
                    antagonist_archive = Archive.from_dict(
                        state["antagonist_archive"]
                    )
            else:
                archive = Archive.load(out_dir / "archive.json")
                _refuse_stale_archive(
                    archive,
                    start_generation,
                    out_dir=out_dir,
                    state_path=state_path,
                )
                if coevolve:
                    antagonist_archive = Archive.load(
                        out_dir / "archive_antagonist.json"
                    )
                    _refuse_stale_archive(
                        antagonist_archive,
                        start_generation,
                        out_dir=out_dir,
                        state_path=state_path,
                        role="antagonist",
                    )
            # summary.json is written before ga_state.json each generation
            # (display-only, like archive.json), so a crash in that window
            # can leave one extra, uncommitted entry past start_generation
            # -- drop anything the checkpoint doesn't vouch for, regardless
            # of state_version, rather than duplicating that generation.
            summaries = list(
                json.loads(
                    (out_dir / "summary.json").read_text(encoding="utf-8")
                )["generations"]
            )[:start_generation]
            previous_results = list(state["previous_results"])
            if coevolve:
                previous_antagonist_results = list(
                    state.get("previous_antagonist_results", [])
                )
            ga_rng.setstate(_rng_state_from_json(state["ga_rng_state"]))
            _clear_stale_generations(out_dir, start_generation)
        else:
            generation_indices = sorted(
                int(match.group(1))
                for match in (
                    re.fullmatch(r"g(\d+)", path.name)
                    for path in (out_dir.iterdir() if out_dir.is_dir() else ())
                    if path.is_dir()
                )
                if match is not None
            )
            if not generation_indices:
                pass  # brand-new run: <out> is empty (or doesn't exist yet)
            elif generation_indices == [0]:
                # Generation 0 crashed before its own first checkpoint ever
                # existed, so nothing completed is at risk -- clear the
                # leftover and start over instead of refusing forever (the
                # caller always launches with --resume, even for a
                # brand-new experiment).
                _clear_stale_generations(out_dir, 0)
            else:
                raise ValueError(
                    "resume失敗: "
                    + str(out_dir)
                    + " に "
                    + state_path.name
                    + " がありません"
                    + "（WB-GA-RESUME以前の実行か、stateが失われています）"
                )

    seed_cell_by_index: dict[int, str] = {}
    if start_generation == 0:
        seeded = seed_pool[:population_size]
        seed_cell_by_index = {
            index: seed_cells_order[index] for index in range(len(seeded))
        }
        population = [(genome, []) for genome in seeded] + [
            (
                Genome.random(
                    ga_rng,
                    rule_ids=rule_ids,
                ),
                [],
            )
            for _ in range(population_size - len(seeded))
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
    else:
        # Set inside the loop below by _next_population() on its first
        # iteration (generation == start_generation > 0) -- building a
        # random initial population here would consume ga_rng draws that
        # the checkpointed state never accounted for (plan §2.2 step 2).
        population = []
        antagonist_population = []

    for generation in range(start_generation, generations):
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
                    **(
                        {"seed_cell": seed_cell_by_index[index]}
                        if generation == 0 and index in seed_cell_by_index
                        else {}
                    ),
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
            if route_enabled:
                job["route_cfg"] = route_cfg
        if observer is not None:
            observer.bind(jobs, generation, "protagonist")
        raw_results = _evaluate_jobs(jobs, processes, observer)

        # WB-JEV-001 Stage 2 (Opus review R4): merged here (before the
        # antagonist pass is built) so a coevolve antagonist job -- which
        # evaluates the *same* protagonist genomes under its own Policy
        # instance -- reuses this generation's protagonist-pass discoveries
        # instead of starting cold. judge_calls/table_size for
        # generation_summary are computed after *both* passes below, once
        # the merge has folded in whichever pass ran second too.
        if rationality_enabled:
            assert rationality_table_path is not None
            _merge_rationality_table(generation_dir, rationality_table_path)

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
                # Opus review R4: the protagonist sample evaluated here gets
                # the *same* rationality_cfg as the main protagonist pass,
                # so the same genome never ends up with m_rat applied in
                # only one of the two passes that evaluate it. The
                # antagonist's own Policy is never given a Rationality
                # (run_individual only attaches it to `policies[protagonist]`).
                if rationality_enabled:
                    job["rationality_cfg"] = rationality_cfg
                    job["rationality_table_path"] = str(rationality_table_path)
                # Same reasoning as rationality_cfg above: the sampled
                # protagonist genome here must see the same route weighting
                # it gets in the main protagonist pass.
                if route_enabled:
                    job["route_cfg"] = route_cfg
            if observer is not None:
                observer.bind(antagonist_jobs, generation, "antagonist")
            antagonist_raw_results = _evaluate_jobs(antagonist_jobs, processes, observer)

            if rationality_enabled:
                assert rationality_table_path is not None
                _merge_rationality_table(generation_dir, rationality_table_path)

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
            sum(archive.cells[cell].quality for cell in sorted(archive.cells))
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
            # Opus review R4: summed across both passes -- coevolve's
            # antagonist pass re-evaluates the same protagonist genomes
            # under their own Rationality now too, so its judge calls count
            # here as well. Read back after both merges above, so this is
            # the generation's *final* table state either way.
            generation_summary["rationality_judge_calls"] = sum(
                int(result.get("rationality_judge_calls", 0))
                for result in (*raw_results, *antagonist_raw_results)
            )
            generation_summary["rationality_table_size"] = len(
                RationalityTable.load(rationality_table_path)
            )
            generation_summary["rationality_thermal_wait_seconds"] = sum(
                float(result.get("rationality_thermal_wait_seconds", 0.0))
                for result in (*raw_results, *antagonist_raw_results)
            )
            # Stage 2 re-review item 2: run_individual already tallies these
            # per job (result["rationality_budget_exhausted_runs"] /
            # ["rationality_judge_disabled_runs"]); sum them the same way as
            # judge_calls above so they reach the generation summary too.
            generation_summary["rationality_budget_exhausted_runs"] = sum(
                int(result.get("rationality_budget_exhausted_runs", 0))
                for result in (*raw_results, *antagonist_raw_results)
            )
            generation_summary["rationality_judge_disabled_runs"] = sum(
                int(result.get("rationality_judge_disabled_runs", 0))
                for result in (*raw_results, *antagonist_raw_results)
            )

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
                    antagonist_archive.cells[cell].quality
                    for cell in sorted(antagonist_archive.cells)
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
        if world_expansion != "off":
            summary_payload["world_expansion"] = world_expansion
        if seed_pool:
            summary_payload["seed_genomes"] = {
                "sha256": seed_sha256,
                "used": min(len(seed_pool), population_size),
                "available": len(seed_pool),
                "source": deepcopy(seed_source) if seed_source is not None else None,
            }
        if world_patch_ids:
            summary_payload["world_patches"] = world_patch_ids
            summary_payload["world_expansion_patches"] = raw_world_patches
        if route_enabled:
            # S1 review 1 recommended fix: summary.json is the display-only,
            # human-facing counterpart of the per-run header (already
            # carries route.rho/multipliers_hash, see
            # RouteEvolveWiringTests) -- an experimenter reading only the GA
            # summary had no way to tell route was even on.
            summary_payload["route"] = {
                "rho": route_cfg["rho"],
                "multipliers_hash": route_multipliers_hash,
                "gene_affinity": route_gene_affinity,
                "motives_hash": route_motives_hash,
            }
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

        # WB-GA-RESUME: written every generation (resumed or not) so any
        # run can later be resumed -- a non-resuming run's other output is
        # unaffected (plan §2.1). version 2 (Opus review): the archive is
        # embedded here too, so resuming never depends on archive.json
        # (written separately, earlier, non-atomically with this file) --
        # archive.json/archive_antagonist.json remain as display-only
        # copies of exactly the same data, never read back on resume.
        state_payload: dict[str, Any] = {
            "archive": archive.to_dict(),
            "cfg_fingerprint": cfg_fingerprint,
            "completed_generations": generation + 1,
            "engine_hash": engine_hash,
            "ga_rng_state": _rng_state_to_json(ga_rng),
            "gapengine_hash": gapengine_hash,
            "previous_results": previous_results,
            "version": 2,
        }
        if coevolve:
            state_payload["antagonist_archive"] = antagonist_archive.to_dict()
            state_payload["previous_antagonist_results"] = previous_antagonist_results
        _json_write(out_dir / "ga_state.json", state_payload)

    if world_expansion in ("detect", "expand"):
        # Post-evolution only: never touches sim state, rng, candidate
        # generation or the Policy. Runs after the last summary/publish, and a
        # report that cannot be built must not turn a finished run into a
        # failed job -- the viewer then just says nothing was collected.
        try:
            _json_write(
                out_dir / "world_demand.json",
                build_world_demand_report(out_dir),
            )
        except Exception as error:
            print(f"world_demand report skipped: {error!r}", file=sys.stderr)

    return archive


def _rationality_lease_params(cfg: Mapping[str, Any]) -> tuple[bool, str, float]:
    """(needs_lease, owner, wait_seconds) for evolve()'s thin GPU-lease
    wrapper (WB-JEV-001). needs_lease is True only when kappa>0 and the
    resolved rationality backend is "ollama" -- kappa<=0 and the
    "none"/"fake" backends never touch the GPU, matching _evolve()'s own
    rationality_enabled/backend resolution exactly (both call
    _rationality_backend_cfg)."""

    template_dir = Path(str(cfg["template"])).resolve()
    out_dir = Path(str(cfg["out"])).resolve()
    rationality_yaml, rationality_yaml_backend, rationality_override = (
        _rationality_backend_cfg(cfg, template_dir)
    )

    def _pick(name: str, default: Any) -> Any:
        value = rationality_override.get(name)
        return default if value is None else value

    kappa = float(_pick("kappa", rationality_yaml.get("kappa", 0.0)) or 0.0)
    if kappa <= 0.0:
        return False, "", 0.0
    backend = str(_pick("backend", rationality_yaml_backend.get("type", "none")))
    if backend != "ollama":
        return False, "", 0.0
    wait_seconds = float(
        _pick(
            "gpu_lease_wait_seconds",
            rationality_yaml_backend.get("gpu_lease_wait_seconds", 600),
        )
    )
    return True, f"jev-ga:{out_dir.name}", wait_seconds


def evolve(cfg: Mapping[str, Any], *, observer=None) -> Archive:
    """WB-JEV-001: a thin wrapper around _evolve() that takes the
    machine-wide GPU lease (gapengine.gpu_guard.gpu_lease) around the whole
    run when this run's rationality judge will actually call Ollama
    (kappa>0 and backend=="ollama"). kappa<=0 and the "none"/"fake" backends
    call _evolve() directly -- a complete no-op path, unchanged from before
    WB-JEV-001's GPU guard. A GpuBusy from a held lease is never swallowed:
    it surfaces as a RuntimeError naming the current holder, and _evolve()
    (so no generation) never runs."""

    needs_lease, owner, wait_seconds = _rationality_lease_params(cfg)
    if not needs_lease:
        return _evolve(cfg, observer=observer)
    try:
        with gpu_guard.gpu_lease(owner, wait_seconds=wait_seconds):
            return _evolve(cfg, observer=observer)
    except gpu_guard.GpuBusy as exc:
        raise RuntimeError(
            "GPU is busy for the GA run: held by "
            f"{exc.holder.get('owner', 'unknown')}"
        ) from exc
