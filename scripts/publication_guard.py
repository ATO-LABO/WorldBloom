"""Reject credentials and private files before publishing directories or ZIPs.

Errors report a rule and relative file name, never the matching value.
This check supplements Gitleaks and a human review of unpublished material/PII.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath


class PublicationError(ValueError):
    pass


PRIVATE_DIRS = {'.git', '.aws', '.ssh', 'control', 'runs', '.venv', 'venv'}
PRIVATE_NAMES = {'secrets.json', 'credentials.json', 'id_rsa', 'id_ed25519'}
PRIVATE_SUFFIXES = {'.pem', '.key', '.p12', '.pfx', '.sqlite', '.sqlite3', '.db'}
BINARY_SUFFIXES = {'.exe', '.dll', '.png', '.jpg', '.jpeg', '.gif', '.ico', '.webp', '.woff', '.woff2', '.ttf', '.otf'}
# mkdocs-material 9.7.7 vendor attribution. Only these exact reviewed bytes
# are exempt from the email check; credential/Gitleaks checks still apply.
REVIEWED_VENDOR_EMAIL_HASHES = {
    'dcfd5eb85b43e975773ecf102220197ef3f985f45abbbee4a5286e137f8097ba',  # bundle.js.map
    '780dccece24891aa19da6285715e2c7d0cd884237a87a3a9be6684823584921f',  # search.js.map
    '1b044c239610ef65fceff6039567442bb1692d413f5ee8686fee81b1a3067761',  # lunr/tinyseg.js
}
CREDENTIAL_FIELDS = {
    'api_key', 'apikey', 'access_token', 'refresh_token', 'client_secret',
    'secret_key', 'private_key', 'password', 'authorization', 'aws_secret_access_key',
}
PLACEHOLDER = re.compile(r'^(?:<[^>]+>|\$\{[^}]+\}|(?:your|example|dummy|test|placeholder)[-_ ].*|sk-\.{3})$', re.I)
EMAIL = re.compile(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
CREDENTIAL_LITERAL = re.compile(
    r'''["']?\b(api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|secret[_-]?key|private[_-]?key|password|authorization)["']?\s*[:=]\s*["']([^"'\n]+)["']''', re.I)
PATTERNS = {
    'github-token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,255}|github_pat_[A-Za-z0-9_]{50,255})\b'),
    'llm-api-key': re.compile(r'\bsk-(?:(?:proj|svcacct|ant-api\d+)-)?[A-Za-z0-9_-]{20,255}\b'),
    'slack-token': re.compile(r'\bxox[baprs]-[A-Za-z0-9-]{15,255}\b'),
    'google-api-key': re.compile(r'\bAIza[A-Za-z0-9_-]{35}\b'),
    'aws-access-key': re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'private-key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----'),
    'credential-url': re.compile(r'https?://[^\s/:@"<>]{2,100}:[^\s/@"<>]{5,100}@[^\s/"<>]+'),
}


def reject(rule: str, name: str) -> None:
    # A private value can also appear in a file name.
    name = EMAIL.sub('[redacted-email]', name)
    for pattern in PATTERNS.values():
        name = pattern.sub('[redacted-secret]', name)
    raise PublicationError(f'Publication blocked: {rule}: {name}')


def check_name(name: str) -> None:
    path = PurePosixPath(name.replace('\\', '/'))
    parts = path.parts
    if path.is_absolute() or '..' in parts or any(':' in p for p in parts):
        reject('unsafe-path', name)
    lowered = [p.lower() for p in parts]
    if any(p in PRIVATE_DIRS for p in lowered):
        reject('private-directory', name)
    leaf = lowered[-1] if lowered else ''
    if leaf in PRIVATE_NAMES or leaf == '.env' or leaf.startswith('.env.') or path.suffix.lower() in PRIVATE_SUFFIXES:
        reject('private-file', name)


def _check_fields(value, name: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower().replace('-', '_')
            if normalized in CREDENTIAL_FIELDS and child not in (None, '', False):
                if not isinstance(child, str) or not PLACEHOLDER.fullmatch(child):
                    reject('nonempty-credential-field', name)
            _check_fields(child, name)
    elif isinstance(value, list):
        for child in value:
            _check_fields(child, name)


def check_content(name: str, data: bytes) -> None:
    text = data.decode('utf-8', errors='replace')
    suffix = PurePosixPath(name).suffix.lower()
    for rule, pattern in PATTERNS.items():
        if pattern.search(text):
            reject(rule, name)
    if '\0' in text and suffix in BINARY_SUFFIXES:
        return  # PII/content review is for text; Gitleaks also scans distribution files.
    for match in CREDENTIAL_LITERAL.finditer(text):
        if match.group(1).lower() == 'authorization' and re.fullmatch(r'(?:Bearer|Basic)\s+', match.group(2), re.I):
            continue  # A header scheme alone contains no credential.
        if not PLACEHOLDER.fullmatch(match.group(2)):
            reject('credential-literal', name)
    for match in (() if hashlib.sha256(data).hexdigest() in REVIEWED_VENDOR_EMAIL_HASHES else EMAIL.finditer(text)):
        address = match.group().lower()
        domain = address.rsplit('@', 1)[1]
        if domain not in {'example.com', 'example.org', 'example.net', 'users.noreply.github.com'} and address != 'noreply@github.com':
            reject('email-needs-publication-review', name)
    if suffix in {'.json', '.jsonl'}:
        try:
            if suffix == '.json':
                _check_fields(json.loads(text), name)
            else:
                for line in text.splitlines():
                    if line.strip():
                        _check_fields(json.loads(line), name)
        except (ValueError, TypeError) as exc:
            if isinstance(exc, PublicationError):
                raise
            reject('invalid-json', name)


def validate_files(files: list[Path], root: Path) -> None:
    root = root.absolute()
    for path in files:
        path = path.absolute()
        try:
            relative = path.relative_to(root)
        except ValueError:
            reject('outside-source-root', '<outside>')
        name = relative.as_posix()
        check_name(name)
        if not path.resolve().is_relative_to(root.resolve()):
            reject('outside-source-root', name)
        for node in (path, *path.parents):
            if node == root:
                break
            if node.is_symlink() or getattr(node, 'is_junction', lambda: False)():
                reject('symbolic-link', name)
        if path.is_file():
            check_content(name, path.read_bytes())


def ensure_output_safe(out_dir: Path, source_dirs: list[Path]) -> None:
    if out_dir.is_symlink() or getattr(out_dir, 'is_junction', lambda: False)():
        reject('symbolic-output-directory', '<output>')
    output = out_dir.resolve()
    if output == Path(output.anchor):
        reject('filesystem-root-output', '<output>')
    for source in source_dirs:
        source = source.resolve()
        if output.is_relative_to(source) or source.is_relative_to(output):
            reject('source-output-overlap', '<output>')


def check_publication(path: Path) -> int:
    if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
        reject('symbolic-link', path.name)
    if path.is_dir():
        files = sorted(path.rglob('*'))
        validate_files(files, path)
        return sum(p.is_file() for p in files)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for entry in archive.infolist():
                check_name(entry.filename)
                if stat.S_ISLNK(entry.external_attr >> 16):
                    reject('symbolic-link', entry.filename)
                if not entry.is_dir():
                    check_content(entry.filename, archive.read(entry))
            return sum(not e.is_dir() for e in archive.infolist())
    if not path.is_file():
        reject('missing-input', path.name)
    validate_files([path], path.parent)
    return 1


def write_checksums(root: Path) -> Path:
    """Write hashes only after validation. Does not claim executable signing."""
    check_publication(root)
    if not root.is_dir():
        raise PublicationError('--checksums requires a directory')
    target = root / 'SHA256SUMS.txt'
    lines = [f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(root).as_posix()}'
             for p in sorted(root.rglob('*')) if p.is_file() and p != target]
    target.write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('paths', type=Path, nargs='+')
    parser.add_argument('--checksums', action='store_true', help='Write SHA256SUMS.txt for one validated directory.')
    args = parser.parse_args(argv)
    if args.checksums and len(args.paths) != 1:
        parser.error('--checksums requires one directory')
    try:
        count = sum(check_publication(path) for path in args.paths)
        if args.checksums:
            write_checksums(args.paths[0])
    except (PublicationError, zipfile.BadZipFile) as exc:
        print(str(exc))
        return 1
    print(json.dumps({'checked_files': count, 'status': 'ok'}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
