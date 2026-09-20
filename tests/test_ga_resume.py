"""WB-GA-RESUME: resuming a GA run from its last completed generation.

Every scenario here uses a tiny population/seed count and either kappa=0 or
the network-free "fake" rationality backend -- no Ollama, no GPU (plan §0).
"""

from __future__ import annotations

import json
import random
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from execution.configs import evolution_defaults, normalize
from gapengine.evolve import evolve
from gapengine.genome import Genome
from gapengine.qd import Archive, Descriptor, Elite
from scripts.evolve import build_parser
from test_gapengine import TEMPLATE, make_reaching_project


def _files_under(root: Path) -> dict[str, Path]:
    return {
        path.relative_to(root).as_posix(): path
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _assert_same_output(test: unittest.TestCase, expected_dir: Path, actual_dir: Path) -> None:
    """Every file byte-identical, except ga_state.json where only the three
    fields that matter for resuming are compared (plan §3 item 1)."""

    expected_files = _files_under(expected_dir)
    actual_files = _files_under(actual_dir)
    test.assertEqual(set(expected_files), set(actual_files))
    for relative, expected_path in expected_files.items():
        actual_path = actual_files[relative]
        if relative == "ga_state.json":
            expected_state = json.loads(expected_path.read_text(encoding="utf-8"))
            actual_state = json.loads(actual_path.read_text(encoding="utf-8"))
            for key in ("completed_generations", "ga_rng_state", "previous_results"):
                test.assertEqual(
                    expected_state[key], actual_state[key], f"{relative}:{key}"
                )
            continue
        test.assertEqual(
            expected_path.read_bytes(), actual_path.read_bytes(), relative
        )


class ByteIdenticalResumeTests(unittest.TestCase):
    """Plan §3 item 1 (+ item 3's coevolve variant, item 4's kappa>0
    variant): a run split into two resumed halves must match a single
    continuous run, file for file, under every ``keep`` policy (item 2.3)."""

    def test_full_run_matches_resumed_run_under_every_keep_policy(self) -> None:
        for keep in ("all", "reached", "exemplar"):
            with self.subTest(keep=keep):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    project = make_reaching_project(root)
                    common = {
                        "ga_seed": 3,
                        "keep": keep,
                        "population": 4,
                        "processes": 1,
                        "project": project,
                        "seed_base": 5,
                        "seeds": 1,
                        "template": TEMPLATE,
                    }

                    evolve({**common, "generations": 4, "out": root / "full"})
                    evolve({**common, "generations": 2, "out": root / "resumed"})
                    evolve(
                        {
                            **common,
                            "generations": 4,
                            "out": root / "resumed",
                            "resume": True,
                        }
                    )

                    _assert_same_output(self, root / "full", root / "resumed")

    def test_coevolve_full_run_matches_resumed_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = make_reaching_project(root)
            common = {
                "coevolve": True,
                "ga_seed": 7,
                "keep": "all",
                "population": 4,
                "processes": 1,
                "project": project,
                "seed_base": 9,
                "seeds": 1,
                "template": TEMPLATE,
            }

            evolve({**common, "generations": 3, "out": root / "full"})
            evolve({**common, "generations": 1, "out": root / "resumed"})
            evolve(
                {
                    **common,
                    "generations": 3,
                    "out": root / "resumed",
                    "resume": True,
                }
            )

            _assert_same_output(self, root / "full", root / "resumed")

    def test_kappa_positive_fake_backend_full_run_matches_resumed_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = make_reaching_project(root)
            common = {
                "ga_seed": 11,
                "keep": "all",
                "population": 3,
                "processes": 1,
                "project": project,
                "rationality": {"kappa": 1.0, "backend": "fake", "method": "noul"},
                "seed_base": 13,
                "seeds": 1,
                "template": TEMPLATE,
            }

            evolve({**common, "generations": 3, "out": root / "full"})
            evolve({**common, "generations": 1, "out": root / "resumed"})
            evolve(
                {
                    **common,
                    "generations": 3,
                    "out": root / "resumed",
                    "resume": True,
                }
            )

            _assert_same_output(self, root / "full", root / "resumed")


class CrashRecoveryTests(unittest.TestCase):
    """Plan §3 item 2: an exception mid-evaluation of generation 2 leaves a
    half-written ``g2`` directory; resuming must delete it and recompute an
    output identical to an uninterrupted run."""

    def test_crash_during_generation_evaluation_then_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = make_reaching_project(root)
            common = {
                "ga_seed": 17,
                "keep": "all",
                "population": 4,
                "processes": 1,
                "project": project,
                "seed_base": 19,
                "seeds": 1,
                "template": TEMPLATE,
            }

            evolve({**common, "generations": 3, "out": root / "full"})

            import gapengine.evolve as evolve_module

            real_evaluate_jobs = evolve_module._evaluate_jobs
            calls = {"n": 0}

            def flaky(jobs, processes, observer=None):
                calls["n"] += 1
                if calls["n"] == 3:  # generation 2's protagonist pass
                    raise RuntimeError("simulated crash mid generation 2")
                return real_evaluate_jobs(jobs, processes, observer)

            with patch("gapengine.evolve._evaluate_jobs", side_effect=flaky):
                with self.assertRaises(RuntimeError):
                    evolve({**common, "generations": 3, "out": root / "resumed"})

            state = json.loads(
                (root / "resumed" / "ga_state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["completed_generations"], 2)
            stale_generation_dir = root / "resumed" / "g2"
            self.assertTrue((stale_generation_dir / "population.json").exists())
            self.assertFalse((stale_generation_dir / "results.json").exists())

            evolve(
                {
                    **common,
                    "generations": 3,
                    "out": root / "resumed",
                    "resume": True,
                }
            )

            _assert_same_output(self, root / "full", root / "resumed")


class ArchiveRoundTripTests(unittest.TestCase):
    """Plan §3 item 7: Archive.to_dict() -> Archive.load() -> to_dict() must
    be byte-identical, since resume rebuilds the archive this way."""

    def test_archive_round_trip_is_byte_identical(self) -> None:
        archive = Archive()
        archive.freeze_thresholds([0.0, 1.0, 2.0, 3.0])
        for index, category in enumerate(("I", "II")):
            archive.insert(
                Elite(
                    genome=Genome.random(random.Random(index)),
                    quality=0.5 + index * 0.1,
                    descriptor=Descriptor(
                        category=category,
                        volatility=float(index),
                        volatility_bin=archive.bin_for(float(index)),
                    ),
                    reach_rate=1.0,
                    exemplar={
                        "engine_hash": "abc123",
                        "layers_path": f"g0/ind-{index}/seed-0/layers.jsonl",
                        "precedent_hash": "precedent",
                        "seed": 0,
                    },
                    generation=0,
                    parents=("g0/ind-0", "g0/ind-1"),
                )
            )

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "archive.json"
            archive.save(path)
            before = path.read_bytes()
            Archive.load(path).save(path)
            self.assertEqual(before, path.read_bytes())


class NonResumeUnaffectedTests(unittest.TestCase):
    """Plan §3 item 8: a run that never uses ``resume`` produces the same
    output as before WB-GA-RESUME, except for the new ga_state.json file --
    every other output file must contain no resume-only keys."""

    def test_no_resume_only_keys_leak_outside_ga_state_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = make_reaching_project(root)
            evolve(
                {
                    "ga_seed": 23,
                    "generations": 2,
                    "keep": "all",
                    "out": root / "out",
                    "population": 4,
                    "processes": 1,
                    "project": project,
                    "seed_base": 27,
                    "seeds": 1,
                    "template": TEMPLATE,
                }
            )

            # "engine_hash" is deliberately excluded here: it is also a
            # pre-existing per-run exemplar field (Simulation's header),
            # unrelated to ga_state.json's top-level field of the same
            # name, so it legitimately appears in archive.json/results.json
            # already and is not a resume-only leak.
            resume_only_keys = {"cfg_fingerprint", "ga_rng_state"}
            for relative, path in _files_under(root / "out").items():
                if relative == "ga_state.json" or path.suffix != ".json":
                    continue
                payload = json.loads(path.read_text(encoding="utf-8"))
                blob = json.dumps(payload)
                for key in resume_only_keys:
                    self.assertNotIn(key, blob, relative)

    def test_two_independent_non_resume_runs_are_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = make_reaching_project(root)
            common = {
                "ga_seed": 31,
                "generations": 2,
                "keep": "all",
                "population": 4,
                "processes": 1,
                "project": project,
                "seed_base": 37,
                "seeds": 1,
                "template": TEMPLATE,
            }
            evolve({**common, "out": root / "one"})
            evolve({**common, "out": root / "two"})
            _assert_same_output(self, root / "one", root / "two")


class ResumeGuardrailTests(unittest.TestCase):
    """Plan §3 items 5 and 6: mismatched settings refuse to resume;
    already-finished or brand-new runs are handled without error."""

    def _common(self, project: Path) -> dict:
        return {
            "ga_seed": 41,
            "keep": "all",
            "population": 4,
            "processes": 1,
            "project": project,
            "seed_base": 43,
            "seeds": 1,
            "template": TEMPLATE,
        }

    def test_changing_population_refuses_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = make_reaching_project(root)
            common = self._common(project)
            evolve({**common, "generations": 2, "out": root / "out"})
            with self.assertRaises(ValueError):
                evolve(
                    {
                        **common,
                        "population": 5,
                        "generations": 3,
                        "out": root / "out",
                        "resume": True,
                    }
                )

    def test_changing_ga_seed_refuses_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = make_reaching_project(root)
            common = self._common(project)
            evolve({**common, "generations": 2, "out": root / "out"})
            with self.assertRaises(ValueError):
                evolve(
                    {
                        **common,
                        "ga_seed": 999,
                        "generations": 3,
                        "out": root / "out",
                        "resume": True,
                    }
                )

    def test_changing_a_template_byte_refuses_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = make_reaching_project(root)
            template_copy = root / "template"
            shutil.copytree(TEMPLATE, template_copy)
            common = {**self._common(project), "template": template_copy}
            evolve({**common, "generations": 2, "out": root / "out"})

            with (template_copy / "qd.yaml").open("a", encoding="utf-8") as handle:
                handle.write("# resume-test-byte-flip\n")

            with self.assertRaises(ValueError):
                evolve(
                    {
                        **common,
                        "generations": 3,
                        "out": root / "out",
                        "resume": True,
                    }
                )

    def test_changing_only_generations_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = make_reaching_project(root)
            common = self._common(project)
            evolve({**common, "generations": 2, "out": root / "out"})
            # Must not raise: generations is excluded from the fingerprint.
            evolve(
                {**common, "generations": 3, "out": root / "out", "resume": True}
            )

    def test_already_completed_generations_is_a_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = make_reaching_project(root)
            common = self._common(project)
            evolve({**common, "generations": 2, "out": root / "out"})
            before = _files_under(root / "out")
            before_bytes = {name: path.read_bytes() for name, path in before.items()}

            archive = evolve(
                {**common, "generations": 2, "out": root / "out", "resume": True}
            )

            after = _files_under(root / "out")
            self.assertEqual(set(before), set(after))
            for name, path in after.items():
                self.assertEqual(before_bytes[name], path.read_bytes(), name)
            self.assertGreaterEqual(len(archive.cells), 1)
            self.assertFalse((root / "out" / "g2").exists())

    def test_missing_state_with_generation_directories_refuses_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            out_dir = root / "out"
            (out_dir / "g0").mkdir(parents=True)
            project = make_reaching_project(root)
            with self.assertRaises(ValueError):
                evolve(
                    {
                        **self._common(project),
                        "generations": 2,
                        "out": out_dir,
                        "resume": True,
                    }
                )

    def test_missing_state_with_empty_out_dir_starts_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = make_reaching_project(root)
            common = self._common(project)

            evolve({**common, "generations": 2, "out": root / "baseline"})
            evolve(
                {
                    **common,
                    "generations": 2,
                    "out": root / "fresh",
                    "resume": True,
                }
            )

            _assert_same_output(self, root / "baseline", root / "fresh")


class ExecutionConfigsResumeExclusionTests(unittest.TestCase):
    """Plan §3 item 9: ``resume`` must never leak into a saved config's
    evolution.* keys or into prepare_run()'s generated CLI argv."""

    def test_resume_is_not_a_normalizable_evolution_key(self) -> None:
        self.assertNotIn("resume", evolution_defaults())
        with self.assertRaises(Exception):
            normalize(
                {
                    "label": "x",
                    "project_id": "romance",
                    "template_id": "romance",
                    "evolution": {"resume": True},
                }
            )

    def test_cli_parser_still_defaults_resume_to_false_but_excluded_from_defaults(
        self,
    ) -> None:
        args = build_parser().parse_args(
            ["--project", "x", "--template", "y", "--out", "z"]
        )
        self.assertFalse(args.resume)
        self.assertNotIn("resume", evolution_defaults())


if __name__ == "__main__":
    unittest.main()
