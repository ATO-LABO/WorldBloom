"""Private Linux-only atomic no-clobber container publication prototype."""
import ctypes
from dataclasses import dataclass, replace
import hashlib
import os
import sys

from engine.calendar_fs import PosixProjectDirectory, _relative
from engine.calendar_spool import CHUNK, integer


@dataclass(frozen=True)
class PublicationReceipt:
    final_leaf: str
    expected_size: int
    expected_sha256: str
    parent_identity: tuple
    object_identity: tuple
    publication: str = 'not_attempted'
    durability: str = 'unconfirmed'
    commit_mode: str = 'atomic_namespace'
    release: str = 'open'
    completion: str = 'pending'
    capture_release: str = 'not_owned'
    release_scope: str = 'file_and_parent'
    file_release: str = 'open'
    borrow_release: str = 'not_borrowed'
    retained_parent: bool = False


class PublicationError(OSError):
    def __init__(self, message, receipt):
        super().__init__(message)
        self.receipt = receipt


def _failure_diagnostics(error, receipt, note=None):
    """Optional metadata must never replace the selected failure."""
    try:
        error.receipt = receipt
    except BaseException:
        pass
    if note is not None:
        _failure_note(error, note)


def _failure_note(error, note):
    try:
        error.add_note(note)
    except BaseException:
        pass


class LinuxSnapshot:
    def __init__(self, existing_parent, final_leaf, *, expected_size, expected_sha256, max_bytes):
        self._initialize(final_leaf, expected_size, expected_sha256, max_bytes)
        try:
            self._parent = PosixProjectDirectory(existing_parent, file_limit=max_bytes)
            self._acquire_file(final_leaf, expected_size, expected_sha256)
        except BaseException as error:
            self._close_preserving_error(error)
            raise

    @classmethod
    def _from_parent_owner(cls, owner, final_leaf, *, expected_size, expected_sha256, max_bytes):
        from engine.calendar_parent_owner import _ExistingParentOwner
        if type(owner) is not _ExistingParentOwner:
            raise ValueError('An existing parent owner is required')
        writer = cls.__new__(cls)
        writer._initialize(final_leaf, expected_size, expected_sha256, max_bytes)
        writer._borrowed = True
        writer._owner = owner
        try:
            writer._parent = owner._borrow('linux')
            writer._acquire_file(final_leaf, expected_size, expected_sha256)
            return writer
        except BaseException as error:
            writer._close_preserving_error(error)
            raise

    def _initialize(self, final_leaf, expected_size, expected_sha256, max_bytes):
        self._fd = None
        self._parent = None
        self._borrowed = False
        self._owner = None
        self._written = 0
        self._attempted = False
        self._failed = False
        self.receipt = None
        integer(max_bytes, (1 << 64) - 1, 'writer budget')
        integer(expected_size, max_bytes, 'expected byte count')
        if (type(expected_sha256) is not str or len(expected_sha256) != 64
                or any(c not in '0123456789abcdef' for c in expected_sha256)):
            raise ValueError('Invalid expected digest')
        if '/' in _relative(final_leaf):
            raise ValueError('Publication requires one leaf')
        if sys.platform != 'linux' or not hasattr(os, 'O_TMPFILE'):
            raise ValueError('Linux O_TMPFILE publication unsupported; no fallback')

    def _acquire_file(self, final_leaf, expected_size, expected_sha256):
        self._check_borrow()
        self._fd = os.open('.', os.O_TMPFILE | os.O_RDWR | os.O_CLOEXEC, 0o600, dir_fd=self._parent.fd)
        parent, created = os.fstat(self._parent.fd), os.fstat(self._fd)
        self.receipt = PublicationReceipt(final_leaf, expected_size, expected_sha256,
                                          (parent.st_dev, parent.st_ino), (created.st_dev, created.st_ino),
                                          release_scope='file_and_borrow' if self._borrowed else 'file_and_parent',
                                          borrow_release='held' if self._borrowed else 'not_borrowed',
                                          retained_parent=self._borrowed)

    def _check_borrow(self):
        if self._borrowed:
            if self._parent is None:
                raise ValueError('Writer has no live parent borrow')
            self._parent._require(self._owner, 'linux')
            self._parent.check()

    def write_all(self, chunks):
        if self._fd is None or self._attempted or self._failed:
            raise ValueError('Writer is closed, failed or already sealed')
        try:
            self._check_borrow()
            for data in chunks:
                self._check_borrow()
                if type(data) is not bytes or len(data) > CHUNK or len(data) > self.receipt.expected_size - self._written:
                    raise ValueError('Invalid or excessive output chunk')
                remaining = memoryview(data)
                while remaining:
                    self._check_borrow()
                    count = os.write(self._fd, remaining)
                    if count <= 0:
                        raise OSError('Short container write')
                    remaining = remaining[count:]
                    self._written += count
        except BaseException:
            self._failed = True
            raise

    def seal(self):
        if self._fd is None or self._attempted or self._failed:
            raise ValueError('Writer is closed, failed or already sealed')
        self._attempted = True
        try:
            self._check_borrow()
            info = os.fstat(self._fd)
            if (self._written != self.receipt.expected_size or info.st_size != self._written
                    or (info.st_dev, info.st_ino) != self.receipt.object_identity):
                raise ValueError('Container size/identity differs from expected output')
            digest = hashlib.sha256()
            position = 0
            while position < self._written:
                data = os.pread(self._fd, min(CHUNK, self._written - position), position)
                if not data:
                    raise ValueError('Truncated container verification read')
                digest.update(data)
                position += len(data)
            if digest.hexdigest() != self.receipt.expected_sha256:
                raise ValueError('Container differs from expected digest')
            os.fsync(self._fd)
            self.receipt = replace(self.receipt, durability='data_flushed')
            # If a call raises with uncertain completion, do not infer absence.
            self.receipt = replace(self.receipt, publication='unknown')
            try:
                _link_unnamed(self._fd, self._parent.fd, os.fsencode(self.receipt.final_leaf))
            except FileExistsError:
                self.receipt = replace(self.receipt, publication='conflict')
                raise
            self.receipt = replace(self.receipt, publication='published')
            try:
                os.fsync(self._parent.fd)
            except BaseException:
                self.receipt = replace(self.receipt, durability='failed_after_publication')
                raise
            self.receipt = replace(self.receipt, durability='namespace_flush_confirmed', completion='sealed')
            return self.receipt
        except (OSError, ValueError) as error:
            self.receipt = replace(self.receipt, completion='failed')
            raise PublicationError(str(error), self.receipt) from error

    def close(self):
        if self._owner is not None:
            self._owner._require_process()
        errors = []
        file_error = parent_error = False
        had_file = self._fd is not None
        had_parent = self._parent is not None
        if self._fd is not None:
            descriptor, self._fd = self._fd, None
            try:
                os.close(descriptor)
            except BaseException as error:
                file_error = True
                errors.append(error)
        if self._parent is not None:
            parent, self._parent = self._parent, None
            try:
                parent.__exit__()
            except BaseException as error:
                parent_error = True
                errors.append(error)
        if self.receipt is not None:
            state = 'uncertain' if errors or self.receipt.release == 'uncertain' else 'closed'
            completion = 'unsealed' if self.receipt.completion == 'pending' else self.receipt.completion
            file_release = ('uncertain' if file_error else 'closed') if had_file else self.receipt.file_release
            borrow_release = self.receipt.borrow_release
            if self._borrowed and had_parent:
                borrow_release = 'uncertain' if parent_error else 'released'
            self.receipt = replace(self.receipt, release=state, completion=completion,
                                   file_release=file_release, borrow_release=borrow_release)
        if errors:
            cancellation = next((e for e in errors if not isinstance(e, Exception)), None)
            if cancellation is not None:
                _failure_diagnostics(cancellation, self.receipt)
                raise cancellation
            try:
                message = str(errors[0])
            except BaseException:
                message = 'Output resource release failed'
            raise PublicationError(message, self.receipt) from errors[0]

    def _close_preserving_error(self, primary):
        """Consume ownership once without replacing an earlier cancellation."""
        failure = None
        try:
            self.close()
        except BaseException as error:
            failure = error
        # Select before attaching even optional metadata to either error.
        if failure is None or (primary is not None and
                               (not isinstance(primary, Exception) or isinstance(failure, Exception))):
            if primary is not None:
                note = 'Output close also failed; release is uncertain' if failure is not None else None
                _failure_diagnostics(primary, self.receipt, note)
            return
        if primary is None or failure is primary:
            raise failure
        raise failure from primary

    def __enter__(self):
        try:
            if self._fd is None:
                raise ValueError('Closed writer')
            self._check_borrow()
            return self
        except BaseException as error:
            self._close_preserving_error(error)
            raise

    def __exit__(self, exc_type=None, exc=None, _traceback=None):
        self._close_preserving_error(exc)
        if exc_type is None and self.receipt.completion != 'sealed':
            raise PublicationError('Writer exited without successful seal', self.receipt)

    def __del__(self):
        try:
            self.close()
        except BaseException:
            pass  # explicit close/context reports failures; never retry fd numbers


def _link_unnamed(source_fd, destination_fd, name):
    # Linux linkat(2), AT_EMPTY_PATH. The source is exactly our retained fd;
    # a pre-existing destination makes linkat fail with EEXIST atomically.
    library = ctypes.CDLL(None, use_errno=True)
    try:
        linkat = library.linkat
    except AttributeError as error:
        raise ValueError('Native handle-bound publication is unavailable') from error
    linkat.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int)
    linkat.restype = ctypes.c_int
    if linkat(source_fd, b'', destination_fd, name, 0x1000) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
