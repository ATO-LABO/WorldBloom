"""Private create-only derived bytes on one caller-retained native parent.

Neither these artifacts nor a successful publication select a restart head.
Failures can leave files; there is no overwrite, scan, rename or cleanup path.
Only the caller's independently retained generation remains save authority.
"""
from dataclasses import dataclass
import hashlib

from engine.calendar_generation import SelectedGeneration, _stream
from engine.calendar_generation_capture import NativeGenerationCapture
from engine.calendar_parent_owner import _ExistingParentOwner
from engine.calendar_run_store import (CapturedGeneration, NativeWriterPort, StoreError,
                                       validate_final_receipt)

MAX_EXPORT_BYTES = 32 * 1024 * 1024  # Per artifact, at most three artifacts.
_ROLES = ('layers.jsonl', 'result.json', 'run-record.json')


def export_leaf(selection, publication_id, role):
    if type(selection) is not SelectedGeneration:
        raise ValueError('An independent selected generation is required')
    selection.validate()
    _stream(publication_id)
    if type(role) is not str or role not in _ROLES:
        raise ValueError('Unknown derived export role')
    ref = selection.expected
    return ('wbexport-' + ref.stream_id + '-' + f'{ref.ordinal:020d}' + '-' + ref.sha256
            + '-' + publication_id + '-' + role)


@dataclass(frozen=True)
class NativeExportArtifact:
    leaf: str
    data: bytes

    @property
    def sha256(self):
        return hashlib.sha256(self.data).hexdigest()


@dataclass(frozen=True)
class PreparedNativeExports:
    selection: SelectedGeneration
    publication_id: str
    ledger: bytes
    result: bytes
    run_record: bytes

    def artifacts(self):
        values = (self.ledger, self.result, self.run_record)
        if any(type(data) is not bytes or len(data) > MAX_EXPORT_BYTES for data in values):
            raise ValueError('Derived exports require bounded immutable bytes')
        return tuple(NativeExportArtifact(export_leaf(self.selection, self.publication_id, role), data)
                     for role, data in zip(_ROLES, values))


@dataclass(frozen=True)
class PublishedNativeExports:
    prepared: PreparedNativeExports
    observations: tuple
    readback: str = 'same_owner_post_close_exact_bytes'
    restart_authority: bool = False


def _write(owner, artifact):
    writer = NativeWriterPort().open(owner, artifact.leaf, expected_size=len(artifact.data),
                                    expected_sha256=artifact.sha256, max_bytes=MAX_EXPORT_BYTES)
    body_completed, body_error = False, None
    try:
        with writer as entered:
            try:
                if entered is not writer:
                    raise StoreError('Writer context returned an unrelated object')
                writer.write_all(artifact.data[i:i + 65536] for i in range(0, len(artifact.data), 65536))
                writer.seal()  # Never trust a pre-close receipt.
                body_completed = True
            except BaseException as error:
                body_error = error
                raise
    except BaseException as context_error:
        if body_error is not None and (not isinstance(body_error, Exception)
                                       or isinstance(context_error, Exception)):
            raise body_error
        raise
    if body_error is not None:
        raise body_error  # A suppressing context cannot turn failure into success.
    if not body_completed:
        raise StoreError('Writer did not complete normally')
    return writer.receipt


def publish_native_artifacts(parent_owner, artifacts):
    """Publish at most three create-only artifacts; accept after exact reads.

    The input bundle is immutable, bounded projection output, not admission to
    the public UI. NativeWriterPort owns each writer's borrow; this call owns
    one separate read/continuity borrow and releases it before returning.
    Cancellation outranks ordinary cleanup errors, including suppressed bodies.
    The caller excludes concurrent mutation and retains the parent/capture.
    """
    if (type(artifacts) is not tuple or not 1 <= len(artifacts) <= 3
            or any(type(a) is not NativeExportArtifact or type(a.data) is not bytes
                   or len(a.data) > MAX_EXPORT_BYTES for a in artifacts)
            or len({a.leaf for a in artifacts}) != len(artifacts)):
        raise ValueError('Bounded immutable native artifacts required')
    from engine.calendar_windows_writer import _leaf as validate_leaf
    for artifact in artifacts:
        validate_leaf(artifact.leaf)
    if type(parent_owner) is not _ExistingParentOwner or parent_owner.state != 'OPEN':
        raise ValueError('An open selected existing output parent owner is required')
    backend, identity = parent_owner.backend, parent_owner.identity
    lease, primary = None, None
    observations = []
    try:
        lease = parent_owner._borrow(backend)
        for artifact in artifacts:
            lease.check()
            receipt = _write(parent_owner, artifact)
            observations.append(validate_final_receipt(
                receipt, backend=backend, parent_identity=identity, expected_leaf=artifact.leaf,
                expected_size=len(artifact.data), expected_sha256=artifact.sha256))
            lease.check()
        # RunRecord existence alone is never success. All contexts must exit
        # normally before reads and acceptance, with the identical retained owner.
        for artifact in artifacts:
            captured = NativeGenerationCapture().capture(parent_owner, artifact.leaf,
                max_bytes=len(artifact.data), parent_borrow=lease)
            if (type(captured) is not CapturedGeneration or captured.owner is not parent_owner
                    or captured.parent_identity != identity or captured.leaf != artifact.leaf
                    or captured.normal_completion is not True or captured.release != 'released'
                    or type(captured.data) is not bytes or captured.data != artifact.data):
                raise StoreError('Derived readback differs from same-owner published bytes')
            lease.check()
        if parent_owner.backend != backend or parent_owner.identity != identity:
            raise StoreError('Native export parent continuity was lost')
    except BaseException as error:
        primary = error
        raise
    finally:
        if lease is not None:
            try:
                lease.close()
            except BaseException as cleanup:
                if primary is None:
                    raise
                if isinstance(primary, Exception) and not isinstance(cleanup, Exception):
                    raise cleanup from primary
    return tuple(observations)


def publish_native_exports(parent_owner, prepared):
    if type(prepared) is not PreparedNativeExports:
        raise ValueError('An exact prepared export bundle is required')
    observations = publish_native_artifacts(parent_owner, prepared.artifacts())
    return PublishedNativeExports(prepared, observations)
