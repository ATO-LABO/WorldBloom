"""Handle-based calendar input acquisition. Internal backend contract.

read(relative, optional=False, budget=None) returns immutable InputBytes (or
None only for a missing optional leaf), with a lightweight native observation.
exact_entries validates names/types/identities and full post-read metadata;
exact_tree adds the fixed-version manifest to that same check. Every handle is
owned by its context and closed on success and exception. No pathname fallback.
"""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat

MANIFEST = "execution-version.json"
_open = os.open  # native operation seam for deterministic race tests

@dataclass(frozen=True)
class SourceObservation:
    """Small native post-read token, independent of captured/spooled bytes."""
    backend: str
    native_identity: tuple
    size_bytes: int
    post_read_metadata: object


def _observation(record, backend):
    if isinstance(record, SourceObservation):
        token = record
    elif backend == "posix" and isinstance(record, InputBytes):
        token = record.observation
    elif backend == "windows":
        from engine.calendar_windows_io import _Record
        token = record.observation if isinstance(record, _Record) else None
    else:
        token = None
    if not isinstance(token, SourceObservation) or token.backend != backend:
        raise ValueError("An acquired native source observation for this backend is required")
    if token.post_read_metadata is None:
        raise ValueError("Native post-read metadata is required")
    return token


def _expected_observations(records, backend, policy):
    if policy not in ("all", "yaml"):
        raise ValueError("Unknown native inventory policy")
    expected, identities, aliases = {}, set(), {}
    for name, record in records.items():
        _relative(name)
        if policy == "yaml" and not name.endswith(".yaml"):
            raise ValueError("YAML inventory expects only .yaml files")
        token = _observation(record, backend)
        if token.native_identity in identities:
            raise ValueError("Declared project files have duplicate native identities")
        identities.add(token.native_identity)
        parts = name.split("/")
        for depth in range(1, len(parts) + 1):
            part = "/".join(parts[:depth])
            kind = depth < len(parts)
            prior = aliases.get(part.casefold())
            if prior is not None and prior != (part, kind):
                raise ValueError("Declared project names have aliases or file/directory collisions")
            aliases[part.casefold()] = (part, kind)
        expected[name] = token
    return expected


@dataclass(frozen=True)
class InputBytes:
    data: bytes
    identity: tuple[int, int]
    observation: SourceObservation | None = None

    def manifest(self):
        return {"size_bytes": len(self.data), "sha256": hashlib.sha256(self.data).hexdigest()}


def _close_many(descriptors, *, primary=None):
    """Consume owned fd numbers before close; attempt each once, even on errors."""
    active = primary
    errors = []
    while descriptors:
        descriptor = descriptors.pop()
        if descriptor is not None:
            try:
                os.close(descriptor)
            except BaseException as error:
                errors.append(error)
    if errors:
        candidates = ([] if active is None else [active]) + errors
        failure = next((e for e in candidates if not isinstance(e, Exception)),
                       active if active is not None else errors[0])
        try:
            failure.add_note("Owned POSIX descriptor release is uncertain; each close was attempted once")
        except BaseException:
            pass  # Optional diagnostics must not replace the selected failure.
        for error in errors:
            if error is not failure:
                try:
                    failure.add_note("Descriptor cleanup failure: " + repr(error))
                except BaseException:
                    pass  # Optional diagnostics must not replace the selected failure.
        cause = next((error for error in errors if error is not failure), None)
        if cause is not None:
            raise failure from cause
        raise failure


def _relative(value):
    if (not isinstance(value, str) or not value or len(value) > 4096 or "\\" in value or ":" in value
            or any(ord(c) < 32 for c in value) or PurePosixPath(value).is_absolute()
            or PureWindowsPath(value).drive or any(p in ("", ".", "..") for p in value.split("/"))):
        raise ValueError("An explicit project-relative path without parent/drive aliases is required")
    return value



def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class PosixProjectDirectory:
    """POSIX capability owner. Never fall back to a checked pathname open."""
    def __init__(self, path, *, file_limit):
        self.file_limit = file_limit
        self.fd = None
        if (os.name != "posix" or not all(hasattr(os, flag) for flag in
                ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC", "O_NONBLOCK"))
                or os.open not in os.supports_dir_fd or os.scandir not in os.supports_fd):
            raise ValueError("Secure calendar filesystem backend is unsupported on this platform; no pathname fallback")
        self.path = Path(path).absolute()
        if self.path.anchor != "/":
            raise ValueError("Calendar root must use an absolute local path")
        ancestors = [_open("/", self._directory_flags())]
        try:
            # '..' selects the retained logical parent, never the current
            # filesystem parent of a directory that another writer may move.
            # A preceding link still has to pass the no-follow open first.
            for component in self.path.parts[1:]:
                if component == "..":
                    if len(ancestors) > 1:
                        os.close(ancestors.pop())
                else:
                    ancestors.append(_open(component, self._directory_flags(), dir_fd=ancestors[-1]))
            # Immutable observations for admission policy, captured from the
            # same no-follow ancestry walk. Not a retained ancestry guarantee.
            self.ancestry = tuple((info.st_dev, info.st_ino)
                                  for info in map(os.fstat, ancestors))
            selected = ancestors.pop()
            try:
                _close_many(ancestors)
            except BaseException as error:
                _close_many([selected], primary=error)
                raise
            self.fd = selected
        except BaseException as error:
            _close_many(ancestors, primary=error)
            raise

    @staticmethod
    def _directory_flags():
        return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC

    def __enter__(self):
        return self

    def __exit__(self, exc_type=None, exc=None, tb=None):
        if self.fd is not None:
            descriptor, self.fd = self.fd, None
            _close_many([descriptor], primary=exc)

    def _parent(self, relative):
        parts = _relative(relative).split("/")
        descriptor = os.dup(self.fd)
        try:
            for component in parts[:-1]:
                following = _open(component, self._directory_flags(), dir_fd=descriptor)
                previous, descriptor = descriptor, following
                os.close(previous)
            return descriptor, parts[-1]
        except BaseException as error:
            _close_many([descriptor], primary=error)
            raise

    def read(self, relative, *, optional=False, budget=None):
        parent, leaf = self._parent(relative)
        descriptor = None
        primary = None
        try:
            try:
                descriptor = _open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                                     dir_fd=parent)
            except FileNotFoundError:
                if optional:
                    return None
                raise
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("Calendar input must be a regular file")
            if before.st_nlink != 1:
                raise ValueError("Multiply-linked calendar inputs (hardlinks) are rejected; use an ordinary independent file copy")
            limit = self.file_limit if budget is None else min(self.file_limit, budget)
            if before.st_size > limit:
                raise ValueError("Calendar input exceeds its file or total byte limit")
            chunks, length = [], 0
            while length <= limit:
                chunk = os.read(descriptor, min(65536, limit + 1 - length))
                if not chunk:
                    break
                chunks.append(chunk)
                length += len(chunk)
            after = os.fstat(descriptor)
            if length > limit:
                raise ValueError("Calendar input exceeds its file or total byte limit")
            if _stamp(before) != _stamp(after) or length != after.st_size:
                raise ValueError("Calendar input changed during collection")
            return InputBytes(b"".join(chunks), (after.st_dev, after.st_ino),
                              SourceObservation("posix", (after.st_dev, after.st_ino),
                                                after.st_size, _stamp(after)))
        except BaseException as error:
            primary = error
            raise
        finally:
            _close_many([parent, descriptor], primary=primary)

    def exact_tree(self, files, version):
        if MANIFEST in files:
            raise ValueError("Version manifest cannot also be a declared input")
        self.exact_entries({**files, MANIFEST: version})

    def exact_entries(self, observations, *, policy="all"):
        """Validate native tokens; YAML policy ignores only safe non-YAML files.

        Empty directories are allowed by YAML inventory. All traversed objects
        still pass native no-link/type checks, without reading their contents.
        """
        expected = _expected_observations(observations, "posix", policy)
        directories = {p.as_posix() for name in expected for p in PurePosixPath(name).parents if p.as_posix() != "."}
        observed = set()
        def walk(descriptor, prefix):
            wanted = {name[len(prefix):].split("/")[0] for name in expected if name.startswith(prefix)}
            before = os.fstat(descriptor)
            found = set()
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    if entry.name in found or (policy == "all" and entry.name not in wanted):
                        raise ValueError("Fixed version file/directory set differs from declared dependencies")
                    found.add(entry.name)
            if policy == "all" and found != wanted:
                raise ValueError("Fixed version file/directory set differs from declared dependencies")
            for leaf in sorted(found):
                name = prefix + leaf
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
                if name in directories:
                    flags |= os.O_DIRECTORY
                child = _open(leaf, flags, dir_fd=descriptor)
                primary = None
                try:
                    info = os.fstat(child)
                    if stat.S_ISDIR(info.st_mode):
                        if name in expected:
                            raise ValueError("Declared input is a directory")
                        walk(child, name + "/")
                    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise ValueError("Linked or unsupported project entry")
                    elif policy == "all" or name.endswith(".yaml"):
                        token = expected.get(name)
                        if (token is None or (info.st_dev, info.st_ino) != token.native_identity
                                or info.st_size != token.size_bytes or _stamp(info) != token.post_read_metadata):
                            raise ValueError("Project file metadata changed or inventory differs after collection")
                        observed.add(name)
                except BaseException as error:
                    primary = error
                    raise
                finally:
                    _close_many([child], primary=primary)
            if _stamp(before) != _stamp(os.fstat(descriptor)):
                raise ValueError("Fixed version directory changed during collection")
        walk(self.fd, "")
        if observed != set(expected):
            raise ValueError("Project directory has missing declared entries")




def open_project_directory(path, *, file_limit):
    if os.name == "nt":
        from engine.calendar_windows_io import WindowsProjectDirectory
        return WindowsProjectDirectory(path, file_limit=file_limit)
    return PosixProjectDirectory(path, file_limit=file_limit)


def directory_ancestry(directory):
    """Admission observations from the acquired no-follow directory walk.

    These are not stable path-containment or same-UID isolation promises.
    Native identities compare acquired roots/ancestors, including case/short-name
    aliases; they do not prove arbitrary bind-mount descendant containment.
    """
    if type(directory) is PosixProjectDirectory:
        if directory.fd is None:
            raise ValueError('Directory is closed')
        info = os.fstat(directory.fd)
        if (info.st_dev, info.st_ino) != directory.ancestry[-1]:
            raise ValueError('Directory identity changed')
        return directory.ancestry
    from engine.calendar_windows_io import WindowsProjectDirectory
    if type(directory) is WindowsProjectDirectory:
        directory._check_anchors()
        return tuple(identity for _handle, identity in directory._anchors)
    raise ValueError('Native directory ancestry is required')
