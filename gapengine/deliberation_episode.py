"""Run authored deliberation worlds through the ordinary seeded Simulation.

The batch predeclares all seeds and retains every outcome. Selection for prose
is a later author operation, never feedback into simulation or success counts.
"""
from collections import Counter
from pathlib import Path
import hashlib
import json
import shutil

from engine.deliberation import digest
from engine.episode_contract import factory_identities
from engine.execution_plan import ExecutionPlan
from engine.sim import Simulation
from engine.subject import Subject
from engine.world import World


def load_episode(world_path):
    world_path = Path(world_path)
    world = World.from_yaml(world_path)
    if world.deliberation is None:
        raise ValueError("This runner requires an explicit deliberation world")
    if world.action_graph_enabled or world.effect_library:
        raise ValueError("This batch runner snapshots self-contained worlds only")
    files = sorted((world_path.parent / "subjects").glob("*.yaml"))
    if not files or len(files) > 40:
        raise ValueError("A bounded subjects directory is required")
    people = {}
    for path in files:
        person = Subject.from_yaml(path)
        if person.id in people:
            raise ValueError("Duplicate episode actor")
        people[person.id] = person
    world.bind_subjects(people)
    return world, people


def run_batch(world_path, output, seeds):
    world_path, output = Path(world_path).resolve(), Path(output)
    if not isinstance(seeds, list) or not seeds or len(seeds) > 1000 or any(type(s) is not int or s < 0 for s in seeds) or len(set(seeds)) != len(seeds):
        raise ValueError("Distinct, predeclared nonnegative seeds required")
    world, _ = load_episode(world_path)
    sampler, provider = factory_identities("legacy_instant")
    source = Path(__file__).read_bytes()
    classification = {"id": "deliberation_terminal_v1", "version": 1,
                      "source_hash": hashlib.sha256(source).hexdigest(),
                      "config_hash": digest({"outcomes": ["approved", "rejected", "deferred", "unresolved"]})}
    plan = ExecutionPlan({"schema_version": 1, "execution_kind": "independent_trials",
                          "sampler": sampler, "provider": provider, "classification": classification,
                          "predeclared_seeds": seeds, "planned_trials": len(seeds),
                          "selection": dict.fromkeys(["fitness_selection", "breeding", "keep_filter", "canon_feedback"], False),
                          "observation": {"unit": "logical_step", "start": 0, "end": world.days * len(world.slots) - 1,
                                          "stop_contract_hash": digest({"target_as_observation": False, "death_stops": True})}})
    output.mkdir(parents=True, exist_ok=False)
    inputs = output / "inputs"
    (inputs / "subjects").mkdir(parents=True)
    # These samples are self-contained. Snapshot inputs before the first trial.
    copied = [world_path, *sorted((world_path.parent / "subjects").glob("*.yaml"))]
    manifest = {}
    for path in copied:
        rel = path.relative_to(world_path.parent)
        target = inputs / rel
        shutil.copyfile(path, target)
        manifest[rel.as_posix()] = hashlib.sha256(target.read_bytes()).hexdigest()
    engine_root = Path(__file__).resolve().parents[1] / "engine"
    source_files = {"engine/" + path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in sorted(engine_root.glob("*.py"))}
    manifest_doc = {"inputs": manifest, "sources": source_files, "runner_sha256": classification["source_hash"]}
    (output / "input-manifest.json").write_text(json.dumps(manifest_doc, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    (output / "execution-plan.json").write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    def evaluate(seed):
        current_world, people = load_episode(inputs / "world.yaml")
        episode = Simulation(seed, current_world, people, output / f"seed-{seed:04}", execution_plan=plan.to_dict())
        ledger = episode.run()
        from gapengine.deliberation_story import scene_material
        material = scene_material(ledger.parent)
        (ledger.parent / "scene-material.json").write_text(json.dumps(material, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        state = current_world.deliberation.snapshot()
        records = state["proposals"]
        statuses = {pid: record["status"] for pid, record in records.items()}
        terminal = next(iter(statuses.values())) if len(statuses) == 1 else "multiple_proposals"
        if terminal not in {"approved", "rejected", "deferred"}:
            terminal = "unresolved"
        return {"status": terminal, "proposals": statuses, "state": state,
                "ledger": ledger.relative_to(output).as_posix(),
                "ledger_sha256": hashlib.sha256(ledger.read_bytes()).hexdigest(),
                "saved_run": episode.run_record()}
    rows = []
    def save(record):
        rows.append(record)
        with (output / "trials.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True)+"\n")
    results = plan.independent(evaluate, save)
    summary = {"schema_version": 1, "world": world.name, "execution_plan_hash": plan.sha256,
               "trial_count": len(results), "counts": dict(sorted(Counter(r["outcome"]["status"] for r in results).items())),
               "api_calls": 0, "gpu": False, "selection": "all_predeclared_trials_retained"}
    (output / "batch-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return summary
