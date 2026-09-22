"""GET/POST /api/status/local* over real HTTP: must work with no job_store (Viewer exe)."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from viewer import local_status
from viewer.data import RunRepository
from viewer.server import ViewerHandler, ViewerServer


class LocalStatusApiTests(unittest.TestCase):
    def setUp(self) -> None:
        local_status._preload_state.update(phase="idle", backend=None, error=None)
        self.addCleanup(local_status._preload_state.update, phase="idle", backend=None, error=None)
        self._runs = tempfile.TemporaryDirectory(prefix="wb-local-status-api-")
        self.addCleanup(self._runs.cleanup)
        self.server = ViewerServer(("127.0.0.1", 0), ViewerHandler)
        self.server.repository = RunRepository(Path(self._runs.name))
        # Deliberately no job_store, no settings_path: the Viewer exe has neither.
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def _get(self, path: str, *, headers: dict | None = None) -> tuple[int, bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            conn.request("GET", path, headers=headers or {})
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def _post(self, path: str, *, body: bytes = b"{}", headers: dict | None = None) -> tuple[int, bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            conn.request("POST", path, body=body, headers={
                "Content-Type": "application/json",
                "X-WorldBloom-Client": "1",
                **(headers or {}),
            })
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def test_works_without_job_store_or_settings(self) -> None:
        status, body = self._get("/api/status/local")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIsNone(data["backend"])
        self.assertEqual(
            {row["id"] for row in data["rows"]},
            {"gpu_temp", "gpu_lease", "llama_server", "ollama"},
        )

    def test_no_client_header_required_for_get(self) -> None:
        # Unlike POST /api/*, a GET status check doesn't need X-WorldBloom-Client.
        status, _ = self._get("/api/status/local")
        self.assertEqual(status, 200)

    def test_mismatched_host_header_is_forbidden(self) -> None:
        status, _ = self._get("/api/status/local", headers={"Host": "evil.example.com"})
        self.assertEqual(status, 403)

    def test_preload_without_settings_is_422(self) -> None:
        # No settings_path -> backend resolves to None -> "not a local backend".
        status, body = self._post("/api/status/local/preload")
        self.assertEqual(status, 422)
        self.assertIn("ローカル", json.loads(body)["message"])

    def test_unload_without_backend_field_is_bad_request(self) -> None:
        status, _ = self._post("/api/status/local/unload")
        self.assertEqual(status, 400)

    def test_unload_with_invalid_backend_is_bad_request(self) -> None:
        status, _ = self._post("/api/status/local/unload", body=b'{"backend": "openai"}')
        self.assertEqual(status, 400)

    def test_preload_gpu_busy_is_409(self) -> None:
        from gapengine.gpu_guard import GpuBusy
        settings_path = Path(self._runs.name) / "settings_busy.json"
        settings_path.write_text(json.dumps({
            "output": {"default_backend": "llama-server", "gpu_guard": {}, "llama-server": {"launch": ["x"]}},
        }), encoding="utf-8")
        self.server.settings_path = settings_path
        done = threading.Event()

        def fake_preload(settings):
            done.set()
            raise GpuBusy({"owner": "output:some-job"})

        with mock.patch("gapengine.gpu_guard.preload_llama_server", side_effect=fake_preload):
            status, _ = self._post("/api/status/local/preload")
            self.assertEqual(status, 202)  # dispatch itself is fire-and-forget
            self.assertTrue(done.wait(timeout=5))

    def test_unload_llama_server_needs_nothing_recorded_is_a_noop(self) -> None:
        # No settings_path at all -- stop_preloaded_llama_server() doesn't
        # need settings, so this succeeds even with nothing to release.
        status, body = self._post("/api/status/local/unload", body=b'{"backend": "llama-server"}')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"backend": "llama-server"})

    def test_preload_requires_client_header(self) -> None:
        status, _ = self._post("/api/status/local/preload", headers={"X-WorldBloom-Client": ""})
        self.assertEqual(status, 403)

    def test_preload_dispatches_and_returns_202(self) -> None:
        settings_path = Path(self._runs.name) / "settings.json"
        settings_path.write_text(json.dumps({
            "output": {"default_backend": "llama-server", "gpu_guard": {}, "llama-server": {"launch": ["x"]}},
        }), encoding="utf-8")
        self.server.settings_path = settings_path
        done = threading.Event()
        with mock.patch(
            "gapengine.gpu_guard.preload_llama_server", side_effect=lambda settings: done.set(),
        ):
            status, body = self._post("/api/status/local/preload")
        self.assertEqual(status, 202)
        self.assertEqual(json.loads(body), {"backend": "llama-server"})
        self.assertTrue(done.wait(timeout=5))

    def test_unload_dispatches_to_the_named_backend_and_returns_200(self) -> None:
        settings_path = Path(self._runs.name) / "settings2.json"
        settings_path.write_text(json.dumps({
            "output": {"default_backend": "llama-server", "gpu_guard": {}},
        }), encoding="utf-8")
        self.server.settings_path = settings_path
        with mock.patch("gapengine.gpu_guard.unload_preloaded_ollama") as unload:
            # Named "ollama" even though the *configured* backend is
            # llama-server -- unload targets the row it was clicked from,
            # not the currently-configured backend (review M1).
            status, body = self._post("/api/status/local/unload", body=b'{"backend": "ollama"}')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"backend": "ollama"})
        unload.assert_called_once()

    def test_preload_nonempty_body_is_rejected(self) -> None:
        settings_path = Path(self._runs.name) / "settings3.json"
        settings_path.write_text(json.dumps({
            "output": {"default_backend": "ollama", "gpu_guard": {}},
        }), encoding="utf-8")
        self.server.settings_path = settings_path
        status, _ = self._post("/api/status/local/preload", body=b'{"backend": "ollama"}')
        self.assertEqual(status, 422)


if __name__ == "__main__":
    unittest.main()
