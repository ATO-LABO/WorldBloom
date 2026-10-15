"""Complete unittest discovery in two module-isolated CI jobs, with a census gate."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
SHARDS = 2
# Approximate seconds observed on head 1c5236ce Windows logs. These weights
# affect placement only, never discovery/admission/assertions or deadlines.
WEIGHTS = dict(test_growth_s3=227, test_lineage=215, test_epoch_chain=239,
               test_execution_configs=199, test_melos_product=190, test_output_jobs=179,
               test_evolution_execution=176, test_execution_jobs=129,
               test_calendar_runtime=101, test_ga_resume=78, test_festival_ga_resume=77,
               test_world_patch_cli=84, test_fact_workspace=65, test_fact_jobs=56,
               test_melos_session=49, test_world_patch_r2=46, test_workbench_pages=52,
               test_lineage_pages=45, test_melos_run_record=35, test_lineage_river=36,
               test_melos_factory=32)


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def partition(tests):
    groups = defaultdict(list)
    for test in tests:
        groups[test.id().split('.')[0]].append(test)
    loads, parts = [0] * SHARDS, [[] for _ in range(SHARDS)]
    for name in sorted(groups, key=lambda name: (-WEIGHTS.get(name, 1), name)):
        index = min(range(SHARDS), key=lambda index: (loads[index], index))
        parts[index].extend(groups[name])
        loads[index] += WEIGHTS.get(name, 1)
    # Preserve unittest's order inside each shard, including inherited duplicate
    # IDs already present in discover. Two occurrences are not silently deduped.
    memberships = [{id(test) for test in part} for part in parts]
    return [[test for test in tests if id(test) in members] for members in memberships]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def validate_reports(reports, *, head, environment):
    if len(reports) != SHARDS or {r['shard'] for r in reports} != set(range(SHARDS)):
        raise ValueError('Missing or duplicate shard report')
    expected = reports[0]['full_ids']
    if not expected:
        raise ValueError('Empty discovery census')
    selected, completed = [], []
    for report in reports:
        if report['head'] != head or report['environment'] != environment:
            raise ValueError('Wrong source head or environment')
        expected_python = list(map(int, environment.rsplit('-py', 1)[1].split('.')))
        expected_platform = 'win32' if environment.startswith('windows-') else 'linux'
        if report['python'][:2] != expected_python or report['platform'] != expected_platform:
            raise ValueError('Wrong actual Python version or operating system')
        if report['full_ids'] != expected or report['loader_errors']:
            raise ValueError('Inconsistent or failed discovery')
        if not report['completed_normally'] or not report['success']:
            raise ValueError('Shard failed or did not finish')
        expected_outcomes = {'python': 'success',
                             'javascript': 'success' if report['shard'] == 0 else 'skipped',
                             'before': 'success' if environment.startswith('windows-') and report['shard'] == 0 else 'skipped',
                             'after': 'success' if environment.startswith('windows-') and report['shard'] == 0 else 'skipped'}
        if report['outcomes'] != expected_outcomes:
            raise ValueError('Required Python, JavaScript or observation step did not succeed')
        if (report['completed_ids'] != report['selected_ids'] or
                report['tests_run'] != len(report['selected_ids'])):
            raise ValueError('Incomplete or extra execution within shard')
        selected.extend(report['selected_ids'])
        completed.extend(report['completed_ids'])
    if Counter(selected) != Counter(expected) or Counter(completed) != Counter(expected):
        raise ValueError('Shard union differs from full discovery census')
    return {'head': head, 'environment': environment, 'tests_run': len(expected),
            'unique_test_ids': len(set(expected)),
            'existing_duplicate_invocations': len(expected) - len(set(expected)),
            'skips': sum(r['skips'] for r in reports), 'failures': 0, 'errors': 0,
            'complete_census_verified': True,
            'census_sha256': hashlib.sha256(json.dumps(expected).encode()).hexdigest(),
            'shards': [{'shard': r['shard'], 'tests': r['tests_run'],
                        'seconds': r['seconds']} for r in sorted(reports, key=lambda r: r['shard'])]}


class RecordingResult(unittest.TextTestResult):
    """Flush failures immediately, before a potential later job cancellation."""
    def __init__(self, *args, events, **kwargs):
        super().__init__(*args, **kwargs)
        self.events = events
        self.completed_ids = []
        self.started = None

    def record(self, kind, test, detail=None):
        self.events.write(json.dumps({'kind': kind, 'test': test.id(),
                                      'detail': detail}, ensure_ascii=False) + '\n')
        self.events.flush()

    def startTest(self, test):
        self.started = time.monotonic()
        self.record('start', test)
        super().startTest(test)

    def stopTest(self, test):
        self.completed_ids.append(test.id())
        self.record('stop', test, {'seconds': time.monotonic() - self.started})
        super().stopTest(test)

    def addFailure(self, test, err):
        self.record('failure', test, self._exc_info_to_string(err, test))
        super().addFailure(test, err)

    def addError(self, test, err):
        self.record('error', test, self._exc_info_to_string(err, test))
        super().addError(test, err)

    def addSubTest(self, test, subtest, err):
        if err is not None:
            self.record('subtest_failure', test, self._exc_info_to_string(err, test))
        super().addSubTest(test, subtest, err)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--shard', type=int, choices=range(SHARDS))
    parser.add_argument('--aggregate', type=Path)
    parser.add_argument('--inventory-only', action='store_true')
    parser.add_argument('--head', default=os.environ.get('WB_SOURCE_HEAD', 'local'))
    parser.add_argument('--environment', default=os.environ.get('WB_CI_ENVIRONMENT', 'local'))
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.aggregate is not None:
        reports = []
        for path in sorted(args.aggregate.rglob('shard-result.json')):
            report = json.loads(path.read_text(encoding='utf-8'))
            report['outcomes'] = json.loads((path.parent / 'gate-outcomes.json').read_text(encoding='utf-8'))
            reports.append(report)
        report = validate_reports(reports, head=args.head, environment=args.environment)
        write_json(args.output, report)
        print(json.dumps(report))
        return 0
    loader = unittest.TestLoader()
    tests = list(flatten(loader.discover(str(ROOT / 'tests'))))
    parts = partition(tests)
    full_ids = [t.id() for t in tests]
    if args.inventory_only:
        print(json.dumps({'tests': len(tests), 'unique_ids': len(set(full_ids)),
                          'shard_sizes': [len(part) for part in parts],
                          'loader_errors': loader.errors}))
        return 1 if loader.errors else 0
    if args.shard is None:
        parser.error('--shard required for execution')
    evidence = Path(os.environ['WB_REGRESSION_EVIDENCE_DIR'])
    evidence.mkdir(parents=True, exist_ok=True)
    selected = parts[args.shard]
    report = {'head': args.head, 'environment': args.environment, 'shard': args.shard,
              'python': list(sys.version_info[:3]), 'platform': sys.platform,
              'full_ids': full_ids, 'selected_ids': [t.id() for t in selected],
              'loader_errors': loader.errors, 'completed_normally': False,
              'success': False, 'completed_ids': [], 'tests_run': 0, 'skips': 0}
    write_json(evidence / 'shard-census.json', report)
    if loader.errors:
        write_json(evidence / 'shard-result.json', report)
        return 1
    started = time.monotonic()
    with (evidence / 'test-events.jsonl').open('w', encoding='utf-8') as events:
        result = None
        def result_factory(*args, **kwargs):
            nonlocal result
            result = RecordingResult(*args, events=events, **kwargs)
            return result
        try:
            unittest.TextTestRunner(verbosity=2, resultclass=result_factory).run(unittest.TestSuite(selected))
            report['completed_normally'] = True
        finally:
            if result is not None:
                report.update(success=result.wasSuccessful() and report['completed_normally'],
                              completed_ids=result.completed_ids, tests_run=result.testsRun,
                              skips=len(result.skipped), failures=len(result.failures), errors=len(result.errors))
            report['seconds'] = round(time.monotonic() - started, 3)
            write_json(evidence / 'shard-result.json', report)
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
