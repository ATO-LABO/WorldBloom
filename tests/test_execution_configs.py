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
from execution.provenance import ConfigError, atomic_json, canonical, directory_lock, read_json, sha256, write_bytes
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

    # ------------------------------------------------------- WB-JEV-002

    def _momotaro_spec(self, **evolution):
        return {"label": "桃太郎 合理性", "project_id": "momotaro", "template_id": "momotaro",
                "evolution": {"generations": 1, "population": 2, "seeds": 1, "keep": "all",
                              **evolution}}

    def test_reject_invalid_kappa_and_rationality_fields(self):
        for bad in (True, False, -0.01, 1.01, "0.5", [0.5]):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                self.store.preview({**self.spec, "evolution": {"kappa": bad}})
        for field, bad in (
            ("rationality_backend", "openai"),
            ("rationality_method", "bad"),
            ("rationality_max_calls", 0),
            ("rationality_max_calls", 1.5),
            ("rationality_max_calls", True),
            ("rationality_table", "rationality.json"),
            ("rationality_table", "../escape.json"),
            ("rationality_model", ""),
            ("rationality_model", "../escape"),
            ("rationality_model", 3),
            ("rationality_model", "x" * 201),
            ("rationality_num_ctx", 255),
            ("rationality_num_ctx", 131073),
            ("rationality_num_ctx", 2048.0),
            ("rationality_num_ctx", True),
        ):
            with self.subTest(field=field, bad=bad), self.assertRaises(ConfigError):
                self.store.preview({**self.spec, "evolution": {field: bad}})
        # In-range values, and a legitimate backend/method/max_calls override,
        # are all accepted.
        for kappa in (0, 0.0, 1, 1.0, 0.5, None):
            with self.subTest(kappa=kappa):
                self.store.preview({**self.spec, "evolution": {"kappa": kappa}})
        self.store.preview({**self.spec, "evolution": {
            "rationality_backend": "none", "rationality_method": "choice",
            "rationality_max_calls": 3}})
        self.store.preview({**self.spec, "evolution": {
            "rationality_model": "qwen3.5:9b", "rationality_num_ctx": 2048}})

    def test_kappa_forced_to_none_when_template_lacks_rationality_yaml(self):
        """Opus review: a genre switch client-side (world/genre <select>
        snapping) followed by submit without a reload must never carry
        another genre's kappa onto a template that has no rationality.yaml
        at all -- romance has none, so even an explicit kappa must not
        survive save()."""
        saved = self.store.save(
            {**self.spec, "evolution": {**self.spec["evolution"], "kappa": 0.5}},
            config_id="cfg-romance-kappa")
        self.assertIsNone(saved["evolution"]["kappa"])
        previewed = self.store.preview(
            {**self.spec, "evolution": {**self.spec["evolution"], "kappa": 0.5}})
        self.assertIsNone(previewed["evolution"]["kappa"])
        duplicated = self.store.duplicate(
            "cfg-romance-kappa", {"evolution": {"kappa": 0.7}}, new_id="cfg-romance-kappa-dup")
        self.assertIsNone(duplicated["evolution"]["kappa"])

    def test_kappa_zero_normalizes_to_none_like_legacy(self):
        zero = self.store.save(self._momotaro_spec(kappa=0), config_id="cfg-kappa-zero")
        none = self.store.save(self._momotaro_spec(kappa=None), config_id="cfg-kappa-none")
        absent = self.store.save(self._momotaro_spec(), config_id="cfg-kappa-absent")
        self.assertIsNone(zero["evolution"]["kappa"])
        self.assertIsNone(none["evolution"]["kappa"])
        self.assertIsNone(absent["evolution"]["kappa"])
        # A pre-WB-JEV-002 spec (no rationality.* keys at all in "evolution")
        # still normalizes fine -- every new key is defaulted to None.
        legacy = self.store.save(self.spec, config_id="cfg-legacy-evolution")
        for key in ("kappa", "rationality_backend", "rationality_method",
                    "rationality_table", "rationality_max_calls",
                    "rationality_model", "rationality_num_ctx"):
            self.assertIsNone(legacy["evolution"][key])

    # ------------------------------------------------------- WB-ROUTE-001 S4

    def _plus2_spec(self, **evolution):
        return {"label": "桃太郎 道筋", "project_id": "momotaro_plus2", "template_id": "momotaro_plus2",
                "evolution": {"generations": 1, "population": 2, "seeds": 1, "keep": "all",
                              **evolution}}

    def test_reject_invalid_route_rho(self):
        for bad in (True, False, -0.01, 1.01, "0.5", [0.5]):
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                self.store.preview({**self.spec, "evolution": {"route_rho": bad}})
        for rho in (0, 0.0, 1, 1.0, 0.5, None):
            with self.subTest(rho=rho):
                self.store.preview({**self.spec, "evolution": {"route_rho": rho}})

    def test_route_rho_forced_to_none_when_template_lacks_route_yaml(self):
        """Mirrors test_kappa_forced_to_none_when_template_lacks_rationality_
        yaml: a genre switch client-side must never carry another genre's ρ
        onto a template with no route.yaml -- romance (self.spec) has none."""
        saved = self.store.save(
            {**self.spec, "evolution": {**self.spec["evolution"], "route_rho": 0.5}},
            config_id="cfg-romance-rho")
        self.assertIsNone(saved["evolution"]["route_rho"])
        previewed = self.store.preview(
            {**self.spec, "evolution": {**self.spec["evolution"], "route_rho": 0.5}})
        self.assertIsNone(previewed["evolution"]["route_rho"])
        duplicated = self.store.duplicate(
            "cfg-romance-rho", {"evolution": {"route_rho": 0.7}}, new_id="cfg-romance-rho-dup")
        self.assertIsNone(duplicated["evolution"]["route_rho"])

    def test_route_rho_zero_normalizes_to_none_like_legacy(self):
        zero = self.store.save(self._plus2_spec(route_rho=0), config_id="cfg-rho-zero")
        none = self.store.save(self._plus2_spec(route_rho=None), config_id="cfg-rho-none")
        absent = self.store.save(self._plus2_spec(), config_id="cfg-rho-absent")
        self.assertIsNone(zero["evolution"]["route_rho"])
        self.assertIsNone(none["evolution"]["route_rho"])
        self.assertIsNone(absent["evolution"]["route_rho"])

    def test_route_rho_kept_when_template_has_route_yaml(self):
        saved = self.store.save(self._plus2_spec(route_rho=1.0), config_id="cfg-rho-kept")
        self.assertEqual(saved["evolution"]["route_rho"], 1.0)

    def test_prepare_run_route_rho_positive_adds_flag_and_zero_or_none_omits_it(self):
        self.runtime()
        self.store.save(self._plus2_spec(route_rho=1.0), config_id="cfg-rho-on")
        manifest = self.store.prepare_run("cfg-rho-on", run_id="run-rho-on", job_id="job-rho-on")
        self.assertIn("--route-rho", manifest["argv"])
        self.assertIn("1.0", manifest["argv"])
        for rho, cid in ((None, "cfg-rho-off-none"), (0, "cfg-rho-off-zero")):
            with self.subTest(rho=rho):
                self.store.save(self._plus2_spec(route_rho=rho), config_id=cid)
                manifest = self.store.prepare_run(
                    cid, run_id=f"run-{cid}", job_id=f"job-{cid}")
                self.assertNotIn("--route-rho", manifest["argv"])

    def test_prepare_run_reads_a_genuinely_pre_wb_jev_002_config_json(self):
        """Opus review: prepare_run() reads config["evolution"] straight off
        disk (never through normalize()), so a config.json saved before
        --kappa existed -- evolution has no kappa/rationality_* keys at all,
        not even as None -- must not KeyError. All 9 configs under this
        machine's C:\\Projects\\WorldBloom-local\\control\\configs predate
        WB-JEV-002 this way."""
        self.runtime()
        self.store.save(self._momotaro_spec(), config_id="cfg-genuinely-legacy")
        config_dir = self.store.control / "configs" / "cfg-genuinely-legacy"

        # Baseline: prepare_run() while config.json is still a normal,
        # normalize()-produced document (kappa=None present as a real key).
        baseline = self.store.prepare_run("cfg-genuinely-legacy", run_id="run-baseline", job_id="job-baseline")

        # Now rewrite config.json (and complete.json's seal, so _bundle()'s
        # integrity check still accepts it) to drop every WB-JEV-002 key
        # entirely -- a genuinely pre-existing config.json, not something
        # normalize() would ever produce.
        config = read_json(config_dir / "config.json")
        for key in ("kappa", "rationality_backend", "rationality_method",
                    "rationality_table", "rationality_max_calls",
                    "rationality_model", "rationality_num_ctx"):
            self.assertIn(key, config["evolution"])
            del config["evolution"][key]
        config_bytes = canonical(config)
        (config_dir / "config.json").write_bytes(config_bytes)
        seal = read_json(config_dir / "complete.json")
        seal["config_sha256"] = sha256(config_bytes)
        atomic_json(config_dir / "complete.json", seal)

        manifest = self.store.prepare_run("cfg-genuinely-legacy", run_id="run-legacy", job_id="job-legacy")
        self.assertNotIn("--kappa", manifest["argv"])
        self.assertNotIn("--rationality-table", manifest["argv"])
        # Byte-identical CLI argv apart from the run-id baked into the paths.
        self.assertEqual(
            [a.replace("run-baseline", "<rid>") for a in baseline["argv"]],
            [a.replace("run-legacy", "<rid>") for a in manifest["argv"]],
        )

    def test_prepare_run_kappa_none_or_zero_argv_matches_legacy_cli(self):
        self.runtime()
        direct = self.base / "direct-momotaro"
        args = [sys.executable, "-B", str(self.repo / "scripts/evolve.py"),
                "--project", str(self.repo / "projects/momotaro"),
                "--template", str(self.repo / "templates/momotaro"), "--out", str(direct),
                "--generations", "1", "--population", "2", "--seeds", "1", "--keep", "all"]
        subprocess.run(args, check=True, capture_output=True, timeout=60)
        direct_logs = {p.relative_to(direct).as_posix(): p.read_bytes() for p in direct.rglob("layers.jsonl")}
        self.assertTrue(direct_logs)
        for kappa in (None, 0):
            with self.subTest(kappa=kappa):
                cid, rid, jid = f"cfg-momo-{kappa}", f"run-momo-{kappa}", f"job-momo-{kappa}"
                self.store.save(self._momotaro_spec(kappa=kappa), config_id=cid)
                manifest = self.store.prepare_run(cid, run_id=rid, job_id=jid)
                self.assertNotIn("--kappa", manifest["argv"])
                self.assertNotIn("--rationality-table", manifest["argv"])
                result = subprocess.run(manifest["argv"], capture_output=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
                fixed = self.store.runs / rid
                fixed_logs = {p.relative_to(fixed).as_posix(): p.read_bytes() for p in fixed.rglob("layers.jsonl")}
                self.assertEqual(fixed_logs, direct_logs)

    def test_prepare_run_kappa_positive_adds_rationality_table_under_control(self):
        self.runtime()
        self.store.save(self._momotaro_spec(kappa=0.6), config_id="cfg-momo-on")
        manifest = self.store.prepare_run("cfg-momo-on", run_id="run-on", job_id="job-on")
        self.assertIn("--kappa", manifest["argv"])
        self.assertIn("0.6", manifest["argv"])
        self.assertIn("--rationality-table", manifest["argv"])
        table_path = Path(manifest["argv"][manifest["argv"].index("--rationality-table") + 1])
        self.assertTrue(table_path.is_relative_to(self.store.control / "rationality"))
        # momotaro's rationality.yaml: method=choice, backend.model=qwen3.6:35b
        # (":" is not filename-safe, so it is folded into "_").
        self.assertEqual(table_path.name, "momotaro.qwen3.6_35b.choice.json")
        self.assertTrue(table_path.parent.is_dir())

    def test_prepare_run_kappa_positive_honors_rationality_method_override(self):
        self.runtime()
        self.store.save(self._momotaro_spec(kappa=0.6, rationality_method="noul"), config_id="cfg-momo-noul")
        manifest = self.store.prepare_run("cfg-momo-noul", run_id="run-noul", job_id="job-noul")
        table_path = Path(manifest["argv"][manifest["argv"].index("--rationality-table") + 1])
        self.assertEqual(table_path.name, "momotaro.qwen3.6_35b.noul.json")

    def test_prepare_run_kappa_positive_honors_rationality_model_override(self):
        """WB-JEV-003: --rationality-model changes which judge answers, so an
        override must land its own table file -- never share qwen3.6:35b's
        accumulated judgments with a different model's."""
        self.runtime()
        self.store.save(
            self._momotaro_spec(kappa=0.6, rationality_model="qwen3.5:9b"),
            config_id="cfg-momo-model-override",
        )
        manifest = self.store.prepare_run(
            "cfg-momo-model-override", run_id="run-model-override", job_id="job-model-override"
        )
        self.assertIn("--rationality-model", manifest["argv"])
        self.assertIn("qwen3.5:9b", manifest["argv"])
        table_path = Path(manifest["argv"][manifest["argv"].index("--rationality-table") + 1])
        self.assertEqual(table_path.name, "momotaro.qwen3.5_9b.choice.json")

    def test_prepare_run_rationality_num_ctx_argv_and_no_table_path_effect(self):
        """--rationality-num-ctx is an operational knob (WB-GA-RESUME plan
        §2.1 treats it like table path/thermal_guard): it reaches argv but
        never changes which table file a run reads/writes."""
        self.runtime()
        self.store.save(
            self._momotaro_spec(kappa=0.6, rationality_num_ctx=2048),
            config_id="cfg-momo-num-ctx",
        )
        manifest = self.store.prepare_run(
            "cfg-momo-num-ctx", run_id="run-num-ctx", job_id="job-num-ctx"
        )
        self.assertIn("--rationality-num-ctx", manifest["argv"])
        self.assertIn("2048", manifest["argv"])
        table_path = Path(manifest["argv"][manifest["argv"].index("--rationality-table") + 1])
        self.assertEqual(table_path.name, "momotaro.qwen3.6_35b.choice.json")

    def test_prepare_run_rationality_table_path_survives_hostile_model_name(self):
        """A rationality.yaml model name containing ".." or a path separator
        must never let --rationality-table escape the control root -- this is
        a security boundary (WB-JEV-002 plan §2), not just a display detail."""
        self.runtime()
        path = self.repo / "templates/momotaro/rationality.yaml"
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        doc["backend"]["model"] = "../../../evil/model"
        path.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
        self.store.save(self._momotaro_spec(kappa=0.6), config_id="cfg-momo-hostile")
        manifest = self.store.prepare_run("cfg-momo-hostile", run_id="run-hostile", job_id="job-hostile")
        table_path = Path(manifest["argv"][manifest["argv"].index("--rationality-table") + 1]).resolve()
        control_rationality = (self.store.control / "rationality").resolve()
        self.assertEqual(control_rationality, table_path.parent)
        self.assertTrue(table_path.is_relative_to(control_rationality))
        self.assertNotIn("..", table_path.parts)

    # -- WB-WORLDGROW-001 stage 3a: expansion patches -----------------------
    # (_momotaro_spec is shared with the WB-JEV-002 tests above; the label
    # text is not asserted by either test group.)

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

    # -- WB-WORLDGROW-001 段階5c: growth ------------------------------------

    def test_growth_absent_and_explicit_off_are_byte_identical_and_carry_no_key(self):
        absent = normalize(self.spec)
        explicit_off = normalize({**self.spec, "growth": {"mode": "off"}})
        self.assertNotIn("growth", absent)
        self.assertEqual(canonical(absent), canonical(explicit_off))

    def test_growth_off_config_save_is_byte_identical_to_no_growth_key(self):
        without = self.store.save(self.spec, config_id="cfg-nogrowth")
        withoff = self.store.save({**self.spec, "growth": {"mode": "off"}}, config_id="cfg-offgrowth")
        without_comparable = {k: v for k, v in without.items() if k not in ("config_id", "created_at")}
        withoff_comparable = {k: v for k, v in withoff.items() if k not in ("config_id", "created_at")}
        self.assertEqual(canonical(without_comparable), canonical(withoff_comparable))
        self.assertNotIn("growth", without)
        self.assertNotIn("growth", withoff)

    def test_growth_auto_defaults_epochs_and_auto_retire_and_forces_expand(self):
        result = normalize({**self.spec, "evolution": {"world_expansion": "off"}, "growth": {"mode": "auto"}})
        self.assertEqual(result["growth"], {"mode": "auto", "epochs": 3, "auto_retire": True})
        self.assertEqual(result["evolution"]["world_expansion"], "expand")

    def test_growth_manual_defaults_auto_retire_false(self):
        result = normalize({**self.spec, "growth": {"mode": "manual", "epochs": 5}})
        self.assertEqual(result["growth"], {"mode": "manual", "epochs": 5, "auto_retire": False})

    def test_growth_epochs_out_of_range_is_rejected(self):
        for epochs in (0, 11):
            with self.assertRaises(ConfigError):
                normalize({**self.spec, "growth": {"mode": "auto", "epochs": epochs}})

    def test_growth_unknown_mode_is_rejected(self):
        with self.assertRaises(ConfigError):
            normalize({**self.spec, "growth": {"mode": "sometimes"}})

    def test_growth_unknown_field_is_rejected(self):
        with self.assertRaises(ConfigError):
            normalize({**self.spec, "growth": {"mode": "auto", "unknown": 1}})

    def test_duplicate_carries_growth_forward(self):
        # WB-WORLDGROW-001 段階5c review R5: duplicate() used to drop growth
        # entirely -- a growth-enabled config's clone silently went back to off.
        self.store.save({**self.spec, "growth": {"mode": "auto", "epochs": 4, "auto_retire": True}},
                         config_id="cfg-growth-src")
        clone = self.store.duplicate("cfg-growth-src", {"label": "複製"}, new_id="cfg-growth-clone")
        self.assertEqual(clone["growth"], {"mode": "auto", "epochs": 4, "auto_retire": True})
        self.assertEqual(clone["parent_config_id"], "cfg-growth-src")

    def test_duplicate_turning_growth_on_saves_as_new_config_not_dead_end(self):
        # R5: growth.mode != off forces world_expansion to "expand" at
        # normalize() time even if the duplicate's own evolution.world_expansion
        # still says "off" -- judged by that effective value, this must save
        # as a fresh (non-parented) config instead of _prepare() dead-ending
        # on "複製元と異なる入力は新規設定として保存してください".
        self.store.save(self._momotaro_spec(world_expansion="off"), config_id="cfg-off-src")
        clone = self.store.duplicate("cfg-off-src", {"growth": {"mode": "auto", "epochs": 2}},
                                      new_id="cfg-off-to-growth")
        self.assertIsNone(clone["parent_config_id"])
        self.assertEqual(clone["evolution"]["world_expansion"], "expand")
        self.assertEqual(clone["growth"]["mode"], "auto")

    def test_duplicate_clearing_growth_is_respected(self):
        self.store.save({**self.spec, "growth": {"mode": "manual", "epochs": 2, "auto_retire": False}},
                         config_id="cfg-growth-src2")
        clone = self.store.duplicate("cfg-growth-src2", {"growth": None}, new_id="cfg-growth-cleared")
        self.assertNotIn("growth", clone)

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

    # ------------------------------------------------- WB-WORLDGROW-001 段階5b

    _GENOME_LOW = {"category_weight": {c: 0.5 for c in ("I", "II", "III", "IV", "V", "VI")},
                   "risk_tolerance": 0.2, "stance_shift_bias": 0.1, "novelty_drive": 0.3}
    _GENOME_HIGH = {"category_weight": {c: 0.8 for c in ("I", "II", "III", "IV", "V", "VI")},
                    "risk_tolerance": 0.9, "stance_shift_bias": -0.4, "novelty_drive": 0.6}

    def _prepare_source_run(self, run_id, *, config_id="cfg-source", project_id="romance", template_id="romance"):
        """A genuine prepared run (real save()+prepare_run() pipeline, so
        verify_run() -- which _prepare() now calls before capturing a
        seed_genomes reference -- actually passes) with no archive written
        yet; the caller publishes one with _publish_archive()."""
        self.runtime()
        spec = {**self.spec, "project_id": project_id, "template_id": template_id}
        self.store.save(spec, config_id=config_id)
        self.store.prepare_run(config_id, run_id=run_id, job_id="job-" + run_id)
        return self.store.runs / run_id

    def _publish_archive(self, run_dir, cells, revision=1):
        """WB-WORLDGROW-001 段階5a で見逃した前例: catalog-managed experiments
        only ever have published/<rev>/archive.json, never a top-level
        archive.json -- this must be the only shape these tests exercise."""
        payload = {"cells": cells}
        raw = canonical(payload)
        folder = run_dir / "published" / str(revision)
        write_bytes(folder / "archive.json", raw)
        manifest = {"schema_version": 1,
                    "files": {"archive": {"path": f"published/{revision}/archive.json", "sha256": sha256(raw)}}}
        write_bytes(folder / "manifest.json", canonical(manifest))
        atomic_json(run_dir / "published" / "current.json", {"schema_version": 1, "revision": revision})
        return raw

    def test_seed_genomes_freezes_from_published_archive(self):
        source_run = self._prepare_source_run("run-seed-source")
        self._publish_archive(source_run, {
            "I|low": {"quality": 0.4, "generation": 2, "genome": self._GENOME_LOW},
            "II|mid": {"quality": 0.9, "generation": 5, "genome": self._GENOME_HIGH},
        })
        spec = {**self.spec, "evolution": {**self.spec["evolution"], "seed_genomes": "run-seed-source"}}
        saved = self.store.save(spec, config_id="cfg-seeded")
        self.assertEqual(saved["evolution"]["seed_genomes"], "run-seed-source")
        doc = json.loads(
            (self.store.control / "configs/cfg-seeded/inputs/seed_genomes.json").read_bytes())
        self.assertEqual(doc["schema_version"], 1)
        self.assertEqual([g["cell"] for g in doc["genomes"]], ["II|mid", "I|low"])  # quality desc
        self.assertEqual(doc["source"]["run_id"], "run-seed-source")
        self.assertEqual(doc["source"]["project_id"], "romance")
        self.assertEqual(doc["source"]["template_id"], "romance")
        self.assertEqual(doc["source"]["revision"], 1)
        manifest = json.loads(
            (self.store.control / "configs/cfg-seeded/input-manifest.json").read_text(encoding="utf-8"))
        record = next(r for r in manifest["files"] if r["path"] == "seed_genomes.json")
        self.assertEqual(record["transformation"], "seed_genomes_from_archive")
        self.assertIn("source_sha256", record)

        run_manifest = self.store.prepare_run("cfg-seeded", run_id="run-seeded", job_id="job-seeded")
        self.assertIn("--seed-genomes", run_manifest["argv"])
        flag_value = run_manifest["argv"][run_manifest["argv"].index("--seed-genomes") + 1]
        self.assertEqual(flag_value, str(self.store.runs / "run-seeded/inputs/seed_genomes.json"))
        self.assertNotIn("run-seed-source", run_manifest["argv"])  # the source run_id, never passed directly
        self.assertEqual(run_manifest["evolution"]["seed_genomes"], "run-seed-source")

    def test_seed_genomes_off_matches_legacy_argv_and_manifest_byte_for_byte(self):
        self.runtime()
        legacy = self.store.save(self.spec, config_id="cfg-legacy-noseed")
        explicit = self.store.save(
            {**self.spec, "evolution": {**self.spec["evolution"], "seed_genomes": None}},
            config_id="cfg-explicit-noseed")
        self.assertEqual(legacy["input_manifest_sha256"], explicit["input_manifest_sha256"])
        legacy_manifest = self.store.prepare_run("cfg-legacy-noseed", run_id="run-legacy-noseed", job_id="job-a")
        explicit_manifest = self.store.prepare_run("cfg-explicit-noseed", run_id="run-explicit-noseed", job_id="job-b")
        strip = lambda argv: [a.replace("run-legacy-noseed", "X").replace("run-explicit-noseed", "X") for a in argv]
        self.assertEqual(strip(legacy_manifest["argv"]), strip(explicit_manifest["argv"]))
        self.assertNotIn("--seed-genomes", legacy_manifest["argv"])
        self.assertNotIn("--seed-genomes", explicit_manifest["argv"])

    def test_seed_genomes_empty_string_normalizes_to_none(self):
        result = normalize({**self.spec, "evolution": {"seed_genomes": ""}})
        self.assertIsNone(result["evolution"]["seed_genomes"])

    def test_seed_genomes_rejects_mismatched_project_or_template(self):
        source_run = self._prepare_source_run("run-seed-wrong-world", project_id="romance", template_id="romance")
        self._publish_archive(source_run, {"I|low": {"quality": 0.4, "generation": 0, "genome": self._GENOME_LOW}})
        spec = {**self.spec, "project_id": "detective", "template_id": "detective",
                "evolution": {**self.spec["evolution"], "seed_genomes": "run-seed-wrong-world"}}
        with self.assertRaises(ConfigError) as caught:
            self.store.save(spec, config_id="cfg-mismatch")
        self.assertIn("evolution.seed_genomes", caught.exception.field_errors)

    def test_seed_genomes_rejects_unknown_run_id(self):
        spec = {**self.spec, "evolution": {**self.spec["evolution"], "seed_genomes": "run-does-not-exist"}}
        with self.assertRaises(ConfigError) as caught:
            self.store.save(spec, config_id="cfg-missing-run")
        self.assertEqual(caught.exception.code, "not_found")

    def test_seed_genomes_rejects_tampered_published_archive(self):
        source_run = self._prepare_source_run("run-seed-tampered")
        self._publish_archive(source_run, {"I|low": {"quality": 0.4, "generation": 0, "genome": self._GENOME_LOW}})
        # Mutate the archive after publication without updating manifest.json's
        # recorded sha256 -- the same tamper-detection published/ already relies
        # on elsewhere (RunCatalog.snapshot's manifest_sha256 chain).
        archive_path = source_run / "published/1/archive.json"
        archive_path.write_bytes(archive_path.read_bytes() + b" ")
        spec = {**self.spec, "evolution": {**self.spec["evolution"], "seed_genomes": "run-seed-tampered"}}
        with self.assertRaises(ConfigError) as caught:
            self.store.save(spec, config_id="cfg-tampered")
        self.assertEqual(caught.exception.code, "snapshot_changed")

    def test_seed_genomes_rejects_empty_archive(self):
        source_run = self._prepare_source_run("run-seed-empty")
        self._publish_archive(source_run, {})
        spec = {**self.spec, "evolution": {**self.spec["evolution"], "seed_genomes": "run-seed-empty"}}
        with self.assertRaises(ConfigError):
            self.store.save(spec, config_id="cfg-empty-archive")

    # -------- Opus review R1: malformed archive/run data must ConfigError, not 500

    def test_seed_genomes_rejects_archive_genome_missing_scalar_key(self):
        source_run = self._prepare_source_run("run-seed-missing-key")
        broken_genome = {"category_weight": {c: 0.5 for c in ("I", "II", "III", "IV", "V", "VI")},
                          "stance_shift_bias": 0.1, "novelty_drive": 0.3}  # no risk_tolerance
        self._publish_archive(source_run, {"I|low": {"quality": 0.4, "generation": 0, "genome": broken_genome}})
        spec = {**self.spec, "evolution": {**self.spec["evolution"], "seed_genomes": "run-seed-missing-key"}}
        with self.assertRaises(ConfigError) as caught:
            self.store.save(spec, config_id="cfg-missing-key")
        self.assertIn("evolution.seed_genomes", caught.exception.field_errors)

    def test_seed_genomes_rejects_archive_with_null_quality(self):
        source_run = self._prepare_source_run("run-seed-null-quality")
        self._publish_archive(source_run,
            {"I|low": {"quality": None, "generation": 0, "genome": self._GENOME_LOW}})
        spec = {**self.spec, "evolution": {**self.spec["evolution"], "seed_genomes": "run-seed-null-quality"}}
        with self.assertRaises(ConfigError) as caught:
            self.store.save(spec, config_id="cfg-null-quality")
        self.assertIn("evolution.seed_genomes", caught.exception.field_errors)

    def test_seed_genomes_rejects_source_run_with_corrupt_complete_json(self):
        source_run = self._prepare_source_run("run-seed-corrupt-seal")
        self._publish_archive(source_run, {"I|low": {"quality": 0.4, "generation": 0, "genome": self._GENOME_LOW}})
        (source_run / "complete.json").write_text("{not json", encoding="utf-8")
        spec = {**self.spec, "evolution": {**self.spec["evolution"], "seed_genomes": "run-seed-corrupt-seal"}}
        with self.assertRaises(ConfigError) as caught:
            self.store.save(spec, config_id="cfg-corrupt-seal")
        self.assertIn("evolution.seed_genomes", caught.exception.field_errors)

    def test_seed_genomes_rejects_source_run_with_empty_complete_json(self):
        source_run = self._prepare_source_run("run-seed-empty-seal")
        self._publish_archive(source_run, {"I|low": {"quality": 0.4, "generation": 0, "genome": self._GENOME_LOW}})
        atomic_json(source_run / "complete.json", {})
        spec = {**self.spec, "evolution": {**self.spec["evolution"], "seed_genomes": "run-seed-empty-seal"}}
        with self.assertRaises(ConfigError) as caught:
            self.store.save(spec, config_id="cfg-empty-seal")
        self.assertIn("evolution.seed_genomes", caught.exception.field_errors)

    # -------- Opus review R2: prefer the sha-verified published archive
    # over the mutable top-level archive.json a ConfigStore-managed run
    # also carries.

    def test_seed_genomes_prefers_published_archive_over_tampered_top_level(self):
        source_run = self._prepare_source_run("run-seed-both-archives")
        self._publish_archive(source_run,
            {"I|low": {"quality": 0.4, "generation": 0, "genome": self._GENOME_LOW}})
        # A top-level archive.json with a *different* cell -- if this were
        # ever read instead of the published one, the frozen seed_genomes.json
        # would carry "II|mid" instead of "I|low".
        write_bytes(source_run / "archive.json",
            canonical({"cells": {"II|mid": {"quality": 0.9, "generation": 0, "genome": self._GENOME_HIGH}}}))
        spec = {**self.spec, "evolution": {**self.spec["evolution"], "seed_genomes": "run-seed-both-archives"}}
        saved = self.store.save(spec, config_id="cfg-prefers-published")
        doc = json.loads(
            (self.store.control / "configs/cfg-prefers-published/inputs/seed_genomes.json").read_bytes())
        self.assertEqual([g["cell"] for g in doc["genomes"]], ["I|low"])
        self.assertEqual(doc["source"]["revision"], 1)

    def test_duplicate_toggling_seed_genomes_saves_as_new_config(self):
        source_run = self._prepare_source_run("run-seed-dup")
        self._publish_archive(source_run, {"I|low": {"quality": 0.4, "generation": 0, "genome": self._GENOME_LOW}})
        base = self.store.save(self.spec, config_id="cfg-base-noseed")
        clone = self.store.duplicate("cfg-base-noseed",
            {"evolution": {"seed_genomes": "run-seed-dup"}}, new_id="cfg-clone-seeded")
        self.assertIsNone(clone["parent_config_id"])
        self.assertEqual(clone["evolution"]["seed_genomes"], "run-seed-dup")


if __name__ == "__main__":
    unittest.main()
