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
from test_gapengine import PROJECT, ROOT, TEMPLATE, make_reaching_project


def _rewrite_json(path: Path, value) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _patch_crash_after_write(filename: str, *, min_generation: int, require_new_cell: bool = False):
    """A ``gapengine.evolve._json_write`` replacement (Opus review probe
    pattern) that writes normally, then raises exactly once, right after
    the first ``min_generation``-or-later write of ``filename`` -- the
    non-atomic archive.json/summary.json/ga_state.json window a crash can
    land in mid-generation. Returns (patcher, triggered) -- ``triggered["done"]``
    must be True after the call, or the window was never hit."""

    import gapengine.evolve as evolve_module

    real_write = evolve_module._json_write
    triggered = {"done": False}

    def fake_write(path: Path, value) -> None:
        real_write(path, value)
        if triggered["done"] or path.name != filename:
            return
        state_file = path.parent / "ga_state.json"
        completed = (
            json.loads(state_file.read_text(encoding="utf-8"))["completed_generations"]
            if state_file.is_file()
            else 0
        )
        if completed < min_generation:
            return
        if require_new_cell and not any(
            int(cell["generation"]) == completed for cell in value["cells"].values()
        ):
            return
        triggered["done"] = True
        raise RuntimeError(
            f"simulated crash right after {filename} of generation {completed}"
        )

    return patch("gapengine.evolve._json_write", side_effect=fake_write), triggered


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

    def test_crash_during_generation_zero_then_resume_matches_fresh_run(self) -> None:
        """Opus review follow-up (recommended item 5): generation 0 has no
        prior checkpoint to fall back to, so a crash there must still be
        recoverable, not a permanent dead end."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = make_reaching_project(root)
            common = {
                "ga_seed": 53,
                "keep": "all",
                "population": 4,
                "processes": 1,
                "project": project,
                "seed_base": 59,
                "seeds": 1,
                "template": TEMPLATE,
            }

            evolve({**common, "generations": 2, "out": root / "full"})

            def flaky(jobs, processes, observer=None):
                raise RuntimeError("simulated crash during generation 0")

            with patch("gapengine.evolve._evaluate_jobs", side_effect=flaky):
                with self.assertRaises(RuntimeError):
                    evolve({**common, "generations": 2, "out": root / "resumed"})

            self.assertFalse((root / "resumed" / "ga_state.json").exists())
            self.assertTrue((root / "resumed" / "g0").exists())

            evolve(
                {**common, "generations": 2, "out": root / "resumed", "resume": True}
            )

            _assert_same_output(self, root / "full", root / "resumed")


class NonAtomicCheckpointWindowTests(unittest.TestCase):
    """Opus review: a generation writes archive.json -> summary.json ->
    ga_state.json in that order, none of them atomic with each other. A
    crash in either gap must not corrupt a later resume -- version 2's
    embedded archive and the summaries[:start_generation] truncation are
    exactly what makes that true regardless of where the crash lands."""

    _ROMANCE_COMMON = {
        "ga_seed": 5,
        "keep": "all",
        "population": 8,
        "processes": 1,
        "project": ROOT / "projects" / "romance",
        "seed_base": 2,
        "seeds": 2,
        "template": ROOT / "templates" / "romance",
        "generations": 6,
    }

    def test_crash_right_after_archive_json_of_a_cell_winning_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            common = self._ROMANCE_COMMON

            evolve({**common, "out": root / "full"})

            patcher, triggered = _patch_crash_after_write(
                "archive.json", min_generation=1, require_new_cell=True
            )
            with patcher:
                with self.assertRaises(RuntimeError):
                    evolve({**common, "out": root / "crashed"})
            self.assertTrue(
                triggered["done"],
                "never hit the archive.json-then-crash window -- test is inconclusive",
            )

            evolve({**common, "out": root / "crashed", "resume": True})
            _assert_same_output(self, root / "full", root / "crashed")

    def test_crash_right_after_summary_json_before_ga_state_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            common = {
                "ga_seed": 5,
                "keep": "all",
                "population": 6,
                "processes": 1,
                "project": ROOT / "projects" / "romance",
                "seed_base": 2,
                "seeds": 1,
                "template": ROOT / "templates" / "romance",
                "generations": 5,
            }

            evolve({**common, "out": root / "full"})

            patcher, triggered = _patch_crash_after_write(
                "summary.json", min_generation=1
            )
            with patcher:
                with self.assertRaises(RuntimeError):
                    evolve({**common, "out": root / "crashed"})
            self.assertTrue(
                triggered["done"],
                "never hit the summary.json-then-crash window -- test is inconclusive",
            )
            # The stray summary.json entry for the crashed generation must
            # not have made it into a resumed run's final output.
            evolve({**common, "out": root / "crashed", "resume": True})
            _assert_same_output(self, root / "full", root / "crashed")


class LegacyVersion1CheckpointTests(unittest.TestCase):
    """Plan item: a version-1 ga_state.json (WB-GA-RESUME's first cut,
    HEAD 03106ae -- no embedded archive, no gapengine_hash) must still be
    resumable, and an archive.json that got ahead of that checkpoint (the
    exact bug this fix addresses) must be refused with a clear message."""

    def _common(self, project: Path) -> dict:
        return {
            "ga_seed": 61,
            "keep": "all",
            "population": 4,
            "processes": 1,
            "project": project,
            "seed_base": 67,
            "seeds": 1,
            "template": TEMPLATE,
        }

    @staticmethod
    def _downgrade_to_version1(state_path: Path) -> None:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        legacy = {
            key: value
            for key, value in state.items()
            if key not in ("archive", "antagonist_archive", "gapengine_hash")
        }
        legacy["version"] = 1
        _rewrite_json(state_path, legacy)

    def test_version1_state_without_embedded_archive_still_resumes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = make_reaching_project(root)
            common = self._common(project)

            evolve({**common, "generations": 4, "out": root / "full"})
            evolve({**common, "generations": 2, "out": root / "resumed"})
            self._downgrade_to_version1(root / "resumed" / "ga_state.json")

            # No gapengine_hash key in this state -- must not be checked
            # (item 6a), so this succeeds even though the *current*
            # gapengine_hash of course differs from "absent".
            evolve(
                {**common, "generations": 4, "out": root / "resumed", "resume": True}
            )

            _assert_same_output(self, root / "full", root / "resumed")

    def test_archive_json_ahead_of_checkpoint_refuses_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = make_reaching_project(root)
            common = self._common(project)

            evolve({**common, "generations": 2, "out": root / "resumed"})
            self._downgrade_to_version1(root / "resumed" / "ga_state.json")

            archive_path = root / "resumed" / "archive.json"
            archive_raw = json.loads(archive_path.read_text(encoding="utf-8"))
            self.assertTrue(archive_raw["cells"], "fixture produced no elites to corrupt")
            any_cell = next(iter(archive_raw["cells"]))
            archive_raw["cells"][any_cell]["generation"] = 99
            _rewrite_json(archive_path, archive_raw)

            with self.assertRaises(ValueError):
                evolve(
                    {**common, "generations": 4, "out": root / "resumed", "resume": True}
                )

    def test_unknown_state_version_refuses_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = make_reaching_project(root)
            common = self._common(project)

            evolve({**common, "generations": 2, "out": root / "out"})
            state_path = root / "out" / "ga_state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["version"] = 3
            _rewrite_json(state_path, state)

            with self.assertRaises(ValueError):
                evolve(
                    {**common, "generations": 3, "out": root / "out", "resume": True}
                )


class GapengineHashGuardTests(unittest.TestCase):
    """Plan item 6: gapengine/*.py's content hash is checked like
    engine_hash, downgradable to a warning with resume_allow_code_change."""

    def _common(self, project: Path) -> dict:
        return {
            "ga_seed": 71,
            "keep": "all",
            "population": 4,
            "processes": 1,
            "project": project,
            "seed_base": 73,
            "seeds": 1,
            "template": TEMPLATE,
        }

    def test_gapengine_hash_mismatch_refuses_resume_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = make_reaching_project(root)
            common = self._common(project)
            evolve({**common, "generations": 2, "out": root / "out"})

            state_path = root / "out" / "ga_state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["gapengine_hash"] = "deadbeefdead"
            _rewrite_json(state_path, state)

            with self.assertRaises(ValueError):
                evolve(
                    {**common, "generations": 3, "out": root / "out", "resume": True}
                )

    def test_gapengine_hash_mismatch_continues_with_resume_allow_code_change(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = make_reaching_project(root)
            common = self._common(project)
            evolve({**common, "generations": 2, "out": root / "out"})

            state_path = root / "out" / "ga_state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["gapengine_hash"] = "deadbeefdead"
            _rewrite_json(state_path, state)

            archive = evolve(
                {
                    **common,
                    "generations": 3,
                    "out": root / "out",
                    "resume": True,
                    "resume_allow_code_change": True,
                }
            )

            self.assertGreaterEqual(len(archive.cells), 1)
            after = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(after["completed_generations"], 3)


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

    def test_missing_state_with_generation_directories_beyond_g0_refuses_resume(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            out_dir = root / "out"
            (out_dir / "g0").mkdir(parents=True)
            (out_dir / "g1").mkdir(parents=True)
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

    def test_missing_state_with_only_generation_zero_clears_it_and_starts_fresh(
        self,
    ) -> None:
        """Opus review follow-up: generation 0 crashing before its own
        first checkpoint ever existed must not refuse resume forever, since
        the caller always launches with --resume from the start."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = make_reaching_project(root)
            common = self._common(project)

            evolve({**common, "generations": 2, "out": root / "baseline"})

            out_dir = root / "crashed-g0"
            (out_dir / "g0" / "leftover").mkdir(parents=True)
            (out_dir / "g0" / "leftover" / "junk.txt").write_text(
                "half-written generation 0\n", encoding="utf-8"
            )
            evolve(
                {**common, "generations": 2, "out": out_dir, "resume": True}
            )

            _assert_same_output(self, root / "baseline", out_dir)

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


class CfgFingerprintNumCtxTests(unittest.TestCase):
    """WB-JEV-003: ``num_ctx`` is an operational knob like table path/
    thermal_guard, not a knob that changes what the GA computes -- but
    unlike those two, its presence/absence must fingerprint identically to
    a run with no ``num_ctx`` key at all (the pre-WB-JEV-003 shape), so an
    already in-flight experiment's ga_state.json keeps resuming after this
    change lands."""

    def _fingerprint(self, rationality_cfg):
        from gapengine.evolve import _cfg_fingerprint

        return _cfg_fingerprint(
            project_dir=PROJECT,
            template_dir=TEMPLATE,
            population_size=2,
            seed_count=1,
            seed_base=0,
            ga_seed=1,
            keep="all",
            coevolve=False,
            meta_evolution=False,
            target_ending=None,
            record_explanations=True,
            rationality_cfg=rationality_cfg,
        )

    def _base_cfg(self):
        return {
            "backend": "fake", "kappa": 1.0, "max_judge_calls": None,
            "method": "noul", "model": "fake-v1",
        }

    def test_num_ctx_none_fingerprints_the_same_as_the_key_being_absent(self) -> None:
        without_key = self._fingerprint(self._base_cfg())
        with_none = self._fingerprint({**self._base_cfg(), "num_ctx": None})
        self.assertEqual(without_key, with_none)

    def test_num_ctx_value_changes_the_fingerprint(self) -> None:
        without_key = self._fingerprint(self._base_cfg())
        with_value = self._fingerprint({**self._base_cfg(), "num_ctx": 2048})
        self.assertNotEqual(without_key, with_value)

    def test_no_rationality_cfg_at_all_is_unaffected(self) -> None:
        self.assertEqual(self._fingerprint(None), self._fingerprint(None))


if __name__ == "__main__":
    unittest.main()
