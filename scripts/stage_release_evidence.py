"""Stage only required build evidence; never upload the raw pip metadata report."""
import argparse
import json
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit


def resolution(report):
    output = []
    for item in report['install']:
        metadata = item['metadata']
        name, version = metadata['name'], metadata['version']
        url = item['download_info']['url']
        checksum = item['download_info']['archive_info']['hashes']['sha256']
        parsed = urlsplit(url)
        if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', name)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.!+_-]*', version)
                or parsed.scheme != 'https' or parsed.netloc != 'files.pythonhosted.org'
                or parsed.query or parsed.fragment or not parsed.path.startswith('/packages/')
                or not re.fullmatch(r'/[A-Za-z0-9/._+-]+', parsed.path)
                or not re.fullmatch(r'[0-9a-f]{64}', checksum)):
            raise ValueError('unexpected package resolution provenance')
        output.append({'package': name, 'version': version, 'download_url': url, 'sha256': checksum})
    if not output or len({x['package'].lower().replace('_', '-') for x in output}) != len(output):
        raise ValueError('empty or duplicate package resolution')
    return sorted(output, key=lambda item: item['package'].casefold())


def stage(temp, destination):
    destination.mkdir(parents=True, exist_ok=False)
    files = ('worldbloom-candidate/WorldBloom-Studio-portable.zip',
             'worldbloom-candidate/SHA256SUMS.txt', 'worldbloom-candidate/package-result.json',
             'worldbloom-smoke/smoke-result.json',
             'worldbloom-smoke/WorldBloom-Studio-payload-check/payload-result.json',
             'worldbloom-build/build-commands.json')
    files = list(files)
    toc_files = sorted((temp / 'worldbloom-build').rglob('*.toc'))
    if not {'Analysis-00.toc', 'PYZ-00.toc', 'PKG-00.toc', 'EXE-00.toc'}.issubset({p.name for p in toc_files}):
        raise ValueError('complete TOC evidence required')
    files.extend(path.relative_to(temp).as_posix() for path in toc_files)
    for name in files:
        source = temp / name
        if not source.is_file() or source.is_symlink():
            raise ValueError('missing or linked required evidence: ' + name)
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    report = json.loads((temp / 'pip-install-report.json').read_text(encoding='utf-8'))
    (destination / 'dependencies-resolved.json').write_text(
        json.dumps(resolution(report), indent=2) + '\n', encoding='utf-8')
    return list(files) + ['dependencies-resolved.json']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--temp', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps({'staged_files': stage(args.temp, args.output)}))
