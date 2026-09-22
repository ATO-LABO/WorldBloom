"""Human approval and recoverable publication of patch revisions."""
import datetime
import hashlib
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path

import yaml

from engine.subject import Subject
from engine.world import World
from execution.provenance import atomic_json
from execution.world_patches import applicable_snapshot, patch_lock
from gapengine.lineage import _EXEMPLAR_PATH
from gapengine.world_patch import (PATCH_RULES_VERSION, ID_RE, PatchError, materialize,
                                  next_digest, patch_id_for, read_stack, template_identifiers, verify_stack)
from gapengine.world_patch_contract import contract_check
from gapengine.world_patch_inputs import digest, inputs_digest, runtime_digest
from gapengine.world_patch_trial import TRIAL_RULES_VERSION, gate_status, seed_sets


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


class StalePatch(PatchError):
    """approve() was asked to check the proposal against an expected
    patch/gate hash (expect_patch_sha256/expect_gate_sha256) that no longer
    matches what's on disk -- a caller (the viewer's approve API) can catch
    this specifically to answer 409 instead of guessing from the message."""


def approve(project, template, patch_id, reason, *, repo_root=None,
            expect_patch_sha256=None, expect_gate_sha256=None):
    """Human approval of a proposed patch.

    What this re-runs at approval time: the static gate (via materialize(),
    which calls validate_patch() again), a cross-check of every evidence
    individual against the experiment's own (freshly re-hashed) archive.json
    and precedent files, the input/runtime/rules digests, and a full engine
    construction + subject bind + contract re-check of the materialized
    world. What it does *not* re-run: the original measurement itself (the
    base-vs-patched simulated runs) or the reproduction check -- those are
    taken from gate.json, which is a *trusted local check record*, not an
    authenticated one: `check` wrote it with the same privileges anything
    else here has, so its hashes detect accidental drift (a re-run, a synced
    copy, an edit) and not a party able to rewrite every file at once. The
    hash this records into stack.json at approval pins what was approved
    from here on; it says nothing about how the measurement was produced.
    Re-run `check` before approving anything you don't trust the provenance
    of (N4, Astra review).
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
        gate_raw = gate_path.read_bytes()
        gate = json.loads(gate_raw)
        # R3 (viewer review): checked here, inside the lock, immediately
        # after reading both files -- a caller that already read patch_sha256/
        # gate_sha256 from outside the lock (e.g. to show them on screen) can
        # pass what it saw and get a dedicated StalePatch instead of racing
        # its own pre-check against a concurrent writer.
        if ((expect_patch_sha256 is not None and expect_patch_sha256 != _sha(raw))
                or (expect_gate_sha256 is not None and expect_gate_sha256 != _sha(gate_raw))):
            raise StalePatch("画面を開いたあとに内容が変わりました。再読み込みしてください")
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
        # N2 (WB-WORLDGROW-001, Astra review): forward repo_root here too --
        # this used to always re-resolve against this module's own default
        # repo, which could raise (or silently resolve against the wrong
        # references) for a patch whose experiment was checked against a
        # --repo control-side copy.
        ctx = _resolve_world_context(RunRepository(experiment.parent), experiment, repo_root=repo_root)
        if ctx["source"] != "frozen_inputs" or ctx["target_ending"] != evidence.get("target_ending"):
            raise PatchError("対象結末または実験の証拠が変更されています")
        summary = json.loads((experiment / "summary.json").read_text(encoding="utf-8"))
        seeds = evidence.get("seeds", [])
        if seeds != seed_sets(summary.get("seeds", []), len(seeds))["holdout"]:
            raise PatchError("holdout seed の証拠が一致しません")
        # N1 (WB-WORLDGROW-001, Astra review): re-hash archive.json itself
        # (not just trust evidence["archive_sha256"] blindly) so a rewritten
        # archive cannot be paired with a gate.json that was never re-checked
        # against it.
        try:
            archive_bytes = experiment.joinpath("archive.json").read_bytes()
        except OSError as error:
            raise PatchError("実験のアーカイブを読み込めません。check をやり直してください") from error
        if evidence.get("archive_sha256") != _sha(archive_bytes):
            raise PatchError("実験のアーカイブ記録が変更されています。check をやり直してください")
        archive = json.loads(archive_bytes)
        # Cross-check every evidence individual against the archive: the
        # cell exists, its recorded generation matches both the individual
        # and the cell's own record, its recorded index matches the actual
        # index encoded in the exemplar's layers_path (not just an
        # attacker-chosen number), and the genome/precedent/antagonist
        # hashes match. De-duplicate by (generation, index) afterward -- the
        # same real individual repeated under padded entries (identical, or
        # relabeled to a different fabricated index that still fails the
        # exemplar-path check above) must not count as several individuals.
        verified_by_key = {}
        for individual in evidence["individuals"]:
            elite = archive.get("cells", {}).get(individual.get("cell"))
            if elite is None:
                raise PatchError("試走個体または前例表の証拠が変更されています")
            match = _EXEMPLAR_PATH.fullmatch(str((elite.get("exemplar") or {}).get("layers_path", "")))
            generation, requested_index = individual.get("generation"), individual.get("index")
            if (not match or elite.get("generation") != generation or int(match.group(1)) != generation
                    or int(match.group(2)) != requested_index):
                raise PatchError("試走個体または前例表の証拠が変更されています")
            precedent = experiment / f"g{generation}/precedent.json"
            opponent_path = experiment / f"g{generation}/precedent.antagonist.json"
            opponent = elite["exemplar"].get("antagonist_genome")
            if (digest(elite["genome"]) != individual["genome_sha256"]
                    or _sha(precedent.read_bytes()) != individual["precedent_sha256"]
                    or (digest(opponent) if opponent is not None else None) != individual["antagonist_genome_sha256"]
                    or (_sha(opponent_path.read_bytes()) if opponent_path.is_file() else None) != individual["antagonist_precedent_sha256"]):
                raise PatchError("試走個体または前例表の証拠が変更されています")
            verified_by_key.setdefault((generation, requested_index), individual)
        if len(verified_by_key) < 3:
            raise PatchError("試走個体または前例表の証拠が変更されています")
        # Structural check: the recorded pairs/runs/total_runs must be
        # exactly what the verified, de-duplicated individuals x holdout
        # seeds would produce -- catches padding that stays internally
        # "consistent" (an inflated pairs/runs list) without needing a
        # forged individual entry of its own.
        trial = gate["trial"]
        expected_cells = {row["cell"] for row in verified_by_key.values()}
        expected_pairs = {(cell, seed) for cell in expected_cells for seed in seeds}
        actual_pairs = [(pair.get("cell"), pair.get("seed")) for pair in trial.get("pairs", [])]
        if (len(actual_pairs) != len(expected_pairs) or set(actual_pairs) != expected_pairs
                or trial.get("runs") != len(verified_by_key)
                or trial.get("total_runs") != len(verified_by_key) * len(seeds)):
            raise PatchError("試走個体または前例表の証拠が変更されています")
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            for role, path in refs.items():
                full.setdefault("gapengine", {})[role] = str(path)
            world_path = temp / "world.yaml"
            world_path.write_text(yaml.safe_dump(full, allow_unicode=True, sort_keys=False), encoding="utf-8")
            subjects_dir = temp / "subjects"
            subjects_dir.mkdir()
            subjects = []
            for name, person in full_people.items():
                path = subjects_dir / name
                path.write_text(yaml.safe_dump(person, allow_unicode=True, sort_keys=False), encoding="utf-8")
                subjects.append(Subject.from_yaml(path))
            graph = template / "action_graph.yaml"
            loaded = World.from_yaml(world_path, action_graph_path=graph if graph.is_file() else None)
            loaded.bind_subjects({p.id: p for p in subjects})
            # Optional (WB-WORLDGROW-001 review): re-run the contract check
            # against this same materialized world/subjects -- cheap (no
            # simulation), and catches a gate.json whose own contract record
            # was rewritten to hide a violation.
            recheck = contract_check(world_path, subjects_dir, patch,
                                      action_graph_path=graph if graph.is_file() else None)
            if recheck["violations"]:
                raise PatchError("承認時の契約再検査で違反が見つかりました: " + "、".join(recheck["violations"]))
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


def reject(project, patch_id):
    """Move a proposal (yaml + gate.json, if present) from _proposed/ to
    _rejected/. Moved from scripts/world_patch.py's cmd_reject verbatim
    (WB-WORLDGROW-001 段階3b) so the viewer's POST /api/worlds/.../reject
    can call the same logic as the CLI without duplicating it."""
    if not ID_RE.fullmatch(patch_id):
        raise PatchError("パッチ ID の形式が不正です")
    project = Path(project)
    with patch_lock(project):
        proposed = project / "patches" / "_proposed"
        destination = project / "patches" / "_rejected"
        files = [proposed / f"{patch_id}{suffix}" for suffix in (".yaml", ".gate.json")]
        files = [p for p in files if p.is_file()]
        if not files:
            raise PatchError("提案が見つかりません")
        if any((destination / p.name).exists() for p in files):
            raise PatchError("却下済みの同名ファイルがあります")
        destination.mkdir(exist_ok=True)
        for path in files:
            shutil.move(str(path), str(destination / path.name))


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
