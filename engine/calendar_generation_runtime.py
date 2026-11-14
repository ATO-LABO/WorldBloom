"""Private native save/restore execution bridge, not a CLI/job/UI entry point.

The caller has already selected a dedicated output parent under its output
policy. Captured input ownership remains with that caller. Results are immutable
bytes, so this bridge cannot fall back to path-based derived-output writers.
"""
from dataclasses import dataclass
import threading

from engine.calendar_checkpoint import compatibility
from engine.calendar_generation import SelectedGeneration
from engine.calendar_generation_capture import NativeGenerationCapture
from engine.calendar_parent_owner import _ExistingParentOwner
from engine.calendar_run_store import RunStore, NativeWriterPort
from engine.calendar_runtime import CalendarRuntime, integer


@dataclass(frozen=True)
class GenerationExecutionResult:
    selection: SelectedGeneration
    layers: bytes
    summary: bytes
    done: bool
    now: int
    saved: bool


class GenerationProgress:
    """Caller-retained accepted selection for one invocation, including failure.

    This is not a durable restart catalog or proof that a failed native attempt
    left no file. It retains only completed commits or a semantically adopted
    resume base, never the most recent attempted generation.
    """
    def __init__(self):
        self._selection = None
        self._saved = False
        self._started = False
        self._claim_lock = threading.Lock()

    def _claim(self):
        # A holder can be shared accidentally across independent runtimes.
        # Claim it atomically before calling any preflight callbacks or I/O.
        with self._claim_lock:
            if self._started:
                raise ValueError('A fresh per-call progress holder is required')
            self._started = True

    @property
    def selection(self):
        return self._selection

    @property
    def saved(self):
        return self._saved

    def _retain(self, selection, *, saved):
        self._selection = selection
        self._saved = saved


def run_calendar_generation(runtime, parent_owner, *, selection=None, until=None,
                            stream_id=None, progress=None):
    """Execute one caller-owned captured runtime and semantically restore first.

    Selection must contain an independently retained expected reference, never
    one reconstructed from the selected file, its name, or a mutable manifest.
    This bridge creates/releases its store borrow but never closes the root.
    """
    if type(runtime) is not CalendarRuntime or runtime.calendar_capture is None:
        raise ValueError('A captured CalendarRuntime is required')
    if type(parent_owner) is not _ExistingParentOwner:
        raise ValueError('A selected existing output parent owner is required')
    if selection is not None:
        if type(selection) is not SelectedGeneration:
            raise ValueError('Explicit independent generation selection is required')
        selection.validate()
    if progress is None:
        progress = GenerationProgress()
    if type(progress) is not GenerationProgress:
        raise ValueError('A fresh per-call progress holder is required')
    progress._claim()
    runtime.calendar_capture.assert_live()
    if runtime.planning is not None:
        runtime.enable_adapter_journal(runtime.planning.identity)
    runtime.sim.verify_execution(runtime.time_kernel.axis)
    boundary = runtime.end if until is None else integer(until, 'stop minute', runtime.now, runtime.end)
    # Derive compatibility from adopted inputs, commands and provider identity,
    # current source/Python/OS, not from untrusted selected checkpoint metadata.
    expected = compatibility(runtime.inputs(), runtime.commands)
    with RunStore(parent_owner, NativeWriterPort(), NativeGenerationCapture(), expected,
                  stream_id=stream_id) as store:
        # This flag is deliberately sticky, including failed/uncertain writes
        # and rejected restores. Never retain a closed store as the lifecycle
        # marker, and never thaw commands or journal identity on rollback.
        runtime._generation_persistence_started = True
        if selection is not None:
            state = store.continue_from(selection)
            # An invalid stop request must not adopt an otherwise valid later
            # state before reporting failure.
            if not isinstance(state, dict):
                raise ValueError('Malformed selected runtime state')
            integer(state.get('now'), 'selected minute', 0, boundary)
            # restore_state stages and validates all semantic state before
            # adopting it. No simulation callback or export precedes this.
            runtime.restore_state(state)
            progress._retain(selection, saved=False)
        else:
            accepted = store.initialize(runtime.export_state())
            progress._retain(accepted.selection(), saved=True)

        def commit():
            accepted = store.save(runtime.export_state())
            progress._retain(accepted.selection(), saved=True)

        if not runtime.done:
            runtime.time_kernel.run(runtime._next_boundary, runtime._boundary_plan(), until=boundary,
                                    before_boundary=runtime.export_state,
                                    after_boundary=commit,
                                    on_error=runtime.restore_state)
        # A successful result is bound to this local store, never to mutable
        # data retained by an outer observer.
        selected = store.head.selection() if store.head is not None else selection
        return GenerationExecutionResult(selected, runtime._layers_bytes(), runtime._summary_bytes(),
                                         runtime.done, runtime.now, store.head is not None)
