"""viewer.local_status: the topbar's GPU/AI status snapshot. No real GPU/Ollama/llama-server."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

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
        import time
        row = local_status._lease_row(
            {}, enabled=True,
            state=lambda: {"busy": True, "owner": "output_worker", "since": time.time()},
        )
        self.assertIn("たった今から", row["value"])

    def test_lease_row_elapsed_minutes(self) -> None:
        import time
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


if __name__ == "__main__":
    unittest.main()
