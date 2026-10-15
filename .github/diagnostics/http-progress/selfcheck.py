"""Synthetic privacy/transport checks; never execute the product shard."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import public_log as p


class Checks(unittest.TestCase):
    def test_untrusted_strings_and_settings_are_not_published(self):
        row = {'event': 'fixture.inputs', 'test': 'test_safe.Case.test_value',
               'settings': {'authorization': 'PRIVATE-CANARY'}, 'input_files': [
                   {'path': 'C:\\private\\canary.yaml', 'sha256': '0' * 64}],
               'error': 'PermissionError', 'unknown_key': 'private-person@example.invalid'}
        counters = {'rejected': 0}
        safe = p.sanitize(row, {'test_safe.Case.test_value'}, counters)
        text = json.dumps(safe).encode()
        self.assertNotIn(b'PRIVATE-CANARY', text)
        self.assertNotIn(b'private-person', text)
        self.assertNotIn(b'canary.yaml', text)
        self.assertIn('path_sha256', safe['input_files'][0])
        self.assertGreater(counters['rejected'], 0)
        p.assert_private_free(text)

    def test_complete_framed_streams_round_trip(self):
        output = io.StringIO()
        with redirect_stdout(output):
            for name, value in (('metadata', b'{"safe":true}'), ('order', b'{}\n'), ('phases', b'{"pid":1}\n')):
                p.frame(name, value)
        decoded = p.decode_log(output.getvalue())
        self.assertEqual(decoded['metadata'], b'{"safe":true}')
        self.assertEqual(decoded['phases'], b'{"pid":1}\n')

    def test_missing_and_duplicate_chunks_are_rejected(self):
        output = io.StringIO()
        with redirect_stdout(output):
            for name in ('metadata', 'order', 'phases'):
                p.frame(name, b'{}')
        lines = output.getvalue().splitlines()
        missing = '\n'.join(line for line in lines if not line.startswith('WBLOG1 DATA phases'))
        with self.assertRaises(ValueError):
            p.decode_log(missing)
        data = next(line for line in lines if line.startswith('WBLOG1 DATA phases'))
        with self.assertRaises(ValueError):
            p.decode_log(output.getvalue() + data + '\n')

    def test_raw_private_addresses_and_paths_fail_closed(self):
        for value in (b'private-person@example.invalid', b'C:\\private\\file'):
            with self.assertRaises(ValueError):
                p.frame('metadata', value)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromTestCase(Checks))
    raise SystemExit(not result.wasSuccessful())
