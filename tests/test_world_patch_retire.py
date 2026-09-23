"""Unit tests for the WB-WORLDGROW-001 段階5a "パッチの淘汰" tombstone-revision
mechanism: gapengine.world_patch.verify_stack/retired_patches with `kind:
"retire"` revisions, and execution.world_patch_approval.retire()/reopen().

Fixtures follow tests/test_world_patch.py's shape: a tempdir copy of the real
momotaro project/template (so retire()'s own materialize() re-validation has
a real world/subjects to work against), with write_approved() (tests/
world_patch_fixtures.py) synthesizing already-approved revisions without
needing a real trial/gate."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from world_patch_fixtures import write_approved

from execution.world_patch_approval import StalePatch, reopen, retire
from gapengine.world_patch import PatchError, approved_patches, read_stack, retired_patches, verify_stack

ROOT = Path(__file__).resolve().parents[1]
MOMOTARO_PROJECT = ROOT / "projects" / "momotaro"
MOMOTARO_TEMPLATE = ROOT / "templates" / "momotaro"

ADD_A = {
    "zones": [{"name": "船大工の小屋", "parent": "海", "note": "船具を扱う小屋"}],
    "items": [{"name": "古びた帆布", "sources": [
        {"type": "investigate", "zone": "船大工の小屋", "count": 1, "max": 2}]}],
    "facts": [],
}
ADD_B = {
    "zones": [{"name": "山道の祠", "parent": "道中", "note": "小さな祠"}],
    "items": [{"name": "祠のお守り", "sources": [
        {"type": "investigate", "zone": "山道の祠", "count": 1, "max": 2}]}],
    "facts": [],
}
# Depends on ADD_A having already added 船大工の小屋 -- its craft_zone only
# resolves once that zone exists.
ADD_C_DEPENDS_ON_A = {
    "zones": [],
    "items": [{"name": "小屋の道具", "sources": [
        {"type": "investigate", "zone": "海", "count": 1, "max": 1}], "craft_zone": "船大工の小屋"}],
    "facts": [],
}


class RetireTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="wb-world-patch-retire-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "projects" / "momotaro"
        self.template = self.root / "templates" / "momotaro"
        shutil.copytree(MOMOTARO_PROJECT, self.project, ignore=shutil.ignore_patterns("patches"))
        shutil.copytree(MOMOTARO_TEMPLATE, self.template)
        # Deliberately does not exist -- retire()'s archive_sha256 must
        # degrade to None (OSError caught) instead of refusing.
        self.missing_experiment = self.root / "runs" / "no-such-experiment"

    def approve(self, add: dict, title: str) -> str:
        patch = write_approved(self.project, {"title": title, "add": add}, template=self.template)
        return patch["id"]


class VerifyStackAndRetiredPatchesTests(RetireTestBase):
    def test_retire_moves_head_and_drops_patch_from_active(self) -> None:
        pid = self.approve(ADD_A, "小屋を足す")
        head_before = read_stack(self.project)["head"]
        revision = retire(self.project, self.template, pid, "使われていないので枯らします",
                          experiment=self.missing_experiment)
        self.assertEqual(revision["kind"], "retire")
        self.assertEqual(revision["patch_id"], pid)
        self.assertNotEqual(read_stack(self.project)["head"], head_before)
        self.assertEqual(approved_patches(self.project), [])
        self.assertEqual(verify_stack(self.project), [])
        # Tombstone, not deletion: yaml/gate/retire.json all remain.
        folder = self.project / "patches"
        self.assertTrue((folder / f"{pid}.yaml").is_file())
        self.assertTrue((folder / f"{pid}.gate.json").is_file())
        self.assertTrue((folder / f"{pid}.retire.json").is_file())
        retire_record = json.loads((folder / f"{pid}.retire.json").read_text(encoding="utf-8"))
        self.assertEqual(retire_record["patch_id"], pid)
        self.assertEqual(retire_record["reason"], "使われていないので枯らします")
        self.assertIsNone(retire_record["archive_sha256"])
        self.assertEqual(retire_record["experiment"], self.missing_experiment.name)

    def test_retire_writes_usage_evidence_verbatim(self) -> None:
        pid = self.approve(ADD_A, "小屋を足す")
        usage = {"elites_total": 3, "elites_strong": 0, "elites_weak": 1, "cells": {}}
        retire(self.project, self.template, pid, "使用表つきで枯らします",
              experiment=self.missing_experiment, usage=usage)
        record = json.loads((self.project / "patches" / f"{pid}.retire.json").read_text(encoding="utf-8"))
        self.assertEqual(record["usage"], usage)

    def test_double_retire_is_rejected(self) -> None:
        pid = self.approve(ADD_A, "小屋を足す")
        retire(self.project, self.template, pid, "一度目の淘汰理由です", experiment=self.missing_experiment)
        with self.assertRaises(PatchError):
            retire(self.project, self.template, pid, "二度目の淘汰理由です", experiment=self.missing_experiment)

    def test_retire_unknown_patch_is_rejected(self) -> None:
        with self.assertRaises(PatchError):
            retire(self.project, self.template, "p-00000000", "存在しないパッチを枯らそうとする試験",
                  experiment=self.missing_experiment)

    def test_retire_short_reason_is_rejected(self) -> None:
        pid = self.approve(ADD_A, "小屋を足す")
        with self.assertRaises(PatchError):
            retire(self.project, self.template, pid, "短い", experiment=self.missing_experiment)

    def test_retire_expect_head_mismatch_raises_stalepatch(self) -> None:
        pid = self.approve(ADD_A, "小屋を足す")
        with self.assertRaises(StalePatch):
            retire(self.project, self.template, pid, "古い head を渡す試験",
                  experiment=self.missing_experiment, expect_head="not-the-real-head")

    def test_dependent_patch_blocks_retire_and_writes_nothing(self) -> None:
        a_id = self.approve(ADD_A, "小屋を足す")
        c_id = self.approve(ADD_C_DEPENDS_ON_A, "小屋の道具を足す")
        stack_before = json.loads((self.project / "patches" / "stack.json").read_text(encoding="utf-8"))
        with self.assertRaisesRegex(PatchError, c_id):
            retire(self.project, self.template, a_id, "依存されているのに枯らそうとする試験",
                  experiment=self.missing_experiment)
        # Nothing written: no retire.json, stack.json byte-identical, both
        # patches still active.
        self.assertFalse((self.project / "patches" / f"{a_id}.retire.json").exists())
        stack_after = json.loads((self.project / "patches" / "stack.json").read_text(encoding="utf-8"))
        self.assertEqual(stack_before, stack_after)
        self.assertEqual({p["id"] for p, _ in verify_stack(self.project)}, {a_id, c_id})

    def test_independent_patch_does_not_block_retire(self) -> None:
        a_id = self.approve(ADD_A, "小屋を足す")
        b_id = self.approve(ADD_B, "祠を足す")
        retire(self.project, self.template, a_id, "無関係なので単独で枯らせます",
              experiment=self.missing_experiment)
        self.assertEqual({p["id"] for p, _ in verify_stack(self.project)}, {b_id})

    def test_retire_then_new_approval_continues_the_chain(self) -> None:
        a_id = self.approve(ADD_A, "小屋を足す")
        retire(self.project, self.template, a_id, "先に枯らしてから次を承認します",
              experiment=self.missing_experiment)
        b_id = self.approve(ADD_B, "祠を足す")
        active = [p["id"] for p, _ in verify_stack(self.project)]
        self.assertEqual(active, [b_id])
        retired = retired_patches(self.project)
        self.assertEqual([entry["patch"]["id"] for entry in retired], [a_id])
        # rev/parent_digest chain: verify_stack already re-derives and
        # checks this internally (raises PatchError if broken); a second,
        # explicit head check that reading doesn't silently short-circuit.
        stack = read_stack(self.project)
        self.assertEqual(len(stack["revisions"]), 3)  # approve A, retire A, approve B
        self.assertEqual(stack["revisions"][-1]["digest"], stack["head"])

    def test_retired_patches_returns_content_in_retire_order(self) -> None:
        a_id = self.approve(ADD_A, "小屋を足す")
        b_id = self.approve(ADD_B, "祠を足す")
        retire(self.project, self.template, a_id, "先にAを枯らす理由です", experiment=self.missing_experiment)
        retire(self.project, self.template, b_id, "次にBを枯らす理由です", experiment=self.missing_experiment)
        retired = retired_patches(self.project)
        self.assertEqual([entry["patch"]["id"] for entry in retired], [a_id, b_id])
        self.assertEqual(retired[0]["retire"]["reason"], "先にAを枯らす理由です")
        self.assertEqual(retired[1]["retire"]["reason"], "次にBを枯らす理由です")
        self.assertEqual(verify_stack(self.project), [])

    def test_legacy_stack_without_kind_field_still_verifies(self) -> None:
        # write_approved() never sets "kind" -- documents that a pre-段階5a
        # manifest (every revision a bare approval) is unaffected.
        self.approve(ADD_A, "小屋を足す")
        stack = json.loads((self.project / "patches" / "stack.json").read_text(encoding="utf-8"))
        self.assertNotIn("kind", stack["revisions"][0])
        self.assertEqual(len(verify_stack(self.project)), 1)

    def test_tampered_retire_json_is_rejected(self) -> None:
        pid = self.approve(ADD_A, "小屋を足す")
        retire(self.project, self.template, pid, "改竄検査用に枯らします", experiment=self.missing_experiment)
        retire_path = self.project / "patches" / f"{pid}.retire.json"
        retire_path.write_bytes(retire_path.read_bytes() + b" ")
        with self.assertRaises(PatchError):
            verify_stack(self.project)

    def test_retire_revision_rev_gap_is_rejected(self) -> None:
        pid = self.approve(ADD_A, "小屋を足す")
        retire(self.project, self.template, pid, "rev改竄検査用に枯らします", experiment=self.missing_experiment)
        stack_path = self.project / "patches" / "stack.json"
        stack = json.loads(stack_path.read_text(encoding="utf-8"))
        stack["revisions"][-1]["rev"] = 99
        stack_path.write_text(json.dumps(stack, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(PatchError):
            verify_stack(self.project)

    def test_retire_revision_parent_digest_mismatch_is_rejected(self) -> None:
        pid = self.approve(ADD_A, "小屋を足す")
        retire(self.project, self.template, pid, "parent_digest改竄検査用です", experiment=self.missing_experiment)
        stack_path = self.project / "patches" / "stack.json"
        stack = json.loads(stack_path.read_text(encoding="utf-8"))
        stack["revisions"][-1]["parent_digest"] = "0" * 64
        stack_path.write_text(json.dumps(stack, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(PatchError):
            verify_stack(self.project)

    def test_orphan_retire_json_from_interrupted_retire_is_overwritten(self) -> None:
        # R2 (Opus review): retire() writes <id>.retire.json before appending
        # the stack revision -- a crash in between leaves an orphan
        # retire.json for a patch verify_stack() still counts as active.
        # Since we've just re-verified the target is active inside the lock,
        # any file already at that path cannot be a real tombstone -- it
        # must be overwritten, not refused forever.
        pid = self.approve(ADD_A, "小屋を足す")
        orphan_path = self.project / "patches" / f"{pid}.retire.json"
        orphan_path.write_text(json.dumps({
            "patch_id": pid, "reason": "中断した淘汰の残骸です", "retired_at": "orphan",
            "parent_digest": "stale-digest-from-a-crashed-attempt", "experiment": "stale-exp",
            "usage": None, "rules_version": 0, "archive_sha256": None, "archive_source": None,
        }, ensure_ascii=False), encoding="utf-8")
        revision = retire(self.project, self.template, pid, "孤立したretire.jsonを上書きします",
                          experiment=self.missing_experiment)
        self.assertEqual(revision["kind"], "retire")
        self.assertEqual(approved_patches(self.project), [])
        record = json.loads(orphan_path.read_text(encoding="utf-8"))
        self.assertEqual(record["reason"], "孤立したretire.jsonを上書きします")
        self.assertNotEqual(record["parent_digest"], "stale-digest-from-a-crashed-attempt")
        retired = retired_patches(self.project)
        self.assertEqual(len(retired), 1)
        self.assertEqual(retired[0]["patch"]["id"], pid)
        self.assertEqual(retired[0]["retire"]["reason"], "孤立したretire.jsonを上書きします")

    def test_second_retire_of_same_patch_id_in_manifest_is_rejected(self) -> None:
        # A hand-crafted manifest with two retire revisions for the same
        # patch_id (the second one's target is no longer active) --
        # verify_stack must reject it even though each revision's own
        # hash chain is internally consistent.
        pid = self.approve(ADD_A, "小屋を足す")
        retire(self.project, self.template, pid, "一度目の淘汰理由です", experiment=self.missing_experiment)
        stack_path = self.project / "patches" / "stack.json"
        stack = json.loads(stack_path.read_text(encoding="utf-8"))
        # Duplicate the retire revision verbatim (same target, already gone).
        stack["revisions"].append(dict(stack["revisions"][-1], rev=len(stack["revisions"]) + 1))
        stack_path.write_text(json.dumps(stack, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(PatchError):
            verify_stack(self.project)


class ReopenWithRetiredPatchesTests(RetireTestBase):
    def test_reopen_sends_retired_to_retired_dir_and_active_to_proposed(self) -> None:
        a_id = self.approve(ADD_A, "小屋を足す")
        retire(self.project, self.template, a_id, "先に枯らしてからreopenします",
              experiment=self.missing_experiment)
        b_id = self.approve(ADD_B, "祠を足す")
        reopened = reopen(self.project)
        self.assertEqual(reopened, [b_id])
        folder = self.project / "patches"
        # Active (B): moved to _proposed/, not left behind.
        self.assertTrue((folder / "_proposed" / f"{b_id}.yaml").is_file())
        self.assertFalse((folder / f"{b_id}.yaml").exists())
        gate = json.loads((folder / "_proposed" / f"{b_id}.gate.json").read_text(encoding="utf-8"))
        self.assertEqual(gate["status"], "trial_pending")
        self.assertNotIn("approval", gate)
        # Retired (A): moved to _retired/, not back to _proposed/.
        self.assertTrue((folder / "_retired" / f"{a_id}.yaml").is_file())
        self.assertTrue((folder / "_retired" / f"{a_id}.gate.json").is_file())
        self.assertTrue((folder / "_retired" / f"{a_id}.retire.json").is_file())
        self.assertFalse((folder / "_proposed" / f"{a_id}.yaml").exists())
        self.assertFalse((folder / f"{a_id}.yaml").exists())
        # stack.json itself is archived away -- a fresh read is an empty stack.
        self.assertEqual(verify_stack(self.project), [])
        self.assertEqual(retired_patches(self.project), [])

    def test_reopen_with_only_retired_patches_returns_empty_list(self) -> None:
        a_id = self.approve(ADD_A, "小屋を足す")
        retire(self.project, self.template, a_id, "唯一のパッチを枯らしてからreopenします",
              experiment=self.missing_experiment)
        self.assertEqual(reopen(self.project), [])
        folder = self.project / "patches"
        self.assertTrue((folder / "_retired" / f"{a_id}.yaml").is_file())


if __name__ == "__main__":
    unittest.main()
