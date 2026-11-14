"""Semantic projection and admission-observation tests for generic native saves."""
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from engine.calendar_capture import open_calendar_capture
from engine.calendar_checkpoint import compatibility
from engine.calendar_fs import directory_ancestry, PosixProjectDirectory
from engine.calendar_generation import (GenerationRef, SelectedGeneration, decode_generation,
                                        checkpoint_bytes, prepare_generation, leaf_for)
from engine.calendar_generation_record import project_calendar_generation_record
from engine.calendar_generation_runtime import run_calendar_generation, GenerationProgress
from engine.calendar_native_exports import publish_native_exports
from engine.calendar_parent_owner import _ExistingParentOwner, _ParentBorrow
from engine.calendar_run_store import RunStore, NativeWriterPort
from engine.calendar_generation_capture import NativeGenerationCapture
from engine.execution_plan import digest
from execution import calendar_native as native
from execution.provenance import canonical

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT/'projects/calendar_workshop_example'


@unittest.skipUnless(sys.platform=='linux' or os.name=='nt','native Linux or Windows required')
class GenericGenerationRecordTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        root=self.stack.enter_context(tempfile.TemporaryDirectory(prefix='wb-generic-record-'))
        self.root=Path(root);self.project=self.root/'input';shutil.copytree(EXAMPLE,self.project)
        self.out=self.root/'out';self.out.mkdir()
        self.capture=self.stack.enter_context(open_calendar_capture(self.project/'world.yaml',capture_ancestry=True))
        self.factory=native.CapturedCalendarFactory(self.capture,native.capture_choices(self.project/'choices.json'))
        self.owner=self.stack.enter_context(_ExistingParentOwner(self.out,backend='windows' if os.name=='nt' else 'linux'))

    def saved(self,until=1442):
        rt=self.factory.create();execution=run_calendar_generation(rt,self.owner,until=until)
        data=native._captured_generation(self.owner,execution.selection)
        return rt,execution,data

    def test_projection_is_immutable_generation_bound_and_has_consistent_hashes(self):
        rt,execution,data=self.saved();before=canonical(rt.export_state())
        prepared=project_calendar_generation_record(execution.selection,data,self.factory)
        self.assertEqual(prepared.ledger,execution.layers);self.assertEqual(prepared.result,execution.summary)
        record=json.loads(prepared.run_record);self.assertEqual(record.pop('saved_revision_hash'),digest(record))
        generation=decode_generation(data,expected=execution.selection.expected,
            expected_compatibility=compatibility(rt.inputs(),rt.commands))
        self.assertEqual(record['checkpoint_sha256'],hashlib.sha256(generation.checkpoint).hexdigest())
        self.assertEqual(record['runtime_state_sha256'],digest(generation.state(compatibility(rt.inputs(),rt.commands))))
        self.assertEqual(record['generation_sha256'],hashlib.sha256(data).hexdigest())
        self.assertEqual(record['choices_sha256'],self.factory.choices.sha256)
        self.assertEqual(record['result_sha256'],hashlib.sha256(prepared.result).hexdigest())
        self.assertEqual(record['descriptor_scope'],'generic_calendar_native_generation_v1')
        self.assertEqual(record['storage']['kind'],'generic_calendar_native_generation_exports_v1')
        self.assertEqual(before,canonical(rt.export_state()))
        with self.assertRaises((AttributeError,TypeError)): prepared.ledger=b'changed'

    def test_projection_never_runs_callbacks_or_opens_saved_paths(self):
        _rt,execution,data=self.saved()
        with patch('engine.time_kernel.TimeActionKernel.run',side_effect=AssertionError('callback')), \
             patch('engine.run_record._saved_projection',side_effect=AssertionError('legacy')):
            prepared=project_calendar_generation_record(execution.selection,data,self.factory)
        self.assertEqual(prepared.ledger,execution.layers)

    def test_corrupt_bytes_and_wrong_independent_fields_rejected(self):
        _rt,execution,data=self.saved();ref=execution.selection.expected
        for field,value in [('sha256','0'*64),('size',ref.size+1),('stream_id','a'*32),('ordinal',ref.ordinal+1)]:
            wrong=replace(ref,**{field:value})
            with self.subTest(field=field),self.assertRaises(ValueError):
                project_calendar_generation_record(SelectedGeneration(leaf_for(wrong),wrong),data,self.factory)
        with self.assertRaises(ValueError):
            project_calendar_generation_record(execution.selection,data[:-1]+bytes([data[-1]^1]),self.factory)

    def test_rehashed_semantically_invalid_generation_rejected_before_exports(self):
        rt,execution,data=self.saved();expected=compatibility(rt.inputs(),rt.commands)
        generation=decode_generation(data,expected=execution.selection.expected,expected_compatibility=expected)
        committed=generation.state(expected)
        mutations=[('now',-1),('done',True),('processed',[1]),('busy',{'Unknown':{}})]
        for key,value in mutations:
            bad=deepcopy(committed);bad[key]=value
            generated=prepare_generation(checkpoint_bytes(bad,expected),expected_compatibility=expected,
                stream_id='f'*32,ordinal=0)
            with self.subTest(key=key), \
                 patch('engine.calendar_runtime.CalendarRuntime._layers_bytes',side_effect=AssertionError('premature export')), \
                 patch('engine.time_kernel.TimeActionKernel.run',side_effect=AssertionError('callback')):
                with self.assertRaises(ValueError):
                    project_calendar_generation_record(generated.selection(),generated.data,self.factory)

    def test_changed_captured_choices_even_equivalent_json_cannot_rebind_old_generation(self):
        _rt,execution,data=self.saved()
        equivalent=replace(self.factory.choices,data=b' '+self.factory.choices.data)
        other=native.CapturedCalendarFactory(self.capture,equivalent)
        with self.assertRaisesRegex(ValueError,'compatibility'):
            project_calendar_generation_record(execution.selection,data,other)

    def test_removed_input_names_do_not_change_projection(self):
        _rt,execution,data=self.saved()
        with ExitStack() as guard:
            if os.name == 'nt':
                # Retained Windows ancestry deliberately prevents renames. Prove
                # projection uses captured bytes without weakening that policy.
                import builtins
                import io
                from engine.calendar_windows_io import WindowsProjectDirectory
                source = os.path.normcase(os.path.abspath(self.project))
                hits = []
                def reject_input(path):
                    if isinstance(path, (str, bytes, os.PathLike)):
                        name = os.path.normcase(os.path.abspath(os.fsdecode(path)))
                        if name == source or name.startswith(source + os.sep):
                            hits.append(name)
                            raise AssertionError('input name reopened')
                real_init = WindowsProjectDirectory.__init__
                def opening_directory(directory, path, **kwargs):
                    reject_input(path)
                    return real_init(directory, path, **kwargs)
                guard.enter_context(patch.object(WindowsProjectDirectory, '__init__', opening_directory))
                for module, name in (
                        (builtins, 'open'), (io, 'open'), (os, 'open'),
                        (os, 'stat'), (os, 'lstat'), (os, 'listdir'),
                        (os, 'scandir'), (os, 'access'), (os, 'readlink'),
                        (os.path, 'realpath'), (Path, 'resolve'),
                        (Path, 'glob'), (Path, 'rglob'),
                        (Path, 'walk'), (os, 'walk')):
                    real_call = getattr(module, name)
                    def accessing(path='.', *args, _call=real_call, **kwargs):
                        reject_input(path)
                        return _call(path, *args, **kwargs)
                    guard.enter_context(patch.object(module, name, accessing))
                # Include metadata/existence branches, not just content reads.
                world = self.project/'world.yaml'
                probes = (
                    lambda: builtins.open(world, 'rb'), lambda: io.open(world, 'rb'),
                    lambda: world.read_bytes(), lambda: os.open(world, os.O_RDONLY),
                    lambda: open_calendar_capture(world),
                    lambda: native.capture_choices(self.project/'choices.json'),
                    lambda: world.stat(), lambda: world.lstat(),
                    lambda: world.exists(), lambda: world.is_file(),
                    lambda: self.project.is_dir(), lambda: world.resolve(),
                    lambda: list(self.project.iterdir()),
                    lambda: list(self.project.glob('*.yaml')),
                    lambda: list(self.project.rglob('*.yaml')),
                    lambda: list(self.project.walk()), lambda: list(os.walk(self.project)),
                    lambda: os.stat(world), lambda: os.lstat(world),
                    lambda: os.listdir(self.project), lambda: os.scandir(self.project),
                    lambda: os.access(world, os.R_OK), lambda: os.readlink(world),
                    lambda: os.path.realpath(world))
                for index, reading in enumerate(probes):
                    with self.subTest(guard_probe=index), self.assertRaisesRegex(
                            AssertionError, 'input name reopened'):
                        reading()
                self.assertEqual(len(hits), len(probes))
                hits.clear()
            else:
                self.project.rename(self.root/'renamed')
            prepared=project_calendar_generation_record(execution.selection,data,self.factory)
            if os.name == 'nt':
                self.assertEqual(hits, [])
        self.assertEqual(prepared.ledger,execution.layers)

    def test_closed_capture_rejects_projection(self):
        _rt,execution,data=self.saved();self.capture.close()
        with self.assertRaisesRegex(ValueError,'closed'):
            project_calendar_generation_record(execution.selection,data,self.factory)

    def test_actual_observation_values_are_not_invented_or_discarded(self):
        import yaml
        self.capture.close()
        path=self.project/'contract.yaml';contract=yaml.safe_load(path.read_text())
        contract['observers']=[{'id':'audit','event_id':'new-shift','verb':'workshop_audit',
                              'fields':{'completed':{'check':'task_complete','task_id':'pieces'}}}]
        path.write_text(yaml.safe_dump(contract))
        capture=self.stack.enter_context(open_calendar_capture(self.project/'world.yaml'))
        factory=native.CapturedCalendarFactory(capture,native.capture_choices(self.project/'choices.json'))
        rt=factory.create();execution=run_calendar_generation(rt,self.owner,until=1442)
        prepared=project_calendar_generation_record(execution.selection,native._captured_generation(self.owner,execution.selection),factory)
        record=json.loads(prepared.run_record)
        self.assertEqual(record['observations'],{'audit':{'at':1441,'completed':False}})
        self.assertEqual(record['observations'],json.loads(prepared.result)['observations'])
        self.assertIsNone(record['actual_terminal']);self.assertIsNone(record['failure_evidence'])

    def test_observer_labels_never_invent_terminal_or_target_semantics(self):
        import yaml
        for label in ('ending','aborted','time_limit','observation_finished','target_observed'):
            project=self.root/('observer-'+label);shutil.copytree(EXAMPLE,project)
            path=project/'contract.yaml';contract=yaml.safe_load(path.read_text())
            contract['observers']=[{'id':'ordinary-audit','event_id':'new-shift','verb':label,
                                  'fields':{'completed':{'check':'task_complete','task_id':'pieces'}}}]
            path.write_text(yaml.safe_dump(contract))
            with open_calendar_capture(project/'world.yaml') as capture:
                factory=native.CapturedCalendarFactory(capture,native.capture_choices(project/'choices.json'))
                for until in (1442,None):
                    with self.subTest(label=label,until=until):
                        rt=factory.create();execution=run_calendar_generation(rt,self.owner,until=until)
                        prepared=project_calendar_generation_record(execution.selection,
                            native._captured_generation(self.owner,execution.selection),factory)
                        record=json.loads(prepared.run_record)
                        self.assertIsNone(record['actual_terminal'])
                        self.assertEqual(record['observed_targets'],[])
                        self.assertEqual(record['stop_reason'],'observation_limit' if rt.done else 'paused')
                        self.assertEqual(record['observations'],{'ordinary-audit':{'at':1441,'completed':False}})
                        self.assertEqual(record['observations'],json.loads(prepared.result)['observations'])
                        rows=[json.loads(line) for line in prepared.ledger.splitlines()]
                        self.assertEqual(len([row for row in rows if row.get('verb')==label]),1)
                        self.assertEqual(prepared.ledger,execution.layers)

    def test_corrupt_rehashed_restore_never_adopts_or_writes(self):
        rt=self.factory.create();state=rt.export_state();state['now']=-1
        with RunStore(self.owner,NativeWriterPort(),NativeGenerationCapture(),compatibility(rt.inputs(),rt.commands)) as store:
            selected=store.initialize(state).selection()
        before={p.name:p.read_bytes() for p in self.out.iterdir()};progress=GenerationProgress()
        with patch('engine.time_kernel.TimeActionKernel.run',side_effect=AssertionError('callback')):
            with self.assertRaises(ValueError):
                native.run_native_calendar(self.project/'world.yaml',self.project/'choices.json',self.out,
                    selection=selected,progress=progress)
        self.assertIsNone(progress.selection)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.out.iterdir()})

    def test_capture_cleanup_preserves_cancellation_and_releases_borrow(self):
        _rt,execution,_data=self.saved();original=_ParentBorrow.close;cancel=KeyboardInterrupt('cancel')
        def closing(borrow): original(borrow);raise OSError('cleanup')
        with patch.object(NativeGenerationCapture,'capture',side_effect=cancel), \
             patch.object(_ParentBorrow,'close',closing):
            with self.assertRaises(KeyboardInterrupt) as caught:
                native._captured_generation(self.owner,execution.selection)
        self.assertIs(caught.exception,cancel);self.assertEqual(self.owner.active_borrows,0)

    def test_capture_cleanup_cancellation_outranks_ordinary_read_failure(self):
        _rt,execution,_data=self.saved();original=_ParentBorrow.close;cancel=KeyboardInterrupt('cleanup')
        def closing(borrow): original(borrow);raise cancel
        with patch.object(NativeGenerationCapture,'capture',side_effect=OSError('read')), \
             patch.object(_ParentBorrow,'close',closing):
            with self.assertRaises(KeyboardInterrupt) as caught:
                native._captured_generation(self.owner,execution.selection)
        self.assertIs(caught.exception,cancel);self.assertEqual(self.owner.active_borrows,0)

    def test_input_ancestry_optin_and_existing_owner_use_acquired_identity(self):
        self.assertEqual(self.capture.source_ancestry[-1],self.factory.choices.ancestry[-1])
        self.assertEqual(self.owner.ancestry[-1],self.owner.identity)
        with open_calendar_capture(self.project/'world.yaml') as unchanged:
            self.assertIsNone(unchanged.source_ancestry)
        native._separate_ancestry(self.owner.ancestry,self.capture.source_ancestry)


class AncestryContractTests(unittest.TestCase):
    def test_windows_synthetic_ancestry_checks_retained_anchor_identity(self):
        from engine.calendar_windows_io import WindowsProjectDirectory
        directory=WindowsProjectDirectory.__new__(WindowsProjectDirectory)
        directory._anchors=[(object(),(11,b'a')),(object(),(11,b'b'))]
        with patch.object(directory,'_check_anchors') as checking:
            self.assertEqual(directory_ancestry(directory),((11,b'a'),(11,b'b')))
            checking.assert_called_once()
        with patch.object(directory,'_check_anchors',side_effect=ValueError('anchor drift')):
            with self.assertRaisesRegex(ValueError,'anchor drift'): directory_ancestry(directory)
        directory._anchors=[]  # No native resources were acquired by this fixture.

    def test_unrecognized_or_closed_directory_cannot_supply_ancestry(self):
        with self.assertRaises(ValueError): directory_ancestry(object())
        if os.name=='posix':
            with tempfile.TemporaryDirectory() as tmp:
                directory=PosixProjectDirectory(tmp,file_limit=0);directory.__exit__()
                with self.assertRaises(ValueError): directory_ancestry(directory)


if __name__=='__main__':unittest.main()
