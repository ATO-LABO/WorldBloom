"""Security regressions use disposable canaries, never real private files."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from execution import genre_editor
from execution.library import LibraryStore
from execution.provenance import ConfigError, contained
from viewer import workbench_pages
from viewer.data import ForbiddenPath, RunRepository
from viewer.server import static_path


def directory_link(test, link, target, *, junction=False):
    if junction:
        if os.name != "nt":
            test.skipTest("Windows junction")
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, text=True)
        test.assertEqual(result.returncode, 0, result.stderr)
        test.addCleanup(os.rmdir, link)
    else:
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as error:
            test.skipTest(f"directory symlinks unavailable: {error}")
        test.addCleanup(link.unlink)


class StorageBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="wb-security-paths-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        self.world = self.repo / "projects" / "source"
        self.genre = self.repo / "templates" / "source"
        self.world.mkdir(parents=True)
        self.genre.mkdir(parents=True)
        (self.world / "world.yaml").write_text("name: source\n", encoding="utf-8")
        (self.genre / "rules.yaml").write_text("[]\n", encoding="utf-8")
        self.outside = self.root / "outside"
        self.outside.mkdir()
        (self.outside / "canary.txt").write_text("fixture-only", encoding="utf-8")
        self.store = LibraryStore(self.repo)

    def copy_rejects_link(self, kind, *, junction=False):
        source = self.world if kind == "world" else self.genre
        directory_link(self, source / "attachments", self.outside, junction=junction)
        with self.assertRaises(ConfigError):
            if kind == "world":
                self.store.create_world("clone", from_id="source", genre_id="source", name="Clone")
            else:
                genre_editor.create(self.store, "clone", from_id="source", name="Clone")
        self.assertFalse((source.parent / "clone").exists())
        self.assertEqual((self.outside / "canary.txt").read_text(), "fixture-only")

    def test_world_copy_rejects_nested_symlink(self):
        self.copy_rejects_link("world")

    def test_genre_copy_rejects_nested_symlink(self):
        self.copy_rejects_link("genre")

    def test_world_copy_rejects_nested_junction(self):
        self.copy_rejects_link("world", junction=True)

    def test_genre_copy_rejects_nested_junction(self):
        self.copy_rejects_link("genre", junction=True)

    def test_contained_rejects_junction_even_when_target_is_inside_root(self):
        directory_link(self, self.repo / "alias", self.world, junction=True)
        with self.assertRaises(ConfigError):
            contained(self.repo, "alias/world.yaml")

    def test_template_context_rejects_linked_directory(self):
        (self.outside / "rationality.yaml").write_text(
            "backend: {type: none, model: fixture-canary}\n", encoding="utf-8")
        directory_link(self, self.repo / "templates" / "linked", self.outside)
        with self.assertRaises(ConfigError):
            workbench_pages._rationality_form_context(self.repo, "linked")

    def test_template_context_rejects_linked_file(self):
        target = self.outside / "rationality.yaml"
        target.write_text("backend: {type: none, model: fixture-canary}\n", encoding="utf-8")
        link = self.genre / "rationality.yaml"
        try:
            link.symlink_to(target)
        except OSError as error:
            self.skipTest(f"file symlinks unavailable: {error}")
        self.addCleanup(link.unlink)
        with self.assertRaises(ConfigError):
            workbench_pages._rationality_form_context(self.repo, "source")

    def test_template_context_rejects_linked_junction(self):
        (self.outside / "rationality.yaml").write_text(
            "backend: {type: none, model: fixture-canary}\n", encoding="utf-8")
        directory_link(self, self.repo / "templates" / "linked", self.outside, junction=True)
        with self.assertRaises(ConfigError):
            workbench_pages._rationality_form_context(self.repo, "linked")

    def test_template_helpers_reject_invalid_ids(self):
        for ident in ("../outside", "/absolute", "C:\\outside", "a/b", "a\\b"):
            for helper in (workbench_pages._rationality_form_context, workbench_pages._route_form_context):
                with self.subTest(ident=ident, helper=helper.__name__), self.assertRaises(ConfigError):
                    helper(self.repo, ident)

    def test_containment_rejects_traversal_and_absolute_paths(self):
        for rel in ("../outside/canary.txt", str(self.outside / "canary.txt"), "a\\..\\outside"):
            with self.subTest(rel=rel), self.assertRaises(ConfigError):
                contained(self.repo, rel)
        repository = RunRepository(self.repo)
        with self.assertRaises(ForbiddenPath):
            repository.safe_path(self.repo, "../outside/canary.txt")

    def test_contained_accepts_the_configured_root_itself(self):
        for relative in ("", "."):
            with self.subTest(relative=relative):
                self.assertEqual(contained(self.repo, relative), self.repo)

    def test_contained_lexical_check_rejects_a_prefix_sibling_before_io(self):
        sibling = self.repo.with_name(self.repo.name + "-other") / "canary.txt"
        original = os.path.abspath
        def normalize(path):
            return str(sibling) if Path(path) == self.repo / "world.yaml" else original(path)
        with patch("execution.provenance.os.path.abspath", side_effect=normalize), \
             patch("execution.provenance._checked_root") as check_root:
            with self.assertRaises(ConfigError):
                contained(self.repo, "world.yaml")
            check_root.assert_not_called()
        self.assertEqual((self.outside / "canary.txt").read_text(), "fixture-only")

    def test_contained_accepts_a_configured_root_with_lexical_parent_segments(self):
        (self.repo / "nested").mkdir()
        root = self.repo / "nested" / ".."
        result = contained(root, "world.yaml")
        self.assertEqual(result, root / "world.yaml")
        self.assertEqual(result.relative_to(root), Path("world.yaml"))

    @unittest.skipUnless(os.name == "nt", "Windows case-insensitive paths")
    def test_contained_preserves_windows_root_case(self):
        root = self.repo / "MiXeDRoot"
        root.mkdir()
        for spelling in (root, Path(str(root).swapcase())):
            with self.subTest(root=spelling):
                result = contained(spelling, "CaSe.yaml")
                self.assertEqual(str(result.parent), str(spelling))
                result.write_text("fixture-only", encoding="utf-8")
                self.assertEqual((root / "CaSe.yaml").read_text(), "fixture-only")

    @unittest.skipUnless(os.name == "nt", "Windows drives and UNC paths")
    def test_contained_rejects_other_drive_and_unc_before_io(self):
        other = "Z:" if self.repo.drive.upper() != "Z:" else "Y:"
        values = (other + "canary.txt", other + "/canary.txt", "//host/share/canary.txt",
                  "\\canary.txt", "\\\\?\\" + other + "\\canary.txt")
        with patch("execution.provenance._checked_root") as check_root:
            for relative in values:
                with self.subTest(relative=relative), self.assertRaises(ConfigError):
                    contained(self.repo, relative)
            check_root.assert_not_called()

    def test_static_allowlist_blocks_external_names(self):
        from viewer.data import MissingResource
        for name in ("../canary.txt", "C:\\outside", "app.js/../../canary.txt", "unlisted.js"):
            with self.subTest(name=name), self.assertRaises((ForbiddenPath, MissingResource)):
                static_path(name)
        self.assertEqual(static_path("app.js").name, "app.js")
