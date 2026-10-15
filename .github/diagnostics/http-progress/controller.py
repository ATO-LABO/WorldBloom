"""One owned fixed-source Windows shard; no artifacts, caches or paid services."""
import ast
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
import uuid
import zipfile
import importlib.util

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from public_log import frame, sanitize
HEAD = '581292187f1ff76f110f17a895133a2fd1dc3274'
BOOTSTRAP = '''
# Isolated diagnostic bootstrap only; product functions remain unchanged.
import os as _wb_diagnostic_os
if (_wb_diagnostic_os.environ.get("WB_PHASE_TRACE") and
        _wb_diagnostic_os.environ.get("WB_PHASE_TARGET_ACTIVE") == "1"):
    import sys as _wb_diagnostic_sys
    import importlib.util as _wb_diagnostic_importlib
    if "wb_http_probe" not in _wb_diagnostic_sys.modules:
        _wb_spec = _wb_diagnostic_importlib.spec_from_file_location(
            "wb_http_probe", _wb_diagnostic_os.environ["WB_PHASE_HELPER"])
        _wb_module = _wb_diagnostic_importlib.module_from_spec(_wb_spec)
        _wb_diagnostic_sys.modules["wb_http_probe"] = _wb_module
        _wb_spec.loader.exec_module(_wb_module)
    _wb_diagnostic_sys.modules["wb_http_probe"].install(_wb_diagnostic_sys.modules[__name__])

'''


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def functions(raw):
    return {node.name: ast.dump(node, include_attributes=False) for node in ast.parse(raw).body
            if isinstance(node, (ast.FunctionDef, ast.ClassDef))}


def host_info():
    command = ('$o=Get-CimInstance Win32_OperatingSystem; $c=Get-CimInstance Win32_Processor; '
               '@{os_caption=$o.Caption;os_build=$o.BuildNumber;cpu_name=@($c|ForEach-Object Name);'
               'logical_cpus=($c|Measure-Object NumberOfLogicalProcessors -Sum).Sum;'
               'total_memory_kib=$o.TotalVisibleMemorySize;free_memory_kib=$o.FreePhysicalMemory}'
               '|ConvertTo-Json -Compress')
    result = subprocess.run(['pwsh', '-NoProfile', '-Command', command], capture_output=True, check=True)
    value = json.loads(result.stdout.decode('utf-8-sig'))
    for key in ('os_caption', 'os_build'):
        if not re.fullmatch(r'[A-Za-z0-9 ._()-]{1,150}', str(value[key])):
            raise ValueError('unexpected-host-metadata')
    for name in value['cpu_name']:
        if not re.fullmatch(r'[A-Za-z0-9 ._()@+-]{1,160}', name):
            raise ValueError('unexpected-cpu-metadata')
    value['cpu_name'] = [name.replace('@', ' at ') for name in value['cpu_name']]
    return value


def before_owned_cleanup(temporary, evidence, worker):
    """Read-only last observations before the Job Object is terminated."""
    from execution.provenance import read_atomic_json
    result = {'jobs': [], 'processes': [], 'error': None}
    try:
        for path in temporary.glob('*/control/jobs/job-*/job.json'):
            if not path.resolve().is_relative_to(temporary.resolve()) or path.stat().st_size > 2 * 1024 * 1024:
                continue
            raw = read_atomic_json(path)
            row = {key: raw.get(key) for key in ('job_id', 'state', 'phase', 'heartbeat', 'revision', 'progress')}
            row['identities'] = {}
            identities = {key: raw.get(key) for key in ('launch_identity', 'preparation_identity', 'child_identity')}
            identities['supervisor'] = (raw.get('receipt') or {}).get('identity')
            for key, identity in identities.items():
                if isinstance(identity, dict):
                    row['identities'][key] = {'pid': identity.get('pid'), 'created': identity.get('created'),
                                              'probe': worker.probe(identity)}
            result['jobs'].append(row)
        for path in sorted((evidence / 'phases').glob('*.jsonl')):
            last, spans, locks, malformed = None, {}, {}, 0
            for line in path.read_text(encoding='utf-8').splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    malformed += 1
                    continue
                last = row
                if row['event'] == 'begin':
                    spans[row['span']] = {key: row.get(key) for key in ('phase', 'span', 'parent', 'mono', 'tid', 'request')}
                elif row['event'] == 'end':
                    spans.pop(row['span'], None)
                elif row['event'] == 'lock':
                    key = (row.get('tid'), row.get('lock_id'), row.get('operation'))
                    if row.get('stage') == 'acquired':
                        locks[key] = {field: row.get(field) for field in ('lock_id', 'operation', 'tid', 'mono')}
                    elif row.get('stage') == 'released':
                        locks.pop(key, None)
            if last:
                result['processes'].append({'pid': last['pid'], 'created': last['process_created'],
                    'probe': worker.probe({'pid': last['pid'], 'created': last['process_created']}),
                    'last': {key: last.get(key) for key in ('event', 'phase', 'mono', 'request', 'test', 'occurrence')},
                    'unfinished_spans': list(spans.values()), 'observed_unreleased_locks': list(locks.values()),
                    'incomplete_json_lines': malformed})
    except BaseException as error:
        result['error'] = type(error).__name__
    return result


def main():
    assert os.name == 'nt' and platform.python_version() == '3.13.16'
    import yaml
    assert yaml.__version__ == '6.0.3'
    assert os.environ['GITHUB_RUN_ATTEMPT'] == '1'
    started_epoch = int(os.environ['WB_DIAG_STARTED_MS']) / 1000
    assert 0 <= time.time() - started_epoch < 8 * 60
    owned = Path(os.environ['RUNNER_TEMP']) / ('wb-logdiag-' + uuid.uuid4().hex[:8])
    owned.mkdir(exist_ok=False)
    source, evidence = owned / 'source', owned / 'evidence'
    archive = owned / 'fixed-source.zip'
    subprocess.run(['git', 'archive', '--format=zip', '--output=' + str(archive), HEAD],
                   cwd=HERE.parents[2], capture_output=True, check=True)
    source.mkdir()
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            if not (source / item.filename).resolve().is_relative_to(source.resolve()):
                raise ValueError('unsafe-source-member')
        bundle.extractall(source)
    manifest = json.loads((HERE / 'source-manifest.json').read_text(encoding='utf-8'))
    assert manifest['source_head'] == HEAD
    assert all(sha(source / row['path']) == row['sha256'] for row in manifest['original_files'])
    bootstrap_files = ('execution/worker.py', 'execution/evolution_worker.py', 'gapengine/evolve.py')
    for name in bootstrap_files:
        path = source / name
        raw = path.read_text(encoding='utf-8')
        marker = 'if __name__ == "__main__":'
        if name == 'gapengine/evolve.py':
            assert marker not in raw
            changed = raw + '\n' + BOOTSTRAP
        else:
            assert raw.count(marker) == 1
            changed = raw.replace(marker, BOOTSTRAP + marker)
        assert functions(raw) == functions(changed)
        path.write_text(changed, encoding='utf-8', newline='\n')
    expected = {row['path']: sha(source / row['path']) for row in manifest['original_files']}
    sys.path.insert(0, str(source))
    from execution import worker
    from execution.worker import _WindowsProcessTree, process_identity
    selected = json.loads((HERE / 'ci-selection.json').read_text(encoding='utf-8'))['selected_ids']
    counters = {'rejected': 0}
    metadata = {'source_head': HEAD, 'source_tree': manifest['source_tree'],
        'workflow_head': os.environ['GITHUB_SHA'], 'run_id': int(os.environ['GITHUB_RUN_ID']),
        'run_attempt': int(os.environ['GITHUB_RUN_ATTEMPT']),
        'first_preparation_step_epoch': started_epoch, 'controller_start_epoch': time.time(),
        'runner_label': 'windows-2025', 'image_os': os.environ.get('ImageOS', ''),
        'image_version': os.environ.get('ImageVersion', ''),
        'python': platform.python_version(), 'python_build': list(platform.python_build()),
        'python_compiler': platform.python_compiler(), 'python_bitness': 64 if sys.maxsize > 2**32 else 32,
        'pyyaml': yaml.__version__, 'libyaml': yaml.__with_libyaml__, 'host': host_info(),
        'binaries': [{'name': name, 'sha256': sha(Path(sys.executable).parent / name)}
                     for name in ('python.exe', 'python313.dll')],
        'pyyaml_extension_sha256': sha(importlib.util.find_spec('yaml._yaml').origin),
        'multiprocessing_pool_source_sha256': sha(Path(sys.prefix) / 'Lib/multiprocessing/pool.py'),
        'bootstrap_file_hashes': [{'path_sha256': hashlib.sha256(name.encode()).hexdigest(),
                                  'diagnostic_sha256': sha(source / name)} for name in bootstrap_files],
        'selected_invocations': 1363, 'test_outer_cap_seconds': 28 * 60,
        'job_timeout_minutes': 34, 'end_to_end_limit_minutes': 45,
        'product_ga_deadline_seconds': 45, 'product_http_timeout_seconds': 5,
        'new_artifacts': 0, 'new_actions_caches': 0, 'no_product_function_ast_changes': True}
    for key in ('image_os', 'image_version'):
        if not re.fullmatch(r'[A-Za-z0-9._-]{0,100}', metadata[key]):
            raise ValueError('unexpected-image-metadata')
    tree = _WindowsProcessTree()
    try:
        child = tree.launch([sys.executable, '-I', '-B', str(HERE / 'runner.py'),
                            '--source-label', 'head', '--kind', 'shard', '--source', str(source),
                            '--owned', str(owned), '--evidence', str(evidence)], source, capture=True)
        identity = process_identity(child.pid)
        metadata['runner_identity'] = {'pid': identity['pid'], 'created': identity['created']}
        limit = min(28 * 60, started_epoch + 30 * 60 - time.time() - 10)
        assert limit > 60
        metadata['actual_outer_limit_seconds'] = limit
        try:
            stdout, _ = child.communicate(timeout=limit)
            metadata['outer_timeout'] = False
        except subprocess.TimeoutExpired:
            metadata['outer_timeout'] = True
            metadata['outer_pre_cleanup_snapshot'] = sanitize(before_owned_cleanup(owned / 'temporary', evidence, worker), set(selected), counters)
            tree.terminate()
            stdout, _ = child.communicate(timeout=10)
        metadata['runner_returncode'] = child.returncode
        # Raw product stdout remains an ephemeral, unuploaded runner-local file.
        (owned / 'stdout.private.log').write_bytes(stdout)
    finally:
        if 'outer_pre_cleanup_snapshot' not in metadata:
            metadata['outer_pre_cleanup_snapshot'] = sanitize(before_owned_cleanup(owned / 'temporary', evidence, worker), set(selected), counters)
        tree.finish()
        metadata['owned_tree_exit_confirmed'] = True
        metadata['source_verified_after'] = all(sha(source / name) == digest for name, digest in expected.items())
    order = (evidence / 'test-order.jsonl').read_bytes() if (evidence / 'test-order.jsonl').exists() else b''
    events = [json.loads(row) for row in order.splitlines()]
    begun = [row['test'] for row in events if row.get('event') == 'start']
    ended = [row['test'] for row in events if row.get('event') == 'stop']
    metadata['started_tests'] = len(begun)
    metadata['completed_test_records'] = len(ended)
    metadata['order_prefix_matches'] = begun == selected[:len(begun)]
    metadata['full_selection_finished'] = begun == ended == selected
    metadata['test_outcomes'] = {kind: sum(row.get('event', row.get('kind')) == kind for row in events)
                                 for kind in ('success', 'skip', 'failure', 'error')}
    raw_result = evidence / 'result.json'
    metadata['test_success'] = json.loads(raw_result.read_text(encoding='utf-8'))['success'] if raw_result.exists() else None
    safe_rows = []
    malformed = 0
    for path in sorted((evidence / 'phases').glob('*.jsonl')):
        for line in path.read_text(encoding='utf-8').splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                malformed += 1
                continue
            safe_rows.append(sanitize(row, set(selected), counters))
    metadata['malformed_producer_lines'] = malformed
    metadata['schema_rejected_fields'] = counters['rejected']
    metadata['safe_phase_records'] = len(safe_rows)
    metadata['diagnostic_end_epoch'] = time.time()
    metadata['elapsed_from_first_step_seconds'] = time.time() - started_epoch
    safe_order = [sanitize(row, set(selected), counters) for row in events]
    metadata['schema_rejected_fields'] = counters['rejected']
    # Ordinary logs only; encoded content has already passed a strict whitelist
    # and a final private-address/path check. Chunks and streams are hash framed.
    frame('metadata', json.dumps(metadata, sort_keys=True, separators=(',', ':')).encode())
    frame('order', b''.join(json.dumps(row, separators=(',', ':')).encode() + b'\n' for row in safe_order))
    frame('phases', b''.join(json.dumps(row, separators=(',', ':')).encode() + b'\n' for row in safe_rows))
    return 0 if metadata['test_success'] and metadata['source_verified_after'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as error:
        # Do not leak exception strings, native paths or product output.
        print(json.dumps({'diagnostic_controller_error': type(error).__name__, 'evidence_incomplete': True}), flush=True)
        raise SystemExit(2)
