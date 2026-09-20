"""Temporary, synthetic approval records for storage and freezing unit tests.

These are not approvals of real project patches; end-to-end tests use check.
"""
import hashlib
import json
from pathlib import Path
import yaml
from gapengine.world_patch import EMPTY_STACK_DIGEST, next_digest, patch_id_for, read_stack
from gapengine.world_patch_inputs import inputs_digest, read_subjects, resolve_references

ROOT = Path(__file__).resolve().parents[1]


def frozen_experiment(root, *, explanations=True, coevolve=False):
    import shutil
    from execution.configs import ConfigStore
    from gapengine.evolve import evolve
    root = Path(root)
    repo = root / "repo"
    project, template = repo / "projects/momotaro", repo / "templates/momotaro"
    shutil.copytree(ROOT / "projects/momotaro", project, ignore=shutil.ignore_patterns("patches"))
    shutil.copytree(ROOT / "templates/momotaro", template)
    for package in ("engine", "gapengine", "scripts", "execution"):
        shutil.copytree(ROOT / package, repo / package, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(ROOT / "requirements.txt", repo / "requirements.txt")
    path = project / "world.yaml"
    world = yaml.safe_load(path.read_text(encoding="utf-8"))
    world["time"] = {"days": 1, "slots": ["朝", "昼", "夕"]}
    world["daily_events"] = None
    world["scheduled_events"] = [{"id": "fixture_end", "day": 1, "slot": "夕", "targets": ["桃太郎"],
        "label": "試験の帰還", "grants_item": {"name": "鬼ヶ島の宝物", "count": 1}, "move_to": "村"}]
    path.write_text(yaml.safe_dump(world, allow_unicode=True, sort_keys=False), encoding="utf-8")
    store = ConfigStore(repo, root / "control", root / "runs")
    spec = {"label": "パッチ試験", "project_id": "momotaro", "template_id": "momotaro",
            "evolution": {"generations": 2, "population": 12, "seeds": 2, "seed_base": 31,
                          "ga_seed": 29, "processes": 1, "keep": "all", "record_explanations": explanations,
                          "coevolve": coevolve}}
    store.save(spec, config_id="cfg-fixture")
    manifest = store.prepare_run("cfg-fixture", run_id="run-fixture", job_id="job-fixture")
    experiment = root / "runs/run-fixture"
    evolve({**manifest["evolution"], "out": experiment,
            "project": experiment / "inputs/projects/momotaro",
            "template": experiment / "inputs/templates/momotaro"})
    return experiment, project, template


def branch_entry_experiment(root):
    """A tiny momotaro-derived experiment where a world-expansion branch off
    the protagonist's own starting zone is essentially the only thing left
    to do -- built so gapengine.world_patch_trial.run_trial's `new_usage`
    counters exercise a real engine run that actually moves into, decides
    in, and gathers from an added branch (WB-WORLDGROW-001 M1: the old
    branch-usage test only counted hand-written JSONL rows). Trimmed to a
    single zone/subject pair (only the protagonist and the antagonist,
    routes/items/facts pared down to what that pair still references) so a
    neutral-genome run reliably wanders into the branch within ~2 days
    instead of getting lost in the full village's social options -- real
    GA runs against the unmodified project, even over many individuals and
    seeds, essentially never happened to wander into an added branch this
    way (verified empirically before writing this fixture).

    No manifest.json/ConfigStore freezing here: run_trial only requires
    resolve_experiment_inputs to find an `expanded-project/world.yaml`,
    which is far cheaper to assemble than a frozen run. Returns
    (experiment, template); the caller supplies its own patch and seeds.
    """
    import shutil
    from gapengine import lineage
    from gapengine.evolve import run_individual
    from gapengine.genome import Genome
    from gapengine.precedent import PrecedentTable
    from gapengine.world_patch_trial import _job
    from viewer.data import RunRepository

    root = Path(root)
    project, template = root / "repo/projects/momotaro", root / "repo/templates/momotaro"
    shutil.copytree(ROOT / "projects/momotaro", project, ignore=shutil.ignore_patterns("patches"))
    shutil.copytree(ROOT / "templates/momotaro", template)

    # when_downed_ally references 犬/猿/キジ by name, which this fixture
    # removes as subjects entirely.
    rules_path = template / "rules.yaml"
    rules = [r for r in yaml.safe_load(rules_path.read_text(encoding="utf-8")) if r["id"] != "when_downed_ally"]
    rules_path.write_text(yaml.safe_dump(rules, allow_unicode=True, sort_keys=False), encoding="utf-8")

    for subject_path in (project / "subjects").glob("*.yaml"):
        if subject_path.stem not in ("03_momotaro", "07_oni"):
            subject_path.unlink()

    momotaro_path = project / "subjects" / "03_momotaro.yaml"
    momotaro = yaml.safe_load(momotaro_path.read_text(encoding="utf-8"))
    momotaro["verbs"] = ["move", "investigate"]
    momotaro["inventory"] = {}
    momotaro["knowledge"] = []
    momotaro["relations"] = {"鬼": momotaro["relations"]["鬼"]}
    momotaro["range"]["zones"] = ["村"]
    momotaro["range"]["exclude"] = []
    momotaro_path.write_text(yaml.safe_dump(momotaro, allow_unicode=True, sort_keys=False), encoding="utf-8")

    oni_path = project / "subjects" / "07_oni.yaml"
    oni = yaml.safe_load(oni_path.read_text(encoding="utf-8"))
    oni["verbs"] = ["rest"]
    oni["inventory"] = {}
    oni["relations"] = {"桃太郎": oni["relations"]["桃太郎"]}
    oni["range"] = {"zones": ["村"], "entry": "村"}
    oni_path.write_text(yaml.safe_dump(oni, allow_unicode=True, sort_keys=False), encoding="utf-8")

    world_path = project / "world.yaml"
    world = yaml.safe_load(world_path.read_text(encoding="utf-8"))
    world["time"] = {"days": 2, "slots": ["朝", "昼", "夕"]}
    world["daily_events"] = None
    world["zones"] = [{"name": "村", "note": "起点"}]
    world["routes"] = {"村": []}
    world["movement"]["destination_weights"] = {"村": 1.0}
    world["items"] = [{"name": "鬼ヶ島の宝物", "lootable": True, "objective": True}]
    world["trials"] = []
    world["facts"] = []
    world["truth"] = {}
    world["scheduled_events"] = [{"id": "fixture_end", "day": 2, "slot": "夕", "targets": ["桃太郎"],
        "label": "試験の帰還", "grants_item": {"name": "鬼ヶ島の宝物", "count": 1}, "move_to": "村"}]
    world_path.write_text(yaml.safe_dump(world, allow_unicode=True, sort_keys=False), encoding="utf-8")

    experiment = root / "runs/exp"
    shutil.copytree(project, experiment / "expanded-project")
    (experiment / "summary.json").write_text(json.dumps({"seeds": [1, 2, 3, 4]}), encoding="utf-8")

    repository = RunRepository(experiment.parent)
    ctx = lineage._resolve_world_context(repository, experiment, template_dir=template)
    genome = Genome.neutral()
    precedent_text = PrecedentTable().to_json()
    job = _job(ctx=ctx, world_path=world_path, out_dir=experiment / "g0/ind-0", elite={"genome": genome.to_dict()},
               index=0, seeds=[1], precedent_json=precedent_text, antagonist_precedent_json=None,
               antagonist_genome=None)
    job["logical_root"] = str(experiment)
    job["record_explanations"] = True
    run = run_individual(job)["runs"][0]

    (experiment / "g0/precedent.json").write_text(precedent_text, encoding="utf-8")
    archive = {"cells": {"c0": {"quality": run["quality"], "generation": 0, "genome": genome.to_dict(),
                                 "exemplar": {**run, "genome": genome.to_dict()}}}}
    (experiment / "archive.json").write_text(json.dumps(archive, ensure_ascii=False), encoding="utf-8")
    return experiment, template


def write_approved(project, patch, template=None, repo=None):
    project = Path(project)
    folder = project / "patches"
    folder.mkdir(parents=True, exist_ok=True)
    stack = read_stack(project)
    patch = dict(patch, id=patch_id_for(patch["add"]), parent_digest=stack["head"])
    patch.pop("approved_seq", None)
    patch.pop("parent_rev", None)
    raw = yaml.safe_dump(patch, allow_unicode=True, sort_keys=False).encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    reason = {"reason": "単体試験専用の合成された承認記録です"}
    evidence = {"seed_set": "holdout", "patched_inputs_digest": "test-patched",
                "patch_rules_version": 2, "trial_rules_version": 3}
    gate = {"patch_id": patch["id"], "patch_sha256": sha, "status": "reviewable",
            "trial": {"evidence": evidence}, "approval": reason}
    gate_raw = json.dumps(gate, ensure_ascii=False).encode("utf-8")
    (folder / f"{patch['id']}.yaml").write_bytes(raw)
    (folder / f"{patch['id']}.gate.json").write_bytes(gate_raw)
    revision = {"rev": len(stack["revisions"]) + 1, "patch_id": patch["id"],
                "parent_digest": stack["head"], "digest": next_digest(stack["head"], sha),
                "patch_sha256": sha, "gate_sha256": hashlib.sha256(gate_raw).hexdigest(),
                "patched_inputs_digest": "test-patched", "rules_version": 2,
                "trial_rules_version": 3, "approved_at": "test", "approval": reason}
    stack["revisions"].append(revision)
    stack["head"] = revision["digest"]
    stack["base_inputs_digest"], stack["template_id"] = "test-base", "test-template"
    if template:
        world = yaml.safe_load((project / "world.yaml").read_text(encoding="utf-8"))
        refs = resolve_references(world, project, repo) if repo else resolve_references(world, project)
        stack["base_inputs_digest"] = inputs_digest(world, read_subjects(project / "subjects"), template, refs)
        stack["template_id"] = Path(template).name
    (folder / "stack.json").write_text(json.dumps(stack, ensure_ascii=False), encoding="utf-8")
    return patch
