"""Private, scoped calendar input capture. Not a public route or save API.

Native observations describe source objects; sealed ranges describe accepted
bytes. Neither is an atomic multi-file snapshot or same-UID process isolation.
The caller owns this context. Display paths never authorize downstream reads.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import tempfile
from weakref import WeakKeyDictionary

from engine.calendar_fs import open_project_directory, directory_ancestry
from engine.calendar_inputs import (FILE_LIMIT, MANIFEST, _canonical, _json_text,
    _relative, _validate_fixed_values, _validation_boundary, _yaml_text,
    validate_execution_config)
from engine.purpose_preparation_runtime import FILENAME as PREPARATION, MAX_BYTES, decode_record


def _spool(*, max_bytes, parent):
    if os.name == 'nt':
        from engine.calendar_windows_spool import WindowsPrivateSpool
        return WindowsPrivateSpool(max_bytes=max_bytes, parent=parent)
    from engine.calendar_spool import PrivateSpool
    return PrivateSpool(max_bytes=max_bytes, parent=tempfile.gettempdir() if parent is None else parent)


def _raise_cleanup(error, cleanup, label):
    """Keep the first cancellation, otherwise prefer cleanup cancellation.

    Ordinary cleanup failures retain the operation's primary error. Ownership
    is consumed by the caller before this selector; nothing is closed/retried
    here. An already selected error must not become its own exception cause.
    """
    if cleanup is error:
        raise error
    if error is not None and not isinstance(error, Exception):
        failure = error
    elif not isinstance(cleanup, Exception) or error is None:
        failure = cleanup
    else:
        failure = error
    failure.add_note(label + repr(cleanup))
    for previous in (error, cleanup):
        if previous is not None and hasattr(previous, 'spool_status'):
            failure.spool_status = previous.spool_status
    if error is None:
        raise failure
    raise failure from (cleanup if failure is error else error)


@contextmanager
def _owned_directory(path):
    """Close once, retaining acquisition/cancellation as the primary failure."""
    owner = open_project_directory(path, file_limit=FILE_LIMIT)
    try:
        directory = owner.__enter__()
    except BaseException as error:
        try:
            owner.__exit__(type(error), error, error.__traceback__)
        except BaseException as cleanup:
            _raise_cleanup(error, cleanup, 'Calendar source cleanup failed: ')
        raise
    try:
        yield directory
    except BaseException as error:
        try:
            owner.__exit__(type(error), error, error.__traceback__)
        except BaseException as cleanup:
            _raise_cleanup(error, cleanup, 'Calendar source cleanup failed: ')
        raise
    else:
        owner.__exit__(None, None, None)


def _names(references):
    """Reject whole-name aliases and file/directory prefix aliases before reads."""
    seen = {}
    for name in references:
        parts = _relative(name).split('/')
        for index in range(1, len(parts) + 1):
            part = '/'.join(parts[:index]); kind = index < len(parts)
            previous = seen.get(part.casefold())
            if previous is not None and (previous != (part, kind) or not kind):
                raise ValueError('Input role/path or file/directory collision')
            seen[part.casefold()] = (part, kind)


@dataclass(frozen=True)
class CapturedMember:
    """Read-only view, intentionally not a path/fd/native identity substitute."""
    _capture: object
    role: str
    logical_name: str
    size_bytes: int
    sha256: str
    source_observation: object

    @property
    def data(self):
        return self._capture.bytes(self.role)

    def manifest(self):
        self._capture.assert_live()
        return {'size_bytes': self.size_bytes, 'sha256': self.sha256}


class CalendarCapture:
    def __init__(self, *, display_source, profile, spool, records, names, observations,
                 core_roles, config, preparation, source_ancestry=None):
        self.source_ancestry = source_ancestry
        self.display_source = display_source
        self.world_name = display_source.name
        self.profile = profile
        self._spool = spool
        self._records = records
        self._names = names
        self._observations = observations
        self._core_roles = tuple(core_roles)
        self._config = deepcopy(config)
        self._preparation = deepcopy(preparation)
        self._worlds = WeakKeyDictionary()

    def assert_live(self):
        if self._spool is None:
            raise ValueError('Calendar capture is closed')
        self._spool.read_at(0, 0)

    def __enter__(self):
        self.assert_live()
        return self

    def close(self):
        spool, self._spool = self._spool, None
        if spool is not None:
            spool.close()

    def __exit__(self, kind, error, traceback):
        try:
            self.close()
        except BaseException as cleanup:
            _raise_cleanup(error, cleanup, 'Calendar capture cleanup failed: ')

    def __deepcopy__(self, memo):
        raise TypeError('Calendar capture ownership cannot be copied')

    def __reduce__(self):
        raise TypeError('Calendar capture ownership cannot be serialized')

    @property
    def config(self):
        self.assert_live()
        return deepcopy(self._config)

    def has_role(self, role):
        self.assert_live()
        return role in self._records

    def member(self, role):
        self.assert_live()
        if role not in self._records:
            raise ValueError('Unknown captured input role: ' + str(role))
        record = self._records[role]
        return CapturedMember(self, role, self._names[role], record.size, record.sha256,
                              self._observations[role])

    def bytes(self, role):
        self.assert_live()
        if role not in self._records:
            raise ValueError('Unknown captured input role: ' + str(role))
        return self._records[role].data

    @_validation_boundary
    def yaml(self, role):
        return _yaml_text(self.bytes(role).decode('utf-8'))

    def core_manifest(self):
        self.assert_live()
        return {self._names[role]: self.member(role).manifest()
                for role in sorted(self._core_roles, key=lambda r: self._names[r])}

    def legacy_runtime_files(self):
        result = {role: self.member(role).sha256 for role in ('world', 'contract')}
        if self.has_role('manifest'):
            result[MANIFEST] = self.member('manifest').sha256
        return result

    def preparation_value(self):
        self.assert_live()
        if self._preparation is not None:
            return deepcopy(self._preparation)
        return decode_record(self.bytes('preparation')) if self.has_role('preparation') else False

    def assert_config(self, config):
        self.assert_live()
        if _canonical(config) != _canonical(self._config):
            raise ValueError('Execution configuration differs from captured world declaration')

    def assert_definition(self, definition, source):
        self.assert_live()
        if Path(source) != self.display_source or _canonical(definition) != _canonical(self.yaml('world')):
            raise ValueError('World differs from captured project definition')

    def validate_fixed_version(self, config, calendar):
        self.assert_config(config)
        if not self.has_role('manifest'):
            if config.get('fixed_version') is True:
                raise ValueError('Required fixed version is missing')
            return None
        version = _json_text(self.bytes('manifest').decode('utf-8'))
        return _validate_fixed_values(self.world_name, self.yaml('world'), config, calendar,
                                      self.core_manifest(), version)

    def validate_profile(self):
        self.assert_live()
        if self.profile != 'melos':
            raise ValueError('Approved Melos capture profile required')
        from execution.melos_factory import approved_catalogs
        actual = {self._names[role]: hashlib.sha256(self.bytes(role)).hexdigest()
                  for role in (*self._core_roles, 'scenario')}
        if actual != approved_catalogs()['inputs']:
            raise ValueError('Captured profile differs from approved inputs')
        if not self.yaml('scenario'):
            raise ValueError('Nonempty approved scenario required')

    def _accept_world(self, world, style):
        self.assert_definition(world.purpose_preparation_definition, world.source)
        self._worlds[world] = (style, deepcopy(getattr(world, 'calendar_input_provenance', None)),
                              deepcopy(getattr(world, 'calendar_subject_sources', None)))

    def assert_world(self, world):
        self.assert_live()
        if getattr(world, 'calendar_capture', None) is not self or world not in self._worlds:
            raise ValueError('World must be fully validated by load_captured_project')
        self.assert_definition(world.purpose_preparation_definition, world.source)
        self.assert_config(world.slot_execution)

    def assert_provenance(self, world):
        self.assert_world(world)
        _style, expected, sources = self._worlds[world]
        if (getattr(world, 'calendar_input_provenance', None) != expected
                or getattr(world, 'calendar_subject_sources', None) != sources):
            raise ValueError('World provenance differs from captured project inputs')


@_validation_boundary
def open_calendar_capture(world_path, *, profile=None, spool_parent=None, purpose_preparation=None, capture_ancestry=False):
    """Acquire a known calendar project; caller must use the returned context.

    profile is closed to None/'melos'. Explicit False/dict preparation suppresses
    the implicit optional read, just as Simulation's existing injection does.
    No public CLI or legacy loader calls this private prototype automatically.
    """
    if type(capture_ancestry) is not bool:
        raise ValueError('Ancestry capture must be explicit boolean')
    if profile not in (None, 'melos'):
        raise ValueError('Unknown calendar capture profile')
    if purpose_preparation is not None and purpose_preparation is not False and not isinstance(purpose_preparation, dict):
        raise ValueError('Preparation override must be False or a mapping')
    preparation = deepcopy(purpose_preparation)
    source = Path(world_path).absolute()  # lexical only: never resolve aliases
    _relative(source.name)
    if source.name.casefold() == MANIFEST.casefold():
        raise ValueError('World filename collides with manifest')
    spool = None
    try:
        with _owned_directory(source.parent) as directory:
            source_ancestry = directory_ancestry(directory) if capture_ancestry else None
            first = directory.read(source.name)
            raw = _yaml_text(first.data.decode('utf-8'))
            config = validate_execution_config(raw.get('slot_execution'))
            gap = raw.get('gapengine') or {}
            if not isinstance(gap, dict):
                raise ValueError('Invalid world gapengine definition')
            if gap.get('effects') is not None:
                raise ValueError('Captured calendar prototype does not support configured effects paths')
            roles = {'world': source.name, 'contract': config['contract']}
            roles.update({'subject:' + entry['id']: entry['path'] for entry in config['subjects']})
            if gap.get('action_graph') is not None:
                roles['graph'] = _relative(gap['action_graph'])
            core_roles = tuple(roles)
            roles['manifest'] = MANIFEST
            if profile == 'melos':
                roles['scenario'] = 'scenario.yaml'
            if preparation is None:
                roles['preparation'] = PREPARATION
            _names(roles.values())
            max_bytes = sum(MAX_BYTES if role == 'preparation' else FILE_LIMIT for role in roles)
            spool = _spool(max_bytes=max_bytes, parent=spool_parent)
            records, observations, identities = {}, {}, set()
            for role, name in roles.items():
                item = first if role == 'world' else directory.read(name,
                    optional=role in ('manifest', 'preparation'),
                    budget=MAX_BYTES if role == 'preparation' else None)
                if item is None:
                    continue
                token = item.observation
                if token is None or token.native_identity in identities:
                    raise ValueError('Input source identity collision or missing native observation')
                identities.add(token.native_identity)
                observations[role] = token
                records[role] = spool.add(item.data, source_identity=token.native_identity)
                if role == 'world':
                    first = None
                del item
            if 'manifest' in records:
                directory.exact_tree({roles[r]: observations[r] for r in core_roles}, observations['manifest'])
            elif config.get('fixed_version') is True:
                raise ValueError('Required fixed version is missing')
            if profile == 'melos':
                directory.exact_entries({roles[r]: observations[r] for r in (*core_roles, 'scenario')}, policy='yaml')
            spool.seal()
        # Root closure must succeed before any usable capture is transferred.
        capture = CalendarCapture(display_source=source, profile=profile, spool=spool,
            records=records, names=roles, observations=observations, core_roles=core_roles,
            config=config, preparation=preparation, source_ancestry=source_ancestry)
        if profile == 'melos':
            capture.validate_profile()
        return capture
    except BaseException as error:
        if spool is not None:
            try:
                spool.close()
            except BaseException as cleanup:
                _raise_cleanup(error, cleanup, 'Calendar capture cleanup failed: ')
        raise
