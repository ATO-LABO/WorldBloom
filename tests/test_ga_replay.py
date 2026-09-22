"""WB-GAVIZ-001: the GA replay panel.

Covers the pure data functions (gene_origins / classify_outcome /
pick_representatives), the server-rendered panel (render_panel), and one
end-to-end check that a hand-built two-generation published run produces
the expected panel over a real HTTP round trip -- no GA process, no LLM.
"""
from __future__ import annotations

import html as html_module
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest
import unittest.mock
import urllib.error
import urllib.request

from execution.configs import ConfigStore
from execution.provenance import atomic_json, canonical, sha256, write_bytes
from gapengine import lineage
from viewer import ga_replay
from viewer.data import RunRepository
from viewer.server import ViewerServer, ViewerHandler

from test_workbench_pages import FakeJobStore, _job

ROOT = Path(__file__).resolve().parents[1]

CATEGORIES = ("I", "II", "III", "IV", "V", "VI")


def _genome(overrides=None, **scalars):
    category_weight = {c: 0.5 for c in CATEGORIES}
    if overrides:
        category_weight.update(overrides)
    return {
        "category_weight": category_weight,
        "risk_tolerance": scalars.get("risk_tolerance", 0.5),
        "stance_shift_bias": scalars.get("stance_shift_bias", 0.0),
        "novelty_drive": scalars.get("novelty_drive", 0.0),
    }


GENOME_A = _genome()  # every scalar at its default/neutral value
GENOME_B = _genome({"I": 0.9}, risk_tolerance=0.9, stance_shift_bias=0.4, novelty_drive=0.4)


class GeneOriginsTests(unittest.TestCase):
    def test_matches_a_only(self):
        # category I: A=0.5, B=0.9, child=0.5 -> "a"
        child = _genome({"I": 0.5})
        self.assertEqual(ga_replay.gene_origins(child, GENOME_A, GENOME_B)[0], "a")

    def test_matches_b_only(self):
        child = _genome({"I": 0.9})
        self.assertEqual(ga_replay.gene_origins(child, GENOME_A, GENOME_B)[0], "b")

    def test_same_when_both_parents_already_agree(self):
        # category II is 0.5 for both A and B; a child at 0.5 proves nothing.
        child = _genome()
        self.assertEqual(ga_replay.gene_origins(child, GENOME_A, GENOME_B)[1], "same")

    def test_mutation_when_neither_parent_matches(self):
        child = _genome({"I": 0.2})
        self.assertEqual(ga_replay.gene_origins(child, GENOME_A, GENOME_B)[0], "mut")

    def test_one_parent_unresolved(self):
        # A match against the known parent is still "b" -- but a *mismatch*
        # is unknown ("None"), not "mut": it could just as well have come
        # from the other, unresolved parent (WB-GAVIZ-001 review, R2).
        matching = _genome({"I": 0.9})
        self.assertEqual(ga_replay.gene_origins(matching, None, GENOME_B)[0], "b")
        mismatching = _genome({"I": 0.3})
        self.assertIsNone(ga_replay.gene_origins(mismatching, None, GENOME_B)[0])

    def test_both_parents_unresolved(self):
        origins = ga_replay.gene_origins(GENOME_A, None, None)
        self.assertTrue(all(value is None for value in origins))
        self.assertEqual(len(origins), 9)


class ClassifyOutcomeTests(unittest.TestCase):
    def test_new_cell(self):
        individual = {"generation": 1, "parents": ["g0/ind-0", "g0/ind-1"],
                      "genome": GENOME_B, "cell_key": "I|low", "quality": 0.6}
        final_cells = {"I|low": {"generation": 1, "parents": ["g0/ind-0", "g0/ind-1"],
                                  "genome": GENOME_B, "quality": 0.6}}
        self.assertEqual(
            ga_replay.classify_outcome(individual, {}, final_cells),
            {"kind": "new", "quality": 0.6},
        )

    def test_replaced_cell(self):
        individual = {"generation": 1, "parents": ["g0/ind-0", "g0/ind-1"],
                      "genome": GENOME_B, "cell_key": "I|low", "quality": 0.6}
        prev_cells = {"I|low": {"generation": 0, "parents": [], "genome": GENOME_A, "quality": 0.4}}
        final_cells = {"I|low": {"generation": 1, "parents": ["g0/ind-0", "g0/ind-1"],
                                  "genome": GENOME_B, "quality": 0.6}}
        outcome = ga_replay.classify_outcome(individual, prev_cells, final_cells)
        self.assertEqual(outcome, {"kind": "replaced", "quality": 0.6, "prev_quality": 0.4})

    def test_rejected_when_a_sibling_wins_the_same_cell(self):
        # Same generation, same parents -- only the genome tells them apart.
        winner = {"generation": 1, "parents": ["g0/ind-0", "g0/ind-1"], "genome": GENOME_A, "quality": 0.7}
        loser = {"generation": 1, "parents": ["g0/ind-0", "g0/ind-1"],
                 "genome": GENOME_B, "cell_key": "I|low", "quality": 0.3}
        final_cells = {"I|low": winner}
        outcome = ga_replay.classify_outcome(loser, {}, final_cells)
        self.assertEqual(outcome, {"kind": "rejected", "quality": 0.3, "incumbent_quality": 0.7})

    def test_unreached_has_no_cell(self):
        self.assertEqual(
            ga_replay.classify_outcome({"cell_key": None}, {}, {}),
            {"kind": "unreached"},
        )

    def test_kept_when_previous_generation_is_unreadable(self):
        individual = {"generation": 1, "parents": [], "genome": GENOME_A, "cell_key": "I|low", "quality": 0.5}
        final_cells = {"I|low": {"generation": 1, "parents": [], "genome": GENOME_A, "quality": 0.5}}
        outcome = ga_replay.classify_outcome(individual, None, final_cells)
        self.assertEqual(outcome, {"kind": "kept", "quality": 0.5})

    def test_unclassified_when_reached_but_uncategorized(self):
        individual = {"cell_key": None, "classification_status": "unclassified"}
        self.assertEqual(
            ga_replay.classify_outcome(individual, {}, {}),
            {"kind": "unclassified"},
        )

    def test_unreached_when_classification_status_missing_and_no_cell(self):
        # Older runs' results.json has no classification_status field at all
        # -- a missing cell must not be mistaken for "unclassified".
        individual = {"cell_key": None}
        self.assertEqual(ga_replay.classify_outcome(individual, {}, {}), {"kind": "unreached"})


def _rep(index, kind, parents_count=2):
    return {"index": index, "parents": [{"label": f"p{i}"} for i in range(parents_count)],
            "outcome": {"kind": kind}}


class PickRepresentativesTests(unittest.TestCase):
    def test_kind_order_and_two_parent_preference(self):
        individuals = [
            _rep(3, "new", parents_count=0),
            _rep(1, "new", parents_count=2),
            _rep(2, "replaced", parents_count=2),
            _rep(0, "rejected", parents_count=2),
            _rep(4, "unreached", parents_count=0),
        ]
        picked = ga_replay.pick_representatives(individuals, limit=4)
        self.assertEqual([item["index"] for item in picked], [1, 2, 0, 4])

    def test_kept_is_grouped_with_new(self):
        individuals = [_rep(5, "rejected"), _rep(0, "kept")]
        picked = ga_replay.pick_representatives(individuals, limit=4)
        self.assertEqual(picked[0]["index"], 0)

    def test_limit_backfills_by_ascending_index(self):
        individuals = [_rep(i, "unreached", parents_count=0) for i in range(6)]
        picked = ga_replay.pick_representatives(individuals, limit=4)
        self.assertEqual([item["index"] for item in picked], [0, 1, 2, 3])

    def test_deterministic_for_the_same_input(self):
        individuals = [_rep(2, "new"), _rep(0, "rejected"), _rep(1, "unreached")]
        self.assertEqual(
            ga_replay.pick_representatives(individuals),
            ga_replay.pick_representatives(individuals),
        )

    def test_unclassified_is_its_own_tier_after_unreached(self):
        individuals = [_rep(1, "unclassified", parents_count=0), _rep(0, "unreached", parents_count=0)]
        picked = ga_replay.pick_representatives(individuals, limit=2)
        self.assertEqual([item["index"] for item in picked], [0, 1])


class RenderPanelTests(unittest.TestCase):
    def _model(self, **overrides):
        model = {
            "generation": 1, "max_generation": 2,
            "categories": ["I", "II"], "bins": ["low", "mid", "high"],
            "gene_labels": ["gene"] * 9, "gene_short": ["g"] * 9, "gene_ranges": [[0.0, 1.0]] * 9,
            "prev_cells": {"I|low": 0.4}, "final_cells": {"I|low": 0.6},
            "individuals": [
                {"index": 0, "parents": [], "genes": [0.5] * 9, "origins": [None] * 9,
                 "cell_key": "I|low", "outcome": {"kind": "new", "quality": 0.6}},
            ],
            "population": 3,
            "counts": {"new": 1, "replaced": 0, "rejected": 1, "unreached": 1, "unclassified": 0, "kept": 0},
        }
        model.update(overrides)
        return model

    def test_none_model_is_empty_string(self):
        self.assertEqual(ga_replay.render_panel(None, "/jobs/x"), "")

    def test_data_replay_round_trips_as_json(self):
        rendered = ga_replay.render_panel(self._model(), "/jobs/x")
        match = re.search(r'data-replay="([^"]*)"', rendered)
        self.assertIsNotNone(match)
        restored = json.loads(html_module.unescape(match.group(1)))
        self.assertEqual(restored["generation"], 1)
        self.assertEqual(restored["individuals"][0]["cell_key"], "I|low")

    def test_ol_lists_every_representative(self):
        rendered = ga_replay.render_panel(self._model(), "/jobs/x")
        self.assertIn('<ol class="ga-replay-list">', rendered)
        self.assertIn("個体 #0", rendered)

    def test_generation_heading_is_1_based_with_internal_id(self):
        # L1: generation 1 internally (0-based) displays as "第 2 世代（g1）".
        rendered = ga_replay.render_panel(self._model(generation=1), "/jobs/x")
        self.assertIn("第 2 世代（g1）", rendered)
        rendered0 = ga_replay.render_panel(self._model(generation=0, max_generation=0), "/jobs/x")
        self.assertIn("第 1 世代（g0）", rendered0)

    def test_track_has_one_segment_per_generation_with_correct_state(self):
        # P1/P2: the player track replaces the old prev/next nav links --
        # one <a class="ga-replay-gen ..."> per generation 0..max_generation,
        # "is-done" before the current one, "is-current" (+ aria-current) on
        # it, plain after it.
        rendered = ga_replay.render_panel(self._model(generation=1, max_generation=2), "/jobs/x")
        segments = re.findall(r'<a class="([^"]*)" href="([^"]*)"[^>]*?(aria-current="true")?>', rendered)
        self.assertEqual(len(segments), 3)
        classes = [c for c, _, _ in segments]
        self.assertEqual(classes, ["ga-replay-gen is-done", "ga-replay-gen is-current", "ga-replay-gen"])
        hrefs = [h for _, h, _ in segments]
        self.assertEqual(hrefs, ["/jobs/x?gen=0", "/jobs/x?gen=1", "/jobs/x?gen=2"])
        current_markers = [marker for _, _, marker in segments]
        self.assertEqual(current_markers, ["", "aria-current=\"true\"", ""])
        self.assertEqual(rendered.count('aria-current="true"'), 1)

    def test_track_has_a_single_segment_at_generation_zero(self):
        rendered = ga_replay.render_panel(self._model(generation=0, max_generation=0), "/jobs/x")
        segments = re.findall(r'<a class="([^"]*)"', rendered)
        self.assertEqual(segments, ["ga-replay-gen is-current"])
        self.assertNotIn("replay-nav", rendered)

    def test_track_escapes_base_url(self):
        rendered = ga_replay.render_panel(self._model(generation=0, max_generation=0), "/jobs/a&b")
        self.assertIn("/jobs/a&amp;b?gen=0", rendered)
        self.assertNotIn("/jobs/a&b?gen=0", rendered)

    def test_no_inline_script(self):
        rendered = ga_replay.render_panel(self._model(), "/jobs/x")
        self.assertNotIn("<script", rendered)

    def test_summary_says_kept_when_prev_cells_unavailable(self):
        # R4: prev_cells is None -> we cannot claim "new"; say what we know.
        # O1: "replaced" is also unsupported without prev_cells (it is
        # detected solely by comparing against it) -- omit it entirely
        # rather than print an always-0 "入れ替え 0".
        model = self._model(prev_cells=None, individuals=[
            {"index": 0, "parents": [], "genes": [0.5] * 9, "origins": [None] * 9,
             "cell_key": "I|low", "outcome": {"kind": "kept", "quality": 0.6}},
        ], counts={
            "new": 0, "replaced": 0, "rejected": 0, "unreached": 0, "unclassified": 0, "kept": 2,
        })
        rendered = ga_replay.render_panel(model, "/jobs/x")
        summary = re.search(r'<p class="replay-summary">([^<]*)</p>', rendered).group(1)
        self.assertIn("地図に残った 2", summary)
        self.assertNotIn("発見", summary)
        self.assertNotIn("入れ替え", summary)

    def test_summary_includes_replaced_when_prev_cells_known(self):
        rendered = ga_replay.render_panel(self._model(), "/jobs/x")
        summary = re.search(r'<p class="replay-summary">([^<]*)</p>', rendered).group(1)
        self.assertIn("入れ替え 0", summary)

    def test_summary_shows_unclassified_only_when_positive(self):
        zero = ga_replay.render_panel(self._model(), "/jobs/x")
        self.assertNotIn("分類不能", zero)
        positive = ga_replay.render_panel(self._model(counts={
            "new": 1, "replaced": 0, "rejected": 1, "unreached": 1, "unclassified": 2, "kept": 0,
        }), "/jobs/x")
        self.assertIn("分類不能 2", positive)

    def test_list_item_reports_shared_parent_truthfully(self):
        # L3/O6: the same ref chosen for both parent slots must say so,
        # instead of silently printing "X × X" -- but the note must not
        # erase which parent it was.
        individual = {
            "index": 5,
            "parents": [
                {"label": "g1/ind-6", "display": "第 2 世代の個体 #6"},
                {"label": "g1/ind-6", "display": "第 2 世代の個体 #6"},
            ],
            "outcome": {"kind": "unreached"},
        }
        item = ga_replay._replay_list_item(individual)
        self.assertIn(ga_replay._SAME_PARENT_NOTE, item)
        self.assertIn("第 2 世代の個体 #6", item)
        self.assertNotIn("×", item)

    def test_list_item_handles_a_single_parent_without_raising(self):
        # O2: a 1-parent entry must not IndexError on parents[1].
        individual = {
            "index": 2,
            "parents": [{"label": "g0/ind-1", "display": "第 1 世代の個体 #1"}],
            "outcome": {"kind": "unreached"},
        }
        item = ga_replay._replay_list_item(individual)
        self.assertIn("親: 第 1 世代の個体 #1", item)

    def test_list_item_uses_parent_display_not_raw_label(self):
        individual = {
            "index": 1,
            "parents": [
                {"label": "g1/ind-6", "display": "第 2 世代の個体 #6"},
                {"label": "g0/archive/III-high", "display": "地図の III × high（第 1 世代に入った物語）"},
            ],
            "outcome": {"kind": "unreached"},
        }
        item = ga_replay._replay_list_item(individual)
        self.assertIn("第 2 世代の個体 #6", item)
        self.assertIn("地図の III × high（第 1 世代に入った物語）", item)
        self.assertNotIn("g1/ind-6", item)

    def test_quality_formatted_to_3_decimals(self):
        # L4: 0.4258 -> 0.4286 must read as distinct 3-decimal numbers, not
        # both rounding down to the same 2-decimal figure.
        outcome = {"kind": "replaced", "prev_quality": 0.4258, "quality": 0.4286}
        sentence = ga_replay._outcome_sentence(outcome)
        self.assertIn("0.426", sentence)
        self.assertIn("0.429", sentence)


class ParentDisplayTests(unittest.TestCase):
    def test_individual_ref(self):
        self.assertEqual(ga_replay._parent_display("g3/ind-5"), "第 4 世代の個体 #5")

    def test_archive_ref_splits_on_last_dash(self):
        self.assertEqual(
            ga_replay._parent_display("g2/archive/III-high"),
            "地図の III × high（第 3 世代に入った物語）",
        )

    def test_unknown_format_falls_back_to_raw_label(self):
        self.assertEqual(ga_replay._parent_display("weird-ref"), "weird-ref")


# --------------------------------------------------------- HTTP round trip

def _publish_revision(root, run_id, revision, cells):
    payloads = {
        "archive": {"cells": cells},
        "summary": {},
        "candidates": {"schema_version": 1, "run_id": run_id, "revision": revision, "candidates": []},
    }
    folder = root / "published" / str(revision)
    folder.mkdir(parents=True)
    files = {}
    for name, value in payloads.items():
        raw = canonical(value)
        write_bytes(folder / f"{name}.json", raw)
        files[name] = {"path": f"published/{revision}/{name}.json", "sha256": sha256(raw)}
    manifest = {"schema_version": 1, "run_id": run_id, "revision": revision,
                "completed_generations": revision, "files": files}
    manifest_bytes = canonical(manifest)
    write_bytes(folder / "manifest.json", manifest_bytes)
    return manifest_bytes


def _hand_published_replay_run(runs_root, run_id, config_id, *, padding=0):
    """A genuine (non-legacy) two-generation run: g0/g1 results.json on disk
    plus two published revisions, built the same way
    test_workbench_pages.py::_hand_published_run builds a one-revision run.

    Generation 0: ind-0 reaches (I|low, q=0.5); ind-1 does not reach.
    Generation 1 (parents = g0/ind-0 x g0/ind-1 unless noted): ind-0 improves
    I|low (q=0.7, replacing ind-0's g0 elite); ind-1 opens a new cell II|mid
    (q=0.6); ind-2 also reaches I|low (q=0.3) but loses it to its sibling
    ind-0 (same generation/parents, different genome); ind-3 has no parents
    and does not reach. That covers all 4 outcome kinds plus the 0-parent
    and "same"/"a"/"b"/"mut" gene-origin cases in one fixture.

    `padding` adds that many more 2-parent "rejected" siblings after ind-3
    (same cell as ind-2, always worse, never picked as a representative) --
    used to make a resolve_ref call count that scales with population
    instead of with representatives actually fail (WB-GAVIZ-001 review, R1).
    """
    root = runs_root / run_id
    root.mkdir(parents=True)
    manifest = {"schema_version": 1, "run_id": run_id, "config_id": config_id,
                "evolution": {}, "target_endings": []}
    manifest_bytes = canonical(manifest)
    write_bytes(root / "manifest.json", manifest_bytes)
    atomic_json(root / "complete.json", {"schema_version": 1, "manifest_sha256": sha256(manifest_bytes)})

    def result(index, genome, parents, cell, quality):
        reached = quality is not None
        return {"index": index, "genome": genome, "parents": parents,
                "cell": list(cell) if cell else None,
                "classification_status": "classified" if cell else "not_reached",
                "shaped": quality or 0.0, "reach_rate": 1.0 if reached else 0.0,
                "runs": [{"reached": reached, "quality": quality or 0.0, "seed": 0}]}

    g0 = [
        result(0, GENOME_A, [], ("I", "low"), 0.5),
        result(1, GENOME_B, [], None, None),
    ]
    parents_01 = ["g0/ind-0", "g0/ind-1"]
    child0 = _genome({"I": 0.9}, risk_tolerance=0.9, stance_shift_bias=0.0, novelty_drive=0.2)
    child1 = _genome({"II": 0.8}, risk_tolerance=0.4, stance_shift_bias=-0.5, novelty_drive=0.1)
    child2 = _genome({"I": 0.9}, risk_tolerance=0.5, stance_shift_bias=0.0, novelty_drive=0.2)
    child3 = _genome(novelty_drive=0.9)
    g1 = [
        result(0, child0, parents_01, ("I", "low"), 0.7),
        result(1, child1, parents_01, ("II", "mid"), 0.6),
        result(2, child2, parents_01, ("I", "low"), 0.3),
        result(3, child3, [], None, None),
    ] + [
        result(4 + offset, child2, parents_01, ("I", "low"), 0.05)
        for offset in range(padding)
    ]
    (root / "g0").mkdir()
    (root / "g0" / "results.json").write_text(json.dumps(g0, ensure_ascii=False), encoding="utf-8")
    (root / "g1").mkdir()
    (root / "g1" / "results.json").write_text(json.dumps(g1, ensure_ascii=False), encoding="utf-8")

    _publish_revision(root, run_id, 1, {
        "I|low": {"generation": 0, "quality": 0.5, "genome": GENOME_A, "parents": []},
    })
    manifest2_bytes = _publish_revision(root, run_id, 2, {
        "I|low": {"generation": 1, "quality": 0.7, "genome": child0, "parents": parents_01},
        "II|mid": {"generation": 1, "quality": 0.6, "genome": child1, "parents": parents_01},
    })
    atomic_json(root / "published" / "current.json", {
        "schema_version": 1, "run_id": run_id, "revision": 2,
        "manifest_sha256": sha256(manifest2_bytes),
    })
    return root


class ReplayHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wb-ga-replay-")
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.runs = base / "runs"
        self.runs.mkdir()
        control = base / "control"
        configs = ConfigStore(ROOT, control, self.runs)
        configs.save({
            "label": "replay", "project_id": "romance", "template_id": "romance",
            "evolution": {"generations": 2, "population": 4, "seeds": 1},
        }, config_id="cfg-replay")
        self.fake = FakeJobStore(configs)

        self.run_id = "run-ga-replay"
        _hand_published_replay_run(self.runs, self.run_id, "cfg-replay")
        self.fake.add(_job("job-replay", self.run_id, "succeeded", config_id="cfg-replay",
                            publication_revision=2))

        self.server = ViewerServer(("127.0.0.1", 0), ViewerHandler)
        self.server.repository = RunRepository(self.runs, control_root=control, jobs=self.fake)
        self.server.job_store = self.fake
        self.server.settings_path = base / "settings.json"
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self, path):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.server.server_port}{path}", timeout=5) as response:
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8")

    def test_latest_generation_panel_over_http(self):
        status, body = self.get(f"/jobs/job-replay")
        self.assertEqual(status, 200, body)
        self.assertIn('data-vessel="replay"', body)
        # L1: internal generation 1 (revision 2 - 1) displays 1-based, with
        # the internal id kept alongside for anyone cross-referencing it.
        self.assertIn("第 2 世代（g1）", body)
        for index in range(4):
            self.assertIn(f"個体 #{index}", body)
        self.assertIn("発見 1・入れ替え 1・残らず 1・未到達 1", body)

    def test_gen_query_selects_older_generation(self):
        status, body = self.get("/jobs/job-replay?gen=0")
        self.assertEqual(status, 200, body)
        self.assertIn("第 1 世代（g0）", body)
        self.assertIn("親なしの新顔", body)


class ReplayModelDirectTests(unittest.TestCase):
    """Exercises replay_model() directly (no HTTP) against the same
    hand-built fixture, for assertions that need to inspect the model or
    count internal calls rather than just read rendered HTML."""

    class _Handler:
        def __init__(self, repository):
            self.repository = repository

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wb-ga-replay-direct-")
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.runs = base / "runs"
        self.runs.mkdir()
        self.repository = RunRepository(self.runs, control_root=base / "control")
        self.axes = (["I", "II", "III"], ["low", "mid", "high"])

    def _job(self, run_id, revision):
        return {"run_id": run_id, "publication_revision": revision}

    def test_parent_resolution_is_limited_to_representatives(self):
        # R1: population 14 (4 distinct outcomes + 10 padding "rejected"
        # siblings that are never chosen); resolve_ref must only run for the
        # <=4 representatives actually picked, not for all 14 individuals.
        run_id = "run-ga-replay-padded"
        _hand_published_replay_run(self.runs, run_id, "cfg-padded", padding=10)
        handler = self._Handler(self.repository)
        job = self._job(run_id, 2)

        calls = []
        original = lineage.resolve_ref

        def counting(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)

        with unittest.mock.patch.object(lineage, "resolve_ref", side_effect=counting):
            model = ga_replay.replay_model(handler, job, self.axes)

        self.assertIsNotNone(model)
        self.assertEqual(model["population"], 14)
        self.assertLessEqual(len(model["individuals"]), 4)
        self.assertLessEqual(len(calls), 2 * len(model["individuals"]))
        self.assertGreater(len(calls), 0)  # sanity: representatives *did* get resolved

    def test_empty_generation_returns_none(self):
        # R8: an empty results.json (a generation with no individuals at
        # all, e.g. a truncated write) must degrade to "nothing to replay",
        # not crash the run page.
        run_id = "run-ga-replay-empty"
        root = self.runs / run_id
        root.mkdir(parents=True)
        manifest = {"schema_version": 1, "run_id": run_id, "config_id": "cfg-empty",
                    "evolution": {}, "target_endings": []}
        manifest_bytes = canonical(manifest)
        write_bytes(root / "manifest.json", manifest_bytes)
        atomic_json(root / "complete.json", {"schema_version": 1, "manifest_sha256": sha256(manifest_bytes)})
        (root / "g0").mkdir()
        (root / "g0" / "results.json").write_text("[]", encoding="utf-8")
        manifest1_bytes = _publish_revision(root, run_id, 1, {})
        atomic_json(root / "published" / "current.json", {
            "schema_version": 1, "run_id": run_id, "revision": 1,
            "manifest_sha256": sha256(manifest1_bytes),
        })
        handler = self._Handler(self.repository)
        model = ga_replay.replay_model(handler, self._job(run_id, 1), self.axes)
        self.assertIsNone(model)


if __name__ == "__main__":
    unittest.main()
