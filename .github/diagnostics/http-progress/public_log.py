"""Allowlisted synthetic metadata in integrity-framed ordinary workflow logs."""
import base64
import hashlib
import json
import math
import re
import zlib

MAX_RAW = 64 * 1024 * 1024
MAX_COMPRESSED = 8 * 1024 * 1024
CHUNK = 3072
EVENTS = set('begin end observer.start observer.stop process.identity job.state job.correlate lock ga.seed_event test.begin test.end test.failure_before_cleanup snapshot.before_cleanup snapshot.unavailable fixture.inputs'.split())
WORDS = set(('queued running stopping failed succeeded partial cancelled interrupted preparing evaluating finishing generating evaluating_independent '
    'preparation supervisor ga runner other inputs runtime repo temporary '
    'thread_acquired acquired released contended timeout snapshot read_error read write event alive dead unknown terminal expired unconfirmed '
    'cleanup_entry failure_before_cleanup error_before_cleanup started completed '
    'failure error success skip start stop wall_timeout preparation_failed process_failed worker_failed '
    'worker.py evolution_worker.py evolve.py ci_phase_suite.py regression_partition.py '
    'job.json request.json prepared.json config.json input-manifest.json runtime-manifest.json manifest.json complete.json '
    'python python.exe git git.exe 0 1 6-log-only-ci head shard').split())
KEYS = set(('event pid parent_pid process_created perf_ns tid mono wall test occurrence request span parent phase files bytes scope record job executable cwd_length '
    'seconds cpu_seconds error errno winerror io_counters dropped observer_errors job_id native_request_sha256 state heartbeat revision progress child_pid child_created '
    'lock_id operation stage observation wait_seconds held_seconds legacy_diagnostics probe_version role entry python seed input_files input_census_sha256 config_sha256 '
    'assertion_deadline_seconds http_timeout_seconds jobs created_at started_at finished_at updated_at error_code identities launch_identity preparation_identity '
    'child_identity supervisor created probe processes last unfinished_spans observed_unreleased_locks incomplete_json_lines count max_seconds '
    'completed_generations total_generations completed_individuals total_individuals completed_seeds total_seeds kind exception reason_sha256 sha256').split())
FUNCTION = re.compile(r'^(?:(?:http\.(?:client|server)|ConfigStore|JobStore|provenance|worker|ga(?:\.observer)?|os|file|path|temp|yaml|genre|configs|process)\.[A-Za-z_]+|legacy\.trace_lock|subprocess\.run|import\.(?:execution\.configs|yaml)|jobs(?:\.get)?|directory_lock)$')
EMAIL = re.compile(rb'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
NATIVE_PATH = re.compile(rb'(?i)(?:\b[A-Z]:[\\/]|/(?:Users|home)/)')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def sanitize(value, tests, counters, key=''):
    if value is None or type(value) in (int, bool):
        return value
    if type(value) is float:
        if math.isfinite(value):
            return value
        counters['rejected'] += 1
        return None
    if isinstance(value, list):
        return [sanitize(item, tests, counters, key) for item in value]
    if isinstance(value, dict):
        result = {}
        for name, item in value.items():
            if name == 'path' and type(item) is str:
                result['path_sha256'] = sha(item.encode())
            elif name in KEYS or name in ('os.stat', 'os.lstat', 'path.resolve', 'path.read_bytes'):
                result[name] = sanitize(item, tests, counters, name)
            else:
                counters['rejected'] += 1
        return result
    if type(value) is str:
        valid = (value in WORDS or not value or value == '<unknown>' or
                 re.fullmatch(r'(?:job-)?[0-9a-f]{32,64}', value) or
                 re.fullmatch(r'[0-9]+-[0-9]+', value) or
                 re.fullmatch(r'3\.13\.16', value) or
                 key == 'test' and value in tests or
                 key == 'event' and value in EVENTS or
                 key in ('phase', 'operation') and FUNCTION.fullmatch(value) or
                 key in ('error', 'exception') and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,100}', value))
        if valid:
            return value
    counters['rejected'] += 1
    return None


def assert_private_free(raw):
    if EMAIL.search(raw) or NATIVE_PATH.search(raw):
        raise ValueError('public-privacy-check-failed')


def frame(name, raw):
    if not re.fullmatch(r'[a-z_]+', name) or len(raw) > MAX_RAW:
        raise ValueError('public-log-input-limit')
    assert_private_free(raw)
    encoded = zlib.compress(raw, 6)
    if len(encoded) > MAX_COMPRESSED:
        raise ValueError('public-log-compressed-limit')
    chunks = [encoded[i:i + CHUNK] for i in range(0, len(encoded), CHUNK)]
    print(f'WBLOG1 BEGIN {name} {len(chunks)} {sha(raw)} {sha(encoded)} {len(raw)} {len(encoded)}', flush=True)
    for index, chunk in enumerate(chunks):
        print(f'WBLOG1 DATA {name} {index} {base64.b64encode(chunk).decode()} {sha(chunk)}', flush=True)
    print(f'WBLOG1 END {name} {sha(raw)}', flush=True)


def decode_log(text):
    """Fail on missing/duplicate/truncated frames; never infer test success."""
    streams = {}
    for line in text.splitlines():
        index = line.find('WBLOG1 ')
        if index < 0:
            continue
        fields = line[index:].strip().split()
        if fields[1] == 'BEGIN':
            _, _, name, count, raw_sha, zipped_sha, raw_bytes, zipped_bytes = fields
            if name in streams:
                raise ValueError('duplicate-stream')
            streams[name] = {'count': int(count), 'raw_sha': raw_sha, 'zipped_sha': zipped_sha,
                             'raw_bytes': int(raw_bytes), 'zipped_bytes': int(zipped_bytes), 'chunks': {}, 'end': None}
        elif fields[1] == 'DATA':
            _, _, name, number, data, digest = fields
            stream = streams[name]
            number = int(number)
            if number in stream['chunks']:
                raise ValueError('duplicate-chunk')
            chunk = base64.b64decode(data, validate=True)
            if sha(chunk) != digest:
                raise ValueError('chunk-digest-mismatch')
            stream['chunks'][number] = chunk
        elif fields[1] == 'END':
            _, _, name, digest = fields
            if streams[name]['end'] is not None:
                raise ValueError('duplicate-end')
            streams[name]['end'] = digest
    decoded = {}
    for name, stream in streams.items():
        if set(stream['chunks']) != set(range(stream['count'])) or stream['end'] != stream['raw_sha']:
            raise ValueError('missing-or-truncated-stream')
        compressed = b''.join(stream['chunks'][i] for i in range(stream['count']))
        if len(compressed) != stream['zipped_bytes'] or sha(compressed) != stream['zipped_sha']:
            raise ValueError('compressed-digest-mismatch')
        raw = zlib.decompress(compressed)
        if len(raw) != stream['raw_bytes'] or sha(raw) != stream['raw_sha']:
            raise ValueError('raw-digest-mismatch')
        assert_private_free(raw)
        decoded[name] = raw
    if set(decoded) != {'metadata', 'order', 'phases'}:
        raise ValueError('missing-required-stream')
    return decoded
