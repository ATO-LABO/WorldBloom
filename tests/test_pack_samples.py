import json
import tempfile
import unittest
from pathlib import Path

from scripts import pack_samples


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class PackSamplesTest(unittest.TestCase):
    def test_only_exemplar_layers_are_copied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "exp1"
            out_dir = root / "samples" / "exp1"

            archive = {
                "cells": {
                    "I|low": {
                        "exemplar": {"layers_path": "g0/ind-0/seed-1/layers.jsonl"},
                    }
                },
                "volatility_thresholds": {},
            }
            _write(run_dir / "archive.json", json.dumps(archive))
            _write(run_dir / "summary.json", "{}")
            _write(run_dir / "synopses.json", "{}")
            _write(run_dir / "selection.json", '{"selected": []}')
            _write(run_dir / "g0" / "population.json", "[]")
            _write(run_dir / "prompts" / "synopsis-I-low.txt", "prompt")
            _write(run_dir / "stories" / "I-low.md", "story")

            # exemplar layers.jsonl (should be copied)
            _write(run_dir / "g0" / "ind-0" / "seed-1" / "layers.jsonl", '{"kind":"header"}')
            # a sibling individual's log (must NOT be copied)
            _write(run_dir / "g0" / "ind-1" / "seed-2" / "layers.jsonl", '{"kind":"header"}')

            files, total = pack_samples.pack_experiment(run_dir, out_dir)

            self.assertTrue((out_dir / "archive.json").is_file())
            self.assertTrue((out_dir / "summary.json").is_file())
            self.assertTrue((out_dir / "synopses.json").is_file())
            self.assertTrue((out_dir / "selection.json").is_file())
            self.assertTrue((out_dir / "g0" / "population.json").is_file())
            self.assertFalse((out_dir / "prompts").exists())
            self.assertTrue((out_dir / "stories" / "I-low.md").is_file())

            self.assertTrue((out_dir / "g0" / "ind-0" / "seed-1" / "layers.jsonl").is_file())
            self.assertFalse((out_dir / "g0" / "ind-1" / "seed-2" / "layers.jsonl").exists())

            self.assertEqual(files, 7)
            self.assertGreater(total, 0)

    def test_raw_prompts_require_explicit_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / 'run/prompts/example.txt', 'Reviewed public prompt')
            files, _ = pack_samples.pack_experiment(root / 'run', root / 'out', include_prompts=True)
            self.assertEqual(files, 1)
            self.assertTrue((root / 'out/prompts/example.txt').is_file())

    def test_private_file_is_rejected_before_existing_output_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / 'run/stories/.env', 'PRIVATE=1')
            _write(root / 'out/keep.txt', 'reviewed output')
            with self.assertRaises(pack_samples.PublicationError):
                pack_samples.pack_experiment(root / 'run', root / 'out')
            self.assertEqual((root / 'out/keep.txt').read_text(), 'reviewed output')

    def test_exemplar_cannot_escape_source_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / 'run/archive.json', json.dumps({'cells': {'I|low': {
                'exemplar': {'layers_path': '../private.jsonl'}}}}))
            _write(root / 'private.jsonl', '{}')
            with self.assertRaises(pack_samples.PublicationError):
                pack_samples.pack_experiment(root / 'run', root / 'out')

    def test_output_cannot_overlap_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / 'run/archive.json', '{}')
            with self.assertRaises(pack_samples.PublicationError):
                pack_samples.pack_experiment(root / 'run', root / 'run')
            self.assertTrue((root / 'run/archive.json').is_file())

    def test_replaces_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "exp1"
            out_dir = root / "samples" / "exp1"

            _write(run_dir / "archive.json", json.dumps({"cells": {}}))
            _write(out_dir / "stale.txt", "old")

            pack_samples.pack_experiment(run_dir, out_dir)

            self.assertFalse((out_dir / "stale.txt").exists())
            self.assertTrue((out_dir / "archive.json").is_file())


if __name__ == "__main__":
    unittest.main()
