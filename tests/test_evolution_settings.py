"""WB-COMPUTE-001: settings.json "evolution" section (GA process count)."""
from pathlib import Path
import shutil
import tempfile
import unittest

from execution.configs import ConfigStore
from execution.evolution_settings import (
    default_processes, read_evolution_settings, write_evolution_settings,
)
from execution.provenance import ConfigError, atomic_json, canonical, sha256

ROOT = Path(__file__).resolve().parents[1]


class EvolutionSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wb-compute-001-")
        self.addCleanup(self.temp.cleanup)
        self.settings_path = Path(self.temp.name) / "settings.json"

    def test_missing_settings_file_returns_default(self):
        view = read_evolution_settings(self.settings_path)
        self.assertEqual(view["processes"], default_processes())
        self.assertEqual(view["default"], default_processes())

    def test_none_settings_path_returns_default(self):
        view = read_evolution_settings(None)
        self.assertEqual(view["processes"], default_processes())

    def test_write_then_read_round_trips(self):
        written = write_evolution_settings(self.settings_path, {"processes": 4})
        self.assertEqual(written["processes"], 4)
        self.assertEqual(read_evolution_settings(self.settings_path)["processes"], 4)

    def test_write_preserves_other_settings_json_sections(self):
        atomic_json(self.settings_path, {"output": {"default_backend": "none"}})
        write_evolution_settings(self.settings_path, {"processes": 2})
        import json
        on_disk = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["output"], {"default_backend": "none"})
        self.assertEqual(on_disk["evolution"]["processes"], 2)

    def test_write_rejects_out_of_range_processes(self):
        with self.assertRaises(ConfigError):
            write_evolution_settings(self.settings_path, {"processes": 0})
        with self.assertRaises(ConfigError):
            write_evolution_settings(self.settings_path, {"processes": 10_000})

    def test_write_rejects_unknown_key(self):
        with self.assertRaises(ConfigError):
            write_evolution_settings(self.settings_path, {"bogus": 1})

    def test_write_requires_settings_path(self):
        with self.assertRaises(ConfigError):
            write_evolution_settings(None, {"processes": 4})

    def test_out_of_range_stored_value_falls_back_to_default(self):
        atomic_json(self.settings_path, {"evolution": {"processes": 999}})
        self.assertEqual(read_evolution_settings(self.settings_path)["processes"], default_processes())


class PrepareRunProcessesOverrideTests(unittest.TestCase):
    """prepare_run(processes=...) overrides argv only; config/config_sha256 untouched."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wb-compute-001-prep-")
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.repo = base / "repo"
        self.repo.mkdir()
        for name in ("projects", "templates", "engine", "gapengine", "scripts", "execution"):
            shutil.copytree(ROOT / name, self.repo / name, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(ROOT / "requirements.txt", self.repo / "requirements.txt")
        self.store = ConfigStore(self.repo, base / "control", base / "runs")
        spec = {"label": "確認用", "project_id": "romance", "template_id": "romance",
                "evolution": {"generations": 1, "population": 2, "seeds": 1, "keep": "all"}}
        self.config = self.store.save(spec, config_id="cfg-test")

    def test_processes_none_keeps_config_default(self):
        manifest = self.store.prepare_run("cfg-test", run_id="run-default", job_id="job-default")
        argv = manifest["argv"]
        self.assertEqual(argv[argv.index("--processes") + 1], "1")
        self.assertEqual(manifest["processes"], 1)

    def test_processes_override_changes_argv_not_config(self):
        baseline = self.store.prepare_run("cfg-test", run_id="run-baseline", job_id="job-baseline")
        overridden = self.store.prepare_run("cfg-test", run_id="run-override", job_id="job-override",
                                            processes=8)
        oargv = overridden["argv"]
        self.assertEqual(oargv[oargv.index("--processes") + 1], "8")
        self.assertEqual(overridden["processes"], 8)
        # Everything except the run_id-derived paths and --processes value is identical.
        def normalize(argv):
            return [a.replace("run-baseline", "<rid>").replace("run-override", "<rid>") for a in argv]
        baseline_argv = normalize(baseline["argv"])
        overridden_argv = normalize(oargv)
        baseline_argv[baseline_argv.index("--processes") + 1] = "<n>"
        overridden_argv[overridden_argv.index("--processes") + 1] = "<n>"
        self.assertEqual(baseline_argv, overridden_argv)
        # config_sha256 (what the config *means*) is unaffected by the override.
        self.assertEqual(baseline["config_sha256"], overridden["config_sha256"])
        config = self.store.get("cfg-test")
        self.assertEqual(config["evolution"]["processes"], 1)


if __name__ == "__main__":
    unittest.main()


class GpuThermalSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wb-gpu-thermal-")
        self.addCleanup(self.temp.cleanup)
        self.settings_path = Path(self.temp.name) / "settings.json"

    def _thermal(self):
        import json
        return json.loads(self.settings_path.read_text(encoding="utf-8"))["output"]["gpu_guard"]["thermal"]

    def test_toggle_and_threshold_round_trip_and_keep_other_thermal_keys(self):
        from gapengine.gpu_guard import wait_until_cool
        atomic_json(self.settings_path, {"output": {"gpu_guard": {"observe_seconds": 15, "thermal": {"poll_seconds": 15, "pause_at": 78, "resume_at": 70}}}})
        view = read_evolution_settings(self.settings_path)
        self.assertEqual((view["thermal_enabled"], view["pause_at"]), (True, 78))
        view = write_evolution_settings(self.settings_path, {"processes": 2, "thermal_enabled": True, "pause_at": 72})
        self.assertEqual(view["pause_at"], 72)
        self.assertEqual(self._thermal(), {"poll_seconds": 15, "pause_at": 72, "resume_at": 64, "enabled": True})
        write_evolution_settings(self.settings_path, {"thermal_enabled": False, "pause_at": 72})
        self.assertFalse(read_evolution_settings(self.settings_path)["thermal_enabled"])
        # Disabled thermal guard never waits, even when the GPU is hot.
        self.assertEqual(wait_until_cool(self._thermal(), read=lambda: 99.0, sleep=lambda s: self.fail("slept")), 0.0)
        with self.assertRaises(ConfigError):
            write_evolution_settings(self.settings_path, {"pause_at": 40})

    def test_saving_processes_alone_does_not_create_gpu_guard(self):
        atomic_json(self.settings_path, {"output": {"default_backend": "none"}})
        view = read_evolution_settings(self.settings_path)
        write_evolution_settings(self.settings_path, {"processes": 2, "thermal_enabled": view["thermal_enabled"], "pause_at": view["pause_at"]})
        import json
        self.assertNotIn("gpu_guard", json.loads(self.settings_path.read_text(encoding="utf-8"))["output"])
