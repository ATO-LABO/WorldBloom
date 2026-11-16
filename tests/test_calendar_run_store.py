"""Memory-only port tests. No native handle, real output, GUI or public route."""
from dataclasses import replace
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from engine.calendar_generation import GenerationError, SelectedGeneration, checkpoint_bytes
from engine.calendar_run_store import (
    AcceptedGeneration, CapturedGeneration, CommitObservation, ConcurrentStoreOperation,
    NativeWriterPort, RunStore, StoreError,
)
from tests.test_calendar_generation import COMPAT, STREAM, prepared


class Borrow:
    def __init__(self,owner):self.owner=owner;self.closed=False
    def close(self):
        if not self.closed:
            self.closed=True;self.owner.active_borrows-=1;self.owner.releases+=1
            if self.owner.release_error is not None:raise self.owner.release_error


class Owner:
    def __init__(self,backend='linux'):
        self.backend=backend;self.identity=(1,2);self.state='OPEN';self.active_borrows=0
        self.releases=0;self.release_error=None
    def _borrow(self,backend):
        if backend!=self.backend or self.state!='OPEN':raise StoreError('bad owner')
        self.active_borrows+=1;return Borrow(self)
    def close(self):
        if self.active_borrows:raise StoreError('borrowed')
        self.state='CLOSED'
    def __fspath__(self):raise AssertionError('No pathname authority')


class Port:
    def __init__(self):
        self.files={};self.opens=[];self.fail={};self.overrides={};self.on_write=None
        self.suppress=False;self.last=None;self.capture_override=None;self.capture_calls=[]
    def open(self,owner,leaf,**expected):
        self.opens.append((owner,leaf,expected))
        if 'open' in self.fail:raise self.fail['open']
        self.last=Writer(self,owner,leaf,expected);return self.last
    def capture(self,owner,leaf,*,max_bytes,parent_borrow):
        self.capture_calls.append((owner,leaf,max_bytes,parent_borrow))
        assert parent_borrow.owner is owner and not parent_borrow.closed
        assert owner.active_borrows>=2
        if 'capture' in self.fail:raise self.fail['capture']
        value=CapturedGeneration(owner,owner.identity,leaf,self.files[leaf],True,'released')
        return self.capture_override(value) if self.capture_override else value


class Writer:
    def __init__(self,port,owner,leaf,expected):
        self.port=port;self.owner=owner;self.leaf=leaf;self.expected=expected
        self.data=b'';self.sealed=False;self.closed=False;self.borrow=None;self.close_count=0
    def __enter__(self):
        if 'enter' in self.port.fail:raise self.port.fail['enter']
        self.borrow=self.owner._borrow(self.owner.backend)
        return self
    def write_all(self,chunks):
        if self.port.on_write:self.port.on_write()
        if 'write' in self.port.fail:raise self.port.fail['write']
        self.data=b''.join(chunks)
    def seal(self):
        if 'seal' in self.port.fail:raise self.port.fail['seal']
        if self.leaf in self.port.files:raise FileExistsError('existing leaf protected')
        self.port.files[self.leaf]=self.data;self.sealed=True
        return self.receipt  # This deliberately reports pre-close status.
    def __exit__(self,typ,error,tb):
        self.close_count+=1;self.closed=True
        if self.borrow:self.borrow.close()
        if 'exit' in self.port.fail:raise self.port.fail['exit']
        return self.port.suppress
    @property
    def receipt(self):
        if self.closed and 'receipt' in self.port.fail:raise self.port.fail['receipt']
        value=dict(parent_identity=self.owner.identity,object_identity=(1,3),final_leaf=self.leaf,
                   expected_size=self.expected['expected_size'],expected_sha256=self.expected['expected_sha256'],
                   release_scope='file_and_borrow',borrow_release='released' if self.closed else 'held',
                   retained_parent=True)
        if self.owner.backend=='linux':
            value.update(publication='published' if self.sealed else 'not_attempted',completion='sealed' if self.sealed else 'pending',
                         durability='namespace_flush_confirmed',release='closed' if self.closed else 'open',
                         file_release='closed' if self.closed else 'open',commit_mode='atomic_namespace')
        else:
            value.update(phase='released' if self.closed else 'sealed',creation='created',expected_bytes_verified=self.sealed,
                         data_flush='confirmed',bytes_written=len(self.data),resources_released='confirmed' if self.closed else 'held',
                         file_release='confirmed' if self.closed else 'held',commit_mode='validity_based',
                         visibility_mode='visible_during_write',namespace_durability='not_established',native_validation='NOT_RUN')
        if self.closed:value.update(self.port.overrides)
        return SimpleNamespace(**value)


def store(backend='linux',**kwargs):
    owner=Owner(backend);port=Port()
    return RunStore(owner,port,port,COMPAT,stream_id=kwargs.pop('stream_id',STREAM)),owner,port


class RunStoreTests(unittest.TestCase):
    def test_repeated_saves_same_owner_exact_checkpoint_and_no_overwrites(self):
        s,o,p=store()
        with s:
            first=s.initialize({'tick':0});before=dict(p.files)
            second=s.save({'tick':1})
            self.assertEqual(checkpoint_bytes({'tick':0},COMPAT),first.prepared.checkpoint)
            self.assertEqual(first.ref,second.prepared.previous)
            self.assertEqual(1,second.ref.ordinal)
            self.assertEqual(before[first.prepared.leaf],p.files[first.prepared.leaf])
            self.assertTrue(all(entry[0] is o for entry in p.opens))
            self.assertEqual(1,o.active_borrows)
            with self.assertRaises(StoreError):o.close()
        self.assertEqual(('CLOSED','released',0,'OPEN'),(s.state,s.session_release,o.active_borrows,o.state))

    def test_explicit_old_generation_continues_without_extra_resume_save(self):
        s,o,p=store();a=s.initialize({'tick':0});b=s.save({'tick':1});s.finish();before=dict(p.files)
        resumed=RunStore(o,p,p,COMPAT,stream_id='b'*32)
        state=resumed.continue_from(a.selection())
        self.assertEqual({'tick':0},state);self.assertEqual(before,p.files)
        self.assertEqual(a.ref,resumed.selected_base);self.assertIsNone(resumed.head)
        fork=resumed.save({'tick':2})
        self.assertEqual(0,fork.ref.ordinal);self.assertEqual(a.ref,fork.prepared.forked_from)
        self.assertIsNone(fork.prepared.previous)
        self.assertEqual(before[a.prepared.leaf],p.files[a.prepared.leaf])
        self.assertEqual(before[b.prepared.leaf],p.files[b.prepared.leaf])
        resumed.finish()

    def test_current_head_in_memory_not_reread_from_output_path(self):
        s,o,p=store();a=s.initialize({'tick':0});p.files[a.prepared.leaf]=b'changed after publish'
        b=s.save({'tick':1});self.assertEqual(a.ref,b.prepared.previous)
        self.assertEqual([],p.capture_calls);s.finish()

    def test_windows_acceptance_records_limited_visibility_and_qualification(self):
        s,o,p=store('windows');a=s.initialize({'tick':0})
        self.assertEqual(('validity_based','visible_during_write','not_established','NOT_RUN'),
                         (a.observation.commit_mode,a.observation.visibility_mode,
                          a.observation.namespace_durability,a.observation.native_validation));s.finish()

    def test_each_receipt_binding_and_release_condition_required(self):
        common={'parent_identity':(9,9),'object_identity':(),'final_leaf':'other','expected_size':True,
                'expected_sha256':'0'*64,'release_scope':'file_and_parent','borrow_release':'held','retained_parent':False}
        variants={'linux':dict(common,publication='unknown',completion='failed',durability='data_flushed',
                               release='uncertain',file_release='uncertain',commit_mode='other'),
                  'windows':dict(common,phase='failed_after_seal',creation='unknown',expected_bytes_verified=False,
                                 data_flush='attempted',bytes_written=True,resources_released='unconfirmed',
                                 file_release='unconfirmed',commit_mode='other',visibility_mode='atomic_appearance',
                                 namespace_durability='namespace_flush_confirmed')}
        for backend,changes in variants.items():
            for key,value in changes.items():
                s,o,p=store(backend);p.overrides[key]=value
                with self.subTest(backend=backend,key=key),self.assertRaises(StoreError):s.initialize({'tick':0})
                self.assertEqual('STOPPED',s.state);self.assertIsNone(s.head);s.finish()

    def test_fault_and_cancellation_at_every_port_stage_stop_without_retry(self):
        for backend in ('linux','windows'):
            for stage in ('open','enter','write','seal','exit','receipt'):
                for kind in (RuntimeError,KeyboardInterrupt):
                    s,o,p=store(backend);error=kind(stage);p.fail[stage]=error
                    with self.subTest(backend=backend,stage=stage,kind=kind),self.assertRaises(kind) as caught:
                        s.initialize({'tick':0})
                    self.assertIs(error,caught.exception);self.assertEqual('STOPPED',s.state)
                    with self.assertRaises(StoreError):s.initialize({'tick':0})
                    self.assertEqual(1,len(p.opens));self.assertIsNone(s.head);s.finish()

    def test_final_success_receipt_does_not_hide_body_failure_or_cancellation(self):
        for kind in (RuntimeError,KeyboardInterrupt):
            s,o,p=store();error=kind('body failure');p.fail['write']=error;p.suppress=True
            # A hostile/broken port reports a successful final observation even
            # though its context swallowed the body error. Coordinator must not.
            p.overrides.update(publication='published',completion='sealed')
            with self.assertRaises(kind) as caught:s.initialize({'tick':0})
            self.assertIs(error,caught.exception);self.assertEqual('STOPPED',s.state);s.finish()

    def test_body_cancellation_preserved_against_ordinary_cleanup_masking(self):
        s,o,p=store();primary=KeyboardInterrupt('primary');p.fail.update(write=primary,exit=OSError('cleanup'))
        with self.assertRaises(KeyboardInterrupt) as caught:s.initialize({'tick':0})
        self.assertIs(primary,caught.exception);self.assertEqual('STOPPED',s.state);s.finish()

    def test_cleanup_cancellation_wins_over_ordinary_body_error(self):
        s,o,p=store();cancel=KeyboardInterrupt('cleanup');p.fail.update(write=OSError('body'),exit=cancel)
        with self.assertRaises(KeyboardInterrupt) as caught:s.initialize({'tick':0})
        self.assertIs(cancel,caught.exception);s.finish()

    def test_existing_generation_leaf_is_never_replaced(self):
        s,o,p=store();g=prepared();p.files[g.leaf]=b'existing unrelated data'
        with self.assertRaises(FileExistsError):s.initialize({'tick':0})
        self.assertEqual(b'existing unrelated data',p.files[g.leaf]);self.assertEqual('STOPPED',s.state);s.finish()

    def test_unknown_after_publication_keeps_prior_head_and_remaining_object(self):
        s,o,p=store();a=s.initialize({'tick':0});p.fail['receipt']=OSError('lost observation')
        with self.assertRaises(OSError):s.save({'tick':1})
        self.assertEqual(a,s.head);self.assertEqual(2,len(p.files));self.assertEqual(1,s.last_attempt.ref.ordinal)
        with self.assertRaises(StoreError):s.save({'tick':2})
        self.assertEqual(2,len(p.opens));s.finish()

    def test_malformed_selection_rejected_before_capture(self):
        s,o,p=store();g=prepared()
        for selection in (None,g.ref,SelectedGeneration('../'+g.leaf,g.ref)):
            with self.subTest(selection=selection),self.assertRaises(GenerationError):s.load_selected(selection)
        self.assertEqual([],p.capture_calls);self.assertEqual('NEW',s.state);s.finish()

    def test_captured_alternate_cannot_self_authorize(self):
        s,o,p=store();a=prepared({'tick':1});b=prepared({'tick':2});p.files[a.leaf]=b.data
        with self.assertRaises(GenerationError):s.continue_from(a.selection(),new_stream_id='b'*32)
        self.assertEqual('NEW',s.state);self.assertIsNone(s.selected_base);self.assertEqual(1,o.active_borrows);s.finish()

    def test_capture_uses_registered_same_owner_borrow_and_checks_completion(self):
        for change in (lambda c:replace(c,owner=Owner()),lambda c:replace(c,parent_identity=(8,8)),
                       lambda c:replace(c,leaf='other'),lambda c:replace(c,normal_completion=False),
                       lambda c:replace(c,release='unconfirmed'),lambda c:replace(c,data=bytearray(c.data))):
            s,o,p=store();g=prepared();p.files[g.leaf]=g.data;p.capture_override=change
            with self.assertRaises(StoreError):s.load_selected(g.selection())
            self.assertEqual('STOPPED',s.state);self.assertEqual(1,o.active_borrows)
            self.assertTrue(p.capture_calls[0][3].closed);s.finish()

    def test_capture_cancellation_and_borrow_release_failure_preserve_primary(self):
        s,o,p=store();g=prepared();cancel=KeyboardInterrupt('capture');p.fail['capture']=cancel;o.release_error=OSError('release')
        with self.assertRaises(KeyboardInterrupt) as caught:s.load_selected(g.selection())
        self.assertIs(cancel,caught.exception);self.assertEqual('STOPPED',s.state)
        o.release_error=None;s.finish()

    def test_same_stream_continuation_rejected_without_io(self):
        s,o,p=store();g=prepared()
        with self.assertRaises(GenerationError):s.continue_from(g.selection())
        self.assertEqual([],p.capture_calls);s.finish()

    def test_concurrent_operation_rejected_without_blocking_or_second_commit(self):
        s,o,p=store();entered=threading.Event();release=threading.Event();errors=[]
        def wait():entered.set();release.wait(5)
        p.on_write=wait
        def work():
            try:s.initialize({'tick':0})
            except BaseException as e:errors.append(e)
        thread=threading.Thread(target=work);thread.start();self.assertTrue(entered.wait(5))
        try:
            with self.assertRaises(ConcurrentStoreOperation):s.initialize({'tick':1})
            with self.assertRaises(ConcurrentStoreOperation):s.finish()
        finally:release.set();thread.join(5)
        self.assertFalse(thread.is_alive());self.assertEqual([],errors);self.assertEqual(1,len(p.opens));s.finish()

    def test_lost_parent_identity_and_fork_pid_rejected_before_port_io(self):
        s,o,p=store();o.identity=(9,9)
        with self.assertRaises(StoreError):s.initialize({'tick':0})
        self.assertEqual([],p.opens);s.finish()
        s,o,p=store()
        with patch('engine.calendar_run_store.os.getpid',return_value=s._pid+1):
            with self.assertRaises(StoreError):s.initialize({'tick':0})
            with self.assertRaises(StoreError):s.finish()
        self.assertEqual([],p.opens);s.finish()

    def test_session_release_failure_is_sticky_and_not_retried(self):
        s,o,p=store();error=KeyboardInterrupt('release');o.release_error=error
        with self.assertRaises(KeyboardInterrupt) as caught:s.finish()
        self.assertIs(error,caught.exception);count=o.releases;s.finish()
        self.assertEqual(('STOPPED','uncertain',count),(s.state,s.session_release,o.releases))

    def test_context_body_cancellation_survives_session_cleanup_error(self):
        s,o,p=store();cancel=KeyboardInterrupt('body');o.release_error=OSError('release')
        with self.assertRaises(KeyboardInterrupt) as caught:
            with s:raise cancel
        self.assertIs(cancel,caught.exception);self.assertEqual('uncertain',s.session_release)

    def test_mutating_caller_compatibility_does_not_change_expected_identity(self):
        c=dict(COMPAT);o=Owner();p=Port();s=RunStore(o,p,p,c,stream_id=STREAM);c['provider']='changed'
        a=s.initialize({'tick':0});self.assertEqual({'tick':0},a.prepared.state(COMPAT));s.finish()

    def test_unintegrated_native_bridge_has_no_path_fallback(self):
        s,o,p=store()
        with patch('engine.calendar_container_linux.LinuxSnapshot._from_parent_owner', None), self.assertRaisesRegex(StoreError,'not integrated'):
            NativeWriterPort().open(o,'anything',expected_size=1,expected_sha256='0'*64,max_bytes=1)
        self.assertEqual(1,o.active_borrows);s.finish()


    def test_preflight_cancellation_stops_but_invalid_state_does_not_publish(self):
        s,o,p=store();error=KeyboardInterrupt('serialize')
        with patch('engine.calendar_run_store.checkpoint_bytes',side_effect=error):
            with self.assertRaises(KeyboardInterrupt) as caught:s.initialize({'tick':0})
        self.assertIs(error,caught.exception);self.assertEqual('STOPPED',s.state);self.assertEqual([],p.opens);s.finish()
        s,o,p=store()
        with self.assertRaises(ValueError):s.initialize({'bad':float('nan')})
        self.assertEqual('NEW',s.state);self.assertEqual([],p.opens);s.finish()

    def test_decode_cancellation_stops_after_capture_borrow_is_released(self):
        s,o,p=store();g=prepared();p.files[g.leaf]=g.data;error=KeyboardInterrupt('decode')
        with patch('engine.calendar_run_store.decode_generation',side_effect=error):
            with self.assertRaises(KeyboardInterrupt) as caught:s.load_selected(g.selection())
        self.assertIs(error,caught.exception);self.assertEqual(('STOPPED',1),(s.state,o.active_borrows));s.finish()

    def test_capture_cleanup_cancellation_wins_over_ordinary_read_error(self):
        s,o,p=store();g=prepared();cancel=KeyboardInterrupt('borrow release')
        p.fail['capture']=OSError('capture');o.release_error=cancel
        with self.assertRaises(KeyboardInterrupt) as caught:s.load_selected(g.selection())
        self.assertIs(cancel,caught.exception);self.assertEqual('STOPPED',s.state)
        o.release_error=None;s.finish()

    def test_capture_bound_is_exact_expected_size_and_overread_stops(self):
        s,o,p=store();g=prepared();p.files[g.leaf]=g.data+b'x'
        with self.assertRaises(StoreError):s.load_selected(g.selection())
        self.assertEqual(g.ref.size,p.capture_calls[0][2]);self.assertEqual('STOPPED',s.state);s.finish()


    def test_store_copying_is_rejected_without_duplicate_lease_ownership(self):
        import copy
        import pickle
        s,o,p=store()
        for operation in (copy.copy,copy.deepcopy,pickle.dumps):
            with self.subTest(operation=operation.__name__),self.assertRaises(TypeError):operation(s)
        self.assertEqual(('NEW',1,0),(s.state,o.active_borrows,o.releases))
        s.finish();self.assertEqual((0,1),(o.active_borrows,o.releases))


class FinalReceiptHelperTests(unittest.TestCase):
    def test_shared_helper_matches_generation_and_accepts_zero_byte_derived_expectation(self):
        from engine.calendar_run_store import validate_final_receipt
        import hashlib
        for backend in ('linux','windows'):
            with self.subTest(backend=backend):
                s,o,p=store(backend)
                with s:
                    accepted=s.initialize({'tick':0})
                    observed=validate_final_receipt(p.last.receipt,backend=backend,parent_identity=o.identity,
                        expected_leaf=accepted.prepared.leaf,expected_size=accepted.ref.size,expected_sha256=accepted.ref.sha256)
                    self.assertEqual(accepted.observation,observed)
                writer=p.open(o,'derived-empty.json',expected_size=0,expected_sha256=hashlib.sha256(b'').hexdigest(),max_bytes=1)
                with writer:writer.write_all([]);writer.seal()
                evidence=validate_final_receipt(writer.receipt,backend=backend,parent_identity=o.identity,
                    expected_leaf='derived-empty.json',expected_size=0,expected_sha256=hashlib.sha256(b'').hexdigest())
                self.assertEqual(backend,evidence.backend)

    def test_shared_helper_requires_exact_expected_leaf_size_hash_and_release(self):
        from engine.calendar_run_store import validate_final_receipt
        for backend in ('linux','windows'):
            s,o,p=store(backend)
            with s:accepted=s.initialize({'tick':0})
            args=dict(backend=backend,parent_identity=o.identity,expected_leaf=accepted.prepared.leaf,
                      expected_size=accepted.ref.size,expected_sha256=accepted.ref.sha256)
            for updates in ({'expected_leaf':'other'},{'expected_size':True},{'expected_size':-1},
                            {'expected_size':accepted.ref.size+1},{'expected_sha256':'0'*64},
                            {'expected_sha256':'invalid'},{'parent_identity':()}, {'backend':'unsupported'}):
                with self.subTest(backend=backend,updates=updates),self.assertRaises(ValueError):
                    validate_final_receipt(p.last.receipt,**dict(args,**updates))
            p.overrides['borrow_release']='held'
            with self.assertRaises(StoreError):validate_final_receipt(p.last.receipt,**args)
