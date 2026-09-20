"""Human approval and recoverable publication of patch revisions."""
import datetime
import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path

import yaml

from engine.subject import Subject
from engine.world import World
from execution.provenance import atomic_json
from execution.world_patches import applicable_snapshot, patch_lock
from gapengine.world_patch import (PATCH_RULES_VERSION, ID_RE, PatchError, materialize,
                                  next_digest, patch_id_for, read_stack, template_identifiers, verify_stack)
from gapengine.world_patch_inputs import digest, inputs_digest, runtime_digest
from gapengine.world_patch_trial import TRIAL_RULES_VERSION, gate_status, seed_sets


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def approve(project, template, patch_id, reason, *, repo_root=None):
    """Human approval of a proposed patch.

    Note: this only re-derives gate.status from the *stored* gate.json's own
    static/trial/evidence fields (see R1) -- it never re-runs the static or
    trial gates, so a gate.json whose `contract`/`reproduction` records were
    themselves rewritten to look consistent cannot be caught here. Re-run
    `check` before approving anything you don't trust the provenance of.
    """
    project, template = Path(project), Path(template)
    if not isinstance(reason, str) or len(reason.strip()) < 10:
        raise PatchError("承認理由を10文字以上で指定してください")
    if not ID_RE.fullmatch(patch_id):
        raise PatchError("パッチ ID の形式が不正です")
    folder = project / "patches"
    with patch_lock(project):
        verified, stack, world, people, refs, root_digest = applicable_snapshot(project, template, repo_root=repo_root)
        source = folder / "_proposed" / f"{patch_id}.yaml"
        gate_path = source.with_suffix(".gate.json")
        if not source.is_file() or not gate_path.is_file():
            # R5: a losing concurrent approve() can reach here after the
            # winner already moved this same proposal out of _proposed/.
            raise PatchError("提案が見つかりません（別の処理が先に承認・却下した可能性があります）")
        raw = source.read_bytes()
        patch = yaml.safe_load(raw)
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
        # R1: re-derive status from the gate's own recorded static/trial
        # evidence rather than trusting either the stored `status` field or
        # trial["runs"] -- both are values a rewritten gate.json could set
        # independently of the individuals/seeds actually recorded.
        derived_status = gate_status(gate)
        if gate.get("status") != derived_status:
            raise PatchError("ゲート結果が書き換えられています。check をやり直してください")
        if derived_status != "reviewable":
            raise PatchError("承認できる状態は reviewable のみです")
        evidence = gate["trial"]["evidence"]
        if evidence.get("seed_set") != "holdout":
            raise PatchError("holdout の検査が必要です")
        if (patch.get("id") != patch_id or gate.get("patch_id") != patch_id
                or patch_id_for(patch.get("add", {})) != patch_id
                or gate.get("patch_sha256") != _sha(raw)):
            raise PatchError("パッチの内容または ID がゲートと一致しません。check をやり直してください")
        if patch.get("parent_digest") != stack["head"]:
            raise PatchError("parent_digest が現在の承認済みスタックと一致しません")
        reserved = template_identifiers(template)
        base, base_people = materialize(world, people, [p for p, _ in verified], reserved=reserved, check_budgets=False)
        full, full_people = materialize(base, base_people, [patch], reserved=reserved)
        expected = {"base_inputs_digest": inputs_digest(base, base_people, template, refs),
                    "patched_inputs_digest": inputs_digest(full, full_people, template, refs),
                    "runtime_digest": runtime_digest(), "trial_rules_version": TRIAL_RULES_VERSION,
                    "patch_rules_version": PATCH_RULES_VERSION}
        if any(evidence.get(k) != v for k, v in expected.items()):
            raise PatchError("入力・実行コード・規約が検査時と一致しません。check をやり直してください")
        # Revalidate the sealed source and the explicit target override at approval.
        # R7: intentional layer inversion, function-local -- resolving a legacy/
        # non-frozen experiment's project+template needs viewer.data's existing
        # RunRepository/resolve_genre; this module isn't imported at module load
        # time, so execution/ doesn't depend on viewer/ just to be imported.
        from gapengine.lineage import _resolve_world_context
        from viewer.data import RunRepository
        experiment = Path(evidence["experiment"])
        ctx = _resolve_world_context(RunRepository(experiment.parent), experiment)
        if ctx["source"] != "frozen_inputs" or ctx["target_ending"] != evidence.get("target_ending"):
            raise PatchError("対象結末または実験の証拠が変更されています")
        summary = json.loads((experiment / "summary.json").read_text(encoding="utf-8"))
        seeds = evidence.get("seeds", [])
        if seeds != seed_sets(summary.get("seeds", []), len(seeds))["holdout"]:
            raise PatchError("holdout seed の証拠が一致しません")
        archive = json.loads((experiment / "archive.json").read_text(encoding="utf-8"))
        for individual in evidence["individuals"]:
            elite = archive["cells"][individual["cell"]]
            generation = individual["generation"]
            precedent = experiment / f"g{generation}/precedent.json"
            opponent_path = experiment / f"g{generation}/precedent.antagonist.json"
            opponent = elite["exemplar"].get("antagonist_genome")
            if (digest(elite["genome"]) != individual["genome_sha256"]
                    or _sha(precedent.read_bytes()) != individual["precedent_sha256"]
                    or (digest(opponent) if opponent is not None else None) != individual["antagonist_genome_sha256"]
                    or (_sha(opponent_path.read_bytes()) if opponent_path.is_file() else None) != individual["antagonist_precedent_sha256"]):
                raise PatchError("試走個体または前例表の証拠が変更されています")
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            for role, path in refs.items():
                full.setdefault("gapengine", {})[role] = str(path)
            world_path = temp / "world.yaml"
            world_path.write_text(yaml.safe_dump(full, allow_unicode=True, sort_keys=False), encoding="utf-8")
            subjects = []
            for name, person in full_people.items():
                path = temp / name
                path.write_text(yaml.safe_dump(person, allow_unicode=True, sort_keys=False), encoding="utf-8")
                subjects.append(Subject.from_yaml(path))
            graph = template / "action_graph.yaml"
            loaded = World.from_yaml(world_path, action_graph_path=graph if graph.is_file() else None)
            loaded.bind_subjects({p.id: p for p in subjects})
        destinations = [folder / source.name, folder / gate_path.name]
        if any(p.exists() for p in destinations):
            raise PatchError("承認先に同名ファイルがあります")
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        gate["approval"] = {"reason": reason.strip()}
        atomic_json(gate_path, gate)
        gate_sha = _sha(gate_path.read_bytes())
        revision = {"rev": len(stack["revisions"]) + 1, "patch_id": patch_id,
                    "patch_sha256": _sha(raw), "gate_sha256": gate_sha,
                    "parent_digest": stack["head"], "digest": next_digest(stack["head"], _sha(raw)),
                    "patched_inputs_digest": evidence["patched_inputs_digest"],
                    "rules_version": PATCH_RULES_VERSION, "trial_rules_version": TRIAL_RULES_VERSION,
                    "approved_at": now, "approval": gate["approval"]}
        os.replace(source, destinations[0])
        os.replace(gate_path, destinations[1])
        stack.update(template_id=template.name, base_inputs_digest=root_digest, head=revision["digest"])
        stack["revisions"].append(revision)
        atomic_json(folder / "stack.json", stack)
        return revision


def repair(project):
    project = Path(project)
    folder, moved = project / "patches", []
    with patch_lock(project):
        stack = read_stack(project)
        listed = {r["patch_id"] for r in stack["revisions"]}
        # Verify listed bytes before moving anything; corrupt committed revisions
        # cannot be repaired by discarding their files.
        for entry in stack["revisions"]:
            for suffix, key in ((".yaml", "patch_sha256"), (".gate.json", "gate_sha256")):
                path = folder / (entry["patch_id"] + suffix)
                if not path.is_file() or _sha(path.read_bytes()) != entry[key]:
                    raise PatchError("manifest掲載済みファイルの破損はrepairできません")
        sources = []
        for path in sorted(folder.glob("*.yaml")):
            if path.stem not in listed:
                sources.append(path)
                if path.with_suffix(".gate.json").exists():
                    sources.append(path.with_suffix(".gate.json"))
        destination = folder / "_proposed"
        if any((destination / p.name).exists() for p in sources):
            raise PatchError("_proposed の同名ファイルと競合しています。上書きはしません")
        destination.mkdir(exist_ok=True)
        for path in sources:
            os.replace(path, destination / path.name)
            moved.append(path.name)
        verify_stack(project)
    return moved


def reopen(project):
    project = Path(project)
    folder = project / "patches"
    with patch_lock(project):
        verified = verify_stack(project)
        if not verified:
            return []
        stack = read_stack(project)
        destination = folder / "_proposed"
        sources = [folder / (p["id"] + suffix) for p, _ in verified for suffix in (".yaml", ".gate.json")]
        if any((destination / p.name).exists() for p in sources):
            raise PatchError("_proposed の同名ファイルと競合しています。上書きはしません")
        history = folder / "_history"
        history.mkdir(exist_ok=True)
        archive = history / f"stack.{stack['head'][:12]}.{uuid.uuid4().hex[:8]}"
        archive.mkdir()
        # Preserve complete evidence before invalidating the active approvals.
        for path in sources:
            (archive / path.name).write_bytes(path.read_bytes())
        atomic_json(archive / "stack.json", stack)
        destination.mkdir(exist_ok=True)
        for path in sources:
            os.replace(path, destination / path.name)
            if path.name.endswith(".gate.json"):
                gate = json.loads((destination / path.name).read_text(encoding="utf-8"))
                gate.update(status="trial_pending", trial=None, passed=False)
                gate.pop("approval", None)
                atomic_json(destination / path.name, gate)
        os.replace(folder / "stack.json", history / f"{archive.name}.json")
        return [p["id"] for p, _ in verified]
