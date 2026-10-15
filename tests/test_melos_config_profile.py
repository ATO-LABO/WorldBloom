"""Explicit profile config only; no job, API capability, or simulation run."""
from copy import deepcopy
import os
import json
import subprocess
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from execution.configs import ConfigStore, evolution_defaults, normalize
from execution.melos_factory import approved_catalogs
from execution.provenance import ConfigError, canonical, sha256
from gapengine.genome import Genome


def profile():
    return {'id': 'melos_approved_ninegene_v1', 'version': 1, 'genome': Genome.neutral().to_dict()}


def spec():
    return {'label': 'approved neutral session', 'project_id': 'renamed-approved-project',
            'template_id': 'unused-template', 'execution_profile': profile()}


class ProfileNormalizationTests(unittest.TestCase):
    def test_explicit_exact_profile_and_legacy_defaults_unchanged(self):
        value = spec(); normalized = normalize(value)
        self.assertEqual(normalized['execution_profile'], profile())
        self.assertEqual(normalized['evolution'], evolution_defaults())
        value['execution_profile']['genome']['category_weight']['I'] = .9
        self.assertEqual(normalized['execution_profile'], profile())
        legacy = spec(); legacy.pop('execution_profile')
        self.assertEqual(normalize(legacy), {'label': legacy['label'], 'project_id': legacy['project_id'],
            'template_id': legacy['template_id'], 'evolution': evolution_defaults(),
            'execution_limits': {'wall_seconds': 3600}})

    def test_profile_rejects_unknown_non_neutral_and_coercible_shapes(self):
        edits = [lambda p: p.update(id='other'), lambda p: p.update(version=True),
                 lambda p: p.update(version=2), lambda p: p.update(extra=1),
                 lambda p: p.pop('genome'), lambda p: p['genome'].update(plasticity=0),
                 lambda p: p['genome'].update(rule_bits={}),
                 lambda p: p['genome'].update(novelty_drive=True),
                 lambda p: p['genome'].update(novelty_drive='0'),
                 lambda p: p['genome']['category_weight'].update(I=.6),
                 lambda p: p['genome']['category_weight'].update(I=float('nan'))]
        for edit in edits:
            value = spec(); edit(value['execution_profile'])
            with self.subTest(edit=edit), self.assertRaises(ConfigError): normalize(value)
        for invalid in (None, 'melos_approved_ninegene_v1', []):
            with self.assertRaises(ConfigError): normalize({**spec(), 'execution_profile': invalid})

    def test_actual_javascript_serialized_spec_normalizes_numeric_zero(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(['node', '-e', "console.log(JSON.stringify(require('./viewer/static/purpose-independent.js').melosProfileSpec()))"],
                                cwd=root, capture_output=True, text=True, check=True)
        value = json.loads(result.stdout)
        self.assertIs(type(value['execution_profile']['genome']['novelty_drive']), int)
        self.assertEqual(normalize(value)['execution_profile'], profile())
        for name in ('novelty_drive', 'stance_shift_bias'):
            bad = deepcopy(value); bad['execution_profile']['genome'][name] = False
            with self.assertRaises(ConfigError): normalize(bad)

    def test_effectful_overrides_rejected_but_legacy_counts_are_inert(self):
        edits = [{'coevolve': True}, {'meta_evolution': True}, {'personality_growth': True},
                 {'world_expansion': 'detect'}, {'world_expansion': 'expand'},
                 {'seed_genomes': 'old-run'}, {'target_ending': ['observation']},
                 {'keep': 'exemplar'}, {'kappa': .1}, {'route_rho': .1},
                 {'rationality_backend': 'ollama'}, {'rationality_backend': 'none'},
                 {'rationality_backend': 'jev'}, {'rationality_method': 'choice'},
                 {'rationality_model': 'some-model'}, {'rationality_num_ctx': 2048},
                 {'rationality_max_calls': 1}]
        for evolution in edits:
            with self.subTest(evolution=evolution), self.assertRaises(ConfigError):
                normalize({**spec(), 'evolution': evolution})
        for growth in ({'mode': 'auto'}, {'mode': 'manual'}):
            with self.assertRaises(ConfigError): normalize({**spec(), 'growth': growth})
        with self.assertRaises(ConfigError): normalize({**spec(), 'generation': {'backend': 'none'}})
        for keep in ('all', 'reached'):
            result = normalize({**spec(), 'evolution': {'generations': 7, 'population': 12, 'keep': keep}})
            self.assertEqual(result['evolution']['population'], 12)
        self.assertNotIn('growth', normalize({**spec(), 'growth': {'mode': 'off'}}))


@unittest.skipUnless(os.environ.get('MELOS_INPUT_DIR'), 'approved project fixture not supplied')
class ProfileCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.repo = self.root/'repo'
        self.project = self.repo/'projects'/spec()['project_id']
        shutil.copytree(Path(os.environ['MELOS_INPUT_DIR']), self.project)
        (self.repo/'templates'/spec()['template_id']).mkdir(parents=True)
        (self.repo/'templates'/spec()['template_id']/'canon.yaml').write_text('{}')
        self.store = ConfigStore(self.repo, self.root/'control', self.root/'runs')

    def test_complete_nine_yaml_capture_preserves_bytes_paths_and_preview_scope(self):
        saved = self.store.save(spec(), config_id='profile')
        config, manifest, root = self.store._bundle('profile')
        prefix = 'projects/' + spec()['project_id'] + '/'
        self.assertEqual({r['path'].removeprefix(prefix): r['sha256'] for r in manifest['files']},
                         approved_catalogs()['inputs'])
        self.assertEqual(len(manifest['files']), 9)
        for entry in manifest['files']:
            self.assertTrue(entry['path'].startswith(prefix))
            self.assertNotIn('transformation', entry)
            self.assertEqual((root/'inputs'/entry['path']).read_bytes(), (self.repo/entry['path']).read_bytes())
        preview = saved['preview']
        self.assertEqual(preview['execution_kind'], 'independent_trials')
        self.assertFalse(preview['ga_supported'])
        self.assertIsNone(preview['planned_seed_evaluations'])
        self.assertIsNone(preview['planned_individual_evaluations'])
        self.assertEqual(preview['fixed_parameters']['genome'], Genome.neutral().to_dict())
        self.assertFalse(self.store.runs.exists())

    def test_changed_extra_missing_or_alternate_suffix_yaml_rejected(self):
        scenario = self.project/'scenario.yaml'; original = scenario.read_bytes()
        edits = [('changed', lambda: scenario.write_bytes(original + b'\n# changed\n'), lambda: scenario.write_bytes(original)),
                 ('missing', lambda: scenario.unlink(), lambda: scenario.write_bytes(original)),
                 ('extra', lambda: (self.project/'extra.yaml').write_text('{}'), lambda: (self.project/'extra.yaml').unlink()),
                 ('yml', lambda: (self.project/'extra.yml').write_text('{}'), lambda: (self.project/'extra.yml').unlink())]
        for name, edit, undo in edits:
            edit()
            with self.subTest(name=name), self.assertRaises(ConfigError): self.store.preview(spec())
            undo()
        self.assertFalse(self.store.control.exists())

    def test_no_name_dispatch_and_profile_transition_requires_fresh_capture(self):
        legacy = spec(); legacy.pop('execution_profile')
        self.store.save(legacy, config_id='legacy')
        old, old_manifest, _ = self.store._bundle('legacy')
        self.assertNotIn('execution_profile', old)
        self.assertFalse(any(r['path'].endswith('/scenario.yaml') for r in old_manifest['files']))
        with self.assertRaises(ConfigError):
            self.store.save(spec(), config_id='bad-parent', parent_config_id='legacy')
        changed = self.store.duplicate('legacy', {'execution_profile': profile()}, new_id='profile')
        self.assertIsNone(changed['parent_config_id'])
        _, manifest, _ = self.store._bundle('profile')
        self.assertEqual(len(manifest['files']), 9)
        disabled = self.store.duplicate('profile', {'execution_profile': None}, new_id='legacy-again')
        self.assertIsNone(disabled['parent_config_id'])
        self.assertNotIn('execution_profile', disabled)

    def test_same_profile_duplicate_retains_frozen_inputs_and_changed_profile_cannot_reuse(self):
        self.store.save(spec(), config_id='profile')
        (self.project/'scenario.yaml').write_bytes(b'invalid live edit')
        duplicate = self.store.duplicate('profile', {'label': 'renamed'}, new_id='copy')
        self.assertEqual(duplicate['execution_profile'], profile())
        self.assertEqual(duplicate['parent_config_id'], 'profile')
        self.assertEqual(self.store._bundle('copy')[1], self.store._bundle('profile')[1])
        legacy = spec(); legacy.pop('execution_profile')
        self.store.save(legacy, config_id='legacy')
        with self.assertRaises(ConfigError):
            self.store.duplicate('legacy', {'execution_profile': profile()}, new_id='must-recapture')

    def test_ga_prepare_rejected_before_any_runtime_or_output_creation(self):
        self.store.save(spec(), config_id='profile')
        with patch('execution.configs.code_snapshot') as snapshot:
            with self.assertRaises(ConfigError):
                self.store.prepare_run('profile', run_id='forbidden-ga', job_id='job')
            snapshot.assert_not_called()
        self.assertFalse(self.store.runs.exists())

    def test_linked_project_member_rejected(self):
        target = self.root/'external.yaml'; target.write_bytes((self.project/'scenario.yaml').read_bytes())
        (self.project/'scenario.yaml').unlink()
        try: (self.project/'scenario.yaml').symlink_to(target)
        except OSError: self.skipTest('symlinks unavailable')
        with self.assertRaises(ConfigError): self.store.preview(spec())


if __name__ == '__main__': unittest.main()
