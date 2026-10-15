"""Coverage gates must reject a green but incomplete or mismatched CI run."""
from copy import deepcopy
import io
import json
import unittest

from regression_partition import partition, validate_reports, RecordingResult


class CensusGateTests(unittest.TestCase):
    def reports(self):
        # Preserve the duplicate invocation inherited from unittest discovery.
        full = ['a.Case.one', 'b.Case.two', 'b.Case.two']
        return [dict(head='head', environment='ubuntu-latest-py3.13', shard=i, full_ids=full,
                     python=[3,13,0], platform='linux',
                     selected_ids=ids, completed_ids=ids, loader_errors=[],
                     completed_normally=True, success=True, tests_run=len(ids),
                     outcomes={'python':'success', 'javascript':'success' if i == 0 else 'skipped',
                               'before':'skipped', 'after':'skipped'},
                     skips=0, seconds=1) for i, ids in enumerate([full[:1], full[1:]])]

    def test_complete_census_preserves_existing_multiplicity(self):
        report = validate_reports(self.reports(), head='head', environment='ubuntu-latest-py3.13')
        self.assertEqual(report['tests_run'], 3)
        self.assertEqual(report['unique_test_ids'], 2)
        self.assertEqual(report['existing_duplicate_invocations'], 1)

    def test_missing_duplicate_and_extra_execution_are_rejected(self):
        for ids in ([], ['b.Case.two'], ['b.Case.two'] * 3):
            reports = self.reports()
            reports[1].update(selected_ids=ids, completed_ids=ids, tests_run=len(ids))
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                validate_reports(reports, head='head', environment='ubuntu-latest-py3.13')

    def test_wrong_head_environment_discovery_and_incomplete_shards_rejected(self):
        for edits in ({'head': 'old'}, {'environment': 'other'}, {'full_ids': []},
                      {'python': [3,11,0]}, {'platform': 'win32'},
                      {'loader_errors': ['import failed']}, {'success': False},
                      {'completed_normally': False}, {'completed_ids': []}, {'tests_run': 0},
                      {'outcomes': {'python':'success', 'javascript':'failure', 'before':'skipped', 'after':'skipped'}}):
            reports = self.reports(); reports[0].update(deepcopy(edits))
            with self.subTest(edits=edits), self.assertRaises(ValueError):
                validate_reports(reports, head='head', environment='ubuntu-latest-py3.13')
        for reports in (self.reports()[:1], [self.reports()[0]] * 2):
            with self.assertRaises(ValueError):
                validate_reports(reports, head='head', environment='ubuntu-latest-py3.13')

    def test_partition_keeps_module_isolation_and_discovery_order(self):
        class Named(unittest.TestCase):
            def __init__(self, name):
                super().__init__(); self.name = name
            def id(self):
                return self.name
        tests = [Named(name) for name in ('a.C.one', 'b.C.two', 'b.C.two', 'c.C.three')]
        parts = partition(tests)
        self.assertEqual(sorted(id(t) for part in parts for t in part), sorted(map(id, tests)))
        self.assertTrue(any([t.id() for t in part] == ['b.C.two', 'b.C.two'] for part in parts))

    def test_assertion_trace_is_saved_before_suite_finishes(self):
        class Fails(unittest.TestCase):
            def runTest(self):
                self.assertEqual('expected', 'actual')
        events = io.StringIO()
        runner = unittest.TextTestRunner(stream=io.StringIO(), resultclass=lambda *a, **kw:
                                        RecordingResult(*a, events=events, **kw))
        result = runner.run(unittest.TestSuite([Fails()]))
        records = [json.loads(line) for line in events.getvalue().splitlines()]
        self.assertFalse(result.wasSuccessful())
        self.assertEqual([r['kind'] for r in records], ['start', 'failure', 'stop'])
        self.assertIn('AssertionError', records[1]['detail'])
        self.assertEqual(len(result.completed_ids), 1)
