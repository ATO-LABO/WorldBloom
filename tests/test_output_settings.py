"""WB-UI-021: settings.json's "output" section as the single generation source."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from execution.output_settings import (
    DEFAULT_BACKEND, current_generation, list_models, read_output_settings,
    resolve_generation, test_generation, write_api_key, write_output_settings,
)
from execution.provenance import ConfigError
from gapengine.synopsis import BACKENDS


class _FakeModelsResponse:
    """Stands in for urllib.request.urlopen()'s context-manager response."""

    def __init__(self, body):
        self._body = body

    @classmethod
    def for_ids(cls, model_ids):
        """openai/anthropic /v1/models shape: {"data": [{"id": ...}, ...]}."""
        return cls(json.dumps({"data": [{"id": m} for m in model_ids]}).encode("utf-8"))

    @classmethod
    def tags(cls, names):
        """ollama /api/tags shape: {"models": [{"name": ...}, ...]}."""
        return cls(json.dumps({"models": [{"name": n} for n in names]}).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self):
        return self._body


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
            if backend == "claude-cli":
                # claude-cli defaults to Sonnet 5 when nothing is configured,
                # matching gapengine/synopsis.py's own generation-time fallback.
                self.assertEqual(view["backends"][backend]["model"], "claude-sonnet-5")
            else:
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
        dumped = json.dumps(view)
        self.assertNotIn("DO-NOT-LEAK", dumped)
        # "has_api_key" (a bare boolean) is fine; the credential's own key/value
        # never is -- check for the quoted key, not the bare substring, so this
        # doesn't false-positive on "has_api_key".
        self.assertNotIn('"api_key"', dumped)
        self.assertNotIn("command", dumped)
        self.assertNotIn("base_url", dumped)
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

    def test_claude_cli_defaults_to_sonnet_5_and_can_be_saved_as_is(self):
        # Switching to claude-cli with no model needs no explicit model --
        # it already has a usable default, unlike every other real backend.
        view = write_output_settings(self.settings_path, {"backend": "claude-cli"})
        self.assertEqual(view["model"], "claude-sonnet-5")
        raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(raw["output"]["claude-cli"]["model"], "claude-sonnet-5")

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

    # -------------------------------------------------------- 疎通テスト

    def test_test_generation_remembers_a_reachable_model_but_not_an_unreachable_one(self):
        self.write({"output": {"openai": {"api_key": "sk-x"}}})
        with patch("execution.output_settings.urllib.request.urlopen",
                   return_value=_FakeModelsResponse.for_ids(["gpt-x", "gpt-y"])):
            result = test_generation(self.settings_path, "openai", "gpt-x")
        self.assertTrue(result["available"])
        view = read_output_settings(self.settings_path)
        self.assertEqual(view["backends"]["openai"]["verified_models"], ["gpt-x"])
        # A backend with no credentials never even reaches the network, and
        # never gets remembered.
        result = test_generation(self.settings_path, "anthropic", "claude-x")
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "credentials_missing")
        view = read_output_settings(self.settings_path)
        self.assertEqual(view["backends"]["anthropic"]["verified_models"], [])
        # test_generation never touches the active backend/model selection.
        self.assertEqual(view["backend"], DEFAULT_BACKEND)

    def test_test_generation_rejects_a_model_name_the_api_key_cannot_see(self):
        # A typo'd/nonexistent model must not be believed just because the
        # api_key itself is valid -- this is the actual "疎通" the button promises.
        self.write({"output": {"openai": {"api_key": "sk-x"}}})
        with patch("execution.output_settings.urllib.request.urlopen",
                   return_value=_FakeModelsResponse.for_ids(["gpt-y"])):
            result = test_generation(self.settings_path, "openai", "gpt-x-typo")
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "model_missing")
        view = read_output_settings(self.settings_path)
        self.assertEqual(view["backends"]["openai"]["verified_models"], [])

    def test_test_generation_reorders_and_caps_verified_models(self):
        self.write({"output": {"openai": {"api_key": "sk-x"}}})
        with patch("execution.output_settings.urllib.request.urlopen",
                   return_value=_FakeModelsResponse.for_ids(["a", "b", "c"])):
            for model in ("a", "b", "c"):
                test_generation(self.settings_path, "openai", model)
            test_generation(self.settings_path, "openai", "a")  # re-verify moves it to the front
        view = read_output_settings(self.settings_path)
        self.assertEqual(view["backends"]["openai"]["verified_models"], ["a", "c", "b"])

    def test_test_generation_rejects_invalid_backend_or_missing_model(self):
        with self.assertRaises(ConfigError):
            test_generation(self.settings_path, "not-a-backend", "m")
        with self.assertRaises(ConfigError):
            test_generation(self.settings_path, "openai", None)
        with self.assertRaises(ConfigError):
            test_generation(None, "openai", "m")
        # backend "none" needs no model and is always available.
        result = test_generation(self.settings_path, "none", None)
        self.assertTrue(result["available"])

    def test_test_generation_leaves_cli_and_ollama_checks_unchanged(self):
        # codex-cli/claude-cli/ollama already get a real check inside
        # generation_availability() itself -- test_generation must not touch
        # the network again for them (no urlopen patch needed here to pass).
        self.write({"output": {"codex-cli": {"command": "__wb_missing_cli__"}}})
        result = test_generation(self.settings_path, "codex-cli", "any-model")
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "executable_missing")

    # -------------------------------------------------------- モデル候補一覧

    def test_list_models_openai_returns_a_live_catalog(self):
        self.write({"output": {"openai": {"api_key": "sk-x"}}})
        with patch("execution.output_settings.urllib.request.urlopen",
                   return_value=_FakeModelsResponse.for_ids(["gpt-a", "gpt-b"])):
            result = list_models(self.settings_path, "openai")
        self.assertEqual(result, {"models": ["gpt-a", "gpt-b"], "live": True, "reason": None})

    def test_list_models_openai_without_credentials_is_empty_but_still_live(self):
        result = list_models(self.settings_path, "openai")
        self.assertEqual(result, {"models": [], "live": True, "reason": "credentials_missing"})

    def test_list_models_ollama_uses_the_local_server_tags(self):
        self.write({"output": {"ollama": {}}})
        with patch("gapengine.ollama.urllib.request.urlopen",
                   return_value=_FakeModelsResponse.tags(["qwen3.6:35b", "llama3"])):
            result = list_models(self.settings_path, "ollama")
        self.assertEqual(result, {"models": ["llama3", "qwen3.6:35b"], "live": True, "reason": None})

    def test_list_models_cli_backend_falls_back_to_verified_history_not_live(self):
        # codex-cli/claude-cli have no "list models" API -- only what a past
        # 疎通テスト confirmed is offered, and it must say so via live=False.
        self.write({"output": {"codex-cli": {"verified_models": ["gpt-5.6-sol"]}}})
        result = list_models(self.settings_path, "codex-cli")
        self.assertEqual(result, {"models": ["gpt-5.6-sol"], "live": False, "reason": None})

    def test_list_models_none_backend_needs_nothing(self):
        result = list_models(self.settings_path, "none")
        self.assertEqual(result, {"models": [], "live": False, "reason": None})

    def test_list_models_rejects_invalid_backend(self):
        with self.assertRaises(ConfigError):
            list_models(self.settings_path, "not-a-backend")

    # -------------------------------------------------------------- APIキー

    def test_write_api_key_is_write_only(self):
        write_api_key(self.settings_path, "openai", "sk-secret")
        view = read_output_settings(self.settings_path)
        self.assertTrue(view["backends"]["openai"]["has_api_key"])
        self.assertNotIn("sk-secret", json.dumps(view))
        raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(raw["output"]["openai"]["api_key"], "sk-secret")
        # It never touches the active backend/model selection.
        self.assertEqual(view["backend"], DEFAULT_BACKEND)

    def test_write_api_key_preserves_other_backend_fields(self):
        self.write({"output": {"openai": {"model": "gpt-x", "limits": {"max_calls": 3}}}})
        write_api_key(self.settings_path, "openai", "sk-secret")
        view = read_output_settings(self.settings_path)
        self.assertEqual(view["backends"]["openai"]["model"], "gpt-x")
        self.assertEqual(view["backends"]["openai"]["limits"]["max_calls"], 3)
        self.assertTrue(view["backends"]["openai"]["has_api_key"])

    def test_write_api_key_rejects_backends_that_do_not_use_one(self):
        for backend in ("codex-cli", "claude-cli", "ollama", "none"):
            with self.subTest(backend=backend), self.assertRaises(ConfigError):
                write_api_key(self.settings_path, backend, "sk-secret")

    def test_write_api_key_rejects_blank_or_missing_key(self):
        with self.assertRaises(ConfigError):
            write_api_key(self.settings_path, "openai", "")
        with self.assertRaises(ConfigError):
            write_api_key(self.settings_path, "openai", "   ")
        with self.assertRaises(ConfigError):
            write_api_key(self.settings_path, "openai", None)
        with self.assertRaises(ConfigError):
            write_api_key(None, "openai", "sk-secret")


if __name__ == "__main__":
    unittest.main()
