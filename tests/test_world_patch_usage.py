"""Unit tests for gapengine/world_patch_usage.py (WB-WORLDGROW-001 段階5a
「パッチの淘汰」): per-patch usage over an experiment's archive.json exemplars,
and the wither-candidate rule (strong use zero, weak-only or none is a
candidate; unreadable logs are undetermined, not a candidate)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from gapengine.world_patch_usage import _cached_counts, load_archive, patch_usage, wither_candidates

_ZERO = dict.fromkeys(
    ("decisions_in_new_zones", "moves_into_new_zones", "gathered_new_items",
     "learned_new_facts", "shared_new_facts", "gave_new_items"), 0)

_MOVE_INTO_BRANCH = {"kind": "decision", "subject": "桃太郎", "verb": "move", "result": "moved",
                     "delta": {"actor": {"zone": "船大工の小屋"}}}
_STAND_ONLY_ROWS = [
    {"kind": "snapshot", "subject": "桃太郎", "layers": {"zone": "船大工の小屋"}},
    {"kind": "decision", "subject": "桃太郎", "verb": "rest", "result": "rested"},
]
_NO_MOVE_ROWS = [{"kind": "decision", "subject": "桃太郎", "verb": "rest", "result": "rested"}]

_ZONE_PATCH = {"id": "p-aaaaaaaa", "add": {"zones": [{"name": "船大工の小屋", "parent": "海"}]}}


def _write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")


def _write_archive(root: Path, cells: dict) -> None:
    (root / "archive.json").write_text(json.dumps({"cells": cells}, ensure_ascii=False), encoding="utf-8")


class PatchUsageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_strong_use_counts_and_is_not_a_wither_candidate(self) -> None:
        _write_jsonl(self.root / "g0/ind-0/seed-1/layers.jsonl", [_MOVE_INTO_BRANCH])
        _write_archive(self.root, {"c0": {"exemplar": {"layers_path": "g0/ind-0/seed-1/layers.jsonl"}}})
        patch = dict(_ZONE_PATCH, id="p-strong01")
        usage = patch_usage(self.root, "桃太郎", [patch])
        self.assertEqual(usage["p-strong01"]["elites_total"], 1)
        self.assertEqual(usage["p-strong01"]["elites_strong"], 1)
        self.assertEqual(usage["p-strong01"]["elites_weak"], 0)
        self.assertEqual(usage["p-strong01"]["cells"]["c0"]["moves_into_new_zones"], 1)
        self.assertEqual(wither_candidates(usage), [])

    def test_weak_only_use_is_a_wither_candidate(self) -> None:
        _write_jsonl(self.root / "g0/ind-0/seed-1/layers.jsonl", _STAND_ONLY_ROWS)
        _write_archive(self.root, {"c0": {"exemplar": {"layers_path": "g0/ind-0/seed-1/layers.jsonl"}}})
        patch = dict(_ZONE_PATCH, id="p-weakonly")
        usage = patch_usage(self.root, "桃太郎", [patch])
        self.assertEqual(usage["p-weakonly"]["elites_strong"], 0)
        self.assertEqual(usage["p-weakonly"]["elites_weak"], 1)
        self.assertEqual(wither_candidates(usage), ["p-weakonly"])

    def test_no_use_at_all_is_a_wither_candidate(self) -> None:
        _write_jsonl(self.root / "g0/ind-0/seed-1/layers.jsonl", _NO_MOVE_ROWS)
        _write_archive(self.root, {"c0": {"exemplar": {"layers_path": "g0/ind-0/seed-1/layers.jsonl"}}})
        patch = dict(_ZONE_PATCH, id="p-nouse000")
        usage = patch_usage(self.root, "桃太郎", [patch])
        self.assertEqual(usage["p-nouse000"], {"elites_total": 1, "elites_strong": 0, "elites_weak": 0,
                                                 "cells": {"c0": dict(_ZERO)}})
        self.assertEqual(wither_candidates(usage), ["p-nouse000"])

    def test_all_logs_unreadable_is_undetermined_not_a_candidate(self) -> None:
        # No layers.jsonl actually written at the referenced path.
        _write_archive(self.root, {"c0": {"exemplar": {"layers_path": "missing/layers.jsonl"}}})
        patch = dict(_ZONE_PATCH, id="p-unread00")
        usage = patch_usage(self.root, "桃太郎", [patch])
        self.assertEqual(usage["p-unread00"]["elites_total"], 0)
        self.assertEqual(wither_candidates(usage), [])

    def test_per_patch_results_differ_on_the_same_log(self) -> None:
        _write_jsonl(self.root / "g0/ind-0/seed-1/layers.jsonl", [_MOVE_INTO_BRANCH])
        _write_archive(self.root, {"c0": {"exemplar": {"layers_path": "g0/ind-0/seed-1/layers.jsonl"}}})
        used = dict(_ZONE_PATCH, id="p-used0001")
        unused = {"id": "p-unused01", "add": {"zones": [{"name": "別の場所", "parent": "海"}]}}
        usage = patch_usage(self.root, "桃太郎", [used, unused])
        self.assertEqual(usage["p-used0001"]["elites_strong"], 1)
        self.assertEqual(usage["p-unused01"]["elites_strong"], 0)
        self.assertEqual(wither_candidates(usage), ["p-unused01"])

    def test_added_name_only_shape_also_works(self) -> None:
        # A frozen world's expansion.patches / an approved patch's own
        # `added` carries names only, no full `add` -- viewer-side callers
        # (run_workspace.py) pass whichever shape they have on hand.
        _write_jsonl(self.root / "g0/ind-0/seed-1/layers.jsonl", [_MOVE_INTO_BRANCH])
        _write_archive(self.root, {"c0": {"exemplar": {"layers_path": "g0/ind-0/seed-1/layers.jsonl"}}})
        patch = {"id": "p-addedfmt", "added": {"zones": ["船大工の小屋"]}}
        usage = patch_usage(self.root, "桃太郎", [patch])
        self.assertEqual(usage["p-addedfmt"]["elites_strong"], 1)

    def test_archive_argument_overrides_reading_archive_json(self) -> None:
        # WB-WORLDGROW-001: a catalog-managed experiment may have no
        # top-level archive.json -- the caller passes one directly.
        _write_jsonl(self.root / "somewhere/layers.jsonl", [_MOVE_INTO_BRANCH])
        archive = {"cells": {"c9": {"exemplar": {"layers_path": "somewhere/layers.jsonl"}}}}
        patch = dict(_ZONE_PATCH, id="p-archarg0")
        usage = patch_usage(self.root, "桃太郎", [patch], archive=archive)
        self.assertEqual(usage["p-archarg0"]["elites_strong"], 1)

    def test_malformed_patch_entries_are_skipped_not_raised(self) -> None:
        _write_jsonl(self.root / "g0/ind-0/seed-1/layers.jsonl", [_MOVE_INTO_BRANCH])
        _write_archive(self.root, {"c0": {"exemplar": {"layers_path": "g0/ind-0/seed-1/layers.jsonl"}}})
        usage = patch_usage(self.root, "桃太郎", [{"title": "no id"}, dict(_ZONE_PATCH, id="p-ok000001")])
        self.assertEqual(list(usage), ["p-ok000001"])

    # -- M1 (Opus review, WB-WORLDGROW-001 段階5a): patch_usage()'s default
    # archive resolution (no `archive=` given) must fall back to a catalog's
    # published/<revision>/archive.json when there is no top-level
    # archive.json -- the only shape a ConfigStore-prepared (screen-run)
    # experiment ever has once the catalog has published it. --------------

    def test_patch_usage_falls_back_to_published_archive_with_no_top_level_file(self) -> None:
        _write_jsonl(self.root / "g0/ind-0/seed-1/layers.jsonl", [_MOVE_INTO_BRANCH])
        published = self.root / "published" / "3"
        published.mkdir(parents=True)
        (published / "archive.json").write_text(
            json.dumps({"cells": {"c0": {"exemplar": {"layers_path": "g0/ind-0/seed-1/layers.jsonl"}}}},
                       ensure_ascii=False), encoding="utf-8")
        (self.root / "published" / "current.json").write_text(
            json.dumps({"schema_version": 1, "run_id": "x", "revision": 3, "manifest_sha256": "irrelevant"},
                       ensure_ascii=False), encoding="utf-8")
        self.assertFalse((self.root / "archive.json").exists())
        patch = dict(_ZONE_PATCH, id="p-published")
        usage = patch_usage(self.root, "桃太郎", [patch])
        self.assertEqual(usage["p-published"]["elites_total"], 1)
        self.assertEqual(usage["p-published"]["elites_strong"], 1)

    def test_load_archive_prefers_top_level_file_when_both_exist(self) -> None:
        (self.root / "archive.json").write_text(json.dumps({"cells": {"top": {}}}), encoding="utf-8")
        published = self.root / "published" / "1"
        published.mkdir(parents=True)
        (published / "archive.json").write_text(json.dumps({"cells": {"pub": {}}}), encoding="utf-8")
        (self.root / "published" / "current.json").write_text(
            json.dumps({"revision": 1}), encoding="utf-8")
        archive, _raw, source = load_archive(self.root)
        self.assertEqual(source, "top-level")
        self.assertEqual(set(archive["cells"]), {"top"})

    def test_load_archive_reads_published_revision_when_no_top_level_file(self) -> None:
        published = self.root / "published" / "5"
        published.mkdir(parents=True)
        (published / "archive.json").write_text(json.dumps({"cells": {"pub": {}}}), encoding="utf-8")
        (self.root / "published" / "current.json").write_text(
            json.dumps({"revision": 5}), encoding="utf-8")
        archive, raw, source = load_archive(self.root)
        self.assertEqual(source, "published/5")
        self.assertEqual(set(archive["cells"]), {"pub"})
        self.assertEqual(json.loads(raw), archive)

    def test_load_archive_raises_when_neither_file_exists(self) -> None:
        with self.assertRaises(OSError):
            load_archive(self.root)

    # -- R1 (Opus review): the per-(log, patch) result must be cached, keyed
    # so different patches on the same log never collide. ------------------

    def test_cache_does_not_confuse_different_patches_on_the_same_log(self) -> None:
        _cached_counts.cache_clear()
        _write_jsonl(self.root / "g0/ind-0/seed-1/layers.jsonl", [_MOVE_INTO_BRANCH])
        _write_archive(self.root, {"c0": {"exemplar": {"layers_path": "g0/ind-0/seed-1/layers.jsonl"}}})
        matching = dict(_ZONE_PATCH, id="p-cache-hit")
        unrelated = {"id": "p-cache-miss", "add": {"zones": [{"name": "別の場所", "parent": "海"}]}}
        # Two *separate* patch_usage() calls (not one call with both patches)
        # so this exercises the cross-call cache, not just per-call logic.
        hit = patch_usage(self.root, "桃太郎", [matching])
        miss = patch_usage(self.root, "桃太郎", [unrelated])
        self.assertEqual(hit["p-cache-hit"]["elites_strong"], 1)
        self.assertEqual(miss["p-cache-miss"]["elites_strong"], 0)
        # Re-run the first patch: still correct after the cache was primed
        # by a different patch on the exact same log file.
        again = patch_usage(self.root, "桃太郎", [matching])
        self.assertEqual(again["p-cache-hit"]["elites_strong"], 1)

    def test_cached_result_is_not_a_shared_mutable_object(self) -> None:
        _cached_counts.cache_clear()
        _write_jsonl(self.root / "g0/ind-0/seed-1/layers.jsonl", [_MOVE_INTO_BRANCH])
        _write_archive(self.root, {"c0": {"exemplar": {"layers_path": "g0/ind-0/seed-1/layers.jsonl"}}})
        patch = dict(_ZONE_PATCH, id="p-nomut0001")
        first = patch_usage(self.root, "桃太郎", [patch])
        first["p-nomut0001"]["cells"]["c0"]["moves_into_new_zones"] = 999
        second = patch_usage(self.root, "桃太郎", [patch])
        self.assertEqual(second["p-nomut0001"]["cells"]["c0"]["moves_into_new_zones"], 1)


if __name__ == "__main__":
    unittest.main()
