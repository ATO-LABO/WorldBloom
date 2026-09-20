"""Paired measurements and sealed evidence; v1 never claims statistical safety."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import yaml
from engine.sim import _engine_source_hash
from gapengine import lineage
from gapengine.evolve import run_individual
from gapengine.qd import read_rows
from gapengine.world_demand import collect
from gapengine.world_patch import (PATCH_RULES_VERSION, PatchError, absolutize_references, materialize,
                                   template_identifiers)
from gapengine.world_patch_inputs import digest, inputs_digest, read_subjects, runtime_digest, verify_frozen
from gapengine.world_patch_contract import contract_check, new_usage, milestones
REPO_ROOT = Path(__file__).resolve().parents[1]
TRIAL_RULES_VERSION = 3
EXPLORATION_SEED_BASE = TRIAL_SEED_BASE = 100000
HOLDOUT_SEED_BASE = 200000

def seed_sets(original, count):
    if type(count) is not int or count < 1:
        raise PatchError("seed数は正の整数で指定してください")
    occupied, result = set(original), {}
    for name, first in (("exploration", EXPLORATION_SEED_BASE), ("holdout", HOLDOUT_SEED_BASE)):
        while occupied.intersection(range(first, first + count)):
            first += 1000000
        result[name] = list(range(first, first + count))
        occupied.update(result[name])
    return result

def individual_count(individuals):
    """De-duplicated by (generation, index) -- the same real individual
    repeated under padded/relabeled entries (WB-WORLDGROW-001 N1: e.g. the
    same row copied 3x, or the same cell re-listed at fabricated indices)
    must not inflate the count trial_state/approve() gate reviewability on."""
    return len({(i.get("generation"), i.get("index")) for i in individuals})


def trial_state(trial):
    if trial.get("errors") or trial.get("contract", {}).get("violations"):
        return "contract_failed"
    evidence, reproduction = trial.get("evidence") or {}, trial.get("reproduction") or {}
    if (evidence.get("base_source") != "frozen_inputs"
            or not evidence.get("engine_hash_experiment")
            or evidence.get("engine_hash_experiment") != evidence.get("engine_hash_trial")
            or reproduction.get("checked", 0) < 1
            or reproduction.get("checked") != reproduction.get("identical")
            or reproduction.get("mismatched")):
        return "reference_only"
    # R1: counted from the sealed evidence lists, not trial["runs"] -- a
    # self-reported counter a rewritten gate.json could inflate independently
    # of the individuals/seeds actually recorded as evidence. N1: de-duplicated
    # by (generation, index), not raw list length -- padding with copies of the
    # same real individual must not read as more individuals than it is.
    if individual_count(evidence.get("individuals", [])) < 3 or len(evidence.get("seeds", [])) < 4:
        return "insufficient"
    return "measured"

def gate_status(gate):
    if not gate.get("static", {}).get("passed") or gate.get("static", {}).get("violations"):
        return "static_failed"
    trial = gate.get("trial")
    if trial is None:
        return "trial_pending"
    state = trial_state(trial)
    return "reviewable" if state == "measured" else state

def _subject_ids(subjects_dir: Path) -> list[str]:
    ids = []
    for path in sorted(subjects_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("id"), str):
            ids.append(data["id"])
    return ids


def _select_cells(archive: dict, max_runs: int) -> list[tuple[str, dict]]:
    cells = archive.get("cells", {}) or {}
    ordered = sorted(cells.items(), key=lambda kv: (-float(kv[1].get("quality", 0.0)), kv[0]))
    return ordered[:max_runs]


def _job(*, ctx: dict, world_path: Path, out_dir: Path, elite: dict,
         index: int, seeds: list[int], precedent_json: str, antagonist_precedent_json,
         antagonist_genome) -> dict[str, Any]:
    return {
        "action_cfg": ctx["action_cfg"],
        "action_graph_path": (str(ctx["action_graph_path"]) if ctx["action_graph_path"] is not None else None),
        "antagonist": ctx["antagonist"],
        "antagonist_action_cfg": ctx["antagonist_action_cfg"],
        "antagonist_genome": antagonist_genome,
        "antagonist_precedent_json": antagonist_precedent_json,
        "genome": elite["genome"],
        "index": index,
        "logical_root": str(out_dir),
        "out_dir": str(out_dir),
        "parents": elite.get("parents", []),
        "precedent_json": precedent_json,
        "protagonist": ctx["protagonist"],
        "qd_cfg": ctx["qd_cfg"],
        "record_explanations": True,
        "rules": ctx["rules"],
        "seeds": list(seeds),
        "subjects_dir": str(ctx["subjects_dir"]),
        "target_ending": ctx["target_ending"],
        "world_path": str(world_path),
    }


def _trigger_counts(report: dict, zones: set[str], verb: str) -> dict[str, int]:
    verb_counts = report.get("verb_counts", {})
    count = whiffs = 0
    for zone in zones:
        pair = verb_counts.get(zone, {}).get(verb, [0, 0])
        count += pair[0]
        whiffs += pair[1]
    return {"count": count, "whiffs": whiffs}


def run_trial(experiment_dir, patch, *, work_dir, template_dir=None, repo_root=None, max_runs=5,
              seeds_per_run=8, seed_set="exploration"):
    # R7: intentional layer inversion, function-local -- a legacy/non-frozen
    # experiment's project+template are resolved through viewer.data's
    # existing RunRepository/resolve_genre; gapengine's module load never
    # depends on viewer/, only this one call path does.
    from viewer.data import RunRepository
    experiment, work = Path(experiment_dir).resolve(), Path(work_dir).resolve()
    if work == experiment or work.is_relative_to(experiment):
        raise PatchError("試走先を既存実験の中には置けません")
    if type(max_runs) is not int or max_runs < 1 or seed_set not in ("exploration", "holdout"):
        raise PatchError("試走の個体数またはseed集合が不正です")
    repository = RunRepository(experiment.parent)
    # N2 (WB-WORLDGROW-001, Astra review): forward the caller's repo_root
    # (e.g. scripts/world_patch.py's --repo) into this re-resolution -- it
    # used to always fall back to this module's own repo, so a
    # gapengine.action_graph/effects reference that exists only under an
    # external repo_root resolved fine in the CLI's own initial ctx but
    # failed here with "参照先がありません".
    ctx = lineage._resolve_world_context(repository, experiment, template_dir=template_dir, repo_root=repo_root)
    summary = json.loads((experiment / "summary.json").read_text(encoding="utf-8"))
    archive_bytes = (experiment / "archive.json").read_bytes()
    archive = json.loads(archive_bytes)
    base = yaml.safe_load(ctx["world_path"].read_text(encoding="utf-8"))
    people = read_subjects(ctx["subjects_dir"])
    patched, patched_people = materialize(base, people, [patch], reserved=template_identifiers(ctx["template_dir"]))
    seeds = seed_sets(summary.get("seeds", []), seeds_per_run)[seed_set]
    runtime_before = runtime_digest()
    evidence = {"base_inputs_digest": inputs_digest(base, people, ctx["template_dir"], ctx["references"]),
                "patched_inputs_digest": inputs_digest(patched, patched_people, ctx["template_dir"], ctx["references"]),
                "runtime_digest": runtime_before, "target_ending": ctx["target_ending"], "individuals": [],
                "seeds": seeds, "seed_set": seed_set, "trial_rules_version": TRIAL_RULES_VERSION,
                "patch_rules_version": PATCH_RULES_VERSION, "base_source": ctx["source"],
                "engine_hash_experiment": None, "engine_hash_trial": _engine_source_hash(REPO_ROOT / "engine"),
                "experiment": str(experiment),
                # N1: sealed alongside the individuals so approve() can
                # detect a rewritten archive.json out from under a gate.
                "archive_sha256": hashlib.sha256(archive_bytes).hexdigest()}
    paths = {}
    for label, definition, subjects in (("base", base, people), ("patched", patched, patched_people)):
        folder = work / label
        (folder / "subjects").mkdir(parents=True, exist_ok=True)
        definition = copy.deepcopy(definition)
        # N2: write the already-resolved absolute reference paths into the
        # copy on disk -- engine/phase2.py's effects loader has no override
        # parameter (unlike action_graph_path), so it only ever finds an
        # external-repo effects file if the world file itself already
        # carries the absolute path.
        absolutize_references(definition, folder, references=ctx["references"])
        path = folder / "world.yaml"
        path.write_text(yaml.safe_dump(definition, allow_unicode=True, sort_keys=False), encoding="utf-8")
        for name, person in subjects.items():
            (folder / "subjects" / name).write_text(yaml.safe_dump(person, allow_unicode=True, sort_keys=False), encoding="utf-8")
        paths[label] = path
    trial = {"schema_version": 2, "evidence": evidence, "runs": 0, "skipped": 0, "errors": [],
             "reproduction": {"checked": 0, "identical": 0, "mismatched": []},
             "contract": contract_check(paths["patched"], paths["patched"].parent / "subjects", patch,
                                         action_graph_path=ctx["action_graph_path"]),
             "pairs": [], "passed": False, "statistics": {"defined": False,
                "note": "v1 は統計的な合否を出さない。規約は複数世界で較正してから導入する"}}
    layers = {"base": [], "patched": []}
    engine_hashes, seen = set(), set()
    for cell, elite in _select_cells(archive, len(archive.get("cells", {}))):
        match = lineage._EXEMPLAR_PATH.fullmatch(str(elite.get("exemplar", {}).get("layers_path", "")))
        if not match:
            trial["skipped"] += 1
            continue
        generation, index, original_seed = map(int, match.groups())
        if (generation, index) in seen:
            continue
        if len(seen) >= max_runs:
            break
        seen.add((generation, index))
        precedent = repository.safe_path(experiment, f"g{generation}/precedent.json")
        if not precedent.is_file():
            trial["skipped"] += 1
            continue
        opponent_path = repository.safe_path(experiment, f"g{generation}/precedent.antagonist.json")
        opponent_text = opponent_path.read_text(encoding="utf-8") if opponent_path.is_file() else None
        opponent = elite["exemplar"].get("antagonist_genome")
        original = repository.safe_path(experiment, elite["exemplar"]["layers_path"])
        original_rows = read_rows(original) if original.is_file() else []
        header = original_rows[0] if original_rows else elite["exemplar"]
        engine_hashes.add(header.get("engine_hash"))
        precedent_text = precedent.read_text(encoding="utf-8")
        individual = {"cell": cell, "generation": generation, "index": index,
                      "genome_sha256": digest(elite["genome"]),
                      "precedent_sha256": hashlib.sha256(precedent.read_bytes()).hexdigest(),
                      "antagonist_genome_sha256": digest(opponent) if opponent is not None else None,
                      "antagonist_precedent_sha256": hashlib.sha256(opponent_path.read_bytes()).hexdigest() if opponent_text else None}
        common = dict(ctx=ctx, elite=elite, index=index, precedent_json=precedent_text,
                      antagonist_precedent_json=opponent_text, antagonist_genome=opponent)
        # Generation zero may have no opponent yet, even in a coevolving run.
        # Explicit null is a recorded matchup, not missing evidence.
        if opponent_text and "antagonist_genome" not in elite["exemplar"]:
            trial["errors"].append({"cell": cell, "error": "敵役genomeがありません"})
            continue
        if original_rows:
            trial["reproduction"]["checked"] += 1
            try:
                recorded = "explanation_recording" in header
                manifest_path = experiment / "manifest.json"
                if manifest_path.is_file():
                    recorded = json.loads(manifest_path.read_text(encoding="utf-8")).get("evolution", {}).get("record_explanations", recorded)
                out = work / "reproduction" / f"g{generation}-ind-{index}"
                job = _job(**common, world_path=paths["base"], out_dir=out, seeds=[original_seed])
                job["record_explanations"] = recorded
                rerun = run_individual(job)
                if (out / rerun["runs"][0]["layers_path"]).read_bytes() == original.read_bytes():
                    trial["reproduction"]["identical"] += 1
                else:
                    trial["reproduction"]["mismatched"].append(cell)
            except Exception as error:
                trial["reproduction"]["mismatched"].append(cell)
                trial["errors"].append({"cell": cell, "world": "reproduction", "error": repr(error)})
        sides = {}
        for label in paths:
            try:
                out = paths[label].parent / f"g{generation}-ind-{index}"
                job = _job(**common, world_path=paths[label], out_dir=out, seeds=seeds)
                job["subjects_dir"] = str(paths[label].parent / "subjects")
                result = run_individual(job)
                sides[label] = (result["runs"], out)
            except Exception as error:
                trial["errors"].append({"cell": cell, "world": label, "error": repr(error)})
        if len(sides) != 2:
            continue
        trial["runs"] += 1
        evidence["individuals"].append(individual)
        for a, b in zip(sides["base"][0], sides["patched"][0]):
            if a["seed"] != b["seed"]:
                raise PatchError("対応試走のseedが一致しません")
            trial["pairs"].append({"cell": cell, "seed": a["seed"], "base": a, "patched": b})
        for label, (runs, out) in sides.items():
            layers[label].extend(out / run["layers_path"] for run in runs)
    evidence["engine_hash_experiment"] = next(iter(engine_hashes)) if len(engine_hashes) == 1 else None
    pairs = trial["pairs"]
    trial["total_runs"], trial["seeds_per_run"], trial["base_source"] = len(pairs), len(seeds), ctx["source"]
    for label in layers:
        trial[f"reached_{label}"] = sum(bool(p[label]["reached"]) for p in pairs)
        trial[f"shaped_{label}"] = sum(p[label].get("shaped", 0) for p in pairs) / len(pairs) if pairs else None
    def differences(key, values):
        result = []
        for value in values:
            selected = [p for p in pairs if p[key] == value]
            if selected:
                result.append({key: value, "mean_diff": sum(int(p["patched"]["reached"]) - int(p["base"]["reached"]) for p in selected) / len(selected)})
        return result
    trial["by_seed"] = differences("seed", seeds)
    trial["by_individual"] = differences("cell", [i["cell"] for i in evidence["individuals"]])
    trial["new_usage"] = new_usage(layers["patched"], patch, ctx["protagonist"])
    trial["trigger"] = None
    trigger = patch.get("trigger") or {}
    if trigger.get("zone") and trigger.get("verb"):
        zones = {trigger["zone"]} | {z["name"] for z in patch.get("add", {}).get("zones", []) if z["parent"] == trigger["zone"]}
        trial["trigger"] = {"zone": trigger["zone"], "verb": trigger["verb"], **{
            label: _trigger_counts(collect(paths_, subject=ctx["protagonist"]), zones, trigger["verb"])
            for label, paths_ in layers.items()}}
    trial["milestones"] = {label: milestones(logs) for label, logs in layers.items()}
    if ctx["source"] == "frozen_inputs":
        verify_frozen(experiment)
    if runtime_digest() != runtime_before:
        raise PatchError("試走中に実行コードが変わりました。再検査してください")
    trial["state"] = trial_state(trial)
    negative = trial["contract"].get("negative", [])
    checked = sum(1 for n in negative if n["status"] == "checked")
    skipped = [n for n in negative if n["status"] == "skipped"]
    trial["reasons"] = [f"到達 {trial['reached_base']}→{trial['reached_patched']}（{len(pairs)}本）",
                         "shaped は枝の追加で尺度が変わるため参考値", trial["statistics"]["note"],
                         f"再現確認 {trial['reproduction']['identical']}/{trial['reproduction']['checked']}",
                         f"陰性検査: 実行 {checked} 件／省略 {len(skipped)} 件"]
    if skipped:
        trial["reasons"].append("陰性検査省略: " + "、".join(
            f"{n['subject']}(規則{n['rule_index']})" for n in skipped))
    return trial
