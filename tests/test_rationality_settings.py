"""WB-JEV-005: execution/rationality_settings.py's TypeSafe Jev api_key
storage/verification. Network-free: urllib.request.urlopen is patched."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from execution.provenance import ConfigError
from execution.rationality_settings import (
    read_jev_api_key,
    read_jev_settings,
    write_jev_api_key,
)
from gapengine.rationality import JEV_DEFAULT_MODEL


class _FakeModelsResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    @classmethod
    def for_ids(cls, model_ids):
        return cls(json.dumps({"data": [{"id": m} for m in model_ids]}).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *_exc_info):
        return False

    def read(self):
        return self._body


class RationalitySettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="wb-rationality-settings-")
        self.addCleanup(self.temp.cleanup)
        self.settings_path = Path(self.temp.name) / "settings.json"

    def test_defaults_when_settings_file_is_absent(self) -> None:
        view = read_jev_settings(self.settings_path)
        self.assertEqual(view, {
            "has_api_key": False, "model": JEV_DEFAULT_MODEL,
            "verified_at": None, "available": False,
        })
        self.assertEqual(read_jev_api_key(self.settings_path), "")

    def test_write_succeeds_and_never_exposes_the_key(self) -> None:
        response = _FakeModelsResponse.for_ids([JEV_DEFAULT_MODEL, "jev-other"])
        with patch("execution.rationality_settings.urllib.request.urlopen", return_value=response):
            view = write_jev_api_key(self.settings_path, "sk-test-key")
        self.assertTrue(view["has_api_key"])
        self.assertEqual(view["model"], JEV_DEFAULT_MODEL)
        self.assertIsNotNone(view["verified_at"])
        self.assertTrue(view["available"])
        self.assertNotIn("sk-test-key", json.dumps(view))

        stored = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(stored["rationality"]["jev"]["api_key"], "sk-test-key")
        self.assertEqual(stored["rationality"]["jev"]["model"], JEV_DEFAULT_MODEL)

        # read_jev_settings never surfaces the raw key.
        self.assertNotIn("sk-test-key", json.dumps(read_jev_settings(self.settings_path)))
        self.assertEqual(read_jev_api_key(self.settings_path), "sk-test-key")

    def test_401_is_rejected_and_settings_file_is_untouched(self) -> None:
        import urllib.error
        error = urllib.error.HTTPError("url", 401, "unauthorized", None, None)
        with patch("execution.rationality_settings.urllib.request.urlopen", side_effect=error):
            with self.assertRaises(ConfigError):
                write_jev_api_key(self.settings_path, "bad-key")
        self.assertFalse(self.settings_path.exists())

    def test_model_not_in_key_list_is_rejected(self) -> None:
        response = _FakeModelsResponse.for_ids(["some-other-model"])
        with patch("execution.rationality_settings.urllib.request.urlopen", return_value=response):
            with self.assertRaises(ConfigError):
                write_jev_api_key(self.settings_path, "sk-test-key")
        self.assertFalse(self.settings_path.exists())

    def test_empty_key_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            write_jev_api_key(self.settings_path, "   ")
        self.assertFalse(self.settings_path.exists())

    def test_network_failure_is_rejected_without_writing(self) -> None:
        import urllib.error
        with patch(
            "execution.rationality_settings.urllib.request.urlopen",
            side_effect=urllib.error.URLError("boom"),
        ):
            with self.assertRaises(ConfigError):
                write_jev_api_key(self.settings_path, "sk-test-key")
        self.assertFalse(self.settings_path.exists())


if __name__ == "__main__":
    unittest.main()
