"""Frozen UI GA adapter, progress journal and immutable generation publications.

Only the UI-003 supervisor owns job state/termination. This adapter reports
progress and cooperatively stops; it never resumes an old run or launches LLMs.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from execution.provenance import (ConfigError, atomic_json, canonical, contained,
    directory_lock, identifier, publish_directory, read_json, sha256, write_bytes)
from gapengine.evolve import (EvolutionCancelled, _cell_for_run, evolve,
    rationality_cfg_override, route_cfg_override)


def candidate_identity(run_id, event):
    identifier(run_id, "run_id")
    role = event["role"]
    if role not in ("protagonist", "antagonist"):
        raise ValueError("invalid role")
    for key in ("generation", "individual_index", "seed"):
        if type(event[key]) is not int or event[key] < 0:
            raise ValueError("invalid candidate coordinate")
    digest = event["source_log_sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("invalid source hash")
    identity = {"scheme": "recorded-v1", "run_id": run_id, "role": role,
                **{k: event[k] for k in ("generation", "individual_index", "seed")},
                "source_log_sha256": digest}
    return identity, "cand-" + sha256(canonical(identity))


def log_observation(root, relative_path, source_hash, pruned_paths):
    path = contained(root, relative_path)
    try:
        observed = sha256(path.read_bytes())
    except FileNotFoundError:
        return {"relative_path": relative_path,
                "availability": "pruned" if relative_path in pruned_paths else "missing",
                "observed_sha256": None}
    return {"relative_path": relative_path,
            "availability": "present" if observed == source_hash else "stale",
            "observed_sha256": observed}


class EvolutionObserver:
    """File events cross spawn boundaries; a thread polls even inside one seed.

    sink(progress) and cancellation() are optional embedding/test hooks. Failures
    are propagated to the GA thread; they must never silently report success.
    """
    def __init__(self, root, run_id, cfg, *, sink=None, cancellation=None, interval=0.5):
        self.root = Path(root).absolute()
        self.run_id = identifier(run_id, "run_id")
        self.folder = contained(self.root, "evaluation")
        self.events = contained(self.root, "evaluation/events")
        self.cancel = contained(self.root, "evaluation/cancel.json")
        self.folder.mkdir(parents=True, exist_ok=False)
        self.events.mkdir()
        self.generations = int(cfg.get("generations", 20))
        self.population = int(cfg.get("population", 100))
        self.seed_count = int(cfg.get("seeds", 3))
        self.roles = ["protagonist", "antagonist"] if cfg.get("coevolve", False) else ["protagonist"]
        self.sink, self.cancellation, self.interval = sink, cancellation, interval
        self.started = time.monotonic()
        self.completed = {}
        self.seed_starts = {}
        self.seen = set()
        self.individuals = set()
        self.pruned_paths = set()
        self.generation = None
        self.role = None
        self.phase = "preparing"
        self.completed_generations = 0
        self.publication_revision = None
        self.metrics = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._error = None
        self._thread = None
        self._last_progress = None

    def __enter__(self):
        self.poll()
        self._thread = threading.Thread(target=self._monitor, name="ga-progress", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, kind, error, traceback):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        if kind is None:
            self.poll()

    def _monitor(self):
        try:
            while not self._stop.wait(self.interval):
                self.poll()
        except BaseException as error:
            self._error = error
            # Cause seed-boundary exit even if the parent is collecting a pool.
            try:
                self.request_cancel()
            except OSError:
                pass

    def request_cancel(self):
        atomic_json(self.cancel, {"schema_version": 1, "requested": True})

    def _drain(self):
        for name in sorted(set(os.listdir(self.events)) - self.seen):
            if not name.endswith(".json"):
                continue
            event = read_json(self.events / name)
            target = self.completed if event["kind"] == "completed" else self.seed_starts
            target[event["event_id"]] = event
            self.seen.add(name)

    def progress(self):
        total = self.generations * self.population * len(self.roles)
        by_role = {}
        for role in self.roles:
            by_role[role] = {
                "completed_individuals": sum(key[1] == role for key in self.individuals),
                "total_individuals": self.generations * self.population,
                "completed_seeds": sum(e["role"] == role for e in self.completed.values()),
                "total_seeds": self.generations * self.population * self.seed_count}
        active = [{k: e[k] for k in ("generation", "role", "individual_index", "seed")}
                  for key, e in sorted(self.seed_starts.items()) if key not in self.completed]
        return {"schema_version": 1, "phase": self.phase, "generation": self.generation,
                "role": self.role, "completed_generations": self.completed_generations,
                "total_generations": self.generations,
                "completed_individuals": len(self.individuals), "total_individuals": total,
                "completed_seeds": len(self.completed), "total_seeds": total * self.seed_count,
                "elapsed_seconds": round(time.monotonic() - self.started, 1),
                "by_role": by_role, "active_seeds": active,
                "publication_revision": self.publication_revision,
                "metrics": deepcopy(self.metrics), "human_quality": "not_evaluated"}

    def poll(self):
        with self._lock:
            if self._error is not None:
                raise self._error
            if self.cancellation is not None and self.cancellation():
                if not self.cancel.exists():
                    self.request_cancel()
            self._drain()
            progress = self.progress()
            if progress != self._last_progress:
                atomic_json(self.folder / "progress.json", progress)
                if self.sink is not None:
                    self.sink(deepcopy(progress))
                self._last_progress = progress

    def checkpoint(self, **values):
        with self._lock:
            for key, value in values.items():
                setattr(self, key, value)
            self.poll()
            if self.cancel.exists():
                raise EvolutionCancelled("cancel requested")

    def bind(self, jobs, generation, role):
        self.checkpoint(phase="evaluating", generation=generation, role=role)
        for job in jobs:
            job["observation"] = {"generation": generation, "role": role,
                                  "events": str(self.events), "cancel": str(self.cancel)}

    def individual_completed(self, index):
        with self._lock:
            self.individuals.add((self.generation, self.role, index))
            atomic_json(self.folder / "individuals.json", sorted(self.individuals))
            self.poll()

    def pruned(self, relative_path):
        with self._lock:
            contained(self.root, relative_path)
            self.pruned_paths.add(relative_path)
            atomic_json(self.folder / "pruned.json", sorted(self.pruned_paths))

    def publish(self, generation, archive, antagonist_archive, summary):
        with self._lock, directory_lock(self.root):
            self.checkpoint(phase="publishing")
            if generation != self.completed_generations:
                raise ValueError("generation publication is not sequential")
            expected = self.population * self.seed_count * len(self.roles)
            if sum(e["generation"] == generation for e in self.completed.values()) != expected:
                raise ValueError("generation has incomplete seeds")
            if sum(k[0] == generation for k in self.individuals) != self.population * len(self.roles):
                raise ValueError("generation has incomplete individuals")
            revision = generation + 1
            candidates = []
            events = sorted(self.completed.values(), key=lambda e: (
                e["generation"], e["role"], e["individual_index"], e["seed"]))
            for event in events:
                if event["generation"] > generation:
                    continue
                identity, cid = candidate_identity(self.run_id, event)
                role = event["role"]
                role_archive = antagonist_archive if role == "antagonist" else archive
                run = event["run"]
                cell = _cell_for_run(run, role_archive, role=role)
                candidates.append({"candidate_id": cid, "identity": identity,
                    **{k: event[k] for k in ("role", "generation", "individual_index", "seed")},
                    "cell_key": "|".join(cell) if cell is not None else "unclassified",
                    "classification_status": "classified" if cell is not None else "unclassified",
                    "reached": run["reached"], "source_log_sha256": event["source_log_sha256"],
                    "log": log_observation(self.root, run["layers_path"], event["source_log_sha256"], self.pruned_paths),
                    "quality": run["antagonist_quality" if role == "antagonist" else "quality"],
                    "shaped": run["shaped"], "volatility": run["volatility"],
                    "parents": event["parents"],
                    "genome": event["antagonist_genome" if role == "antagonist" else "genome"]})
            if len({c["candidate_id"] for c in candidates}) != len(candidates):
                raise ValueError("duplicate candidate identity")
            published = contained(self.root, "published")
            staging = contained(self.root, "published/.pending-" + uuid.uuid4().hex)
            destination = contained(self.root, f"published/{revision}")
            payloads = {"archive": archive.to_dict(), "summary": summary,
                        "candidates": {"schema_version": 1, "run_id": self.run_id,
                                       "revision": revision, "candidates": candidates}}
            if antagonist_archive is not None:
                payloads["archive_antagonist"] = antagonist_archive.to_dict()
            files = {}
            for name, value in payloads.items():
                data = canonical(value)
                write_bytes(staging / (name + ".json"), data)
                files[name] = {"path": f"published/{revision}/{name}.json", "sha256": sha256(data)}
            manifest = {"schema_version": 1, "run_id": self.run_id, "revision": revision,
                        "completed_generations": revision, "files": files}
            manifest_bytes = canonical(manifest)
            write_bytes(staging / "manifest.json", manifest_bytes)
            self.checkpoint()
            publish_directory(staging, destination)
            self.checkpoint()
            atomic_json(published / "current.json", {"schema_version": 1, "run_id": self.run_id,
                        "revision": revision, "manifest_sha256": sha256(manifest_bytes)})
            self.completed_generations = revision
            self.publication_revision = revision
            self.metrics = deepcopy(summary["generations"][-1])
            self.phase = "generation_completed"
            self.poll()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--control", required=True, type=Path)
    parser.add_argument("--job", required=True)
    args = parser.parse_args(argv)
    from execution.configs import ConfigStore
    from execution import worker
    run = args.run.absolute()
    runtime = Path(__file__).resolve().parents[1]
    if runtime != run / "runtime":
        raise ValueError("adapter must execute from the frozen run runtime")
    manifest = ConfigStore(runtime, args.control, run.parent).verify_run(run.name)
    jobs = contained(args.control, "jobs")
    folder = contained(jobs, identifier(args.job, "job_id"))
    job = worker.read_job(jobs, folder)
    if (manifest["job_id"] != args.job or manifest["run_id"] != run.name or
            job["run_id"] != run.name or job["config_id"] != manifest["config_id"] or job["kind"] != "evolve"):
        raise ValueError("job manifest mismatch")
    config = read_json(run / "config.json")
    # WB-COMPUTE-001: the ⚙ setting recorded at submit wins over the config's
    # frozen value; manifests from before it carry no "processes" key.
    #
    # WB-ROUTE-001 S4 bugfix (scope widened to kappa/rationality_* per user
    # decision): manifest["evolution"] carries route_rho/kappa/rationality_*
    # as flat keys (execution/configs.py's normalize()), but
    # gapengine.evolve._route_cfg()/_rationality_backend_cfg() only read the
    # nested cfg["route"]/cfg["rationality"] shapes scripts/evolve.py's own
    # --route-rho/--kappa/... CLI wiring builds. Spreading manifest["evolution"]
    # straight into cfg (as this used to) means those overrides never reach
    # evolve() -- a run's settings from the UI job screen were silently
    # ignored. route_cfg_override()/rationality_cfg_override() do the same
    # nesting scripts/evolve.py's main() does, so both launch paths agree.
    rationality = rationality_cfg_override(manifest["evolution"])
    # rationality_table is never in manifest["evolution"] itself (normalize()
    # never accepts it from the API) -- prepare_run() computes the real
    # shared-table path separately and records it at manifest["rationality_table"]
    # (None for kappa<=0, or an old manifest predating that field) so the CLI
    # and adapter launch paths land on the same table file.
    rationality["table"] = manifest.get("rationality_table")
    cfg = {**manifest["evolution"],
           "processes": manifest.get("processes", manifest["evolution"]["processes"]),
           "out": run,
           "project": contained(run, "inputs/projects/" + config["project_id"]),
           "template": contained(run, "inputs/templates/" + config["template_id"]),
           "rationality": rationality,
           "route": route_cfg_override(manifest["evolution"].get("route_rho"))}

    def cancelled():
        current = worker.read_job(jobs, folder)
        return current.get("cancel_requested_at") is not None or current["state"] in worker.TERMINAL

    def report(progress):
        worker.change(jobs, folder, job["nonce"], progress=progress,
                      phase=progress["phase"], publication_revision=progress["publication_revision"])

    try:
        with EvolutionObserver(run, run.name, cfg, sink=report, cancellation=cancelled) as observer:
            evolve(cfg, observer=observer)
        return 0
    except EvolutionCancelled:
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
