"""Pre-positioned links must not redirect fact-workspace history I/O."""
import unittest

from execution.provenance import ConfigError
from test_fact_workspace import WorkspaceFixture
from test_security_paths import directory_link


class FactHistoryBoundaryTests(WorkspaceFixture, unittest.TestCase):
    def outside(self):
        folder = self.fx.base / "fact-security-outside"
        folder.mkdir(exist_ok=True)
        return folder

    def linked_file(self, path):
        external = self.outside() / path.name
        external.write_bytes(path.read_bytes())
        path.unlink()
        try:
            path.symlink_to(external)
        except OSError as exc:
            self.skipTest(f"File symlinks unavailable: {exc}")
        self.addCleanup(path.unlink)
        return external

    def reject_linked_revision_directory(self, *, junction=False):
        folder = self.store.folder(self.rid, self.cid)
        folder.mkdir(parents=True)
        external = self.outside()
        canary = external / "canary.txt"
        canary.write_bytes(b"unchanged fixture")
        directory_link(self, folder / "revisions", external, junction=junction)
        with self.assertRaises(ConfigError):
            self.store.save(self.rid, self.cid, self.body, 0)
        self.assertFalse((external / "1.json").exists())
        self.assertEqual(canary.read_bytes(), b"unchanged fixture")

    def test_save_rejects_linked_revision_directory(self):
        self.reject_linked_revision_directory()

    def test_save_rejects_junction_revision_directory(self):
        self.reject_linked_revision_directory(junction=True)

    def test_current_rejects_linked_head_file(self):
        self.store.save(self.rid, self.cid, self.body, 0)
        self.linked_file(self.store.folder(self.rid, self.cid) / "current.json")
        with self.assertRaises(ConfigError):
            self.store.current(self.rid, self.cid)

    def test_current_rejects_linked_revision_file(self):
        self.store.save(self.rid, self.cid, self.body, 0)
        self.linked_file(self.store.folder(self.rid, self.cid) / "revisions" / "1.json")
        with self.assertRaises(ConfigError):
            self.store.current(self.rid, self.cid)

    def test_fact_store_rejects_linked_seal(self):
        saved = self.store.save(self.rid, self.cid, self.body, 0)
        reference = saved["reference"]
        self.linked_file(self.store.facts.root / reference["package_id"] / "seal.json")
        with self.assertRaises(ConfigError):
            self.store.facts.resolve(reference)
