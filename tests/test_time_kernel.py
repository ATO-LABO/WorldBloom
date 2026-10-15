"""Shared clock invariants, independent of stories and UI modes."""
from fractions import Fraction
import unittest

from engine.time_kernel import TimeAxis, TimeActionKernel, ActionWindow, BoundaryStage, BoundaryResult


class TimeKernelTests(unittest.TestCase):
    def test_display_partition_does_not_change_elapsed_action_or_energy(self):
        action = ActionWindow(2, 17, 'minute')
        def accrue(points):
            return sum((Fraction(action.elapsed(a, b), 120)
                        for a, b in zip(points, points[1:])), Fraction())
        self.assertEqual(accrue([0, 30]), accrue([0, 1, 3, 7, 11, 15, 18, 30]))
        self.assertEqual(accrue([0, 30]), Fraction(15, 120))

    def test_same_loop_runs_symbolic_and_minute_schedules(self):
        logical = TimeActionKernel(TimeAxis.logical(2, ['first', 'last']))
        physical = TimeActionKernel(TimeAxis('minute', 0, 3))
        def run(kernel):
            seen = []
            kernel.run(kernel.sequence([0, 1, 2, 3]), [BoundaryStage('observe', lambda f: seen.append((f.at, f.elapsed)))])
            return seen
        self.assertEqual(run(logical), run(physical))
        self.assertEqual(logical.axis.label(2)['day'], 2)
        self.assertEqual(logical.axis.label(2)['slot'], 'first')

    def test_action_duration_is_not_energy_or_decision_interval(self):
        k = TimeActionKernel(TimeAxis('minute', 0, 20))
        window = k.window(10)
        k.run(k.sequence([0, 3, 8, 10]), [BoundaryStage('display', lambda f: None)], until=10)
        self.assertEqual(window.finish, 10)
        self.assertEqual(k.now, 10)

    def test_cross_unit_and_outside_observation_are_rejected_without_advancing(self):
        k = TimeActionKernel(TimeAxis('minute', 10, 20))
        for call in [lambda: k.window(1, unit='logical_step'), lambda: k.window(11),
                     lambda: k.dispatch(9, []), lambda: k.dispatch(True, [])]:
            with self.assertRaises(ValueError): call()
            self.assertEqual(k.now, 10)

    def test_same_time_completion_precedes_deadline_and_new_start(self):
        state = {'complete': False, 'due_met': None, 'new_start_saw': None}
        k = TimeActionKernel(TimeAxis('minute', 0, 10))
        stages = [BoundaryStage('physical', lambda f: state.update(complete=True)),
                  BoundaryStage('deadline', lambda f: state.update(due_met=state['complete'])),
                  BoundaryStage('choice', lambda f: state.update(new_start_saw=state['due_met']))]
        k.dispatch(10, stages)
        self.assertTrue(state['due_met']); self.assertTrue(state['new_start_saw'])

    def test_halt_prevents_same_boundary_start(self):
        seen = []
        k = TimeActionKernel(TimeAxis('minute', 0, 10))
        self.assertTrue(k.run(k.sequence([0, 5, 10]), [
            BoundaryStage('end', lambda f: BoundaryResult.HALT),
            BoundaryStage('choice', lambda f: seen.append(f.at))]))
        self.assertEqual(seen, []); self.assertEqual(k.now, 0)

    def test_save_failure_restores_clock_and_domain_before_retry(self):
        k = TimeActionKernel(TimeAxis('minute', 0, 10))
        state = []
        def fail(): raise OSError('disk unavailable')
        with self.assertRaises(OSError):
            k.run(k.sequence([5]), [BoundaryStage('effect', lambda f: state.append(f.at))],
                  before_boundary=lambda: list(state), after_boundary=fail,
                  on_error=lambda saved: state.__setitem__(slice(None), saved))
        self.assertEqual(state, []); self.assertEqual(k.now, 0)
        k.run(k.sequence([5]), [BoundaryStage('effect', lambda f: state.append(f.at))], until=5)
        self.assertEqual(state, [5])

    def test_failed_stage_and_explicit_restore_do_not_replay_effects(self):
        k = TimeActionKernel(TimeAxis('minute', 0, 10))
        def fail(f): raise ValueError('invalid boundary')
        with self.assertRaises(ValueError): k.dispatch(5, [BoundaryStage('fail', fail)])
        self.assertEqual(k.now, 0)
        k.restore_position(7); self.assertEqual(k.now, 7)
        k.restore_position(0); self.assertEqual(k.now, 0)

    def test_reentrancy_and_nonprogressing_provider_are_bounded(self):
        k = TimeActionKernel(TimeAxis('minute', 0, 10))
        with self.assertRaises(ValueError):
            k.dispatch(1, [BoundaryStage('recursive', lambda f: k.dispatch(2, []))])
        with self.assertRaises(ValueError): k.run(lambda now, limit: now, [])
        self.assertEqual(k.now, 0)

    def test_duplicate_stages_and_backwards_provider_are_rejected(self):
        k = TimeActionKernel(TimeAxis('minute', 1, 10))
        with self.assertRaises(ValueError):
            k.dispatch(2, [BoundaryStage('same', lambda f: None), BoundaryStage('same', lambda f: None)])
        with self.assertRaises(ValueError): k.run(lambda now, limit: now - 1, [])
        self.assertEqual(k.now, 1)


if __name__ == '__main__': unittest.main()
