"""Acceptance of the shipped legacy-story editions, separate from engine fixtures."""
import hashlib
import json
from pathlib import Path
import unittest
from test_native_planning import FIXTURE_ROOT, ROOT, FAMILIES

class ShippedLegacyStoryTests(unittest.TestCase):
    def test_migration_paths_are_portable_and_resolve_to_exact_sources(self):
        for source in FAMILIES:
            edition=ROOT/'projects'/(source+'_shared_planning')
            manifest=json.loads((edition/'migration.json').read_text(encoding='utf8'))
            expected={'world.yaml',*(p.relative_to(ROOT/'projects'/source).as_posix()
                                       for p in (ROOT/'projects'/source/'subjects').glob('*.yaml'))}
            self.assertEqual(set(manifest['source_sha256']),expected)
            for relative,digest in manifest['source_sha256'].items():
                self.assertNotIn('\\',relative)
                self.assertEqual(hashlib.sha256((ROOT/'projects'/source/relative).read_bytes()).hexdigest(),digest)

    def test_shipped_editions_match_safe_reproducible_migration(self):
        for source in FAMILIES:
            expected=FIXTURE_ROOT/'projects'/(source+'_shared_planning')
            actual=ROOT/'projects'/expected.name
            for path in expected.rglob('*'):
                if path.is_file():
                    self.assertEqual(path.read_bytes(),(actual/path.relative_to(expected)).read_bytes(),str(path.relative_to(expected)))

    def test_runtime_package_contains_every_shipped_edition_file(self):
        manifest=json.loads((ROOT/'launch-assets/package-files.json').read_text(encoding='utf8'))
        for source in FAMILIES:
            for path in (ROOT/'projects'/(source+'_shared_planning')).rglob('*'):
                if path.is_file():
                    key=path.relative_to(ROOT).as_posix()
                    self.assertEqual(manifest.get(key),hashlib.sha256(path.read_bytes()).hexdigest(),key)

if __name__=='__main__':unittest.main()
