"""r2 regression cases: admission, identities, budgets, state and recovery."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import random
import shutil
import tempfile
import unittest
from unittest.mock import patch as mock_patch
import yaml

from engine.actions import Action, candidates
from engine.subject import Subject
from engine.verbs import VerbEngine
from engine.world import World
from execution.world_patch_approval import approve as approve_patch, repair, reopen
from execution.world_patches import applicable_snapshot, expanded_snapshot, patch_lock
from gapengine.world_patch import (EMPTY_STACK_DIGEST, PatchError, materialize, patch_id_for,
                                  read_stack, validate_patch, verify_stack)
from gapengine.world_patch_contract import contract_check, new_usage
from gapengine.world_patch_inputs import inputs_digest, read_subjects, resolve_references, runtime_digest
from gapengine.world_patch_trial import gate_status, seed_sets, run_trial
from world_patch_fixtures import (ROOT, branch_entry_experiment, external_repo_experiment, frozen_experiment,
                                  frozen_experiment_with_external_reference, write_approved)


def proposal(parent="海"):
    add = {"zones": [{"name": "船大工の小屋", "parent": parent}],
           "items": [{"name": "古びた帆布", "sources": [{"type": "investigate", "zone": "船大工の小屋", "count": 1, "max": 1}]}]}
    return {"id": patch_id_for(add), "title": "追加の試験", "parent_digest": EMPTY_STACK_DIGEST, "add": add}


class Boundaries(unittest.TestCase):
    def test_approve_missing_options_exit_one_without_writes(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as temp:
            for omitted in ("--reason", "--template"):
                options = {"--project": temp, "--patch": "p-12345678",
                           "--template": str(ROOT / "templates/momotaro"),
                           "--reason": "測定結果を確認して承認します"}
                argv = [value for key, value in options.items() if key != omitted for value in (key, value)]
                result = subprocess.run([sys.executable, "-X", "utf8", "-B",
                    str(ROOT / "scripts/world_patch.py"), "approve", *argv], capture_output=True)
                self.assertEqual(result.returncode, 1, result.stderr.decode("utf-8"))
                self.assertEqual(list(Path(temp).iterdir()), [])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "momotaro"
        self.template = ROOT / "templates/momotaro"
        shutil.copytree(ROOT / "projects/momotaro", self.project, ignore=shutil.ignore_patterns("patches"))
        self.world = yaml.safe_load((self.project / "world.yaml").read_text(encoding="utf-8"))
        self.people = read_subjects(self.project / "subjects")

    def write_materialized(self, p):
        world, people = materialize(self.world, self.people, [p])
        path = self.root / "patched.yaml"
        path.write_text(yaml.safe_dump(world, allow_unicode=True), encoding="utf-8")
        folder = self.root / "subjects"
        folder.mkdir()
        for name, person in people.items():
            (folder / name).write_text(yaml.safe_dump(person, allow_unicode=True), encoding="utf-8")
        return path, folder

    def test_range_contract_and_move_and_gather(self):
        before = deepcopy(self.people)
        p = proposal()
        path, folder = self.write_materialized(p)
        self.assertEqual(self.people, before)
        world = World.from_yaml(path, action_graph_path=self.template / "action_graph.yaml")
        people = {s.id: s for s in (Subject.from_yaml(path) for path in folder.glob("*.yaml"))}
        world.bind_subjects(people)
        actor = people["桃太郎"]
        actor.zone, actor.stamina = "海", actor.stamina_max
        self.assertIn("船大工の小屋", world.reachable_paths(actor))
        moves = [a for a, _ in candidates(actor, world, SimpleNamespace(turn=1, day=1)) if a.verb == "move"]
        move = next(a for a in moves if a.args[0] == "船大工の小屋")
        verbs = VerbEngine(world, random.Random(1))
        verbs.execute(actor, move, turn=1, day=1)
        self.assertEqual(actor.zone, "船大工の小屋")
        verbs.execute(actor, Action("investigate", ("船大工の小屋",)), turn=2, day=1)
        self.assertTrue(actor.has_item("古びた帆布"))
        self.assertNotIn("船大工の小屋", people["鬼"].range_zones)

    def test_conditional_admission_and_empty_identity(self):
        world, people = materialize(self.world, self.people, [])
        self.assertIs(world, self.world)
        self.assertIs(people, self.people)
        path, folder = self.write_materialized(proposal("村"))
        self.assertEqual(contract_check(path, folder, proposal("村"), action_graph_path=self.template / "action_graph.yaml")["violations"], [])

    def test_negative_check_is_not_poisoned_by_an_already_satisfied_unrelated_exclude(self):
        # N3 (WB-WORLDGROW-001, Astra review), real momotaro data: 桃太郎
        # already excludes 村 until he holds 鬼ヶ島の宝物 (active: he starts
        # without it). Adding an unrelated, already-satisfied exclude rule
        # (until_item: きびだんご, which he starts holding x3) targeting 道中
        # -- the *only* route neighbor of 村 -- must not exclude 道中 from
        # the negative check's start-location search for the unrelated 村
        # rule; he can freely enter 道中.
        people = deepcopy(self.people)
        momotaro_key = next(k for k, v in people.items() if v.get("id") == "桃太郎")
        people[momotaro_key]["range"]["exclude"].append({"zones": ["道中"], "until_item": "きびだんご"})
        p = proposal("村")
        world, materialized_people = materialize(self.world, people, [p])
        path = self.root / "patched_n3.yaml"
        path.write_text(yaml.safe_dump(world, allow_unicode=True), encoding="utf-8")
        folder = self.root / "subjects_n3"
        folder.mkdir()
        for name, person in materialized_people.items():
            (folder / name).write_text(yaml.safe_dump(person, allow_unicode=True), encoding="utf-8")
        result = contract_check(path, folder, p, action_graph_path=self.template / "action_graph.yaml")
        self.assertEqual(result["violations"], [])
        momotaro_entries = [n for n in result["negative"] if n["subject"] == "桃太郎"]
        checked = [n for n in momotaro_entries if n["status"] == "checked"]
        self.assertTrue(checked, momotaro_entries)
        self.assertEqual(checked[0]["start"], "道中")
        self.assertFalse([n for n in momotaro_entries if n["status"] == "skipped"], momotaro_entries)

    def test_budgets_effective_defaults_and_bypass_keeps_safety(self):
        p = proposal()
        p["add"]["items"] *= 3
        for n, item in enumerate(p["add"]["items"]):
            p["add"]["items"][n] = dict(item, name=f"帆布試験{n}")
        errors = validate_patch(self.world, p)
        self.assertTrue(any("give" in e for e in errors))
        self.assertEqual(validate_patch(self.world, p, check_budgets=False), [])
        p["add"]["daily_events"] = []
        self.assertTrue(validate_patch(self.world, p, check_budgets=False))
        p = proposal()
        p["add"] = {"facts": [{"id": "最初の事実", "label": "試験", "sources": [{"type": "investigate", "zone": "海", "count": 1}]}]}
        world = dict(self.world, facts=[])
        self.assertEqual(validate_patch(world, p), [])

    def test_materialize_derives_give_available_from_subjects(self):
        # M2: materialize() used to always re-validate with the
        # validate_patch default (give_available=True), so a give-less,
        # plentiful item (sources.max:3) that's fine in a world with no
        # give_item verb (e.g. detective) still failed the give budget at
        # materialize() time, even though it already passed the static gate
        # with the correct give_available=False.
        detective_dir = ROOT / "projects" / "detective"
        detective_world = yaml.safe_load((detective_dir / "world.yaml").read_text(encoding="utf-8"))
        detective_people = read_subjects(detective_dir / "subjects")
        add = {"zones": [], "items": [{"name": "手がかりの品",
               "sources": [{"type": "investigate", "zone": "書斎", "count": 1, "max": 3}]}]}
        patch = {"id": patch_id_for(add), "title": "給付試験", "add": add}
        materialize(detective_world, detective_people, [patch])  # must not raise

        # The same give-less max:3 item, in momotaro (桃太郎 has give_item),
        # still costs give budget at the engine's default affinities.
        momotaro_add = {"zones": [], "items": [{"name": "手がかりの品",
                        "sources": [{"type": "investigate", "zone": "海", "count": 1, "max": 3}]}]}
        momotaro_patch = {"id": patch_id_for(momotaro_add), "title": "給付試験2", "add": momotaro_add}
        with self.assertRaises(PatchError):
            materialize(self.world, self.people, [momotaro_patch])

    def test_content_digest_tracks_role_not_filename(self):
        template = self.root / "template"
        template.mkdir()
        paths = [self.root / "a/effects.yaml", self.root / "b/effects.yaml"]
        for n, path in enumerate(paths):
            path.parent.mkdir()
            path.write_text(f"value: {n}", encoding="utf-8")
        world = {"gapengine": {"effects": str(paths[0])}}
        first = inputs_digest(world, {}, template, {"effects": paths[0]})
        world["gapengine"]["effects"] = str(paths[1])
        self.assertNotEqual(first, inputs_digest(world, {}, template, {"effects": paths[1]}))
        paths[1].write_bytes(paths[0].read_bytes())
        self.assertEqual(first, inputs_digest(world, {}, template, {"effects": paths[1]}))

    def test_runtime_identity_ignores_git_and_paths_not_bytes(self):
        manifest = {"files": [{"path": "engine/a.py", "sha256": "a", "bytes": 1, "source_path": "c:/x"}],
                    "python": "3", "pyyaml": "6", "head": "a", "dirty": False}
        with mock_patch("gapengine.world_patch_inputs.code_snapshot", return_value=({}, manifest)):
            first = runtime_digest()
            manifest.update(head="b", dirty=True)
            manifest["files"][0]["source_path"] = "d:/x"
            self.assertEqual(first, runtime_digest())
            manifest["files"][0]["sha256"] = "b"
            self.assertNotEqual(first, runtime_digest())

    def test_stack_corruption_applicability_and_reopen(self):
        p = write_approved(self.project, proposal(), self.template)
        world, people, verified = expanded_snapshot(self.project, self.template)
        self.assertIn("船大工の小屋", [z["name"] for z in world["zones"]])
        original = (self.project / "world.yaml").read_bytes()
        (self.project / "world.yaml").write_bytes(original + b"\nname: changed\n")
        with self.assertRaisesRegex(PatchError, "reopen"):
            expanded_snapshot(self.project, self.template)
        self.assertEqual(reopen(self.project), [p["id"]])
        self.assertEqual(verify_stack(self.project), [])
        gate = json.loads((self.project / "patches/_proposed" / f"{p['id']}.gate.json").read_text(encoding="utf-8"))
        self.assertEqual(gate["status"], "trial_pending")
        self.assertIsNone(gate["trial"])
        self.assertTrue(list((self.project / "patches/_history").glob("*/stack.json")))

    def test_applicable_snapshot_resolves_gapengine_references_via_repo_root(self):
        # R4: applicable_snapshot (and approve(), which now forwards its own
        # repo_root the same way execution/configs.py's expanded_snapshot()
        # call already did) must resolve a project's gapengine action_graph/
        # effects references against a caller-supplied repo_root -- not
        # silently fall back to this module's own repo, which would mask a
        # broken reference whenever the relative path happens to also exist
        # there.
        with tempfile.TemporaryDirectory() as tmp:
            control_repo = Path(tmp) / "control-repo"
            shutil.copytree(self.template, control_repo / "templates" / "unreal-template")
            world = dict(self.world)
            world["gapengine"] = {"action_graph": "templates/unreal-template/action_graph.yaml",
                                   "effects": "templates/unreal-template/effects.yaml"}
            (self.project / "world.yaml").write_text(
                yaml.safe_dump(world, allow_unicode=True, sort_keys=False), encoding="utf-8")
            other_template = control_repo / "templates" / "unreal-template"
            with patch_lock(self.project), self.assertRaises(PatchError):
                applicable_snapshot(self.project, other_template)
            with patch_lock(self.project):
                verified, stack, w, people, refs, digest_ = applicable_snapshot(
                    self.project, other_template, repo_root=control_repo)
        self.assertEqual(verified, [])
        self.assertEqual(refs["action_graph"], (other_template / "action_graph.yaml").resolve())

    def test_check_reaches_external_repo_action_graph_and_effects_via_repo_root(self):
        # N2 (WB-WORLDGROW-001, Astra review): --repo/repo_root is resolved
        # fine by the CLI's own initial ctx (_resolve_ctx), but check/
        # propose used to re-resolve everything themselves without
        # forwarding it, silently falling back to this checkout's own repo.
        # Reproduces both failure modes named in Astra's review: a
        # world.gapengine.action_graph reference resolve_references can't
        # find without repo_root (raises "参照先がありません: action_graph"
        # straight out of check), and a world.gapengine.effects reference
        # the *engine* can only load once the world file actually written
        # for it carries the absolute path (raises "Effect library does not
        # exist" from inside run_individual, even once resolve_references
        # itself succeeds) -- both action_graph.yaml and effects.yaml here
        # exist only under `control`, not under this checkout's own ROOT.
        #
        # This fixture is necessarily an "expanded_project"-sourced
        # experiment (not frozen), the only source repo_root ever affects --
        # trial_state() requires base_source == "frozen_inputs" to ever
        # reach "reviewable", and a frozen experiment's own repo is always
        # experiment/inputs regardless of repo_root. So the reachable,
        # meaningful assertion here is that the trial actually *runs* to a
        # clean reference_only (engine constructed successfully against the
        # external effects.yaml, reproduction matched) instead of failing
        # on a reference lookup -- not "reviewable" itself.
        import scripts.world_patch as wpc
        with tempfile.TemporaryDirectory() as root:
            experiment, project, template, control = external_repo_experiment(Path(root), individuals=3)
            self.assertFalse((ROOT / "lib" / "action_graph.yaml").exists())
            # A single item with two sources -- one in the added branch (祠,
            # satisfies A2) and one in the trigger zone itself (村, satisfies
            # A1) -- so this stays within this fixture's items cap of 1.
            add = {"zones": [{"name": "祠", "parent": "村"}],
                   "items": [{"name": "石版", "sources": [
                       {"type": "investigate", "zone": "祠", "count": 1, "max": 1},
                       {"type": "investigate", "zone": "村", "count": 1, "max": 1}]}]}
            patch = {"id": patch_id_for(add), "title": "外部repo参照試験", "add": add,
                     "trigger": {"zone": "村", "verb": "investigate"}, "parent_digest": EMPTY_STACK_DIGEST}
            subject_ids = wpc._subject_ids(project / "subjects")
            _, ctx = wpc._resolve_ctx(experiment, template, repo_root=control)

            # Today's actual bug, reproduced directly: _gate (exactly as
            # cmd_check/cmd_propose call it) drops repo_root even though the
            # ctx above already resolved it correctly.
            # This fixture's momotaro/oni subjects were trimmed down to
            # verbs=["move", "investigate"]/["rest"] -- no give_item, so
            # give_available is False here (R3: _gate no longer defaults it).
            with self.assertRaisesRegex(PatchError, "action_graph"):
                wpc._gate(experiment, project, patch, ctx, subject_ids, skip_trial=False,
                          max_runs=3, seeds_per_run=4, seed_set="holdout", template_dir=template,
                          give_available=False)

            # Fixed: forwarding repo_root all the way through reaches a
            # real, error-free trial -- which requires the engine to have
            # actually constructed and run against the external
            # effects.yaml too, not just resolved the reference for the
            # inputs digest.
            gate = wpc._gate(experiment, project, patch, ctx, subject_ids, skip_trial=False,
                              max_runs=3, seeds_per_run=4, seed_set="holdout", template_dir=template,
                              repo_root=control, give_available=False)
            self.assertEqual(gate["status"], "reference_only", gate)
            trial = gate["trial"]
            self.assertEqual(trial["errors"], [])
            self.assertGreater(trial["reproduction"]["checked"], 0)
            self.assertEqual(trial["reproduction"]["checked"], trial["reproduction"]["identical"])
            self.assertEqual(trial["contract"]["violations"], [])

    def test_repair_does_not_move_committed_files_or_overwrite_proposals(self):
        p = write_approved(self.project, proposal(), self.template)
        folder = self.project / "patches"
        committed = (folder / f"{p['id']}.yaml").read_bytes()
        (folder / "p-stray.yaml").write_text("id: p-stray", encoding="utf-8")
        with self.assertRaisesRegex(PatchError, "repair"):
            verify_stack(self.project)
        self.assertEqual(repair(self.project), ["p-stray.yaml"])
        self.assertEqual((folder / f"{p['id']}.yaml").read_bytes(), committed)
        (folder / "p-stray.yaml").write_text("new", encoding="utf-8")
        with self.assertRaisesRegex(PatchError, "競合"):
            repair(self.project)
        (folder / f"{p['id']}.yaml").write_bytes(committed + b"# edited")
        with self.assertRaises(PatchError):
            verify_stack(self.project)

    def test_status_and_disjoint_seeds(self):
        self.assertEqual(gate_status({"static": {"passed": True}, "trial": None}), "trial_pending")
        individuals = [{"cell": f"c{n}", "generation": 0, "index": n} for n in range(3)]
        trial = {"runs": 999, "errors": [], "contract": {"violations": []},
                 "reproduction": {"checked": 1, "identical": 1}, "evidence": {
                    "base_source": "frozen_inputs", "engine_hash_experiment": "x", "engine_hash_trial": "x",
                    "seeds": [1, 2, 3, 4], "individuals": individuals}}
        gate = {"static": {"passed": True}, "trial": trial}
        self.assertEqual(gate_status(gate), "reviewable")
        # R1: a self-reported runs=999 must not paper over too few individuals
        # actually recorded as evidence.
        trial["evidence"]["individuals"] = individuals[:2]
        self.assertEqual(gate_status(gate), "insufficient")
        # N1: nor does padding with copies of the same (generation, index).
        trial["evidence"]["individuals"] = [individuals[0]] * 3
        self.assertEqual(gate_status(gate), "insufficient")
        trial["evidence"]["individuals"] = individuals
        trial["reproduction"]["checked"] = 0
        self.assertEqual(gate_status(gate), "reference_only")
        trial["errors"] = ["error"]
        self.assertEqual(gate_status(gate), "contract_failed")
        old = [100000, 200000, 1100000, 1200000]
        seeds = seed_sets(old, 4)
        self.assertFalse(set(seeds["exploration"]) & set(old + seeds["holdout"]))
        self.assertFalse(set(seeds["holdout"]) & set(old))

    def test_typed_usage_not_substrings_or_duplicate_learning(self):
        rows = [{"kind": "snapshot", "subject": "桃太郎", "layers": {"zone": "海"}},
                {"kind": "decision", "subject": "桃太郎", "verb": "move", "result": "moved", "delta": {"actor": {"zone": "船大工の小屋"}}},
                {"kind": "decision", "subject": "桃太郎", "verb": "investigate", "result": "investigated", "details": {"learned": ["噂A"], "gathered": [{"item": "古びた帆布"}]}},
                {"kind": "event", "subject": "桃太郎", "verb": "learn_fact", "details": {"fact": "噂A"}},
                {"kind": "decision", "subject": "桃太郎", "verb": "give_item", "result": "invalid", "args": ["犬", "古びた帆布"]},
                {"kind": "decision", "subject": "桃太郎", "verb": "give_item", "result": "given", "args": ["犬", "古びた帆布"]}]
        path = self.root / "layers.jsonl"
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
        p = proposal()
        p["add"]["facts"] = [{"id": "噂A"}]
        counts = new_usage([path], p, "桃太郎")
        self.assertEqual(counts, {"decisions_in_new_zones": 3, "moves_into_new_zones": 1,
            "gathered_new_items": 1, "learned_new_facts": 1, "shared_new_facts": 0, "gave_new_items": 1})

    def test_summary_only_expansion_and_missing_details(self):
        from viewer.data import RunRepository, world_expansion_state
        from viewer.pages import _world_expansion_line
        experiment = self.root / "sample"
        experiment.mkdir()
        repository = RunRepository(self.root)
        summary = experiment / "summary.json"
        summary.write_text(json.dumps({"world_patches": ["p-a"]}), encoding="utf-8")
        state = world_expansion_state(repository, experiment)
        self.assertEqual(state["state"], "unknown")
        self.assertIn("不明", _world_expansion_line(state))
        summary.write_text(json.dumps({"world_patches": ["p-a"],
            "world_expansion_patches": [{"id": "p-a", "title": "test", "added": {}}]}), encoding="utf-8")
        self.assertEqual(world_expansion_state(repository, experiment)["state"], "expanded")

    def test_cli_expansion_extends_subjects_and_resolves_recorded_template(self):
        from scripts.evolve import _expanded_project
        from gapengine.world_patch_inputs import resolve_experiment_inputs
        write_approved(self.project, proposal(), self.template)
        out = self.root / "cli-run"
        expanded = _expanded_project(self.project, out, self.template)
        subjects = read_subjects(expanded / "subjects")
        hero = next(p for p in subjects.values() if p["id"] == "桃太郎")
        self.assertIn("船大工の小屋", hero["range"]["zones"])
        resolved = resolve_experiment_inputs(out)
        self.assertEqual(resolved["source"], "expanded_project")
        self.assertEqual(resolved["template_dir"], self.template)
        source = expanded / "source.json"
        record = json.loads(source.read_text(encoding="utf-8"))
        record["template_digest"] = "changed"
        source.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(PatchError, "--template"):
            resolve_experiment_inputs(out)

    def test_gate_parent_and_hash_tampering_are_rejected(self):
        p = write_approved(self.project, proposal(), self.template)
        folder = self.project / "patches"
        gate = folder / f"{p['id']}.gate.json"
        original = gate.read_bytes()
        gate.write_bytes(original + b" ")
        with self.assertRaises(PatchError):
            verify_stack(self.project)
        gate.write_bytes(original)
        manifest = folder / "stack.json"
        stack = json.loads(manifest.read_text(encoding="utf-8"))
        stack["revisions"][0]["parent_digest"] = "wrong"
        manifest.write_text(json.dumps(stack), encoding="utf-8")
        with self.assertRaises(PatchError):
            verify_stack(self.project)


class FrozenReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.experiment, cls.project, cls.template = frozen_experiment(Path(cls.temp.name), explanations=False)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_explanation_off_reproduces_and_corruption_fails_closed(self):
        before = {p.relative_to(self.experiment): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in self.experiment.rglob("*") if p.is_file()}
        with tempfile.TemporaryDirectory() as work:
            result = run_trial(self.experiment, proposal(), work_dir=Path(work), max_runs=3, seeds_per_run=4)
        self.assertEqual(result["reproduction"]["checked"], result["reproduction"]["identical"])
        self.assertGreater(result["reproduction"]["checked"], 0)
        after = {p.relative_to(self.experiment): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in self.experiment.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        frozen = self.experiment / "inputs/projects/momotaro/world.yaml"
        original = frozen.read_bytes()
        try:
            frozen.write_bytes(original + b"# changed")
            with tempfile.TemporaryDirectory() as work, self.assertRaisesRegex(PatchError, "封印"):
                run_trial(self.experiment, proposal(), work_dir=Path(work))
        finally:
            frozen.write_bytes(original)

    def test_new_usage_counts_a_real_branch_entry_from_an_actual_run(self):
        # WB-WORLDGROW-001 M1: test_typed_usage_not_substrings_or_duplicate_learning
        # (above) only ever counts hand-written JSONL rows -- no acceptance
        # test ran a real engine simulation through a branch and checked that
        # new_usage actually saw it. branch_entry_experiment trims a momotaro
        # copy down to one zone/subject pair so a neutral-genome run reliably
        # wanders into an added branch within ~2 days.
        with tempfile.TemporaryDirectory() as root:
            experiment, template = branch_entry_experiment(Path(root))
            add = {"zones": [{"name": "枝分岐", "parent": "村"}],
                   "items": [{"name": "試験アイテム",
                              "sources": [{"type": "investigate", "zone": "枝分岐", "count": 1, "max": 3}]}]}
            patch = {"id": patch_id_for(add), "title": "枝への到達試験", "add": add}
            with tempfile.TemporaryDirectory() as work:
                trial = run_trial(experiment, patch, work_dir=Path(work), template_dir=template,
                                   max_runs=5, seeds_per_run=4, seed_set="exploration")
        self.assertEqual(trial["errors"], [])
        self.assertGreaterEqual(trial["runs"], 1)
        usage = trial["new_usage"]
        self.assertGreaterEqual(usage["moves_into_new_zones"], 1, usage)
        self.assertGreaterEqual(usage["decisions_in_new_zones"], 1, usage)
        self.assertGreaterEqual(usage["gathered_new_items"], 1, usage)

    def test_new_usage_stays_zero_when_a_branch_is_never_reached(self):
        # Contrast for the acceptance test above: the shared FrozenReplay
        # fixture's real, un-trimmed momotaro world never actually wanders
        # into proposal()'s branch off 海 within its forced 1-day return.
        with tempfile.TemporaryDirectory() as work:
            trial = run_trial(self.experiment, proposal(), work_dir=Path(work), max_runs=3, seeds_per_run=4)
        usage = trial["new_usage"]
        self.assertEqual(usage["moves_into_new_zones"], 0, usage)
        self.assertEqual(usage["decisions_in_new_zones"], 0, usage)
        self.assertEqual(usage["gathered_new_items"], 0, usage)

    def _reviewable_patch(self, project, add, title):
        """Static+trial-gate a real patch against the shared frozen fixture
        and stage it in `project`'s _proposed/, the way `scripts.world_patch
        propose`+`check` would -- so an approve() race has genuine evidence
        to fight over, not a hand-built stub."""
        import scripts.world_patch as wpc
        from gapengine import lineage as lineage_engine
        from viewer.data import RunRepository

        repository = RunRepository(self.experiment.parent)
        ctx = lineage_engine._resolve_world_context(repository, self.experiment, template_dir=self.template)
        trigger = {"zone": add["zones"][0]["parent"], "verb": "investigate"}
        patch = {"id": patch_id_for(add), "title": title, "trigger": trigger,
                 "parent_digest": EMPTY_STACK_DIGEST, "add": add}
        subject_ids = wpc._subject_ids(ctx["subjects_dir"])
        # frozen_experiment() copies the full momotaro project, whose 桃太郎
        # has give_item -- give_available=True here (R3: _gate no longer
        # defaults it).
        gate = wpc._gate(self.experiment, project, patch, ctx, subject_ids, skip_trial=False,
                          max_runs=5, seeds_per_run=4, seed_set="holdout", template_dir=self.template,
                          give_available=True)
        self.assertEqual(gate["status"], "reviewable", gate)
        raw = yaml.safe_dump(patch, allow_unicode=True, sort_keys=False).encode("utf-8")
        proposed_dir = project / "patches" / "_proposed"
        with patch_lock(project):
            proposed_dir.mkdir(parents=True, exist_ok=True)
            (proposed_dir / f"{patch['id']}.yaml").write_bytes(raw)
        wpc._save_gate(project, proposed_dir / f"{patch['id']}.yaml", raw, gate)
        return patch["id"]

    def test_concurrent_approval_of_two_different_patches_publishes_exactly_one(self):
        # R5: two different reviewable patches proposed against the same
        # head, approved from two threads at once -- exactly one wins;
        # stack.json ends up with a single revision that verify_stack
        # accepts; the loser gets PatchError, never a bare FileNotFoundError
        # from racing the winner's os.replace() of the same _proposed/ dir.
        from execution.world_patch_approval import approve as approve_patch

        with tempfile.TemporaryDirectory() as root:
            project = Path(root) / "momotaro"
            shutil.copytree(self.project, project, ignore=shutil.ignore_patterns("patches"))
            # Each item sources from both its added branch (A2) and the
            # branch's own parent, standing in as the trigger zone (A1).
            # max:1 each (not 2) keeps the implicit give budget -- items with
            # no explicit `give` still count at the engine's default
            # affinities x total source max -- within MAX_GIVE_PER_PATCH.
            add_a = {"zones": [{"name": "小屋A", "parent": "海"}],
                     "items": [{"name": "アイテムA", "sources": [
                         {"type": "investigate", "zone": "小屋A", "count": 1, "max": 1},
                         {"type": "investigate", "zone": "海", "count": 1, "max": 1}]}]}
            add_b = {"zones": [{"name": "小屋B", "parent": "森"}],
                     "items": [{"name": "アイテムB", "sources": [
                         {"type": "investigate", "zone": "小屋B", "count": 1, "max": 1},
                         {"type": "investigate", "zone": "森", "count": 1, "max": 1}]}]}
            pid_a = self._reviewable_patch(project, add_a, "並行試験A")
            pid_b = self._reviewable_patch(project, add_b, "並行試験B")

            results = {}

            def attempt(pid):
                try:
                    approve_patch(project, self.template, pid, "並行承認のテストとして確認した")
                    return True
                except PatchError:
                    return False

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = {pool.submit(attempt, pid): pid for pid in (pid_a, pid_b)}
                for future in futures:
                    results[futures[future]] = future.result()

            self.assertEqual(sorted(results.values()), [False, True])
            stack = read_stack(project)
            self.assertEqual(len(stack["revisions"]), 1)
            self.assertEqual(len(verify_stack(project)), 1)  # does not raise
            winner = stack["revisions"][0]["patch_id"]
            self.assertIn(winner, (pid_a, pid_b))
            self.assertEqual(results[winner], True)

    def test_coevolved_antagonist_reproduces_and_is_bound_in_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            experiment, _, _ = frozen_experiment(root, coevolve=True)
            # Record an actual matchup in this temporary fixture. Its short
            # always-return world need not populate the antagonist archive.
            from gapengine import lineage
            from gapengine.evolve import run_individual
            from gapengine.world_patch_trial import _job
            from viewer.data import RunRepository
            archive_path = experiment / "archive.json"
            archive = json.loads(archive_path.read_text(encoding="utf-8"))
            elite = next(iter(archive["cells"].values()))
            opponents = json.loads((experiment / "g0/results.antagonist.json").read_text(encoding="utf-8"))
            opponent = opponents[0]["genome"]
            ctx = lineage._resolve_world_context(RunRepository(experiment.parent), experiment)
            job = _job(ctx=ctx, world_path=ctx["world_path"], out_dir=experiment / "g0/ind-99",
                elite=elite, index=99, seeds=[31],
                precedent_json=(experiment / "g0/precedent.json").read_text(encoding="utf-8"),
                antagonist_precedent_json=(experiment / "g0/precedent.antagonist.json").read_text(encoding="utf-8"),
                antagonist_genome=opponent)
            job["logical_root"] = str(experiment)
            recorded = run_individual(job)["runs"][0]
            archive["cells"]["fixture-matchup"] = {"quality": 1000, "generation": 0,
                "genome": elite["genome"], "exemplar": {**recorded, "antagonist_genome": opponent}}
            archive_path.write_text(json.dumps(archive, ensure_ascii=False), encoding="utf-8")
            result = run_trial(experiment, proposal(), work_dir=root / "trial", max_runs=3, seeds_per_run=4)
            self.assertGreater(result["reproduction"]["checked"], 0, repr({k: result[k] for k in ("runs", "skipped", "errors", "reproduction")}))
            self.assertEqual(result["reproduction"]["checked"], result["reproduction"]["identical"])
            self.assertEqual(result["errors"], [])
            self.assertTrue(result["evidence"]["individuals"])
            self.assertTrue(any(i["antagonist_genome_sha256"] for i in result["evidence"]["individuals"]))
            self.assertTrue(any(i["antagonist_genome_sha256"] is None for i in result["evidence"]["individuals"]))
            for individual in result["evidence"]["individuals"]:
                self.assertTrue(individual["antagonist_precedent_sha256"])


class ExternalRepoApproval(unittest.TestCase):
    """N2 (WB-WORLDGROW-001, Astra re-review): approve()'s *own* repo_root
    use is a second, separate resolution from run_trial's -- it recomputes
    base/patched inputs digests from the *live* --project's own world.yaml
    via applicable_snapshot(project, template, repo_root=...), not from the
    frozen experiment's self-contained inputs/ (which never needs repo_root
    at all). Builds both frozen fixtures once per class (each is a real,
    if small, evolve() run) and drives propose/check/approve through the
    same argparse entry points scripts/world_patch.py's CLI uses."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        (cls.ext_experiment, cls.ext_project, cls.ext_template,
         cls.ext_repo) = frozen_experiment_with_external_reference(root / "external", mode="external_only")
        (cls.col_experiment, cls.col_project, cls.col_template,
         cls.col_repo) = frozen_experiment_with_external_reference(root / "collision", mode="collision")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def _run(self, argv):
        import scripts.world_patch as wpc
        args = wpc.build_parser().parse_args(argv)
        return args.func(args)

    def _propose_check(self, experiment, project, template, repo):
        proposal_path = Path(project).parent / "proposal.json"
        # One item with two sources -- 祠 (the added branch, A2) and 海 (the
        # trigger zone itself, A1).
        add = {"zones": [{"name": "祠", "parent": "海"}],
               "items": [{"name": "石版", "sources": [
                   {"type": "investigate", "zone": "祠", "count": 1, "max": 1},
                   {"type": "investigate", "zone": "海", "count": 1, "max": 1}]}]}
        proposal_path.write_text(json.dumps({"title": "外部repo受入試験", "rationale": "試験", "add": add},
                                             ensure_ascii=False), encoding="utf-8")
        code = self._run(["propose", "--experiment", str(experiment), "--project", str(project),
                           "--from-file", str(proposal_path), "--template", str(template), "--repo", str(repo),
                           "--max-runs", "5", "--seeds-per-run", "4"])
        self.assertEqual(code, 0)
        patch_id = next((project / "patches" / "_proposed").glob("*.yaml")).stem
        code = self._run(["check", "--experiment", str(experiment), "--project", str(project), "--patch", patch_id,
                           "--template", str(template), "--repo", str(repo), "--max-runs", "5", "--seeds-per-run", "4"])
        self.assertEqual(code, 0)
        gate = json.loads((project / "patches" / "_proposed" / f"{patch_id}.gate.json").read_text(encoding="utf-8"))
        self.assertEqual(gate["status"], "reviewable", gate)
        return patch_id, gate

    def test_action_graph_and_effects_only_in_external_repo_round_trip_via_repo_root(self):
        # Point 1 (Astra re-review): action_graph/effects exist only under
        # `ext_repo`'s templates/_only_in_external/ -- not under this
        # checkout's own ROOT.
        self.assertFalse((ROOT / "templates" / "_only_in_external").exists())
        experiment, project, template, repo = (self.ext_experiment, self.ext_project,
                                                self.ext_template, self.ext_repo)
        patch_id, gate = self._propose_check(experiment, project, template, repo)

        # Without --repo, approve() re-resolves the *live* project's own
        # gapengine reference against this checkout's own default repo and
        # must not succeed -- either it can't resolve the reference at all,
        # or (covered by the collision test below) it resolves the wrong
        # file's content.
        with self.assertRaises(PatchError):
            approve_patch(project, template, patch_id, "外部repo参照の受入試験として確認する")
        self.assertTrue((project / "patches" / "_proposed" / f"{patch_id}.yaml").is_file())
        self.assertFalse((project / "patches" / "stack.json").is_file())

        # With --repo, it must actually publish.
        revision = approve_patch(project, template, patch_id,
                                  "外部repo参照の受入試験として確認する", repo_root=repo)
        self.assertEqual(revision["rev"], 1)
        self.assertEqual(len(verify_stack(project)), 1)

        # Point 3: the sealed base_inputs_digest matches an independent
        # applicable_snapshot(..., repo_root=repo) computation against the
        # same live project/template -- project_inputs()'s own base_digest
        # is computed from the project's raw world.yaml/subjects regardless
        # of what's since been approved, so this still recomputes the same
        # base the trial measured (approve() never touches world.yaml).
        with patch_lock(project):
            _, _, _, _, _, base_digest = applicable_snapshot(project, template, repo_root=repo)
        self.assertEqual(gate["trial"]["evidence"]["base_inputs_digest"], base_digest)

    def test_same_relative_path_colliding_with_this_checkouts_own_repo_is_not_mixed_up(self):
        # Point 2 (Astra re-review): gapengine.effects keeps the normal
        # templates/momotaro/effects.yaml path, which also exists verbatim
        # under this checkout's own ROOT -- only the *content* differs
        # (col_repo's own copy has one effect's modifier value changed).
        self.assertNotEqual(
            (self.col_repo / "templates/momotaro/effects.yaml").read_bytes(),
            (ROOT / "templates/momotaro/effects.yaml").read_bytes())
        experiment, project, template, repo = (self.col_experiment, self.col_project,
                                                self.col_template, self.col_repo)
        patch_id, gate = self._propose_check(experiment, project, template, repo)

        # Without --repo, resolve_references *can* find a same-named file
        # under this checkout's own ROOT -- it must not silently accept
        # that wrong-content file; the input digest comparison must catch
        # the mismatch and reject with the "inputs changed" reason, not a
        # bare reference-not-found error.
        with self.assertRaisesRegex(PatchError, "入力"):
            approve_patch(project, template, patch_id, "取り違え検知の受入試験として確認する")
        self.assertTrue((project / "patches" / "_proposed" / f"{patch_id}.yaml").is_file())

        revision = approve_patch(project, template, patch_id,
                                  "取り違え検知の受入試験として確認する", repo_root=repo)
        self.assertEqual(revision["rev"], 1)
        self.assertEqual(len(verify_stack(project)), 1)
