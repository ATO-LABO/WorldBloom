"""GPU lease, Ollama arbitration and thermal guard. No real GPU/Ollama/llama-server."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from execution.output_store import OutputStore
from gapengine import gpu_guard, llama_server, ollama
from gapengine.synopsis import GenerationError, generate_text

ROOT = Path(__file__).resolve().parents[1]


def _reap_process(process: subprocess.Popen) -> None:
    """Kill (if still running) and always wait -- an unwaited Windows child can
    leave its temp/working directory locked, turning cleanup into a flaky
    PermissionError on the next test's TemporaryDirectory teardown."""

    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


class _EnvIsolatedTestCase(unittest.TestCase):
    """Every test points WORLDBLOOM_GPU_LEASE_DIR at a private temp dir."""

    def setUp(self) -> None:
        super().setUp()
        gpu_guard._lease_local.depth = 0
        self._lease_temp = tempfile.TemporaryDirectory(prefix="wb-gpu-guard-lease-")
        self.addCleanup(self._lease_temp.cleanup)
        patcher = mock.patch.dict(os.environ, {"WORLDBLOOM_GPU_LEASE_DIR": self._lease_temp.name})
        patcher.start()
        self.addCleanup(patcher.stop)


class LeaseStateTests(_EnvIsolatedTestCase):
    def test_not_busy_with_no_lock_file_yet(self) -> None:
        self.assertEqual(gpu_guard.lease_state(), {"busy": False})

    def test_probe_with_no_lock_file_creates_nothing(self) -> None:
        gpu_guard.lease_state()
        self.assertFalse((Path(self._lease_temp.name) / "gpu.lock").exists())
        self.assertFalse((Path(self._lease_temp.name) / "gpu.holder.json").exists())

    def test_probe_does_not_leave_the_lease_held(self) -> None:
        with gpu_guard.gpu_lease("setup", wait_seconds=1):
            pass
        self.assertFalse(gpu_guard.lease_state()["busy"])
        # A real acquire right after the probe must still succeed --
        # lease_state() must release what it took to test the lock.
        with gpu_guard.gpu_lease("after-probe", wait_seconds=1):
            pass


class WaitUntilCoolTests(_EnvIsolatedTestCase):
    def test_none_temperature_does_not_wait(self) -> None:
        sleep = mock.Mock()
        waited = gpu_guard.wait_until_cool(read=lambda: None, sleep=sleep)
        self.assertEqual(waited, 0.0)
        sleep.assert_not_called()

    def test_below_pause_at_does_not_wait(self) -> None:
        sleep = mock.Mock()
        waited = gpu_guard.wait_until_cool({"pause_at": 78}, read=lambda: 50.0, sleep=sleep)
        self.assertEqual(waited, 0.0)
        sleep.assert_not_called()

    def test_defaults_are_78_and_70(self) -> None:
        self.assertEqual(gpu_guard.DEFAULT_THERMAL["pause_at"], 78)
        self.assertEqual(gpu_guard.DEFAULT_THERMAL["resume_at"], 70)

    def test_cools_down_after_two_polls(self) -> None:
        readings = iter([85.0, 75.0, 68.0])
        clock_value = [0.0]

        def clock() -> float:
            return clock_value[0]

        def sleep(seconds: float) -> None:
            clock_value[0] += seconds

        waited = gpu_guard.wait_until_cool(
            {"pause_at": 78, "resume_at": 70, "poll_seconds": 15, "max_wait_seconds": 600},
            read=lambda: next(readings), sleep=sleep, clock=clock,
        )
        self.assertEqual(waited, 30.0)

    def test_max_wait_seconds_cuts_off(self) -> None:
        clock_value = [0.0]

        def clock() -> float:
            return clock_value[0]

        def sleep(seconds: float) -> None:
            clock_value[0] += seconds

        waited = gpu_guard.wait_until_cool(
            {"pause_at": 78, "resume_at": 70, "poll_seconds": 10, "max_wait_seconds": 25},
            read=lambda: 90.0, sleep=sleep, clock=clock,
        )
        self.assertEqual(waited, 30.0)


_LEASE_HOLDER_WORKER = (
    "import sys, time\n"
    "sys.path.insert(0, sys.argv[2])\n"
    "from gapengine import gpu_guard\n"
    "with gpu_guard.gpu_lease('other-process', wait_seconds=5):\n"
    "    open(sys.argv[1], 'w', encoding='utf-8').write('ready')\n"
    "    time.sleep(300)\n"
)


def _spawn_lease_holder(case: unittest.TestCase, lease_dir: str) -> subprocess.Popen:
    """A real subprocess that holds the GPU lease -- the reentrant _lease_depth
    fast path only applies within one process, so testing that some code
    respects an *externally* held lease needs a genuinely separate process."""

    marker = Path(lease_dir) / "ready.marker"
    process = subprocess.Popen(
        [sys.executable, "-c", _LEASE_HOLDER_WORKER, str(marker), str(ROOT)],
        env=os.environ.copy(),
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    case.addCleanup(_reap_process, process)
    deadline = time.monotonic() + 10
    while not marker.exists():
        if time.monotonic() >= deadline:
            process.kill()
            raise AssertionError("lease holder subprocess never signalled readiness")
        time.sleep(0.05)
    return process


class LeaseDepthThreadSafetyTests(_EnvIsolatedTestCase):
    def test_a_lease_held_by_one_thread_still_blocks_another_thread(self) -> None:
        # Regression for WB-PRELOAD-001 review M2: a process-global reentrancy
        # counter would let this second thread's acquire silently no-op
        # (skip the OS lock entirely) just because *some* thread in this
        # process already holds it. threading.local() must scope reentrancy
        # to the thread that actually holds the lease.
        holding = threading.Event()
        release = threading.Event()
        outcome: dict = {}

        def hold():
            with gpu_guard.gpu_lease("holder-thread", wait_seconds=5):
                holding.set()
                release.wait(timeout=5)

        def contend():
            holding.wait(timeout=5)
            try:
                with gpu_guard.gpu_lease("contender-thread", wait_seconds=0.3, poll_seconds=0.05):
                    outcome["acquired"] = True
            except gpu_guard.GpuBusy as error:
                outcome["busy_owner"] = error.holder.get("owner")

        holder_thread = threading.Thread(target=hold)
        contender_thread = threading.Thread(target=contend)
        holder_thread.start()
        contender_thread.start()
        contender_thread.join(timeout=10)
        release.set()
        holder_thread.join(timeout=10)

        self.assertNotIn("acquired", outcome)
        self.assertEqual(outcome.get("busy_owner"), "holder-thread")


class GpuLeaseCrossProcessTests(_EnvIsolatedTestCase):
    def _spawn_holder(self):
        return _spawn_lease_holder(self, self._lease_temp.name)

    def test_lease_state_busy_while_other_process_holds_it(self) -> None:
        process = self._spawn_holder()
        try:
            state = gpu_guard.lease_state()
            self.assertTrue(state["busy"])
            self.assertEqual(state.get("owner"), "other-process")
        finally:
            process.kill()
            process.wait(timeout=10)

    def test_lease_state_probe_does_not_steal_the_other_process_lease(self) -> None:
        # The probe must not just *report* busy from stale holder.json -- the
        # other process's actual OS-level hold must still be intact right after.
        process = self._spawn_holder()
        try:
            gpu_guard.lease_state()
            with self.assertRaises(gpu_guard.GpuBusy):
                with gpu_guard.gpu_lease("me", wait_seconds=0.5, poll_seconds=0.1):
                    pass
        finally:
            process.kill()
            process.wait(timeout=10)

    def test_other_process_holds_lease_raises_gpu_busy(self) -> None:
        process = self._spawn_holder()
        try:
            with self.assertRaises(gpu_guard.GpuBusy) as caught:
                with gpu_guard.gpu_lease("me", wait_seconds=0.5, poll_seconds=0.1):
                    pass
            self.assertEqual(caught.exception.holder.get("owner"), "other-process")
        finally:
            process.kill()
            process.wait(timeout=10)

    def test_lease_becomes_available_after_holder_process_dies(self) -> None:
        process = self._spawn_holder()
        process.kill()
        process.wait(timeout=10)
        entered = []
        with gpu_guard.gpu_lease("me", wait_seconds=5, poll_seconds=0.1):
            entered.append(True)
        self.assertEqual(entered, [True])

    def test_reentrant_within_same_process_does_not_wait(self) -> None:
        entered = []
        with gpu_guard.gpu_lease("outer", wait_seconds=1):
            start = time.monotonic()
            with gpu_guard.gpu_lease("inner", wait_seconds=1):
                entered.append(True)
            self.assertLess(time.monotonic() - start, 0.5)
        self.assertEqual(entered, [True])


class OllamaModelsTests(_EnvIsolatedTestCase):
    def _fake_response(self, body):
        response = mock.MagicMock()
        response.read.return_value = json.dumps(body).encode("utf-8")
        response.__enter__.return_value = response
        return response

    def test_unreachable_returns_empty_list(self) -> None:
        with mock.patch(
            "gapengine.gpu_guard.urllib.request.urlopen",
            side_effect=urllib.error.URLError("refused"),
        ):
            self.assertEqual(gpu_guard.ollama_models("http://localhost:11434"), [])

    def test_reachable_returns_model_dicts(self) -> None:
        response = self._fake_response({"models": [{"name": "m1", "expires_at": "t1"}]})
        with mock.patch("gapengine.gpu_guard.urllib.request.urlopen", return_value=response):
            models = gpu_guard.ollama_models("http://localhost:11434")
        self.assertEqual(models, [{"name": "m1", "expires_at": "t1"}])


class OllamaIsActiveTests(_EnvIsolatedTestCase):
    def test_no_models_returns_false_without_sleep(self) -> None:
        sleep = mock.Mock()
        with mock.patch("gapengine.gpu_guard.ollama_models", return_value=[]):
            self.assertFalse(gpu_guard.ollama_is_active("http://x", observe_seconds=15, sleep=sleep))
        sleep.assert_not_called()

    def test_unchanged_expiry_is_idle(self) -> None:
        sleep = mock.Mock()
        with mock.patch(
            "gapengine.gpu_guard.ollama_models",
            return_value=[{"name": "m1", "expires_at": "t1"}],
        ):
            self.assertFalse(gpu_guard.ollama_is_active("http://x", observe_seconds=15, sleep=sleep))
        sleep.assert_called_once_with(15)

    def test_changed_expiry_is_active(self) -> None:
        sleep = mock.Mock()
        responses = iter([
            [{"name": "m1", "expires_at": "t1"}],
            [{"name": "m1", "expires_at": "t2"}],
        ])
        with mock.patch("gapengine.gpu_guard.ollama_models", side_effect=lambda *a, **k: next(responses)):
            self.assertTrue(gpu_guard.ollama_is_active("http://x", observe_seconds=15, sleep=sleep))
        sleep.assert_called_once_with(15)


class UnloadOllamaTests(_EnvIsolatedTestCase):
    def test_posts_keep_alive_zero_per_model_and_swallows_errors(self) -> None:
        calls = []

        def fake_urlopen(request, timeout=None):
            calls.append(json.loads(request.data.decode("utf-8")))
            if calls[-1]["model"] == "boom":
                raise urllib.error.URLError("refused")
            response = mock.MagicMock()
            response.__enter__.return_value = response
            return response

        with mock.patch("gapengine.gpu_guard.urllib.request.urlopen", side_effect=fake_urlopen):
            gpu_guard.unload_ollama("http://x", ["m1", "boom"])
        self.assertEqual([c["model"] for c in calls], ["m1", "boom"])
        self.assertTrue(all(c["keep_alive"] == 0 for c in calls))


class ReapOrphanServerTests(_EnvIsolatedTestCase):
    def _spawn_sleeper(self):
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(300)"],
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.addCleanup(_reap_process, process)
        return process

    def test_no_recorded_pid_is_a_noop(self) -> None:
        gpu_guard.reap_orphan_server()

    def test_terminates_when_image_matches(self) -> None:
        process = self._spawn_sleeper()
        image = os.path.basename(sys.executable)
        gpu_guard.record_managed_server(process.pid, image=image)
        gpu_guard.reap_orphan_server()
        process.wait(timeout=10)
        self.assertIsNotNone(process.poll())
        holder = gpu_guard._read_holder(gpu_guard.lease_dir())
        self.assertIsNone(holder.get("managed_server_pid"))
        self.assertIsNone(holder.get("managed_server_image"))

    def test_does_not_terminate_when_image_mismatches(self) -> None:
        # A reused pid legitimately running a different executable must
        # never be killed -- only forgotten.
        process = self._spawn_sleeper()
        gpu_guard.record_managed_server(process.pid, image="totally-unrelated.exe")
        gpu_guard.reap_orphan_server()
        time.sleep(0.2)
        self.assertIsNone(process.poll())
        holder = gpu_guard._read_holder(gpu_guard.lease_dir())
        self.assertIsNone(holder.get("managed_server_pid"))

    def test_does_not_terminate_when_image_was_never_recorded(self) -> None:
        # An older-format holder file (pid only, no image) is not trustworthy
        # evidence on its own.
        process = self._spawn_sleeper()
        gpu_guard.record_managed_server(process.pid)
        gpu_guard.reap_orphan_server()
        time.sleep(0.2)
        self.assertIsNone(process.poll())
        holder = gpu_guard._read_holder(gpu_guard.lease_dir())
        self.assertIsNone(holder.get("managed_server_pid"))

    def test_never_terminates_its_own_process(self) -> None:
        gpu_guard.record_managed_server(os.getpid(), image="python.exe")
        with mock.patch("gapengine.gpu_guard.subprocess.run") as run:
            gpu_guard.reap_orphan_server()
        run.assert_not_called()

    def test_leaves_a_preloaded_server_alone(self) -> None:
        # reap_orphan_server() only ever looks at the "managed" key -- a
        # preload_llama_server() process is recorded separately so this
        # never mistakes it for a crashed session's leftover.
        process = self._spawn_sleeper()
        image = os.path.basename(sys.executable)
        gpu_guard._record_server("preloaded", process.pid, image=image)
        gpu_guard.reap_orphan_server()
        time.sleep(0.2)
        self.assertIsNone(process.poll())
        holder = gpu_guard._read_holder(gpu_guard.lease_dir())
        self.assertEqual(holder.get("preloaded_server_pid"), process.pid)


FAKE_HEALTH_SERVER = (
    "import http.server, sys\n"
    "port = int(sys.argv[1])\n"
    "class Handler(http.server.BaseHTTPRequestHandler):\n"
    "    def do_GET(self):\n"
    "        if self.path == '/health':\n"
    "            self.send_response(200); self.end_headers(); self.wfile.write(b'ok')\n"
    "        else:\n"
    "            self.send_response(404); self.end_headers()\n"
    "    def log_message(self, *a): pass\n"
    "http.server.HTTPServer(('127.0.0.1', port), Handler).serve_forever()\n"
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ManagedServerTests(_EnvIsolatedTestCase):
    def test_already_reachable_server_is_not_launched(self) -> None:
        with mock.patch("gapengine.llama_server.is_ready", return_value=True), \
                mock.patch("gapengine.llama_server.subprocess.Popen") as popen:
            with llama_server.managed_server({}) as started:
                self.assertFalse(started)
        popen.assert_not_called()

    def test_no_launch_command_is_not_launched(self) -> None:
        with mock.patch("gapengine.llama_server.is_ready", return_value=False), \
                mock.patch("gapengine.llama_server.subprocess.Popen") as popen:
            with llama_server.managed_server({}) as started:
                self.assertFalse(started)
        popen.assert_not_called()

    def test_launches_and_stops_fake_server(self) -> None:
        port = _free_port()
        config = {
            "base_url": f"http://127.0.0.1:{port}",
            "launch": [sys.executable, "-c", FAKE_HEALTH_SERVER, str(port)],
            "startup_seconds": 10,
        }
        started_pids: list[int] = []
        with llama_server.managed_server(
            config, on_started=started_pids.append, sleep=lambda s: time.sleep(0.05),
        ) as started:
            self.assertTrue(started)
            self.assertTrue(llama_server.is_ready(config))
        self.assertEqual(len(started_pids), 1)
        deadline = time.monotonic() + 5
        while llama_server.is_ready(config) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(llama_server.is_ready(config))

    def test_command_that_exits_immediately_raises_runtime_error(self) -> None:
        port = _free_port()
        config = {
            "base_url": f"http://127.0.0.1:{port}",
            "launch": [sys.executable, "-c", "pass"],
            "startup_seconds": 2,
        }
        with self.assertRaises(RuntimeError):
            with llama_server.managed_server(config, sleep=lambda s: time.sleep(0.02)):
                pass


class PreloadLlamaServerTests(_EnvIsolatedTestCase):
    def test_requires_gpu_guard(self) -> None:
        with self.assertRaises(ValueError):
            gpu_guard.preload_llama_server({})

    def test_requires_launch_command(self) -> None:
        with self.assertRaises(ValueError):
            gpu_guard.preload_llama_server({"gpu_guard": {}, "llama-server": {}})

    def test_already_ready_is_a_noop_not_an_error(self) -> None:
        with mock.patch("gapengine.llama_server.is_ready", return_value=True), \
                mock.patch("gapengine.llama_server.spawn_server") as spawn:
            pid = gpu_guard.preload_llama_server(
                {"gpu_guard": {}, "llama-server": {"launch": ["x"]}},
            )
        self.assertIsNone(pid)
        spawn.assert_not_called()

    def test_gpu_busy_propagates(self) -> None:
        with mock.patch("gapengine.llama_server.is_ready", return_value=False), \
                mock.patch("gapengine.gpu_guard.gpu_lease",
                            side_effect=gpu_guard.GpuBusy({"owner": "someone-else"})):
            with self.assertRaises(gpu_guard.GpuBusy):
                gpu_guard.preload_llama_server(
                    {"gpu_guard": {}, "llama-server": {"launch": ["x"]}}, wait_seconds=0,
                )

    def test_launches_leaves_running_and_a_real_session_reuses_it_without_killing_it(self) -> None:
        port = _free_port()
        settings = {
            "gpu_guard": {},
            "llama-server": {
                "base_url": f"http://127.0.0.1:{port}",
                "launch": [sys.executable, "-c", FAKE_HEALTH_SERVER, str(port)],
                "startup_seconds": 10,
            },
        }
        config = settings["llama-server"]
        with mock.patch("gapengine.gpu_guard.ollama_is_active", return_value=False), \
                mock.patch("gapengine.gpu_guard.ollama_models", return_value=[]):
            pid = gpu_guard.preload_llama_server(settings)
        try:
            self.assertIsNotNone(pid)
            self.assertTrue(llama_server.is_ready(config))
            holder = gpu_guard._read_holder(gpu_guard.lease_dir())
            self.assertEqual(holder.get("preloaded_server_pid"), pid)
            self.assertIsNone(holder.get("managed_server_pid"))

            # A real generation session must find it via is_ready() and reuse
            # it (managed_server()'s existing "yield False" path) -- not
            # reap it as an orphan, and not still be running afterwards.
            with mock.patch("gapengine.gpu_guard.ollama_is_active", return_value=False), \
                    mock.patch("gapengine.gpu_guard.ollama_models", return_value=[]):
                with gpu_guard.local_gpu_session("llama-server", settings, owner="job", wait_seconds=1):
                    self.assertTrue(llama_server.is_ready(config))
            self.assertTrue(llama_server.is_ready(config))
        finally:
            gpu_guard.stop_preloaded_llama_server()
        deadline = time.monotonic() + 5
        while llama_server.is_ready(config) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(llama_server.is_ready(config))
        holder = gpu_guard._read_holder(gpu_guard.lease_dir())
        self.assertIsNone(holder.get("preloaded_server_pid"))


class StopPreloadedLlamaServerTests(_EnvIsolatedTestCase):
    def test_no_recorded_pid_is_a_noop(self) -> None:
        gpu_guard.stop_preloaded_llama_server()

    def test_respects_an_active_lease(self) -> None:
        # Must not kill a server a real generation session is mid-call with,
        # even if that session never recorded it under "preloaded" itself.
        # A genuinely separate process is required here: gpu_lease() is
        # reentrant within one process, so an in-process "held" lease
        # wouldn't actually block a nested acquire the way a real holder does.
        process = _spawn_lease_holder(self, self._lease_temp.name)
        try:
            with self.assertRaises(gpu_guard.GpuBusy):
                gpu_guard.stop_preloaded_llama_server(wait_seconds=0)
        finally:
            process.kill()
            process.wait(timeout=10)


class PreloadOllamaTests(_EnvIsolatedTestCase):
    def test_requires_gpu_guard(self) -> None:
        with self.assertRaises(ValueError):
            gpu_guard.preload_ollama({})

    def test_busy_when_llama_server_reachable(self) -> None:
        with mock.patch("gapengine.llama_server.is_ready", return_value=True):
            with self.assertRaises(gpu_guard.GpuBusy):
                gpu_guard.preload_ollama({"gpu_guard": {}, "llama-server": {}})

    def test_posts_a_30_minute_keep_alive_for_the_configured_model(self) -> None:
        calls = []

        def fake_urlopen(request, timeout=None):
            calls.append(json.loads(request.data.decode("utf-8")))
            response = mock.MagicMock()
            response.__enter__.return_value = response
            return response

        with mock.patch("gapengine.llama_server.is_ready", return_value=False), \
                mock.patch("gapengine.gpu_guard.urllib.request.urlopen", side_effect=fake_urlopen):
            gpu_guard.preload_ollama({"gpu_guard": {}, "ollama": {"model": "qwen3.6:35b"}})
        self.assertEqual(calls, [{"model": "qwen3.6:35b", "keep_alive": gpu_guard.PRELOAD_OLLAMA_KEEP_ALIVE}])

    def test_respects_an_active_lease(self) -> None:
        process = _spawn_lease_holder(self, self._lease_temp.name)
        try:
            with self.assertRaises(gpu_guard.GpuBusy):
                gpu_guard.preload_ollama({"gpu_guard": {}}, wait_seconds=0)
        finally:
            process.kill()
            process.wait(timeout=10)


class UnloadPreloadedOllamaTests(_EnvIsolatedTestCase):
    def test_unloads_whatever_is_loaded(self) -> None:
        calls = []

        def fake_urlopen(request, timeout=None):
            response = mock.MagicMock()
            response.__enter__.return_value = response
            if isinstance(request, str):
                # ollama_models()'s GET /api/ps -- a bare URL string, not a Request.
                response.read.return_value = json.dumps({"models": [{"name": "qwen3.6:35b"}]}).encode("utf-8")
            else:
                calls.append(json.loads(request.data.decode("utf-8")))
            return response

        with mock.patch("gapengine.gpu_guard.urllib.request.urlopen", side_effect=fake_urlopen):
            gpu_guard.unload_preloaded_ollama({"gpu_guard": {}})
        self.assertEqual(calls, [{"model": "qwen3.6:35b", "keep_alive": 0}])

    def test_respects_an_active_lease(self) -> None:
        process = _spawn_lease_holder(self, self._lease_temp.name)
        try:
            with self.assertRaises(gpu_guard.GpuBusy):
                gpu_guard.unload_preloaded_ollama({}, wait_seconds=0)
        finally:
            process.kill()
            process.wait(timeout=10)


class LocalGpuSessionTests(_EnvIsolatedTestCase):
    def test_no_gpu_guard_key_is_a_complete_noop(self) -> None:
        with mock.patch("gapengine.gpu_guard.gpu_lease") as lease, \
                mock.patch("gapengine.gpu_guard.ollama_is_active") as active, \
                mock.patch("gapengine.gpu_guard.reap_orphan_server") as reap:
            entered = []
            with gpu_guard.local_gpu_session("llama-server", {}, owner="t", wait_seconds=1):
                entered.append(True)
        self.assertEqual(entered, [True])
        lease.assert_not_called()
        active.assert_not_called()
        reap.assert_not_called()

    def test_non_local_backend_is_noop_even_with_guard(self) -> None:
        with mock.patch("gapengine.gpu_guard.gpu_lease") as lease:
            entered = []
            with gpu_guard.local_gpu_session("anthropic", {"gpu_guard": {}}, owner="t", wait_seconds=1):
                entered.append(True)
        self.assertEqual(entered, [True])
        lease.assert_not_called()

    def _fake_managed_server(self):
        cm = mock.MagicMock()
        cm.__enter__.return_value = True
        cm.__exit__.return_value = False
        return mock.patch("gapengine.llama_server.managed_server", return_value=cm)

    def test_reap_orphan_server_runs_on_entry(self) -> None:
        with mock.patch("gapengine.gpu_guard.reap_orphan_server") as reap, \
                mock.patch("gapengine.gpu_guard.ollama_is_active", return_value=False), \
                mock.patch("gapengine.gpu_guard.ollama_models", return_value=[]), \
                self._fake_managed_server():
            with gpu_guard.local_gpu_session("llama-server", {"gpu_guard": {}}, owner="t", wait_seconds=1):
                pass
        reap.assert_called_once()

    def test_nested_session_in_same_process_is_a_complete_noop(self) -> None:
        # scripts/synopsize.py wraps its whole cell loop in one outer session,
        # and each cell's generate_text() opens its own (nested) session. The
        # inner one must not re-run reap/Ollama-arbitration/managed_server --
        # doing so would treat the outer session's own server as an orphan
        # and restart it on every single cell.
        with mock.patch("gapengine.gpu_guard.reap_orphan_server") as reap, \
                mock.patch("gapengine.gpu_guard.ollama_is_active", return_value=False) as active, \
                mock.patch("gapengine.gpu_guard.ollama_models", return_value=[]), \
                mock.patch("gapengine.gpu_guard.unload_ollama") as unload, \
                self._fake_managed_server() as managed:
            with gpu_guard.local_gpu_session("llama-server", {"gpu_guard": {}}, owner="outer", wait_seconds=5):
                reap.reset_mock()
                active.reset_mock()
                unload.reset_mock()
                managed.reset_mock()
                entered = []
                with gpu_guard.local_gpu_session("llama-server", {"gpu_guard": {}}, owner="inner", wait_seconds=5):
                    entered.append(True)
                self.assertEqual(entered, [True])
        reap.assert_not_called()
        active.assert_not_called()
        unload.assert_not_called()
        managed.assert_not_called()

    def test_ollama_backend_busy_when_llama_server_reachable(self) -> None:
        settings = {"gpu_guard": {}, "llama-server": {"base_url": "http://x"}}
        with mock.patch("gapengine.llama_server.is_ready", return_value=True):
            with self.assertRaises(gpu_guard.GpuBusy) as caught:
                with gpu_guard.local_gpu_session("ollama", settings, owner="t", wait_seconds=1):
                    pass
        self.assertEqual(caught.exception.holder.get("owner"), "llama-server (running)")

    def test_ollama_backend_allowed_when_no_llama_server_configured(self) -> None:
        entered = []
        with gpu_guard.local_gpu_session("ollama", {"gpu_guard": {}}, owner="t", wait_seconds=1):
            entered.append(True)
        self.assertEqual(entered, [True])

    def test_idle_ollama_is_unloaded_before_llama_server_starts(self) -> None:
        with mock.patch("gapengine.gpu_guard.ollama_is_active", return_value=False), \
                mock.patch("gapengine.gpu_guard.ollama_models", return_value=[{"name": "m1"}]), \
                mock.patch("gapengine.gpu_guard.unload_ollama") as unload, \
                self._fake_managed_server():
            with gpu_guard.local_gpu_session("llama-server", {"gpu_guard": {}}, owner="t", wait_seconds=1):
                pass
        unload.assert_called_once_with("http://localhost:11434", ["m1"])

    def test_busy_ollama_times_out_to_gpu_busy_without_unloading(self) -> None:
        with mock.patch("gapengine.gpu_guard.ollama_is_active", return_value=True), \
                mock.patch("gapengine.gpu_guard.unload_ollama") as unload:
            with self.assertRaises(gpu_guard.GpuBusy) as caught:
                with gpu_guard.local_gpu_session("llama-server", {"gpu_guard": {}}, owner="t", wait_seconds=0.2):
                    pass
        self.assertEqual(caught.exception.holder.get("owner"), "ollama (in use)")
        unload.assert_not_called()


class OutputWorkerGpuBusyTests(_EnvIsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._control_temp = tempfile.TemporaryDirectory(prefix="wb-gpu-guard-control-")
        self.addCleanup(self._control_temp.cleanup)
        self.control = Path(self._control_temp.name)
        self.store = OutputStore(self.control)
        self._settings_temp = tempfile.TemporaryDirectory(prefix="wb-gpu-guard-settings-")
        self.addCleanup(self._settings_temp.cleanup)

    def _settings_path(self, gpu_guard_value):
        path = Path(self._settings_temp.name) / "settings.json"
        path.write_text(json.dumps({"output": {
            "gpu_guard": gpu_guard_value,
            "llama-server": {"model": "bonsai2-27b"},
        }}), encoding="utf-8")
        return path

    def _make_output(self, output_id="out-test", count=2):
        ids = [f"cand-{i}" for i in range(count)]
        request = {
            "schema_version": 1, "output_id": output_id, "request_id": "req-test", "job_id": "job-test",
            "kind": "synopsize", "run_id": "run-test", "config_id": "cfg-test", "candidate_ids": ids,
            "backend": "llama-server", "model": "bonsai2-27b",
            "limits": {"max_calls": len(ids), "call_timeout_seconds": 10, "wall_seconds": 100,
                       "max_saved_response_bytes": 4096},
        }
        self.store.create(request, {cid: "prompt" for cid in ids})
        return ids

    def _make_job(self, output_id, settings_path):
        jobs = self.control / "jobs"
        folder = jobs / "job-test"
        folder.mkdir(parents=True)
        job = {
            "schema_version": 1, "job_id": "job-test", "output_id": output_id, "nonce": "nonce-1",
            "state": "running", "phase": "generating", "cancel_requested_at": None,
            "created_at": time.time(), "wall_seconds": 100, "settings_path": str(settings_path),
            "revision": 0, "updated_at": time.time(),
        }
        (folder / "job.json").write_text(json.dumps(job), encoding="utf-8")
        return jobs, folder

    def test_gpu_busy_fails_all_candidates_without_sending(self) -> None:
        settings_path = self._settings_path({})
        ids = self._make_output()
        jobs, folder = self._make_job("out-test", settings_path)

        with mock.patch("gapengine.gpu_guard.gpu_lease", side_effect=gpu_guard.GpuBusy({"owner": "someone-else"})), \
                mock.patch("execution.output_worker.run_generation") as fake_generation:
            from execution.output_worker import run
            run(str(self.control), "out-test")

        fake_generation.assert_not_called()
        payload = self.store.project("out-test")
        self.assertEqual(len(payload["entries"]), len(ids))
        for entry in payload["entries"]:
            self.assertEqual(entry["status"], "error")
            self.assertEqual(entry["code"], "preflight_failed")
            self.assertEqual(entry["cause_type"], "GpuBusy")
            self.assertEqual(entry["retry_policy"], "safe_new_request")
        # A GpuBusy at session entry must not leave the job stuck showing
        # waiting="gpu" forever.
        job = json.loads((folder / "job.json").read_text(encoding="utf-8"))
        self.assertIsNone(job.get("waiting"))

    def test_unexpected_session_error_still_ends_the_job_and_clears_waiting(self) -> None:
        # A malformed gpu_guard value (or any other surprise while entering the
        # session) must fail the candidates, never leave the job stuck on waiting="gpu".
        settings_path = self._settings_path({"observe_seconds": None})
        ids = self._make_output(output_id="out-surprise")
        jobs, folder = self._make_job("out-surprise", settings_path)

        with mock.patch("gapengine.gpu_guard.ollama_models", return_value=[]),                 mock.patch("execution.output_worker.run_generation") as fake_generation:
            from execution.output_worker import run
            run(str(self.control), "out-surprise")

        fake_generation.assert_not_called()
        payload = self.store.project("out-surprise")
        self.assertEqual(len(payload["entries"]), len(ids))
        for entry in payload["entries"]:
            self.assertEqual(entry["status"], "error")
            self.assertEqual(entry["code"], "preflight_failed")
            self.assertEqual(entry["cause_type"], "TypeError")
        from execution.worker import read_job
        self.assertIsNone(read_job(jobs, folder).get("waiting"))

    def test_server_startup_failure_fails_all_candidates_without_sending(self) -> None:
        settings_path = self._settings_path({})
        ids = self._make_output(output_id="out-startup")
        jobs, folder = self._make_job("out-startup", settings_path)

        with mock.patch("gapengine.gpu_guard.ollama_is_active", return_value=False), \
                mock.patch("gapengine.gpu_guard.ollama_models", return_value=[]), \
                mock.patch(
                    "gapengine.llama_server.managed_server",
                    side_effect=RuntimeError("llama-server failed to start"),
                ), \
                mock.patch("execution.output_worker.run_generation") as fake_generation:
            from execution.output_worker import run
            run(str(self.control), "out-startup")

        fake_generation.assert_not_called()
        payload = self.store.project("out-startup")
        self.assertEqual(len(payload["entries"]), len(ids))
        for entry in payload["entries"]:
            self.assertEqual(entry["status"], "error")
            self.assertEqual(entry["code"], "preflight_failed")
            self.assertEqual(entry["cause_type"], "RuntimeError")
            self.assertEqual(entry["retry_policy"], "safe_new_request")
        job = json.loads((folder / "job.json").read_text(encoding="utf-8"))
        self.assertIsNone(job.get("waiting"))


class GenerateTextGpuBusyTests(_EnvIsolatedTestCase):
    def test_gpu_busy_becomes_generation_error(self) -> None:
        settings_dir = Path(tempfile.mkdtemp(prefix="wb-gpu-guard-generate-"))
        self.addCleanup(lambda: shutil.rmtree(settings_dir, ignore_errors=True))
        settings_path = settings_dir / "settings.json"
        settings_path.write_text(json.dumps({"output": {"gpu_guard": {}, "llama-server": {}}}), encoding="utf-8")

        with mock.patch("gapengine.gpu_guard.gpu_lease", side_effect=gpu_guard.GpuBusy({"owner": "x"})):
            with self.assertRaises(GenerationError):
                generate_text("llama-server", "prompt", settings_path=settings_path)


if __name__ == "__main__":
    unittest.main()
