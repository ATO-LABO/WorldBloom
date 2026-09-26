"""viewer.library_pages._latest_matching_experiment (WB-WORLDGROW-001 5d-2,
Opus re-review R-a/R-b): only this world's own experiments, newest 20."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from viewer import library_pages


def _run(root, name, project_id, frozen_ids):
    run = root / name
    (run / "inputs" / "projects" / "w").mkdir(parents=True)
    if project_id is not None:
        (run / "config.json").write_text(json.dumps({"project_id": project_id}), encoding="utf-8")
    patches = [{"id": pid} for pid in frozen_ids]
    (run / "inputs" / "projects" / "w" / "world.yaml").write_text(
        json.dumps({"expansion": {"patches": patches}}), encoding="utf-8")
    return run


class LatestMatchingExperimentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state = {"approved": [{"patch": {"id": "p-a"}}],
                      "proposed": [{"patch": {"author": {"backend": "library"}}}]}
        self.repository = mock.Mock()
        self.repository.experiment.side_effect = lambda name: self.root / name

    def latest(self, names):
        with mock.patch.object(library_pages, "_world_experiments", return_value=names), \
             mock.patch.object(library_pages, "_frozen_ids_for",
                               side_effect=lambda root, _pid: {
                                   p["id"] for p in json.loads(
                                       (root / "inputs/projects/w/world.yaml").read_text(encoding="utf-8")
                                   )["expansion"]["patches"]}):
            return library_pages._latest_matching_experiment(
                self.repository, "w", {"name": "W"}, self.root / "projects" / "w", self.state)

    def test_skips_other_worlds_and_unprovable_runs(self):
        _run(self.root, "clone", "w-copy", ["p-a"])      # same display name, other world
        _run(self.root, "legacy", None, ["p-a"])         # no config.json
        _run(self.root, "stale", "w", [])                # this world, old stack
        _run(self.root, "good", "w", ["p-a"])
        self.assertEqual(self.latest(["clone", "legacy", "stale", "good"]), "good")

    def test_only_the_newest_twenty_are_scanned(self):
        names = [f"r{i:02d}" for i in range(21)]
        for name in names[:20]:
            _run(self.root, name, "w", [])
        _run(self.root, names[20], "w", ["p-a"])
        self.assertIsNone(self.latest(names))

    def test_no_library_proposal_means_no_scan(self):
        self.state["proposed"] = []
        self.assertIsNone(self.latest(["anything"]))
        self.repository.experiment.assert_not_called()


if __name__ == "__main__":
    unittest.main()
