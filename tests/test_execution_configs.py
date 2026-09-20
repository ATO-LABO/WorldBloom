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

from execution.configs import ConfigStore, evolution_defaults, normalize
from execution.provenance import ConfigError, atomic_json, canonical, directory_lock, read_json, sha256
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
                        {"target_ending": ["mutual", "mutual"]}, {"mutation": 0.9}):
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


if __name__ == "__main__":
    unittest.main()
