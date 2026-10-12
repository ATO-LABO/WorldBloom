"""AIT-135: real CPU Choice GA checkpoint continuity and fail-closed recovery.

Only the opt-in fixed-world Choice adapter is exercised here. A passing test is
not evidence of GUI/lineage completion or of the unavailable integration ZIP.
"""
from copy import deepcopy
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from gapengine.evolve import _evolve
from gapengine.festival_choices import ACTORS, ChoiceGenome
from gapengine.festival_ga import ADAPTER
from gapengine import festival_checkpoint as checkpoint
from gapengine.qd import Archive
from scripts.cultural_festival_probe import PROJECT


class InjectedCrash(RuntimeError):
    pass


class FestivalGAResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wb-choice-resume-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        shutil.copytree(PROJECT, self.project)

    def cfg(self, name='run', *, generations=1, population=2, seeds=(0,), **extra):
        return {'search_adapter': ADAPTER, 'out': str(self.root / name),
                'project': str(self.project), 'population': population,
                'generations': generations, 'seeds': list(seeds), 'ga_seed': 3,
                'processes': 1, **extra}

    @staticmethod
    def state(out):
        return json.loads((Path(out) / 'ga_state.json').read_text(encoding='utf-8'))

    @staticmethod
    def files(out):
        return {p.relative_to(out).as_posix(): p.read_bytes()
                for p in sorted(Path(out).rglob('*')) if p.is_file()
                and not p.name.endswith('.lock')}

    @staticmethod
    def stable_files(out):
        names = {'ga_state.json', 'archive.json', 'summary.json', 'evaluations.jsonl'}
        return {name: raw for name, raw in FestivalGAResumeTests.files(out).items()
                if name in names or re.match(r'^g\d+/', name)}

    def run_cfg(self, cfg):
        # Any accidental external evaluation becomes an immediate test failure.
        with patch('socket.socket.connect', side_effect=AssertionError('external network forbidden')):
            return _evolve(cfg)

    def write_state(self, out, state):
        # Re-sign intentionally modified content to exercise semantic validation,
        # rather than having every corruption case stop only at the checksum.
        sealed = checkpoint.seal(deepcopy(state))
        (out / 'ga_state.json').write_text(json.dumps(sealed, ensure_ascii=False,
            sort_keys=True, indent=2) + '\n', encoding='utf-8')

    def reject_preserving(self, cfg):
        out = Path(cfg['out']); before = self.files(out)
        with patch('gapengine.evolve._evaluate_jobs', side_effect=AssertionError('must reject before evaluation')):
            with self.assertRaises((ValueError, RuntimeError, OSError)):
                _evolve({**cfg, 'resume': True})
        self.assertEqual(self.files(out), before, 'Rejected resume must not delete or rewrite evidence')

    def test_real_four_generation_four_population_two_seed_continuity(self):
        full = self.cfg('full', generations=4, population=4, seeds=(0, 1))
        split = self.cfg('split', generations=2, population=4, seeds=(0, 1))
        archive_full = self.run_cfg(full); self.run_cfg(split)
        out = Path(split['out']); committed_first_half = {
            name: raw for name, raw in self.files(out).items() if name.startswith(('g0/', 'g1/'))}
        archive_split = self.run_cfg({**split, 'generations': 4, 'resume': True})
        self.assertEqual(archive_full.to_dict(), archive_split.to_dict())
        self.assertEqual(self.state(Path(full['out'])), self.state(out))
        self.assertEqual(self.stable_files(Path(full['out'])), self.stable_files(out))
        self.assertEqual(committed_first_half, {name: raw for name, raw in self.files(out).items()
            if name.startswith(('g0/', 'g1/'))}, 'Resume cannot rewrite completed generation files')
        state = self.state(out)
        self.assertEqual(state['completed_generations'], 4)
        self.assertEqual(len(state['history']), 4)
        known = set(); evaluation_ids = set()
        for generation, results in enumerate(state['history']):
            prior_ids = set(known)
            self.assertEqual(len(results), 4)
            for index, individual in enumerate(results):
                candidate = f'g{generation}/ind-{index}'
                self.assertEqual(individual['candidate_id'], candidate)
                self.assertEqual(individual['generation'], generation)
                self.assertEqual(individual['index'], index)
                self.assertTrue(set(individual['parents']) <= prior_ids)
                self.assertEqual(len(ChoiceGenome.from_dict(individual['genome']).choices), 88)
                for run in individual['runs']:
                    evaluation_id = f'{candidate}/seed-{run["seed"]}'
                    self.assertEqual(run['evaluation_id'], evaluation_id)
                    self.assertNotIn(evaluation_id, evaluation_ids); evaluation_ids.add(evaluation_id)
                    self.assertNotIn('performance', run)
                    self.assertTrue(run['traits_unchanged']); self.assertEqual(run['jev_calls'], 0)
                    self.assertEqual(run['audit']['decision_checks'], 88)
                    self.assertEqual(run['temporal_evaluation']['matched_completions'], 88)
                    self.assertEqual(run['temporal_evaluation']['absence_reward'], 0)
                    draws = json.loads((out / candidate / f'seed-{run["seed"]}' / 'choice-draws.json').read_text())
                    self.assertEqual(len(draws), 88)
                    self.assertEqual({d['actor'] for d in draws}, set(ACTORS))
                    genes = ChoiceGenome.from_dict(individual['genome']).choices
                    for draw in draws:
                        self.assertEqual(draw['gene'], genes[ACTORS.index(draw['actor']) * 22 + draw['t']])
                        self.assertEqual(draw['quantile'], ((draw['gene'] + .5) / 2**32 + draw['seed_shift']) % 1)
                known.add(candidate)
        self.assertEqual(len(evaluation_ids), 32)
        self.assertTrue(any(ind['parents'] for gen in state['history'][1:] for ind in gen))
        for elite in archive_split.cells.values():
            self.assertTrue(set(elite.parents) <= known)
            self.assertIn('/'.join(Path(elite.exemplar['layers_path']).parts[:2]), known)
        logs = [json.loads(line) for line in (out / 'evaluations.jsonl').read_text().splitlines()]
        self.assertEqual(len(logs), 32)
        self.assertEqual({entry['evaluation_id'] for entry in logs}, evaluation_ids)
        self.assertEqual(checkpoint.input_fingerprint(self.project), state['input_manifest'])

    def test_noop_resume_evaluates_nothing_and_preserves_all_bytes(self):
        cfg = self.cfg(); self.run_cfg(cfg); out = Path(cfg['out']); before = self.files(out)
        with patch('gapengine.evolve._evaluate_jobs', side_effect=AssertionError('no-op evaluated again')):
            for _ in range(2):
                resumed = _evolve({**cfg, 'resume': True})
        self.assertEqual(resumed.to_dict(), Archive.from_dict(self.state(out)['archive'],
            genome_codec=ChoiceGenome).to_dict())
        self.assertEqual(self.files(out), before)

    def test_derived_outputs_regenerate_without_repeating_evaluation_or_logs(self):
        cfg = self.cfg(); self.run_cfg(cfg); out = Path(cfg['out'])
        expected = {name: (out / name).read_bytes() for name in
                    ('archive.json', 'summary.json', 'evaluations.jsonl')}
        committed = {name: raw for name, raw in self.files(out).items() if name.startswith('g0/')}
        (out / 'archive.json').unlink(); (out / 'summary.json').write_text('broken derived summary')
        (out / 'evaluations.jsonl').write_bytes(b'not a log\nnot a log\n')
        with patch('gapengine.evolve._evaluate_jobs', side_effect=AssertionError('must derive from checkpoint')):
            _evolve({**cfg, 'resume': True})
        self.assertEqual({name: (out / name).read_bytes() for name in expected}, expected)
        self.assertEqual({name: raw for name, raw in self.files(out).items() if name.startswith('g0/')}, committed)

    def test_truncated_bad_checksum_and_duplicate_keys_reject_without_writes(self):
        cfg = self.cfg(); self.run_cfg(cfg); out = Path(cfg['out']); original = (out / 'ga_state.json').read_bytes()
        for broken in (b'{"completed_generations":', b'{}', original[:-8],
                       original.replace(b'"checksum":', b'"checksum":"wrong", "checksum":', 1)):
            with self.subTest(broken=broken[:60]):
                (out / 'ga_state.json').write_bytes(broken)
                self.reject_preserving(cfg)
        (out / 'ga_state.json').write_bytes(original)

    def test_resigned_protocol_python_source_input_and_rng_mismatches_reject(self):
        cfg = self.cfg(); self.run_cfg(cfg); out = Path(cfg['out']); original = self.state(out)
        mutations = {
            'format': lambda s: s.__setitem__('format', 'personality-ga'),
            'version': lambda s: s.__setitem__('version', 999),
            'boolean-version': lambda s: s.__setitem__('version', True),
            'negative-completed': lambda s: s.__setitem__('completed_generations', -1),
            'adapter': lambda s: s.__setitem__('adapter', 'personality'),
            'codec': lambda s: s.__setitem__('codec', 'festival_choices_v999'),
            'python': lambda s: s.__setitem__('python', '0.0-incompatible'),
            'source': lambda s: s.__setitem__('source_manifest', {'files': {}, 'sha256': '0' * 64}),
            'input': lambda s: s.__setitem__('input_manifest', {'files': {}, 'common_settings_sha256': '0' * 64}),
            'rng': lambda s: s.__setitem__('rng_state', [3, [1, 2, 3], None]),
            'unknown-field': lambda s: s.__setitem__('unrecognized', True),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                state = deepcopy(original); mutate(state); self.write_state(out, state)
                self.reject_preserving(cfg)
        self.write_state(out, original)

    def test_population_history_archive_or_artifact_state_corruption_rejects(self):
        cfg = self.cfg(); self.run_cfg(cfg); out = Path(cfg['out']); original = self.state(out)
        def bad_population(state): state['population'][0]['genome']['choices'].pop()
        def bad_history(state): state['history'][0][0]['candidate_id'] = 'g9/ind-0'
        def bad_previous(state): state['previous_results'] = []
        def bad_archive(state): state['archive'] = {'cells': []}
        def bad_artifact(state): state['artifacts'] = {'g0': {'../outside': {'size_bytes': 1, 'sha256': '0' * 64}}}
        for mutate in (bad_population, bad_history, bad_previous, bad_archive, bad_artifact):
            with self.subTest(mutation=mutate.__name__):
                state = deepcopy(original); mutate(state); self.write_state(out, state)
                self.reject_preserving(cfg)
        self.write_state(out, original)

    def test_changed_committed_generation_file_or_missing_layers_refuses(self):
        cfg = self.cfg(); self.run_cfg(cfg); out = Path(cfg['out'])
        path = out / 'g0/ind-0/seed-0/layers.jsonl'; original = path.read_bytes()
        path.write_bytes(original + b'\n'); self.reject_preserving(cfg)
        path.unlink(); self.reject_preserving(cfg)
        path.write_bytes(original)
        result = out / 'g0/ind-0/result.json'; original = result.read_bytes()
        result.write_bytes(original + b'\n'); self.reject_preserving(cfg)

    def test_changed_world_person_or_frozen_input_rejects_without_cleanup(self):
        cfg = self.cfg(); self.run_cfg(cfg); out = Path(cfg['out'])
        unknown = out / '.choice-pending/unrelated-user-file'; unknown.parent.mkdir(exist_ok=True)
        unknown.write_bytes(b'preserve unrelated work')
        for path in (self.project / 'world.yaml', next((self.project / 'subjects').glob('*.yaml'))):
            with self.subTest(path=path.name):
                original = path.read_bytes(); path.write_bytes(original + b'\n# externally changed input\n')
                self.reject_preserving({**cfg, 'generations': 2})
                self.assertEqual(unknown.read_bytes(), b'preserve unrelated work'); path.write_bytes(original)

    def test_changed_resume_config_and_smaller_target_refuse(self):
        cfg = self.cfg(); self.run_cfg(cfg)
        for extra in ({'population': 3}, {'ga_seed': 4}, {'seeds': [1]}, {'processes': 2},
                      {'rationality_cfg': {'backend': 'external'}}, {'growth_enabled': True},
                      {'generations': 0}, {'generations': 5}):
            with self.subTest(extra=extra): self.reject_preserving({**cfg, **extra})
        cfg2 = self.cfg('completed-two', generations=2); self.run_cfg(cfg2)
        self.reject_preserving({**cfg2, 'generations': 1})

    def test_existing_output_requires_explicit_resume_and_missing_checkpoint_is_protected(self):
        cfg = self.cfg(); self.run_cfg(cfg); out = Path(cfg['out']); before = self.files(out)
        with self.assertRaises((ValueError, RuntimeError)):
            self.run_cfg(cfg)
        self.assertEqual(self.files(out), before)
        (out / 'ga_state.json').unlink(); self.reject_preserving(cfg)
        missing = self.cfg('missing', resume=True)
        with self.assertRaises((ValueError, RuntimeError, OSError)):
            self.run_cfg(missing)
        self.assertFalse(Path(missing['out']).exists())

    def test_initial_checkpoint_crash_resumes_exact_initial_population(self):
        cfg = self.cfg('interrupted'); out = Path(cfg['out'])
        with patch('gapengine.evolve._evaluate_jobs', side_effect=InjectedCrash('before first evaluation')):
            with self.assertRaises(InjectedCrash): self.run_cfg(cfg)
        initial = self.state(out)
        self.assertEqual(initial['completed_generations'], 0)
        self.assertEqual(initial['history'], [])
        self.assertEqual(len(initial['population']), 2)
        self.assertEqual(initial['artifacts'], {})
        self.assertFalse((out / 'g0').exists())
        resumed = self.run_cfg({**cfg, 'resume': True})
        clean = self.cfg('clean'); uninterrupted = self.run_cfg(clean)
        self.assertEqual(resumed.to_dict(), uninterrupted.to_dict())
        self.assertEqual(self.stable_files(out), self.stable_files(Path(clean['out'])))

    def test_crash_after_generation_rename_before_checkpoint_commit_recovers_exactly(self):
        cfg = self.cfg('interrupted', generations=2); out = Path(cfg['out'])
        actual_save = checkpoint.save
        def save_crash(out_dir, state):
            if state['completed_generations'] == 2:
                raise InjectedCrash('after g1 publish, before checkpoint marker')
            return actual_save(out_dir, state)
        with patch.object(checkpoint, 'save', side_effect=save_crash):
            with self.assertRaises(InjectedCrash): self.run_cfg(cfg)
        self.assertEqual(self.state(out)['completed_generations'], 1)
        self.assertTrue((out / 'g1').is_dir())
        committed = {n: raw for n, raw in self.files(out).items() if n.startswith('g0/')}
        self.run_cfg({**cfg, 'resume': True}); clean = self.cfg('clean', generations=2); self.run_cfg(clean)
        self.assertEqual(self.stable_files(out), self.stable_files(Path(clean['out'])))
        self.assertEqual(committed, {n: raw for n, raw in self.files(out).items() if n.startswith('g0/')})
        self.assertFalse((out / '.choice-pending').exists())

    def test_atomic_checkpoint_replace_failure_leaves_old_marker_and_can_resume(self):
        cfg = self.cfg('atomic'); out = Path(cfg['out'])
        real_replace = checkpoint.os.replace
        calls = []
        def fail_replace(source, destination):
            if Path(destination) == out / 'ga_state.json' and (out / 'g0').exists():
                calls.append(str(destination)); raise OSError('injected checkpoint disk failure')
            return real_replace(source, destination)
        with patch.object(checkpoint.os, 'replace', side_effect=fail_replace):
            with self.assertRaises(OSError): self.run_cfg(cfg)
        self.assertTrue(calls)
        state = self.state(out); self.assertEqual(state['completed_generations'], 0)
        self.assertFalse(list(out.glob('.ga_state.json-*')))
        self.run_cfg({**cfg, 'resume': True}); clean = self.cfg('clean'); self.run_cfg(clean)
        self.assertEqual(self.stable_files(out), self.stable_files(Path(clean['out'])))

    def test_two_writers_cannot_evaluate_or_commit_the_same_generation(self):
        from gapengine import evolve
        cfg = self.cfg('concurrent'); entered = threading.Event(); release = threading.Event()
        real_evaluate = evolve._evaluate_jobs; exceptions = []
        def held_evaluate(*args, **kwargs):
            entered.set()
            if not release.wait(10): raise AssertionError('writer test release timed out')
            return real_evaluate(*args, **kwargs)
        def first_writer():
            try: self.run_cfg(cfg)
            except BaseException as error: exceptions.append(error)
        with patch.object(evolve, '_evaluate_jobs', side_effect=held_evaluate):
            worker = threading.Thread(target=first_writer, name='choice-first-writer', daemon=True); worker.start()
            try:
                self.assertTrue(entered.wait(10), exceptions)
                with self.assertRaises((ValueError, RuntimeError, OSError)):
                    _evolve({**cfg, 'resume': True})
            finally:
                release.set(); worker.join(20)
        self.assertFalse(worker.is_alive()); self.assertEqual(exceptions, [])
        self.assertEqual(self.state(Path(cfg['out']))['completed_generations'], 1)
        self.assertEqual(len(self.state(Path(cfg['out']))['history'][0]), 2)


    def test_choice_codec_refuses_short_long_boolean_nonfinite_and_out_of_range_genes(self):
        import random
        original = ChoiceGenome.random(random.Random(0)).to_dict()
        variants = [original['choices'][:-1], original['choices'] + [0]]
        variants += [[value, *original['choices'][1:]] for value in
                     (True, False, -1, 2**32, 1.5, float('nan'), float('inf'))]
        for choices in variants:
            with self.subTest(length=len(choices), first=choices[0]):
                with self.assertRaises((ValueError, TypeError)):
                    ChoiceGenome.from_dict({**original, 'choices': choices})
        with self.assertRaises(ValueError):
            ChoiceGenome.from_dict({**original, 'traits': {}})

    def test_input_file_addition_removal_and_authored_contract_changes_are_rejected(self):
        cfg = self.cfg(); self.run_cfg(cfg)
        extra = self.project / 'additional.yaml'; extra.write_text('new: true\n')
        self.reject_preserving(cfg); extra.unlink()
        contract = next(p for p in self.project.rglob('*.yaml') if 'contract' in p.name)
        content = contract.read_bytes(); contract.unlink()
        self.reject_preserving(cfg); contract.write_bytes(content + b'\n# changed contract\n')
        self.reject_preserving(cfg); contract.write_bytes(content)

    def test_template_compatibility_guard_uses_real_bytes_without_mutating_source_checkout(self):
        input_repo = self.root / 'input-repo'
        for name in ('engine', 'gapengine', 'scripts', 'templates'):
            shutil.copytree(checkpoint.ROOT / name, input_repo / name,
                ignore=shutil.ignore_patterns('__pycache__'))
        cfg = self.cfg()
        with patch.object(checkpoint, 'ROOT', input_repo):
            self.run_cfg(cfg)
            template = next((input_repo / 'templates').rglob('*.yaml'))
            original = template.read_bytes(); template.write_bytes(original + b'\n# changed template\n')
            self.reject_preserving(cfg)

    def test_unowned_pending_or_extra_generation_refuses_destructive_cleanup(self):
        cfg = self.cfg(); self.run_cfg(cfg); out = Path(cfg['out'])
        pending = out / '.choice-pending'; pending.mkdir()
        unrelated = pending / 'user-notes.txt'; unrelated.write_bytes(b'never erase this')
        self.reject_preserving({**cfg, 'generations': 2})
        self.assertEqual(unrelated.read_bytes(), b'never erase this')
        shutil.rmtree(pending)
        generation = out / 'g1'; generation.mkdir()
        unrelated = generation / 'user-notes.txt'; unrelated.write_bytes(b'not our transaction')
        self.reject_preserving({**cfg, 'generations': 2})
        self.assertEqual(unrelated.read_bytes(), b'not our transaction')

    def test_symlinked_committed_artifact_is_rejected_even_if_bytes_match(self):
        cfg = self.cfg(); self.run_cfg(cfg); out = Path(cfg['out'])
        layers = out / 'g0/ind-0/seed-0/layers.jsonl'
        outside = self.root / 'outside-layers.jsonl'; outside.write_bytes(layers.read_bytes())
        original = layers.read_bytes(); layers.unlink()
        try:
            layers.symlink_to(outside)
        except OSError:
            layers.write_bytes(original)
            self.skipTest('Host cannot create symlinks without additional privileges')
        self.reject_preserving(cfg)
        self.assertTrue(layers.is_symlink()); self.assertTrue(outside.is_file())

    def test_choice_and_legacy_personality_checkpoints_refuse_each_others_family(self):
        from test_gapengine import make_reaching_project, TEMPLATE
        legacy_project = make_reaching_project(self.root / 'legacy-input')
        choice = self.cfg(); self.run_cfg(choice); choice_out = Path(choice['out'])
        legacy_common = {'project': legacy_project, 'template': TEMPLATE,
            'population': 2, 'generations': 1, 'ga_seed': 3, 'seeds': 1,
            'seed_base': 0, 'processes': 1, 'keep': 'all'}
        before = self.files(choice_out)
        with self.assertRaises((ValueError, RuntimeError)):
            self.run_cfg({**legacy_common, 'out': choice_out, 'resume': True})
        self.assertEqual(self.files(choice_out), before)
        legacy = {**legacy_common, 'out': self.root / 'legacy-output'}
        self.run_cfg(legacy)
        self.reject_preserving({**choice, 'out': str(legacy['out'])})


    def test_partial_generation_evaluation_is_discarded_without_duplicate_seed_logs(self):
        from gapengine import evolve
        cfg = self.cfg('partial'); out = Path(cfg['out'])
        def partial_evaluate(jobs, processes, observer=None):
            evolve.run_individual(jobs[0])
            raise InjectedCrash('after first candidate only')
        with patch.object(evolve, '_evaluate_jobs', side_effect=partial_evaluate):
            with self.assertRaises(InjectedCrash): self.run_cfg(cfg)
        self.assertEqual(self.state(out)['completed_generations'], 0)
        self.assertTrue((out / '.choice-pending/g0/ind-0/seed-0/layers.jsonl').is_file())
        self.assertFalse((out / 'g0').exists())
        self.run_cfg({**cfg, 'resume': True}); clean = self.cfg('clean'); self.run_cfg(clean)
        self.assertEqual(self.stable_files(out), self.stable_files(Path(clean['out'])))
        logs = (out / 'evaluations.jsonl').read_text().splitlines()
        self.assertEqual(len(logs), 2)

    def test_generation_rename_failure_retains_checkpoint_and_restarts_own_stage(self):
        cfg = self.cfg('rename'); out = Path(cfg['out'])
        real_replace = checkpoint.os.replace
        def failed_rename(source, destination):
            if Path(source) == out / '.choice-pending/g0' and Path(destination) == out / 'g0':
                raise OSError('injected generation rename failure')
            return real_replace(source, destination)
        with patch.object(checkpoint.os, 'replace', side_effect=failed_rename):
            with self.assertRaises(OSError): self.run_cfg(cfg)
        self.assertEqual(self.state(out)['completed_generations'], 0)
        self.assertFalse((out / 'g0').exists())
        self.assertTrue((out / '.choice-pending/g0').is_dir())
        self.run_cfg({**cfg, 'resume': True}); clean = self.cfg('clean'); self.run_cfg(clean)
        self.assertEqual(self.stable_files(out), self.stable_files(Path(clean['out'])))

    def test_committed_checkpoint_before_exports_recovers_without_reevaluation(self):
        cfg = self.cfg('postcommit'); out = Path(cfg['out']); real_save = checkpoint.save
        def committed_crash(directory, state):
            result = real_save(directory, state)
            if state['completed_generations'] == 1:
                raise InjectedCrash('checkpoint marker committed, exports not yet written')
            return result
        with patch.object(checkpoint, 'save', side_effect=committed_crash):
            with self.assertRaises(InjectedCrash): self.run_cfg(cfg)
        self.assertEqual(self.state(out)['completed_generations'], 1)
        self.assertTrue((out / 'g0').is_dir())
        self.assertEqual({p.name for p in (out / '.choice-pending').iterdir()}, {'transaction.json'})
        self.assertFalse((out / 'summary.json').exists())
        with patch('gapengine.evolve._evaluate_jobs', side_effect=AssertionError('already committed')):
            _evolve({**cfg, 'resume': True})
        self.assertFalse((out / '.choice-pending').exists())
        clean = self.cfg('clean'); self.run_cfg(clean)
        self.assertEqual(self.stable_files(out), self.stable_files(Path(clean['out'])))


    def test_committed_marker_survives_stage_cleanup_failure_without_reevaluation(self):
        cfg = self.cfg('cleanup-failure'); out = Path(cfg['out'])
        real_remove = checkpoint.shutil.rmtree
        def failed_cleanup(path, *args, **kwargs):
            if Path(path) == out / '.choice-pending':
                raise OSError('injected stage cleanup failure')
            return real_remove(path, *args, **kwargs)
        with patch.object(checkpoint.shutil, 'rmtree', side_effect=failed_cleanup):
            with self.assertRaises(OSError): self.run_cfg(cfg)
        self.assertEqual(self.state(out)['completed_generations'], 1)
        self.assertTrue((out / '.choice-pending/transaction.json').is_file())
        with patch('gapengine.evolve._evaluate_jobs', side_effect=AssertionError('already committed')):
            _evolve({**cfg, 'resume': True})
        clean = self.cfg('clean'); self.run_cfg(clean)
        self.assertEqual(self.stable_files(out), self.stable_files(Path(clean['out'])))

    def test_exports_failure_does_not_roll_back_committed_generation(self):
        cfg = self.cfg('exports-failure'); out = Path(cfg['out'])
        real_write = checkpoint.atomic_write
        def failed_export(path, data):
            if Path(path) == out / 'summary.json':
                raise OSError('injected derived export write failure')
            return real_write(path, data)
        with patch.object(checkpoint, 'atomic_write', side_effect=failed_export):
            with self.assertRaises(OSError): self.run_cfg(cfg)
        self.assertEqual(self.state(out)['completed_generations'], 1)
        committed = {n: raw for n, raw in self.files(out).items() if n.startswith('g0/')}
        with patch('gapengine.evolve._evaluate_jobs', side_effect=AssertionError('export recovery cannot evaluate')):
            _evolve({**cfg, 'resume': True})
        self.assertEqual(committed, {n: raw for n, raw in self.files(out).items() if n.startswith('g0/')})
        clean = self.cfg('clean'); self.run_cfg(clean)
        self.assertEqual(self.stable_files(out), self.stable_files(Path(clean['out'])))

    def test_disk_fsync_and_partial_stream_write_failure_preserve_existing_checkpoint(self):
        cfg = self.cfg(); self.run_cfg(cfg); out = Path(cfg['out'])
        path = out / 'ga_state.json'; before = path.read_bytes()
        replacement = checkpoint.encode({**self.state(out), 'proposed': 'new bytes'})
        with patch.object(checkpoint.os, 'fsync', side_effect=OSError('injected disk fsync failure')):
            with self.assertRaises(OSError): checkpoint.atomic_write(path, replacement)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(list(out.glob('.ga_state.json-*')))
        real_open = Path.open
        class PartialWriter:
            def __init__(self, stream, mode): self.stream, self.mode = stream, mode
            def __enter__(self): self.stream.__enter__(); return self
            def __exit__(self, *args): return self.stream.__exit__(*args)
            def write(self, data):
                if self.mode == 'write':
                    self.stream.write(data[:len(data)//2]); self.stream.flush()
                    raise OSError('injected partial write / disk full')
                return self.stream.write(data)
            def flush(self):
                if self.mode == 'flush': raise OSError('injected stream flush failure')
                return self.stream.flush()
            def __getattr__(self, name): return getattr(self.stream, name)
        def failed_open(candidate, *args, **kwargs):
            stream = real_open(candidate, *args, **kwargs)
            if candidate.parent == out and candidate.name.startswith('.ga_state.json-'):
                return PartialWriter(stream, failure_mode)
            return stream
        for failure_mode in ('write', 'flush'):
            with self.subTest(failure_mode=failure_mode), patch.object(Path, 'open', failed_open):
                with self.assertRaises(OSError): checkpoint.atomic_write(path, replacement)
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse(list(out.glob('.ga_state.json-*')))
        with patch('gapengine.evolve._evaluate_jobs', side_effect=AssertionError('old checkpoint valid')):
            _evolve({**cfg, 'resume': True})

    def test_source_file_change_and_added_source_are_strictly_guarded(self):
        input_repo = self.root / 'source-repo'
        for name in ('engine', 'gapengine', 'scripts', 'templates'):
            shutil.copytree(checkpoint.ROOT / name, input_repo / name,
                ignore=shutil.ignore_patterns('__pycache__'))
        cfg = self.cfg()
        with patch.object(checkpoint, 'ROOT', input_repo):
            self.run_cfg(cfg)
            source = input_repo / 'gapengine/festival_choices.py'
            original = source.read_bytes(); source.write_bytes(original + b'\n# changed source\n')
            self.reject_preserving(cfg); source.write_bytes(original)
            extra = input_repo / 'engine/new_module.py'; extra.write_text('# added source\n')
            self.reject_preserving(cfg)

    def test_actual_second_process_writer_is_refused_and_lock_releases_for_resume(self):
        cfg = self.cfg('process-lock'); self.run_cfg(cfg); out = Path(cfg['out']); before = self.files(out)
        command = [sys.executable, str(checkpoint.ROOT / 'scripts/cultural_festival_ga.py'),
            '--out', str(out), '--resume', '--population', '2', '--generations', '1', '--ga-seed', '3']
        with checkpoint.writer_lock(out):
            result = subprocess.run(command, cwd=checkpoint.ROOT, capture_output=True, text=True, timeout=15)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('active writer', result.stderr)
            self.assertEqual(self.files(out), before)
        result = subprocess.run(command, cwd=checkpoint.ROOT, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.files(out), before)


if __name__ == '__main__':
    unittest.main()
