"""Run-setting, frozen-worker, CLI and GA pressure integration."""

import importlib
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import yaml
from engine.time_pressure import PROFILE
from engine.world_format import initialize
from execution.configs import ConfigStore, evolution_defaults, normalize
from execution.provenance import ConfigError
from test_goal_planner import load

ROOT = Path(__file__).resolve().parents[1]


def sources(world, kind="public"):
    awareness = {"kind": kind}
    if kind == "known_fact":
        awareness["fact_id"] = next(iter(world.facts))
    return {
        "version": 1,
        "sources": [
            {
                "id": "test-deadline",
                "anchor": {"kind": "goal_period", "id": "beacon_sent"},
                "response_goal_id": "beacon_sent",
                "actor_ids": [world.protagonist],
                "awareness": awareness,
                "estimate": {"duration": 6, "fact_id": next(iter(world.facts))},
            }
        ],
    }


class ConfigAndWiringTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pressure-config-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / "repo"
        self.repo.mkdir()
        for area in (
            "projects",
            "templates",
            "engine",
            "gapengine",
            "scripts",
            "execution",
        ):
            shutil.copytree(
                ROOT / area,
                self.repo / area,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
        shutil.copyfile(ROOT / "requirements.txt", self.repo / "requirements.txt")
        world_path = self.repo / "projects/ship_planning/world.yaml"
        definition = yaml.safe_load(world_path.read_text(encoding="utf-8"))
        definition = initialize(
            {
                **definition,
                "gapengine": {
                    "action_graph": "templates/basic/action_graph.yaml",
                    "effects": "templates/basic/effects.yaml",
                },
            }
        )
        world_path.write_text(
            yaml.safe_dump(definition, allow_unicode=True), encoding="utf-8"
        )
        self.store = ConfigStore(self.repo, self.base / "control", self.base / "runs")
        self.spec = {
            "label": "pressure fixture",
            "project_id": "ship_planning",
            "template_id": "basic",
            "evolution": {
                "generations": 1,
                "population": 1,
                "seeds": 1,
                "processes": 1,
                "keep": "all",
                "kappa": 0,
            },
        }
        w, p, _ = load()
        self.doc = sources(w)
        self.path = self.repo / "projects/ship_planning/time-pressure.yaml"
        self.path.write_text(
            yaml.safe_dump(self.doc, allow_unicode=True), encoding="utf-8"
        )

    def test_off_normalized_compilation_and_capture_ignore_sidecar(self):
        self.assertNotIn("time_pressure", evolution_defaults())
        self.assertEqual(
            normalize(self.spec),
            normalize({**self.spec, "time_pressure": {"enabled": False}}),
        )
        a = self.store.preview(self.spec)
        self.path.write_text("broken: [", encoding="utf-8")
        self.assertEqual(
            a, self.store.preview({**self.spec, "time_pressure": {"enabled": False}})
        )
        cfg = self.store.save(self.spec, config_id="off")
        manifest = self.store.prepare_run(
            cfg["config_id"], run_id="off-run", job_id="job-off"
        )
        self.assertNotIn("time_pressure", manifest)
        self.assertNotIn("--time-pressure", manifest["argv"])
        self.assertFalse(
            (
                self.base
                / "runs/off-run/inputs/projects/ship_planning/time-pressure.yaml"
            ).exists()
        )

    def test_on_save_reload_capture_cli_and_duplicate_off(self):
        on = {**self.spec, "time_pressure": {"enabled": True}}
        cfg = self.store.save(on, config_id="on")
        self.assertEqual(self.store.get("on")["time_pressure"], PROFILE)
        manifest = self.store.prepare_run("on", run_id="on-run", job_id="job-on")
        self.assertEqual(manifest["time_pressure"], PROFILE)
        self.assertIn("--time-pressure", manifest["argv"])
        self.assertTrue(
            (
                self.base
                / "runs/on-run/inputs/projects/ship_planning/time-pressure.yaml"
            ).is_file()
        )
        off = self.store.duplicate(
            "on", {"time_pressure": {"enabled": False}}, new_id="off-again"
        )
        self.assertNotIn("time_pressure", off)
        self.assertTrue(self.path.exists())
        self.assertFalse(
            (
                self.base
                / "control/configs/off-again/inputs/projects/ship_planning/time-pressure.yaml"
            ).exists()
        )

    def test_on_rejects_missing_source_and_unsupported_modes(self):
        self.path.unlink()
        with self.assertRaises(ConfigError):
            self.store.preview({**self.spec, "time_pressure": {"enabled": True}})
        spec = {
            **self.spec,
            "time_pressure": {"enabled": True},
            "evolution": {"world_expansion": "expand"},
        }
        with self.assertRaises(ConfigError):
            normalize(spec)

    def test_cli_off_does_not_insert_new_cfg_and_on_passes_canonical(self):
        cli = importlib.import_module("scripts.evolve")
        argv = [
            "--project",
            str(self.repo / "projects/ship_planning"),
            "--template",
            str(self.repo / "templates/basic"),
            "--out",
            str(self.base / "output"),
        ]
        for enabled in (False, True):
            with patch.object(cli, "evolve", return_value=SimpleNamespace(cells={})), (
                patch.object(cli, "_show_optional_outputs")
                if hasattr(cli, "_show_optional_outputs")
                else patch("builtins.print")
            ):
                with patch.object(
                    cli, "evolve", return_value=SimpleNamespace(cells={})
                ) as call:
                    cli.main(argv + (["--time-pressure"] if enabled else []))
                    cfg = call.call_args[0][0]
                    self.assertEqual(
                        cfg.get("time_pressure"), PROFILE if enabled else None
                    )
                    if not enabled:
                        self.assertNotIn("time_pressure", cfg)

    def test_fingerprint_new_key_only_when_enabled(self):
        module = importlib.import_module("gapengine.evolve")
        kw = dict(
            project_dir=self.repo / "projects/ship_planning",
            template_dir=self.repo / "templates/basic",
            population_size=1,
            seed_count=1,
            seed_base=1,
            ga_seed=1,
            keep="all",
            coevolve=False,
            meta_evolution=False,
            target_ending=None,
            record_explanations=False,
            rationality_cfg=None,
        )
        off = module._cfg_fingerprint(**kw)
        self.assertEqual(off, module._cfg_fingerprint(**kw, time_pressure_cfg=None))
        self.assertNotEqual(
            off,
            module._cfg_fingerprint(
                **kw, time_pressure_cfg={**PROFILE, "sources_sha256": "a" * 64}
            ),
        )
        self.path.write_text("other sidecar", encoding="utf-8")
        self.assertEqual(off, module._cfg_fingerprint(**kw))

    def test_actual_worker_manifest_compiles_same_on_off_setting(self):
        adapter = importlib.import_module("execution.evolution_worker")
        for enabled in (False, True):
            spec = {
                **self.spec,
                **({"time_pressure": {"enabled": True}} if enabled else {}),
            }
            ident = "worker-on" if enabled else "worker-off"
            self.store.save(spec, config_id=ident)
            manifest = self.store.prepare_run(
                ident, run_id=ident + "-run", job_id=ident + "-job"
            )
            run = self.base / "runs" / (ident + "-run")
            job = {
                "run_id": run.name,
                "config_id": ident,
                "kind": "evolve",
                "nonce": "fixture",
            }
            with patch.object(
                adapter, "__file__", str(run / "runtime/execution/evolution_worker.py")
            ), patch("execution.worker.read_job", return_value=job), patch.object(
                adapter, "read_jev_api_key", return_value=None
            ), patch.object(
                adapter, "EvolutionObserver"
            ), patch.object(
                adapter, "evolve"
            ) as execute:
                self.assertEqual(
                    adapter.main(
                        [
                            "--run",
                            str(run),
                            "--control",
                            str(self.base / "control"),
                            "--job",
                            ident + "-job",
                        ]
                    ),
                    0,
                )
            cfg = execute.call_args[0][0]
            self.assertEqual(cfg.get("time_pressure"), PROFILE if enabled else None)
            if not enabled:
                self.assertNotIn("time_pressure", cfg)
            self.assertEqual(cfg["project"], run / "inputs/projects/ship_planning")

    def test_worker_still_rejects_execution_outside_frozen_runtime(self):
        adapter = importlib.import_module("execution.evolution_worker")
        with patch.object(
            adapter, "__file__", str(ROOT / "execution/evolution_worker.py")
        ):
            with self.assertRaisesRegex(ValueError, "frozen run runtime"):
                adapter.main(
                    [
                        "--run",
                        str(self.base / "runs/outside"),
                        "--control",
                        str(self.base / "control"),
                        "--job",
                        "outside-job",
                    ]
                )

    def test_real_single_generation_frozen_fixture_runs_without_model_calls(self):
        module = importlib.import_module("gapengine.evolve")
        out = self.base / "ga-output"
        cfg = {
            "project": self.repo / "projects/ship_planning",
            "template": self.repo / "templates/basic",
            "out": out,
            "population": 1,
            "generations": 1,
            "seeds": 1,
            "processes": 1,
            "seed_base": 11,
            "ga_seed": 2,
            "keep": "all",
            "record_explanations": True,
            "time_pressure": PROFILE,
            "rationality": {"kappa": 0, "backend": "none"},
        }
        module.evolve(cfg)
        files = list(out.rglob("layers.jsonl"))
        self.assertTrue(files)
        rows = [
            json.loads(line)
            for line in files[0].read_text(encoding="utf-8").splitlines()
        ]
        decisions = [
            r
            for r in rows
            if r.get("kind") == "decision" and r.get("subject") == "通信士"
        ]
        self.assertTrue(
            any(("time_pressure" in (r.get("explanation") or {}) for r in decisions))
        )
        self.assertTrue((out / "ga_state.json").is_file())
        module.evolve({**cfg, "resume": True})
        changed = json.loads(json.dumps(self.doc))
        changed["sources"][0]["estimate"]["duration"] += 1
        self.path.write_text(yaml.safe_dump(changed), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "cfg_fingerprint"):
            module.evolve({**cfg, "resume": True})

    def test_concurrent_saved_deadline_settings_are_refused(self):
        from execution import purpose_preparation
        from execution.library import LibraryStore

        world_path = self.path.parent / "world.yaml"
        world = yaml.safe_load(world_path.read_bytes())
        ending = world["ending"][0]
        ending["when"] = (
            f"zone({world['protagonist']!r}) == {world['zones'][-1]['name']!r}"
        )
        world["target_ending"] = [ending["id"]]
        world_path.write_text(
            yaml.safe_dump(world, allow_unicode=True), encoding="utf-8"
        )
        spec = {**self.spec, "time_pressure": {"enabled": True}}
        baseline = self.store.preview(spec)
        library = LibraryStore(self.repo)
        current = purpose_preparation.snapshot(library, "ship_planning")
        self.assertTrue(current["available"])
        candidate = next(
            item
            for item in current["candidates"]
            if item["supported"] and item["status"] != "stale"
        )
        entry = {
            "candidate_id": candidate["id"],
            "day": world["time"]["days"],
            "slot": world["time"]["slots"][-1],
            "awareness": {"kind": "public"},
        }
        values = {
            "managed_ids": [candidate["id"]],
            "pressure": {"enabled": True, "entries": [entry]},
        }
        saved = purpose_preparation.save(
            library,
            "ship_planning",
            {
                "revision": current["revision"],
                "values": values,
            },
        )
        with self.assertRaisesRegex(ConfigError, "同時に有効にできません"):
            self.store.preview(spec)
        values["pressure"]["enabled"] = False
        purpose_preparation.save(
            library,
            "ship_planning",
            {
                "revision": saved["revision"],
                "values": values,
            },
        )
        self.assertEqual(baseline, self.store.preview(spec))

    def test_run_form_restores_the_saved_switch(self):
        from html.parser import HTMLParser
        from viewer import run_settings, workbench_pages

        class Fields(HTMLParser):
            def __init__(self, text):
                super().__init__()
                self.pressure = None
                self.feed(text)

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if attrs.get("data-field") == "time_pressure.enabled":
                    self.pressure = attrs

        store = SimpleNamespace(
            configs=SimpleNamespace(repo=self.repo, list=lambda: []), list=lambda: []
        )
        values = workbench_pages._new_config_values()
        values.update(project_id="ship_planning", template_id="basic")
        for enabled in (False, True):
            parent = {"config_id": "saved", "time_pressure": {"enabled": enabled}}
            captured = []
            handler = SimpleNamespace(_send_html=captured.append)
            with patch("viewer.workbench_pages._job_store", return_value=store):
                run_settings.render(
                    handler,
                    values,
                    projects=["ship_planning"],
                    templates=["basic"],
                    worlds=[{"id": "ship_planning", "name": "fixture"}],
                    parent=parent,
                )
            field = Fields(captured[0]).pressure
            self.assertIsNotNone(field)
            self.assertEqual("checked" in field, enabled)
            self.assertIn("data-time-pressure-availability", captured[0])
