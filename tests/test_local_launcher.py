"""Offline launcher safety; no GPU query, model load or real LLM."""
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('local_launcher',ROOT/'tools/local_launcher.py')
launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)

class LauncherTests(unittest.TestCase):
    def test_current_ollama_runner_is_recognized_by_install_path(self):
        with patch.dict(launcher.os.environ,{'LOCALAPPDATA':r'C:\Users\example\AppData\Local'}):
            launcher.check_gpu([['17',r'C:\Users\example\AppData\Local\Programs\Ollama\lib\ollama\llama-server.exe','C']])
            with self.assertRaises(RuntimeError):launcher.check_gpu([['18',r'C:\other\llama-server.exe','C']])

    def test_verified_desktop_paths_are_allowed_but_spoofs_and_compute_are_not(self):
        launcher.check_gpu([['1',r'C:\Windows\explorer.exe','C+G'],
            ['2',r'C:\Program Files\WindowsApps\OpenAI.Codex_123_x64\app\ChatGPT.exe','C+G']])
        for row in [['3',r'C:\temp\explorer.exe','C+G'],['4',r'C:\ComfyUI\python.exe','C'],
                    ['5',r'C:\Windows\explorer.exe','C']]:
            with self.subTest(row=row),self.assertRaises(RuntimeError):launcher.check_gpu([row])

    def test_unknown_graphics_context_requires_verified_compositor(self):
        row=[['2256','[Insufficient Permissions]','C+G']]
        with patch.object(launcher,'protected_desktop_context',return_value=False):
            with self.assertRaises(RuntimeError):launcher.check_gpu(row)
        with patch.object(launcher,'protected_desktop_context',return_value=True):launcher.check_gpu(row)
        with patch.object(launcher,'protected_desktop_context') as probe:
            with self.assertRaises(RuntimeError):launcher.check_gpu([['2256','[Insufficient Permissions]','C']])
            probe.assert_not_called()

    def test_gpu_headroom_missing_or_low_is_not_allowed(self):
        for value in ['2047','N/A','']:
            with self.subTest(value=value),patch.object(launcher.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=value)):
                with self.assertRaises(RuntimeError):launcher.check_gpu_headroom()
        with patch.object(launcher.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='2048\n')):launcher.check_gpu_headroom()

    def test_gpu_unknown_or_foreign_is_rejected_without_followup_probe(self):
        for rows in ([['1','[Insufficient Permissions]']],[['2',r'C:\ComfyUI\python.exe']],[['3','python']],[['broken']]):
            with self.subTest(rows=rows),self.assertRaises(RuntimeError):launcher.check_gpu(rows)
        launcher.check_gpu([])
        launcher.check_gpu([['4',r'C:\Ollama\ollama.exe']])

    def test_prepare_copies_once_and_keeps_user_edits(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'source';state=Path(d)/'state'
            (source/'launch-assets').mkdir(parents=True)
            (source/'hello.txt').write_text('original')
            manifest={'hello.txt':hashlib.sha256(b'original').hexdigest()}
            p=source/'launch-assets/package-files.json';p.write_text(json.dumps(manifest))
            workspace=launcher.prepare(source,state)
            (workspace/'hello.txt').write_text('user edit')
            self.assertEqual(launcher.prepare(source,state),workspace)
            self.assertEqual((workspace/'hello.txt').read_text(),'user edit')
            self.assertEqual((source/'hello.txt').read_text(),'original')
            p.write_text('{}')
            with self.assertRaises(RuntimeError):launcher.prepare(source,state)
            self.assertEqual((workspace/'hello.txt').read_text(),'user edit')

    def test_package_traversal_or_corruption_is_rejected(self):
        for name in ('../outside','hello.txt'):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as d:
                source=Path(d)/'source';(source/'launch-assets').mkdir(parents=True)
                (source/'hello.txt').write_text('wrong')
                (source/'launch-assets/package-files.json').write_text(json.dumps({name:hashlib.sha256(b'expected').hexdigest()}))
                with self.assertRaises(RuntimeError):launcher.prepare(source,Path(d)/'state')

    def test_cmd_checkout_line_endings_preserve_verified_content(self):
        for raw in (b'@echo off\nexit /b 0\n',b'@echo off\r\nexit /b 0\r\n'):
            with self.subTest(raw=raw),tempfile.TemporaryDirectory() as d:
                source=Path(d)/'source';(source/'launch-assets').mkdir(parents=True)
                (source/'start.cmd').write_bytes(raw)
                (source/'launch-assets/package-files.json').write_text(json.dumps({'start.cmd':hashlib.sha256(b'@echo off\nexit /b 0\n').hexdigest()}))
                workspace=launcher.prepare(source,Path(d)/'state')
                self.assertEqual((workspace/'start.cmd').read_bytes(),raw)

    def test_cmd_content_changes_and_other_type_line_endings_are_rejected(self):
        for name,raw in [('start.cmd',b'@echo off\r\nexit /b 1\r\n'),('other.txt',b'@echo off\r\nexit /b 0\r\n')]:
            with self.subTest(name=name),tempfile.TemporaryDirectory() as d:
                source=Path(d)/'source';(source/'launch-assets').mkdir(parents=True)
                (source/name).write_bytes(raw)
                (source/'launch-assets/package-files.json').write_text(json.dumps({name:hashlib.sha256(b'@echo off\nexit /b 0\n').hexdigest()}))
                with self.assertRaises(RuntimeError):launcher.prepare(source,Path(d)/'state')

    def test_instance_lock_releases_and_rejects_second_owner(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'lock'
            with launcher.instance_lock(p):
                with self.assertRaises(OSError):
                    with launcher.instance_lock(p):pass
            with launcher.instance_lock(p):pass

    def test_port_conflict_uses_next_port_without_stopping_owner(self):
        from viewer.server import ViewerHandler
        with socket.socket() as held:
            held.bind(('127.0.0.1',0));held.listen();port=held.getsockname()[1]
            server=launcher.bind_server(ViewerHandler,port)
            try:self.assertNotEqual(server.server_address[1],port)
            finally:server.server_close()
            self.assertEqual(held.getsockname()[1],port)

    def test_strict_port_never_moves_to_another_port(self):
        from viewer.server import ViewerHandler
        with socket.socket() as held:
            held.bind(('127.0.0.1',0));held.listen();port=held.getsockname()[1]
            with self.assertRaises(RuntimeError):launcher.bind_server(ViewerHandler,port,strict=True)
            self.assertEqual(held.getsockname()[1],port)

    def test_foreign_gpu_blocks_before_local_api_or_provider(self):
        with tempfile.TemporaryDirectory() as d:
            settings=Path(d)/'settings.json'
            settings.write_text(json.dumps({'output':{'default_backend':'ollama','ollama':{'model':launcher.MODEL,'base_url':launcher.BASE}}}))
            with patch('execution.world_drafts.existing_provider') as provider:
                generate=launcher.guarded_provider(settings)
                with patch.object(launcher,'compute_processes',return_value=[['7','[Insufficient Permissions]']]),patch.object(launcher,'read_local') as read:
                    from execution.provenance import ConfigError
                    with self.assertRaises(ConfigError):generate('x','y')
                    read.assert_not_called();provider.return_value.assert_not_called()
                self.assertFalse(generate.busy())

    def test_packaged_version_does_not_probe_parent_git(self):
        from viewer import version
        with tempfile.TemporaryDirectory() as d:
            version.current.cache_clear()
            try:
                with patch.object(version,'ROOT',Path(d)),patch.object(version,'_git',side_effect=AssertionError('no parent Git probe')):
                    self.assertIsNone(version.current()['sha'])
            finally:version.current.cache_clear()

if __name__=='__main__':unittest.main()
