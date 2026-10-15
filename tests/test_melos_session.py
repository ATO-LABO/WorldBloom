"""Bounded parity/resume checks of the approved profile, never a sensitivity run."""
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from gapengine.genome import Genome
from gapengine.melos_choice_provider import MelosChoiceProvider
from execution.melos_session import MelosSession
from execution.melos_adapter import _hash


@unittest.skipUnless(os.environ.get('MELOS_APPROVED_ADAPTER_DIR') and os.environ.get('MELOS_INPUT_DIR'),
                     'approved external fixture paths not supplied')
class MelosSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder=Path(os.environ['MELOS_APPROVED_ADAPTER_DIR'])
        sys.path.insert(0,str(folder))
        import melos_runner
        import ninegene_melos
        cls.base=melos_runner;cls.original=ninegene_melos
        root=Path(os.environ['MELOS_INPUT_DIR'])
        cls.base.ROOT=root.parent;cls.original.ROOT=root.parent
        cls.inputs={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob('*.yaml'))}
        cls.sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [folder/'melos_runner.py',folder/'ninegene_melos.py']}

    def session(self,seed=0):
        genome=Genome.neutral()
        trial=self.base.Trial(genome,seed)
        return MelosSession(trial,input_hashes=self.inputs,source_hashes=self.sources,
                            choice_provider=MelosChoiceProvider(genome,seed))

    def test_uninterrupted_matches_approved_ninetrial_rows_and_results(self):
        original=self.original.NineTrial(Genome.neutral(),0);result,rows=original.run()
        session=self.session(0);session.run()
        self.assertTrue(session.finished)
        self.assertEqual(session.rt.rows,rows)
        self.assertEqual(session.trial.state,result['state'])
        self.assertEqual(session.trial.decisions,original.decisions)
        self.assertEqual(session.trial.count,original.count)
        self.assertEqual(session.rt.registry.export_state(),original.rt.registry.export_state())
        self.assertEqual(session.trial.audit(),original.audit())

    def test_midmove_resume_matches_continuous_and_done_noop(self):
        continuous=self.session(0);continuous.run()
        split=self.session(0);split.run(until=73)
        self.assertEqual(split.rt.people['Melos'].zone,'移動中')
        state=json.loads(json.dumps(split.export_state()))
        fresh=self.session(0);fresh.restore_state(state);fresh.run()
        self.assertEqual(fresh.export_state(),continuous.export_state())
        before=fresh.export_state();fresh.run();self.assertEqual(fresh.export_state(),before)

    def test_atomic_disk_resume_rebuilds_exact_rows_and_result(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            continuous=self.session(0);continuous.run_saved(root/'continuous')
            split=self.session(0);split.run_saved(root/'split',until=73)
            (root/'split/layers.jsonl').write_bytes(b'stale-derived-output')
            fresh=self.session(0);fresh.run_saved(root/'split',resume=True)
            self.assertEqual((root/'continuous/layers.jsonl').read_bytes(),(root/'split/layers.jsonl').read_bytes())
            self.assertEqual((root/'continuous/result.json').read_bytes(),(root/'split/result.json').read_bytes())
            self.assertEqual((root/'continuous/calendar-state.json').read_bytes(),(root/'split/calendar-state.json').read_bytes())

    def test_wedding_seed63_matches_original_and_resumes_half_work(self):
        original=self.original.NineTrial(Genome.neutral(),63);result,rows=original.run()
        continuous=self.session(63);continuous.run()
        self.assertTrue(result['state']['wedding'])
        self.assertEqual(continuous.rt.rows,rows)
        split=self.session(63);split.run(until=2220)
        self.assertEqual(split.stage.phase,'physical')
        self.assertEqual(split.stage.pending['candidate']['id'],'wedding')
        fresh=self.session(63);fresh.restore_state(json.loads(json.dumps(split.export_state())));fresh.run()
        self.assertEqual(fresh.export_state(),continuous.export_state())

    def test_branch_failure_rolls_back_choice_rng_rows_and_physical_state(self):
        session=self.session(0);before=session.export_state()
        with patch.object(session,'_action',side_effect=RuntimeError('injected after choice')):
            with self.assertRaises(RuntimeError):session.run(until=73)
        self.assertEqual(session.export_state(),before)
        control=self.session(0);control.run(until=73);session.run(until=73)
        self.assertEqual(session.export_state(),control.export_state())

    def test_original_loop_exhaustion_precedes_late_outcome_break(self):
        session=self.session();session.step_index=40
        session.trial.state['outcome']='both_alive_pardoned'
        session._choose_step()
        self.assertEqual(session.trial.state['outcome'],'decision_budget_observation_incomplete')

    def test_rehashed_continuation_provider_and_budget_tampering_rejected(self):
        session=self.session();session.run(until=73);before=session.export_state()
        empty=self.session().stage.export_state()['state']['provider']
        def branch(s):s['stage']['state']['pending']['continuation']={'branch':'report'}
        def provider(s):s['stage']['state']['provider']=deepcopy(empty)
        def budget(s):s['step_index']=40
        def padded_budget(s):
            count=len(s['stage']['state']['runtime']['rows'])
            s['step_index']=40
            s['step_history'].extend({'step':i,'at':73,'row_start':count,'row_end':count} for i in range(2,41))
        def omitted_budget(s):s['step_index']=0;s['step_history']=[]
        def fake_wedding(s):s['stage']['state']['external']['state']['wedding']=True
        def fake_death(s):s['stage']['state']['external']['state']['dead_friend']=True
        def fake_return(s):s['stage']['state']['external']['state']['returned_at']=73
        for edit in (branch,provider,budget,padded_budget,omitted_budget,fake_wedding,fake_death,fake_return):
            bad=deepcopy(before);edit(bad['state'])
            bad['state']['stage']['sha256']=_hash(bad['state']['stage']['state']);bad['sha256']=_hash(bad['state'])
            with self.assertRaises(ValueError):session.restore_state(bad)
            self.assertEqual(session.export_state(),before)

    def test_existing_failure_branches_keep_death_deadline_order_and_resume(self):
        # Explicit unit fixtures at already-observed obstacles, not new worlds,
        # parameter sweeps, or sensitivity experiments.
        for obstacle in ('flood','bandits'):
            def prepare(t):
                t.advance(3200)
                t.rt.people['Melos'].zone='east_bank' if obstacle=='flood' else 'pass'
                t.state['direction']='return'
                t.set_energy(10,'unit-fixture-own-energy')
                if obstacle=='flood':t.observe_river()
                return t
            original=prepare(self.original.NineTrial(Genome.neutral(),1))
            result,rows=original.run()
            def make():
                trial=prepare(self.base.Trial(Genome.neutral(),1))
                return MelosSession(trial,input_hashes=self.inputs,source_hashes=self.sources,
                                    choice_provider=MelosChoiceProvider(Genome.neutral(),1))
            continuous=make();continuous.run()
            with self.subTest(obstacle=obstacle):
                self.assertTrue(result['state']['dead_melos']);self.assertTrue(result['state']['dead_friend'])
                self.assertEqual(continuous.rt.rows,rows)
                split=make();split.run(until=3500)
                self.assertEqual(split.phase,'advance_only')
                fresh=make();fresh.restore_state(json.loads(json.dumps(split.export_state())));fresh.run()
                self.assertEqual(fresh.export_state(),continuous.export_state())

    def test_constructor_rejects_mismatched_provider_seed_or_genome(self):
        trial=self.base.Trial(Genome.neutral(),0)
        with self.assertRaises(ValueError):
            MelosSession(trial,input_hashes=self.inputs,source_hashes=self.sources,
                         choice_provider=MelosChoiceProvider(Genome.neutral(),1))
        changed=Genome.neutral().to_dict();changed['risk_tolerance']=.1
        with self.assertRaises(ValueError):
            MelosSession(trial,input_hashes=self.inputs,source_hashes=self.sources,
                         choice_provider=MelosChoiceProvider(Genome.from_dict(changed),0))

    def test_tampered_session_cursor_rejects_before_mutation(self):
        session=self.session();session.run(until=73);before=session.export_state()
        for key,value in [('phase','finished'),('step_index',True),('advance_target',5)]:
            bad=deepcopy(before);bad['state'][key]=value;bad['sha256']=_hash(bad['state'])
            with self.subTest(key=key),self.assertRaises(ValueError):session.restore_state(bad)
            self.assertEqual(session.export_state(),before)

if __name__=='__main__':unittest.main()
