"""Frozen generation runner. One dispatch per durable attempt, no restart retry."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from execution.output_store import OutputStore
from execution.generation import run_generation, result
from execution.provenance import read_json, contained, ConfigError
from execution.worker import read_job, change, TERMINAL


def credentials(path, backend):
    if backend == "none":
        return {}
    from gapengine.synopsis import _backend_config
    settings = read_json(Path(path)) if path is not None else {}
    if not isinstance(settings, dict):
        raise ValueError("invalid local settings")
    config = _backend_config(settings, backend)
    keys = ("command", "api_key", "base_url", "options", "think", "seed")
    return {key: config[key] for key in keys if key in config}


def run(control, output_id):
    store = OutputStore(control)
    request = store.request(output_id)
    jobs = Path(control) / "jobs"
    folder = contained(jobs, request["job_id"])
    job = read_job(jobs, folder)
    if job.get("output_id") != output_id:
        raise ConfigError("output_id", "ジョブと生成版が一致しません")
    # A second worker invocation cannot send even previously unstarted items.
    # The exclusive marker is permanent; restart recovery is local-only.
    from execution.provenance import write_bytes
    write_bytes(store.folder(output_id) / "worker-started.json", b'{"schema_version":1}')
    for cid in request["candidate_ids"]:
        sink = store.sink(output_id, cid)
        job = read_job(jobs, folder)
        if job["state"] in TERMINAL or job.get("cancel_requested_at") is not None:
            sink.finish(result(sink.identity, "skipped_cancelled", "cancelled_before_start", retry_policy="safe_new_request"))
            continue
        if time.time() >= job["created_at"] + job["wall_seconds"]:
            sink.finish(result(sink.identity, "skipped_limit", "limit_reached", retry_policy="new_budget_request"))
            continue
        try:
            store.verify_artifacts(output_id)
            auth = credentials(job.get("settings_path"), request["backend"])
        except (OSError, ValueError, ConfigError) as error:
            sink.finish(result(sink.identity, "error", "preflight_failed", stage="preflight",
                cause_type=type(error).__name__, retry_policy="safe_new_request"))
        else:
            call = sink.call_request(auth)
            call["deadline"] = job["created_at"] + job["wall_seconds"]
            run_generation(call, sink)
        payload = store.project(output_id)
        counts = {}
        for entry in payload["entries"]:
            counts[entry["status"]] = counts.get(entry["status"], 0) + 1
        change(jobs, folder, job["nonce"], progress={"total":len(request["candidate_ids"]),
            "completed":sum(v for k,v in counts.items() if k not in ("pending", "running")), "counts":counts})
    store.project(output_id)


def build(root):
    """Deterministic, offline prompt construction from the captured runtime."""
    from copy import deepcopy
    from gapengine.qd import read_rows
    from gapengine.scenes import extract_scenes
    from gapengine.synopsis import load_world_meta, build_synopsis_prompt, build_narration_prompt
    from execution.provenance import atomic_json
    plan = read_json(root / "source-plan.json")
    config = read_json(root / "config.json")
    request = plan["request"]
    meta = load_world_meta(root / "inputs/projects" / config["project_id"],
                           root / "inputs/templates" / config["template_id"])
    prompts, archive = {}, {"schema_version": 1, "cells": {}, "candidate_mapping": {}}
    for c in plan["candidates"]:
        cid, cell = c["candidate_id"], c["cell_key"]
        if plan["representatives"].get(cell) == cid:
            elite = deepcopy(plan["archive"]["cells"][cell])
        else:
            elite = {"generation": c["generation"], "quality": c.get("quality", "不明"),
                     "exemplar": {"seed": c["seed"]}, "genome": c.get("genome")}
        elite["cell"] = cell
        archive["cells"][cid] = elite
        archive["candidate_mapping"][cid] = {"candidate_id": cid, "cell": cell}
        rows = read_rows(root / "inputs/candidates" / cid / "layers.jsonl")
        scenes = extract_scenes(rows, meta)
        synopsis = None if request["synopsis_refs"][cid] is None else (
            root / "inputs/synopses" / (cid + ".txt")).read_text(encoding="utf-8")
        prompts[cid] = (build_synopsis_prompt(elite, scenes, meta) if request["kind"] == "synopsize"
                        else build_narration_prompt(elite, scenes, meta, synopsis=synopsis))
    atomic_json(root / "build-result.json", {"schema_version":1, "prompts":prompts, "archive":archive})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--control")
    parser.add_argument("--output")
    parser.add_argument("--build")
    args = parser.parse_args()
    if args.build:
        build(Path(args.build))
    elif args.control and args.output:
        run(args.control, args.output)
    else:
        parser.error("--control and --output are required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
