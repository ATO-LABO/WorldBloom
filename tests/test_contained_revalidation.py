"""Containment retries must recover a creation race and still fail closed."""
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from execution import provenance as p
from test_security_paths import directory_link


class ContainmentRevalidationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="wb-contained-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "parent" / "control"
        self.child = self.root / "legacy"
        self.outside = self.base / "outside"
        self.outside.mkdir()
        self.canary = self.outside / "canary.txt"
        self.canary.write_bytes(b"fixture-only")
        p._checked_root.cache_clear()
        self.addCleanup(p._checked_root.cache_clear)

    def mismatch_then(self, action=None, *, always=False):
        original = Path.resolve
        calls = []

        def resolve(path, *args, **kwargs):
            if path == self.child:
                calls.append(path)
                if len(calls) == 1 or always:
                    if action is not None:
                        action()
                    return self.outside / "legacy"
            return original(path, *args, **kwargs)

        return patch.object(Path, "resolve", resolve), calls

    def assert_canary_unchanged(self):
        self.assertEqual(self.canary.read_bytes(), b"fixture-only")
        self.assertEqual(sorted(x.name for x in self.outside.iterdir()), ["canary.txt"])

    def test_transient_mismatch_revalidates_without_writing(self):
        changed, calls = self.mismatch_then()
        with changed:
            self.assertEqual(p.contained(self.root, "legacy"), self.child)
        self.assertEqual(len(calls), 2)
        self.assertFalse(self.root.exists())
        self.assert_canary_unchanged()

    def test_persistent_outside_result_is_rejected_after_one_revalidation(self):
        changed, calls = self.mismatch_then(always=True)
        with changed, self.assertRaises(p.ConfigError):
            p.contained(self.root, "legacy")
        self.assertEqual(len(calls), 2)
        self.assertFalse(self.root.exists())
        self.assert_canary_unchanged()

    def test_invalid_relative_syntax_is_rejected_before_validation(self):
        values = ["../outside", str(self.outside), "a/../../outside", "a\\outside"]
        if os.name == "nt":
            values += ["C:outside", "C:/outside", "//server/share/outside",
                       "\\\\?\\C:\\outside", "\\\\?\\UNC\\server\\share\\outside"]
        with patch.object(p, "_checked_root") as checked:
            for relative in values:
                with self.subTest(relative=relative), self.assertRaises(p.ConfigError):
                    p.contained(self.root, relative)
            checked.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_resolve_error_is_not_swallowed_or_retried(self):
        p._checked_root(str(self.root))
        with patch.object(Path, "resolve", side_effect=PermissionError("fixture")) as resolve:
            with self.assertRaises(PermissionError):
                p.contained(self.root, "legacy")
        self.assertEqual(resolve.call_count, 1)

    def check_root_anchor_after_inspection(self, location, *, junction=False):
        self.root.mkdir(parents=True)
        (self.root / "canary.txt").write_bytes(b"inside-fixture")
        link = self.root if location == "root" else self.root.parent
        if location == "ancestor":
            (self.outside / "control").mkdir()
            (self.outside / "control" / "canary.txt").write_bytes(b"fixture-only")
        original = link.with_name(link.name + "-original")
        self.assertTrue(link.resolve().is_relative_to(self.base.resolve()))
        self.assertTrue(original.resolve().is_relative_to(self.base.resolve()))
        last_ancestor = self.root.parents[-1]
        real_is_link = p.is_link
        changed = []

        def inspected(path):
            answer = real_is_link(path)
            if path == last_ancestor and not changed:
                changed.append(True)
                link.rename(original)
                directory_link(self, link, self.outside, junction=junction)
            return answer

        with patch.object(p, "is_link", inspected), self.assertRaises(p.ConfigError):
            p.contained(self.root, "canary.txt").read_bytes()
        self.assertEqual(changed, [True])
        self.assertEqual(self.canary.read_bytes(), b"fixture-only")

    def test_symlink_root_does_not_change_checked_anchor(self):
        self.check_root_anchor_after_inspection("root")

    def test_symlink_ancestor_does_not_change_checked_anchor(self):
        self.check_root_anchor_after_inspection("ancestor")

    def test_junction_root_does_not_change_checked_anchor(self):
        self.check_root_anchor_after_inspection("root", junction=True)

    def test_junction_ancestor_does_not_change_checked_anchor(self):
        self.check_root_anchor_after_inspection("ancestor", junction=True)

    def check_link_added_during_revalidation(self, location, *, junction=False):
        self.root.mkdir(parents=True)
        # Prime the normal-path cache before planting the link. The retry must
        # validate the real root/ancestors, even when this cache still says safe.
        p._checked_root(str(self.root))

        def plant():
            if location == "child":
                link = self.child
            elif location == "root":
                link = self.root
                link.rename(link.with_name("control-original"))
            else:
                link = self.root.parent
                link.rename(link.with_name("parent-original"))
            directory_link(self, link, self.outside, junction=junction)

        changed, calls = self.mismatch_then(plant)
        with changed, self.assertRaises(p.ConfigError) as error:
            p.contained(self.root, "legacy")
        self.assertIn("リンク", str(error.exception))
        self.assertEqual(len(calls), 1)
        self.assert_canary_unchanged()

    def test_symlink_child_planted_during_retry_is_rejected(self):
        self.check_link_added_during_revalidation("child")

    def test_symlink_root_planted_during_retry_is_rejected_despite_cache(self):
        self.check_link_added_during_revalidation("root")

    def test_symlink_ancestor_planted_during_retry_is_rejected_despite_cache(self):
        self.check_link_added_during_revalidation("ancestor")

    def test_junction_child_planted_during_retry_is_rejected(self):
        self.check_link_added_during_revalidation("child", junction=True)

    def test_junction_root_planted_during_retry_is_rejected_despite_cache(self):
        self.check_link_added_during_revalidation("root", junction=True)

    def test_junction_ancestor_planted_during_retry_is_rejected_despite_cache(self):
        self.check_link_added_during_revalidation("ancestor", junction=True)

    @unittest.skipUnless(os.name == "nt", "Windows realpath race")
    def test_extended_outside_path_is_not_accepted_by_retry(self):
        outside = Path("\\\\?\\" + str(self.outside / "legacy"))
        p._checked_root(str(self.root))
        original = Path.resolve

        def resolve(path, *args, **kwargs):
            return outside if path == self.child else original(path, *args, **kwargs)

        with patch.object(Path, "resolve", resolve), self.assertRaises(p.ConfigError):
            p.contained(self.root, "legacy")
        self.assert_canary_unchanged()

    @unittest.skipUnless(os.name == "nt", "Windows realpath race")
    def test_real_parent_creation_between_windows_errors_3_and_2(self):
        import ntpath
        self.root.parent.mkdir()
        missing_parent = threading.Event()
        parent_created = threading.Event()
        stopped = threading.Event()
        errors = []
        creator_errors = []
        resolutions = []
        self.native_errors = errors
        self.native_resolutions = resolutions
        original_final = ntpath._getfinalpathname
        original_resolve = Path.resolve

        def create_parent():
            try:
                if not missing_parent.wait(5):
                    raise AssertionError("reader did not reach the missing-parent boundary")
                if not stopped.is_set():
                    self.root.mkdir()
            except BaseException as error:
                creator_errors.append(error)
            finally:
                parent_created.set()

        def final(path):
            try:
                return original_final(path)
            except OSError as error:
                if os.path.normcase(path) == os.path.normcase(str(self.child)):
                    errors.append(error.winerror)
                    if len(errors) == 1:
                        self.assertEqual(error.winerror, 3)
                        missing_parent.set()
                        self.assertTrue(parent_created.wait(5), "parent creation did not finish")
                raise  # The real OS error and all subsequent OS calls are retained.

        def resolve(path, *args, **kwargs):
            result = original_resolve(path, *args, **kwargs)
            if path == self.child:
                resolutions.append(str(result))
            return result

        creator = threading.Thread(target=create_parent)
        creator.start()
        try:
            with patch.object(ntpath, "_getfinalpathname", final), patch.object(Path, "resolve", resolve):
                self.assertEqual(p.contained(self.root, "legacy"), self.child)
        finally:
            stopped.set()
            missing_parent.set()
            creator.join(5)
        self.assertFalse(creator.is_alive())
        self.assertEqual(creator_errors, [])
        self.assertEqual(errors[:2], [3, 2])
        self.assertTrue(self.root.is_dir())
        self.assertFalse(self.child.exists())
        # 3.12 retains the extended prefix on the first real resolution. Future
        # runtimes may normalize it themselves; containment must succeed in both.
        if resolutions[0].startswith("\\\\?\\"):
            self.assertEqual(len(resolutions), 2)
        self.assert_canary_unchanged()
