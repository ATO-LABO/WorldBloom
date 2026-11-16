"""Explicit generic calendar native route; legacy execution remains separate.

The existing dedicated output directory is selected exactly once. No path reads,
scans, retries or cleanup of native output occur. Selection is caller authority;
derived files and diagnostics are never a restart catalog.
"""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path

from engine.calendar_capture import CalendarCapture, _owned_directory, _raise_cleanup, open_calendar_capture
from engine.calendar_checkpoint import strict_json
from engine.calendar_fs import directory_ancestry
from engine.calendar_generation import GenerationRef, SelectedGeneration, leaf_for
from engine.calendar_generation_capture import NativeGenerationCapture
from engine.calendar_generation_runtime import GenerationProgress, run_calendar_generation
from engine.calendar_inputs import load_captured_project
from engine.calendar_native_exports import publish_native_exports
from engine.calendar_parent_owner import _ExistingParentOwner
from engine.calendar_run_store import CapturedGeneration
from engine.sim import Simulation

MAX_CHOICES_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class CapturedChoices:
    data: bytes
    ancestry: tuple

    @property
    def sha256(self):
        return hashlib.sha256(self.data).hexdigest()

    def commands(self):
        if type(self.data) is not bytes or len(self.data) > MAX_CHOICES_BYTES:
            raise ValueError('Choices require bounded immutable JSON bytes')
        try:
            value = strict_json(self.data.decode('utf-8'))
        except (UnicodeError, RecursionError) as error:
            raise ValueError('Invalid choices JSON') from error
        if type(value) is not list:
            raise ValueError('Choices must be a JSON array')
        return value


def capture_choices(path):
    """Use the existing native input reader, never stat then reopen a label."""
    path = Path(path).absolute()
    with _owned_directory(path.parent) as directory:
        item = directory.read(path.name, budget=MAX_CHOICES_BYTES)
        captured = CapturedChoices(item.data, directory_ancestry(directory))
        captured.commands()  # Validate before output ownership or configure.
    return captured  # Only immutable bytes escape successful source closure.


def selected_from_values(*, resume, sha256=None, size=None, stream_id=None, ordinal=None):
    """Validate ALL independent caller evidence before opening any output."""
    values = (sha256, size, stream_id, ordinal)
    if resume:
        if any(value is None for value in values):
            raise ValueError('Native --resume requires all four independent expected reference fields')
        ref = GenerationRef(sha256, size, stream_id, ordinal)
        return SelectedGeneration(leaf_for(ref), ref).validate()
    if any(value is not None for value in values):
        raise ValueError('Native expected reference fields require --resume')
    return None


def selection_value(selection):
    if selection is None:
        return None
    selection.validate()
    return {'leaf': selection.leaf, 'expected': {key: getattr(selection.expected, key)
            for key in ('sha256', 'size', 'stream_id', 'ordinal')}}


@dataclass(frozen=True)
class CapturedCalendarFactory:
    capture: CalendarCapture
    choices: CapturedChoices

    def create(self):
        if type(self.capture) is not CalendarCapture or type(self.choices) is not CapturedChoices:
            raise ValueError('Exact captured generic inputs are required')
        self.capture.assert_live()
        if self.capture.profile is not None:
            raise ValueError('Generic calendar capture required')
        world, people = load_captured_project(self.capture)
        runtime = Simulation(0, world, people, Path('unused-native-output'),
                             calendar_capture=self.capture).slot_runtime
        runtime.configure(self.choices.commands())
        runtime._native_choices_identity = (self.choices.sha256, len(self.choices.data))
        if runtime.planning is not None:
            runtime.enable_adapter_journal(runtime.planning.identity)
        runtime.sim.verify_execution(runtime.time_kernel.axis)
        # This route has no network/LLM provider or GPU path. Fail rather than
        # silently misreport resource flags if a future constructor adds one.
        if runtime.sim.decision_provider is not None:
            raise ValueError('Generic native CLI requires local explicit/planned choices')
        return runtime


def _output_label(path, protected):
    output = Path(path).absolute()  # No resolve: native opens reject aliases.
    if '..' in output.parts or output == Path(output.anchor):
        raise ValueError('Native output requires an explicit existing dedicated directory')
    for source in protected:
        source = Path(source).absolute()
        if output == source or output in source.parents or source in output.parents:
            raise ValueError('Native output must be separate from input and installed source directories')
    return output


def _separate_ancestry(output_ancestry, protected_ancestry):
    if (not output_ancestry or not protected_ancestry
            or output_ancestry[-1] in protected_ancestry
            or protected_ancestry[-1] in output_ancestry):
        raise ValueError('Native output aliases an input or installed source directory or ancestor')


def _captured_generation(owner, selection):
    # Registered borrow context closes once and preserves cancellation precedence.
    borrow, primary = owner._borrow(owner.backend), None
    try:
        captured = NativeGenerationCapture().capture(owner, selection.leaf,
            max_bytes=selection.expected.size, parent_borrow=borrow)
        if (type(captured) is not CapturedGeneration or captured.owner is not owner
                or captured.parent_identity != owner.identity or captured.leaf != selection.leaf
                or captured.normal_completion is not True or captured.release != 'released'):
            raise ValueError('Selected generation capture differs from retained output owner')
        return captured.data
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            borrow.close()
        except BaseException as cleanup:
            _raise_cleanup(primary, cleanup, "Generation capture borrow cleanup failed: ")


def run_native_calendar(world_path, choices_path, output_path, *, selection=None, until=None,
                        progress=None):
    """Run, capture, project and publish while retaining one output owner.

    Caller retains a fresh one-call GenerationProgress even on failure. No
    success escapes failed source/parent closure. The directory must already
    exist outside both input roots and this installation; creation is separate.
    Caller excludes concurrent mutation of the capture/factory/runtime.
    """
    if selection is not None:
        if type(selection) is not SelectedGeneration:
            raise ValueError('Explicit independent selected generation is required')
        selection.validate()
    if progress is None:
        progress = GenerationProgress()
    if type(progress) is not GenerationProgress:
        raise ValueError('A fresh per-call progress holder is required')
    installed = Path(__file__).resolve().parents[1]
    output = _output_label(output_path, (Path(world_path).parent, Path(choices_path).parent, installed))
    choices = capture_choices(choices_path)
    with open_calendar_capture(world_path, capture_ancestry=True) as capture:
        factory = CapturedCalendarFactory(capture, choices)
        runtime = factory.create()
        # Read source-directory policy observations before output acquisition.
        with _owned_directory(installed) as directory:
            installed_ancestry = directory_ancestry(directory)
        with _ExistingParentOwner(output, backend='windows' if os.name == 'nt' else 'linux') as owner:
            for ancestry in (capture.source_ancestry, choices.ancestry, installed_ancestry):
                _separate_ancestry(owner.ancestry, ancestry)
            execution = run_calendar_generation(runtime, owner, selection=selection,
                                                 until=until, progress=progress)
            data = _captured_generation(owner, execution.selection)
            from engine.calendar_generation_record import project_calendar_generation_record
            prepared = project_calendar_generation_record(execution.selection, data, factory)
            if prepared.ledger != execution.layers or prepared.result != execution.summary:
                raise ValueError('Fresh selected projection differs from executed boundary')
            published = publish_native_exports(owner, prepared)
            result = {'status': 'completed' if execution.done else 'checkpointed',
                'storage': 'generic_calendar_native_generation_v1',
                'selected_generation': selection_value(execution.selection),
                'generation_saved': execution.saved,
                'publication_id': prepared.publication_id,
                'artifacts': {role: {'leaf': artifact.leaf, 'sha256': artifact.sha256,
                                    'size': len(artifact.data)} for role, artifact in
                              zip(('layers', 'result', 'run_record'), prepared.artifacts())},
                'readback': published.readback, 'restart_authority': 'caller_retained_reference',
                'api_calls': 0, 'gpu': False}
    return result
