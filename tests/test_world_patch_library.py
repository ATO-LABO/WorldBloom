"""Unit tests for WB-WORLDGROW-001 段階5d "ジャンルの資産" (genre library):
execution.world_patch_library's export_patch/import_patch/list_entries/fit/
rewrite_for_world, and the digest/fingerprint invariants LIBRARY_DIR's
exclusion (D1-D3, gapengine.world_patch.LIBRARY_DIR) depends on.

Fixtures follow tests/test_world_patch_retire.py's shape: a tempdir copy of
the real momotaro project/template, with write_approved() (tests/
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

from execution.configs import ConfigStore
from execution.world_patch_approval import retire
from execution.world_patch_library import (DuplicateAssetError, export_patch, fit, import_patch,
                                            library_dir, list_entries, rewrite_for_world)
from execution.world_patches import expanded_snapshot
from gapengine.evolve import _content_fingerprint
from gapengine.world_patch import PatchError, read_stack
from gapengine.world_patch_inputs import inputs_digest, read_subjects, resolve_references

ROOT = Path(__file__).resolve().parents[1]

ADD_HUT = {
    "zones": [{"name": "船大工の小屋", "parent": "海", "note": "船具を扱う小屋"}],
    "items": [{"name": "古びた帆布", "sources": [
        {"type": "investigate", "zone": "船大工の小屋", "count": 1, "max": 2}]}],
    "facts": [],
}

STRONG_USAGE = {"elites_total": 3, "elites_strong": 1, "elites_weak": 0, "cells": {}}
NO_STRONG_USAGE = {"elites_total": 3, "elites_strong": 0, "elites_weak": 1, "cells": {}}


def _published_only_experiment(root, name="exp-published"):
    """A ConfigStore-prepared-shaped experiment with only published/<rev>/
    archive.json (no top-level archive.json) -- gapengine.world_patch_usage.
    load_archive()'s fallback path (plan §7: 公開版のみ実験フィクスチャで export)."""
    experiment = Path(root) / "runs" / name
    revision_dir = experiment / "published" / "1"
    revision_dir.mkdir(parents=True)
    (revision_dir / "archive.json").write_text(json.dumps({"cells": {}}, ensure_ascii=False), encoding="utf-8")
    (experiment / "published" / "current.json").write_text(
        json.dumps({"revision": 1}, ensure_ascii=False), encoding="utf-8")
    return experiment


class LibraryTestBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wb-world-patch-library-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.project = self.repo / "projects" / "momotaro"
        self.template = self.repo / "templates" / "momotaro"
        shutil.copytree(ROOT / "projects" / "momotaro", self.project, ignore=shutil.ignore_patterns("patches"))
        shutil.copytree(ROOT / "templates" / "momotaro", self.template)
        self.experiment = _published_only_experiment(self.root)

    def approve(self, project, add, title="小屋を足す"):
        patch = write_approved(project, {"title": title, "add": add}, template=self.template)
        return patch["id"]

    def freeze(self, patch_id, *, project_name="momotaro"):
        """WB-WORLDGROW-001 段階5d M1: export_patch() now requires that
        self.experiment's own *frozen* world actually had `patch_id` applied
        -- write that world (execution.epoch_chain's own frozen-world shape,
        inputs/projects/<id>/world.yaml). Only expansion.patches[].id is
        read by _world_parent_rev, so the rest of the world is irrelevant."""
        world_path = self.experiment / "inputs" / "projects" / project_name / "world.yaml"
        world_path.parent.mkdir(parents=True, exist_ok=True)
        world_path.write_text(
            yaml.safe_dump({"expansion": {"patches": [{"id": patch_id}]}}, allow_unicode=True), encoding="utf-8")

    def fresh_target(self, name="momotaro2"):
        target = self.repo / "projects" / name
        shutil.copytree(ROOT / "projects" / "momotaro", target, ignore=shutil.ignore_patterns("patches"))
        return target


class ExportPatchTests(LibraryTestBase):
    def test_export_writes_asset_with_provenance(self):
        pid = self.approve(self.project, ADD_HUT)
        self.freeze(pid)
        path = export_patch(self.project, self.template, pid, experiment=self.experiment,
                            protagonist="桃太郎", usage=STRONG_USAGE)
        self.assertEqual(path, library_dir(self.template) / f"{pid}.yaml")
        entry = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.assertEqual(entry["schema_version"], 1)
        self.assertEqual(entry["patch"]["id"], pid)
        stack = read_stack(self.project)
        revision = next(r for r in stack["revisions"] if r["patch_id"] == pid)
        prov = entry["provenance"]
        self.assertEqual(prov["patch_sha256"], revision["patch_sha256"])
        self.assertEqual(prov["revision"], revision)
        self.assertEqual(prov["stack_head"], stack["head"])
        self.assertEqual(prov["world_id"], "momotaro")
        self.assertEqual(prov["template_id"], "momotaro")
        self.assertEqual(prov["evidence"]["experiment"], self.experiment.name)
        self.assertEqual(prov["evidence"]["archive_source"], "published/1")
        self.assertIsNotNone(prov["evidence"]["archive_sha256"])
        self.assertEqual(prov["evidence"]["usage"], STRONG_USAGE)
        self.assertEqual(prov["evidence"]["protagonist"], "桃太郎")

    def test_export_unapproved_patch_is_rejected(self):
        with self.assertRaises(PatchError):
            export_patch(self.project, self.template, "p-00000000", experiment=self.experiment,
                        protagonist="桃太郎", usage=STRONG_USAGE)

    def test_export_retired_patch_is_rejected(self):
        pid = self.approve(self.project, ADD_HUT)
        retire(self.project, self.template, pid, "テストのため枯らします", experiment=self.experiment)
        with self.assertRaises(PatchError):
            export_patch(self.project, self.template, pid, experiment=self.experiment,
                        protagonist="桃太郎", usage=STRONG_USAGE)

    def test_export_without_strong_use_is_rejected(self):
        pid = self.approve(self.project, ADD_HUT)
        self.freeze(pid)  # M1 satisfied -- this test is about the usage rejection specifically
        with self.assertRaises(PatchError):
            export_patch(self.project, self.template, pid, experiment=self.experiment,
                        protagonist="桃太郎", usage=NO_STRONG_USAGE)
        self.assertEqual(list_entries(self.template), [])

    def test_export_duplicate_id_is_rejected(self):
        pid = self.approve(self.project, ADD_HUT)
        self.freeze(pid)
        export_patch(self.project, self.template, pid, experiment=self.experiment,
                    protagonist="桃太郎", usage=STRONG_USAGE)
        with self.assertRaises(DuplicateAssetError):
            export_patch(self.project, self.template, pid, experiment=self.experiment,
                        protagonist="桃太郎", usage=STRONG_USAGE)

    def test_export_without_frozen_world_is_rejected(self):
        # WB-WORLDGROW-001 段階5d M1: no inputs/projects/.../world.yaml or
        # expanded-project/world.yaml at all under self.experiment.
        pid = self.approve(self.project, ADD_HUT)
        with self.assertRaises(PatchError):
            export_patch(self.project, self.template, pid, experiment=self.experiment,
                        protagonist="桃太郎", usage=STRONG_USAGE)
        self.assertEqual(list_entries(self.template), [])

    def test_export_when_frozen_world_lacks_this_patch_is_rejected(self):
        # M1: the frozen world exists but was never built with THIS patch --
        # a same-named patch approved elsewhere must not count as evidence.
        pid = self.approve(self.project, ADD_HUT)
        self.freeze("p-notthisone")
        with self.assertRaises(PatchError):
            export_patch(self.project, self.template, pid, experiment=self.experiment,
                        protagonist="桃太郎", usage=STRONG_USAGE)
        self.assertEqual(list_entries(self.template), [])


class ListEntriesTests(LibraryTestBase):
    def test_empty_template_has_no_entries(self):
        self.assertEqual(list_entries(self.template), [])

    def test_broken_asset_reports_error_without_hiding_others(self):
        pid = self.approve(self.project, ADD_HUT)
        self.freeze(pid)
        export_patch(self.project, self.template, pid, experiment=self.experiment,
                    protagonist="桃太郎", usage=STRONG_USAGE)
        broken = library_dir(self.template) / "zzz-broken.yaml"
        broken.write_text("not: [valid, yaml", encoding="utf-8")
        entries = {e["id"]: e for e in list_entries(self.template)}
        self.assertEqual(sorted(entries), sorted([pid, "zzz-broken"]))
        self.assertIsNone(entries[pid]["error"])
        self.assertIsNotNone(entries["zzz-broken"]["error"])
        self.assertIsNone(entries["zzz-broken"]["doc"])


class DigestInvarianceTests(LibraryTestBase):
    """WB-WORLDGROW-001 段階5d §3: adding/removing a library asset under
    <template>/expansions/ must never change any input digest, frozen input
    manifest, or GA content fingerprint (the whole point of D1-D3's
    exclusion), and must never break expanded_snapshot()."""

    def snapshot(self):
        world = yaml.safe_load((self.project / "world.yaml").read_text(encoding="utf-8"))
        people = read_subjects(self.project / "subjects")
        refs = resolve_references(world, self.project)
        content_fingerprint = _content_fingerprint(self.project, self.template)
        input_digest = inputs_digest(world, people, self.template, refs)
        store = ConfigStore(self.repo, self.root / "control", self.root / "runs-unused")
        spec = {"label": "digest確認", "project_id": "momotaro", "template_id": "momotaro",
                "evolution": {"generations": 1, "population": 2, "seeds": 1, "keep": "all"}}
        manifest_sha = store.preview(spec)["input_manifest_sha256"]
        expanded_snapshot(self.project, self.template)  # must not raise
        return content_fingerprint, input_digest, manifest_sha

    def test_asset_does_not_move_any_digest_or_fingerprint(self):
        pid = self.approve(self.project, ADD_HUT)
        self.freeze(pid)
        before = self.snapshot()
        export_patch(self.project, self.template, pid, experiment=self.experiment,
                    protagonist="桃太郎", usage=STRONG_USAGE)
        self.assertTrue((library_dir(self.template) / f"{pid}.yaml").is_file())
        after_export = self.snapshot()
        self.assertEqual(before, after_export)
        for asset in library_dir(self.template).glob("*.yaml"):
            asset.unlink()
        after_removal = self.snapshot()
        self.assertEqual(before, after_removal)


class FitAndImportTests(LibraryTestBase):
    def setUp(self):
        super().setUp()
        self.pid = self.approve(self.project, ADD_HUT)
        self.freeze(self.pid)
        export_patch(self.project, self.template, self.pid, experiment=self.experiment,
                    protagonist="桃太郎", usage=STRONG_USAGE)
        self.entry = list_entries(self.template)[0]

    def test_fit_is_empty_for_a_compatible_world(self):
        target = self.fresh_target()
        world, people, _verified = expanded_snapshot(target, self.template)
        violations = fit(self.entry["doc"], world, people, reserved=set())
        self.assertEqual(violations, [])

    def test_fit_reports_violation_when_target_lacks_the_parent_zone(self):
        target = self.fresh_target()
        world_path = target / "world.yaml"
        world = yaml.safe_load(world_path.read_text(encoding="utf-8"))
        world["zones"] = [z for z in world["zones"] if z.get("name") != "海"]
        world_path.write_text(yaml.safe_dump(world, allow_unicode=True, sort_keys=False), encoding="utf-8")
        loaded, people, _verified = expanded_snapshot(target, self.template)
        violations = fit(self.entry["doc"], loaded, people, reserved=set())
        self.assertTrue(violations)

    def test_import_stages_a_trigger_pending_proposal(self):
        target = self.fresh_target()
        rewritten = import_patch(target, self.template, self.pid, repo_root=self.repo)
        self.assertEqual(rewritten["id"], self.pid)
        self.assertEqual(rewritten["parent_digest"], read_stack(target)["head"])
        self.assertEqual(rewritten["author"]["backend"], "library")
        self.assertEqual(rewritten["author"]["origin"]["world_id"], "momotaro")
        self.assertIn(rewritten["trigger"]["zone"], {"船大工の小屋"})
        proposed_path = target / "patches" / "_proposed" / f"{self.pid}.yaml"
        self.assertTrue(proposed_path.is_file())
        self.assertFalse(proposed_path.with_suffix(".gate.json").exists())
        on_disk = yaml.safe_load(proposed_path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk, rewritten)

    def test_import_rejects_when_already_proposed(self):
        target = self.fresh_target()
        import_patch(target, self.template, self.pid, repo_root=self.repo)
        with self.assertRaises(PatchError):
            import_patch(target, self.template, self.pid, repo_root=self.repo)

    def test_import_rejects_when_already_applied(self):
        target = self.fresh_target()
        same_id = self.approve(target, ADD_HUT)
        self.assertEqual(same_id, self.pid)
        with self.assertRaises(PatchError):
            import_patch(target, self.template, self.pid, repo_root=self.repo)

    def test_import_rejects_when_already_retired(self):
        target = self.fresh_target()
        same_id = self.approve(target, ADD_HUT)
        self.assertEqual(same_id, self.pid)
        retire(target, self.template, self.pid, "先に枯らしておくテストです", experiment=self.experiment)
        with self.assertRaises(PatchError):
            import_patch(target, self.template, self.pid, repo_root=self.repo)

    def test_import_unknown_entry_is_rejected(self):
        target = self.fresh_target()
        with self.assertRaises(PatchError):
            import_patch(target, self.template, "p-00000000", repo_root=self.repo)

    def test_import_head_mismatch_is_rejected(self):
        # R4 (Opus review): expect_head is checked inside patch_lock.
        target = self.fresh_target()
        from execution.world_patch_library import StaleImport
        with self.assertRaises(StaleImport):
            import_patch(target, self.template, self.pid, repo_root=self.repo, expect_head="not-the-real-head")
        self.assertFalse((target / "patches" / "_proposed" / f"{self.pid}.yaml").exists())

    def test_import_rejects_asset_whose_content_does_not_match_its_id(self):
        # R5 (Opus review): a hand-edited/corrupted asset file whose `add`
        # no longer hashes to its own filename must never be imported under
        # that filename's id.
        target = self.fresh_target()
        asset_path = library_dir(self.template) / f"{self.pid}.yaml"
        doc = yaml.safe_load(asset_path.read_text(encoding="utf-8"))
        doc["patch"]["add"]["items"][0]["name"] = "改変済みアイテム"
        asset_path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
        with self.assertRaises(PatchError):
            import_patch(target, self.template, self.pid, repo_root=self.repo)

    def test_imported_proposal_passes_static_check(self):
        """WB-WORLDGROW-001 段階5d §7: an imported entry must be an ordinary
        static-gate-passing proposal, checkable the same way a freshly
        generated one is (scripts/world_patch.py check --skip-trial)."""
        import scripts.world_patch as world_patch_cli

        target = self.fresh_target()
        import_patch(target, self.template, self.pid, repo_root=self.repo)
        experiment = self.root / "runs" / "exp-check"
        shutil.copytree(target, experiment / "expanded-project")
        (experiment / "summary.json").write_text(json.dumps({"seeds": [1, 2, 3, 4]}), encoding="utf-8")
        args = world_patch_cli.build_parser().parse_args([
            "check", "--experiment", str(experiment), "--project", str(target),
            "--patch", self.pid, "--template", str(self.template), "--skip-trial",
        ])
        status = world_patch_cli.cmd_check(args)
        self.assertEqual(status, 0)
        gate = json.loads((target / "patches" / "_proposed" / f"{self.pid}.gate.json").read_text(encoding="utf-8"))
        self.assertEqual(gate["static"]["violations"], [])
        self.assertEqual(gate["status"], "trial_pending")

    def test_rewrite_for_world_carries_content_verbatim(self):
        target_world = yaml.safe_load((self.fresh_target() / "world.yaml").read_text(encoding="utf-8"))
        rewritten = rewrite_for_world(
            self.entry["doc"]["patch"], parent_digest="deadbeef",
            library_ref={"template_id": "momotaro", "patch_id": self.pid,
                         "world_id": "momotaro", "world_name": "桃太郎"},
            world=target_world)
        self.assertEqual(rewritten["add"], self.entry["doc"]["patch"]["add"])
        self.assertEqual(rewritten["title"], self.entry["doc"]["patch"]["title"])
        self.assertEqual(rewritten["parent_digest"], "deadbeef")
        self.assertEqual(rewritten["trigger"]["verb"], "investigate")


if __name__ == "__main__":
    unittest.main()
