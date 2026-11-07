"""Assemble candidates from immutable Git objects and measured Windows binaries.
No downloads, GitHub writes, signing, user settings, or overwrites.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import struct
import subprocess
import sys
import tarfile
import zipfile
from collect_release_licenses import collect as collect_licenses, RUNTIME_PACKAGES

SOURCE_SHA = '36bb15c383ed35c615b5505425c905cb2a612eb4'
SOURCE_TREE = '5fa313918481dfbc35e34b0d10be33693a6c831e'
PACKAGES = ('viewer', 'execution', 'gapengine', 'engine', 'scripts',
            'templates', 'projects', 'samples')
SOURCE_PATHS = (*PACKAGES, 'requirements.txt', 'LICENSE')
MODES = ('WorldBloom-Studio',)
REQUIRED_FILES = ('app/requirements.txt', 'app/LICENSE', 'app/viewer/app_desktop.py',
                  'app/viewer/server.py', 'app/scripts/evolve.py',
                  'app/engine/action_types.json', 'app/engine/story_genres.json')

def sha(data):
    return hashlib.sha256(data).hexdigest()

def safe_name(name):
    path = PurePosixPath(name)
    reserved = {'CON', 'PRN', 'AUX', 'NUL'} | {
        prefix + str(number) for prefix in ('COM', 'LPT') for number in range(1, 10)}
    parts = name.split('/')
    if (not path.parts or path.is_absolute() or path.as_posix() != name
            or any(part in ('', '.', '..') or part.rstrip(' .') != part
                   or part.split('.')[0].upper() in reserved or PureWindowsPath(part).is_reserved()
                   for part in parts)
            or any(ord(char) < 32 or char in '<>:"\\|?*' for char in name)):
        raise ValueError('unsafe package path')
    return path

def git(source, *args):
    return subprocess.check_output(['git', '-C', str(source), *args])

def pe_x64(path):
    data = path.read_bytes()
    if len(data) < 64 or data[:2] != b'MZ':
        raise ValueError('missing PE executable')
    offset = struct.unpack_from('<I', data, 0x3c)[0]
    if offset + 6 > len(data) or data[offset:offset + 4] != b'PE\0\0':
        raise ValueError('invalid PE signature')
    if struct.unpack_from('<H', data, offset + 4)[0] != 0x8664:
        raise ValueError('executable is not x64')

def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from strings(child)
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from strings(key)
            yield from strings(child)

def check_tocs(build):
    pyz = sorted(build.rglob('PYZ-00.toc'))
    if len(pyz) != len(MODES):
        raise ValueError('require one PYZ-00.toc for each executable')
    critical_stdlib = {'statistics', 'zoneinfo', 'http.server', 'multiprocessing', 'urllib.request'}
    for path in pyz:
        module_names = set(strings(ast.literal_eval(path.read_text(encoding='utf-8'))))
        base_library = path.parent / 'base_library.zip'
        if base_library.is_file():
            with zipfile.ZipFile(base_library) as archive:
                module_names.update(name.removesuffix('.pyc').replace('/__init__', '').replace('/', '.')
                                    for name in archive.namelist())
        if not critical_stdlib.issubset(module_names):
            raise ValueError('required frozen stdlib missing: ' + ', '.join(sorted(critical_stdlib - module_names)))
        for value in module_names:
            if any(value == name or value.startswith(name + '.')
                   for name in PACKAGES[:5]):
                raise ValueError('application code was frozen into executable')
    for path in build.rglob('*.toc'):
        if 'default_root.txt' in path.read_text(encoding='utf-8').lower():
            raise ValueError('developer root was bundled')

def export_source(source, output):
    head = git(source, 'rev-parse', 'HEAD').decode().strip()
    tree = git(source, 'rev-parse', 'HEAD^{tree}').decode().strip()
    if (head, tree) != (SOURCE_SHA, SOURCE_TREE):
        raise ValueError('source is not the approved immutable main')
    if git(source, 'status', '--porcelain', '--untracked-files=no').strip():
        raise ValueError('tracked source changed during build')
    # Canonical Git bytes, never local settings or untracked working-tree files.
    archive = git(source, 'archive', '--format=tar', SOURCE_SHA, *SOURCE_PATHS)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar:
            relative = safe_name(member.name.rstrip('/') if member.isdir() else member.name)
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError('source archive contains non-regular file')
            target = output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = tar.extractfile(member)
            if stream is None:
                raise ValueError('source archive file missing')
            target.write_bytes(stream.read())
    date = git(source, 'show', '-s', '--format=%cs', SOURCE_SHA).decode().strip()
    (output / 'VERSION.txt').write_text(f'{SOURCE_SHA} {date}\n', encoding='utf-8')
    (output / 'settings.json').write_text('{}\n', encoding='utf-8')

def file_records(root):
    records = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
            raise ValueError('linked package entry')
        if path.is_file() and path.relative_to(root).as_posix() != 'MANIFEST.json':
            name = path.relative_to(root).as_posix()
            safe_name(name)
            data = path.read_bytes()
            records[name] = {'bytes': len(data), 'sha256': sha(data)}
    return records

def write_manifest(root):
    (root / 'MANIFEST.json').write_text(json.dumps(file_records(root), indent=2,
        ensure_ascii=False) + '\n', encoding='utf-8')

def verify(root):
    expected = json.loads((root / 'MANIFEST.json').read_text(encoding='utf-8'))
    if file_records(root) != expected:
        raise ValueError('file list or checksum mismatch')
    for package in PACKAGES:
        folder = root / 'app' / package
        if not folder.is_dir() or not any(folder.rglob('*')):
            raise ValueError('missing required payload: ' + package)
    for name in REQUIRED_FILES:
        if not (root / name).is_file():
            raise ValueError('missing required file: ' + name)
    version = (root / 'app/VERSION.txt').read_text(encoding='utf-8').strip()
    if version != SOURCE_SHA + ' 2026-11-07':
        raise ValueError('missing or incorrect VERSION.txt')
    if json.loads((root / 'app/settings.json').read_text(encoding='utf-8')) != {}:
        raise ValueError('package must contain only fresh public default settings')
    for forbidden in ('runs', 'control', 'app/.git', 'app/.env', 'app/default_root.txt'):
        if (root / forbidden).exists():
            raise ValueError('private or runtime path in candidate')
    if not (root / 'THIRD_PARTY_LICENSES/CPython/LICENSE.txt').is_file():
        raise ValueError('required CPython license missing')
    if not (root / 'THIRD_PARTY_LICENSES/inventory.json').is_file():
        raise ValueError('required license inventory missing')
    closure = json.loads((root / 'THIRD_PARTY_LICENSES/closure-proof.json').read_text(encoding='utf-8'))
    proof = closure.get('proof')
    if (closure.get('scope') != 'observed frozen distribution plus declared runtime/bootloader'
            or not proof or not {'Analysis-00.toc', 'PYZ-00.toc', 'PKG-00.toc', 'EXE-00.toc'}.issubset(
                {PurePosixPath(row['file']).name for row in proof.get('toc_files', [])})):
        raise ValueError('complete third-party license closure proof required')
    licenses = json.loads((root / 'THIRD_PARTY_LICENSES/inventory.json').read_text(encoding='utf-8'))
    required_licenses = {'CPython', 'Microsoft.Web.WebView2'} | RUNTIME_PACKAGES
    if not required_licenses.issubset({entry['package'] for entry in licenses}):
        raise ValueError('required third-party license inventory incomplete')
    for entry in licenses:
        target = root / 'THIRD_PARTY_LICENSES' / safe_name(entry['file'])
        if not target.is_file() or sha(target.read_bytes()) != entry['sha256']:
            raise ValueError('third-party license checksum mismatch')
    info = json.loads((root / 'build-info.json').read_text(encoding='utf-8'))
    if info['source_sha'] != SOURCE_SHA or info['source_tree'] != SOURCE_TREE:
        raise ValueError('wrong source provenance')
    if info['mode'] not in MODES or info['authenticode_status'] != 'NotSigned':
        raise ValueError('unexpected mode or signing state')
    executables = sorted(p.name for p in root.glob('*.exe'))
    if executables != [info['mode'] + '.exe']:
        raise ValueError('wrong executable file list')
    pe_x64(root / executables[0])
    return {'files': len(expected), 'source_sha': SOURCE_SHA, 'mode': info['mode'],
            'manifest_sha256': sha((root / 'MANIFEST.json').read_bytes())}

def zip_bundle(root, destination):
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob('*')):
            if path.is_file():
                archive.write(path, path.relative_to(root).as_posix())

def extract_verified(archive_path, destination):
    destination.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        # ZipInfo.filename normalizes backslashes on Windows and truncates NULs.
        # Validate the original archive name, before normalization can hide it.
        names = [item.orig_filename for item in entries]
        for name in names:
            safe_name(name)
        if len({name.casefold() for name in names}) != len(names):
            raise ValueError('duplicate ZIP paths')
        for item in entries:
            path = safe_name(item.orig_filename)
            if item.is_dir() or ((item.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError('unexpected directory/link in ZIP')
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(item))
    return verify(destination)

def build(args):
    if args.output.exists():
        raise ValueError('output exists; refusing overwrite')
    if args.output.resolve().is_relative_to(args.source.resolve()):
        raise ValueError('output must be outside source')
    signatures = json.loads(args.signatures.read_text(encoding='utf-8-sig'))
    if signatures != {mode: 'NotSigned' for mode in MODES}:
        raise ValueError('Studio signature must be measured as NotSigned')
    check_tocs(args.build)
    args.output.mkdir(parents=True)
    packages = {dist.metadata['Name']: dist.version for dist in importlib.metadata.distributions()}
    summaries = []
    licenses = args.output / 'license-source'
    license_inventory = collect_licenses(licenses, args.build)
    print(json.dumps({'third_party_license_inventory': license_inventory}))
    for mode in MODES:
        root = args.output / mode
        root.mkdir()
        shutil.copytree(licenses, root / 'THIRD_PARTY_LICENSES')
        export_source(args.source, root / 'app')
        shutil.copyfile(args.build / 'dist' / (mode + '.exe'), root / (mode + '.exe'))
        info = {'source_sha': SOURCE_SHA, 'source_tree': SOURCE_TREE, 'mode': mode,
                'authenticode_status': signatures[mode], 'python': sys.version,
                'packages': packages, 'builder_commit': os.environ.get('GITHUB_SHA'),
                'workflow_run': os.environ.get('GITHUB_RUN_ID'),
                'workflow_attempt': os.environ.get('GITHUB_RUN_ATTEMPT'),
                'runner_image': os.environ.get('ImageVersion'),
                'windows_gui_smoke': 'pending; see separate smoke-result.json',
                'known_high_codeql_alerts': 4, 'license_inventory': license_inventory}
        (root / 'build-info.json').write_text(json.dumps(info, indent=2) + '\n', encoding='utf-8')
        (root / 'README.txt').write_text(
            f'{mode}: unsigned Windows x64 candidate\n'
            'Extract the entire ZIP into a new folder; keep app/ next to the EXE.\n'
            'Windows 10/11 and WebView2 are required. Studio also needs Python 3.12/3.13\n'
            'with PyYAML available under python -I. No model or credentials are bundled.\n'
            'Old worlds are not automatically converted. Keep prior data and versions.\n'
            'Four High CodeQL findings remain unresolved. This is not security clearance.\n'
            'Check the official source and SHA-256. A hash is not a code signature.\n'
            'Do not disable Windows security protections to run an unknown file.\n', encoding='utf-8')
        write_manifest(root)
        summary = verify(root)
        archive = args.output / (mode + '-portable.zip')
        zip_bundle(root, archive)
        summary['extracted_verification'] = extract_verified(archive, args.output / ('verify-' + mode))
        summary['archive'] = archive.name
        summary['sha256'] = sha(archive.read_bytes())
        summaries.append(summary)
    (args.output / 'SHA256SUMS.txt').write_text(''.join(
        x['sha256'] + '  ' + x['archive'] + '\n' for x in summaries), encoding='utf-8')
    (args.output / 'package-result.json').write_text(json.dumps(summaries, indent=2) + '\n')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('build')
    for field in ('source', 'build', 'output', 'signatures'):
        p.add_argument('--' + field, type=Path, required=True)
    p = sub.add_parser('verify')
    p.add_argument('root', type=Path)
    p = sub.add_parser('extract')
    p.add_argument('archive', type=Path)
    p.add_argument('destination', type=Path)
    args = parser.parse_args()
    if args.action == 'build':
        build(args)
    elif args.action == 'verify':
        print(json.dumps(verify(args.root)))
    else:
        print(json.dumps(extract_verified(args.archive, args.destination)))

if __name__ == '__main__':
    main()
