"""Keep the six already-approved manifest hash fingerprints precise."""
from pathlib import Path
import hashlib
import json
import re
import unittest

ROOT=Path(__file__).resolve().parents[1]
APPROVED_FILES=frozenset(('tests/test_independent_api.py','tests/test_local_status_api.py',
 'tests/test_world_expansion_api.py','viewer/independent_api.py','viewer/job_api.py','viewer/static/ui-tokens.css'))


def validate(manifest_text,ignore_text,read_bytes):
    lines=manifest_text.splitlines();found=set()
    for text in ignore_text.splitlines():
        if not text or text.startswith('#'):continue
        if re.fullmatch(r'[0-9a-f]{40}:launch-assets/package-files.json:generic-api-key:[0-9]+',text):continue
        match=re.fullmatch(r'launch-assets/package-files.json:generic-api-key:([0-9]+)',text)
        if match is None:raise ValueError('Unexpected fingerprint scope')
        number=int(match[1])
        if not 1<=number<=len(lines):raise ValueError('Stale manifest line')
        row=json.loads('{'+lines[number-1].strip().rstrip(',')+'}')
        if len(row)!=1:raise ValueError('A fingerprint must identify one hash')
        name,value=next(iter(row.items()))
        if name not in APPROVED_FILES or name in found:raise ValueError('Unexpected hash exclusion')
        if hashlib.sha256(read_bytes(name)).hexdigest()!=value:raise ValueError('Excluded value is not the file checksum')
        found.add(name)
    if found!=APPROVED_FILES:raise ValueError('Approved working-tree fingerprints are missing')
    return found


class ManifestHashExclusionTests(unittest.TestCase):
    def setUp(self):
        self.manifest=(ROOT/'launch-assets/package-files.json').read_text(encoding='utf8')
        self.ignore=(ROOT/'.gitleaksignore').read_text(encoding='utf8')
        self.read=lambda name:(ROOT/name).read_bytes()

    def test_every_working_tree_exception_is_one_of_the_six_real_checksums(self):
        self.assertEqual(validate(self.manifest,self.ignore,self.read),APPROVED_FILES)

    def test_shifted_lines_are_rejected(self):
        shifted='\n'+self.manifest
        with self.assertRaises((ValueError,json.JSONDecodeError)):validate(shifted,self.ignore,self.read)

    def test_replacing_a_checksum_with_a_credential_like_value_is_rejected(self):
        name='viewer/job_api.py';values=json.loads(self.manifest)
        changed=self.manifest.replace(values[name],'test-placeholder-not-a-checksum',1)
        with self.assertRaises(ValueError):validate(changed,self.ignore,self.read)

    def test_an_extra_exception_cannot_broaden_the_reviewed_scope(self):
        with self.assertRaises(ValueError):validate(self.manifest,self.ignore+'\nother-file:generic-api-key:1\n',self.read)

if __name__=='__main__':unittest.main()
