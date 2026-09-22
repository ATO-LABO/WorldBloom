"""viewer.local_status: the topbar's GPU/AI status snapshot. No real GPU/Ollama/llama-server."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from viewer import local_status


def _row(rows, row_id):
    return next(row for row in rows if row["id"] == row_id)


# Fakes for snapshot()'s injection seams -- never touch nvidia-smi/localhost.
def _no_temperature():
    return None


def _lease_free():
    return {"busy": False}


def _llama_not_ready(config):
    return False


def _ollama_unreachable(config):
    return [], "server_unreachable"


def _no_loaded_models(base_url):
    return []


def _no_preloaded_llama_server():
    return False


class RowBuilderTests(unittest.TestCase):
    def test_temperature_row_levels(self) -> None:
        thermal = {"pause_at": 78, "resume_at": 70}
        self.assertEqual(local_status._temperature_row(thermal, True, read=lambda: None)["level"], "off")
        self.assertEqual(local_status._temperature_row(thermal, True, read=lambda: 50.0)["level"], "ok")
        self.assertEqual(local_status._temperature_row(thermal, True, read=lambda: 72.0)["level"], "warn")
        self.assertEqual(local_status._temperature_row(thermal, True, read=lambda: 80.0)["level"], "warn")

    def test_temperature_row_does_not_claim_autopause_when_guard_disabled(self) -> None:
        thermal = {"pause_at": 78, "resume_at": 70}
        row = local_status._temperature_row(thermal, False, read=lambda: 80.0)
        self.assertEqual(row["level"], "warn")
        self.assertNotIn("一時停止されます", row["value"])

    def test_lease_row_disabled_guard(self) -> None:
        row = local_status._lease_row({}, enabled=False)
        self.assertEqual(row["level"], "off")

    def test_lease_row_reports_holder_without_since(self) -> None:
        row = local_status._lease_row(
            {}, enabled=True,
            state=lambda: {"busy": True, "owner": "output_worker", "since": None},
        )
        self.assertEqual(row["level"], "warn")
        self.assertIn("output_worker", row["value"])

    def test_lease_row_just_now(self) -> None:
        row = local_status._lease_row(
            {}, enabled=True,
            state=lambda: {"busy": True, "owner": "output_worker", "since": time.time()},
        )
        self.assertIn("たった今から", row["value"])

    def test_lease_row_elapsed_minutes(self) -> None:
        row = local_status._lease_row(
            {}, enabled=True,
            state=lambda: {"busy": True, "owner": "output_worker", "since": time.time() - 300},
        )
        self.assertIn("5分前から", row["value"])

    def test_llama_row_not_configured_but_unreachable_is_unset(self) -> None:
        row = local_status._llama_row({}, is_ready=_llama_not_ready)
        self.assertEqual(row["value"], "未設定")
        self.assertEqual(row["level"], "off")

    def test_llama_row_probes_default_url_even_without_config(self) -> None:
        # Symmetric with _ollama_row: a server already listening at the
        # default base_url is real signal even with no explicit config.
        row = local_status._llama_row({}, is_ready=lambda config: True)
        self.assertEqual(row["level"], "ok")

    def test_llama_row_ready(self) -> None:
        row = local_status._llama_row({"model": "bonsai2-27b"}, is_ready=lambda config: True)
        self.assertEqual(row["level"], "ok")
        self.assertIn("bonsai2-27b", row["value"])

    def test_llama_row_stopped_with_launch_configured(self) -> None:
        row = local_status._llama_row(
            {"launch": ["llama-server.exe"]}, is_ready=_llama_not_ready,
        )
        self.assertEqual(row["level"], "off")
        self.assertIn("自動起動", row["value"])

    def test_ollama_row_unreachable(self) -> None:
        row = local_status._ollama_row({}, {}, list_models=_ollama_unreachable)
        self.assertEqual(row["level"], "off")

    def test_ollama_row_prefers_the_backend_config_base_url(self) -> None:
        # gpu_guard.ollama_base_url is arbitration-only; the row that carries
        # in_use must probe the URL generation would actually use.
        seen = {}

        def list_models(config):
            seen["base_url"] = config["base_url"]
            return ["qwen"], None

        local_status._ollama_row(
            {"ollama_base_url": "http://guard-url"}, {"base_url": "http://backend-url"},
            list_models=list_models, loaded_models=_no_loaded_models,
        )
        self.assertEqual(seen["base_url"], "http://backend-url")

    def test_ollama_row_idle_vs_loaded(self) -> None:
        idle = local_status._ollama_row(
            {}, {}, list_models=lambda config: (["qwen"], None), loaded_models=lambda base_url: [],
        )
        self.assertEqual(idle["level"], "ok")
        self.assertIn("モデル未読み込み", idle["value"])
        loaded = local_status._ollama_row(
            {}, {}, list_models=lambda config: (["qwen"], None),
            loaded_models=lambda base_url: [{"name": "qwen"}],
        )
        self.assertIn("qwen", loaded["value"])


class SnapshotTests(unittest.TestCase):
    def _snapshot(self, settings_path, **overrides):
        kwargs = dict(
            temperature_read=_no_temperature,
            lease_state=_lease_free,
            llama_is_ready=_llama_not_ready,
            ollama_list_models=_ollama_unreachable,
            ollama_loaded_models=_no_loaded_models,
            has_preloaded_llama_server=_no_preloaded_llama_server,
        )
        kwargs.update(overrides)
        return local_status.snapshot(settings_path, **kwargs)

    def test_missing_settings_has_no_backend(self) -> None:
        snapshot = self._snapshot(None)
        self.assertIsNone(snapshot["backend"])
        self.assertEqual(snapshot["backend_label"], "文章生成の送り先: 未設定")
        self.assertEqual(_row(snapshot["rows"], "gpu_lease")["level"], "off")
        self.assertEqual({row["id"] for row in snapshot["rows"]},
                          {"gpu_temp", "gpu_lease", "llama_server", "ollama"})

    def test_broken_settings_json_does_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings_path.write_text("not json", encoding="utf-8")
            snapshot = self._snapshot(settings_path)
            self.assertIsNone(snapshot["backend"])

    def test_in_use_flag_follows_default_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings_path.write_text(
                json.dumps({"output": {"default_backend": "ollama"}}), encoding="utf-8",
            )
            snapshot = self._snapshot(settings_path)
            self.assertEqual(snapshot["backend"], "ollama")
            self.assertTrue(_row(snapshot["rows"], "ollama")["in_use"])
            self.assertFalse(_row(snapshot["rows"], "llama_server")["in_use"])

    def test_empty_gpu_guard_mapping_is_still_enabled(self) -> None:
        # {"gpu_guard": {}} is a Mapping, so local_gpu_session() treats it as
        # enabled -- the status row must agree, not read the empty dict as falsy.
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings_path.write_text(json.dumps({"output": {"gpu_guard": {}}}), encoding="utf-8")
            snapshot = self._snapshot(settings_path, lease_state=_lease_free)
            self.assertEqual(_row(snapshot["rows"], "gpu_lease")["value"], "空き")

    def test_missing_gpu_guard_key_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings_path.write_text(json.dumps({"output": {}}), encoding="utf-8")
            snapshot = self._snapshot(settings_path)
            self.assertIn("無効", _row(snapshot["rows"], "gpu_lease")["value"])

    def test_can_preload_llama_server_when_stopped_and_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings_path.write_text(json.dumps({
                "output": {
                    "default_backend": "llama-server", "gpu_guard": {}, "llama-server": {"launch": ["x"]},
                },
            }), encoding="utf-8")
            row = _row(self._snapshot(settings_path)["rows"], "llama_server")
            self.assertTrue(row["can_preload"])
            self.assertFalse(row["can_unload"])
            self.assertNotIn("ready", row)
            self.assertNotIn("has_launch", row)

    def test_not_in_use_never_shows_a_button_even_if_otherwise_eligible(self) -> None:
        # VRAM can't hold both backends -- only the configured one's row may
        # ever offer preload/unload, regardless of that row's own state.
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings_path.write_text(json.dumps({
                "output": {
                    "default_backend": "llama-server", "gpu_guard": {}, "llama-server": {"launch": ["x"]},
                },
            }), encoding="utf-8")
            row = _row(self._snapshot(settings_path)["rows"], "ollama")
            self.assertFalse(row["in_use"])
            self.assertFalse(row["can_preload"])
            self.assertFalse(row["can_unload"])

    def test_cannot_preload_llama_server_without_launch_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings_path.write_text(
                json.dumps({"output": {"default_backend": "llama-server", "gpu_guard": {}}}), encoding="utf-8",
            )
            row = _row(self._snapshot(settings_path)["rows"], "llama_server")
            self.assertFalse(row["can_preload"])

    def test_can_unload_llama_server_when_ready_and_preloaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings_path.write_text(
                json.dumps({"output": {"default_backend": "llama-server", "gpu_guard": {}}}), encoding="utf-8",
            )
            row = _row(self._snapshot(
                settings_path, llama_is_ready=lambda config: True,
                has_preloaded_llama_server=lambda: True,
            )["rows"], "llama_server")
            self.assertFalse(row["can_preload"])
            self.assertTrue(row["can_unload"])

    def test_ready_but_not_our_preload_cannot_unload(self) -> None:
        # A llama-server started by hand (or by a real generation session) is
        # still reachable, but only holder.json's "preloaded" pid is ours to stop.
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings_path.write_text(
                json.dumps({"output": {"default_backend": "llama-server", "gpu_guard": {}}}), encoding="utf-8",
            )
            row = _row(self._snapshot(
                settings_path, llama_is_ready=lambda config: True,
                has_preloaded_llama_server=_no_preloaded_llama_server,
            )["rows"], "llama_server")
            self.assertFalse(row["can_unload"])

    def test_can_preload_ollama_when_not_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings_path.write_text(
                json.dumps({"output": {"default_backend": "ollama", "gpu_guard": {}}}), encoding="utf-8",
            )
            row = _row(self._snapshot(settings_path)["rows"], "ollama")
            self.assertTrue(row["can_preload"])
            self.assertFalse(row["can_unload"])

    def test_can_preload_ollama_when_server_up_but_idle(self) -> None:
        # Regression for review H1: the server being reachable (level="ok")
        # does NOT mean a model is loaded -- "idle" (level="ok", nothing
        # loaded) is exactly the state a preload button should be offered
        # for. The old `level != "ok"` gate hid the button here.
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings_path.write_text(
                json.dumps({"output": {"default_backend": "ollama", "gpu_guard": {}}}), encoding="utf-8",
            )
            row = _row(self._snapshot(
                settings_path,
                ollama_list_models=lambda config: (["qwen"], None),
                ollama_loaded_models=lambda base_url: [],
            )["rows"], "ollama")
            self.assertEqual(row["level"], "ok")
            self.assertTrue(row["can_preload"])
            self.assertFalse(row["can_unload"])

    def test_can_unload_ollama_when_model_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings_path.write_text(
                json.dumps({"output": {"default_backend": "ollama", "gpu_guard": {}}}), encoding="utf-8",
            )
            row = _row(self._snapshot(
                settings_path,
                ollama_list_models=lambda config: (["qwen"], None),
                ollama_loaded_models=lambda base_url: [{"name": "qwen"}],
            )["rows"], "ollama")
            self.assertFalse(row["can_preload"])
            self.assertTrue(row["can_unload"])
            self.assertNotIn("has_model_loaded", row)

    def test_starting_overrides_the_matching_row_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings_path.write_text(json.dumps({
                "output": {
                    "default_backend": "llama-server", "gpu_guard": {}, "llama-server": {"launch": ["x"]},
                },
            }), encoding="utf-8")
            local_status._preload_state.update(phase="starting", backend="llama-server", error=None)
            try:
                rows = self._snapshot(settings_path)["rows"]
            finally:
                local_status._preload_state.update(phase="idle", backend=None, error=None)
            llama_row = _row(rows, "llama_server")
            ollama_row = _row(rows, "ollama")
            self.assertIn("起動中…", llama_row["value"])
            self.assertEqual(llama_row["level"], "warn")
            self.assertFalse(llama_row["can_preload"])
            self.assertNotIn("起動中…", ollama_row["value"])

    def test_preloading_flag_reflects_phase(self) -> None:
        self.assertFalse(self._snapshot(None)["preloading"])
        local_status._preload_state.update(phase="starting", backend="llama-server", error=None)
        try:
            self.assertTrue(self._snapshot(None)["preloading"])
        finally:
            local_status._preload_state.update(phase="idle", backend=None, error=None)

    def test_preload_error_surfaces_until_next_attempt(self) -> None:
        local_status._preload_state.update(phase="idle", backend="llama-server", error="失敗しました")
        try:
            snapshot = self._snapshot(None)
        finally:
            local_status._preload_state.update(phase="idle", backend=None, error=None)
        self.assertEqual(snapshot["preload_error"], "失敗しました")


class PreloadOrchestrationTests(unittest.TestCase):
    def setUp(self) -> None:
        local_status._preload_state.update(phase="idle", backend=None, error=None)
        self.addCleanup(local_status._preload_state.update, phase="idle", backend=None, error=None)

    def _settings_path(self, tmp, output: dict) -> Path:
        settings_path = Path(tmp) / "settings.json"
        settings_path.write_text(json.dumps({"output": output}), encoding="utf-8")
        return settings_path

    def test_rejects_cloud_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = self._settings_path(tmp, {"default_backend": "openai"})
            with self.assertRaisesRegex(ValueError, "ローカル"):
                local_status.start_preload(settings_path)
            with self.assertRaisesRegex(ValueError, "ローカル"):
                local_status.stop_preload(settings_path, "openai")

    def test_rejects_concurrent_preload(self) -> None:
        local_status._preload_state.update(phase="starting", backend="llama-server")
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = self._settings_path(tmp, {
                "default_backend": "llama-server", "gpu_guard": {}, "llama-server": {"launch": ["x"]},
            })
            with self.assertRaisesRegex(ValueError, "読み込み中"):
                local_status.start_preload(settings_path)

    def test_dispatches_to_llama_server_and_resets_phase_on_success(self) -> None:
        done = threading.Event()
        calls = []

        def fake_preload(output_settings):
            calls.append(output_settings)
            done.set()

        with tempfile.TemporaryDirectory() as tmp:
            settings_path = self._settings_path(tmp, {
                "default_backend": "llama-server", "gpu_guard": {}, "llama-server": {"launch": ["x"]},
            })
            with mock.patch("gapengine.gpu_guard.preload_llama_server", side_effect=fake_preload):
                result = local_status.start_preload(settings_path)
                self.assertEqual(result, {"backend": "llama-server"})
                self.assertTrue(done.wait(timeout=5))
        deadline = time.monotonic() + 5
        while local_status._preload_state["phase"] != "idle" and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(local_status._preload_state["phase"], "idle")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["llama-server"], {"launch": ["x"]})

    def test_records_error_and_still_resets_phase(self) -> None:
        done = threading.Event()

        def fake_preload(output_settings):
            done.set()
            raise ValueError("失敗しました")

        with tempfile.TemporaryDirectory() as tmp:
            settings_path = self._settings_path(tmp, {
                "default_backend": "llama-server", "gpu_guard": {}, "llama-server": {"launch": ["x"]},
            })
            with mock.patch("gapengine.gpu_guard.preload_llama_server", side_effect=fake_preload):
                local_status.start_preload(settings_path)
                self.assertTrue(done.wait(timeout=5))
        deadline = time.monotonic() + 5
        while local_status._preload_state["phase"] != "idle" and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(local_status._preload_state["error"], "失敗しました")

    def test_stop_dispatches_to_ollama_unload(self) -> None:
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = self._settings_path(tmp, {"gpu_guard": {}, "default_backend": "ollama"})
            with mock.patch(
                "gapengine.gpu_guard.unload_preloaded_ollama", side_effect=lambda s: calls.append(s),
            ):
                result = local_status.stop_preload(settings_path, "ollama")
        self.assertEqual(result, {"backend": "ollama"})
        self.assertEqual(len(calls), 1)

    def test_stop_targets_the_named_backend_even_after_a_config_switch(self) -> None:
        # Regression for review M1: preloaded llama-server, then switched
        # default_backend to ollama -- stop_preload must still be able to
        # release the llama-server it actually warmed up.
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = self._settings_path(tmp, {"gpu_guard": {}, "default_backend": "ollama"})
            with mock.patch(
                "gapengine.gpu_guard.stop_preloaded_llama_server", side_effect=lambda: calls.append(True),
            ):
                result = local_status.stop_preload(settings_path, "llama-server")
        self.assertEqual(result, {"backend": "llama-server"})
        self.assertEqual(len(calls), 1)

    def test_stop_clears_a_stale_preload_error(self) -> None:
        local_status._preload_state.update(error="前回失敗しました")
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = self._settings_path(tmp, {"gpu_guard": {}})
            with mock.patch("gapengine.gpu_guard.stop_preloaded_llama_server"):
                local_status.stop_preload(settings_path, "llama-server")
        self.assertIsNone(local_status._preload_state["error"])


if __name__ == "__main__":
    unittest.main()
