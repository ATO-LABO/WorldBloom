import json
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import package_release as package

class ReleasePackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'bundle'
        self.root.mkdir()
        for name in package.PACKAGES:
            directory = self.root / 'app' / name
            directory.mkdir(parents=True)
            (directory / 'placeholder.txt').write_text('fixture')
        for name in package.REQUIRED_FILES:
            (self.root / name).write_text('{}' if name.endswith('.json') else 'fixture')
        (self.root / 'app/VERSION.txt').write_text(package.SOURCE_SHA + ' 2026-11-07\n')
        (self.root / 'app/settings.json').write_text('{}\n')
        binary = bytearray(128)
        binary[:2] = b'MZ'
        struct.pack_into('<I', binary, 0x3c, 80)
        binary[80:84] = b'PE\0\0'
        struct.pack_into('<H', binary, 84, 0x8664)
        (self.root / 'WorldBloom.exe').write_bytes(binary)
        self.info = {'source_sha': package.SOURCE_SHA, 'source_tree': package.SOURCE_TREE,
                     'mode': 'WorldBloom', 'authenticode_status': 'NotSigned'}
        self.write_info()
        inventory = []
        for name in ('CPython', 'Microsoft.Web.WebView2', 'pywebview', 'pyyaml', 'pythonnet', 'pyinstaller'):
            path = self.root / 'THIRD_PARTY_LICENSES' / name / 'LICENSE.txt'
            path.parent.mkdir(parents=True)
            path.write_text('Fixture license')
            inventory.append({'package': name, 'version': 'fixture', 'file': name + '/LICENSE.txt',
                              'sha256': package.sha(path.read_bytes())})
        (self.root / 'THIRD_PARTY_LICENSES/inventory.json').write_text(json.dumps(inventory))
        package.write_manifest(self.root)

    def write_info(self):
        (self.root / 'build-info.json').write_text(json.dumps(self.info))

    def test_complete_payload_and_zip_round_trip(self):
        expected = package.verify(self.root)
        archive = self.base / 'candidate.zip'
        package.zip_bundle(self.root, archive)
        self.assertEqual(package.extract_verified(archive, self.base / 'extract'), expected)

    def test_missing_samples_rejected_even_with_rewritten_manifest(self):
        shutil.rmtree(self.root / 'app/samples')
        package.write_manifest(self.root)
        with self.assertRaisesRegex(ValueError, 'required payload'):
            package.verify(self.root)

    def test_missing_version_rejected_even_with_rewritten_manifest(self):
        (self.root / 'app/VERSION.txt').unlink()
        package.write_manifest(self.root)
        with self.assertRaises(FileNotFoundError):
            package.verify(self.root)

    def test_missing_requirements_rejected_even_with_rewritten_manifest(self):
        (self.root / 'app/requirements.txt').unlink()
        package.write_manifest(self.root)
        with self.assertRaisesRegex(ValueError, 'required file'):
            package.verify(self.root)

    def test_hash_change_and_extra_file_are_rejected(self):
        (self.root / 'app/viewer/placeholder.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            package.verify(self.root)
        package.write_manifest(self.root)
        (self.root / 'extra.txt').write_text('new')
        with self.assertRaisesRegex(ValueError, 'file list'):
            package.verify(self.root)

    def test_wrong_source_is_rejected(self):
        self.info['source_sha'] = '0' * 40
        self.write_info()
        package.write_manifest(self.root)
        with self.assertRaisesRegex(ValueError, 'provenance'):
            package.verify(self.root)

    def test_private_settings_rejected(self):
        (self.root / 'app/settings.json').write_text('{"output":{"model":"private"}}')
        package.write_manifest(self.root)
        with self.assertRaisesRegex(ValueError, 'public default'):
            package.verify(self.root)

    def test_wrong_executable_mode_rejected(self):
        (self.root / 'WorldBloom.exe').rename(self.root / 'WorldBloom-Studio.exe')
        package.write_manifest(self.root)
        with self.assertRaisesRegex(ValueError, 'executable file list'):
            package.verify(self.root)

    def test_not_a_windows_binary_rejected(self):
        (self.root / 'WorldBloom.exe').write_bytes(b'not an executable')
        package.write_manifest(self.root)
        with self.assertRaisesRegex(ValueError, 'PE executable'):
            package.verify(self.root)

    def test_non_x64_binary_rejected(self):
        path = self.root / 'WorldBloom.exe'
        binary = bytearray(path.read_bytes())
        struct.pack_into('<H', binary, 84, 0x14c)
        path.write_bytes(binary)
        package.write_manifest(self.root)
        with self.assertRaisesRegex(ValueError, 'not x64'):
            package.verify(self.root)

    def test_unsafe_and_case_colliding_zip_paths_rejected(self):
        samples = (('../escape',), ('C:/escape',), ('app/x', 'APP/X'),
                   ('app//x',), ('app/./x',), ('app/NUL.txt',), ('app/file. ',),
                   ('app/a:b',), ('app/back\\slash',), ('app/COM¹.txt',), ('app/NUL .txt',))
        for index, paths in enumerate(samples):
            archive = self.base / ('unsafe' + str(index) + '.zip')
            with zipfile.ZipFile(archive, 'w') as zip_file:
                for name in paths:
                    zip_file.writestr(name, b'fixture')
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                package.extract_verified(archive, self.base / ('out' + str(index)))

    def test_application_modules_and_baked_root_in_toc_rejected(self):
        build = self.base / 'build'
        for name in package.MODES:
            folder = build / name
            folder.mkdir(parents=True)
            (folder / 'PYZ-00.toc').write_text(repr(['statistics', 'zoneinfo', 'http.server', 'multiprocessing', 'urllib.request', 'webview']))
        package.check_tocs(build)
        (build / 'WorldBloom/PYZ-00.toc').write_text(repr(['statistics', 'zoneinfo', 'http.server', 'multiprocessing', 'urllib.request', ('viewer.server', 'server.py', 'PYMODULE')]))
        with self.assertRaisesRegex(ValueError, 'frozen'):
            package.check_tocs(build)
        (build / 'WorldBloom/PYZ-00.toc').write_text(repr(['statistics', 'zoneinfo', 'http.server', 'multiprocessing', 'urllib.request']))
        (build / 'WorldBloom/Analysis-00.toc').write_text(repr(['default_root.txt']))
        with self.assertRaisesRegex(ValueError, 'developer root'):
            package.check_tocs(build)

    def test_missing_frozen_stdlib_is_rejected(self):
        build = self.base / 'build'
        for name in package.MODES:
            folder = build / name
            folder.mkdir(parents=True)
            (folder / 'PYZ-00.toc').write_text(repr(['webview']))
        with self.assertRaisesRegex(ValueError, 'required frozen stdlib missing'):
            package.check_tocs(build)

    def test_license_inventory_is_required_and_hash_checked(self):
        path = self.root / 'THIRD_PARTY_LICENSES/pywebview/LICENSE.txt'
        path.write_text('modified notice')
        package.write_manifest(self.root)
        with self.assertRaisesRegex(ValueError, 'license checksum'):
            package.verify(self.root)
        (self.root / 'THIRD_PARTY_LICENSES/inventory.json').write_text('[]')
        package.write_manifest(self.root)
        with self.assertRaisesRegex(ValueError, 'inventory incomplete'):
            package.verify(self.root)

    def test_vendored_notice_matches_reviewed_upstream_blob(self):
        import hashlib
        data = (ROOT / 'release/licenses/Microsoft.Web.WebView2.LICENSE.md').read_bytes()
        blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        self.assertEqual(blob, '5dd43f57f273797aea6904ae8000c870e424b35a')

    def test_workflow_scans_before_first_upload_and_cannot_pass_zero_tests(self):
        import yaml
        workflow = yaml.safe_load((ROOT / '.github/workflows/release-build.yml').read_text())
        steps = workflow['jobs']['windows-candidate']['steps']
        first_upload = next(i for i, step in enumerate(steps)
                            if step.get('uses', '').startswith('actions/upload-artifact@'))
        scan = next(i for i, step in enumerate(steps) if 'gitleaks.exe' in step.get('run', ''))
        self.assertLess(scan, first_upload)
        test = next(step for step in steps if step.get('name') == 'Test candidate packaging contracts')
        self.assertIn('controller/tests/test_release_package.py -v', test['run'])
        self.assertIn('build-commands.json', steps[first_upload]['with']['path'])

    def test_workflow_never_publishes_or_expands_token_permissions(self):
        import yaml
        workflow = yaml.safe_load((ROOT / '.github/workflows/release-build.yml').read_text())
        self.assertEqual(workflow['permissions'], {'contents': 'read'})
        events = workflow.get('on', workflow.get(True))
        self.assertEqual(events, {'push': {'branches': ['codex/windows-release-20261107']}})
        for job in workflow['jobs'].values():
            self.assertLessEqual(job['timeout-minutes'], 30)
            self.assertNotIn('permissions', job)
            for step in job['steps']:
                if 'uses' in step:
                    self.assertRegex(step['uses'], r'^actions/[a-z-]+@[0-9a-f]{40}$')
                if step.get('uses', '').startswith('actions/checkout@'):
                    self.assertFalse(step['with']['persist-credentials'])
                for forbidden in ('gh release', 'git push', 'github-script', 'secrets.'):
                    self.assertNotIn(forbidden, json.dumps(step))

if __name__ == '__main__':
    unittest.main()
