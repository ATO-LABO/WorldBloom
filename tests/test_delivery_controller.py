import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from test_delivery_installer import Fixture, A, snapshot, TMP
from wb_delivery.contract import DATA_ADAPTER, ReleaseEvidence, digest
from wb_delivery.controller import Controller
from wb_delivery.errors import DeliveryError
from wb_delivery.preflight import CopiedDataValidator, WindowsRuntimeProbe
from wb_delivery.runtime_guard import hold_runtime_lease, run_guarded
from wb_delivery.lease import lease


class FakeReader:
    def __init__(self, fixture):
        self.fixture = fixture
        self.calls = []

    def resolve(self, sha=None):
        self.calls.append("resolve")
        return self.fixture.evidence

    def compare(self, *args):
        self.calls.append("compare")
        return True

    def download(self, evidence, path):
        self.calls.append("download")
        with path.open("xb") as out:
            out.write(self.fixture.path.read_bytes())
        return path


def controller(f, *, busy=False, config=None, reader=None):
    r = reader or FakeReader(f)
    c = dict(enabled=True, allow_apply=True, target=str(f.target), state=str(f.state))
    c.update(config or {})
    return Controller(c, reader_factory=lambda _: r,
        runtime_probe=lambda _: dict(app_running=busy, jobs_running=False), compatibility=lambda *_: True), r


class ControllerTests(unittest.TestCase):
    def test_disabled_import_cli_and_apply_flag_do_not_start_network_or_processes(self):
        def forbidden(*_):
            self.fail("disabled updater created a reader")
        self.assertEqual(Controller({}, reader_factory=forbidden).check_once(), {"status": "disabled", "can_launch": True})
        p = subprocess.run([sys.executable, "-B", "-m", "wb_delivery"], capture_output=True, text=True, timeout=10)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["status"], "disabled")
        with Fixture() as f:
            c, r = controller(f, config={"allow_apply": False})
            self.assertEqual(c.check_once(apply_update=True)["status"], "apply_disabled")
            self.assertEqual(r.calls, [])

    def test_prepare_idle_apply_cached_digest_and_already_current(self):
        with Fixture() as f:
            c, r = controller(f)
            self.assertEqual(c.check_once()["status"], "available")
            self.assertEqual(snapshot(f.target), f.before)
            self.assertEqual(c.check_once(apply_update=True)["status"], "installed")
            self.assertEqual(r.calls.count("download"), 1)
            self.assertEqual(c.check_once()["status"], "current")
            f.installer.rollback(f.installer._receipt()["transaction"])
            self.assertEqual(c.check_once(apply_update=True)["status"], "rollback_hold")

    def test_busy_and_no_enrollment_do_not_apply_or_auto_enroll(self):
        with Fixture() as f:
            c, _ = controller(f, busy=True)
            self.assertEqual(c.check_once(apply_update=True)["status"], "pending_in_use")
            self.assertEqual(snapshot(f.target), f.before)
            (f.state / "installed.json").unlink()
            c, r = controller(f)
            self.assertEqual(c.check_once()["status"], "first_enrollment_required")
            self.assertFalse((f.state / "installed.json").exists())
            self.assertNotIn("download", r.calls)

    def test_offline_or_bad_artifact_keeps_current_app_and_never_uses_partial_download(self):
        with Fixture() as f:
            r = FakeReader(f)
            def fail(*_):
                raise DeliveryError("github_unavailable")
            r.resolve = fail
            c, _ = controller(f, reader=r)
            self.assertEqual(c.check_once(), {"status": "update_unavailable", "code": "github_unavailable", "can_launch": True})
            self.assertEqual(snapshot(f.target), f.before)
        with Fixture() as f:
            c, r = controller(f)
            c.check_once()
            next((f.state / "downloads").glob("*.zip")).write_bytes(b"tampered cache")
            result = c.check_once(apply_update=True)
            self.assertEqual(result["status"], "artifact_unavailable")
            self.assertTrue(result["can_launch"])
            self.assertEqual(snapshot(f.target), f.before)

    def test_pending_recovery_blocks_launch_before_reader_is_created_or_requested(self):
        with Fixture() as f:
            folder = f.state / "transactions" / ("e" * 32)
            folder.mkdir(parents=True)
            (folder / "journal.json").write_text('{"status":"applying"}', encoding="utf8")
            c, r = controller(f)
            self.assertEqual(c.check_once(), {"status": "recovery_required", "can_launch": False})
            self.assertEqual(r.calls, [])

    def test_runtime_guard_is_disabled_by_default_and_holds_shared_lease_when_enabled(self):
        with tempfile.TemporaryDirectory(dir=TMP) as temp:
            state = Path(temp) / "updates"
            self.assertEqual(run_guarded(lambda: "old-app", environment={}), "old-app")
            self.assertFalse(state.exists())
            with hold_runtime_lease(enabled=True, state=state):
                with self.assertRaisesRegex(DeliveryError, "in_use"):
                    with lease(state / "update.lock", exclusive=True):
                        pass
            with lease(state / "update.lock", exclusive=True):
                with self.assertRaisesRegex(DeliveryError, "in_use"):
                    run_guarded(lambda: self.fail("app started during update"), environment={
                        "WORLDBLOOM_DELIVERY_GUARD": "1", "WORLDBLOOM_DELIVERY_STATE": str(state)})

    def test_runtime_guard_blocks_interrupted_or_invalid_journal_before_app_load(self):
        with tempfile.TemporaryDirectory(dir=TMP) as temp:
            state = Path(temp) / "updates"
            folder = state / "transactions" / ("a" * 32)
            folder.mkdir(parents=True)
            journal = folder / "journal.json"
            environment = {"WORLDBLOOM_DELIVERY_GUARD": "1", "WORLDBLOOM_DELIVERY_STATE": str(state)}
            for content in ('{"status":"applying"}', '{"status":"rolling_back"}',
                            '{"status":"recovery_required"}', '{}', 'null', 'not-json',
                            '{"status":"applying","status":"committed"}',
                            '{"status":"committed","other":NaN}'):
                with self.subTest(content=content):
                    journal.write_text(content, encoding="utf-8")
                    with self.assertRaises(DeliveryError):
                        run_guarded(lambda: self.fail("app loaded incomplete code"), environment=environment)
                    with lease(state / "update.lock", exclusive=True):
                        pass
            for status in ("committed", "rolled_back"):
                journal.write_text(json.dumps({"status": status}), encoding="utf-8")
                self.assertEqual(run_guarded(lambda: "allowed", environment=environment), "allowed")
            journal.unlink()
            self.assertEqual(run_guarded(lambda: "staging-only", environment=environment), "staging-only")

    def test_runtime_guard_rejects_invalid_store_and_oversize_journal(self):
        import io
        from wb_delivery.contract import MAX_MANIFEST
        with tempfile.TemporaryDirectory(dir=TMP) as temp:
            state = Path(temp) / "updates"
            state.mkdir()
            store = state / "transactions"
            store.write_bytes(b"not-a-directory")
            with self.assertRaisesRegex(DeliveryError, "invalid_transaction_store"):
                with hold_runtime_lease(enabled=True, state=state):
                    self.fail("invalid transaction store accepted")
            store.unlink()
            folder = store / ("a" * 32)
            folder.mkdir(parents=True)
            journal = folder / "journal.json"
            journal.write_bytes(b" " * (MAX_MANIFEST + 1))
            with self.assertRaisesRegex(DeliveryError, "invalid_journal"):
                with hold_runtime_lease(enabled=True, state=state):
                    self.fail("oversize journal accepted")
            journal.write_bytes(b'{"status":"committed"}')
            original_open = Path.open
            def grew_after_stat(path, *args, **kwargs):
                if path == journal:
                    return io.BytesIO(b'{"status":"committed"}' + b" " * MAX_MANIFEST)
                return original_open(path, *args, **kwargs)
            with patch.object(Path, "open", grew_after_stat), self.assertRaisesRegex(DeliveryError, "invalid_journal"):
                with hold_runtime_lease(enabled=True, state=state):
                    self.fail("journal growth after stat exceeded the bounded read")

    def test_packager_bundle_is_accepted_by_updater_without_source_userdata(self):
        import test_delivery_packager as packtests
        from wb_delivery.packager import pack_bundle
        p = packtests.PackagerTests()
        p.setUp()
        try:
            out = p.root / "bundle-integration"
            manifest = pack_bundle(p.repo, p.exe, out, p.context)
            archive = p.root / "github-artifact.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
                for item in out.rglob("*"):
                    if item.is_file():
                        z.write(item, item.relative_to(out).as_posix())
            with Fixture() as f:
                baseline = f.installer._receipt()
                baseline["allowed_first_sha"] = p.sha
                f.installer._save("installed.json", baseline)
                f.path = archive
                f.evidence = ReleaseEvidence(p.sha, 20, 1, 10, 3, 55, digest(archive.read_bytes()), archive.stat().st_size, 6)
                c, _ = controller(f)
                result = c.check_once(apply_update=True)
                self.assertEqual(result["status"], "installed")
                self.assertEqual(result["sha"], p.sha)
                self.assertEqual((f.target / "app/projects/story/world.yaml").read_bytes(), b"fixture-user-data-preserve")
                self.assertFalse((f.target / "app/projects/private").exists())
        finally:
            p.tearDown()


class PreflightTests(unittest.TestCase):
    def prepare(self, root, mode="valid"):
        target, stage = root / "portable", root / "stage"
        for folder in [target / "app/projects/story", target / "app/templates/genre", stage / "app/execution"]:
            folder.mkdir(parents=True)
        (target / "app/projects/story/world.yaml").write_text("valid", encoding="utf8")
        (target / "app/templates/genre/rules.yaml").write_text("valid", encoding="utf8")
        (target / "app/settings.json").write_text("unshipped-sensitive-fixture", encoding="utf8")
        (stage / "app/execution/__init__.py").write_text("", encoding="utf8")
        (stage / "app/execution/library.py").write_text('''from pathlib import Path
class LibraryStore:
 def __init__(self, repo): self.repo=Path(repo)
 def worlds(self): return [{'id':p.name} for p in (self.repo/'projects').iterdir()]
 def genres(self): return [{'id':p.name} for p in (self.repo/'templates').iterdir()]
''', encoding="utf8")
        body = "assert (store.repo/'projects'/ident/'world.yaml').read_text()=='valid'"
        if mode == "mutate":
            body = "(store.repo/'projects'/ident/'world.yaml').write_text('changed copy')"
        if mode == "network":
            body = "import socket; socket.create_connection(('example.invalid',443))"
        (stage / "app/execution/world_editor.py").write_text("def snapshot(store,ident):\n " + body + "\n", encoding="utf8")
        (stage / "app/execution/genre_editor.py").write_text("def snapshot(store,ident):\n assert (store.repo/'templates'/ident/'rules.yaml').read_text()=='valid'\n", encoding="utf8")
        return target, stage

    def test_candidate_reads_copies_and_never_receives_live_settings_or_credentials(self):
        with tempfile.TemporaryDirectory(dir=TMP) as temp:
            root = Path(temp)
            target, stage = self.prepare(root)
            seen = {}
            def runner(args, **kwargs):
                seen.update(kwargs)
                seen["args"] = args
                return subprocess.run(args, **kwargs)
            validator = CopiedDataValidator(root / "validation", runner=runner)
            with patch.dict("os.environ", {"GH_TOKEN": "fixture-no-leak", "OPENAI_API_KEY": "fixture-no-leak"}):
                self.assertTrue(validator(stage, target, {"data": {"adapter": DATA_ADAPTER, "requires_migration": False}}))
            self.assertNotIn("GH_TOKEN", seen["env"])
            self.assertNotIn("OPENAI_API_KEY", seen["env"])
            self.assertNotIn(str(target), seen["args"])
            self.assertFalse(list((root / "validation").rglob("settings.json")))
            self.assertEqual((target / "app/settings.json").read_text(), "unshipped-sensitive-fixture")

    def test_candidate_accidental_migration_network_and_invalid_data_are_rejected(self):
        for mode in ("mutate", "network", "invalid"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir=TMP) as temp:
                root = Path(temp)
                target, stage = self.prepare(root, mode)
                if mode == "invalid":
                    (target / "app/projects/story/world.yaml").write_text("invalid", encoding="utf8")
                before = snapshot(target)
                self.assertFalse(CopiedDataValidator(root / "validation")(stage, target,
                    {"data": {"adapter": DATA_ADAPTER, "requires_migration": False}}))
                self.assertEqual(snapshot(target), before)

    def test_runtime_probe_is_read_only_and_unknown_process_or_job_state_is_not_idle(self):
        with tempfile.TemporaryDirectory(dir=TMP) as temp:
            target = Path(temp)
            calls = []
            def runner(args, **kwargs):
                calls.append((args, kwargs))
                return SimpleNamespace(returncode=0, stdout='[]')
            with patch("wb_delivery.preflight.IS_WINDOWS", True):
                probe = WindowsRuntimeProbe(runner=runner)
                self.assertEqual(probe(target), {"app_running": False, "jobs_running": False})
                self.assertIn("Get-CimInstance", calls[0][0][-1])
                self.assertNotIn("Stop-Process", calls[0][0][-1])
                job = target / "control/jobs/job/job.json"
                job.parent.mkdir(parents=True)
                job.write_text('{"state":"running"}', encoding="utf8")
                self.assertTrue(probe(target)["jobs_running"])
                job.write_text('{"state":"partial"}', encoding="utf8")
                self.assertFalse(probe(target)["jobs_running"])
                job.write_text('null', encoding="utf8")
                with self.assertRaisesRegex(DeliveryError, "jobs_unknown"):
                    probe(target)


if __name__ == "__main__":
    unittest.main()
