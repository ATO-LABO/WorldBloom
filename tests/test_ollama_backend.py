"""Tests for the local Ollama output backend (no network access)."""

from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from gapengine import ollama
import gapengine.synopsis as synopsis_module
from gapengine.synopsis import GenerationError, generate_text


class BuildRequestTests(unittest.TestCase):
    def test_default_payload_shape(self) -> None:
        url, payload = ollama.build_request({}, "prompt text")

        self.assertEqual(url, "http://localhost:11434/api/chat")
        self.assertEqual(payload["model"], ollama.DEFAULT_MODEL)
        self.assertFalse(payload["stream"])
        self.assertFalse(payload["think"])
        self.assertEqual(payload["options"]["num_ctx"], 16384)
        self.assertEqual(
            payload["messages"],
            [{"role": "user", "content": "prompt text"}],
        )

    def test_seed_is_folded_into_options(self) -> None:
        _, payload = ollama.build_request({"seed": 7}, "prompt")

        self.assertEqual(payload["options"]["seed"], 7)

    def test_base_url_trailing_slash_is_stripped(self) -> None:
        url, _ = ollama.build_request(
            {"base_url": "http://localhost:11434/"},
            "prompt",
        )

        self.assertEqual(url, "http://localhost:11434/api/chat")

    def test_custom_model_and_options(self) -> None:
        _, payload = ollama.build_request(
            {"model": "custom:tag", "options": {"num_predict": 1}},
            "prompt",
        )

        self.assertEqual(payload["model"], "custom:tag")
        # Partial options merge over the defaults; num_ctx must survive.
        self.assertEqual(
            payload["options"],
            {"num_ctx": 16384, "num_predict": 1},
        )


class ExtractTextTests(unittest.TestCase):
    def test_incomplete_response_raises(self) -> None:
        with self.assertRaises(ValueError):
            ollama.extract_text(
                {
                    "done_reason": "length",
                    "message": {"content": "text"},
                }
            )

    def test_non_string_content_raises(self) -> None:
        with self.assertRaises(ValueError):
            ollama.extract_text(
                {
                    "done_reason": "stop",
                    "message": {"content": None},
                }
            )

    def test_stop_returns_content(self) -> None:
        text = ollama.extract_text(
            {
                "done_reason": "stop",
                "message": {"content": "本文"},
            }
        )

        self.assertEqual(text, "本文")


class GenerateTextOllamaTests(unittest.TestCase):
    def _settings_path(self, config: dict) -> Path:
        settings_dir = Path(tempfile.mkdtemp(prefix="worldbloom-ollama-"))
        self.addCleanup(
            lambda: __import__("shutil").rmtree(
                settings_dir, ignore_errors=True
            )
        )
        settings_path = settings_dir / "settings.json"
        settings_path.write_text(
            json.dumps({"output": {"ollama": config}}),
            encoding="utf-8",
        )
        return settings_path

    def test_ok_without_api_key(self) -> None:
        settings_path = self._settings_path({"model": "qwen3.5:9b-q4_K_M"})
        captured: dict = {}

        def fake_post_json(url, headers, payload, *, timeout):
            captured["url"] = url
            captured["headers"] = headers
            captured["payload"] = payload
            return {
                "done_reason": "stop",
                "message": {"content": "生成された本文です。"},
            }

        with mock.patch(
            "gapengine.synopsis._post_json",
            side_effect=fake_post_json,
        ):
            result = generate_text(
                "ollama",
                "prompt",
                settings_path=settings_path,
            )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.text, "生成された本文です。")
        self.assertEqual(captured["headers"], {})
        self.assertEqual(captured["payload"]["model"], "qwen3.5:9b-q4_K_M")

    def test_incomplete_response_raises_generation_error(self) -> None:
        settings_path = self._settings_path({})

        with mock.patch(
            "gapengine.synopsis._post_json",
            return_value={
                "done_reason": "length",
                "message": {"content": "途中まで"},
            },
        ):
            with self.assertRaises(GenerationError):
                generate_text(
                    "ollama",
                    "prompt",
                    settings_path=settings_path,
                )


class AvailabilityTests(unittest.TestCase):
    def _fake_urlopen(self, body: dict):
        response = mock.MagicMock()
        response.read.return_value = json.dumps(body).encode("utf-8")
        response.__enter__.return_value = response
        return response

    def test_model_present(self) -> None:
        config = {"model": "qwen3.5:9b-q4_K_M"}
        response = self._fake_urlopen(
            {"models": [{"name": "qwen3.5:9b-q4_K_M"}]}
        )
        with mock.patch(
            "gapengine.ollama.urllib.request.urlopen",
            return_value=response,
        ):
            result = ollama.availability(config)

        self.assertTrue(result["available"])
        self.assertIsNone(result["reason"])

    def test_model_missing(self) -> None:
        config = {"model": "qwen3.5:9b-q4_K_M"}
        response = self._fake_urlopen({"models": [{"name": "other:1b"}]})
        with mock.patch(
            "gapengine.ollama.urllib.request.urlopen",
            return_value=response,
        ):
            result = ollama.availability(config)

        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "model_missing")

    def test_server_unreachable(self) -> None:
        config = {"model": "qwen3.5:9b-q4_K_M"}
        with mock.patch(
            "gapengine.ollama.urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            result = ollama.availability(config)

        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "server_unreachable")


class EmptyResponseTests(unittest.TestCase):
    def test_blank_content_is_rejected_by_validate_response(self) -> None:
        settings_dir = Path(tempfile.mkdtemp(prefix="worldbloom-ollama-"))
        self.addCleanup(
            lambda: __import__("shutil").rmtree(
                settings_dir, ignore_errors=True
            )
        )
        settings_path = settings_dir / "settings.json"
        settings_path.write_text(
            json.dumps({"output": {"ollama": {}}}), encoding="utf-8"
        )
        with mock.patch(
            "gapengine.synopsis._post_json",
            return_value={"done_reason": "stop", "message": {"content": "  \n"}},
        ):
            with self.assertRaises(GenerationError):
                generate_text("ollama", "prompt", settings_path=settings_path)


class ConfigsAvailabilityTests(unittest.TestCase):
    def _generation(self):
        return {"backend": "ollama", "model": "qwen3.5:9b-q4_K_M",
                "limits": {"max_calls": 1, "call_timeout_seconds": 180,
                           "wall_seconds": 240, "max_saved_response_bytes": 128000}}

    def test_available_without_credentials(self) -> None:
        from execution.configs import generation_availability

        probe = {"available": True, "reason": None,
                 "model": "qwen3.5:9b-q4_K_M", "error": "must not leak"}
        with mock.patch("gapengine.ollama.availability", return_value=probe):
            result = generation_availability(self._generation())

        self.assertTrue(result["available"])
        self.assertIsNone(result["reason"])
        self.assertEqual(result["authentication"], "not_required")
        self.assertNotIn("error", result)

    def test_unreachable_server_is_reported(self) -> None:
        from execution.configs import generation_availability

        probe = {"available": False, "reason": "server_unreachable",
                 "model": "qwen3.5:9b-q4_K_M", "error": "refused"}
        with mock.patch("gapengine.ollama.availability", return_value=probe):
            result = generation_availability(self._generation())

        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "server_unreachable")
        self.assertNotIn("error", result)


if __name__ == "__main__":
    unittest.main()
