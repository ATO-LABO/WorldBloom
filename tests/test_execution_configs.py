"""Acceptance boundaries for immutable settings and run preparation (UI-002)."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml
from world_patch_fixtures import write_approved
from gapengine.world_patch import patch_id_for

from execution.configs import ConfigStore, evolution_defaults, normalize
from execution.provenance import ConfigError, atomic_json, canonical, directory_lock, sha256
from scripts.evolve import build_parser

ROOT = Path(__file__).resolve().parents[1]


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wb-ui002-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        for name in ("projects", "templates"):
            shutil.copytree(ROOT / name, self.repo / name)
        self.store = ConfigStore(self.repo, self.base / "control", self.base / "runs")
        self.spec = {"label": "確認用", "project_id": "romance", "template_id": "romance",
                     "evolution": {"generations": 1, "population": 2, "seeds": 1, "keep": "all"}}

    def runtime(self):
        for name in ("engine", "gapengine", "scripts", "execution"):
            shutil.copytree(ROOT / name, self.repo / name, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copyfile(ROOT / "requirements.txt", self.repo / "requirements.txt")

    def save(self):
        return self.store.save(self.spec, config_id="cfg-test")

    def test_preview_is_read_only_and_defaults_match_cli(self):
        before = {p.relative_to(self.repo): p.read_bytes() for p in self.repo.rglob("*") if p.is_file()}
        preview = self.store.preview(self.spec)
        self.assertFalse(self.store.control.exists())
        self.assertFalse(self.store.runs.exists())
        self.assertEqual(before, {p.relative_to(self.repo): p.read_bytes() for p in self.repo.rglob("*") if p.is_file()})
        parsed = vars(build_parser().parse_args(["--project", "x", "--template", "y", "--out", "z"]))
        for key, value in evolution_defaults().items():
            self.assertEqual(value, parsed[key], key)
        self.assertTrue(preview["evolution"]["record_explanations"])
        self.assertEqual(preview["preview"]["target_endings"], ["mutual"])

    def test_three_genres_and_50_generations(self):
        for genre in ("romance", "detective", "momotaro"):
            spec = {**self.spec, "project_id": genre, "template_id": genre,
                    "evolution": {"generations": 50, "population": 3, "seeds": 2, "coevolve": True}}
            with self.subTest(genre=genre):
                result = self.store.preview(spec)
                self.assertEqual(result["preview"]["planned_seed_evaluations"], 600)
                self.assertFalse(self.store.control.exists())

    def test_reject_invalid_scalar_values_and_unknown_fields(self):
        for field in ("generations", "population", "seeds", "processes"):
            for bad in (True, False, 0, -1, 1.5, "", "2", None):
                with self.subTest(field=field, bad=bad):
                    with self.assertRaises(ConfigError):
                        self.store.preview({**self.spec, "evolution": {field: bad}})
        for changes in ({"seed_base": -1}, {"ga_seed": True}, {"keep": "bad"},
                        {"coevolve": 1}, {"record_explanations": "true"},
                        {"target_ending": []}, {"target_ending": "mutual"},
                        {"target_ending": ["mutual", "mutual"]}, {"mutation": 0.9},
                        {"world_expansion": "bogus"}, {"world_expansion": "propose"}):
            with self.subTest(changes=changes), self.assertRaises(ConfigError):
                self.store.preview({**self.spec, "evolution": changes})
        for key in ("out", "api_key", "runtime", "argv"):
            with self.subTest(key=key), self.assertRaises(ConfigError):
                self.store.preview({**self.spec, key: "not-allowed"})

    def assert_invalid_rules_rejected_before_save(self, change):
        original = self.save()
        before = {p.relative_to(self.store.control): p.read_bytes()
                  for p in self.store.control.rglob("*") if p.is_file()}
        path = self.repo / "templates/romance/rules.yaml"
        rules = yaml.safe_load(path.read_text(encoding="utf-8"))
        change(rules)
        path.write_text(yaml.safe_dump(rules, allow_unicode=True), encoding="utf-8")
        for coevolve in (False, True):
            spec = deepcopy(self.spec)
            spec["evolution"].update(coevolve=coevolve, meta_evolution=coevolve)
            for operation in ("preview", "save"):
                with self.subTest(coevolve=coevolve, operation=operation):
                    with self.assertRaises(ConfigError) as caught:
                        if operation == "preview":
                            self.store.preview(spec)
                        else:
                            self.store.save(spec, config_id="cfg-invalid")
                    self.assertEqual(caught.exception.code, "invalid_config")
                    self.assertIn("inputs.rules.yaml", caught.exception.field_errors)
                    self.assertFalse((self.store.control / "configs/cfg-invalid").exists())
                    self.assertFalse(self.store.runs.exists())
                    self.assertEqual(self.store.get("cfg-test"), original)
                    self.assertEqual(before, {p.relative_to(self.store.control): p.read_bytes()
                                             for p in self.store.control.rglob("*") if p.is_file()})

    def test_invalid_rule_scope_is_rejected_before_save(self):
        self.assert_invalid_rules_rejected_before_save(
            lambda rules: rules[0].update(scope="unsupported"))

    def test_invalid_rule_adjustment_is_rejected_before_save(self):
        self.assert_invalid_rules_rejected_before_save(
            lambda rules: rules[0].update(adjust={"category_weight.I": "not-a-number"}))

    def test_invalid_rule_predicate_is_rejected_before_save(self):
        self.assert_invalid_rules_rejected_before_save(
            lambda rules: rules[0].update(when="("))

    def test_duplicate_rule_id_is_rejected_before_save(self):
        self.assert_invalid_rules_rejected_before_save(
            lambda rules: rules.append(deepcopy(rules[0])))

    def test_invalid_ending_and_missing_subject_are_rejected(self):
        with self.assertRaises(ConfigError):
            self.store.preview({**self.spec, "evolution": {"target_ending": ["nonexistent"]}})
        path = self.repo / "projects/romance/subjects/01_A.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["id"] = "renamed-in-isolated-fixture"
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        with self.assertRaises(ConfigError):
            self.store.preview(self.spec)

    def test_missing_world_and_duplicate_subject_rejected(self):
        with self.assertRaises(ConfigError):
            self.store.preview({**self.spec, "project_id": "unknown"})
        shutil.copyfile(self.repo / "projects/romance/subjects/01_A.yaml",
                        self.repo / "projects/romance/subjects/99_duplicate.yaml")
        with self.assertRaises(ConfigError):
            self.store.preview(self.spec)

    def test_snapshot_and_duplicate_ignore_original_changes(self):
        original = self.save()
        fixed = self.store.get("cfg-test")
        (self.repo / "projects/romance/world.yaml").write_text("invalid: current input", encoding="utf-8")
        self.assertEqual(self.store.get("cfg-test"), fixed)
        clone = self.store.duplicate("cfg-test", {"label": "別の版", "evolution": {"generations": 50}}, new_id="cfg-clone")
        self.assertEqual(clone["parent_config_id"], "cfg-test")
        self.assertEqual(clone["input_manifest_sha256"], original["input_manifest_sha256"])
        self.assertEqual(clone["evolution"]["generations"], 50)
        self.assertEqual(self.store.get("cfg-test"), fixed)
        self.assertEqual(len(self.store.list()), 2)

    def test_saved_data_return_values_do_not_mutate_store(self):
        document = self.save()
        document["evolution"]["generations"] = 999
        self.assertEqual(self.store.get("cfg-test")["evolution"]["generations"], 1)

    def test_tampered_snapshot_and_config_rejected(self):
        self.save()
        root = self.store.control / "configs/cfg-test"
        p = root / "inputs/projects/romance/world.yaml"
        original = p.read_bytes()
        p.write_bytes(original + b"\n# tampered")
        with self.assertRaises(ConfigError):
            self.store.get("cfg-test")
        p.write_bytes(original)
        config = root / "config.json"
        config.write_bytes(config.read_bytes() + b" ")
        with self.assertRaises(ConfigError):
            self.store.get("cfg-test")

    def test_extra_input_is_not_silently_loaded(self):
        self.save()
        target = self.store.control / "configs/cfg-test/inputs/projects/romance/subjects/extra.yaml"
        target.write_text("id: injected", encoding="utf-8")
        with self.assertRaises(ConfigError):
            self.store.get("cfg-test")

    def test_same_id_collision_does_not_overwrite(self):
        original = self.save()
        with self.assertRaises(ConfigError) as caught:
            self.store.save(self.spec, config_id="cfg-test")
        self.assertEqual(caught.exception.code, "conflict")
        self.assertEqual(self.store.get("cfg-test"), original)

    def test_concurrent_save_publishes_once(self):
        def attempt(_):
            try:
                return self.store.save(self.spec, config_id="cfg-race")["config_id"]
            except ConfigError:
                return "conflict"
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertEqual(sorted(results), ["cfg-race", "conflict"])
        self.assertEqual(len(self.store.list()), 1)

    def test_failed_publish_is_not_visible(self):
        with patch("execution.configs.atomic_json", side_effect=OSError("disk failed")):
            with self.assertRaises(OSError):
                self.save()
        self.assertEqual(self.store.list(), [])

    def test_os_lock_excludes_another_process(self):
        code = ("from pathlib import Path; from execution.provenance import directory_lock; "
                "exec('with directory_lock(Path(' + repr(__import__('sys').argv[1]) + ')):\\n pass')")
        folder = self.base / "lock"
        with directory_lock(folder):
            result = subprocess.run([sys.executable, "-B", "-c", code, str(folder)],
                                    cwd=ROOT, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"ConfigError", result.stderr)

    def test_root_and_id_boundaries(self):
        for bad in ("../cfg", "C:/tmp", "/root", "a/b", "a\\b", "", "CON:"):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                self.store.save(self.spec, config_id=bad)
        with self.assertRaises(ConfigError):
            ConfigStore(self.repo, self.repo / "output", self.base / "runs")
        with self.assertRaises(ConfigError):
            ConfigStore(self.repo, self.base / "control", self.base / "control/runs")

    def test_external_graph_reference_rejected(self):
        p = self.repo / "projects/romance/world.yaml"
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        outside = self.base / "outside.yaml"
        outside.write_text("nodes: []", encoding="utf-8")
        data.setdefault("gapengine", {})["action_graph"] = str(outside)
        p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        with self.assertRaises(ConfigError):
            self.store.preview(self.spec)

    def test_repo_relative_graph_rebased_without_changing_original(self):
        # An empty chosen template makes World use its world-side reference.
        (self.repo / "templates/blank").mkdir()
        (self.repo / "templates/blank/canon.yaml").write_text("{}", encoding="utf-8")
        p = self.repo / "projects/romance/world.yaml"
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        data.setdefault("gapengine", {})["action_graph"] = "templates/romance/action_graph.yaml"
        p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        original = p.read_bytes()
        spec = {**self.spec, "template_id": "blank"}
        result = self.store.save(spec, config_id="cfg-ref")
        manifest = json.loads((self.store.control / "configs/cfg-ref/input-manifest.json").read_text(encoding="utf-8"))
        record = next(r for r in manifest["files"] if r["path"].endswith("world.yaml"))
        self.assertEqual(record["source_sha256"], sha256(original))
        self.assertNotEqual(record["sha256"], record["source_sha256"])
        self.assertEqual(p.read_bytes(), original)
        self.assertIn("qd.yaml", result["preview"]["fallbacks"])
        self.assertEqual(self.store.duplicate("cfg-ref")["preview"]["target_endings"], ["mutual"])

    def test_generation_is_rejected_from_execution_config(self):
        # WB-UI-021: generation moved out to settings.json's "output" section
        # (execution/output_settings.py); an execution config must not accept
        # it any more, in any shape.
        for generation in ({"backend": "codex-cli"}, {"backend": "none"}, {"model": "explicit"}):
            with self.subTest(generation=generation), self.assertRaises(ConfigError):
                normalize({**self.spec, "generation": generation})

    def test_preview_and_save_carry_no_generation_section(self):
        preview = self.store.preview(self.spec)
        self.assertNotIn("generation", preview)
        self.assertNotIn("generation", preview["preview"])
        saved = self.save()
        self.assertNotIn("generation", saved)
        self.assertNotIn("generation", saved["preview"])
        self.assertNotIn("generation", self.store.duplicate("cfg-test", new_id="cfg-clone"))

    def test_prepare_does_not_spawn_and_freezes_code_inputs(self):
        self.runtime()
        original = self.save()
        with patch("execution.configs.code_snapshot", wraps=__import__("execution.provenance", fromlist=["code_snapshot"]).code_snapshot):
            manifest = self.store.prepare_run("cfg-test", run_id="run-test", job_id="job-test")
        self.assertEqual(manifest["status"], "prepared")
        self.assertFalse((self.store.runs / "run-test/archive.json").exists())
        self.assertEqual(manifest["input_manifest_sha256"], original["input_manifest_sha256"])
        self.assertIn("--record-explanations", manifest["argv"])
        self.assertIn("-I", manifest["argv"])
        (self.repo / "engine/__init__.py").write_text("# later edit", encoding="utf-8")
        (self.repo / "projects/romance/world.yaml").write_text("bad later input", encoding="utf-8")
        self.assertEqual(self.store.verify_run("run-test"), manifest)
        self.assertNotIn("bad later input", (self.store.runs / "run-test/inputs/projects/romance/world.yaml").read_text(encoding="utf-8"))
        with self.assertRaises(ConfigError):
            self.store.prepare_run("cfg-test", run_id="run-test", job_id="job-new")
        runtime = json.loads((self.store.runs / "run-test/runtime-manifest.json").read_text(encoding="utf-8"))
        self.assertIn("python", runtime)
        self.assertIn("pyyaml", runtime)
        self.assertIsNone(runtime["head"])  # fixture repo is not a git checkout
        # The frozen runtime is source code (execution/output_settings.py included);
        # only the settings.json *data* file (secrets) must never be swept in.
        self.assertFalse(any(f["path"].rsplit("/", 1)[-1] == "settings.json" for f in runtime["files"]))

    def test_run_tampering_detected(self):
        self.runtime()
        self.save()
        self.store.prepare_run("cfg-test", run_id="run-test", job_id="job-test")
        path = self.store.runs / "run-test/runtime/engine/injected.py"
        path.write_text("# extra", encoding="utf-8")
        with self.assertRaises(ConfigError):
            self.store.verify_run("run-test")

    def test_historic_missing_values_are_not_backfilled(self):
        old = ConfigStore.legacy_settings({"generations": 2})
        self.assertEqual(old["evolution"]["generations"], 2)
        self.assertIsNone(old["evolution"]["record_explanations"])
        self.assertIsNone(old["input_manifest_sha256"])

    def test_frozen_runtime_matches_direct_cli_even_after_source_edit(self):
        self.runtime()
        self.save()
        manifest = self.store.prepare_run("cfg-test", run_id="run-smoke", job_id="job-smoke")
        direct = self.base / "direct"
        args = [sys.executable, "-B", str(self.repo / "scripts/evolve.py"),
                "--project", str(self.repo / "projects/romance"),
                "--template", str(self.repo / "templates/romance"), "--out", str(direct),
                "--generations", "1", "--population", "2", "--seeds", "1", "--keep", "all"]
        subprocess.run(args, check=True, capture_output=True, timeout=60)
        (self.repo / "gapengine/evolve.py").write_text("raise RuntimeError('source changed')", encoding="utf-8")
        (self.repo / "projects/romance/world.yaml").write_text("invalid: changed", encoding="utf-8")
        result = subprocess.run(manifest["argv"], capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        fixed = self.store.runs / "run-smoke"
        direct_logs = {p.relative_to(direct).as_posix(): p.read_bytes() for p in direct.rglob("layers.jsonl")}
        fixed_logs = {p.relative_to(fixed).as_posix(): p.read_bytes() for p in fixed.rglob("layers.jsonl")}
        self.assertTrue(direct_logs)
        self.assertEqual(fixed_logs, direct_logs)
        self.assertEqual((direct / "archive.json").read_bytes(), (fixed / "archive.json").read_bytes())

    def test_frozen_parallel_coevolution_uses_fixed_worker_modules(self):
        self.runtime()
        spec = {**self.spec, "evolution": {**self.spec["evolution"], "processes": 2, "coevolve": True}}
        self.store.save(spec, config_id="cfg-parallel")
        manifest = self.store.prepare_run("cfg-parallel", run_id="run-parallel", job_id="job-parallel")
        (self.repo / "engine/sim.py").write_text("raise RuntimeError('must not import live source')", encoding="utf-8")
        result = subprocess.run(manifest["argv"], capture_output=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        fixed = self.store.runs / "run-parallel"
        self.assertTrue((fixed / "archive_antagonist.json").is_file())
        self.assertGreaterEqual(len(list(fixed.rglob("layers.jsonl"))), 4)
        self.store.verify_run("run-parallel")

    def test_output_settings_resolve_live_from_settings_json(self):
        # WB-UI-021: there is no frozen "recheck" any more -- current_generation()
        # always reads settings.json as it is *now* (execution/output_settings.py
        # owns this; ConfigStore has nothing left to check_generation()).
        from execution.output_settings import current_generation
        settings = self.base / "settings.json"
        settings.write_text(json.dumps({"output": {"default_backend": "openai",
            "openai": {"model": "model-one", "api_key": "hidden"}}}), encoding="utf-8")
        result = current_generation(settings)
        self.assertEqual(result["model"], "model-one")
        self.assertTrue(result["availability"]["available"])
        settings.write_text(json.dumps({"output": {"default_backend": "openai",
            "openai": {"model": "model-two"}}}), encoding="utf-8")
        result = current_generation(settings)
        self.assertEqual(result["model"], "model-two")
        self.assertFalse(result["availability"]["available"])
        self.assertEqual(result["availability"]["reason"], "credentials_missing")

    def test_actual_subject_content_is_available_from_fixed_preview(self):
        saved = self.save()
        self.assertIn("identity", saved["preview"]["subjects"][0])
        self.assertIn("ending", saved["preview"]["world"])
        self.assertEqual(saved["preview"]["world"]["name"], "放課後の約束")

    def test_permission_error_not_hidden_as_missing_input(self):
        real = Path.read_bytes
        def denied(path):
            if path == self.repo / "projects/romance/world.yaml":
                raise PermissionError("denied")
            return real(path)
        with patch.object(Path, "read_bytes", denied), self.assertRaises(PermissionError):
            self.store.preview(self.spec)

    def test_linked_input_is_rejected_before_read(self):
        real = Path.is_symlink
        target = self.repo / "projects/romance/world.yaml"
        with patch.object(Path, "is_symlink", lambda path: path == target or real(path)):
            with self.assertRaises(ConfigError):
                self.store.preview(self.spec)

    def test_run_config_tampering_is_detected(self):
        self.runtime()
        self.save()
        self.store.prepare_run("cfg-test", run_id="run-test", job_id="job-test")
        path = self.store.runs / "run-test/config.json"
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaises(ConfigError):
            self.store.verify_run("run-test")

    # -- WB-WORLDGROW-001 stage 3a: expansion patches -----------------------

    def _momotaro_spec(self, **evolution):
        return {"label": "拡張確認", "project_id": "momotaro", "template_id": "momotaro",
                "evolution": {"generations": 1, "population": 2, "seeds": 1, "keep": "all", **evolution}}

    def _write_patch(self, patch, *, proposed=False):
        project = self.repo / "projects/momotaro"
        if not proposed:
            write_approved(project, patch, self.repo / "templates/momotaro", self.repo)
        else:
            folder = project / "patches/_proposed"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{patch['id']}.yaml").write_text(yaml.safe_dump(patch, allow_unicode=True), encoding="utf-8")

    @staticmethod
    def _sample_patch(**overrides):
        patch = {
            "id": "placeholder", "title": "海辺の船大工小屋",
            "trigger": {"zone": "海", "verb": "investigate"},
            "add": {"zones": [{"name": "船大工の小屋", "parent": "海", "note": "船具を扱う小屋"}],
                    "items": [], "facts": []},
        }
        patch.update(overrides)
        patch["id"] = patch_id_for(patch["add"])
        return patch

    def test_expand_with_approved_patch_adds_zone_and_records_manifest(self):
        self._write_patch(self._sample_patch())
        spec = self._momotaro_spec(world_expansion="expand")
        saved = self.store.save(spec, config_id="cfg-expand")
        world = yaml.safe_load(
            (self.store.control / "configs/cfg-expand/inputs/projects/momotaro/world.yaml").read_bytes())
        self.assertIn("船大工の小屋", {z["name"] for z in world["zones"]})
        self.assertEqual(world["expansion"]["patches"][0]["id"], self._sample_patch()["id"])
        manifest = json.loads((self.store.control / "configs/cfg-expand/input-manifest.json").read_text(encoding="utf-8"))
        record = next(r for r in manifest["files"] if r["path"] == "projects/momotaro/world.yaml")
        self.assertEqual(record["world_patches"], [{"id": self._sample_patch()["id"], "sha256": sha256(
            (self.repo / "projects/momotaro/patches" / (self._sample_patch()["id"] + ".yaml")).read_bytes())}])
        self.assertIn("船大工の小屋", {z["name"] for z in saved["preview"]["world"]["zones"]})

    def test_off_and_detect_freeze_byte_identical_world_even_with_patches_present(self):
        # Baseline: no patches/ directory at all.
        baseline = self.store.save(self._momotaro_spec(world_expansion="off"), config_id="cfg-baseline")
        baseline_world = (self.store.control / "configs/cfg-baseline/inputs/projects/momotaro/world.yaml").read_bytes()
        self._write_patch(self._sample_patch())
        for world_expansion in ("off", "detect"):
            with self.subTest(world_expansion=world_expansion):
                spec = self._momotaro_spec(world_expansion=world_expansion)
                saved = self.store.save(spec, config_id=f"cfg-{world_expansion}")
                frozen = (self.store.control / f"configs/cfg-{world_expansion}/inputs/projects/momotaro/world.yaml").read_bytes()
                self.assertEqual(frozen, baseline_world)
                self.assertEqual(saved["input_manifest_sha256"], baseline["input_manifest_sha256"])
                manifest = json.loads((self.store.control / f"configs/cfg-{world_expansion}/input-manifest.json")
                    .read_text(encoding="utf-8"))
                record = next(r for r in manifest["files"] if r["path"] == "projects/momotaro/world.yaml")
                self.assertNotIn("world_patches", record)

    def test_invalid_approved_patch_rejected_as_config_error(self):
        self._write_patch(self._sample_patch(add={"zones": [], "items": [], "facts": []}))
        spec = self._momotaro_spec(world_expansion="expand")
        with self.assertRaises(ConfigError) as caught:
            self.store.preview(spec)
        self.assertIn("inputs.world_patches", caught.exception.field_errors)

    def test_expand_rejects_patch_colliding_with_template_identifier(self):
        # "hostile_lean" is a rule id in templates/momotaro/rules.yaml -- not
        # in RESERVED_NAMES itself, only reachable via template_identifiers().
        self._write_patch(self._sample_patch(add={
            "zones": [{"name": "hostile_lean", "parent": "海"}], "items": [], "facts": [],
        }))
        spec = self._momotaro_spec(world_expansion="expand")
        with self.assertRaises(ConfigError) as caught:
            self.store.preview(spec)
        self.assertIn("inputs.world_patches", caught.exception.field_errors)

    def test_invalid_approved_patch_does_not_block_off(self):
        self._write_patch(self._sample_patch(add={"zones": [], "items": [], "facts": []}))
        spec = self._momotaro_spec(world_expansion="off")
        self.store.preview(spec)  # must not raise

    def test_proposed_patch_is_never_applied_even_under_expand(self):
        self._write_patch(self._sample_patch(), proposed=True)
        spec = self._momotaro_spec(world_expansion="expand")
        self.store.save(spec, config_id="cfg-proposed-only")
        world = yaml.safe_load(
            (self.store.control / "configs/cfg-proposed-only/inputs/projects/momotaro/world.yaml").read_bytes())
        self.assertNotIn("expansion", world)

    def test_duplicate_toggling_expand_saves_as_new_config_not_dead_end(self):
        self._write_patch(self._sample_patch())
        base = self.store.save(self._momotaro_spec(world_expansion="off"), config_id="cfg-base")
        clone = self.store.duplicate("cfg-base", {"evolution": {"world_expansion": "expand"}}, new_id="cfg-clone")
        self.assertIsNone(clone["parent_config_id"])
        self.assertEqual(clone["evolution"]["world_expansion"], "expand")

    def test_normalize_accepts_expand(self):
        result = normalize({**self.spec, "evolution": {"world_expansion": "expand"}})
        self.assertEqual(result["evolution"]["world_expansion"], "expand")

    # -- R5: duplicating a config saved before world_expansion existed -----

    def _strip_world_expansion_key(self, config_id):
        """Rewrite an already-saved config's on-disk files to the pre-WB-WORLDGROW-001
        shape (no evolution.world_expansion key at all) and refresh complete.json's
        seal to match, so _bundle()'s integrity check still passes. Production code
        never does this -- it only reconstructs a legacy on-disk shape for the test."""
        root = self.store.control / "configs" / config_id
        document = json.loads((root / "config.json").read_text(encoding="utf-8"))
        del document["evolution"]["world_expansion"]
        atomic_json(root / "config.json", document)
        seal = json.loads((root / "complete.json").read_text(encoding="utf-8"))
        seal["config_sha256"] = sha256(canonical(document))
        atomic_json(root / "complete.json", seal)

    def test_duplicate_legacy_config_missing_world_expansion_key_succeeds(self):
        self.store.save(self._momotaro_spec(world_expansion="off"), config_id="cfg-legacy")
        self._strip_world_expansion_key("cfg-legacy")
        clone = self.store.duplicate("cfg-legacy", {"label": "複製"}, new_id="cfg-legacy-clone")
        self.assertEqual(clone["parent_config_id"], "cfg-legacy")
        self.assertEqual(clone["evolution"]["world_expansion"], "off")

    def test_duplicate_legacy_config_to_detect_succeeds(self):
        self.store.save(self._momotaro_spec(world_expansion="off"), config_id="cfg-legacy2")
        self._strip_world_expansion_key("cfg-legacy2")
        clone = self.store.duplicate(
            "cfg-legacy2", {"evolution": {"world_expansion": "detect"}}, new_id="cfg-legacy2-detect")
        self.assertEqual(clone["parent_config_id"], "cfg-legacy2")
        self.assertEqual(clone["evolution"]["world_expansion"], "detect")

    def test_duplicate_legacy_config_to_expand_saves_as_new_config(self):
        self.store.save(self._momotaro_spec(world_expansion="off"), config_id="cfg-legacy3")
        self._strip_world_expansion_key("cfg-legacy3")
        clone = self.store.duplicate(
            "cfg-legacy3", {"evolution": {"world_expansion": "expand"}}, new_id="cfg-legacy3-expand")
        self.assertIsNone(clone["parent_config_id"])
        self.assertEqual(clone["evolution"]["world_expansion"], "expand")


if __name__ == "__main__":
    unittest.main()
