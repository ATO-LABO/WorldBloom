"""AIT-91 sensor, history and residency invariants; no GPU/model mutations."""
import json
import io
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from viewer.local_telemetry import TelemetryMonitor, parse_gpu_csv, read_gpu_metrics
from viewer.local_models import model_key, ollama_items, placement, read_loaded_models
from viewer import local_status


class SensorTests(unittest.TestCase):
    def test_multiple_devices_and_partial_sensor_failure(self):
        devices = parse_gpu_csv('0, gpu-a, First, 56, 3, 820, 12288\n1, gpu-b, "Name, comma", N/A, 100, 0, 8192')
        self.assertEqual([d['id'] for d in devices], ['gpu-a', 'gpu-b'])
        self.assertEqual(devices[1]['name'], 'Name, comma')
        self.assertIsNone(devices[1]['temperature_c'])
        self.assertEqual(devices[1]['memory_used_mib'], 0)

    def test_invalid_numbers_are_missing_and_duplicate_devices_are_rejected(self):
        devices = parse_gpu_csv('0, a, GPU, NaN, 101, 13000, 12000\n0, a, GPU, 40, 2, 1, 12000\nmalformed')
        self.assertEqual(len(devices), 1)
        for key in ('temperature_c', 'utilization_percent', 'memory_used_mib'):
            self.assertIsNone(devices[0][key])
        self.assertEqual(parse_gpu_csv('0, a, GPU, inf, -1, N/A, N/A')[0]['memory_total_mib'], None)

    def test_missing_nvidia_smi_and_timeouts_stay_unavailable(self):
        for error in (FileNotFoundError(), subprocess.TimeoutExpired('nvidia-smi', 3)):
            def run(*a, **kw):
                raise error
            self.assertEqual(read_gpu_metrics(run=run), {'devices': [], 'reason': 'unavailable'})

    def test_single_command_probes_all_metrics_without_shell(self):
        calls = []
        def run(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0, '0, a, GPU, 56, 3, 820, 12288')
        self.assertEqual(read_gpu_metrics(run=run)['devices'][0]['temperature_c'], 56)
        self.assertEqual(len(calls), 1)
        self.assertNotIn('shell', calls[0][1])
        self.assertIn('utilization.gpu', calls[0][0][1])


class HistoryTests(unittest.TestCase):
    def test_retention_missing_samples_and_independent_copies(self):
        at = [0]
        result = [{'devices': [{'id': 'a', 'temperature_c': 56}], 'reason': None}]
        monitor = TelemetryMonitor(read=lambda: result[0], clock=lambda: at[0], interval=2, retention=6)
        monitor.sample()
        at[0] = 2; result[0] = {'devices': [], 'reason': 'unavailable'}; monitor.sample()
        at[0] = 4; result[0] = {'devices': [{'id': 'b', 'temperature_c': 50}], 'reason': None}; monitor.sample()
        snap = monitor.snapshot(start=False)
        self.assertEqual([s['at'] for s in snap['history']], [0, 2, 4])
        self.assertEqual(snap['history'][1]['devices'], [])
        snap['history'][0]['devices'][0]['temperature_c'] = 999
        self.assertEqual(monitor.snapshot(start=False)['history'][0]['devices'][0]['temperature_c'], 56)
        at[0] = 10; monitor.sample()
        self.assertEqual([s['at'] for s in monitor.snapshot(start=False)['history']], [4, 10])

    def test_clock_rollback_clears_incomparable_history(self):
        at = [100]
        m = TelemetryMonitor(read=lambda: {'devices': []}, clock=lambda: at[0])
        m.sample(); at[0] = 50; m.sample()
        self.assertEqual(m.snapshot(start=False)['started_at'], 50)
        self.assertEqual(len(m.snapshot(start=False)['history']), 1)

    def test_events_only_record_observed_state_changes(self):
        m = TelemetryMonitor(read=lambda: {'devices': []})
        m.record_context(busy=False); m.record_context(busy=True)
        m.record_context(busy=True); m.record_context(busy=False)
        self.assertEqual([e['label'] for e in m.snapshot(start=False)['events']], ['WorldBloom処理開始', 'WorldBloom処理終了'])

    def test_reader_is_lazy_singleton_per_monitor_and_stops_on_close(self):
        calls = []
        m = TelemetryMonitor(read=lambda: (calls.append(1) or {'devices': []}), interval=100)
        self.assertEqual(calls, [])
        threads = [threading.Thread(target=m.start) for _ in range(5)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(calls, [1])
        m.close(); self.assertFalse(m._thread.is_alive())
        m.start(); self.assertEqual(calls, [1])


class ModelTests(unittest.TestCase):
    def test_latest_alias_never_matches_other_tags(self):
        self.assertEqual(model_key('qwen:latest'), model_key('qwen'))
        self.assertNotEqual(model_key('qwen:8b'), model_key('qwen:4b'))
        self.assertEqual(model_key('host:5000/path/qwen:latest'), 'host:5000/path/qwen')

    def test_selected_model_does_not_borrow_another_models_residency(self):
        items = ollama_items(['a:8b', 'b:8b'], [{'name': 'b:8b', 'size': 100, 'size_vram': 100}], 'a:8b')
        self.assertEqual(items[0]['state'], 'unloaded')
        self.assertEqual(items[1]['state'], 'loaded')
        self.assertFalse(items[1]['configured'])
        self.assertEqual(items[1]['execution'], 'unknown')

    def test_residency_placement_requires_positive_size_and_real_vram(self):
        self.assertEqual(placement({'size': 100, 'size_vram': 100}), 'gpu')
        self.assertEqual(placement({'size': 100, 'size_vram': 50}), 'mixed')
        self.assertEqual(placement({'size': 100, 'size_vram': 0}), 'cpu')
        for model in ({}, {'size': 100}, {'size': 0, 'size_vram': 0}, {'size': 100, 'size_vram': float('nan')}):
            self.assertEqual(placement(model), 'unknown')

    def test_failed_ps_probe_is_not_an_empty_resident_list(self):
        items = ollama_items(['a'], None, 'a')
        self.assertEqual(items[0]['state'], 'unknown')
        with patch('viewer.local_models.urllib.request.urlopen', side_effect=OSError()):
            self.assertIsNone(read_loaded_models('http://example.invalid'))

    def test_saved_model_catalog_and_external_resident_models_are_merged(self):
        items = ollama_items(['a:latest', 'b'], [{'name': 'external', 'size': 100, 'size_vram': 0}], 'a')
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]['name'], 'a')
        self.assertEqual(items[1]['state'], 'loaded')
        self.assertEqual(items[2]['state'], 'unloaded')

    def test_malformed_resident_entries_are_not_treated_as_unloaded(self):
        for payload in ({'models': [None]}, {'models': [{}]}, {'models': [{'name': 123}]}):
            with patch('viewer.local_models.urllib.request.urlopen', return_value=io.BytesIO(json.dumps(payload).encode())):
                self.assertIsNone(read_loaded_models('http://example.invalid'))


class SnapshotFactsTests(unittest.TestCase):
    def test_llama_health_does_not_prove_configured_model_loaded(self):
        row = local_status._llama_row({'model': 'configured'}, is_ready=lambda _: True,
                                      list_models=lambda _: (['actual'], None))
        self.assertEqual(row['models'][0]['name'], 'configured')
        self.assertEqual(row['models'][0]['state'], 'unknown')
        self.assertEqual(row['models'][1]['name'], 'actual')
        self.assertEqual(row['models'][1]['state'], 'loaded')
        self.assertFalse(row['models'][1]['configured'])

    def test_llama_model_list_failure_keeps_residency_unknown(self):
        row = local_status._llama_row({'model': 'configured'}, is_ready=lambda _: True,
                                      list_models=lambda _: ([], 'server_unreachable'))
        self.assertEqual(row['server_state'], 'up')
        self.assertFalse(row['models_known'])
        self.assertEqual(row['models'][0]['state'], 'unknown')

    def make_snapshot(self, output, **overrides):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'settings.json'
            p.write_text(json.dumps({'output': output}), encoding='utf-8')
            kwargs = dict(temperature_read=lambda: None, lease_state=lambda: {'busy': False},
                          llama_is_ready=lambda _: False, ollama_list_models=lambda _: (['a', 'b'], None),
                          ollama_loaded_models=lambda _: [{'name': 'b', 'size': 100, 'size_vram': 100}],
                          has_preloaded_llama_server=lambda: False)
            kwargs.update(overrides)
            return local_status.snapshot(p, **kwargs)

    def test_preload_targets_configured_unloaded_model_even_if_another_is_loaded(self):
        s = self.make_snapshot({'default_backend': 'ollama', 'ollama': {'model': 'a'}, 'gpu_guard': {}})
        row = next(r for r in s['rows'] if r['id'] == 'ollama')
        self.assertTrue(row['can_preload_selected'])
        self.assertTrue(row['models'][0]['selected'])
        self.assertFalse(row['models'][1]['selected'])

    def test_unknown_state_and_busy_lease_disable_mutations(self):
        s = self.make_snapshot({'default_backend': 'ollama', 'gpu_guard': {}}, ollama_loaded_models=lambda _: None)
        row = next(r for r in s['rows'] if r['id'] == 'ollama')
        self.assertFalse(row['can_preload_selected']); self.assertFalse(row['can_unload'])
        self.assertTrue(row['actions_disabled'])
        s = self.make_snapshot({'default_backend': 'ollama', 'gpu_guard': {}}, lease_state=lambda: {'busy': True, 'owner': 'output:example'})
        row = next(r for r in s['rows'] if r['id'] == 'ollama')
        self.assertTrue(row['actions_disabled'])
        self.assertEqual(row['models'][1]['state'], 'loaded')

    def test_telemetry_is_the_single_temperature_source_and_partial_fields_stay_missing(self):
        m = TelemetryMonitor(read=lambda: {'devices': [{'id': 'a', 'index': 0, 'temperature_c': 53}], 'reason': None})
        m.sample()
        with patch.object(m, 'start'):
            s = self.make_snapshot({'gpu_guard': {}}, telemetry=m,
                                   temperature_read=lambda: self.fail('Duplicate temperature probe'))
        self.assertIn('53℃', s['rows'][0]['value'])
        self.assertEqual(s['gpu']['devices'][0]['temperature_c'], 53)
        self.assertEqual(s['thermal']['pause_at'], 78)


if __name__ == '__main__':
    unittest.main()
