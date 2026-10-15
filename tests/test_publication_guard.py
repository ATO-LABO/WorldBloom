import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts import publication_guard as guard


class PublicationGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_nested_credential_values_are_blocked_without_printing_values(self):
        value = 'unpublished-' + 'credential-value'
        data = json.dumps({'output': {'provider': {'api_key': value}}}).encode()
        with self.assertRaises(guard.PublicationError) as caught:
            guard.check_content('app/settings.json', data)
        self.assertNotIn(value, str(caught.exception))

    def test_blank_and_documented_placeholder_settings_are_allowed(self):
        guard.check_content('settings.json', b'{"api_key":"","password":null}')
        guard.check_content('settings.json', b'{"api_key":"<your-key>"}')

    def test_provider_key_in_story_is_rejected(self):
        value = 'sk-' + 'aB3cD4eF5gH6iJ7kL8mN9oP0qR1sT2uV'
        with self.assertRaises(guard.PublicationError) as caught:
            guard.check_content('stories/example.md', ('Text ' + value).encode())
        self.assertNotIn(value, str(caught.exception))

    def test_runtime_header_scheme_is_allowed_but_embedded_credentials_are_not(self):
        guard.check_content('app/client.py', b'headers = {"Authorization": "Bearer " + key}')
        with self.assertRaises(guard.PublicationError):
            guard.check_content('app/client.py', b'headers = {"Authorization": "Bearer unpublished-value"}')

    def test_personal_email_needs_review_but_examples_and_noreply_are_allowed(self):
        value = 'private-writer' + '@gmail.com'
        with self.assertRaises(guard.PublicationError) as caught:
            guard.check_content('story.md', value.encode())
        self.assertNotIn(value, str(caught.exception))
        guard.check_content('example.txt', b'user@example.com 123+writer@users.noreply.github.com')

    def test_nul_byte_cannot_hide_private_text(self):
        with self.assertRaises(guard.PublicationError):
            guard.check_content('story.md', ('\0writer' + '@gmail.com').encode())
        with self.assertRaises(guard.PublicationError):
            guard.check_content('settings.json', b'{\0}')

    def test_private_file_and_zip_paths_are_blocked(self):
        for name in ['.env', 'app/credentials.json', 'app/key.pfx', 'control/settings.json', '../outside.txt', 'C:/private.txt']:
            with self.subTest(name=name), self.assertRaises(guard.PublicationError):
                guard.check_name(name)
        archive = self.root / 'release.zip'
        with zipfile.ZipFile(archive, 'w') as out:
            out.writestr('app/settings.json', '{"api_key":"' + 'private-value' + '"}')
        with self.assertRaises(guard.PublicationError):
            guard.check_publication(archive)

    def test_email_in_private_filename_is_redacted(self):
        value = 'private-writer' + '@gmail.com'
        with self.assertRaises(guard.PublicationError) as caught:
            guard.check_name('../' + value + '.txt')
        self.assertNotIn(value, str(caught.exception))

    def test_checksum_manifest_is_stable_and_does_not_hash_itself(self):
        package = self.root / 'package'
        package.mkdir()
        (package / 'README.txt').write_bytes(b'Public release')
        manifest = guard.write_checksums(package)
        expected = hashlib.sha256(b'Public release').hexdigest() + '  README.txt\n'
        self.assertEqual(manifest.read_text(), expected)
        guard.write_checksums(package)
        self.assertEqual(manifest.read_text(), expected)

    def test_source_output_overlap_is_rejected(self):
        source = self.root / 'source'
        for output in [source, source / 'nested', self.root]:
            with self.subTest(output=output), self.assertRaises(guard.PublicationError):
                guard.ensure_output_safe(output, [source])

    def test_symlink_is_rejected_even_if_target_is_inside_source(self):
        public = self.root / 'public.txt'
        public.write_text('public')
        link = self.root / 'linked.txt'
        try:
            link.symlink_to(public)
        except OSError:
            self.skipTest('OS does not permit creating symlinks for this user')
        with self.assertRaises(guard.PublicationError):
            guard.validate_files([link], self.root)


if __name__ == '__main__':
    unittest.main()
