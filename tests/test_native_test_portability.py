"""Test-package resolution must not depend on an unrelated installed tests package."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NativeTestImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        installed = Path(self.tmp.name) / 'site-packages'
        package = installed / 'tests'; package.mkdir(parents=True)
        (package / '__init__.py').write_text("raise AssertionError('unrelated installed tests imported')\n")
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
                        PYTHONPATH=os.pathsep.join((str(ROOT), str(installed))))

    def child(self, *args):
        result = subprocess.run([sys.executable, '-B', *args], cwd=ROOT, env=self.env,
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        return result

    def test_source_package_wins_installed_collision_without_test_path_injection(self):
        result = self.child('-c', '''
import json, pathlib, tests
from tests import test_calendar_generation_capture as capture
from tests import test_melos_generation as melos
import engine.calendar_generation_capture as product
root = pathlib.Path.cwd().resolve()
assert pathlib.Path(tests.__file__).resolve() == root / 'tests/__init__.py'
assert pathlib.Path(capture.__file__).resolve() == root / 'tests/test_calendar_generation_capture.py'
assert pathlib.Path(melos.fixtures.__file__).resolve() == root / 'tests/test_melos_capture_factory.py'
assert pathlib.Path(product.__file__).resolve() == root / 'engine/calendar_generation_capture.py'
print(json.dumps({'source_package': True, 'source_product': True}))
''')
        self.assertEqual({'source_package': True, 'source_product': True}, json.loads(result.stdout))

    def test_unittest_module_invocation_and_normal_discovery(self):
        commands = (
            ('-m', 'unittest', 'tests.test_calendar_generation_capture.BoundedReadTests',
             'tests.test_melos_generation.MelosNativeGenerationTests.test_initial_write_failures_preserve_exception_and_leave_session_unchanged'),
            ('-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_calendar_generation_capture.py'),
        )
        for command in commands:
            with self.subTest(command=command):
                result = self.child(*command)
                self.assertIn('OK', result.stderr)
                self.assertNotIn('_FailedTest', result.stderr)

    def test_product_import_failure_is_not_masked_by_helper_fallback(self):
        result = self.child('-c', '''
import importlib.abc, sys
class MissingProduct(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'engine.calendar_generation_capture':
            raise ModuleNotFoundError('intentional product import sentinel', name=fullname)
sys.meta_path.insert(0, MissingProduct())
try:
    import tests.test_calendar_generation_capture
except ModuleNotFoundError as error:
    assert error.name == 'engine.calendar_generation_capture'
    assert str(error) == 'intentional product import sentinel'
    print('original product import failure propagated')
else:
    raise AssertionError('product import error was hidden')
''')
        self.assertEqual('original product import failure propagated', result.stdout.strip())
