import json
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

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
        (self.root / 'WorldBloom-Studio.exe').write_bytes(binary)
        self.info = {'source_sha': package.SOURCE_SHA, 'source_tree': package.SOURCE_TREE,
                     'mode': 'WorldBloom-Studio', 'authenticode_status': 'NotSigned'}
        self.write_info()
        inventory = []
        for name in sorted({'CPython', 'Microsoft.Web.WebView2'} | package.RUNTIME_PACKAGES):
            path = self.root / 'THIRD_PARTY_LICENSES' / name / 'LICENSE.txt'
            path.parent.mkdir(parents=True)
            path.write_text('Fixture license')
            inventory.append({'package': name, 'version': 'fixture', 'file': name + '/LICENSE.txt',
                              'sha256': package.sha(path.read_bytes())})
        (self.root / 'THIRD_PARTY_LICENSES/inventory.json').write_text(json.dumps(inventory))
        (self.root / 'THIRD_PARTY_LICENSES/closure-proof.json').write_text(json.dumps({
            'scope': 'observed frozen distribution plus declared runtime/bootloader',
            'proof': {'toc_files': [{'file': name, 'sha256': '0' * 64} for name in
                ('Analysis-00.toc', 'PYZ-00.toc', 'PKG-00.toc', 'EXE-00.toc')]}}))
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
        (self.root / 'WorldBloom-Studio.exe').rename(self.root / 'WorldBloom.exe')
        package.write_manifest(self.root)
        with self.assertRaisesRegex(ValueError, 'executable file list'):
            package.verify(self.root)

    def test_not_a_windows_binary_rejected(self):
        (self.root / 'WorldBloom-Studio.exe').write_bytes(b'not an executable')
        package.write_manifest(self.root)
        with self.assertRaisesRegex(ValueError, 'PE executable'):
            package.verify(self.root)

    def test_non_x64_binary_rejected(self):
        path = self.root / 'WorldBloom-Studio.exe'
        binary = bytearray(path.read_bytes())
        struct.pack_into('<H', binary, 84, 0x14c)
        path.write_bytes(binary)
        package.write_manifest(self.root)
        with self.assertRaisesRegex(ValueError, 'not x64'):
            package.verify(self.root)

    def write_raw_name_fixture(self, archive, paths):
        # Bypass ZipInfo constructor normalization only when building the test
        # fixture. Both physical ZIP headers must contain the intended raw name.
        with zipfile.ZipFile(archive, 'w') as zip_file:
            for name in paths:
                info = zipfile.ZipInfo('fixture-placeholder')
                info.filename = name
                info.orig_filename = name
                zip_file.writestr(info, b'fixture')
        raw = archive.read_bytes()
        with zipfile.ZipFile(archive) as zip_file:
            self.assertEqual(tuple(item.orig_filename for item in zip_file.infolist()), paths)
            central = zip_file.start_dir
            for expected in paths:
                self.assertEqual(raw[central:central + 4], b'PK\x01\x02')
                flags = struct.unpack_from('<H', raw, central + 8)[0]
                name_len, extra_len, comment_len = struct.unpack_from('<HHH', raw, central + 28)
                local = struct.unpack_from('<I', raw, central + 42)[0]
                encoded = expected.encode('utf-8' if flags & 0x800 else 'cp437')
                self.assertEqual(raw[central + 46:central + 46 + name_len], encoded)
                self.assertEqual(raw[local:local + 4], b'PK\x03\x04')
                local_name_len = struct.unpack_from('<H', raw, local + 26)[0]
                self.assertEqual(raw[local + 30:local + 30 + local_name_len], encoded)
                central += 46 + name_len + extra_len + comment_len

    def test_raw_windows_name_is_rejected_even_when_zipfile_normalizes_it(self):
        archive = self.base / 'raw-windows-name.zip'
        original = 'app/back\\slash'
        self.write_raw_name_fixture(archive, (original,))
        # Reproduce Windows ZipInfo read normalization on every test OS.
        with patch.object(zipfile.os, 'sep', '\\'), patch.object(zipfile.os, 'altsep', '/'):
            with zipfile.ZipFile(archive) as zip_file:
                item = zip_file.infolist()[0]
                self.assertEqual(item.orig_filename, original)
                self.assertEqual(item.filename, 'app/back/slash')
            with self.assertRaisesRegex(ValueError, 'unsafe package path'):
                package.extract_verified(archive, self.base / 'windows-read')
        self.assertFalse((self.base / 'windows-read/app').exists())

    def test_unsafe_and_case_colliding_zip_paths_rejected(self):
        samples = (('../escape',), ('C:/escape',), ('app/x', 'APP/X'),
                   ('app//x',), ('app/./x',), ('app/NUL.txt',), ('app/file. ',),
                   ('app/a:b',), ('app/back\\slash',), ('app/COM¹.txt',), ('app/NUL .txt',), ('app/nul\x00hidden',))
        for index, paths in enumerate(samples):
            archive = self.base / ('unsafe' + str(index) + '.zip')
            self.write_raw_name_fixture(archive, paths)
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                package.extract_verified(archive, self.base / ('out' + str(index)))

    def test_application_modules_and_baked_root_in_toc_rejected(self):
        build = self.base / 'build'
        for name in package.MODES:
            folder = build / name
            folder.mkdir(parents=True)
            (folder / 'PYZ-00.toc').write_text(repr(['statistics', 'zoneinfo', 'http.server', 'multiprocessing', 'urllib.request', 'webview']))
        package.check_tocs(build)
        (build / 'WorldBloom-Studio/PYZ-00.toc').write_text(repr(['statistics', 'zoneinfo', 'http.server', 'multiprocessing', 'urllib.request', ('viewer.server', 'server.py', 'PYMODULE')]))
        with self.assertRaisesRegex(ValueError, 'frozen'):
            package.check_tocs(build)
        (build / 'WorldBloom-Studio/PYZ-00.toc').write_text(repr(['statistics', 'zoneinfo', 'http.server', 'multiprocessing', 'urllib.request']))
        (build / 'WorldBloom-Studio/Analysis-00.toc').write_text(repr(['default_root.txt']))
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

    def test_missing_closure_proof_is_rejected(self):
        (self.root / 'THIRD_PARTY_LICENSES/closure-proof.json').write_text('{}')
        package.write_manifest(self.root)
        with self.assertRaisesRegex(ValueError, 'closure proof'):
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
        self.assertEqual(steps[first_upload]['with']['path'], '${{ runner.temp }}/upload-evidence/')
        self.assertNotIn('pip-install-report.json', steps[first_upload]['with']['path'])
        self.assertIn('upload-evidence', steps[scan]['run'])
        self.assertIn('publication_guard.py $file.FullName', steps[scan]['run'])

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

class LicenseClosureTests(unittest.TestCase):
    def setUp(self):
        import collect_release_licenses as licenses
        self.licenses = licenses
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.build = self.base / 'build-output'
        self.folder = self.build / 'build/WorldBloom-Studio'
        self.folder.mkdir(parents=True)
        for name in ('PYZ.pyz', 'WorldBloom-Studio.pkg'):
            (self.folder / name).write_bytes(b'fixture archive with separately described TOC')
        self.toc('PYZ-00.toc', (str(self.folder / 'PYZ.pyz'), ['webview', 'statistics']))
        self.toc('PKG-00.toc', (str(self.folder / 'WorldBloom-Studio.pkg'), []))
        self.toc('Analysis-00.toc', ['webview'])
        self.toc('EXE-00.toc', ['WorldBloom-Studio.exe'])

    def toc(self, name, contents):
        (self.folder / name).write_text(repr(contents), encoding='utf-8')

    def test_complete_tocs_required_and_parse_fail_closed(self):
        proof, values = self.licenses.toc_evidence(self.build)
        self.assertTrue(proof['wheel_absent'])
        self.assertEqual(len(proof['toc_files']), 4)
        (self.folder / 'Analysis-00.toc').write_text('not a literal')
        with self.assertRaisesRegex(ValueError, 'unparseable'):
            self.licenses.toc_evidence(self.build)
        (self.folder / 'Analysis-00.toc').unlink()
        with self.assertRaisesRegex(ValueError, 'complete Studio'):
            self.licenses.toc_evidence(self.build)

    def test_wheel_module_data_metadata_and_case_aliases_prevent_omission(self):
        for value in ('wheel', 'Wheel.bdist_wheel', 'setuptools._vendor.WHEEL',
                      r'C:\venv\WHEEL\data', 'WHEEL-0.46.3.dist-info/METADATA'):
            with self.subTest(value=value):
                self.toc('Analysis-00.toc', [value])
                proof, _ = self.licenses.toc_evidence(self.build)
                self.assertFalse(proof['wheel_absent'])
                with patch.object(self.licenses, 'digest', return_value=self.licenses.WHEEL_NOTICE_SHA):
                    with self.assertRaisesRegex(ValueError, 'may be distributed'):
                        self.licenses.omit_known_unbundled_wheel('setuptools', '84.2.1',
                            self.licenses.WHEEL_NOTICE, b'fixture', proof)

    def test_exact_wheel_version_bytes_and_absence_proof_are_all_required(self):
        omit = self.licenses.omit_known_unbundled_wheel
        args = ('setuptools', '84.2.1', self.licenses.WHEEL_NOTICE, b'fixture')
        with self.assertRaisesRegex(ValueError, 'exact version/bytes'):
            omit(*args, {'wheel_absent': True})
        with patch.object(self.licenses, 'digest', return_value=self.licenses.WHEEL_NOTICE_SHA):
            with self.assertRaisesRegex(ValueError, 'publication review'):
                omit(*args, None)
            self.assertTrue(omit(*args, {'wheel_absent': True}))
            with self.assertRaisesRegex(ValueError, 'exact version/bytes'):
                omit('setuptools', '0.0.0', args[2], args[3], {'wheel_absent': True})

    def test_unclassified_archives_and_nested_jars_fail_closed(self):
        for suffix, member in (('.zip', 'unknown.py'), ('.jar', 'nested.zip')):
            path = self.folder / ('unknown' + suffix)
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr(member, b'fixture')
            self.toc('Analysis-00.toc', [str(path)])
            with self.assertRaisesRegex(ValueError, 'unclassified'):
                self.licenses.toc_evidence(self.build)

    def test_real_data_tuple_archive_destination_is_bound_to_source(self):
        jar = self.base / 'installed/webview/lib/pywebview-android.jar'
        jar.parent.mkdir(parents=True)
        with zipfile.ZipFile(jar, 'w') as archive:
            archive.writestr('org/example/Main.class', b'fixture')
        self.toc('Analysis-00.toc', [('webview/lib/pywebview-android.jar', str(jar), 'DATA')])
        proof, _ = self.licenses.toc_evidence(self.build)
        self.assertIn('pywebview-android.jar', [row['name'] for row in proof['archives']])
        jar.unlink()
        with self.assertRaisesRegex(ValueError, 'missing source'):
            self.licenses.toc_evidence(self.build)

    def test_archive_destination_cannot_hide_an_unclassified_source(self):
        source = self.base / 'existing.dat'
        source.write_bytes(b'opaque archive')
        self.toc('Analysis-00.toc', [('webview/lib/hidden.jar', str(source), 'DATA')])
        with self.assertRaisesRegex(ValueError, 'unclassified source'):
            self.licenses.toc_evidence(self.build)

    def test_base_library_is_enumerated_and_nonstdlib_rejected(self):
        path = self.folder / 'base_library.zip'
        self.toc('Analysis-00.toc', [str(path)])
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('collections/__init__.pyc', b'fixture')
        proof, _ = self.licenses.toc_evidence(self.build)
        self.assertTrue(any(x['name'] == 'base_library.zip' for x in proof['archives']))
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('wheel/__init__.pyc', b'fixture')
        with self.assertRaisesRegex(ValueError, 'unclassified base_library'):
            self.licenses.toc_evidence(self.build)

    def fake_dist(self, name, version='1.0'):
        from types import SimpleNamespace
        root = self.base / 'installed' / name
        root.mkdir(parents=True, exist_ok=True)
        files = [name + '.py', name + '.dist-info/LICENSE.txt']
        for file in files:
            path = root / file
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('unchanged original fixture notice' if file.endswith('.txt') else '# fixture')
        return SimpleNamespace(metadata={'Name': name}, version=version, files=files,
                               locate_file=lambda file: root / str(file))

    def test_observed_build_dependency_is_retained_with_original_notice(self):
        installed = [self.fake_dist(n) for n in sorted(self.licenses.RUNTIME_PACKAGES)]
        extra = self.fake_dist('build-only-example')
        installed.append(extra)
        self.toc('Analysis-00.toc', [str(extra.locate_file(extra.files[0]))])
        python = self.base / 'python'
        python.mkdir()
        (python / 'LICENSE.txt').write_text('CPython fixture notice')
        with patch.object(self.licenses.importlib.metadata, 'distributions', return_value=installed), patch.object(self.licenses.sys, 'base_prefix', str(python)):
            result = self.licenses.collect(self.base / 'notices', self.build)
        record = next(row for row in result if row['package'] == 'build-only-example')
        self.assertEqual((self.base / 'notices' / record['file']).read_bytes(),
                         extra.locate_file(extra.files[1]).read_bytes())

    def test_unobserved_build_distribution_is_excluded_with_toc_proof(self):
        installed = [self.fake_dist(n) for n in sorted(self.licenses.RUNTIME_PACKAGES)]
        installed.append(self.fake_dist('build-only-example'))
        python = self.base / 'python'
        python.mkdir()
        (python / 'LICENSE.txt').write_text('CPython fixture notice')
        with patch.object(self.licenses.importlib.metadata, 'distributions', return_value=installed), patch.object(self.licenses.sys, 'base_prefix', str(python)):
            result = self.licenses.collect(self.base / 'notices', self.build)
        self.assertNotIn('build-only-example', [r['package'] for r in result])
        proof = json.loads((self.base / 'notices/closure-proof.json').read_text())
        self.assertEqual(len(proof['proof']['toc_files']), 4)
        self.assertIn('build-only-example', [r['package'] for r in proof['excluded_distributions']])

    def test_proxy_fallback_checks_exact_code_version_and_original_notice(self):
        from types import SimpleNamespace
        original = (ROOT / 'release/licenses/proxy_tools-LICENSE.txt').read_bytes()
        self.assertEqual(self.licenses.git_blob(original), self.licenses.PROXY_LICENSE_BLOB)
        with self.assertRaisesRegex(ValueError, 'reviewed version'):
            self.licenses.proxy_notice(SimpleNamespace(version='0.2.0'))
        code = self.base / 'code.py'
        code.write_bytes(b'# different code')
        with self.assertRaisesRegex(ValueError, 'source differs'):
            self.licenses.proxy_notice(SimpleNamespace(version='0.1.0', locate_file=lambda p: code))

    def test_resolution_uses_only_validated_needed_fields(self):
        import stage_release_evidence as evidence
        item = {'metadata': {'name': 'example', 'version': '1.0', 'author': 'unneeded metadata'},
                'download_info': {'url': 'https://files.pythonhosted.org/packages/ab/example.whl',
                                  'archive_info': {'hashes': {'sha256': 'a' * 64}}}}
        result = evidence.resolution({'install': [item]})
        self.assertEqual(set(result[0]), {'package', 'version', 'download_url', 'sha256'})
        item['download_info']['url'] += '?unexpected=metadata'
        with self.assertRaisesRegex(ValueError, 'unexpected package'):
            evidence.resolution({'install': [item]})

    def test_studio_only_controller_preserves_internal_viewer_and_samples(self):
        self.assertEqual(package.MODES, ('WorldBloom-Studio',))
        self.assertIn('viewer', package.PACKAGES)
        self.assertIn('samples', package.PACKAGES)
        build = (ROOT / 'scripts/build_windows_release.py').read_text()
        smoke = (ROOT / 'scripts/windows_release_smoke.ps1').read_text()
        self.assertIn("for mode in ('WorldBloom-Studio',)", build)
        self.assertIn("@('WorldBloom-Studio')", smoke)
        self.assertNotIn("$mode -eq 'WorldBloom'", smoke)


if __name__ == '__main__':
    unittest.main()
