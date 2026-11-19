"""Private generation coordinator; NOT connected to runtime, CLI, jobs or UI.

The caller owns the root. This store retains one session borrow; each writer
owns its own borrow. Capture is injected and must use the identical owner.
No pathname fallback, latest selection, persistent witness, deletion or migration.
Synthetic ports are not native qualification. Callers exclusively own simulation
state while serializing it. Source/provider/Python/OS compatibility is unchanged.
"""
from dataclasses import dataclass
import os
import secrets
import threading

from engine.calendar_checkpoint import strict_json
from engine.calendar_generation import (
    GenerationError, GenerationRef, SelectedGeneration, PreparedGeneration,
    MAX_GENERATION_BYTES, checkpoint_bytes, prepare_generation, decode_generation,
    _stream,
)
from execution.provenance import canonical


class StoreError(ValueError):
    pass


class ConcurrentStoreOperation(StoreError):
    pass


@dataclass(frozen=True)
class CapturedGeneration:
    owner: object
    parent_identity: tuple
    leaf: str
    data: bytes
    normal_completion: bool
    release: str


@dataclass(frozen=True)
class CommitObservation:
    backend: str
    parent_identity: tuple
    object_identity: tuple
    commit_mode: str
    visibility_mode: str
    data_flush: str
    namespace_durability: str
    release_scope: str
    file_release: str
    borrow_release: str
    retained_parent_at_acquisition: bool
    native_validation: str


@dataclass(frozen=True)
class AcceptedGeneration:
    prepared: PreparedGeneration
    observation: CommitObservation

    @property
    def ref(self):
        return self.prepared.ref

    def selection(self):
        return self.prepared.selection()


class NativeWriterPort:
    """Lazy bridge to the separately reviewed owner/borrow writer increment.

    Missing factories are deliberately rejected without a pathname fallback.
    Windows native qualification remains an independent product/release gate.
    """
    def open(self, owner, leaf, *, expected_size, expected_sha256, max_bytes):
        if owner.backend == 'linux':
            from engine.calendar_container_linux import LinuxSnapshot
            cls = LinuxSnapshot
        elif owner.backend == 'windows':
            from engine.calendar_windows_writer import WindowsDirectSnapshot
            cls = WindowsDirectSnapshot
        else:
            raise StoreError('Unsupported output backend; no pathname fallback')
        factory = getattr(cls, '_from_parent_owner', None)
        if factory is None:
            raise StoreError('Run-wide parent writer adapter is not integrated')
        return factory(owner, leaf, expected_size=expected_size,
                       expected_sha256=expected_sha256, max_bytes=max_bytes)


def _identity(value):
    if type(value) is not tuple or not value or any(type(v) not in (int, str, bytes) for v in value):
        raise StoreError('Invalid retained native identity')
    return value


def _receipt(receipt, *, backend, parent_identity, prepared):
    return validate_final_receipt(receipt, backend=backend, parent_identity=parent_identity,
                                  expected_leaf=prepared.leaf, expected_size=prepared.ref.size,
                                  expected_sha256=prepared.ref.sha256)


def validate_final_receipt(receipt, *, backend, parent_identity, expected_leaf,
                           expected_size, expected_sha256):
    """Validate final native evidence for exact immutable bytes, after close.

    Shared by generation saves and derived exports. This is observation, not
    restart authority, a successful readback, or native platform qualification.
    """
    from engine.calendar_generation import _digest
    _identity(parent_identity)
    if (type(expected_leaf) is not str or not expected_leaf
            or type(expected_size) is not int or not 0 <= expected_size <= MAX_GENERATION_BYTES):
        raise StoreError('Invalid expected native receipt binding')
    _digest(expected_sha256)
    if (receipt.parent_identity != parent_identity or receipt.final_leaf != expected_leaf
            or type(receipt.expected_size) is not int or receipt.expected_size != expected_size
            or receipt.expected_sha256 != expected_sha256
            or receipt.release_scope != 'file_and_borrow'
            or receipt.borrow_release != 'released' or receipt.retained_parent is not True):
        raise StoreError('Final receipt does not bind this borrowed-parent attempt')
    object_id = _identity(receipt.object_identity)
    if backend == 'linux':
        if (receipt.publication != 'published' or receipt.completion != 'sealed'
                or receipt.durability != 'namespace_flush_confirmed'
                or receipt.release != 'closed' or receipt.file_release != 'closed'
                or receipt.commit_mode != 'atomic_namespace'):
            raise StoreError('Linux publication or release is unconfirmed')
        return CommitObservation(backend, parent_identity, object_id, receipt.commit_mode,
                                 'atomic_appearance', 'confirmed', 'namespace_flush_confirmed',
                                 receipt.release_scope, receipt.file_release, receipt.borrow_release,
                                 True, 'NOT_ESTABLISHED_BY_COORDINATOR')
    if backend == 'windows':
        if (receipt.phase != 'released' or receipt.creation != 'created'
                or receipt.expected_bytes_verified is not True or receipt.data_flush != 'confirmed'
                or type(receipt.bytes_written) is not int or receipt.bytes_written != expected_size
                or receipt.resources_released != 'confirmed' or receipt.file_release != 'confirmed'
                or receipt.commit_mode != 'validity_based' or receipt.visibility_mode != 'visible_during_write'
                or receipt.namespace_durability != 'not_established'):
            raise StoreError('Windows validity or release is unconfirmed')
        return CommitObservation(backend, parent_identity, object_id, receipt.commit_mode,
                                 receipt.visibility_mode, receipt.data_flush, receipt.namespace_durability,
                                 receipt.release_scope, receipt.file_release, receipt.borrow_release,
                                 True, receipt.native_validation)
    raise StoreError('Unsupported output backend')


class RunStore:
    def __init__(self, parent_owner, writer_port, capture_port, expected_compatibility, *, stream_id=None):
        if type(expected_compatibility) is not dict:
            raise StoreError('Expected compatibility must be an object')
        self._compatibility = canonical(expected_compatibility)
        self._stream = _stream(secrets.token_hex(16) if stream_id is None else stream_id)
        self._owner = parent_owner
        self._backend = parent_owner.backend
        if self._backend not in ('linux', 'windows') or parent_owner.state != 'OPEN':
            raise StoreError('An open supported parent owner is required')
        self._parent_id = _identity(parent_owner.identity)
        self._writer_port, self._capture_port = writer_port, capture_port
        self._pid = os.getpid()
        self._gate = threading.Lock()
        self._state = 'NEW'
        self._head = None
        self._base = None
        self._last_prepared = None
        self._lease = parent_owner._borrow(self._backend)
        self._session_release = 'held'

    def __copy__(self):
        raise TypeError('RunStore owns one session borrow and cannot be copied')

    def __deepcopy__(self, memo):
        raise TypeError('RunStore owns one session borrow and cannot be copied')

    def __reduce_ex__(self, protocol):
        raise TypeError('RunStore ownership cannot be serialized or transferred')

    @property
    def state(self):
        return self._state

    @property
    def session_release(self):
        return self._session_release

    @property
    def head(self):
        return self._head

    @property
    def last_attempt(self):
        return self._last_prepared

    @property
    def selected_base(self):
        return None if self._base is None else self._base.ref

    def _expected(self):
        return strict_json(self._compatibility.decode('utf-8'))

    def _enter_operation(self):
        # Check before a potentially inherited lock; no cross-process transfer.
        if os.getpid() != self._pid:
            raise StoreError('RunStore cannot cross a process boundary')
        if not self._gate.acquire(blocking=False):
            raise ConcurrentStoreOperation('Another store operation is in progress')
        try:
            if self._state in ('STOPPED', 'CLOSED'):
                raise StoreError('Store is stopped or closed')
            if (self._owner.state != 'OPEN' or self._owner.backend != self._backend
                    or self._owner.identity != self._parent_id or self._lease is None):
                self._state = 'STOPPED'
                raise StoreError('Run-wide parent continuity was lost')
        except BaseException:
            self._gate.release()
            raise

    def _prepare(self, encoded):
        previous = None if self._head is None else self._head.ref
        ordinal = 0 if previous is None else previous.ordinal + 1
        forked = self._base.ref if previous is None and self._base is not None else None
        return prepare_generation(encoded, expected_compatibility=self._expected(),
                                  stream_id=self._stream, ordinal=ordinal,
                                  previous=previous, forked_from=forked)

    def _commit(self, prepared):
        # Set before invoking anything that may acquire/publish an output object.
        self._last_prepared = prepared
        self._state = 'COMMITTING'
        try:
            writer = self._writer_port.open(self._owner, prepared.leaf,
                                           expected_size=prepared.ref.size,
                                           expected_sha256=prepared.ref.sha256,
                                           max_bytes=MAX_GENERATION_BYTES)
            body_completed = False
            body_error = None
            try:
                with writer as entered:
                    try:
                        if entered is not writer:
                            raise StoreError('Writer context returned an unrelated object')
                        writer.write_all(prepared.data[i:i + 65536] for i in range(0, len(prepared.data), 65536))
                        writer.seal()  # Returned receipt is pre-close and is never accepted.
                        body_completed = True
                    except BaseException as error:
                        body_error = error
                        raise
            except BaseException as context_error:
                if body_error is not None and (not isinstance(body_error, Exception) or isinstance(context_error, Exception)):
                    raise body_error
                raise
            if body_error is not None:
                raise body_error  # A context manager must not suppress cancellation/failure.
            if not body_completed:
                raise StoreError('Writer did not complete normally')
            observation = _receipt(writer.receipt, backend=self._backend,
                                   parent_identity=self._parent_id, prepared=prepared)
            if self._owner.state != 'OPEN' or self._owner.identity != self._parent_id:
                raise StoreError('Parent continuity failed before acceptance')
            accepted = AcceptedGeneration(prepared, observation)
            self._head = accepted
            self._state = 'ACTIVE'
            return accepted
        except BaseException:
            self._state = 'STOPPED'
            raise

    def initialize(self, state):
        self._enter_operation()
        try:
            if self._state != 'NEW':
                raise StoreError('Initialize requires a new store')
            prepared = self._prepare(checkpoint_bytes(state, self._expected()))
            return self._commit(prepared)
        except BaseException as error:
            if not isinstance(error, Exception):
                self._state = 'STOPPED'
            raise
        finally:
            self._gate.release()

    def save(self, state):
        self._enter_operation()
        try:
            if self._state != 'ACTIVE':
                raise StoreError('Save requires an initialized or explicitly continued store')
            prepared = self._prepare(checkpoint_bytes(state, self._expected()))
            return self._commit(prepared)
        except BaseException as error:
            if not isinstance(error, Exception):
                self._state = 'STOPPED'
            raise
        finally:
            self._gate.release()

    def _capture(self, selection):
        if type(selection) is not SelectedGeneration:
            raise GenerationError('Explicit selected generation required')
        selection.validate()  # Reject malformed selection before capture I/O.
        lease = None
        primary = None
        try:
            lease = self._owner._borrow(self._backend)
            captured = self._capture_port.capture(self._owner, selection.leaf,
                                                  max_bytes=selection.expected.size,
                                                  parent_borrow=lease)
            if (type(captured) is not CapturedGeneration or captured.owner is not self._owner
                    or captured.parent_identity != self._parent_id or captured.leaf != selection.leaf
                    or captured.normal_completion is not True or captured.release != 'released'
                    or type(captured.data) is not bytes
                    or len(captured.data) > selection.expected.size
                    or self._owner.state != 'OPEN' or self._owner.identity != self._parent_id):
                raise StoreError('Capture did not complete through the same retained parent')
        except BaseException as error:
            primary = error
            self._state = 'STOPPED'
            raise
        finally:
            if lease is not None:
                try:
                    lease.close()
                except BaseException as cleanup:
                    self._state = 'STOPPED'
                    if primary is None:
                        raise
                    if isinstance(primary, Exception) and not isinstance(cleanup, Exception):
                        raise cleanup from primary
                    # Otherwise preserve the already propagating primary failure.
        return decode_generation(captured.data, expected=selection.expected,
                                 expected_compatibility=self._expected())

    def load_selected(self, selection):
        self._enter_operation()
        try:
            return self._capture(selection)
        except BaseException as error:
            if not isinstance(error, Exception):
                self._state = 'STOPPED'
            raise
        finally:
            self._gate.release()

    def continue_from(self, selection, *, new_stream_id=None):
        self._enter_operation()
        try:
            if self._state != 'NEW':
                raise StoreError('Continuation requires a new store')
            stream = _stream(self._stream if new_stream_id is None else new_stream_id)
            if type(selection) is not SelectedGeneration:
                raise GenerationError('Explicit selected generation required')
            selection.validate()
            if stream == selection.expected.stream_id:
                raise GenerationError('Continuation must use a new stream')
            base = self._capture(selection)
            state = base.state(self._expected())
            self._base = base
            self._stream = stream
            self._state = 'ACTIVE'
            # Resume itself adds no save callback. First existing callback writes
            # ordinal zero in the new stream; a no-op resume can use the base view.
            return state
        except BaseException as error:
            if not isinstance(error, Exception):
                self._state = 'STOPPED'
            raise
        finally:
            self._gate.release()

    def finish(self):
        if os.getpid() != self._pid:
            raise StoreError('RunStore cannot cross a process boundary')
        if not self._gate.acquire(blocking=False):
            raise ConcurrentStoreOperation('Cannot finish while an operation is active')
        try:
            lease, self._lease = self._lease, None  # Detach before fallible release.
            if lease is not None:
                try:
                    lease.close()
                except BaseException:
                    self._session_release = 'uncertain'
                    self._state = 'STOPPED'
                    raise
                self._session_release = 'released'
            if self._session_release != 'uncertain':
                self._state = 'CLOSED'
        finally:
            self._gate.release()

    def __enter__(self):
        self._enter_operation()
        self._gate.release()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self.finish()
        except BaseException as cleanup:
            if exc is not None and (not isinstance(exc, Exception) or isinstance(cleanup, Exception)):
                return False
            if exc is not None and cleanup is not exc:
                raise cleanup from exc
            raise
        return False
