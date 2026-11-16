"""Private, process-local retained output parent. No application routing.

An owner selects an existing native directory once. Writers own only a borrow
and their newly created file. There is no path conversion, handle transfer,
restart authority, directory creation, or close-time name cleanup here.
"""
import os
import stat
import sys
import threading

from engine.calendar_fs import PosixProjectDirectory, directory_ancestry


def _diagnose(error, state, note=None):
    # Diagnostics are optional even for hostile exception implementations.
    try:
        error.parent_release_state = state
    except BaseException:
        pass
    if note is not None:
        try:
            error.add_note(note)
        except BaseException:
            pass


def _cleanup_failure(primary, failure, state):
    selected = primary
    if failure is not None and (primary is None or
            (isinstance(primary, Exception) and not isinstance(failure, Exception))):
        selected = failure
    if selected is not None:
        _diagnose(selected, state, 'Parent cleanup also failed; inspect release state'
                  if failure is not None and primary is not None else None)
    if selected is not primary:
        if primary is None:
            raise selected
        raise selected from primary


class _ExistingParentOwner:
    """Own one native directory selection; close requires zero active borrows.

    ``adapter`` is a private Windows test seam, not a backend fallback. The
    actual Windows adapter retains its original volume/ancestor ledger.
    Identity is a native observation, not an authenticity or restart token.
    """
    def __init__(self, existing_parent, *, backend, adapter=None):
        self._pid = os.getpid()
        self._lock = threading.RLock()
        self._epoch = object()
        self._borrows = set()
        self._parent = None
        self._identity = None
        self._state = 'CLOSED'
        self._backend = backend
        self._adapter = adapter
        self._unreturned_acquisition = False
        if backend not in ('linux', 'windows'):
            raise ValueError('Unsupported parent backend; no fallback')
        if backend == 'linux' and (adapter is not None or sys.platform != 'linux'):
            raise ValueError('Linux parent requires the native Linux backend')
        if backend == 'windows' and adapter is None:
            from engine.calendar_windows_writer import NativeWindowsAdapter
            self._adapter = NativeWindowsAdapter()
        try:
            self._unreturned_acquisition = True
            if backend == 'linux':
                self._parent = PosixProjectDirectory(existing_parent, file_limit=0)
                self._unreturned_acquisition = False
                info = os.fstat(self._parent.fd)
                self._identity = (info.st_dev, info.st_ino)
            else:
                self._parent = self._adapter.open_existing_parent(existing_parent)
                self._unreturned_acquisition = False
                self._identity = self._parent.identity
            self._check_native()
            self._state = 'OPEN'
        except BaseException as primary:
            self._close_preserving(primary)
            raise

    def __copy__(self):
        raise TypeError('Parent capabilities cannot be copied')

    def __deepcopy__(self, memo):
        raise TypeError('Parent capabilities cannot be copied')

    def __reduce_ex__(self, protocol):
        raise TypeError('Parent capabilities cannot be serialized or transferred')

    @property
    def backend(self):
        return self._backend

    @property
    def identity(self):
        return self._identity

    @property
    def state(self):
        return self._state

    @property
    def ancestry(self):
        """Acquisition-time admission observations, not reopenable authority."""
        self._require_open(self._backend)
        self._check_native()
        directory = self._parent if self._backend == 'linux' else self._parent.project
        return directory_ancestry(directory)

    @property
    def active_borrows(self):
        self._require_process()
        with self._lock:
            return len(self._borrows)

    def _require_process(self):
        # Check before acquiring a potentially inherited lock after fork.
        if os.getpid() != self._pid:
            raise ValueError('Parent capability cannot be used or closed in another process')

    def _check_native(self):
        self._require_process()
        if self._parent is None:
            raise ValueError('Parent has no owned native resource')
        if self._backend == 'linux':
            info = os.fstat(self._parent.fd)
            if (not stat.S_ISDIR(info.st_mode)
                    or (info.st_dev, info.st_ino) != self._identity
                    or os.get_inheritable(self._parent.fd)):
                raise ValueError('Retained parent type, identity or noninheritance changed')
        else:
            self._parent.check()
            if self._parent.identity != self._identity:
                raise ValueError('Retained parent identity changed')

    def _require_open(self, backend):
        self._require_process()
        if self._state != 'OPEN' or backend != self._backend:
            raise ValueError('Parent is not open for the required backend')

    def _borrow(self, backend):
        self._require_process()
        with self._lock:
            self._require_open(backend)
            self._check_native()
            borrow = _ParentBorrow(self, self._epoch)
            self._borrows.add(borrow)
            return borrow

    def _validate(self, borrow, backend):
        self._require_process()
        with self._lock:
            self._require_open(backend)
            if (type(borrow) is not _ParentBorrow or borrow._owner is not self
                    or borrow._epoch is not self._epoch or borrow._released
                    or borrow not in self._borrows):
                raise ValueError('Wrong-owner, stale or released parent borrow')
            return self._parent

    def _release(self, borrow):
        self._require_process()
        with self._lock:
            self._validate(borrow, self._backend)
            self._borrows.remove(borrow)
            borrow._released = True

    def close(self):
        self._require_process()
        with self._lock:
            if self._borrows:
                raise ValueError('Cannot close parent with active borrows')
            if self._state == 'CLOSING':
                raise ValueError('Parent close is already in progress')
            if self._parent is None:
                if self._unreturned_acquisition:
                    self._state = 'RELEASE_UNCERTAIN'
                return self._state
            parent, self._parent = self._parent, None
            self._state = 'CLOSING'
            self._epoch = object()
            try:
                if self._backend == 'linux':
                    parent.__exit__()
                else:
                    parent.close()
            except BaseException as error:
                self._state = 'RELEASE_UNCERTAIN'
                _diagnose(error, self._state)
                raise
            self._state = 'CLOSED'
            return self._state

    def _close_preserving(self, primary):
        failure = None
        try:
            self.close()
        except BaseException as error:
            failure = error
        _cleanup_failure(primary, failure, self._state)

    def __enter__(self):
        try:
            self._require_open(self._backend)
            self._check_native()
        except BaseException as primary:
            self._close_preserving(primary)
            raise
        return self

    def __exit__(self, exc_type=None, exc=None, tb=None):
        self._close_preserving(exc)

    def __del__(self):
        try:
            self.close()
        except BaseException:
            pass  # Explicit close/context reports; never retry a detached handle.


class _ParentBorrow:
    """Opaque registration, with no native ownership and no path interface."""
    def __init__(self, owner, epoch):
        self._owner = owner
        self._epoch = epoch
        self._released = False

    def __copy__(self):
        raise TypeError('Parent borrows cannot be copied')

    def __deepcopy__(self, memo):
        raise TypeError('Parent borrows cannot be copied')

    def __reduce_ex__(self, protocol):
        raise TypeError('Parent borrows cannot be serialized or transferred')

    def _require(self, owner, backend):
        if owner is not self._owner:
            raise ValueError('Parent borrow belongs to a different owner')
        return owner._validate(self, backend)

    @property
    def identity(self):
        self._require(self._owner, self._owner.backend)
        return self._owner.identity

    @property
    def fd(self):
        return self._require(self._owner, 'linux').fd

    @property
    def value(self):
        return self._require(self._owner, 'windows').value

    @property
    def _adapter(self):
        self._require(self._owner, 'windows')
        return self._owner._adapter

    def check(self):
        self._require(self._owner, self._owner.backend)
        self._owner._check_native()

    def close(self):
        self._owner._require_process()
        if not self._released:
            self._owner._release(self)

    def __exit__(self, exc_type=None, exc=None, tb=None):
        self.close()
