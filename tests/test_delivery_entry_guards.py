"""Offline entry guards: no GUI, HTTP, actual jobs, or LLM execution."""
import ast
from contextlib import contextmanager
import hashlib
import importlib.util
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wb_delivery.errors import DeliveryError
from wb_delivery.lease import lease
from execution import worker
from execution.jobs import JobStore
from execution.provenance import ConfigError

with patch.dict(sys.modules, {"webview": SimpleNamespace()}):
    spec = importlib.util.spec_from_file_location("delivery_desktop_entry", ROOT / "viewer/app_desktop.py")
    desktop = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(desktop)

GUARD = "WORLDBLOOM_DELIVERY_GUARD"
STATE = "WORLDBLOOM_DELIVERY_STATE"
APP = "WORLDBLOOM_DELIVERY_APP_ROOT"
WORKSPACE_ROOT = ROOT.parent.parent.resolve()
FIXTURE_ROOT = ROOT.parent / "entry-test-fixtures"


class DeliveryEntryTests(unittest.TestCase):
    def setUp(self):
        if not FIXTURE_ROOT.resolve().is_relative_to(WORKSPACE_ROOT):
            raise RuntimeError("fixture root escapes the authorized workspace")
        FIXTURE_ROOT.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="wb-delivery-entry-", dir=FIXTURE_ROOT)
        self.base = Path(self.temp.name).resolve()
        self.app = self.base / "portable/app"
        self.app.mkdir(parents=True)
        (self.app / "viewer").mkdir()
        (self.app / "samples").mkdir()
        self.state = self.base / "updates"
        environment = dict(os.environ)
        for name in (GUARD, STATE, APP):
            environment.pop(name, None)
        self.environment = patch.dict(os.environ, environment, clear=True)
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.assertTrue(self.base.is_relative_to(FIXTURE_ROOT.resolve()))
        self.assertTrue(FIXTURE_ROOT.resolve().is_relative_to(WORKSPACE_ROOT))
        self.temp.cleanup()

    def enable(self, state=None):
        os.environ[GUARD] = "1"
        os.environ[STATE] = str(self.state if state is None else state)

    def assert_shared(self):
        with self.assertRaises(DeliveryError) as caught:
            with lease(self.state / "update.lock", exclusive=True):
                self.fail("exclusive update entered while runtime was active")
        self.assertEqual(caught.exception.code, "in_use")

    def assert_released(self):
        with lease(self.state / "update.lock", exclusive=True):
            pass

    def desktop_context(self):
        return patch.object(desktop, "resolve_app_root", return_value=self.app)

    def test_desktop_disabled_preserves_old_main_and_does_not_resolve_roots(self):
        os.environ[STATE] = "not absolute and ignored while disabled"
        for value in (None, "0"):
            with self.subTest(mode=value):
                if value is None:
                    os.environ.pop(GUARD, None)
                else:
                    os.environ[GUARD] = value
                with patch.object(desktop, "_main", return_value=37) as body, \
                     patch.object(desktop, "_delivery_context") as context:
                    self.assertEqual(desktop.main(), 37)
                    body.assert_called_once_with()
                    context.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_desktop_holds_lease_through_main_and_releases_after_return(self):
        self.enable()
        def body():
            self.assert_shared()
            return 42
        with self.desktop_context(), patch.object(desktop, "_main", side_effect=body), \
             patch.object(desktop, "_show_error_native") as message:
            self.assertEqual(desktop.main(), 42)
            message.assert_not_called()
        self.assert_released()

    def test_desktop_busy_never_enters_old_main_or_external_imports(self):
        self.enable()
        with self.desktop_context(), lease(self.state / "update.lock", exclusive=True), \
             patch.object(desktop, "_main") as body, patch.object(desktop, "_show_error_native") as message, \
             patch.object(desktop.importlib, "import_module") as external:
            self.assertEqual(desktop.main(), 1)
            body.assert_not_called()
            external.assert_not_called()
            self.assertIn("更新中", message.call_args.args[0])

    def test_desktop_invalid_state_modes_and_reparse_fail_before_main(self):
        for state in ("relative", self.app, self.app / "nested", self.base, self.app.parent / "updates"):
            self.enable(state)
            with self.subTest(state=str(state)), self.desktop_context(), \
                 patch.object(desktop, "_main") as body, patch.object(desktop, "_show_error_native") as message:
                self.assertEqual(desktop.main(), 1)
                body.assert_not_called()
                message.assert_called_once()
        self.enable()
        os.environ[GUARD] = "invalid"
        with self.desktop_context(), patch.object(desktop, "_main") as body, \
             patch.object(desktop, "_show_error_native"):
            self.assertEqual(desktop.main(), 1)
            body.assert_not_called()
        self.enable()
        original = getattr(Path, "is_junction", lambda _: False)
        with self.desktop_context(), patch.object(Path, "is_junction", lambda p: p == self.state or original(p), create=True), \
             patch.object(desktop, "_main") as body, patch.object(desktop, "_show_error_native"):
            self.assertEqual(desktop.main(), 1)
            body.assert_not_called()

    def test_desktop_guard_errors_do_not_start_main_and_body_exception_releases(self):
        self.enable()
        with patch.object(desktop, "_delivery_context", side_effect=PermissionError("fixture")), \
             patch.object(desktop, "_main") as body, patch.object(desktop, "_show_error_native"):
            self.assertEqual(desktop.main(), 1)
            body.assert_not_called()
        with self.desktop_context(), patch.object(desktop, "_main", side_effect=RuntimeError("body")), \
             patch.object(desktop, "_show_error_native") as message:
            with self.assertRaisesRegex(RuntimeError, "body"):
                desktop.main()
            message.assert_not_called()
        self.assert_released()

    def test_desktop_frozen_guard_is_statically_importable_and_restores_path(self):
        tree = ast.parse((ROOT / "viewer/app_desktop.py").read_text("utf8"))
        self.assertTrue(any(isinstance(node, ast.ImportFrom) and node.module == "wb_delivery.runtime_guard" for node in ast.walk(tree)))
        self.enable()
        with patch.object(desktop.sys, "frozen", True, create=True), \
             patch.object(desktop, "_exe_dir", return_value=self.app.parent), self.desktop_context(), \
             patch.object(desktop, "_main", side_effect=lambda: self.assert_shared() or 0):
            before = list(sys.path)
            self.assertEqual(desktop.main(), 0)
            self.assertEqual(sys.path, before)

    def test_worker_disabled_preserves_main_and_does_not_create_state(self):
        os.environ[STATE] = "relative ignored"
        with patch.object(worker, "_main", return_value=31) as body:
            self.assertEqual(worker.main(["--repo", "not a real directory"]), 31)
            body.assert_called_once_with(["--repo", "not a real directory"])
        self.assertIsNone(worker.delivery_environment("ignored"))
        self.assertFalse(self.state.exists())

    def test_worker_holds_shared_lease_and_scopes_internal_root_environment(self):
        self.enable()
        os.environ[APP] = "outer-value-to-preserve"
        def body(argv):
            self.assert_shared()
            self.assertEqual(os.environ[APP], str(self.app.resolve()))
            return 0
        with patch.object(worker, "_main", side_effect=body):
            self.assertEqual(worker.main(["--repo", str(self.app)]), 0)
        self.assertEqual(os.environ[APP], "outer-value-to-preserve")
        self.assert_released()

    def test_worker_busy_invalid_or_unknown_guard_never_runs_job_body(self):
        self.enable()
        with lease(self.state / "update.lock", exclusive=True), patch.object(worker, "_main") as body:
            self.assertEqual(worker.main(["--repo", str(self.app)]), 2)
            body.assert_not_called()
        for state in ("relative", self.app, self.base, self.app.parent / "updates"):
            self.enable(state)
            with self.subTest(state=str(state)), patch.object(worker, "_main") as body:
                self.assertEqual(worker.main(["--repo", str(self.app)]), 2)
                body.assert_not_called()
        self.enable()
        os.environ[GUARD] = "invalid"
        with patch.object(worker, "_main") as body:
            self.assertEqual(worker.main(["--repo", str(self.app)]), 2)
            body.assert_not_called()

    def test_worker_body_exception_releases_lease_and_restores_environment(self):
        self.enable()
        with patch.object(worker, "_main", side_effect=RuntimeError("fixture body")):
            with self.assertRaisesRegex(RuntimeError, "fixture body"):
                worker.main(["--repo", str(self.app)])
        self.assertNotIn(APP, os.environ)
        self.assert_released()

    def test_linux_guard_entry_keeps_lease_for_drain_and_uses_verified_parent_root(self):
        self.enable()
        os.environ[APP] = str(self.app)
        def drain(*args):
            self.assertEqual(args, ("registry", 7, 8))
            self.assert_shared()
            return 0
        with patch.object(worker, "_linux_guard", side_effect=drain):
            self.assertEqual(worker.guarded_linux_guard("registry", 7, 8), 0)
        self.assert_released()
        os.environ.pop(APP)
        with patch.object(worker, "_linux_guard") as body:
            self.assertEqual(worker.guarded_linux_guard("registry", 7, 8), 2)
            body.assert_not_called()

    def test_admission_guard_blocks_queued_job_publication_before_original_submit(self):
        store = object.__new__(JobStore)
        store.configs = SimpleNamespace(repo=self.app)
        self.enable()
        with lease(self.state / "update.lock", exclusive=True), \
             patch.object(store, "_submit_without_delivery") as body:
            with self.assertRaises(ConfigError) as caught:
                store.submit({"fixture": True})
            self.assertEqual(caught.exception.code, "unavailable")
            body.assert_not_called()
        os.environ[STATE] = str(self.app)
        with patch.object(store, "_submit_without_delivery") as body:
            with self.assertRaises(ConfigError):
                store.submit({"fixture": True})
            body.assert_not_called()

    def test_admission_preserves_original_signature_and_parent_global_environment(self):
        store = object.__new__(JobStore)
        store.configs = SimpleNamespace(repo=self.app)
        request = {"fixture": True}
        with patch.object(store, "_submit_without_delivery", return_value=("result", True)) as body:
            self.assertEqual(store.submit(request, settings_path="fixture-settings"), ("result", True))
            body.assert_called_once_with(request, settings_path="fixture-settings")
        self.enable()
        os.environ[APP] = "parent-marker"
        def submit(*args, **kwargs):
            self.assert_shared()
            self.assertEqual(os.environ[APP], "parent-marker")
            return ("guarded", True)
        with patch.object(store, "_submit_without_delivery", side_effect=submit):
            self.assertEqual(store.submit(request), ("guarded", True))
        self.assertEqual(os.environ[APP], "parent-marker")

    def test_child_environment_copies_guard_without_mutating_parent(self):
        self.enable()
        os.environ[APP] = "parent-marker"
        before = dict(os.environ)
        child = worker.delivery_environment(self.app)
        self.assertEqual(dict(os.environ), before)
        self.assertEqual(child[GUARD], "1")
        self.assertEqual(child[STATE], str(self.state))
        self.assertEqual(child[APP], str(self.app.resolve()))

    def journal(self, raw):
        folder = self.state / "transactions/fixture-transaction"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "journal.json"
        path.write_bytes(raw)
        return path

    def assert_all_entries_blocked(self):
        store = object.__new__(JobStore)
        store.configs = SimpleNamespace(repo=self.app)
        with self.desktop_context(), patch.object(desktop, "_main") as app, \
             patch.object(desktop, "_show_error_native"), patch.object(worker, "_main") as job, \
             patch.object(store, "_submit_without_delivery") as admission:
            self.assertEqual(desktop.main(), 1)
            self.assertEqual(worker.main(["--repo", str(self.app)]), 2)
            with self.assertRaises(ConfigError):
                store.submit({"fixture": True})
            app.assert_not_called()
            job.assert_not_called()
            admission.assert_not_called()

    def test_pending_or_unknown_journal_blocks_desktop_worker_and_admission(self):
        self.enable()
        for raw in (b'{"status":"applying"}', b'{"status":"recovery_required"}',
                    b'{"status":"unknown"}', b'{}', b'{"status":null}'):
            with self.subTest(raw=raw):
                self.journal(raw)
                self.assert_all_entries_blocked()
        self.assert_released()

    def test_terminal_journals_and_abandoned_no_journal_folder_allow_entries(self):
        self.enable()
        (self.state / "transactions/staging-without-journal").mkdir(parents=True)
        store = object.__new__(JobStore)
        store.configs = SimpleNamespace(repo=self.app)
        for raw in (b'{"status":"committed"}', b'{"status":"rolled_back"}'):
            self.journal(raw)
            with self.subTest(raw=raw), self.desktop_context(), \
                 patch.object(desktop, "_main", return_value=0) as app, \
                 patch.object(worker, "_main", return_value=0) as job, \
                 patch.object(store, "_submit_without_delivery", return_value=("ok", True)) as admission:
                self.assertEqual(desktop.main(), 0)
                self.assertEqual(worker.main(["--repo", str(self.app)]), 0)
                self.assertEqual(store.submit({"fixture": True}), ("ok", True))
                app.assert_called_once()
                job.assert_called_once()
                admission.assert_called_once()
        self.assert_released()

    def test_malformed_duplicate_nonfinite_and_oversized_journals_fail_closed(self):
        self.enable()
        for raw in (b'[]', b'null', b'{',
                    b'{"status":"committed","status":"committed"}',
                    b'{"status":"committed","x":NaN}', b'{"status":"committed","x":Infinity}',
                    b' ' * (4 * 1024 * 1024 + 1)):
            with self.subTest(kind=raw[:50]):
                self.journal(raw)
                self.assert_all_entries_blocked()
        self.assert_released()

    def test_invalid_transaction_store_and_journal_directory_fail_closed(self):
        self.enable()
        self.state.mkdir()
        (self.state / "transactions").write_bytes(b"not a directory")
        self.assert_all_entries_blocked()
        store = self.state / "transactions"
        self.assertTrue(store.resolve().is_relative_to(self.base))
        store.unlink()  # Only this test's own regular file, never a directory.
        journal = store / "fixture-transaction/journal.json"
        journal.mkdir(parents=True)
        self.assert_all_entries_blocked()

    def test_linked_transaction_store_and_journal_fail_closed(self):
        self.enable()
        path = self.journal(b'{"status":"committed"}')
        original = Path.lstat
        for linked in (self.state / "transactions", path.parent, path):
            def reparse(p, *args, **kwargs):
                info = original(p, *args, **kwargs)
                if p == linked:
                    return SimpleNamespace(st_mode=info.st_mode,
                                           st_file_attributes=getattr(info, "st_file_attributes", 0) | 0x400)
                return info
            with self.subTest(path=linked.name), \
                 patch.object(Path, "lstat", reparse):
                self.assert_all_entries_blocked()

    def test_copied_worker_nonce_and_digest_checks_remain_before_job_execution(self):
        self.enable()
        copy = self.base / "copied-worker.py"
        copy.write_bytes((ROOT / "execution/worker.py").read_bytes())
        spec = importlib.util.spec_from_file_location("copied_delivery_worker", copy)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        folder = self.base / "control/jobs/job-fixture"
        folder.mkdir(parents=True)
        request = b'{"fixture":true}'
        (folder / "request.json").write_bytes(request)
        job = {"nonce": "fixture-nonce", "entrypoint": str(copy.resolve()),
               "worker_sha256": hashlib.sha256(copy.read_bytes()).hexdigest(),
               "request_hash": hashlib.sha256(request).hexdigest(), "state": "succeeded"}
        argv = ["--control", str(self.base / "control"), "--runs", str(self.base / "runs"),
                "--repo", str(self.app), "--job", "job-fixture", "--nonce", "fixture-nonce"]
        with patch.object(module, "read_job", return_value=job), patch.object(module, "ProcessTree") as tree:
            self.assertEqual(module.main(argv), 0)
            tree.assert_not_called()
            job["worker_sha256"] = "0" * 64
            self.assertEqual(module.main(argv), 2)
            job["nonce"] = "changed"
            self.assertEqual(module.main(argv), 2)
        self.assert_released()

    def test_isolated_copied_worker_uses_stdlib_only_and_cooperates_with_core_lock(self):
        copy = self.base / "standalone/worker.py"
        copy.parent.mkdir()
        copy.write_bytes((ROOT / "execution/worker.py").read_bytes())
        environment = dict(os.environ) | {GUARD: "1", STATE: str(self.state)}
        script = (
            "import runpy,sys,os\n"
            "n=runpy.run_path(sys.argv[1],run_name='delivery_guard_probe')\n"
            "assert not any(x=='wb_delivery' or x.startswith('wb_delivery.') for x in sys.modules)\n"
            "def body(argv):\n"
            " assert os.environ['WORLDBLOOM_DELIVERY_APP_ROOT']==sys.argv[2]\n"
            " print('entered',flush=True)\n"
            " sys.stdin.readline()\n"
            " return 11\n"
            "n['main'].__globals__['_main']=body\n"
            "print('done:'+str(n['main'](['--repo',sys.argv[2]])),flush=True)\n"
        )
        command = [sys.executable, "-I", "-B", "-c", script, str(copy), str(self.app.resolve())]
        process = subprocess.Popen(command, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        first_line = queue.Queue()
        reader = threading.Thread(target=lambda: first_line.put(process.stdout.readline()), daemon=True)
        reader.start()
        try:
            self.assertEqual(first_line.get(timeout=10).strip(), "entered")
            self.assert_shared()
            out, err = process.communicate("release\n", timeout=10)
            self.assertEqual(process.returncode, 0, err)
            self.assertEqual(out.strip(), "done:11")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            reader.join(timeout=10)
        self.assert_released()
        with lease(self.state / "update.lock", exclusive=True):
            probe = subprocess.run(command, env=environment, input="release\n", capture_output=True, text=True, timeout=10,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            self.assertEqual(probe.returncode, 0, probe.stderr)
            self.assertEqual(probe.stdout.strip(), "done:2")


if __name__ == "__main__":
    unittest.main()
