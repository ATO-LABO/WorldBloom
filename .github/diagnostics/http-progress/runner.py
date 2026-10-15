"""Original test(s), deadlines and cleanup with sanitized pre-cleanup observation."""
from collections import Counter, defaultdict, deque
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import re
import sys
import tempfile
import time
import unittest

HERE = Path(__file__).resolve().parent
TARGET = 'test_evolution_execution.EvolutionHttpTests.test_real_frozen_adapter_progress_and_publication_over_http'


def flat(suite):
    for case in suite:
        if isinstance(case, unittest.TestSuite):
            yield from flat(case)
        else:
            yield case


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-label', choices=('base', 'head'), required=True)
    parser.add_argument('--kind', choices=('single', 'shard'), required=True)
    parser.add_argument('--evidence', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--owned', required=True)
    args = parser.parse_args()
    assert args.source_label == 'head' and args.kind == 'shard'
    source = Path(args.source).resolve()
    evidence = Path(args.evidence).resolve()
    owned = Path(args.owned).resolve()
    assert source.parent == owned and evidence.parent == owned
    evidence.mkdir(exist_ok=False)
    temporary = owned / 'temporary'
    temporary.mkdir(exist_ok=False)
    os.environ.update(TEMP=str(temporary), TMP=str(temporary), PYTHONUTF8='1',
        PYTHONIOENCODING='utf-8', WB_REGRESSION_DIAGNOSTICS='1',
        WB_REGRESSION_EVIDENCE_DIR=str(evidence / 'legacy'), WB_REGRESSION_PHASE='full-suite',
        WB_EPOCH_CAPTURE_STDERR='0', MELOS_APPROVED_ADAPTER_DIR=str(source / 'execution/approved_melos_sources'),
        MELOS_INPUT_DIR=str(source / 'projects/melos-input'), WB_PHASE_TRACE=str(evidence / 'phases'),
        WB_PHASE_HELPER=str(HERE / 'phase_probe.py'), WB_PHASE_TARGET_ACTIVE='0')
    # CI has no user API keys. Remove inherited credentials from the owned test
    # process without logging their names/values or changing the parent session.
    removed = 0
    for key in list(os.environ):
        if re.search(r'(API_KEY|ACCESS_TOKEN|REFRESH_TOKEN|CLIENT_SECRET|SECRET_KEY|PASSWORD)$', key, re.I):
            os.environ.pop(key)
            removed += 1
    tempfile.tempdir = None
    sys.path[:0] = [str(source / 'tests'), str(source)]
    os.chdir(source)
    spec = importlib.util.spec_from_file_location('wb_http_probe', HERE / 'phase_probe.py')
    probe = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = probe
    spec.loader.exec_module(probe)
    probe.install()
    from execution import worker
    from execution.provenance import read_atomic_json
    import test_evolution_execution as tests

    def digest(value):
        import hashlib
        return hashlib.sha256(value).hexdigest()

    def stream_snapshot():
        probe.flush()
        state = []
        for path in (evidence / 'phases').glob('*.jsonl'):
            active_spans = {}
            locks = {}
            last = None
            malformed = 0
            for raw in path.read_text(encoding='utf-8').splitlines():
                try:
                    row = json.loads(raw)
                except ValueError:
                    malformed += 1
                    continue
                if row.get('test') not in probe.TARGETS:
                    continue
                last = row
                if row['event'] == 'begin':
                    active_spans[row['span']] = {key: row.get(key) for key in ('phase', 'span', 'parent', 'mono', 'tid', 'request')}
                elif row['event'] == 'end':
                    active_spans.pop(row['span'], None)
                elif row['event'] == 'lock':
                    key = (row.get('tid'), row.get('lock_id'), row.get('operation'))
                    if row.get('stage') == 'acquired':
                        locks[key] = {key: row.get(key) for key in ('lock_id', 'operation', 'tid', 'mono')}
                    elif row.get('stage') == 'released':
                        locks.pop(key, None)
            if last:
                state.append({'pid': last['pid'], 'created': last['process_created'],
                              'last': {key: last.get(key) for key in ('event', 'phase', 'mono', 'request', 'test', 'occurrence')},
                              'unfinished_spans': list(active_spans.values()),
                              'observed_unreleased_locks': list(locks.values()),
                              'incomplete_json_lines': malformed})
        return state

    def snapshot(test, stage):
        if test.id() not in probe.TARGETS:
            return
        root = getattr(test, 'base', None)
        jobs = []
        try:
            root = Path(root).resolve()
            assert root.is_relative_to(temporary.resolve())
            for path in (root / 'control/jobs').glob('job-*/job.json'):
                assert path.resolve().is_relative_to(root) and path.stat().st_size < 2 * 1024 * 1024
                raw = read_atomic_json(path)  # Shared atomic read; no added product lock.
                row = {key: probe._number(raw.get(key)) for key in
                       ('heartbeat', 'created_at', 'started_at', 'finished_at', 'updated_at', 'revision')}
                row.update(job_id=probe._safe_id(raw.get('job_id')),
                    state=raw.get('state') if raw.get('state') in worker.TERMINAL | {'queued', 'running', 'stopping'} else None,
                    phase=raw.get('phase') if raw.get('phase') in ('preparing', 'evaluating', 'finishing') else None,
                    progress={k: probe._number(v) for k, v in (raw.get('progress') or {}).items() if k in probe.PROGRESS})
                error = raw.get('error') or {}
                row['error_code'] = error.get('code') if error.get('code') in (
                    'wall_timeout', 'preparation_failed', 'process_failed', 'worker_failed') else None
                row['identities'] = {}
                identities = {key: raw.get(key) for key in ('launch_identity', 'preparation_identity', 'child_identity')}
                identities['supervisor'] = (raw.get('receipt') or {}).get('identity')
                for key, identity in identities.items():
                    if isinstance(identity, dict):
                        row['identities'][key] = {'pid': probe._number(identity.get('pid')),
                            'created': probe._number(identity.get('created')), 'probe': worker.probe(identity)}
                jobs.append(row)
            probe.emit('snapshot.before_cleanup', stage=stage, jobs=jobs, processes=stream_snapshot())
        except BaseException as error:
            probe.emit('snapshot.unavailable', stage=stage, error=type(error).__name__)
        probe.flush()

    original_setup = tests.EvolutionHttpTests.setUp
    original_cleanup = tests.EvolutionHttpTests.cleanup

    def setup(test):
        original_setup(test)
        if test.id() in probe.TARGETS:
            folder = test.base / 'control/configs/cfg-test'
            inputs = [{'path': p.relative_to(folder / 'inputs').as_posix(), 'sha256': digest(p.read_bytes())}
                      for p in sorted((folder / 'inputs').rglob('*')) if p.is_file()]
            probe.emit('fixture.inputs', input_files=inputs,
                       input_census_sha256=digest(json.dumps(inputs, sort_keys=True).encode()),
                       config_sha256=digest((folder / 'config.json').read_bytes()),
                       assertion_deadline_seconds=45, http_timeout_seconds=5)

    def cleanup(test):
        snapshot(test, 'cleanup_entry')
        return original_cleanup(test)

    tests.EvolutionHttpTests.setUp = setup
    tests.EvolutionHttpTests.cleanup = cleanup
    if args.kind == 'single':
        suite = unittest.defaultTestLoader.loadTestsFromName(TARGET)
        selected = [TARGET]
        discovery_verified = None
    else:
        cases = list(flat(unittest.defaultTestLoader.discover(str(source / 'tests'))))
        all_ids = [case.id() for case in cases]
        ci_path = HERE / 'ci-selection.json'
        original = json.loads(ci_path.read_text(encoding='utf-8'))
        assert all_ids == original['full_ids'], 'Discovery differs from original CI'
        selected = original['selected_ids']
        pools = defaultdict(deque)
        for case in cases:
            pools[case.id()].append(case)
        suite = unittest.TestSuite(pools[ident].popleft() for ident in selected)
        assert len(selected) == 1363
        discovery_verified = True
    counts, completed, failed = Counter(), [], []
    events = (evidence / 'test-order.jsonl').open('w', encoding='utf-8')

    class Result(unittest.TextTestResult):
        def startTest(self, test):
            ident = test.id()
            counts[ident] += 1
            os.environ.update(WB_PHASE_TEST=ident, WB_PHASE_OCCURRENCE=str(counts[ident]),
                              WB_PHASE_TARGET_ACTIVE='1' if ident in probe.TARGETS else '0')
            self.started = time.monotonic()
            probe.emit('test.begin')
            events.write(json.dumps({'event': 'start', 'test': ident, 'occurrence': counts[ident]}) + '\n')
            events.flush()
            super().startTest(test)

        def stopTest(self, test):
            probe.emit('test.end', seconds=time.monotonic() - self.started)
            events.write(json.dumps({'event': 'stop', 'test': test.id(), 'seconds': time.monotonic() - self.started}) + '\n')
            events.flush()
            completed.append(test.id())
            super().stopTest(test)
            os.environ['WB_PHASE_TARGET_ACTIVE'] = '0'

        def addSuccess(self, test):
            events.write(json.dumps({'event': 'success', 'test': test.id()}) + '\n'); events.flush()
            super().addSuccess(test)

        def addSkip(self, test, reason):
            events.write(json.dumps({'event': 'skip', 'test': test.id(), 'reason_sha256': digest(reason.encode())}) + '\n'); events.flush()
            super().addSkip(test, reason)

        def addFailure(self, test, error):
            snapshot(test, 'failure_before_cleanup')
            failed.append({'test': test.id(), 'kind': 'failure', 'exception': error[0].__name__})
            events.write(json.dumps(failed[-1]) + '\n'); events.flush()
            super().addFailure(test, error)

        def addError(self, test, error):
            snapshot(test, 'error_before_cleanup')
            failed.append({'test': test.id(), 'kind': 'error', 'exception': error[0].__name__})
            events.write(json.dumps(failed[-1]) + '\n'); events.flush()
            super().addError(test, error)

    log = io.StringIO()
    started = time.monotonic()
    result = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=Result).run(suite)
    events.close()
    (evidence / 'test-output.private.log').write_text(log.getvalue(), encoding='utf-8')
    summary = {'source_label': args.source_label, 'kind': args.kind, 'python': platform.python_version(),
        'platform': platform.platform(), 'credential_environment_entries_removed': removed,
        'tests': result.testsRun, 'success': result.wasSuccessful(), 'failures': failed,
        'skips': len(result.skipped), 'seconds': time.monotonic() - started,
        'selection': selected, 'completion': completed, 'order_complete': selected == completed,
        'discovery_matches_original_ci': discovery_verified,
        'deadlines_and_assertions_unchanged': True}
    (evidence / 'result.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    probe.finish()
    print(json.dumps({key: value for key, value in summary.items() if key not in ('selection', 'completion')}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except BaseException as error:
        print(json.dumps({'runner_error': type(error).__name__}), flush=True)
        raise SystemExit(2)
