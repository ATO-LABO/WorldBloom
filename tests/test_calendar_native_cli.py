"""Explicit generic native CLI, captured choices and retained-owner boundaries."""
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from engine.calendar_capture import open_calendar_capture
from engine.calendar_checkpoint import compatibility
from engine.calendar_fs import PosixProjectDirectory, directory_ancestry
from engine.calendar_generation import GenerationRef, SelectedGeneration, leaf_for
from engine.calendar_generation_runtime import GenerationProgress
from engine.calendar_parent_owner import _ExistingParentOwner
from execution import calendar_native as native
from scripts import calendar_sim

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / 'projects/calendar_workshop_example'
SUPPORTED = sys.platform == 'linux' or os.name == 'nt'


@unittest.skipUnless(SUPPORTED, 'native Linux or Windows required')
class NativeCalendarCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='wb-native-cli-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / 'inputs'
        shutil.copytree(EXAMPLE, self.project)
        self.world = self.project / 'world.yaml'
        self.choices = self.project / 'choices.json'
        self.output = self.root / 'output'; self.output.mkdir()

    def run_native(self, **kwargs):
        return native.run_native_calendar(self.world, self.choices, self.output, **kwargs)

    def invoke(self, *options):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', ['calendar_sim.py', '--storage', 'native',
                '--world', str(self.world), '--choices', str(self.choices), '--out', str(self.output),
                *map(str, options)]), redirect_stdout(stdout), redirect_stderr(stderr):
            code = calendar_sim.main()
        return code, stdout.getvalue(), stderr.getvalue()

    def selection(self, result):
        selected = result['selected_generation']
        return SelectedGeneration(selected['leaf'], GenerationRef(**selected['expected']))

    def artifact(self, result, name):
        ref = result['artifacts'][name]
        data = (self.output / ref['leaf']).read_bytes()
        self.assertEqual(ref['size'], len(data))
        self.assertEqual(ref['sha256'], hashlib.sha256(data).hexdigest())
        return data

    def test_cli_happy_path_returns_exact_selection_and_all_publication_refs(self):
        code, stdout, stderr = self.invoke('--until-minute', 1442)
        self.assertEqual(code, 0, stderr)
        result = json.loads(stdout)
        self.assertEqual(result['status'], 'checkpointed')
        self.assertEqual(result['api_calls'], 0); self.assertIs(result['gpu'], False)
        selected = self.selection(result)
        data = (self.output / selected.leaf).read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), selected.expected.sha256)
        self.assertEqual(len(data), selected.expected.size)
        for role in ('layers', 'result', 'run_record'): self.artifact(result, role)
        record = json.loads(self.artifact(result, 'run_record'))
        self.assertEqual(record['storage']['selected_generation'], result['selected_generation'])
        self.assertEqual(record['stop_reason'], 'paused')
        self.assertEqual(record['result']['kind'], 'calendar_summary')
        self.assertIsNone(record['verified_execution']); self.assertIsNone(record['failure_evidence'])
        self.assertFalse((self.output/'calendar-state.json').exists())
        self.assertFalse((self.output/'layers.jsonl').exists())

    def test_split_resume_full_noop_equivalence_and_immutable_old_files(self):
        full = self.run_native()
        paused = self.run_native(until=1442)
        before = {p.name: p.read_bytes() for p in self.output.iterdir()}
        resumed = self.run_native(selection=self.selection(paused))
        self.assertEqual(resumed['status'], 'completed')
        for role in ('layers', 'result'):
            self.assertEqual(self.artifact(full, role), self.artifact(resumed, role))
        for name, data in before.items(): self.assertEqual(data, (self.output/name).read_bytes())
        before = {p.name for p in self.output.glob('wbgen-*')}
        noop = self.run_native(selection=self.selection(resumed))
        self.assertFalse(noop['generation_saved'])
        self.assertEqual(noop['selected_generation'], resumed['selected_generation'])
        self.assertEqual(before, {p.name for p in self.output.glob('wbgen-*')})
        self.assertNotEqual(noop['publication_id'], resumed['publication_id'])
        self.assertEqual(self.artifact(noop, 'layers'), self.artifact(full, 'layers'))
        complete = json.loads(self.artifact(noop, 'run_record'))
        self.assertEqual(complete['stop_reason'], 'observation_limit')
        self.assertIsNone(complete['actual_terminal'])
        self.assertEqual(complete['observations'], {})

    def test_explicit_cli_restore_uses_four_fields_and_derived_leaf(self):
        paused = self.run_native(until=1442)
        ref = paused['selected_generation']['expected']
        options = ['--resume']
        for key in ('sha256', 'size', 'stream_id', 'ordinal'):
            options.extend(['--expected-' + key.replace('_', '-'), ref[key]])
        code, out, err = self.invoke(*options)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)['status'], 'completed')

    def test_invalid_independent_refs_are_rejected_before_all_route_io(self):
        valid = dict(sha256='a'*64, size=2048, stream_id='b'*32, ordinal=0)
        cases = [dict(resume=True), dict(resume=False, **valid)]
        for missing in valid:
            cases.append(dict(resume=True, **{k:v for k,v in valid.items() if k!=missing}))
        for key, bad in [('sha256','A'*64),('size',True),('size',0),('size',10**20),
                         ('stream_id','../x'),('ordinal',-1),('ordinal',True)]:
            cases.append(dict(resume=True, **{**valid,key:bad}))
        with patch.object(native, 'run_native_calendar', side_effect=AssertionError('no route I/O')):
            for case in cases:
                with self.subTest(case=case), self.assertRaises(ValueError):
                    native.selected_from_values(**case)
            code, out, err = self.invoke('--resume')
            self.assertEqual(code, 2); self.assertEqual(out, '')
            self.assertIsNone(json.loads(err)['selected_generation'])
        self.assertEqual(list(self.output.iterdir()), [])

    def test_bare_selection_filename_or_mapping_is_not_authority(self):
        with patch.object(native, 'capture_choices', side_effect=AssertionError('no input I/O')):
            for selected in ('wbgen-fake.wbg', {}, 'latest'):
                with self.subTest(selected=selected), self.assertRaises(ValueError):
                    self.run_native(selection=selected)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_resume_ref_mismatch_never_runs_or_writes(self):
        paused = self.run_native(until=1442); selected = self.selection(paused)
        before = {p.name:p.read_bytes() for p in self.output.iterdir()}
        wrong = GenerationRef('0'*64, selected.expected.size, selected.expected.stream_id,
                              selected.expected.ordinal)
        progress = GenerationProgress()
        with patch('engine.time_kernel.TimeActionKernel.run', side_effect=AssertionError('callback')):
            with self.assertRaises((ValueError, OSError)):
                self.run_native(selection=SelectedGeneration(leaf_for(wrong), wrong), progress=progress)
        self.assertIsNone(progress.selection)
        self.assertEqual(before, {p.name:p.read_bytes() for p in self.output.iterdir()})

    def test_choices_after_capture_replacement_is_ignored_without_reopen(self):
        captured = native.capture_choices(self.choices)
        original = captured.data
        replacement = self.project/'replacement.json'; replacement.write_text('[]')
        os.replace(replacement, self.choices)
        self.assertEqual(captured.data, original)
        with patch.object(Path,'read_bytes',side_effect=AssertionError('path reopen')):
            self.assertEqual(len(captured.commands()), 5)

    def test_choices_link_hardlink_oversize_invalid_json_rejected_before_output(self):
        original = self.choices.read_bytes()
        original_path = self.project/'original.json'; original_path.write_bytes(original)
        self.choices.unlink()
        variants = ('symlink','hardlink','oversize','duplicate','nonlist','unicode')
        for kind in variants:
            with self.subTest(kind=kind):
                if kind == 'symlink':
                    try: self.choices.symlink_to(original_path)
                    except OSError as error: self.skipTest('symlink privilege unavailable: '+str(error))
                elif kind == 'hardlink': os.link(original_path, self.choices)
                elif kind == 'oversize': self.choices.write_bytes(b' '*(native.MAX_CHOICES_BYTES+1))
                elif kind == 'duplicate': self.choices.write_text('[{"id":"a","id":"b"}]')
                elif kind == 'unicode': self.choices.write_bytes(b'\xff')
                else: self.choices.write_text('{}')
                with patch.object(native, '_ExistingParentOwner', side_effect=AssertionError('output must not open')):
                    with self.assertRaises((ValueError, OSError)): self.run_native()
                self.choices.unlink()
        self.assertEqual(list(self.output.iterdir()), [])

    @unittest.skipUnless(sys.platform == 'linux', 'POSIX deterministic handle race fixture')
    def test_choices_label_replaced_after_open_reads_retained_file(self):
        from engine import calendar_fs
        original = self.choices.read_bytes(); real = calendar_fs._open
        replacement = self.project/'replacement'; replacement.write_text('[]')
        def opening(name, flags, **kwargs):
            fd = real(name, flags, **kwargs)
            if name == 'choices.json':
                self.choices.rename(self.project/'retained-old-choices')
                os.replace(replacement, self.choices)
            return fd
        with patch.object(calendar_fs, '_open', side_effect=opening):
            captured = native.capture_choices(self.choices)
        self.assertEqual(captured.data, original)

    @unittest.skipUnless(sys.platform == 'linux', 'POSIX deterministic mutation fixture')
    def test_choices_inplace_mutation_during_read_rejected(self):
        real = os.read; changed = False
        def reading(fd, size):
            nonlocal changed
            data = real(fd, size)
            if not changed:
                changed = True; self.choices.write_text('[]')
            return data
        with patch('engine.calendar_fs.os.read', side_effect=reading):
            with self.assertRaisesRegex(ValueError,'changed'):
                native.capture_choices(self.choices)

    def test_removed_input_labels_after_capture_do_not_affect_run_or_projection(self):
        real = native.run_calendar_generation
        def removing(*args, **kwargs):
            self.project.rename(self.root/'removed-input-label')
            return real(*args, **kwargs)
        with patch.object(native, 'run_calendar_generation', side_effect=removing):
            result = self.run_native(until=1442)
        self.assertEqual(result['status'], 'checkpointed')
        self.assertFalse(self.project.exists())

    def test_native_route_uses_no_legacy_writer_or_output_path_read(self):
        original = Path.open
        def opening(path, *args, **kwargs):
            if path == self.output or self.output in path.parents:
                raise AssertionError('output path reopened')
            return original(path, *args, **kwargs)
        with ExitStack() as stack:
            for target in ('engine.calendar_runtime.CheckpointStore', 'engine.calendar_runtime.atomic_bytes',
                           'engine.run_record.commit_run_record', 'engine.run_record._saved_projection'):
                stack.enter_context(patch(target, side_effect=AssertionError('legacy')))
            stack.enter_context(patch.object(Path, 'open', opening))
            stack.enter_context(patch.object(Path, 'iterdir', side_effect=AssertionError('scan')))
            result = self.run_native(until=1442)
        self.assertEqual(result['status'], 'checkpointed')

    def test_native_output_requires_existing_directory_and_rejects_lexical_aliases(self):
        for output in (self.project, self.project/'new', self.root, ROOT,
                       self.output/'..'/'output', self.root/'not-created'):
            with self.subTest(output=str(output)), self.assertRaises((ValueError, OSError)):
                native.run_native_calendar(self.world,self.choices,output,until=1442)
        self.assertFalse((self.root/'not-created').exists())
        self.assertEqual(list(self.output.iterdir()), [])

    @unittest.skipUnless(sys.platform == 'linux', 'POSIX symlink/ancestor fixture')
    def test_output_link_and_symlinked_ancestor_rejected(self):
        link = self.root/'out-link'; link.symlink_to(self.output, target_is_directory=True)
        ancestor = self.root/'ancestor'; ancestor.symlink_to(self.root, target_is_directory=True)
        for output in (link, ancestor/'output'):
            with self.subTest(output=str(output)), self.assertRaises((ValueError,OSError)):
                native.run_native_calendar(self.world,self.choices,output,until=1442)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_native_identity_policy_rejects_alias_and_ancestor_despite_names(self):
        for protected in (((1,2),(1,3)), ((1,2),(1,3),(1,4)), ((1,2),)):
            with self.subTest(protected=protected), self.assertRaises(ValueError):
                native._separate_ancestry(((1,2),(1,3)), protected)
        native._separate_ancestry(((1,2),(1,3)), ((1,2),(1,4)))

    def test_one_owner_retained_through_generation_capture_and_exports(self):
        owners=[]; real_owner=native._ExistingParentOwner; real_publish=native.publish_native_exports
        real_capture=native._captured_generation
        def opening(*args,**kwargs):
            owner=real_owner(*args,**kwargs);owners.append(owner);return owner
        def capture(owner,*args,**kwargs):
            self.assertIs(owner,owners[0]);return real_capture(owner,*args,**kwargs)
        def publish(owner,*args,**kwargs):
            self.assertIs(owner,owners[0]);return real_publish(owner,*args,**kwargs)
        with patch.object(native,'_ExistingParentOwner',side_effect=opening), \
             patch.object(native,'_captured_generation',side_effect=capture), \
             patch.object(native,'publish_native_exports',side_effect=publish):
            result=self.run_native(until=1442)
        self.assertEqual(len(owners),1);self.assertEqual(owners[0].state,'CLOSED')
        self.assertEqual(owners[0].active_borrows,0)
        self.assertEqual(result['readback'],'same_owner_post_close_exact_bytes')

    def test_export_failure_reports_valid_ref_without_retry_and_can_restore(self):
        with patch.object(native,'publish_native_exports',side_effect=OSError('uncertain export')) as call:
            code,out,err=self.invoke('--until-minute',1442)
        self.assertEqual(code,2);self.assertEqual(out,'');self.assertEqual(call.call_count,1)
        failure=json.loads(err)
        self.assertFalse(failure['publication_accepted'])
        selected=self.selection(failure);self.assertTrue((self.output/selected.leaf).is_file())
        result=self.run_native(selection=selected)
        self.assertEqual(result['status'],'completed')

    def test_initial_native_write_failure_never_infers_absence_or_a_ref(self):
        with patch('engine.calendar_run_store.NativeWriterPort.open',side_effect=OSError('uncertain initial')) as call:
            code,out,err=self.invoke('--until-minute',1442)
        self.assertEqual(code,2);self.assertEqual(out,'');self.assertEqual(call.call_count,1)
        failure=json.loads(err);self.assertIsNone(failure['selected_generation'])
        self.assertIn('unknown',failure['failed_attempt'])

    def test_cancellation_after_save_reports_ref_and_closes_ownership(self):
        owners=[];real=native._ExistingParentOwner
        def opening(*args,**kwargs):
            owner=real(*args,**kwargs);owners.append(owner);return owner
        with patch.object(native,'_ExistingParentOwner',side_effect=opening), \
             patch.object(native,'publish_native_exports',side_effect=KeyboardInterrupt('cancelled')):
            code,out,err=self.invoke('--until-minute',1442)
        self.assertEqual(code,130);self.assertEqual(out,'')
        failure=json.loads(err);self.assertEqual(failure['status'],'cancelled')
        self.assertIsNotNone(failure['selected_generation'])
        self.assertEqual(owners[0].state,'CLOSED');self.assertEqual(owners[0].active_borrows,0)

    def test_transition_write_failure_reports_last_confirmed_not_attempted_ref(self):
        from engine.calendar_run_store import NativeWriterPort
        original=NativeWriterPort.open;calls=[]
        def failing(port,owner,leaf,**kwargs):
            calls.append(leaf)
            if len(calls)==2: raise OSError('uncertain transition')
            return original(port,owner,leaf,**kwargs)
        with patch.object(NativeWriterPort,'open',failing):
            code,out,err=self.invoke('--until-minute',1442)
        self.assertEqual(code,2);self.assertEqual(out,'');self.assertEqual(len(calls),2)
        failure=json.loads(err);selected=self.selection(failure)
        self.assertEqual(selected.leaf,calls[0]);self.assertNotEqual(selected.leaf,calls[1])
        self.assertEqual(self.run_native(selection=selected)['status'],'completed')

    def test_parent_close_failure_prevents_success_but_keeps_accepted_selection(self):
        original=_ExistingParentOwner.close
        def closing(owner):
            was_open=owner.state=='OPEN';result=original(owner)
            if was_open: raise OSError('parent release reported uncertain')
            return result
        with patch.object(_ExistingParentOwner,'close',closing):
            code,out,err=self.invoke('--until-minute',1442)
        self.assertEqual(code,2);self.assertEqual(out,'')
        self.assertIsNotNone(json.loads(err)['selected_generation'])
        self.assertFalse(json.loads(err)['publication_accepted'])

    def test_planned_choices_fresh_projection_and_resume_match_full(self):
        from test_calendar_goal_planning import project
        planned=self.root/'planned';project(planned)
        self.world=planned/'world.yaml';self.choices=planned/'choices.json'
        full=self.run_native();paused=self.run_native(until=510)
        resumed=self.run_native(selection=self.selection(paused))
        for role in ('layers','result'):
            self.assertEqual(self.artifact(full,role),self.artifact(resumed,role))

    def test_reused_progress_cannot_publish_again(self):
        progress=GenerationProgress();self.run_native(until=1442,progress=progress)
        selected=progress.selection;before={p.name for p in self.output.iterdir()}
        with self.assertRaisesRegex(ValueError,'fresh per-call'):
            self.run_native(until=1442,progress=progress)
        self.assertEqual(progress.selection,selected)
        self.assertEqual(before,{p.name for p in self.output.iterdir()})

    def test_legacy_cli_and_freeze_remain_explicitly_separate(self):
        def invoke(*args):
            return subprocess.run([sys.executable,'-B',str(ROOT/'scripts/calendar_sim.py'),
                '--world',str(self.world),*map(str,args)],capture_output=True,text=True,timeout=30)
        legacy=invoke('--choices',self.choices,'--out',self.root/'legacy')
        self.assertEqual(legacy.returncode,0,legacy.stderr)
        self.assertTrue((self.root/'legacy/calendar-state.json').is_file())
        frozen=invoke('--freeze-to',self.root/'frozen')
        self.assertEqual(frozen.returncode,0,frozen.stderr)
        self.assertEqual(json.loads(frozen.stdout)['status'],'frozen_only')
        rejected=invoke('--storage','native','--freeze-to',self.root/'bad-frozen')
        self.assertEqual(rejected.returncode,2)
        self.assertFalse((self.root/'bad-frozen').exists())
        rejected=invoke('--expected-sha256','a'*64,'--choices',self.choices,'--out',self.root/'bad-legacy')
        self.assertEqual(rejected.returncode,2)
        self.assertFalse((self.root/'bad-legacy').exists())


if __name__ == '__main__': unittest.main()
