"""Bounded native generation reads through an existing registered parent borrow.

This port owns only its opened file. The caller owns and releases the borrow;
no parent path is reopened, no reader limit is changed, and no root is closed.
An independent GenerationRef is still required by RunStore before this I/O.
"""
import os
import stat

from engine.calendar_generation import MAX_GENERATION_BYTES
from engine.calendar_parent_owner import _ExistingParentOwner, _ParentBorrow
from engine.calendar_run_store import CapturedGeneration
from engine.calendar_windows_writer import _leaf


def _bounded_read(read, limit):
    chunks, size = [], 0
    while size <= limit:
        amount = min(65536, limit + 1 - size)
        chunk = read(amount)
        if type(chunk) is not bytes or len(chunk) > amount:
            raise ValueError('Invalid native generation read result')
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    if size > limit:
        raise ValueError('Generation exceeds its independent byte limit')
    return b''.join(chunks)


class NativeGenerationCapture:
    """Read one regular, single-linked file using the same live owner/borrow."""
    def capture(self, owner, leaf, *, max_bytes, parent_borrow):
        if type(owner) is not _ExistingParentOwner or type(parent_borrow) is not _ParentBorrow:
            raise ValueError('A registered existing-parent borrow is required')
        if type(max_bytes) is not int or not 0 <= max_bytes <= MAX_GENERATION_BYTES:
            raise ValueError('Invalid generation capture byte limit')
        _leaf(leaf)
        parent_borrow._require(owner, owner.backend)
        parent_borrow.check()
        identity = owner.identity
        if owner.backend == 'linux':
            data = self._linux(parent_borrow, leaf, max_bytes)
        elif owner.backend == 'windows':
            data = self._windows(parent_borrow, leaf, max_bytes)
        else:
            raise ValueError('Unsupported generation backend; no pathname fallback')
        parent_borrow.check()
        if owner.identity != identity:
            raise ValueError('Retained generation parent identity changed')
        return CapturedGeneration(owner, identity, leaf, data, True, 'released')

    @staticmethod
    def _linux(borrow, leaf, limit):
        from engine.calendar_fs import _close_many, _stamp
        descriptor, primary = None, None
        try:
            descriptor = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                                 dir_fd=borrow.fd)
            before = os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or os.get_inheritable(descriptor)):
                raise ValueError('Generation must be a noninheritable single-linked regular file')
            if before.st_size > limit:
                raise ValueError('Generation exceeds its independent byte limit')
            data = _bounded_read(lambda amount: os.read(descriptor, amount), limit)
            after = os.fstat(descriptor)
            if _stamp(before) != _stamp(after) or len(data) != after.st_size:
                raise ValueError('Generation changed during capture')
            borrow.check()
            return data
        except BaseException as error:
            primary = error
            raise
        finally:
            _close_many([descriptor], primary=primary)

    @staticmethod
    def _windows(borrow, leaf, limit):
        # The adapter belongs to this exact owner. Its existing native reader
        # provides type-neutral no-follow relative opens and noninheritance.
        adapter = borrow._adapter
        native, reader = adapter.native, adapter.reader
        handle, primary = None, None
        try:
            handle = native.open_relative(borrow.value, leaf)
            before = native.inspect(handle.value)
            if before.directory or before.links != 1:
                raise ValueError('Generation must be a single-linked regular file')
            if before.size > limit:
                raise ValueError('Generation exceeds its independent byte limit')
            data = _bounded_read(lambda amount: native.read(handle.value, amount), limit)
            after = native.inspect(handle.value)
            if before != after or len(data) != after.size:
                raise ValueError('Generation changed during capture')
            borrow.check()
            return data
        except BaseException as error:
            primary = error
            raise
        finally:
            reader._close_owned([] if handle is None else [handle], primary=primary)
