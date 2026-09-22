"""Tests for the WB-GAVIZ-002 lineage river."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

from gapengine import lineage
from gapengine.evolve import evolve
from viewer import data, lineage_river, server, workbench_pages


def _result(index, *, cell=None, quality=None, parents=None, status=None):
    reached = quality is not None
    return {
        "index": index,
        "genome": {},
        "parents": list(parents or []),
        "cell": list(cell) if cell else None,
        "classification_status": status,
        "runs": [{
            "quality": quality or 0.0,
            "reached": reached,
            "seed": 1,
        }],
    }


def _momotaro_paths():
    root = Path(__file__).resolve().parents[1]
    return root / "projects" / "momotaro", root / "templates" / "momotaro"


class RiverPureFunctionTests(unittest.TestCase):
    def test_resolve_parents_handles_refs_ties_hyphens_unresolved_and_duplicates(self):
        generations = [
            [
                _result(0, cell=("arc-with-dash", "high"), quality=0.7),
                _result(1, cell=("arc-with-dash", "high"), quality=0.7),
            ],
            [
                _result(0, cell=("arc-with-dash", "high"), quality=0.7),
                _result(2, parents=[
                    "g0/archive/arc-with-dash-high",
                    "g0/archive/arc-with-dash-high",
                ]),
                _result(3, parents=["g0/ind-1", "bad-ref"]),
            ],
        ]
        index = lineage_river.build_index(generations)
        edges, unresolved = lineage_river.resolve_parents(index, 2)

        duplicate = next(edge for edge in edges if edge["child"] == (1, 2))
        self.assertEqual(duplicate["parent"], (0, 0))
        self.assertTrue(duplicate["twice"])
        individual = next(edge for edge in edges if edge["child"] == (1, 3))
        self.assertEqual(individual["parent"], (0, 1))
        self.assertEqual(unresolved, 1)

    def test_survivors_walks_both_parents_and_merges_elite_cells(self):
        index = {
            (0, 0): {},
            (0, 1): {},
            (1, 0): {},
            (1, 1): {},
            (2, 0): {},
        }
        edges = [
            {"parent": (0, 0), "child": (1, 0)},
            {"parent": (0, 1), "child": (1, 0)},
            {"parent": (1, 0), "child": (2, 0)},
            {"parent": (0, 0), "child": (1, 1)},
        ]
        result = lineage_river.survivors(
            index,
            edges,
            {"I|low": (2, 0), "II|high": (1, 1)},
        )
        self.assertEqual(result[(0, 0)], {"I|low", "II|high"})
        self.assertEqual(result[(0, 1)], {"I|low"})
        self.assertEqual(result[(1, 0)], {"I|low"})

    def test_layout_is_deterministic_stacks_points_and_caps_bars(self):
        index = {
            (0, individual): {
                "cell_key": "I|low" if individual < 2 else None,
            }
            for individual in range(67)
        }
        first = lineage_river.layout(index, [], {}, ["I|low"])
        second = lineage_river.layout(index, [], {}, ["I|low"])
        self.assertEqual(first, second)
        self.assertNotEqual(first["positions"][(0, 0)]["y"], first["positions"][(0, 1)]["y"])
        self.assertEqual(first["bars"][0]["count"], 65)
        self.assertEqual(first["bars"][0]["height"], 60)
        self.assertEqual(first["bands"][0]["height"], 16.0)

    def test_render_is_static_selectable_counted_and_escaped(self):
        generations = [
            [_result(0, cell=("A<", "high"), quality=0.4), _result(1, quality=None)],
            [
                _result(0, cell=("A<", "high"), quality=0.8, parents=["g0/ind-0"]),
                _result(1, cell=("B", "low"), quality=0.5, parents=["g0/ind-1"]),
            ],
        ]
        index = lineage_river.build_index(generations)
        edges, unresolved = lineage_river.resolve_parents(index, 2)
        elite_nodes = {"A<|high": (1, 0), "B|low": (1, 1)}
        survivor_map = lineage_river.survivors(index, edges, elite_nodes)
        model = {
            "index": index,
            "edges": edges,
            "survivor_map": survivor_map,
            "elite_nodes": elite_nodes,
            "elite_quality": {"A<|high": 0.8, "B|low": 0.5},
            "selected_cell": "A<|high",
            "layout": lineage_river.layout(index, edges, survivor_map, ["A<|high", "B|low"]),
            "counts": {
                "total": 4,
                "generations": 2,
                "population": 2,
                "survivors": len(survivor_map),
                "parentless": 2,
                "offmap": 1,
                "unresolved": unresolved,
                "elites": 2,
            },
        }
        rendered = lineage_river.render_river(model, "/exp/demo/river")
        self.assertNotIn("<script", rendered)
        self.assertIn("2 世代 × 2 体 = 4 体", rendered)
        self.assertIn('href="/exp/demo/river?cell=B%7Clow"', rendered)
        self.assertIn('class="river-edge is-alive is-selected"', rendered)
        self.assertIn('class="river-edge is-alive"', rendered)
        self.assertIn("A&lt; × high", rendered)
        self.assertNotIn("A< × high", rendered)

    def test_workbench_exit_keeps_candidate_and_river_links_together(self):
        rendered = workbench_pages._run_vessel_exits({
            "job": {"state": "succeeded", "run_id": "run-1"},
            "run_name": "exp-1",
            "live": {},
        })
        actions = rendered.split('<p class="actions">', 1)[1].split("</p>", 1)[0]
        self.assertIn('href="/runs/run-1/candidates"', actions)
        self.assertIn('href="/exp/exp-1/river"', actions)


class RiverRealRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project, template = _momotaro_paths()
        cls.temporary = tempfile.TemporaryDirectory()
        cls.runs_root = Path(cls.temporary.name) / "runs"
        evolve({
            "ga_seed": 1,
            "generations": 5,
            "keep": "all",
            "population": 8,
            "project": project,
            "seed_base": 11,
            "seeds": 2,
            "template": template,
            "out": cls.runs_root / "exp1",
            "processes": 1,
            "record_explanations": False,
        })
        legacy = cls.runs_root / "legacy"
        legacy.mkdir(parents=True)
        (legacy / "archive.json").write_text(
            json.dumps({"cells": {}}, ensure_ascii=False),
            encoding="utf-8",
        )
        cls.repository = data.RunRepository(cls.runs_root)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_bulk_archive_resolution_matches_lineage_resolve_ref(self):
        experiment = self.repository.experiment("exp1")
        generations = [
            lineage._generation_results(self.repository, experiment, generation)
            for generation in range(5)
        ]
        index = lineage_river.build_index(generations)
        edges, unresolved = lineage_river.resolve_parents(index, 5)
        actual = defaultdict(set)
        for edge in edges:
            actual[edge["child"]].add(edge["parent"])
        for node_key, node in index.items():
            for ref in node["parent_refs"]:
                expected = lineage.resolve_ref(self.repository, experiment, ref)
                expected_key = (expected["generation"], expected["index"])
                self.assertIn(expected_key, actual[node_key], ref)
        self.assertEqual(unresolved, 0)

    def test_model_page_http_and_missing_results_contract(self):
        model = lineage_river.river_model(self.repository, "exp1")
        self.assertEqual(model["counts"]["total"], 40)
        self.assertEqual(model["counts"]["generations"], 5)
        rendered = lineage_river.river_page(self.repository, "exp1")
        self.assertIn('class="river-svg"', rendered)
        self.assertIn("系譜の川", rendered)
        with self.assertRaises(data.MissingResource):
            lineage_river.river_model(self.repository, "legacy")

        httpd = server.ViewerServer(("127.0.0.1", 0), server.ViewerHandler)
        httpd.repository = self.repository
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            with urllib.request.urlopen(base_url + "/exp/exp1/river", timeout=10) as response:
                self.assertEqual(response.status, 200)
                self.assertIn("系譜の川", response.read().decode("utf-8"))
            with self.assertRaises(urllib.error.HTTPError) as unknown:
                urllib.request.urlopen(base_url + "/exp/exp1/river?cell=none", timeout=10)
            self.assertEqual(unknown.exception.code, 404)
            with self.assertRaises(urllib.error.HTTPError) as legacy:
                urllib.request.urlopen(base_url + "/exp/legacy/river", timeout=10)
            self.assertEqual(legacy.exception.code, 404)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
