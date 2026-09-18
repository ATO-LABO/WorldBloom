"""Tests for the local llama-server output backend (no network access)."""

from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from gapengine import llama_server
import gapengine.synopsis as synopsis_module
from gapengine.synopsis import GenerationError, generate_text


class BuildRequestTests(unittest.TestCase):
    def test_default_payload_shape(self) -> None:
        url, payload = llama_server.build_request({}, "prompt text")

        self.assertEqual(url, "http://127.0.0.1:8089/v1/chat/completions")
        self.assertEqual(payload["model"], llama_server.DEFAULT_MODEL)
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": True})
        self.assertEqual(payload["temperature"], 0.7)
        self.assertEqual(payload["max_tokens"], 8192)
        self.assertEqual(
            payload["messages"],
            [{"role": "user", "content": "prompt text"}],
        )

    def test_think_false_is_honored(self) -> None:
        _, payload = llama_server.build_request({"think": False}, "prompt")

        self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": False})

    def test_seed_is_added_to_payload(self) -> None:
        _, payload = llama_server.build_request({"seed": 7}, "prompt")

        self.assertEqual(payload["seed"], 7)

    def test_base_url_trailing_slash_is_stripped(self) -> None:
        url, _ = llama_server.build_request(
            {"base_url": "http://127.0.0.1:8089/"},
            "prompt",
        )

        self.assertEqual(url, "http://127.0.0.1:8089/v1/chat/completions")

    def test_custom_model_and_options(self) -> None:
        _, payload = llama_server.build_request(
            {"model": "custom-model", "options": {"max_tokens": 1}},
            "prompt",
        )

        self.assertEqual(payload["model"], "custom-model")
        # Partial options merge over the defaults; temperature must survive.
        self.assertEqual(payload["temperature"], 0.7)
        self.assertEqual(payload["max_tokens"], 1)


class ExtractTextTests(unittest.TestCase):
    def test_incomplete_response_raises(self) -> None:
        with self.assertRaises(ValueError):
            llama_server.extract_text(
                {
                    "choices": [
                        {"finish_reason": "length", "message": {"content": "text"}}
                    ]
                }
            )

    def test_non_string_content_raises(self) -> None:
        with self.assertRaises(ValueError):
            llama_server.extract_text(
                {
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": None}}
                    ]
                }
            )

    def test_empty_choices_raises(self) -> None:
        with self.assertRaises(ValueError):
            llama_server.extract_text({"choices": []})

    def test_stop_returns_content(self) -> None:
        text = llama_server.extract_text(
            {
                "choices": [
                    {"finish_reason": "stop", "message": {"content": "本文"}}
                ]
            }
        )

        self.assertEqual(text, "本文")


class GenerateTextLlamaServerTests(unittest.TestCase):
    def _settings_path(self, config: dict) -> Path:
        settings_dir = Path(tempfile.mkdtemp(prefix="worldbloom-llama-server-"))
        self.addCleanup(
            lambda: __import__("shutil").rmtree(
                settings_dir, ignore_errors=True
            )
        )
        settings_path = settings_dir / "settings.json"
        settings_path.write_text(
            json.dumps({"output": {"llama-server": config}}),
            encoding="utf-8",
        )
        return settings_path

    def test_ok_without_api_key(self) -> None:
        settings_path = self._settings_path({"model": "bonsai2-27b"})
        captured: dict = {}

        def fake_post_json(url, headers, payload, *, timeout):
            captured["url"] = url
            captured["headers"] = headers
            captured["payload"] = payload
            return {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "生成された本文です。"},
                    }
                ]
            }

        with mock.patch(
            "gapengine.synopsis._post_json",
            side_effect=fake_post_json,
        ):
            result = generate_text(
                "llama-server",
                "prompt",
                settings_path=settings_path,
            )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.text, "生成された本文です。")
        self.assertEqual(captured["headers"], {})
        self.assertEqual(captured["payload"]["model"], "bonsai2-27b")

    def test_incomplete_response_raises_generation_error(self) -> None:
        settings_path = self._settings_path({})

        with mock.patch(
            "gapengine.synopsis._post_json",
            return_value={
                "choices": [
                    {"finish_reason": "length", "message": {"content": "途中まで"}}
                ]
            },
        ):
            with self.assertRaises(GenerationError):
                generate_text(
                    "llama-server",
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
        config = {"model": "bonsai2-27b"}
        response = self._fake_urlopen({"data": [{"id": "bonsai2-27b"}]})
        with mock.patch(
            "gapengine.llama_server.urllib.request.urlopen",
            return_value=response,
        ):
            result = llama_server.availability(config)

        self.assertTrue(result["available"])
        self.assertIsNone(result["reason"])

    def test_model_missing(self) -> None:
        config = {"model": "bonsai2-27b"}
        response = self._fake_urlopen({"data": [{"id": "other-model"}]})
        with mock.patch(
            "gapengine.llama_server.urllib.request.urlopen",
            return_value=response,
        ):
            result = llama_server.availability(config)

        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "model_missing")

    def test_server_unreachable(self) -> None:
        config = {"model": "bonsai2-27b"}
        with mock.patch(
            "gapengine.llama_server.urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            result = llama_server.availability(config)

        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "server_unreachable")


class EmptyResponseTests(unittest.TestCase):
    def test_blank_content_is_rejected_by_validate_response(self) -> None:
        settings_dir = Path(tempfile.mkdtemp(prefix="worldbloom-llama-server-"))
        self.addCleanup(
            lambda: __import__("shutil").rmtree(
                settings_dir, ignore_errors=True
            )
        )
        settings_path = settings_dir / "settings.json"
        settings_path.write_text(
            json.dumps({"output": {"llama-server": {}}}), encoding="utf-8"
        )
        with mock.patch(
            "gapengine.synopsis._post_json",
            return_value={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": "  \n"}}
                ]
            },
        ):
            with self.assertRaises(GenerationError):
                generate_text(
                    "llama-server", "prompt", settings_path=settings_path
                )


class ConfigsAvailabilityTests(unittest.TestCase):
    def _generation(self):
        return {"backend": "llama-server", "model": "bonsai2-27b",
                "limits": {"max_calls": 1, "call_timeout_seconds": 900,
                           "wall_seconds": 3600, "max_saved_response_bytes": 128000}}

    def test_available_without_credentials(self) -> None:
        from execution.configs import generation_availability

        probe = {"available": True, "reason": None,
                 "model": "bonsai2-27b", "error": "must not leak"}
        with mock.patch("gapengine.llama_server.availability", return_value=probe):
            result = generation_availability(self._generation())

        self.assertTrue(result["available"])
        self.assertIsNone(result["reason"])
        self.assertEqual(result["authentication"], "not_required")
        self.assertNotIn("error", result)

    def test_unreachable_server_is_reported(self) -> None:
        from execution.configs import generation_availability

        probe = {"available": False, "reason": "server_unreachable",
                 "model": "bonsai2-27b", "error": "refused"}
        with mock.patch("gapengine.llama_server.availability", return_value=probe):
            result = generation_availability(self._generation())

        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "server_unreachable")
        self.assertNotIn("error", result)


class UiGenerationPathTests(unittest.TestCase):
    """execution.generation: preflight, transport and validate_response for llama-server."""

    def _request(self, credentials=None):
        return {"backend": "llama-server", "model": "bonsai2-27b", "prompt": "prompt",
                "credentials": credentials if credentials is not None else {},
                "limits": {"max_saved_response_bytes": 8000, "call_timeout_seconds": 5}}

    def test_preflight_needs_no_api_key(self) -> None:
        from execution.generation import preflight

        self.assertIsNone(preflight(self._request()))

    def test_transport_posts_to_chat_completions_without_auth_header(self) -> None:
        from execution.generation import transport

        seen = {}

        class Stream:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self, n):
                return (
                    b'{"choices": [{"finish_reason": "stop", '
                    b'"message": {"content": "x"}}]}'
                )

        def fake_urlopen(req, timeout):
            seen["url"] = req.full_url
            seen["headers"] = dict(req.header_items())
            seen["body"] = json.loads(req.data.decode("utf-8"))
            return Stream()

        with mock.patch("execution.generation.urllib.request.urlopen", side_effect=fake_urlopen):
            response = transport(self._request({"base_url": "http://host:1/"}), None)

        self.assertEqual(seen["url"], "http://host:1/v1/chat/completions")
        self.assertNotIn("Authorization", seen["headers"])
        self.assertEqual(seen["body"]["chat_template_kwargs"], {"enable_thinking": True})
        self.assertEqual(seen["body"]["model"], "bonsai2-27b")
        self.assertFalse(response.truncated)

    def test_validate_response_accepts_stop_and_rejects_length(self) -> None:
        from execution.generation import Response, validate_response

        ok = json.dumps(
            {"choices": [{"finish_reason": "stop", "message": {"content": "本文"}}]}
        ).encode("utf-8")
        self.assertEqual(validate_response("llama-server", Response(ok)), "本文")
        for payload in (
            {"choices": [{"finish_reason": "length", "message": {"content": "x"}}]},
            {"choices": [{"finish_reason": "stop", "message": {"content": 1}}]},
            {"choices": []},
        ):
            with self.assertRaises(ValueError):
                validate_response(
                    "llama-server", Response(json.dumps(payload).encode("utf-8"))
                )

    def test_worker_credentials_pass_connection_options_only(self) -> None:
        from execution.output_worker import credentials

        settings_dir = Path(tempfile.mkdtemp(prefix="worldbloom-llama-server-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(settings_dir, ignore_errors=True))
        path = settings_dir / "settings.json"
        path.write_text(json.dumps({"output": {"llama-server": {
            "model": "m", "base_url": "http://h:1", "options": {"max_tokens": 8}, "think": False,
            "extra": "dropped"}}}), encoding="utf-8")

        self.assertEqual(credentials(path, "llama-server"),
                         {"base_url": "http://h:1", "options": {"max_tokens": 8}, "think": False})


class OutputSettingsTests(unittest.TestCase):
    def test_list_models_is_live(self) -> None:
        from execution.output_settings import list_models

        settings_dir = Path(tempfile.mkdtemp(prefix="worldbloom-llama-server-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(settings_dir, ignore_errors=True))
        settings_path = settings_dir / "settings.json"
        settings_path.write_text(json.dumps({"output": {"llama-server": {}}}), encoding="utf-8")

        with mock.patch(
            "gapengine.llama_server.list_models",
            return_value=(["bonsai2-27b"], None),
        ):
            result = list_models(settings_path, "llama-server")

        self.assertEqual(result, {"models": ["bonsai2-27b"], "live": True, "reason": None})

    def test_default_limits_match_local_server_timeouts(self) -> None:
        from execution.output_settings import default_limits

        limits = default_limits("llama-server")

        self.assertEqual(limits["call_timeout_seconds"], 900)
        self.assertEqual(limits["wall_seconds"], 3600)


if __name__ == "__main__":
    unittest.main()
