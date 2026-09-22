"""GET /api/status/local over real HTTP: must work with no job_store (Viewer exe)."""

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from viewer.data import RunRepository
from viewer.server import ViewerHandler, ViewerServer


class LocalStatusApiTests(unittest.TestCase):
    def setUp(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
