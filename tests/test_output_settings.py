"""WB-UI-021: settings.json's "output" section as the single generation source."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from execution.output_settings import (
    DEFAULT_BACKEND, current_generation, read_output_settings,
    resolve_generation, write_output_settings,
)
from execution.provenance import ConfigError
from gapengine.synopsis import BACKENDS


class OutputSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wb-output-settings-")
        self.addCleanup(self.temp.cleanup)
        self.settings_path = Path(self.temp.name) / "settings.json"

    def write(self, payload):
        self.settings_path.write_text(json.dumps(payload), encoding="utf-8")

    # ---------------------------------------------------------------- read

    def test_defaults_when_settings_file_is_absent(self):
        self.assertFalse(self.settings_path.exists())
        view = read_output_settings(self.settings_path)
        self.assertEqual(view["backend"], DEFAULT_BACKEND)
        self.assertEqual(view["backend"], "codex-cli")
        self.assertIsNone(view["model"])
        self.assertEqual(view["limits"], {"max_calls": 1, "call_timeout_seconds": 180,
            "wall_seconds": 240, "max_saved_response_bytes": 128000})
        self.assertEqual(set(view["backends"]), set(BACKENDS))
        for backend in BACKENDS:
            self.assertIsNone(view["backends"][backend]["model"])
        # none defaults to max_calls 0; ollama's own timeout/wall differ.
        self.assertEqual(view["backends"]["none"]["limits"]["max_calls"], 0)
        self.assertEqual(view["backends"]["ollama"]["limits"]["call_timeout_seconds"], 900)
        self.assertEqual(view["backends"]["ollama"]["limits"]["wall_seconds"], 3600)
        # None settings_path (no server.settings_path configured) behaves the same.
        self.assertEqual(read_output_settings(None), view)

    def test_resolves_the_existing_minimal_shape_unchanged(self):
        # This is the exact shape the real repo settings.json is in today --
        # WB-UI-021 must keep it working with zero migration.
        self.write({"output": {"codex-cli": {"model": "gpt-5.6-sol"}}})
        view = read_output_settings(self.settings_path)
        self.assertEqual(view["backend"], "codex-cli")
        self.assertEqual(view["model"], "gpt-5.6-sol")
        self.assertEqual(view["limits"]["max_calls"], 1)
        self.assertEqual(resolve_generation(self.settings_path),
            {"backend": "codex-cli", "model": "gpt-5.6-sol", "limits": view["limits"]})

    def test_broken_json_is_rejected_not_silently_defaulted(self):
        self.settings_path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(ConfigError):
            read_output_settings(self.settings_path)
        with self.assertRaises(ConfigError):
            write_output_settings(self.settings_path, {"model": "x"})

    # --------------------------------------------------------------- write

    def test_write_preserves_credentials_and_never_returns_them(self):
        self.write({"output": {"codex-cli": {"model": "old-model", "api_key": "DO-NOT-LEAK",
            "command": "codex", "base_url": "http://leak.example"}}})
        view = write_output_settings(self.settings_path, {"model": "new-model"})
        self.assertEqual(view["model"], "new-model")
        self.assertNotIn("api_key", json.dumps(view))
        self.assertNotIn("command", json.dumps(view))
        self.assertNotIn("base_url", json.dumps(view))
        raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(raw["output"]["codex-cli"]["api_key"], "DO-NOT-LEAK")
        self.assertEqual(raw["output"]["codex-cli"]["command"], "codex")
        self.assertEqual(raw["output"]["codex-cli"]["base_url"], "http://leak.example")
        self.assertEqual(raw["output"]["codex-cli"]["model"], "new-model")

    def test_unknown_top_level_key_is_rejected(self):
        with self.assertRaises(ConfigError):
            write_output_settings(self.settings_path, {"model": "x", "api_key": "sneaky"})

    def test_invalid_backend_is_rejected(self):
        with self.assertRaises(ConfigError):
            write_output_settings(self.settings_path, {"backend": "not-a-backend", "model": "x"})

    def test_missing_model_for_a_real_backend_is_rejected(self):
        with self.assertRaises(ConfigError):
            write_output_settings(self.settings_path, {"backend": "openai"})
        # Switching *to* a backend that has never had a model configured, with
        # no model in this write either, must fail the same way.
        self.write({"output": {"default_backend": "codex-cli", "codex-cli": {"model": "x"}}})
        with self.assertRaises(ConfigError):
            write_output_settings(self.settings_path, {"backend": "openai"})

    def test_invalid_limits_are_rejected(self):
        for limits in ({"unknown_field": 1}, {"max_calls": -1},
                       {"call_timeout_seconds": 0}, {"max_calls": "1"}):
            with self.subTest(limits=limits), self.assertRaises(ConfigError):
                write_output_settings(self.settings_path, {"backend": "openai", "model": "m", "limits": limits})

    def test_none_backend_allows_null_model_and_zero_max_calls(self):
        view = write_output_settings(self.settings_path, {"backend": "none"})
        self.assertEqual(view["backend"], "none")
        self.assertIsNone(view["model"])
        self.assertEqual(view["limits"]["max_calls"], 0)
        # An explicit model for "none" is simply ignored, not an error --
        # only a real backend without a model is rejected.
        view = write_output_settings(self.settings_path, {"backend": "none", "model": "ignored"})
        self.assertIsNone(view["model"])
        # max_calls 0 is also accepted explicitly; the other three limits
        # keep a minimum of 1 even for backend "none".
        view = write_output_settings(self.settings_path, {"backend": "none",
            "limits": {"max_calls": 0, "call_timeout_seconds": 1, "wall_seconds": 1, "max_saved_response_bytes": 1}})
        self.assertEqual(view["limits"]["max_calls"], 0)
        with self.assertRaises(ConfigError):
            write_output_settings(self.settings_path, {"backend": "none",
                "limits": {"max_calls": 0, "call_timeout_seconds": 1, "wall_seconds": 1, "max_saved_response_bytes": 0}})

    def test_default_backend_switches_and_each_backend_keeps_its_own_settings(self):
        write_output_settings(self.settings_path, {"backend": "openai", "model": "gpt-x"})
        view = write_output_settings(self.settings_path, {"backend": "codex-cli", "model": "gpt-5.6-sol"})
        self.assertEqual(view["backend"], "codex-cli")
        self.assertEqual(view["model"], "gpt-5.6-sol")
        # Switching back to openai must not have lost its own model.
        self.assertEqual(view["backends"]["openai"]["model"], "gpt-x")
        reread = read_output_settings(self.settings_path)
        self.assertEqual(reread["backend"], "codex-cli")
        self.assertEqual(reread["backends"]["openai"]["model"], "gpt-x")

    # ---------------------------------------------------------- availability

    def test_write_with_no_settings_path_is_rejected(self):
        with self.assertRaises(ConfigError) as caught:
            write_output_settings(None, {"model": "x"})
        self.assertEqual(caught.exception.field_errors, {"settings": "設定ファイルの場所が未設定です"})

    def test_current_generation_wraps_resolve_with_availability(self):
        self.write({"output": {"default_backend": "openai", "openai": {"model": "m"}}})
        result = current_generation(self.settings_path)
        self.assertEqual(result["backend"], "openai")
        self.assertEqual(result["model"], "m")
        self.assertFalse(result["availability"]["available"])
        self.assertEqual(result["availability"]["reason"], "credentials_missing")


if __name__ == "__main__":
    unittest.main()
