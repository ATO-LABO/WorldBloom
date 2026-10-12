"""Linux lifetime cleanup and descriptor identity; no model/provider calls."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from execution import worker


def until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("process cleanup did not finish")


class PlatformDispatchTests(unittest.TestCase):
    def test_windows_keeps_existing_named_job_object_arguments(self):
        with patch.object(worker.os, "name", "nt"), patch.object(worker, "_WindowsProcessTree") as original:
            result = worker.ProcessTree(include_self=True, name="Local\\WorldBloom-test", registry="unused")
            original.assert_called_once_with(include_self=True, name="Local\\WorldBloom-test")
            self.assertIs(result, original.return_value)


@unittest.skipUnless(sys.platform == "linux", "Linux process identity and subreaper")
class LinuxProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def script(self, source):
        path = self.root / "child.py"
        path.write_text(source)
        return [sys.executable, "-I", "-B", str(path)]

    def test_descriptor_identity_rejects_reused_pid_and_boot(self):
        actual = worker.process_identity(os.getpid())
        for key in ("created", "boot_id", "image"):
            value = actual[key] + 1 if key == "created" else actual[key] + "wrong"
            self.assertFalse(worker.terminate_verified({**actual, key: value}))
        self.assertEqual(worker.probe(actual), "alive")

    def test_success_reaps_descendant_even_after_new_session(self):
        marker = self.root / "pid.json"
        argv = self.script("import subprocess,sys,json,time\nfrom pathlib import Path\n"
            "p=subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(300)'],start_new_session=True)\n"
            f"Path({str(marker)!r}).write_text(json.dumps(p.pid))\n"
            "time.sleep(.1)\n")
        tree = worker.ProcessTree(registry=self.root / "trees")
        self.addCleanup(tree.close)
        child = tree.launch(argv, self.root)
        until(marker.exists)
        descendant = worker.process_identity(json.loads(marker.read_text()))
        self.assertIsNotNone(descendant)
        self.assertEqual(child.wait(timeout=5), 0)
        tree.finish()
        self.assertEqual(worker.probe(descendant), "dead")
        records = [worker.read(p) for p in (self.root / "trees").glob("*.json")]
        self.assertTrue(records and all(r["stopped"] for r in records))

    def test_owner_sigkill_drains_children_without_any_reconciliation(self):
        marker = self.root / "identity.json"
        child_argv = self.script("import time;time.sleep(300)\n")
        owner = self.root / "owner.py"
        source = "import sys,time,json\nfrom pathlib import Path\n"
        source += f"sys.path.insert(0,{str(Path(worker.__file__).parents[1])!r})\n"
        source += "from execution.worker import ProcessTree,process_identity\n"
        source += f"t=ProcessTree(registry={str(self.root/'trees')!r})\n"
        source += f"c=t.launch({child_argv!r},{str(self.root)!r})\n"
        source += f"Path({str(marker)!r}).write_text(json.dumps(process_identity(c.pid)))\n"
        source += "time.sleep(300)\n"
        owner.write_text(source)
        process = subprocess.Popen([sys.executable, "-I", "-B", str(owner)])
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        until(marker.exists)
        identity = worker.read(marker)
        os.kill(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        until(lambda: worker.probe(identity) == "dead")
        until(lambda: all(worker.read(p)["stopped"] for p in (self.root/'trees').glob('*.json')))

    def test_unknown_guard_record_blocks_terminal_recovery(self):
        folder = self.root / "job"
        (folder / "process-trees").mkdir(parents=True)
        path = folder / "process-trees/record.json"
        path.write_text(json.dumps({"stopped": False, "identity": {"pid": 0}}))
        job = {"entrypoint": str(folder / "worker.py")}
        self.assertFalse(worker.output_tree_stopped(job))
        path.write_text("not json")
        self.assertFalse(worker.output_tree_stopped(job))

    def test_recycled_descendant_pid_is_not_signalled(self):
        with patch.object(worker, "_linux_descendants", side_effect=[{123: 100}, {}]), \
             patch.object(worker, "process_identity", return_value={"pid": 123, "created": 101}), \
             patch.object(worker, "terminate_verified") as terminate:
            worker._linux_drain()
        terminate.assert_not_called()

    def test_stale_parent_snapshot_cannot_kill_an_unrelated_process(self):
        identity = worker.process_identity(os.getpid())
        with patch.object(worker, "_linux_descendants", side_effect=[{identity["pid"]: identity["created"]}, {}]), \
             patch.object(worker, "terminate_verified") as terminate:
            worker._linux_drain()
        terminate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
