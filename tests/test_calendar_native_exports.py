"""Native Linux publication plus synthetic fault/Windows-observation witnesses."""
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from engine.calendar_generation_capture import NativeGenerationCapture
from engine.calendar_container_linux import PublicationError
from engine.calendar_native_exports import (MAX_EXPORT_BYTES, PreparedNativeExports,
                                            export_leaf, publish_native_exports)
from engine.calendar_parent_owner import _ExistingParentOwner, _ParentBorrow
from engine.calendar_run_store import CapturedGeneration, NativeWriterPort, RunStore, StoreError
from test_calendar_generation import prepared as generation, COMPAT
from test_calendar_parent_owner import fake_owner
from test_calendar_run_store import Port, Writer


def bundle():
    return PreparedNativeExports(generation().selection(),'b'*32,b'ledger\n',b'result\n',b'record\n')


class ExportPort(Port):
    def __init__(self):
        super().__init__(); self.writers=[]; self.readback_edit=None; self.events=[]
    def open(self,owner,leaf,**expected):
        writer=super().open(owner,leaf,**expected); self.writers.append(writer)
        self.events.append(('open',leaf)); return writer
    def capture(self,owner,leaf,*,max_bytes,parent_borrow):
        parent_borrow._require(owner,owner.backend);parent_borrow.check()
        self.capture_calls.append((owner,leaf,max_bytes,parent_borrow)); self.events.append(('read',leaf))
        if 'capture' in self.fail:raise self.fail['capture']
        assert len(self.writers)==3 and all(w.closed for w in self.writers)
        captured=CapturedGeneration(owner,owner.identity,leaf,self.files[leaf],True,'released')
        return self.readback_edit(captured) if self.readback_edit else captured


class ExportContractTests(unittest.TestCase):
    def owner(self):
        adapter,owner=fake_owner();self.addCleanup(owner.close);return owner
    def publish(self,owner,port,value=None):
        with patch.object(NativeWriterPort,'open',side_effect=port.open), \
                patch.object(NativeGenerationCapture,'capture',side_effect=port.capture):
            return publish_native_exports(owner,value or bundle())

    def test_generated_safe_bounded_names_and_immutable_byte_requirements(self):
        value=bundle()
        for item in value.artifacts():
            self.assertLess(len(item.leaf),255)
            self.assertTrue(item.leaf.isascii())
            self.assertIn(value.selection.expected.sha256,item.leaf)
            self.assertIn(value.publication_id,item.leaf)
        owner=self.owner()
        for bad in (replace(value,publication_id='../x'),replace(value,ledger=bytearray(b'x')),
                    replace(value,run_record=b'x'*(MAX_EXPORT_BYTES+1))):
            with self.assertRaises(ValueError): publish_native_exports(owner,bad)
            self.assertEqual(0,owner.active_borrows)
        for role in ('../x','NUL','unknown',None):
            with self.assertRaises(ValueError):export_leaf(value.selection,value.publication_id,role)
        with self.assertRaises(ValueError):publish_native_exports(Path('not-owner'),value)

    def test_identical_retained_owner_all_contexts_closed_before_readback_and_success(self):
        owner=self.owner();port=ExportPort()
        result=self.publish(owner,port)
        self.assertTrue(all(entry[0] is owner for entry in port.opens+port.capture_calls))
        self.assertEqual([a.leaf for a in bundle().artifacts()],[x[1] for x in port.opens])
        self.assertEqual(['open']*3+['read']*3,[e[0] for e in port.events])
        self.assertEqual([1]*3,[w.close_count for w in port.writers])
        self.assertEqual(0,owner.active_borrows);self.assertEqual('OPEN',owner.state)
        self.assertFalse(result.restart_authority)
        self.assertEqual('same_owner_post_close_exact_bytes',result.readback)
        for evidence in result.observations:
            self.assertEqual(('windows','validity_based','visible_during_write','confirmed','not_established','NOT_RUN'),
                (evidence.backend,evidence.commit_mode,evidence.visibility_mode,evidence.data_flush,
                 evidence.namespace_durability,evidence.native_validation))

    def test_all_stages_failure_and_cancellation_never_accept_or_leak(self):
        for stage in ('open','enter','write','seal','exit','receipt','capture'):
            for kind in (OSError,KeyboardInterrupt):
                with self.subTest(stage=stage,kind=kind):
                    owner=self.owner();port=ExportPort();error=kind(stage);port.fail[stage]=error
                    with self.assertRaises(kind) as caught:self.publish(owner,port)
                    self.assertIs(error,caught.exception);self.assertEqual(0,owner.active_borrows)
                    self.assertTrue(all(w.close_count==1 for w in port.writers if w.borrow is not None))
                    self.assertLessEqual(len(port.opens),3)

    def test_failure_and_cancellation_at_every_artifact_write_seal_close_receipt(self):
        for index in range(3):
            for stage in ('write','seal','exit','receipt'):
                for kind in (OSError,KeyboardInterrupt):
                    with self.subTest(index=index,stage=stage,kind=kind):
                        owner=self.owner();port=ExportPort();error=kind(stage)
                        original=port.open
                        def selected_open(*args,**kwargs):
                            if len(port.opens)==index:port.fail[stage]=error
                            return original(*args,**kwargs)
                        port.open=selected_open
                        with self.assertRaises(kind) as caught:self.publish(owner,port)
                        self.assertIs(error,caught.exception)
                        self.assertEqual(index+1,len(port.opens));self.assertEqual([],port.capture_calls)
                        self.assertEqual(0,owner.active_borrows)
                        self.assertTrue(all(w.close_count==1 for w in port.writers))

    def test_final_receipt_failure_or_readback_drift_cannot_accept_existing_record(self):
        owner=self.owner()
        changes=({'data':b'changed'},{'owner':object()},{'parent_identity':(9,9)},
                 {'leaf':'other'},{'normal_completion':False},{'release':'held'})
        for change in changes:
            with self.subTest(change=change):
                port=ExportPort();port.readback_edit=lambda value:replace(value,**change)
                with self.assertRaises(StoreError):self.publish(owner,port)
                self.assertEqual(3,len(port.files));self.assertEqual(0,owner.active_borrows)
        port=ExportPort();port.overrides['borrow_release']='uncertain'
        with self.assertRaises(StoreError):self.publish(owner,port)
        self.assertEqual(1,len(port.files));self.assertEqual([],port.capture_calls)

    def test_suppressed_body_failure_and_cancellation_still_propagate(self):
        owner=self.owner()
        for kind in (OSError,KeyboardInterrupt):
            port=ExportPort();primary=kind('primary');port.fail['write']=primary;port.suppress=True
            port.overrides.update(expected_bytes_verified=True)
            with self.assertRaises(kind) as caught:self.publish(owner,port)
            self.assertIs(primary,caught.exception);self.assertEqual(0,owner.active_borrows)

    def test_body_close_and_final_borrow_cancellation_precedence_and_single_release(self):
        owner=self.owner()
        for first,second in ((OSError,ValueError),(OSError,KeyboardInterrupt),
                             (KeyboardInterrupt,OSError),(KeyboardInterrupt,KeyboardInterrupt)):
            for place in ('exit','borrow'):
                with self.subTest(first=first,second=second,place=place):
                    primary,cleanup=first('body'),second('cleanup');port=ExportPort();port.fail['write']=primary
                    expected=cleanup if isinstance(primary,Exception) and not isinstance(cleanup,Exception) else primary
                    original=_ParentBorrow.close;closed=[]
                    def fail_close(lease):
                        closed.append(lease);original(lease)
                        if len(closed)==2:raise cleanup
                    with patch.object(_ParentBorrow,'close',fail_close if place=='borrow' else original):
                        if place=='exit':port.fail['exit']=cleanup
                        with self.assertRaises(type(expected)) as caught:self.publish(owner,port)
                    self.assertIs(expected,caught.exception);self.assertEqual(0,owner.active_borrows)
                    if place=='borrow':self.assertEqual(2,len({id(x) for x in closed}))

    def test_failure_of_final_release_prevents_success_after_complete_readback(self):
        owner=self.owner();port=ExportPort();original=_ParentBorrow.close;closed=[]
        def close(lease):
            closed.append(lease);original(lease)
            if len(closed)==4:raise OSError('final release uncertain')
        with patch.object(_ParentBorrow,'close',close):
            with self.assertRaises(OSError):self.publish(owner,port)
        self.assertEqual(3,len(port.capture_calls));self.assertEqual(4,len({id(x) for x in closed}))
        self.assertEqual(0,owner.active_borrows)

    def test_conflict_on_each_leaf_preserves_prior_bytes_and_stops(self):
        owner=self.owner()
        for index,artifact in enumerate(bundle().artifacts()):
            with self.subTest(index=index):
                port=ExportPort();port.files[artifact.leaf]=b'preexisting exact-or-unrelated'
                with self.assertRaises(FileExistsError):self.publish(owner,port)
                self.assertEqual(b'preexisting exact-or-unrelated',port.files[artifact.leaf])
                self.assertEqual(index+1,len(port.opens));self.assertEqual([],port.capture_calls)
                self.assertEqual(0,owner.active_borrows)

    def test_wrong_context_object_is_rejected_and_released(self):
        owner=self.owner();port=ExportPort();original=Writer.__enter__
        def wrong(writer):original(writer);return object()
        with patch.object(Writer,'__enter__',wrong):
            with self.assertRaises(StoreError):self.publish(owner,port)
        self.assertEqual(0,owner.active_borrows)


@unittest.skipUnless(sys.platform=='linux','Native Linux evidence only')
class NativeExportLinuxTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.owner=_ExistingParentOwner(self.root,backend='linux')
        self.addCleanup(self.owner.close)

    def test_native_publish_exact_bytes_and_conflict_never_overwrite(self):
        value=bundle()
        result=publish_native_exports(self.owner,value)
        self.assertEqual(3,len(result.observations))
        for artifact in value.artifacts():self.assertEqual(artifact.data,(self.root/artifact.leaf).read_bytes())
        with self.assertRaises(PublicationError) as caught:publish_native_exports(self.owner,value)
        self.assertIsInstance(caught.exception.__cause__,FileExistsError)
        for artifact in value.artifacts():self.assertEqual(artifact.data,(self.root/artifact.leaf).read_bytes())
        self.assertEqual(0,self.owner.active_borrows)

    def test_renamed_parent_no_path_reads_scans_or_legacy_writer(self):
        import os
        owner=self.owner;value=bundle();renamed=self.root.with_name(self.root.name+'-retained')
        self.root.rename(renamed);self.addCleanup(lambda:renamed.rename(self.root))
        original=os.open
        def relative(name,*args,**kwargs):
            self.assertIsNotNone(kwargs.get('dir_fd'));return original(name,*args,**kwargs)
        with patch.object(Path,'read_bytes',side_effect=AssertionError('no pathname reads')), \
                patch.object(Path,'iterdir',side_effect=AssertionError('no directory scans')), \
                patch('engine.calendar_generation_capture.os.open',relative), \
                patch('engine.calendar_checkpoint.atomic_bytes',side_effect=AssertionError('no legacy')):
            result=publish_native_exports(owner,value)
        self.assertEqual('same_owner_post_close_exact_bytes',result.readback)
        self.assertFalse(self.root.exists());self.assertEqual(0,owner.active_borrows)

    def test_failed_export_keeps_independent_generation_recoverable(self):
        with RunStore(self.owner,NativeWriterPort(),NativeGenerationCapture(),COMPAT,stream_id='a'*32) as store:
            saved=store.initialize({'tick':0})
        value=replace(bundle(),selection=saved.selection())
        original=NativeGenerationCapture.capture
        def fail_exports(port,owner,leaf,**kwargs):
            if leaf.startswith('wbexport-'):raise OSError('post-close readback')
            return original(port,owner,leaf,**kwargs)
        with patch.object(NativeGenerationCapture,'capture',fail_exports):
            with self.assertRaises(OSError):publish_native_exports(self.owner,value)
        with RunStore(self.owner,NativeWriterPort(),NativeGenerationCapture(),COMPAT,stream_id='c'*32) as store:
            self.assertEqual({'tick':0},store.continue_from(saved.selection()))
            self.assertIsNone(store.head);self.assertIsNone(store.last_attempt)
        self.assertEqual(0,self.owner.active_borrows)
        fresh=replace(value,publication_id='c'*32)
        self.assertEqual(fresh,publish_native_exports(self.owner,fresh).prepared)
