"""Private checkpoint-generation codec; no filesystem I/O or public route.

A generation is one complete existing checkpoint envelope, not a delta or an
archive to extract. Checkpoint bytes are retained exactly. Expected references
come from the caller, not a mutable manifest beside the saved data.
"""
from dataclasses import dataclass
import hashlib
import re
import struct

from engine.calendar_checkpoint import strict_json
from execution.provenance import canonical

MAX_CHECKPOINT_BYTES = 32 * 1024 * 1024  # Existing checkpoint read limit.
MAX_HEADER_BYTES = 2048  # Fixed schema only; bounded IDs, hashes and integers.
_MAGIC = b'WBGEN1\r\n'
_PREFIX = struct.Struct('<8sIQ')
MAX_GENERATION_BYTES = _PREFIX.size + MAX_HEADER_BYTES + MAX_CHECKPOINT_BYTES
MAX_ORDINAL = (1 << 63) - 1
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_STREAM = re.compile(r'[0-9a-f]{32}\Z')
_HEADER_KEYS = {'schema', 'kind', 'stream_id', 'ordinal', 'previous', 'forked_from',
                'checkpoint_size', 'checkpoint_sha256', 'compatibility_sha256'}
_REF_KEYS = {'sha256', 'size', 'stream_id', 'ordinal'}


class GenerationError(ValueError):
    pass


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _integer(value, maximum, label):
    if type(value) is not int or not 0 <= value <= maximum:
        raise GenerationError('Invalid ' + label)
    return value


def _stream(value):
    if type(value) is not str or not _STREAM.fullmatch(value):
        raise GenerationError('Invalid generation stream ID')
    return value


def _digest(value):
    if type(value) is not str or not _HASH.fullmatch(value):
        raise GenerationError('Invalid generation digest')
    return value


@dataclass(frozen=True)
class GenerationRef:
    sha256: str
    size: int
    stream_id: str
    ordinal: int


def validate_ref(ref):
    if type(ref) is not GenerationRef:
        raise GenerationError('An exact independent GenerationRef is required')
    _digest(ref.sha256)
    _integer(ref.size, MAX_GENERATION_BYTES, 'generation size')
    if ref.size <= _PREFIX.size:
        raise GenerationError('Generation reference is too small')
    _stream(ref.stream_id)
    _integer(ref.ordinal, MAX_ORDINAL, 'generation ordinal')
    return ref


def _ref_value(ref):
    if ref is None:
        return None
    validate_ref(ref)
    return {key: getattr(ref, key) for key in sorted(_REF_KEYS)}


def _parse_ref(value):
    if value is None:
        return None
    if type(value) is not dict or set(value) != _REF_KEYS:
        raise GenerationError('Invalid generation reference schema')
    return validate_ref(GenerationRef(**value))


def leaf_for(ref):
    validate_ref(ref)
    # Ordinary ASCII leaf, no platform aliases or variable path components.
    return 'wbgen-' + ref.stream_id + '-' + f'{ref.ordinal:020d}' + '-' + ref.sha256 + '.wbg'


@dataclass(frozen=True)
class SelectedGeneration:
    leaf: str
    expected: GenerationRef

    def validate(self):
        validate_ref(self.expected)
        if type(self.leaf) is not str or self.leaf != leaf_for(self.expected):
            raise GenerationError('Selected leaf differs from the independent generation reference')
        return self


def checkpoint_bytes(state, compatibility):
    if type(compatibility) is not dict:
        raise GenerationError('Checkpoint compatibility must be an object')
    payload = {'compatibility': compatibility, 'state': state}
    encoded = canonical({'payload': payload, 'sha256': _hash(canonical(payload))})
    if len(encoded) > MAX_CHECKPOINT_BYTES:
        raise GenerationError('Checkpoint exceeds the existing 32 MiB read limit')
    return encoded


def checkpoint_payload(data, expected_compatibility):
    if type(data) is not bytes or len(data) > MAX_CHECKPOINT_BYTES:
        raise GenerationError('Checkpoint requires bounded immutable bytes')
    if type(expected_compatibility) is not dict:
        raise GenerationError('Expected checkpoint compatibility must be an object')
    try:
        value = strict_json(data.decode('utf-8'))
    except (UnicodeError, ValueError, RecursionError) as error:
        raise GenerationError('Invalid checkpoint JSON') from error
    if type(value) is not dict or set(value) != {'payload', 'sha256'}:
        raise GenerationError('Wrong checkpoint envelope')
    payload = value['payload']
    if type(payload) is not dict or set(payload) != {'compatibility', 'state'}:
        raise GenerationError('Wrong checkpoint payload')
    if _digest(value['sha256']) != _hash(canonical(payload)):
        raise GenerationError('Checkpoint checksum mismatch')
    if canonical(payload['compatibility']) != canonical(expected_compatibility):
        raise GenerationError('Checkpoint compatibility mismatch')
    return payload


def _lineage(stream_id, ordinal, previous, forked_from):
    _stream(stream_id)
    _integer(ordinal, MAX_ORDINAL, 'generation ordinal')
    if ordinal == 0:
        if previous is not None:
            raise GenerationError('Initial generation cannot have a previous generation')
        if forked_from is not None:
            validate_ref(forked_from)
            if forked_from.stream_id == stream_id:
                raise GenerationError('Continuation must use a new stream')
    else:
        validate_ref(previous)
        if (previous.stream_id != stream_id or previous.ordinal != ordinal - 1
                or forked_from is not None):
            raise GenerationError('Non-contiguous generation lineage')


@dataclass(frozen=True)
class PreparedGeneration:
    data: bytes
    ref: GenerationRef
    checkpoint: bytes
    previous: GenerationRef | None
    forked_from: GenerationRef | None

    @property
    def leaf(self):
        return leaf_for(self.ref)

    def selection(self):
        return SelectedGeneration(self.leaf, self.ref)

    def state(self, expected_compatibility):
        # A fresh parse means callers cannot mutate the retained accepted bytes.
        return checkpoint_payload(self.checkpoint, expected_compatibility)['state']


def prepare_generation(checkpoint, *, expected_compatibility, stream_id, ordinal,
                       previous=None, forked_from=None):
    checkpoint_payload(checkpoint, expected_compatibility)
    _lineage(stream_id, ordinal, previous, forked_from)
    header = {'schema': 1, 'kind': 'checkpoint', 'stream_id': stream_id, 'ordinal': ordinal,
              'previous': _ref_value(previous), 'forked_from': _ref_value(forked_from),
              'checkpoint_size': len(checkpoint), 'checkpoint_sha256': _hash(checkpoint),
              'compatibility_sha256': _hash(canonical(expected_compatibility))}
    index = canonical(header)
    if len(index) > MAX_HEADER_BYTES:
        raise GenerationError('Generation metadata exceeds its fixed-schema bound')
    data = _PREFIX.pack(_MAGIC, len(index), len(checkpoint)) + index + checkpoint
    ref = GenerationRef(_hash(data), len(data), stream_id, ordinal)
    return PreparedGeneration(data, ref, checkpoint, previous, forked_from)


def decode_generation(data, *, expected, expected_compatibility):
    validate_ref(expected)
    if type(data) is not bytes or not _PREFIX.size < len(data) <= MAX_GENERATION_BYTES:
        raise GenerationError('Generation requires bounded immutable bytes')
    if len(data) != expected.size or _hash(data) != expected.sha256:
        raise GenerationError('Generation differs from independently expected bytes')
    magic, header_size, payload_size = _PREFIX.unpack_from(data)
    if (magic != _MAGIC or not 0 < header_size <= MAX_HEADER_BYTES
            or not 0 < payload_size <= MAX_CHECKPOINT_BYTES
            or _PREFIX.size + header_size + payload_size != len(data)):
        raise GenerationError('Invalid generation framing or lengths')
    index = data[_PREFIX.size:_PREFIX.size + header_size]
    try:
        header = strict_json(index.decode('utf-8'))
    except (UnicodeError, ValueError, RecursionError) as error:
        raise GenerationError('Invalid generation metadata') from error
    if (type(header) is not dict or set(header) != _HEADER_KEYS
            or type(header['schema']) is not int or header['schema'] != 1
            or header['kind'] != 'checkpoint' or canonical(header) != index):
        raise GenerationError('Wrong generation schema or noncanonical metadata')
    previous, forked = _parse_ref(header['previous']), _parse_ref(header['forked_from'])
    _lineage(header['stream_id'], header['ordinal'], previous, forked)
    if (header['stream_id'] != expected.stream_id or header['ordinal'] != expected.ordinal
            or type(header['checkpoint_size']) is not int or header['checkpoint_size'] != payload_size
            or _digest(header['compatibility_sha256']) != _hash(canonical(expected_compatibility))):
        raise GenerationError('Generation identity, size or compatibility differs')
    checkpoint = data[_PREFIX.size + header_size:]
    if _digest(header['checkpoint_sha256']) != _hash(checkpoint):
        raise GenerationError('Checkpoint member checksum mismatch')
    checkpoint_payload(checkpoint, expected_compatibility)
    return PreparedGeneration(data, expected, checkpoint, previous, forked)
