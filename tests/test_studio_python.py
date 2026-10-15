"""Studio probes the minimum interpreter without starting GUI or worker jobs."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from viewer import app_desktop


class StudioPythonTests(unittest.TestCase):
    def probe(self, candidates):
        def run(argv, **kwargs):
            self.assertEqual(argv[1:4], ['-I', '-B', '-c'])
            self.assertTrue(kwargs['capture_output'])
            version, has_yaml = candidates[argv[0]]
            if version is None:
                raise OSError('execution alias is unavailable')
            def stop(code):
                raise SystemExit(code)
            def imported(name, *args, **kwargs):
                if name == 'sys':
                    return SimpleNamespace(version_info=version, exit=stop)
                if name == 'yaml' and has_yaml:
                    return SimpleNamespace()
                raise ModuleNotFoundError(name)
            try:
                exec(argv[4], {'__builtins__': {'__import__': imported}})
            except SystemExit as exit:
                return SimpleNamespace(returncode=exit.code)
            except ModuleNotFoundError:
                return SimpleNamespace(returncode=1)
            self.fail('probe did not exit')
        with patch('shutil.which', side_effect=lambda name: name if name in candidates else None), \
             patch('subprocess.run', side_effect=run), \
             patch('subprocess.CREATE_NO_WINDOW', 0, create=True):
            return app_desktop._find_system_python()

    def test_python_311_is_rejected_and_312_fallback_is_selected(self):
        self.assertEqual(self.probe({'python': ((3, 11, 9), True),
                                     'python3': ((3, 12, 0), True)}), 'python3')

    def test_both_supported_versions_pass_the_isolated_probe(self):
        for version in ((3, 12, 0), (3, 13, 0)):
            with self.subTest(version=version):
                self.assertEqual(self.probe({'python': (version, True)}), 'python')

    def test_missing_yaml_and_broken_alias_are_rejected(self):
        self.assertIsNone(self.probe({'python': ((3, 13, 0), False), 'py': (None, False)}))

    def test_studio_explains_new_requirement_before_creating_workspace(self):
        with patch.object(app_desktop, '_find_system_python', return_value=None), \
             patch.object(app_desktop, '_exe_dir') as folder:
            with self.assertRaisesRegex(RuntimeError, r'Python 3\.12'):
                app_desktop._setup_studio(None, None, None)
            folder.assert_not_called()
