"""Preserve notices for declared runtime/bootloader and observed frozen dependencies.
Preflight checks availability only. Final closure requires complete build evidence.
No notice text or publication-guard rule is changed.
"""
import ast
import hashlib
import importlib.metadata
import json
from pathlib import Path, PurePosixPath
import shutil
import sys
import zipfile

RUNTIME_PACKAGES = {'pywebview', 'pyyaml', 'pythonnet', 'clr-loader', 'cffi',
                    'pycparser', 'bottle', 'proxy-tools', 'typing-extensions', 'pyinstaller'}
PROXY_COMMIT = 'db43f1e35d4f90a65c5a4d56d9e9af88212ec6e6'
PROXY_CODE_BLOB = 'ea266c8f49da949ec6199a616609637c9aab3fb8'
PROXY_LICENSE_BLOB = '078411c7399fbc0454f36cbe8c8cdbaaba7ebc95'
WHEEL_NOTICE = 'setuptools/_vendor/wheel-0.46.3.dist-info/licenses/license.txt'
WHEEL_NOTICE_SHA = '30c23618679108f3e8ea1d2a658c7ca417bdfc891c98ef1a89fa4ff0c9828654'
ARCHIVES = {'.zip', '.egg', '.whl', '.jar', '.pyz', '.pkg'}


def canonical(name):
    return name.lower().replace('_', '-')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def git_blob(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def normalized(value):
    return str(value).replace('\\', '/').casefold()


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, (tuple, list)):
        for child in value:
            yield from strings(child)
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from strings(key)
            yield from strings(child)


def has_wheel(value):
    # Module, vendored module, metadata, and data directory forms.
    value = normalized(value).replace('.', '/')
    return any(part == 'wheel' or part.startswith('wheel-') for part in value.split('/'))


def archive_bindings(value):
    if isinstance(value, (list, tuple)):
        if (len(value) == 3 and all(isinstance(x, str) for x in value)
                and PurePosixPath(normalized(value[0])).suffix in ARCHIVES
                and value[2] in {'DATA', 'ZIPFILE', 'PYZ', 'PKG'}):
            yield normalized(value[0]), normalized(value[1])
        for child in value:
            yield from archive_bindings(child)
    elif isinstance(value, dict):
        for child in value.values():
            yield from archive_bindings(child)


def toc_evidence(build):
    """Fail closed before relying on absent distributions or vendored wheel."""
    folder = build / 'build' / 'WorldBloom-Studio'
    required = {'Analysis-00.toc', 'PYZ-00.toc', 'PKG-00.toc', 'EXE-00.toc'}
    paths = sorted(build.rglob('*.toc'))
    if not required.issubset({p.name for p in paths if p.parent == folder}):
        raise ValueError('complete Studio Analysis/PYZ/PKG/EXE TOCs required for notice closure')
    values, records, internal_archives, original_paths = set(), [], set(), {}
    destinations = {}
    for path in paths:
        try:
            data = path.read_bytes()
            parsed = ast.literal_eval(data.decode('utf-8'))
        except (SyntaxError, ValueError, UnicodeError) as error:
            raise ValueError('unparseable build TOC: ' + path.name) from error
        if not isinstance(parsed, (tuple, list)) or not parsed:
            raise ValueError('unclassified build TOC: ' + path.name)
        for value in strings(parsed):
            values.add(normalized(value))
            if Path(value).is_file():
                original_paths[normalized(value)] = Path(value)
        for destination, source in archive_bindings(parsed):
            if destination in destinations and destinations[destination] != source:
                raise ValueError('conflicting archive destination/source binding')
            destinations[destination] = source
        records.append({'file': path.relative_to(build).as_posix(), 'sha256': digest(data)})
        if path.name in {'PYZ-00.toc', 'PKG-00.toc'}:
            if not isinstance(parsed[0], str) or not Path(parsed[0]).is_file():
                raise ValueError('missing generated archive for ' + path.name)
            internal_archives.add(normalized(parsed[0]))
    for destination, source in destinations.items():
        if source not in original_paths:
            raise ValueError('missing source for archive destination: ' + destination)
    archives = []
    classified_sources = set()
    for value in sorted(values):
        suffix = PurePosixPath(value).suffix
        if suffix not in ARCHIVES or '/' not in value:
            continue
        # Directory-containing DATA destinations are aliases, not source paths.
        # Their corresponding existing source remains in values and is classified below.
        if value in destinations and destinations[value] != value:
            continue
        path = original_paths.get(value)
        if path is None:
            raise ValueError('unclassified or missing bundled archive: ' + value)
        if value in internal_archives:
            kind = 'generated archive described by complete matching TOC'
        elif suffix in {'.zip', '.jar'}:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
            if any(has_wheel(n) for n in names):
                values.add('wheel/archive-member')
            if path.name == 'base_library.zip' and path.parent == folder:
                for name in names:
                    if name.endswith('/'):
                        continue
                    parts = PurePosixPath(name).parts
                    top = parts[0].split('.')[0]
                    if top not in sys.stdlib_module_names or not name.endswith('.pyc'):
                        raise ValueError('unclassified base_library member: ' + name)
                kind = 'enumerated CPython standard library bytecode'
            elif suffix == '.jar':
                if any(PurePosixPath(n).suffix in ARCHIVES | {'.py', '.pyc', '.pyd'}
                       or '.dist-info/' in n or '.egg-info/' in n for n in names):
                    raise ValueError('unclassified Python or nested archive in JAR')
                kind = 'enumerated non-Python JAR members'
            else:
                raise ValueError('unclassified bundled ZIP: ' + value)
            values.update(normalized(n) for n in names)
        else:
            raise ValueError('unclassified bundled archive: ' + value)
        classified_sources.add(value)
        archives.append({'name': path.name, 'sha256': digest(path.read_bytes()), 'classification': kind})
    if not set(destinations.values()).issubset(classified_sources):
        raise ValueError('archive destination has an unclassified source')
    # Compare installed file identities and TOC source identities on the same
    # resolved, case/separator-normalized basis (including Windows junctions).
    values.update(normalized(path.resolve()) for path in original_paths.values())
    return {'toc_files': records, 'archives': archives, 'wheel_absent': not any(map(has_wheel, values))}, values


def notice_files(dist):
    found = []
    for file in dist.files or ():
        parts = PurePosixPath(normalized(file)).parts
        basename = parts[-1]
        if (any(p.endswith(('.dist-info', '.egg-info')) for p in parts)
                and basename.startswith(('license', 'copying', 'notice', 'copyright'))
                and not basename.endswith(('.py', '.pyc'))):
            found.append(file)
    return sorted(found, key=str)


def observed_distribution(dist, values):
    if dist.files is None:
        raise ValueError('distribution has no file inventory: ' + dist.metadata['Name'])
    for file in dist.files:
        path = normalized(Path(dist.locate_file(file)).resolve())
        if path in values or (path.endswith('.py') and path + 'c' in values):
            return True
    return False


def omit_known_unbundled_wheel(name, version, file, data, proof):
    if name == 'setuptools' and normalized(file) == WHEEL_NOTICE:
        if version != '84.2.1' or digest(data) != WHEEL_NOTICE_SHA:
            raise ValueError('vendored wheel notice differs from reviewed exact version/bytes')
        if not proof or not proof['wheel_absent']:
            raise ValueError('vendored wheel may be distributed; original notice requires publication review')
        return True
    return False


def proxy_notice(dist):
    if dist.version != '0.1.0':
        raise ValueError('proxy-tools fallback is restricted to reviewed version 0.1.0')
    code = Path(dist.locate_file('proxy_tools/__init__.py')).read_bytes()
    if git_blob(code) != PROXY_CODE_BLOB:
        raise ValueError('proxy-tools source differs from reviewed official sdist/upstream code')
    source = Path(__file__).resolve().parents[1] / 'release/licenses/proxy_tools-LICENSE.txt'
    data = source.read_bytes()
    if git_blob(data) != PROXY_LICENSE_BLOB:
        raise ValueError('proxy-tools upstream notice bytes changed')
    return data, {'source': f'https://github.com/jtushman/proxy_tools/blob/{PROXY_COMMIT}/LICENSE.txt',
                  'license_git_blob': PROXY_LICENSE_BLOB, 'code_git_blob': PROXY_CODE_BLOB,
                  'sdist_sha256': 'ccb3751f529c047e2d8a58440d86b205303cf0fe8146f784d1cbcd94f0a28010',
                  'metadata_discrepancy': 'Official 0.1.0 sdist setup.py says MIT; matching upstream source has BSD notice. Original BSD text retained; no legal certainty asserted.'}


def collect(destination, build=None):
    destination.mkdir(parents=True, exist_ok=False)
    inventory = []
    proof, values = toc_evidence(build) if build is not None else (None, set())
    closure = {'scope': 'observed frozen distribution plus declared runtime/bootloader' if proof else 'declared runtime/bootloader preflight only',
               'proof': proof, 'excluded_distributions': [], 'omitted_notices': []}

    def save(name, version, relative, data, **extra):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        inventory.append({'package': name, 'version': version, 'file': relative,
                          'sha256': digest(data), **extra})

    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if not python_license.is_file():
        raise ValueError('CPython LICENSE.txt is missing')
    save('CPython', sys.version.split()[0], 'CPython/LICENSE.txt', python_license.read_bytes())
    vendor = Path(__file__).resolve().parents[1] / 'release/licenses/Microsoft.Web.WebView2.LICENSE.md'
    data = vendor.read_bytes()
    if git_blob(data) != '5dd43f57f273797aea6904ae8000c870e424b35a':
        raise ValueError('WebView2 vendor notice differs from reviewed upstream bytes')
    save('Microsoft.Web.WebView2', 'pywebview-6.2.1', vendor.name, data)
    installed = {canonical(dist.metadata['Name']): dist for dist in importlib.metadata.distributions()}
    if not RUNTIME_PACKAGES.issubset(installed):
        raise ValueError('Required Windows dependency missing: ' + ', '.join(sorted(RUNTIME_PACKAGES - installed.keys())))
    for name, dist in sorted(installed.items()):
        observed = observed_distribution(dist, values) if proof else False
        selected = name in RUNTIME_PACKAGES or observed
        if not selected:
            closure['excluded_distributions'].append({'package': name, 'version': dist.version,
                'reason': 'no installed file referenced by complete TOCs' if proof else 'not in declared preflight scope'})
        paths = notice_files(dist)
        if selected and name == 'proxy-tools' and not paths:
            data, provenance = proxy_notice(dist)
            save(name, dist.version, 'proxy-tools/0.1.0/upstream-LICENSE.txt', data, **provenance)
            continue
        if selected and not paths:
            raise ValueError('Required third-party notice missing: ' + name)
        for index, file in enumerate(paths):
            source = Path(dist.locate_file(file))
            if not source.is_file() or source.is_symlink():
                raise ValueError('Third-party notice is missing or linked: ' + name)
            data = source.read_bytes()
            # Preflight omits build-only distributions, not notices within selected packages.
            if not selected and proof is None:
                continue
            if omit_known_unbundled_wheel(name, dist.version, file, data, proof):
                closure['omitted_notices'].append({'package': name, 'version': dist.version,
                    'original_path': str(file), 'sha256': digest(data),
                    'reason': 'exact vendored wheel code/metadata/data absent from all TOCs and inspected archives'})
                continue
            if selected:
                save(name, dist.version, f'{name}/{dist.version}/{index}-{source.name}', data,
                     installed_path=str(file))
    (destination / 'inventory.json').write_text(json.dumps(inventory, indent=2) + '\n', encoding='utf-8')
    (destination / 'closure-proof.json').write_text(json.dumps(closure, indent=2) + '\n', encoding='utf-8')
    return inventory


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--build', type=Path)
    args = parser.parse_args()
    print(json.dumps({'third_party_license_inventory': collect(args.output, args.build)}, indent=2))
