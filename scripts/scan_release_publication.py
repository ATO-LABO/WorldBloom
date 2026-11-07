"""Run the unchanged source guard with exact reviewed public-notice decisions.
Only the email verdict for known CPython and vendored wheel notice placements can be reviewed here.
The original bytes, every other guard verdict, and Gitleaks remain unchanged.
"""
import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import zipfile
import struct
import zlib
from validate_release_zip import is_archive, validate_zip

GUARD_SHA256 = 'cf18a58567e56052617d541b796b15ae55ed9cb21c8aadffe53d611794df5203'
NOTICE_SHA256 = 'e502c6b880ff58d614901495a9009c136539cd0b1e2a2abb8fc00b934c203419'
NOTICE_PATHS = frozenset({
    'CPython/LICENSE.txt',
    'license-source/CPython/LICENSE.txt',
    'WorldBloom-Studio/THIRD_PARTY_LICENSES/CPython/LICENSE.txt',
    'verify-WorldBloom-Studio/THIRD_PARTY_LICENSES/CPython/LICENSE.txt',
    'THIRD_PARTY_LICENSES/CPython/LICENSE.txt',
})

WHEEL_NOTICE_SHA256 = '30c23618679108f3e8ea1d2a658c7ca417bdfc891c98ef1a89fa4ff0c9828654'
WHEEL_NOTICE_PATHS = frozenset({
    'setuptools/84.2.1/wheel-0.46.3-LICENSE.txt',
    'license-source/setuptools/84.2.1/wheel-0.46.3-LICENSE.txt',
    'WorldBloom-Studio/THIRD_PARTY_LICENSES/setuptools/84.2.1/wheel-0.46.3-LICENSE.txt',
    'verify-WorldBloom-Studio/THIRD_PARTY_LICENSES/setuptools/84.2.1/wheel-0.46.3-LICENSE.txt',
    'THIRD_PARTY_LICENSES/setuptools/84.2.1/wheel-0.46.3-LICENSE.txt',
})
# Each placement is bound to its own reviewed bytes; never combine separate sets.
REVIEWED_NOTICE_HASHES = {**dict.fromkeys(NOTICE_PATHS, NOTICE_SHA256),
                          **dict.fromkeys(WHEEL_NOTICE_PATHS, WHEEL_NOTICE_SHA256)}


def load_guard(path):
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != GUARD_SHA256:
        raise ValueError('Publication guard differs from the reviewed immutable source')
    spec = importlib.util.spec_from_file_location('worldbloom_original_publication_guard', path)
    if spec is None or spec.loader is None:
        raise ValueError('Cannot load original publication guard')
    module = importlib.util.module_from_spec(spec)
    # Execute the exact bytes just verified, not a second read of the file.
    exec(compile(data, str(path), 'exec'), module.__dict__)
    return module


@contextmanager
def reviewed_notice_guard(guard):
    original = guard.check_content

    def check_content(name, data):
        try:
            original(name, data)
        except guard.PublicationError as error:
            # Always run the original checker first. Earlier credential checks
            # still reject. This exact .txt notice has no later JSON-field check.
            if (name not in REVIEWED_NOTICE_HASHES
                    or str(error) != 'Publication blocked: email-needs-publication-review: ' + name
                    or hashlib.sha256(data).hexdigest() != REVIEWED_NOTICE_HASHES[name]):
                raise
            # Exact official redistribution notice with reviewed public attribution.
            # No file bytes are transformed, removed, or replaced.
    guard.check_content = check_content
    try:
        yield guard
    finally:
        guard.check_content = original


@contextmanager
def structured_archive_guard(guard):
    original_publication = guard.check_publication
    original_content = guard.check_content
    archive_counts = {}

    def archive_entry(name, data):
        if is_archive(name, data):
            guard.reject('nested-or-unsupported-archive', name)
        original_content(name, data)

    def dispatch_content(name, data):
        if not is_archive(name, data):
            return original_content(name, data)
        try:
            expected = validate_zip(guard, name, data, original_content)
        except (zipfile.BadZipFile, EOFError, UnicodeError, ValueError, struct.error, zlib.error) as error:
            if isinstance(error, guard.PublicationError):
                raise
            guard.reject('invalid-release-zip', name)
        # The original validator has already checked the container's filesystem
        # path. Scan a byte-identical private snapshot via its canonical ZIP API.
        # No compressed-byte UTF-8 interpretation or unreviewed file is skipped.
        with tempfile.TemporaryDirectory(prefix='wb-zip-scan-') as temporary:
            snapshot = Path(temporary) / 'candidate.zip'
            snapshot.write_bytes(data)
            guard.check_content = archive_entry
            try:
                try:
                    checked = original_publication(snapshot)
                except (zipfile.BadZipFile, RuntimeError, NotImplementedError, zlib.error):
                    guard.reject('invalid-release-zip-payload', name)
            finally:
                guard.check_content = dispatch_content
            if checked != expected:
                guard.reject('zip-entry-count-mismatch', name)
            archive_counts[name] = checked

    def check_publication(path):
        if path.is_file():
            archive_counts.pop(path.name, None)
            # Preserves the original name, containment, symlink and content checks.
            guard.validate_files([path], path.parent)
            return archive_counts.get(path.name, 1)
        return original_publication(path)

    guard.check_content = dispatch_content
    guard.check_publication = check_publication
    try:
        yield guard
    finally:
        guard.check_content = original_content
        guard.check_publication = original_publication


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--guard', type=Path, required=True)
    parser.add_argument('paths', type=Path, nargs='+')
    args = parser.parse_args()
    guard = load_guard(args.guard)
    with reviewed_notice_guard(guard), structured_archive_guard(guard):
        return guard.main([str(path) for path in args.paths])


if __name__ == '__main__':
    raise SystemExit(main())
