import copy
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile

from wb_delivery.bundle import verify_and_stage
from wb_delivery.contract import (DATA_ADAPTER, REPOSITORY, REQUIRED_FILES, ReleaseEvidence,
                                  canonical, digest, managed_path)
from wb_delivery.errors import DeliveryError
from wb_delivery.lease import lease
from wb_delivery.transaction import Installer, protected_snapshot

A, B, OLD = "a" * 40, "b" * 40, "1" * 40
TMP = Path(__file__).resolve().parents[1] / "test-tmp"
TMP.mkdir(exist_ok=True)


def contents(label):
    result = {p: (b"MZ" if p.endswith(".exe") else b"# ") + label.encode() for p in REQUIRED_FILES}
    return result


def artifact(root, payload=None, *, sha=A, edit_manifest=None, extra=None, raw_manifest=None):
    payload = payload or contents("new")
    manifest = dict(schema_version=1, repository=REPOSITORY, branch="develop", source_sha=sha,
                    platform="windows-x64", build=dict(workflow_id=2, workflow_path=".github/workflows/tests.yml",
                    run_id=3, run_attempt=1, merged_pr=4), data=dict(adapter=DATA_ADAPTER, requires_migration=False),
                    files=[dict(path=p, size=len(b), sha256=digest(b)) for p, b in sorted(payload.items())])
    if edit_manifest:
        edit_manifest(manifest)
    path = root / ("artifact-" + sha[0] + ".zip")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", raw_manifest if raw_manifest is not None else canonical(manifest))
            for p, b in payload.items():
                z.writestr(p, b)
            for p, b in extra or []:
                z.writestr(p, b)
    data = path.read_bytes()
    evidence = ReleaseEvidence(sha, 3, 1, 2, 4, 5, digest(data), len(data), 6)
    return path, evidence


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


class Fixture:
    def __enter__(self):
        self.temp = tempfile.TemporaryDirectory(dir=TMP)
        self.root = Path(self.temp.name)
        self.target, self.state = self.root / "portable", self.root / "updates"
        self.target.mkdir()
        self.old = contents("old") | {"app/viewer/obsolete.py": b"# old obsolete"}
        for p, b in self.old.items():
            path = self.target / p
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b)
        for p in ("app/settings.json", "app/projects/story/world.yaml", "app/templates/original/rules.yaml",
                  "app/samples/old/result.json", "runs/history.txt", "control/saved.json"):
            path = self.target / p
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture-user-data-preserve")
        self.protected = protected_snapshot(self.target)
        self.payload = contents("new") | {"app/viewer/new.py": b"# new"}
        self.path, self.evidence = artifact(self.root, self.payload)
        self.installer = Installer(self.target, self.state,
            runtime_probe=lambda _: dict(app_running=False, jobs_running=False),
            compatibility=lambda *_: True, forward_check=lambda *_: True)
        self.installer.enroll(self.old, source_sha=OLD, allowed_first_sha=A, confirmed=True)
        self.before = snapshot(self.target)
        return self

    def __exit__(self, *args):
        assert self.root.resolve().is_relative_to(TMP.resolve())
        self.temp.cleanup()


class BundleTests(unittest.TestCase):
    def test_verified_snapshot_stages_only_manifested_code(self):
        with Fixture() as f:
            stage = f.root / "stage"
            m = verify_and_stage(f.path, f.evidence, stage)
            self.assertEqual(m["source_sha"], A)
            self.assertEqual({p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file()}, set(f.payload) | {"manifest.json"})
            self.assertEqual(snapshot(f.target), f.before)

    def test_digest_size_and_manifest_evidence_are_required(self):
        with Fixture() as f:
            bad = ReleaseEvidence(A, 3, 1, 2, 4, 5, "0" * 64, f.evidence.artifact_size, 6)
            with self.assertRaisesRegex(DeliveryError, "artifact_integrity"):
                verify_and_stage(f.path, bad, f.root / "stage")
            p, e = artifact(f.root, edit_manifest=lambda m: m.update(source_sha=B))
            with self.assertRaisesRegex(DeliveryError, "manifest_evidence_mismatch"):
                verify_and_stage(p, e, f.root / "stage")

    def test_protected_paths_traversal_ads_and_case_duplicates_are_rejected(self):
        paths = ["../outside.py", "app/viewer/../../settings.json", "app/settings.json", "app/projects/a/world.yaml",
                 "app/viewer/a.py:secret", "app/viewer/CON.py", "app/viewer/trailing. ", "app\\viewer\\x.py",
                 "app/viewer/server.py"]
        for rel in paths:
            with self.subTest(path=rel), Fixture() as f:
                p, e = artifact(f.root, extra=[(rel, b"untrusted")])
                with self.assertRaises(DeliveryError):
                    verify_and_stage(p, e, f.root / "stage")
                self.assertFalse((f.root / "stage").exists())
                self.assertEqual(snapshot(f.target), f.before)
        with Fixture() as f:
            p, e = artifact(f.root, extra=[("app/viewer/SERVER.py", b"alias")])
            with self.assertRaises(DeliveryError):
                verify_and_stage(p, e, f.root / "stage")

    def test_symlink_and_unknown_members_are_rejected(self):
        with Fixture() as f:
            info = zipfile.ZipInfo("app/viewer/linked.py")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            p, e = artifact(f.root, extra=[(info, b"../../../outside")])
            with self.assertRaisesRegex(DeliveryError, "linked_zip_entry"):
                verify_and_stage(p, e, f.root / "stage")
        with Fixture() as f:
            p, e = artifact(f.root, extra=[("app/viewer/unlisted.py", b"x")])
            with self.assertRaisesRegex(DeliveryError, "zip_manifest_members"):
                verify_and_stage(p, e, f.root / "stage")

    def test_member_hash_migration_unknown_adapter_and_duplicate_json_keys(self):
        edits = [lambda m: m["files"][0].update(sha256="0" * 64),
                 lambda m: m["data"].update(requires_migration=True),
                 lambda m: m["data"].update(adapter="future-unknown")]
        for edit in edits:
            with Fixture() as f:
                p, e = artifact(f.root, edit_manifest=edit)
                with self.assertRaises(DeliveryError):
                    verify_and_stage(p, e, f.root / "stage")
        with Fixture() as f:
            p, e = artifact(f.root, raw_manifest=b'{"schema_version":1,"schema_version":1}')
            with self.assertRaisesRegex(DeliveryError, "duplicate_json_key"):
                verify_and_stage(p, e, f.root / "stage")


class InstallerTests(unittest.TestCase):
    def assert_preserved(self, f):
        self.assertEqual(protected_snapshot(f.target), f.protected)

    def test_install_and_explicit_rollback_preserve_data_and_remove_only_managed_files(self):
        with Fixture() as f:
            result = f.installer.install(f.path, f.evidence)
            self.assertEqual(result["status"], "installed")
            self.assertFalse((f.target / "app/viewer/obsolete.py").exists())
            for rel, blob in f.payload.items():
                self.assertEqual((f.target / rel).read_bytes(), blob)
            self.assert_preserved(f)
            self.assertEqual(f.installer.install(f.path, f.evidence)["status"], "already_installed")
            f.installer.rollback(result["transaction"])
            self.assertEqual(snapshot(f.target), f.before)
            with self.assertRaisesRegex(DeliveryError, "release_on_hold"):
                f.installer.install(f.path, f.evidence)
            with self.assertRaisesRegex(DeliveryError, "hold_confirmation_required"):
                f.installer.clear_hold(A)
            f.installer.clear_hold(A, confirmed=True)
            self.assertEqual(f.installer.install(f.path, f.evidence)["status"], "installed")

    def test_busy_jobs_unknown_runtime_and_default_adapters_never_write_app(self):
        for probe in (None, lambda _: {}, lambda _: dict(app_running=True, jobs_running=False),
                      lambda _: dict(app_running=False, jobs_running=True), lambda _: dict(app_running=None, jobs_running=False)):
            with Fixture() as f:
                f.installer.runtime_probe = probe
                with self.assertRaises(DeliveryError):
                    f.installer.install(f.path, f.evidence)
                self.assertEqual(snapshot(f.target), f.before)
        with Fixture() as f:
            f.installer.compatibility = None
            with self.assertRaisesRegex(DeliveryError, "compatibility_adapter_not_configured"):
                f.installer.install(f.path, f.evidence)
            self.assertEqual(snapshot(f.target), f.before)

    def test_local_changes_unmanaged_collisions_and_incompatible_data_are_kept(self):
        with Fixture() as f:
            (f.target / "app/viewer/server.py").write_bytes(b"unpublished local edit")
            before = snapshot(f.target)
            with self.assertRaisesRegex(DeliveryError, "local_code_edits"):
                f.installer.install(f.path, f.evidence)
            self.assertEqual(snapshot(f.target), before)
        with Fixture() as f:
            (f.target / "app/viewer/new.py").write_bytes(b"local untracked")
            before = snapshot(f.target)
            with self.assertRaisesRegex(DeliveryError, "unmanaged_file_collision"):
                f.installer.install(f.path, f.evidence)
            self.assertEqual(snapshot(f.target), before)
        with Fixture() as f:
            f.installer.compatibility = lambda *_: False
            with self.assertRaisesRegex(DeliveryError, "data_incompatible"):
                f.installer.install(f.path, f.evidence)
            self.assertEqual(snapshot(f.target), f.before)

    def test_first_sha_and_forward_order_are_explicit(self):
        with Fixture() as f:
            p, e = artifact(f.root, sha=B)
            with self.assertRaisesRegex(DeliveryError, "first_sha_not_approved"):
                f.installer.install(p, e)
            f.installer.install(f.path, f.evidence)
            before = snapshot(f.target)
            f.installer.forward_check = lambda *_: False
            with self.assertRaisesRegex(DeliveryError, "not_forward_update"):
                f.installer.install(p, e)
            self.assertEqual(snapshot(f.target), before)

    def test_ordinary_failures_at_every_write_restore_exact_old_code(self):
        count = len(set(contents("old")) | {"app/viewer/obsolete.py", "app/viewer/new.py"})
        for phase in ("before_write", "after_write"):
            for position in range(count):
                with self.subTest(phase=phase, position=position), Fixture() as f:
                    def fault(at, index):
                        if (at, index) == (phase, position):
                            raise OSError("injected write failure")
                    f.installer.fault = fault
                    with self.assertRaisesRegex(DeliveryError, "install_failed_rolled_back"):
                        f.installer.install(f.path, f.evidence)
                    self.assertEqual(snapshot(f.target), f.before)
                    self.assertEqual(f.installer._receipt()["kind"], "local")

    def test_abrupt_interruption_after_write_or_receipt_requires_and_supports_recovery(self):
        class Crash(BaseException):
            pass
        for when in ("after_write", "after_receipt"):
            with self.subTest(when=when), Fixture() as f:
                def fault(at, index):
                    if at == when:
                        raise Crash()
                f.installer.fault = fault
                with self.assertRaises(Crash):
                    f.installer.install(f.path, f.evidence)
                f.installer.fault = lambda *_: None
                with self.assertRaisesRegex(DeliveryError, "recovery_required"):
                    f.installer.install(f.path, f.evidence)
                f.installer.recover()
                self.assertEqual(snapshot(f.target), f.before)

    def test_corrupt_backup_and_post_update_local_edits_are_not_overwritten(self):
        class Crash(BaseException):
            pass
        with Fixture() as f:
            f.installer.fault = lambda at, _: (_ for _ in ()).throw(Crash()) if at == "after_write" else None
            with self.assertRaises(Crash):
                f.installer.install(f.path, f.evidence)
            tx = f.installer._pending()[0]
            backup = next((f.state / "transactions" / tx / "backup").rglob("*.py"))
            backup.write_bytes(b"corrupted backup")
            before = snapshot(f.target)
            with self.assertRaisesRegex(DeliveryError, "backup_integrity"):
                f.installer.recover()
            self.assertEqual(snapshot(f.target), before)
        with Fixture() as f:
            result = f.installer.install(f.path, f.evidence)
            (f.target / "app/viewer/server.py").write_bytes(b"new local edit")
            before = snapshot(f.target)
            with self.assertRaisesRegex(DeliveryError, "local_code_edits"):
                f.installer.rollback(result["transaction"])
            self.assertEqual(snapshot(f.target), before)

    def test_update_lease_and_changed_userdata_are_respected(self):
        with Fixture() as f:
            with lease(f.state / "update.lock", exclusive=False):
                with self.assertRaisesRegex(DeliveryError, "in_use"):
                    f.installer.install(f.path, f.evidence)
            self.assertEqual(snapshot(f.target), f.before)
        with Fixture() as f:
            def bad_adapter(*_):
                (f.target / "app/settings.json").write_bytes(b"concurrent user change")
                return True
            f.installer.compatibility = bad_adapter
            with self.assertRaisesRegex(DeliveryError, "data_incompatible_or_changed"):
                f.installer.install(f.path, f.evidence)
            self.assertEqual((f.target / "app/settings.json").read_bytes(), b"concurrent user change")
            for rel, b in f.old.items():
                self.assertEqual((f.target / rel).read_bytes(), b)

    def test_recovery_cannot_claim_success_with_edit_to_unchanged_managed_file(self):
        class Crash(BaseException):
            pass
        with Fixture() as f:
            f.payload["app/viewer/server.py"] = f.old["app/viewer/server.py"]
            path, evidence = artifact(f.root, f.payload)
            f.installer.fault = lambda at, _: (_ for _ in ()).throw(Crash()) if at == "after_write" else None
            with self.assertRaises(Crash):
                f.installer.install(path, evidence)
            (f.target / "app/viewer/server.py").write_bytes(b"external edit after interruption")
            before = snapshot(f.target)
            with self.assertRaisesRegex(DeliveryError, "recovery_code_conflict"):
                f.installer.recover()
            self.assertEqual(snapshot(f.target), before)
            self.assertTrue(f.installer._pending())

    def test_staging_parent_link_is_rejected_before_any_external_stage_write(self):
        with Fixture() as f:
            outside = f.root / "outside-stage"
            outside.mkdir()
            try:
                (f.state / "staging").symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("Temporary directory symlink unavailable")
            with self.assertRaises(DeliveryError):
                f.installer.install(f.path, f.evidence)
            self.assertEqual(list(outside.iterdir()), [])
            self.assertEqual(snapshot(f.target), f.before)

    def test_rollback_rechecks_later_targets_before_overwriting_them(self):
        from unittest.mock import patch
        from wb_delivery import transaction
        with Fixture() as f:
            result = f.installer.install(f.path, f.evidence)
            victim = "app/viewer/app_desktop.py"
            real_write = transaction.atomic_write
            injected = False
            def write(root, rel, blob, **kwargs):
                nonlocal injected
                answer = real_write(root, rel, blob, **kwargs)
                if Path(root) == f.target and not injected:
                    injected = True
                    (f.target / victim).write_bytes(b"external edit during restore")
                return answer
            with patch.object(transaction, "atomic_write", side_effect=write):
                with self.assertRaisesRegex(DeliveryError, "recovery_code_conflict"):
                    f.installer.rollback(result["transaction"])
            self.assertEqual((f.target / victim).read_bytes(), b"external edit during restore")
            self.assertTrue(f.installer._pending())

    def test_linked_target_and_state_overlap_are_rejected(self):
        with Fixture() as f:
            with self.assertRaisesRegex(DeliveryError, "overlapping_state"):
                Installer(f.target, f.target / "updates")
            link = f.root / "linked"
            try:
                link.symlink_to(f.target, target_is_directory=True)
            except OSError as exc:
                self.skipTest("Temporary symlink unavailable: " + str(exc.winerror if hasattr(exc, 'winerror') else exc.errno))
            with self.assertRaisesRegex(DeliveryError, "linked_root"):
                Installer(link, f.state)


class LeaseTests(unittest.TestCase):
    def test_shared_readers_and_exclusive_writer_across_processes(self):
        with tempfile.TemporaryDirectory(dir=TMP) as temp:
            lock = Path(temp) / "update.lock"
            script = "from wb_delivery.lease import lease\nfrom wb_delivery.errors import DeliveryError\nimport sys\ntry:\n with lease(sys.argv[1],exclusive=sys.argv[2]=='exclusive'): print('held')\nexcept DeliveryError as e: print(e.code)"
            def probe(mode):
                p = subprocess.run([sys.executable, "-B", "-c", script, str(lock), mode], capture_output=True, text=True, timeout=10)
                self.assertEqual(p.returncode, 0, p.stderr)
                return p.stdout.strip()
            with lease(lock, exclusive=False):
                self.assertEqual(probe("shared"), "held")
                self.assertEqual(probe("exclusive"), "in_use")
            with lease(lock, exclusive=True):
                self.assertEqual(probe("shared"), "in_use")
                self.assertEqual(probe("exclusive"), "in_use")
            self.assertEqual(probe("exclusive"), "held")


if __name__ == "__main__":
    unittest.main()
