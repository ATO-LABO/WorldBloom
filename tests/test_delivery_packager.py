from copy import deepcopy
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from wb_delivery import packager
from wb_delivery.contract import canonical, digest, strict_json, validate_manifest
from wb_delivery.errors import DeliveryError

TEST_ROOT = Path(__file__).resolve().parents[1] / "test-tmp-packager"


class PackagerTests(unittest.TestCase):
    def setUp(self):
        TEST_ROOT.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=TEST_ROOT)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.hooks = self.root / "empty-hooks"
        self.hooks.mkdir()
        self.git("init", "-q")
        self.git("config", "core.autocrlf", "false")
        self.git("config", "core.symlinks", "false")
        self.files = {
            "viewer/app_desktop.py": b"# committed desktop\n",
            "viewer/server.py": b"# committed server\n",
            "viewer/worldbloom.ico": b"fixture-icon",
            "viewer/static/app.css": b"body{color:green}\n",
            "execution/world_editor.py": b"# world editor\n",
            "engine/world.py": b"# world model\n",
            "gapengine/__init__.py": b"",
            "scripts/runner.py": b"# runner\n",
            "requirements.txt": b"PyYAML>=6\n",
            "wb_delivery/__init__.py": b"# delivery\n",
            "wb_delivery/runtime_guard.py": b"# runtime guard\n",
            "wb_delivery/lease.py": b"# shared exclusive lease\n",
            "wb_delivery/errors.py": b"# delivery errors\n",
            "wb_delivery/contract.py": b"# delivery contract\n",
            "settings.json": b'{"example_private_setting":"not shipped"}',
            "projects/private/world.yaml": b"name: fixture private world\n",
            "templates/private/canon.yaml": b"private: fixture\n",
            "samples/private/data.txt": b"fixture sample",
            "runs/private/result.json": b"{}",
            "control/private/job.json": b"{}",
        }
        for rel, blob in self.files.items():
            p = self.repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(blob)
        self.sha = self.commit()
        self.context = self.context_for(self.sha)
        self.exe = self.root / "built.exe"
        self.exe.write_bytes(b"MZ fixture binary")
        self.context["build_receipt"] = self.receipt(self.sha)

    def tearDown(self):
        # Verify the absolute recursive-cleanup target belongs to this fixture.
        self.assertTrue(self.root.is_relative_to(TEST_ROOT.resolve()))
        self.temp.cleanup()

    def git(self, *args, input=None):
        p = subprocess.run(["git", "--no-optional-locks", "-C", str(self.repo), *args],
                           input=input, capture_output=True, timeout=15)
        self.assertEqual(p.returncode, 0, p.stderr.decode("utf8", errors="replace"))
        return p.stdout

    def commit(self):
        self.git("add", ".")
        self.git("-c", "user.name=Delivery Fixture", "-c", "user.email=delivery-fixture@example.invalid",
                 "-c", "commit.gpgsign=false", "-c", "core.hooksPath=" + str(self.hooks),
                 "commit", "-q", "-m", "fixture")
        return self.git("rev-parse", "HEAD").decode("ascii").strip()

    def context_for(self, sha):
        return {"repository": "ATO-LABO/WorldBloom", "event": "push", "ref": "refs/heads/develop",
                "source_sha": sha, "workflow_path": ".github/workflows/tests.yml", "workflow_id": 10,
                "run_id": 20, "run_attempt": 1, "ci_policy": "success",
                "merged_pr": {"number": 3, "merged": True, "merge_commit_sha": sha,
                              "base": {"ref": "develop", "repo": {"full_name": "ATO-LABO/WorldBloom"}}}}

    def receipt(self, sha):
        blob = self.exe.read_bytes()
        return {"builder": "wb-delivery-build-v1", "source_sha": sha, "head_sha": sha,
                "exe_sha256": digest(blob), "exe_size": len(blob)}

    def pack(self, output="bundle", context=None):
        return packager.pack_bundle(self.repo, self.exe, self.root / output, context or self.context)

    def error(self, code, function, *args, **kwargs):
        with self.assertRaises(DeliveryError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_exact_clean_checkout_is_verified_without_modification(self):
        source = packager.verify_source(self.repo, self.context)
        self.assertEqual(source.source_sha, self.sha)
        self.assertEqual(source.merged_pr, 3)
        self.assertEqual(self.git("status", "--porcelain"), b"")

    def test_only_push_develop_in_the_expected_repository_is_accepted(self):
        for key, value in [("event", "pull_request"), ("event", "workflow_dispatch"),
                           ("ref", "refs/heads/main"), ("repository", "fork/WorldBloom")]:
            c = deepcopy(self.context)
            c[key] = value
            self.error("not_merged_develop", packager.verify_source, self.repo, c)

    def test_other_sha_or_nested_checkout_cannot_be_packaged(self):
        c = deepcopy(self.context)
        c["source_sha"] = "a" * 40
        c["merged_pr"]["merge_commit_sha"] = c["source_sha"]
        self.error("source_sha_mismatch", packager.verify_source, self.repo, c)
        self.error("source_sha_mismatch", packager.verify_source, self.repo / "viewer", self.context)

    def test_ci_gate_and_identifiers_fail_closed(self):
        for key, value in [("ci_policy", "failure"), ("ci_policy", "skipped"),
                           ("run_id", True), ("run_attempt", 0), ("workflow_id", None)]:
            c = deepcopy(self.context)
            c[key] = value
            self.error("ci_not_verified", packager.verify_source, self.repo, c)
        c = deepcopy(self.context)
        c["workflow_path"] = ".github/workflows/other.yml"
        self.error("build_context", packager.verify_source, self.repo, c)

    def test_merged_pr_details_must_match_sha_base_and_repository(self):
        for path, value in [(('merged',), False), (('merged',), 1), (('number',), 0),
                            (('merge_commit_sha',), "b" * 40), (('base', 'ref'), "main"),
                            (('base', 'repo', 'full_name'), "fork/WorldBloom")]:
            c = deepcopy(self.context)
            row = c["merged_pr"]
            for k in path[:-1]:
                row = row[k]
            row[path[-1]] = value
            self.error("merge_not_verified", packager.verify_source, self.repo, c)

    def test_tracked_local_edits_are_rejected_before_output_creation(self):
        (self.repo / "viewer/server.py").write_bytes(b"unmerged local code")
        self.error("dirty_source", self.pack)
        self.assertFalse((self.root / "bundle").exists())

    def test_untracked_local_files_are_rejected(self):
        (self.repo / "viewer/local-helper.py").write_bytes(b"unmerged helper")
        self.error("dirty_source", self.pack)

    def test_ignored_untracked_files_are_rejected(self):
        (self.repo / ".gitignore").write_text(".env\n", encoding="utf8")
        self.sha = self.commit()
        c = self.context_for(self.sha)
        (self.repo / ".env").write_text("fixture only", encoding="utf8")
        self.assertEqual(self.git("status", "--porcelain"), b"")
        self.error("dirty_source", packager.verify_source, self.repo, c)

    def test_bundle_contains_only_commit_runtime_and_checked_binary(self):
        manifest = self.pack()
        out = self.root / "bundle"
        self.assertEqual(validate_manifest(manifest), manifest)
        self.assertEqual(strict_json((out / "manifest.json").read_bytes()), manifest)
        names = {f["path"] for f in manifest["files"]}
        self.assertEqual(names, {"WorldBloom-Studio.exe", "app/VERSION.txt", "app/PACKAGE-MANIFEST.json",
                                 *{"app/" + x for x in self.files if x.split('/')[0] in packager.RUNTIME_DIRS or x == 'requirements.txt'}})
        self.assertFalse(any((out / "app" / p).exists() for p in ["settings.json", "projects", "templates", "samples", "runs", "control", ".git"]))
        for f in manifest["files"]:
            b = (out / f["path"]).read_bytes()
            self.assertEqual((len(b), digest(b)), (f["size"], f["sha256"]))
        self.assertEqual((out / "app/viewer/server.py").read_bytes(), self.files["viewer/server.py"])
        self.assertEqual(self.git("status", "--porcelain"), b"")

    def test_changed_or_unproven_executable_is_rejected_before_output(self):
        c = deepcopy(self.context)
        del c["build_receipt"]
        self.error("build_receipt_mismatch", self.pack, context=c)
        for field, value in [("source_sha", "a" * 40), ("head_sha", "b" * 40),
                             ("exe_sha256", "c" * 64), ("exe_size", True), ("builder", "unknown")]:
            c = deepcopy(self.context)
            c["build_receipt"][field] = value
            self.error("build_receipt_mismatch", self.pack, context=c)
        self.exe.write_bytes(b"MZ altered binary")
        self.error("build_receipt_mismatch", self.pack)
        self.assertFalse((self.root / "bundle").exists())

    def test_output_inside_checkout_or_existing_output_is_never_overwritten(self):
        self.error("unsafe_or_existing_output", packager.pack_bundle, self.repo, self.exe,
                   self.repo / "bundle", self.context)
        out = self.root / "bundle"
        out.mkdir()
        marker = out / "existing.txt"
        marker.write_bytes(b"keep")
        self.error("unsafe_or_existing_output", self.pack)
        self.assertEqual(marker.read_bytes(), b"keep")

    def test_source_changed_during_packaging_never_publishes_a_manifest(self):
        original = packager._read_blob
        fired = False
        def read(source, oid):
            nonlocal fired
            if not fired:
                fired = True
                (self.repo / "viewer/server.py").write_bytes(b"unmerged while packaging")
            return original(source, oid)
        with patch.object(packager, "_read_blob", side_effect=read):
            self.error("dirty_source", self.pack)
        self.assertFalse((self.root / "bundle/manifest.json").exists())
        self.assertEqual((self.root / "bundle/app/viewer/server.py").read_bytes(), self.files["viewer/server.py"])

    def test_committed_runtime_secret_path_is_rejected(self):
        (self.repo / "viewer/credentials.json").write_text('{"fixture":"not a secret"}', encoding="utf8")
        sha = self.commit()
        c = self.context_for(sha)
        c["build_receipt"] = self.receipt(sha)
        self.error("sensitive_source_path", self.pack, context=c)
        self.assertFalse((self.root / "bundle").exists())

    def test_git_link_blob_is_rejected_without_creating_an_os_symlink(self):
        blob = b"../../outside-fixture"
        oid = self.git("hash-object", "-w", "--stdin", input=blob).decode("ascii").strip()
        (self.repo / "viewer/link.txt").write_bytes(blob)
        self.git("update-index", "--add", "--cacheinfo", "120000," + oid + ",viewer/link.txt")
        self.git("-c", "user.name=Delivery Fixture", "-c", "user.email=delivery-fixture@example.invalid",
                 "-c", "commit.gpgsign=false", "-c", "core.hooksPath=" + str(self.hooks), "commit", "-q", "-m", "link fixture")
        sha = self.git("rev-parse", "HEAD").decode("ascii").strip()
        c = self.context_for(sha)
        c["build_receipt"] = self.receipt(sha)
        self.error("linked_or_submodule_source", self.pack, context=c)

    def test_reparse_root_is_rejected(self):
        actual = packager.is_link
        with patch.object(packager, "is_link", side_effect=lambda p: Path(p) == self.repo or actual(p)):
            self.error("linked_source_or_output", packager.verify_source, self.repo, self.context)

    def test_git_replace_ref_cannot_substitute_uncommitted_blob_content(self):
        original = self.git("rev-parse", "HEAD:viewer/server.py").decode("ascii").strip()
        substitute = self.git("hash-object", "-w", "--stdin", input=b"unmerged replacement blob").decode("ascii").strip()
        self.git("replace", original, substitute)
        self.assertEqual(self.git("cat-file", "blob", original), b"unmerged replacement blob")
        self.pack()
        self.assertEqual((self.root / "bundle/app/viewer/server.py").read_bytes(), self.files["viewer/server.py"])

    def test_executable_from_original_checkout_is_not_a_build_output(self):
        inside = self.repo / "compiled.exe"
        inside.write_bytes(self.exe.read_bytes())
        sha = self.commit()
        c = self.context_for(sha)
        c["build_receipt"] = self.receipt(sha)
        self.error("executable_inside_checkout", packager.pack_bundle, self.repo, inside, self.root / "bundle", c)

    def test_blob_size_is_checked_before_loading_content(self):
        source = packager.verify_source(self.repo, self.context)
        oid = self.git("rev-parse", "HEAD:viewer/server.py").decode("ascii").strip()
        with patch.object(packager, "MAX_FILE", 1):
            self.error("source_file_too_large", packager._read_blob, source, oid)

    def test_executable_header_and_size_limits_are_checked(self):
        self.exe.write_bytes(b"not an exe")
        self.error("invalid_executable_header", self.pack)
        self.exe.write_bytes(b"MZ fixture binary")
        with patch.object(packager, "MAX_FILE", 3):
            self.error("executable_too_large", self.pack)
        with patch.object(packager, "MAX_EXPANDED", 1):
            self.error("bundle_too_large", self.pack)
        self.assertFalse((self.root / "bundle/manifest.json").exists())

    def fake_build(self, args, **kwargs):
        self.assertFalse(kwargs["shell"])
        self.assertEqual(args[1:5], ["-I", "-B", "-m", "PyInstaller"])
        self.assertEqual(args[args.index("--name") + 1], "WorldBloom-Studio")
        script = Path(args[-1])
        self.assertNotEqual(script, self.repo / "viewer/app_desktop.py")
        self.assertEqual(script.read_bytes(), self.files["viewer/app_desktop.py"])
        self.assertFalse((Path(kwargs["cwd"]) / "projects").exists())
        dist = Path(args[args.index("--distpath") + 1])
        dist.mkdir()
        (dist / "WorldBloom-Studio.exe").write_bytes(b"MZ" + script.read_bytes())
        return SimpleNamespace(returncode=0)

    def test_build_wrapper_uses_committed_snapshot_and_generates_matching_receipt(self):
        def checked_build(args, **kwargs):
            self.assertIn("-I", args)
            self.assertEqual(Path(args[args.index("--paths") + 1]), self.root / "build/source")
            self.assertNotIn("PYTHONPATH", kwargs["env"])
            self.assertNotIn("GITHUB_TOKEN", kwargs["env"])
            self.assertEqual(kwargs["env"]["PYINSTALLER_CONFIG_DIR"], str(self.root / "build/cache"))
            return self.fake_build(args, **kwargs)
        result = packager.build_studio(self.repo, self.root / "build", self.context, run=checked_build)
        blob = Path(result["exe"]).read_bytes()
        self.assertEqual(result["receipt"]["exe_sha256"], digest(blob))
        self.assertEqual(result["receipt"]["head_sha"], self.sha)
        self.assertEqual(strict_json((self.root / "build/build-receipt.json").read_bytes()), result["receipt"])
        c = deepcopy(self.context)
        c["build_receipt"] = result["receipt"]
        self.assertEqual(packager.pack_bundle(self.repo, result["exe"], self.root / "bundle", c)["source_sha"], self.sha)
        self.assertEqual(self.git("status", "--porcelain"), b"")

    def test_build_failure_cannot_produce_a_success_receipt(self):
        self.error("build_failed", packager.build_studio, self.repo, self.root / "build", self.context,
                   run=lambda *a, **k: SimpleNamespace(returncode=1))
        self.assertFalse((self.root / "build/build-receipt.json").exists())

    def test_edit_and_revert_during_build_does_not_enter_snapshot_binary(self):
        def run(args, **kwargs):
            p = self.repo / "viewer/app_desktop.py"
            p.write_bytes(b"unmerged transient source")
            result = self.fake_build(args, **kwargs)
            p.write_bytes(self.files["viewer/app_desktop.py"])
            return result
        result = packager.build_studio(self.repo, self.root / "build", self.context, run=run)
        self.assertEqual(Path(result["exe"]).read_bytes(), b"MZ" + self.files["viewer/app_desktop.py"])

    def test_build_rejects_source_left_dirty_by_other_work(self):
        def run(args, **kwargs):
            result = self.fake_build(args, **kwargs)
            (self.repo / "viewer/server.py").write_bytes(b"unmerged left dirty")
            return result
        self.error("dirty_source", packager.build_studio, self.repo, self.root / "build", self.context, run=run)
        self.assertFalse((self.root / "build/build-receipt.json").exists())


if __name__ == "__main__":
    unittest.main()
