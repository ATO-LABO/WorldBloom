"""Acceptance of the shipped legacy-story editions, separate from engine fixtures."""
import hashlib
import json
from pathlib import Path
import unittest
from test_native_planning import FIXTURE_ROOT, ROOT, FAMILIES

class ShippedLegacyStoryTests(unittest.TestCase):
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
