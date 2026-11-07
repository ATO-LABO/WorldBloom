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

def wheel_notice_fixture():
    import os
    import importlib.metadata
    override = os.environ.get('WORLDBLOOM_TEST_WHEEL_NOTICE')
    if override:
        data = Path(override).read_bytes()
    else:
        dist = importlib.metadata.distribution('setuptools')
        if dist.version != '84.2.1':
            raise ValueError('Test requires the exact official setuptools 84.2.1 wheel notice')
        data = Path(dist.locate_file('setuptools/_vendor/wheel-0.46.3.dist-info/licenses/LICENSE.txt')).read_bytes()
    if package.sha(data) != '30c23618679108f3e8ea1d2a658c7ca417bdfc891c98ef1a89fa4ff0c9828654':
        raise ValueError('Test wheel notice differs from official original bytes')
    return data


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
        original_wheel = wheel_notice_fixture()
        path = self.root / 'THIRD_PARTY_LICENSES' / package.WHEEL_NOTICE_OUTPUT
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(original_wheel)
        inventory.append({'package': 'setuptools', 'version': '84.2.1',
                          'file': package.WHEEL_NOTICE_OUTPUT, 'sha256': package.WHEEL_NOTICE_SHA})
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

    def test_original_wheel_notice_cannot_be_removed_from_package_or_inventory(self):
        path = self.root / 'THIRD_PARTY_LICENSES' / package.WHEEL_NOTICE_OUTPUT
        path.unlink()
        package.write_manifest(self.root)
        with self.assertRaisesRegex(ValueError, 'license checksum'):
            package.verify(self.root)
        path.write_bytes(wheel_notice_fixture())
        inventory_path = self.root / 'THIRD_PARTY_LICENSES/inventory.json'
        rows = [row for row in json.loads(inventory_path.read_text()) if row['file'] != package.WHEEL_NOTICE_OUTPUT]
        inventory_path.write_text(json.dumps(rows))
        package.write_manifest(self.root)
        with self.assertRaisesRegex(ValueError, 'vendored wheel notice inventory missing'):
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
        self.assertIn('scan_release_publication.py --guard release-source/scripts/publication_guard.py $file.FullName', steps[scan]['run'])

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

    def test_wheel_module_data_metadata_and_case_aliases_are_recorded(self):
        for value in ('wheel', 'Wheel.bdist_wheel', 'setuptools._vendor.WHEEL',
                      r'C:\venv\WHEEL\data', 'WHEEL-0.46.3.dist-info/METADATA'):
            with self.subTest(value=value):
                self.toc('Analysis-00.toc', [value])
                proof, _ = self.licenses.toc_evidence(self.build)
                self.assertFalse(proof['wheel_absent'])
                self.assertEqual(self.licenses.retained_wheel_notice('setuptools', '84.2.1',
                    self.licenses.WHEEL_NOTICE, wheel_notice_fixture()), self.licenses.WHEEL_NOTICE_OUTPUT)

    def test_exact_wheel_version_bytes_and_source_path_required(self):
        retained = self.licenses.retained_wheel_notice
        args = ('setuptools', '84.2.1', self.licenses.WHEEL_NOTICE, wheel_notice_fixture())
        self.assertEqual(retained(*args), self.licenses.WHEEL_NOTICE_OUTPUT)
        with self.assertRaisesRegex(ValueError, 'exact version/bytes'):
            retained(args[0], args[1], args[2], args[3] + b' ')
        with self.assertRaisesRegex(ValueError, 'exact version/bytes'):
            retained(args[0], '0.0.0', args[2], args[3])
        self.assertIsNone(retained(args[0], args[1], 'unknown/LICENSE.txt', args[3]))

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
        if name == 'setuptools':
            version = '84.2.1'
            file = 'setuptools/_vendor/wheel-0.46.3.dist-info/licenses/LICENSE.txt'
            target = root / file
            target.parent.mkdir(parents=True)
            target.write_bytes(wheel_notice_fixture())
            files.append(file)
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

    def test_wheel_notice_is_retained_with_and_without_observed_wheel(self):
        installed = [self.fake_dist(n) for n in sorted(self.licenses.RUNTIME_PACKAGES)]
        python = self.base / 'python'
        python.mkdir()
        (python / 'LICENSE.txt').write_text('CPython fixture notice')
        for index, observed in enumerate((False, True)):
            self.toc('Analysis-00.toc', ['setuptools._vendor.wheel'] if observed else ['webview'])
            output = self.base / ('retained-' + str(index))
            with patch.object(self.licenses.importlib.metadata, 'distributions', return_value=installed), patch.object(self.licenses.sys, 'base_prefix', str(python)):
                rows = self.licenses.collect(output, self.build)
            self.assertEqual((output / self.licenses.WHEEL_NOTICE_OUTPUT).read_bytes(), wheel_notice_fixture())
            row = next(row for row in rows if row['file'] == self.licenses.WHEEL_NOTICE_OUTPUT)
            self.assertEqual(row['sha256'], self.licenses.WHEEL_NOTICE_SHA)
            self.assertEqual(row['installed_path'], 'setuptools/_vendor/wheel-0.46.3.dist-info/licenses/LICENSE.txt')
            proof = json.loads((output / 'closure-proof.json').read_text())
            self.assertEqual(proof['omitted_notices'], [])
            self.assertEqual(proof['retained_notices'][0]['file'], self.licenses.WHEEL_NOTICE_OUTPUT)

    def test_missing_required_original_wheel_notice_rejected(self):
        installed = [self.fake_dist(n) for n in sorted(self.licenses.RUNTIME_PACKAGES)]
        dist = next(dist for dist in installed if dist.metadata['Name'] == 'setuptools')
        dist.files = [file for file in dist.files if 'wheel-0.46.3' not in file]
        python = self.base / 'python'
        python.mkdir()
        (python / 'LICENSE.txt').write_text('CPython fixture notice')
        with patch.object(self.licenses.importlib.metadata, 'distributions', return_value=installed), patch.object(self.licenses.sys, 'base_prefix', str(python)):
            with self.assertRaisesRegex(ValueError, 'wheel notice missing'):
                self.licenses.collect(self.base / 'missing')

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


class ReviewedNoticeTests(unittest.TestCase):
    def setUp(self):
        import os
        import scan_release_publication as scanner
        self.scanner = scanner
        # On Windows the installed runtime supplies the exact original fixture.
        # Linux reviewers supply the same official archive bytes, never a rewrite.
        path = Path(os.environ.get('WORLDBLOOM_TEST_CPYTHON_NOTICE', str(Path(sys.base_prefix) / 'LICENSE.txt')))
        self.notice = path.read_bytes()
        self.assertEqual(package.sha(self.notice), scanner.NOTICE_SHA256)
        self.guard_path = Path(os.environ.get('WORLDBLOOM_TEST_GUARD', str(ROOT / 'scripts/publication_guard.py')))
        self.guard = scanner.load_guard(self.guard_path)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def test_exact_official_notice_all_known_placements_and_original_bytes(self):
        original = self.guard.check_content
        with self.scanner.reviewed_notice_guard(self.guard):
            for name in self.scanner.NOTICE_PATHS:
                with self.subTest(name=name):
                    with self.assertRaisesRegex(self.guard.PublicationError, 'email-needs-publication-review'):
                        original(name, self.notice)
                    before = bytes(self.notice)
                    self.guard.check_content(name, self.notice)
                    self.assertEqual(self.notice, before)
                    self.assertEqual(package.sha(self.notice), self.scanner.NOTICE_SHA256)
        self.assertIs(self.guard.check_content, original)

    def test_modified_notice_and_unknown_paths_still_rejected(self):
        with self.scanner.reviewed_notice_guard(self.guard):
            for name in ('LICENSE.txt', 'other/CPython/LICENSE.txt', 'CPython/license.txt',
                         'WorldBloom/THIRD_PARTY_LICENSES/CPython/LICENSE.txt'):
                with self.subTest(name=name), self.assertRaisesRegex(self.guard.PublicationError, 'email-needs-publication-review'):
                    self.guard.check_content(name, self.notice)
            with self.assertRaisesRegex(self.guard.PublicationError, 'email-needs-publication-review'):
                self.guard.check_content('CPython/LICENSE.txt', self.notice + b' ')

    def test_other_email_secret_fields_and_tokens_still_rejected(self):
        # Synthetic, non-sensitive fixtures are assembled to exercise detection;
        # no actual credentials or private contact data are embedded in tests.
        email = ('fixture' + chr(64) + 'fixture.invalid').encode()
        token = ('gh' + 'p_' + 'A' * 36).encode()
        field = json.dumps({'pass' + 'word': 'synthetic-regression-value'}).encode()
        with self.scanner.reviewed_notice_guard(self.guard):
            for name, data, rule in [('CPython/LICENSE.txt', email, 'email-needs-publication-review'),
                                     ('CPython/LICENSE.txt', self.notice + token, 'github-token'),
                                     ('other.txt', token, 'github-token'),
                                     ('other.json', field, 'credential'),
                                     ('other.json', json.dumps({'pass' + 'word': 123}).encode(), 'nonempty-credential-field')]:
                with self.subTest(name=name, rule=rule), self.assertRaisesRegex(self.guard.PublicationError, rule):
                    self.guard.check_content(name, data)

    def test_unknown_original_verdict_is_never_accepted(self):
        name = 'CPython/LICENSE.txt'
        error = self.guard.PublicationError('Publication blocked: unknown-future-rule: ' + name)
        with patch.object(self.guard, 'check_content', side_effect=error):
            with self.scanner.reviewed_notice_guard(self.guard):
                with self.assertRaisesRegex(self.guard.PublicationError, 'unknown-future-rule'):
                    self.guard.check_content(name, self.notice)
        self.assertTrue(all(path.endswith('.txt') for path in self.scanner.NOTICE_PATHS))

    def test_directory_and_zip_scans_continue_after_reviewed_notice(self):
        root = self.base / 'files'
        notice_path = root / 'CPython/LICENSE.txt'
        notice_path.parent.mkdir(parents=True)
        notice_path.write_bytes(self.notice)
        extra = root / 'ZZ-other.txt'
        with self.scanner.reviewed_notice_guard(self.guard):
            self.assertEqual(self.guard.check_publication(root), 1)
            extra.write_bytes(('fixture' + chr(64) + 'fixture.invalid').encode())
            with self.assertRaisesRegex(self.guard.PublicationError, 'email-needs-publication-review'):
                self.guard.check_publication(root)
            archive = self.base / 'package.zip'
            with zipfile.ZipFile(archive, 'w') as zip_file:
                zip_file.writestr('THIRD_PARTY_LICENSES/CPython/LICENSE.txt', self.notice)
            self.assertEqual(self.guard.check_publication(archive), 1)
            with zipfile.ZipFile(archive, 'a') as zip_file:
                zip_file.writestr('ZZ-other.txt', extra.read_bytes())
            with self.assertRaisesRegex(self.guard.PublicationError, 'email-needs-publication-review'):
                self.guard.check_publication(archive)
        self.assertEqual(notice_path.read_bytes(), self.notice)

    def test_original_guard_identity_and_path_guards_remain_enforced(self):
        changed = self.base / 'guard.py'
        changed.write_bytes(self.guard_path.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'differs from'):
            self.scanner.load_guard(changed)
        with self.scanner.reviewed_notice_guard(self.guard):
            for name in ('../CPython/LICENSE.txt', 'control/LICENSE.txt'):
                with self.subTest(name=name), self.assertRaises(self.guard.PublicationError):
                    self.guard.check_name(name)

    def test_original_wheel_notice_at_known_paths_and_pairing_cannot_be_swapped(self):
        data = wheel_notice_fixture()
        with self.scanner.reviewed_notice_guard(self.guard):
            for path in self.scanner.WHEEL_NOTICE_PATHS:
                self.guard.check_content(path, data)
                with self.assertRaisesRegex(self.guard.PublicationError, 'email-needs-publication-review'):
                    self.guard.check_content(path, self.notice)
                with self.assertRaisesRegex(self.guard.PublicationError, 'email-needs-publication-review'):
                    self.guard.check_content(path, data + b' ')
            for path in self.scanner.NOTICE_PATHS:
                with self.assertRaisesRegex(self.guard.PublicationError, 'email-needs-publication-review'):
                    self.guard.check_content(path, data)
            with self.assertRaisesRegex(self.guard.PublicationError, 'email-needs-publication-review'):
                self.guard.check_content('setuptools/84.2.1/13-LICENSE.txt', data)
            token = ('gh' + 'p_' + 'A' * 36).encode()
            with self.assertRaisesRegex(self.guard.PublicationError, 'github-token'):
                self.guard.check_content('setuptools/84.2.1/wheel-0.46.3-LICENSE.txt', data + token)

    def test_both_original_notices_in_zip_do_not_exempt_other_files(self):
        archive = self.base / 'both-notices.zip'
        with zipfile.ZipFile(archive, 'w') as zip_file:
            zip_file.writestr('THIRD_PARTY_LICENSES/CPython/LICENSE.txt', self.notice)
            zip_file.writestr('THIRD_PARTY_LICENSES/setuptools/84.2.1/wheel-0.46.3-LICENSE.txt', wheel_notice_fixture())
        with self.scanner.reviewed_notice_guard(self.guard):
            self.assertEqual(self.guard.check_publication(archive), 2)
            with zipfile.ZipFile(archive, 'a') as zip_file:
                zip_file.writestr('ZZ-other.txt', ('fixture' + chr(64) + 'fixture.invalid').encode())
            with self.assertRaisesRegex(self.guard.PublicationError, 'email-needs-publication-review'):
                self.guard.check_publication(archive)

    def test_unknown_wheel_verdict_is_not_accepted(self):
        path = 'setuptools/84.2.1/wheel-0.46.3-LICENSE.txt'
        error = self.guard.PublicationError('Publication blocked: unknown-future-rule: ' + path)
        with patch.object(self.guard, 'check_content', side_effect=error):
            with self.scanner.reviewed_notice_guard(self.guard):
                with self.assertRaisesRegex(self.guard.PublicationError, 'unknown-future-rule'):
                    self.guard.check_content(path, wheel_notice_fixture())

    def test_all_workflow_guard_calls_use_reviewed_entrypoint(self):
        import yaml
        workflow = yaml.safe_load((ROOT / '.github/workflows/release-build.yml').read_text())
        checked = 0
        for job in workflow['jobs'].values():
            for step in job['steps']:
                for line in step.get('run', '').splitlines():
                    if 'python -B ' in line and 'publication_guard.py' in line:
                        self.assertIn('scan_release_publication.py --guard ', line)
                        checked += 1
        self.assertEqual(checked, 6)


class StructuredArchiveTests(unittest.TestCase):
    setUp = ReviewedNoticeTests.setUp

    def archive(self, name='candidate.zip', entries=None, compression=zipfile.ZIP_DEFLATED):
        path = self.base / name
        with zipfile.ZipFile(path, 'w', compression=compression) as archive:
            for filename, data in (entries or [('readme.txt', b'public fixture')]):
                archive.writestr(filename, data)
        return path

    def check(self, path):
        with self.scanner.reviewed_notice_guard(self.guard), self.scanner.structured_archive_guard(self.guard):
            return self.guard.check_publication(path)

    def test_directory_zip_uses_canonical_entry_api_and_preserves_original_bytes(self):
        # This reproduces the API mismatch, not the unavailable real run-4 bytes.
        archive = self.archive(entries=[('THIRD_PARTY_LICENSES/CPython/LICENSE.txt', self.notice),
            ('THIRD_PARTY_LICENSES/setuptools/84.2.1/wheel-0.46.3-LICENSE.txt', wheel_notice_fixture())],
            compression=zipfile.ZIP_STORED)
        before = archive.read_bytes()
        with self.scanner.reviewed_notice_guard(self.guard):
            with self.assertRaisesRegex(self.guard.PublicationError, 'email-needs-publication-review: candidate.zip'):
                self.guard.check_publication(self.base)
        self.assertEqual(self.check(archive), 2)
        self.assertEqual(self.check(self.base), 1)
        self.assertEqual(archive.read_bytes(), before)

    def test_deflated_utf8_entries_are_checked(self):
        archive = self.archive(entries=[('app/日本語.txt', b'public fixture')])
        self.assertEqual(self.check(archive), 1)

    def test_zip_written_by_actual_packager_is_accepted(self):
        payload = self.base / 'payload'
        payload.mkdir()
        (payload / 'readme.txt').write_text('public fixture')
        archive = self.base / 'packager-output.zip'
        package.zip_bundle(payload, archive)
        self.assertEqual(self.check(archive), 1)

    def test_other_directory_file_is_not_skipped_after_zip(self):
        self.archive('A-candidate.zip')
        (self.base / 'Z-other.txt').write_text('fixture' + chr(64) + 'fixture.invalid')
        with self.assertRaisesRegex(self.guard.PublicationError, 'email-needs-publication-review'):
            self.check(self.base)

    def test_unknown_email_and_secret_in_zip_entries_are_rejected(self):
        for index, data in enumerate((('fixture' + chr(64) + 'fixture.invalid').encode(),
                                     ('gh' + 'p_' + 'A' * 36).encode())):
            archive = self.archive(str(index) + '.zip', [('other.txt', data)])
            with self.assertRaises(self.guard.PublicationError):
                self.check(archive)

    def test_zip_filename_metadata_is_not_a_notice_exception(self):
        filename = 'fixture' + chr(64) + 'fixture.invalid'
        archive = self.archive(entries=[(filename, b'public fixture')])
        with self.assertRaisesRegex(self.guard.PublicationError, 'email-needs-publication-review'):
            self.check(archive)

    def test_comments_and_extra_fields_rejected_without_interpreting_them(self):
        for kind in ('archive-comment', 'entry-comment', 'extra'):
            archive = self.base / (kind + '.zip')
            info = zipfile.ZipInfo('public.txt')
            if kind == 'entry-comment': info.comment = b'unreviewed metadata'
            if kind == 'extra': info.extra = b'\xfe\xca\x04\x00data'
            with zipfile.ZipFile(archive, 'w') as zip_file:
                zip_file.writestr(info, b'public fixture')
                if kind == 'archive-comment': zip_file.comment = b'unreviewed metadata'
            with self.subTest(kind=kind), self.assertRaises(self.guard.PublicationError):
                self.check(archive)

    def test_encryption_and_unknown_compression_rejected(self):
        for field in ('encrypted', 'method'):
            archive = self.archive(field + '.zip')
            data = bytearray(archive.read_bytes())
            central = data.index(b'PK\x01\x02')
            if field == 'encrypted':
                struct.pack_into('<H', data, 6, 1)
                struct.pack_into('<H', data, central + 8, 1)
            else:
                struct.pack_into('<H', data, 8, 99)
                struct.pack_into('<H', data, central + 10, 99)
            archive.write_bytes(data)
            with self.subTest(field=field), self.assertRaises(self.guard.PublicationError):
                self.check(archive)

    def test_unsafe_raw_names_aliases_duplicates_and_links_rejected(self):
        cases = [('../escape',), ('app/back\\slash',), ('app/nul\x00hidden',),
                 ('app/NUL.txt',), ('app//x',), ('app/x', 'APP/X')]
        for index, names in enumerate(cases):
            archive = self.base / ('unsafe-' + str(index) + '.zip')
            with zipfile.ZipFile(archive, 'w') as zip_file:
                for name in names:
                    info = zipfile.ZipInfo('placeholder');info.filename = name;info.orig_filename = name
                    zip_file.writestr(info, b'fixture')
            with self.subTest(names=names), self.assertRaises(self.guard.PublicationError):
                self.check(archive)
        linked = self.base / 'linked.zip'
        with zipfile.ZipFile(linked, 'w') as zip_file:
            info = zipfile.ZipInfo('link');info.external_attr = 0o120777 << 16
            zip_file.writestr(info, b'target')
        with self.assertRaises(self.guard.PublicationError): self.check(linked)

    def test_prefix_trailer_and_mismatched_local_header_are_rejected(self):
        original = self.archive().read_bytes()
        variants = [b'prefix' + original, original + b'trailer']
        changed = bytearray(original);changed[30] ^= 1;variants.append(bytes(changed))
        for index, data in enumerate(variants):
            path = self.base / ('structure-' + str(index) + '.zip');path.write_bytes(data)
            with self.subTest(index=index), self.assertRaises(self.guard.PublicationError):
                self.check(path)

    def test_bytes_hidden_inside_declared_compressed_range_are_rejected(self):
        hidden = ('fixture' + chr(64) + 'fixture.invalid').encode()
        for method in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            path = self.archive('hidden-' + str(method) + '.zip', compression=method)
            data = bytearray(path.read_bytes())
            central = struct.unpack_from('<I', data, len(data) - 6)[0]
            original_size = struct.unpack_from('<I', data, 18)[0]
            data[central:central] = hidden
            shifted = central + len(hidden)
            struct.pack_into('<I', data, 18, original_size + len(hidden))
            struct.pack_into('<I', data, shifted + 20, original_size + len(hidden))
            struct.pack_into('<I', data, len(data) - 6, shifted)
            path.write_bytes(data)
            with self.subTest(method=method), self.assertRaises(self.guard.PublicationError):
                self.check(path)

    def test_corrupt_crc_payload_is_rejected(self):
        path = self.archive(compression=zipfile.ZIP_STORED)
        data = bytearray(path.read_bytes());name_len = struct.unpack_from('<H', data, 26)[0]
        data[30 + name_len] ^= 1;path.write_bytes(data)
        with self.assertRaisesRegex(self.guard.PublicationError, 'invalid-release-zip-payload'):
            self.check(path)

    def test_unknown_and_nested_archive_formats_are_rejected(self):
        nested = self.archive('nested-input.zip').read_bytes()
        for index, (name, data) in enumerate((('inner.zip', nested), ('inner.gz', b'\x1f\x8bcompressed'),
                                            ('inner.dat', b'\xfd7zXZ\0opaque'))):
            path = self.archive('nested-' + str(index) + '.zip', [(name, data)])
            with self.subTest(name=name), self.assertRaisesRegex(self.guard.PublicationError, 'nested-or-unsupported-archive'):
                self.check(path)
        path = self.base / 'not-really.zip';path.write_text('unknown archive format')
        with self.assertRaises(self.guard.PublicationError): self.check(path)

    def test_guard_callbacks_restore_after_rejected_archive(self):
        content, publication = self.guard.check_content, self.guard.check_publication
        path = self.base / 'invalid.zip';path.write_bytes(b'not a zip')
        with self.assertRaises(self.guard.PublicationError): self.check(path)
        self.assertIs(self.guard.check_content, content)
        self.assertIs(self.guard.check_publication, publication)


class PowerShellVariableTests(unittest.TestCase):
    # Automatic/read-only names are case-insensitive. Mutable preferences such
    # as ErrorActionPreference are intentionally not classified as reserved.
    RESERVED = {name.casefold() for name in (
        'args', 'ConsoleFileName', 'EnabledExperimentalFeatures', 'Error', 'Event',
        'EventArgs', 'EventSubscriber', 'ExecutionContext', 'false', 'foreach',
        'HOME', 'Host', 'input', 'IsCoreCLR', 'IsLinux', 'IsMacOS', 'IsWindows',
        'LASTEXITCODE', 'Matches', 'MyInvocation', 'NestedPromptLevel', 'null',
        'PID', 'PROFILE', 'PSBoundParameters', 'PSCommandPath', 'PSCulture',
        'PSDebugContext', 'PSEdition', 'PSHOME', 'PSItem', 'PSScriptRoot',
        'PSSenderInfo', 'PSStyle', 'PSUICulture', 'PSVersionTable', 'PWD',
        'Sender', 'ShellId', 'StackTrace', 'switch', 'this', 'true', '_')}

    @classmethod
    def written_names(cls, source):
        import re
        variable = r'\$(?:\{([A-Za-z_][A-Za-z0-9_:]*)\}|([A-Za-z_][A-Za-z0-9_:]*))'
        patterns = [variable + r'(?:\[[^\r\n]*\]|\.[A-Za-z_][A-Za-z0-9_]*)*\s*(?:[+*/%\-]?=(?!=)|\+\+|--)',
                    r'\bforeach\s*\(\s*' + variable + r'\s+in\b']
        names = set()
        for pattern in patterns:
            for match in re.finditer(pattern, source, re.I):
                name = next(part for part in match.groups() if part is not None)
                parts = name.casefold().split(':')
                if len(parts) == 1 or parts[0] in {'global', 'script', 'local', 'private'}:
                    names.add(parts[-1])
        return names

    def test_reserved_name_detection_is_case_insensitive(self):
        self.assertEqual(self.written_names("$home = 1; $HoMe = 2; ${HOME} = 3; $global:PiD += 1; foreach ($hOsT in @()) {}") & self.RESERVED,
                         {'home', 'pid', 'host'})
        self.assertEqual(self.written_names("$homeResponse = 1; $ErrorActionPreference = 'Stop'; $env:HOME = 'fixture'") & self.RESERVED, set())

    def test_smoke_and_workflow_do_not_assign_reserved_variables(self):
        import yaml
        sources = [('smoke', (ROOT / 'scripts/windows_release_smoke.ps1').read_text())]
        workflow = yaml.safe_load((ROOT / '.github/workflows/release-build.yml').read_text())
        sources.extend((step['name'], step.get('run', '')) for job in workflow['jobs'].values()
                       for step in job['steps'] if step.get('shell') == 'pwsh')
        for name, source in sources:
            with self.subTest(source=name):
                self.assertEqual(self.written_names(source) & self.RESERVED, set())
        # Function and script parameter bindings were also reviewed explicitly.
        smoke = sources[0][1]
        for parameter in ('Candidates', 'Output', 'Packager', 'PayloadValidator', 'Record', 'Owned'):
            self.assertNotIn(parameter.casefold(), self.RESERVED)
        self.assertEqual(self.written_names(smoke.replace('$homeResponse', '$home')) & self.RESERVED, {'home'})

    def test_home_response_rename_preserves_the_existing_assertions(self):
        smoke = (ROOT / 'scripts/windows_release_smoke.ps1').read_text()
        self.assertNotRegex(smoke, r'(?i)\$home\b')
        self.assertIn('$homeResponse = Invoke-WebRequest "http://127.0.0.1:$port/" -TimeoutSec 10', smoke)
        self.assertIn("if ($homeResponse.StatusCode -ne 200) { throw 'Home response failed' }", smoke)
        self.assertIn('$row.http_home = 200', smoke)



class PayloadEncodingTests(unittest.TestCase):
    def setUp(self):
        import validate_release_payload
        self.payload = validate_release_payload
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output = Path(self.tmp.name)

    def test_explicit_utf8_preserves_isolation_and_ignores_encoding_environment(self):
        import os
        self.assertEqual(self.payload.isolated_python()[1:], ['-I', '-B', '-X', 'utf8'])
        with patch.dict(os.environ, {'PYTHONUTF8': '0', 'PYTHONIOENCODING': 'ascii', 'LC_ALL': 'C'}):
            state = self.payload.check_child_encoding(self.output)
        self.assertEqual(state['utf8_mode'], 1)
        self.assertEqual(state['isolated'], 1)
        self.assertEqual(state['ignore_environment'], 1)

    def test_japanese_help_survives_pipe_and_bytes_are_preserved(self):
        import os
        text = '\u65e5\u672c\u8a9e'
        code = "import argparse; p=argparse.ArgumentParser(description=" + repr(text) + "); p.parse_args(['--help'])"
        with patch.dict(os.environ, {'PYTHONUTF8': '0', 'PYTHONIOENCODING': 'ascii', 'LC_ALL': 'C'}):
            stdout, stderr = self.payload.run_checked(self.payload.isolated_python() + ['-c', code], self.output, 'help', 30)
        self.assertIn(text, stdout)
        self.assertEqual(stderr, '')
        self.assertEqual((self.output / 'help.stdout.log').read_bytes(), stdout.encode('utf-8'))

    def test_nonzero_exit_retains_raw_diagnostics_without_printing_them(self):
        import contextlib
        import io
        import subprocess
        raw = b'ModuleNotFoundError: private diagnostic \xff'
        result = subprocess.CompletedProcess(['fixture'], 1, b'private stdout', raw)
        printed = io.StringIO()
        with patch.object(self.payload.subprocess, 'run', return_value=result), contextlib.redirect_stdout(printed):
            with self.assertRaises(subprocess.CalledProcessError):
                self.payload.run_checked(['fixture'], self.output, 'help', 30)
        self.assertEqual((self.output / 'help.stderr.log').read_bytes(), raw)
        self.assertNotIn('private', printed.getvalue())
        diagnostic = json.loads(printed.getvalue())
        self.assertEqual(diagnostic['stderr_exception_markers'], ['ModuleNotFoundError'])
        self.assertEqual(diagnostic['returncode'], 1)

    def test_timeout_keeps_original_failure_and_partial_bytes(self):
        import contextlib
        import io
        import subprocess
        error = subprocess.TimeoutExpired(['fixture'], 30, output=b'partial', stderr=b'detail')
        with patch.object(self.payload.subprocess, 'run', side_effect=error), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                self.payload.run_checked(['fixture'], self.output, 'help', 30)
        self.assertIs(caught.exception, error)
        self.assertEqual((self.output / 'help.stdout.log').read_bytes(), b'partial')
        self.assertTrue(json.loads((self.output / 'help.failure.json').read_text())['timeout'])

    def test_wrong_child_encoding_and_isolation_are_rejected(self):
        state = {'stdout': 'utf-8', 'stderr': 'utf-8', 'utf8_mode': 1, 'isolated': 1, 'ignore_environment': 1}
        for key, value in [('stdout', 'cp1252'), ('stderr', 'ascii'), ('utf8_mode', 0), ('isolated', 0), ('ignore_environment', 0)]:
            with self.subTest(key=key), patch.object(self.payload, 'run_checked', return_value=(json.dumps({**state, key: value}), '')):
                with self.assertRaisesRegex(ValueError, 'encoding/isolation'):
                    self.payload.check_child_encoding(self.output)

    def test_cli_flags_timeouts_and_success_gates_are_not_relaxed(self):
        source = (ROOT / 'scripts/validate_release_payload.py').read_text()
        self.assertIn("run_checked(command + ['--help'], args.output, 'help', 30)", source)
        self.assertIn("run_checked(ga_command, args.output, 'ga', 120)", source)
        self.assertIn('assert flag in help_stdout', source)
        self.assertIn('assert isinstance(archive, dict)', source)
        self.assertIn("'--kappa', '0', '--world-expansion', 'off'", source)
        # No failure diagnostics are added to the public artifact allowlist.
        stage = (ROOT / 'scripts/stage_release_evidence.py').read_text()
        self.assertNotIn('.stderr.log', stage)
        self.assertNotIn('.stdout.log', stage)


if __name__ == '__main__':
    unittest.main()
