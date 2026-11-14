"""Private whole-session native persistence, not a product/API entry point.

The caller retains both the live input capture and selected output-parent owner.
Only independently retained selections are accepted. Derived exports are bytes;
this module never opens legacy output paths or discovers a latest generation.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
from pathlib import Path
import threading

from engine.calendar_checkpoint import compatibility
from engine.calendar_generation import SelectedGeneration
from engine.calendar_generation_capture import NativeGenerationCapture
from engine.calendar_parent_owner import _ExistingParentOwner
from engine.calendar_run_store import RunStore, NativeWriterPort
from engine.calendar_runtime import integer
from engine.decision_provider import provider_snapshot
from execution.melos_factory import MelosSessionFactory
from execution.melos_session import MelosSession
from execution.provenance import canonical


@dataclass(frozen=True)
class MelosGenerationResult:
    selection: SelectedGeneration
    layers: bytes
    result: bytes
    done: bool
    now: int
    saved: bool


class MelosGenerationProgress:
    """Optional one-call retention of only confirmed, semantically adopted heads.

    On failure this remains the last accepted selection, or None. It does not
    assert that a failed attempt left no file, or authorize guessing its status.
    The outer caller must retain it independently; it is not a durable catalog.
    """
    def __init__(self):
        self._selection = None
        self._saved = False
        self._started = False
        self._gate = threading.Lock()

    @property
    def selection(self):
        return self._selection

    @property
    def saved(self):
        return self._saved

    def _claim(self):
        # Claim before callbacks/IO. Do not wait on a concurrent or inherited
        # held lock, and never let another call publish into this holder.
        if not self._gate.acquire(blocking=False):
            raise ValueError('A fresh per-call progress holder is required')
        try:
            if self._started:
                raise ValueError('A fresh per-call progress holder is required')
            self._started = True
        finally:
            self._gate.release()

    def _retain(self, selection, *, saved):
        self._selection = selection
        self._saved = saved


def melos_generation_compatibility(session):
    """Compute identity from adopted inputs/configuration and installed code."""
    return {'family': 'melos_native_session_v1', 'schema_version': 1,
            'runtime': compatibility(session.rt.inputs(), session.rt.commands),
            'session': deepcopy(session.stage.identity),
            'provider': provider_snapshot(session.stage.provider)['identity'],
            'bridge_source_hash': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def _result_bytes(session):
    committed = session.export_state()
    result = {'schema': 'melos_native_session_result_v1',
              'completion': 'complete' if session.finished else 'paused',
              'seed': session.trial.seed, 'state': deepcopy(session.trial.state),
              'decisions': deepcopy(session.trial.decisions),
              'counts': deepcopy(session.trial.count),
              'audit': session.trial.audit() if session.finished else None,
              'product_profile_active': False, 'checkpoint_sha256': committed['sha256']}
    return canonical(result) + b'\n'


def run_melos_generation(session, parent_owner, *, selection=None, until=None,
                         stream_id=None, progress=None):
    """Save/restore the complete finite scenario on a caller-owned native parent.

    Each session transition is transactional, including provider/RNG state and
    deferred action effects. A successful restore preserves the original Trial,
    Simulation, World, Runtime and real subject objects. The native store owns
    only its temporary borrow. Session.finished governs terminal no-op resumes.
    The caller must exclude concurrent session/factory mutation for this call.
    """
    if type(session) is not MelosSession or session.rt.calendar_capture is None:
        raise ValueError('A captured MelosSession is required')
    factory = getattr(session.stage, '_captured_factory', None)
    if type(factory) is not MelosSessionFactory or factory._calendar_capture is not session.rt.calendar_capture:
        raise ValueError('Captured session requires its approved factory binding')
    if type(parent_owner) is not _ExistingParentOwner:
        raise ValueError('A selected existing output parent owner is required')
    if selection is not None:
        if type(selection) is not SelectedGeneration:
            raise ValueError('Explicit independent generation selection is required')
        selection.validate()
    if progress is None:
        progress = MelosGenerationProgress()
    if type(progress) is not MelosGenerationProgress:
        raise ValueError('A fresh per-call progress holder is required')
    progress._claim()
    session.rt.calendar_capture.assert_live()
    # Verify the approved source/configuration and restore capability before
    # acquiring an output borrow. This never reopens captured input paths.
    session.stage._staging_trial()
    session.rt.sim.verify_execution(session.rt.time_kernel.axis)
    boundary = session.rt.end if until is None else integer(until, 'stop minute', session.rt.now, session.rt.end)
    expected = melos_generation_compatibility(session)
    with RunStore(parent_owner, NativeWriterPort(), NativeGenerationCapture(), expected,
                  stream_id=stream_id) as store:
        # Sticky on success, failed writes and rejected restores. Never store a
        # closed handle in the runtime, or thaw commands/journal on rollback.
        session.rt._generation_persistence_started = True
        if selection is not None:
            state = store.continue_from(selection)
            # Reject a backwards stop before adopting any selected state.
            try:
                selected_now = state['state']['stage']['state']['runtime']['now']
                integer(selected_now, 'selected minute', 0, boundary)
            except (KeyError, TypeError) as exc:
                raise ValueError('Malformed selected session cursor') from exc
            session.restore_state(state)
            progress._retain(selection, saved=False)
        else:
            accepted = store.initialize(session.export_state())
            progress._retain(accepted.selection(), saved=True)

        def commit(snapshot):
            accepted = store.save(snapshot)
            progress._retain(accepted.selection(), saved=True)

        if not session.finished:
            session.run(until=boundary, _commit=commit)
        layers = b''.join(canonical(row) + b'\n' for row in session.rt.rows)
        result = _result_bytes(session)
        # Successful result authority belongs to this store, never a mutable
        # observer supplied by a caller (even though its claim is exclusive).
        selected = store.head.selection() if store.head is not None else selection
        return MelosGenerationResult(selected, layers, result,
                                     session.finished, session.rt.now, store.head is not None)
