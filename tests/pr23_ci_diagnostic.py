"""Temporary bounded PR23 diagnostics; this is not a full-suite gate."""
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]


def main():
    from melos_reference_sequence import reference_sequence
    evidence = Path(os.environ['WB_REGRESSION_EVIDENCE_DIR'])
    evidence.mkdir(parents=True, exist_ok=True)
    comparison = reference_sequence()
    comparison['python'] = sys.version
    (evidence / 'reference-24-choices.json').write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding='utf-8')
    names = [
        'test_melos_choice_provider.MelosChoiceProviderTests.test_same_input_history_draw_and_calculation_as_approved_algorithm',
        'test_independent_http_boundary.IndependentHttpBoundaryTests.test_real_http_capability_csrf_admission_and_idempotency',
        'test_melos_factory.FactoryTests.test_two_concurrent_factories_keep_root_and_import_path_isolated',
    ]
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestLoader().loadTestsFromNames(names))
    report = {'scope': 'Three bounded diagnostics only; NOT full regression',
              'python': sys.version, 'reference_choices_equal': len(comparison['choices']),
              'reference_digest': comparison['digest'], 'tests': result.testsRun,
              'failures': [{'test': str(test), 'traceback': trace} for test, trace in result.failures],
              'errors': [{'test': str(test), 'traceback': trace} for test, trace in result.errors]}
    (evidence / 'bounded-diagnostics.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
