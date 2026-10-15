from copy import deepcopy
import unittest
from engine.execution_plan import ExecutionPlan,run_spec_hashes

H='1'*64
def plan():
    identity={'id':'fixture','version':1,'source_hash':H,'config_hash':H}
    return {'schema_version':1,'execution_kind':'independent_trials',
            'sampler':identity,'provider':identity,'classification':identity,
            'predeclared_seeds':[0,1,2],'planned_trials':3,
            'selection':dict.fromkeys(['fitness_selection','breeding','keep_filter','canon_feedback'],False),
            'observation':{'unit':'minute','start':0,'end':10,'stop_contract_hash':H}}

class ExecutionPlanTests(unittest.TestCase):
    def test_independent_controller_preserves_failures_and_unresolved_denominator(self):
        p=ExecutionPlan(plan());saved=[];calls=[]
        def evaluate(seed):
            calls.append(seed)
            if seed==1:raise ValueError('fixture failure')
            return {'status':'unresolved' if seed==2 else 'in_time','fitness':0}
        result=p.independent(evaluate,saved.append)
        self.assertEqual(calls,[0,1,2]);self.assertEqual(len(saved),3)
        self.assertEqual([r['outcome']['status'] for r in result],['in_time','execution_failure','unresolved'])
        self.assertTrue(all(r['execution_plan_hash']==p.sha256 for r in saved))

    def test_independent_rejects_selection_canon_feedback_duplicate_seeds_or_count(self):
        for flag in plan()['selection']:
            p=plan();p['selection'][flag]=True
            with self.assertRaises(ValueError):ExecutionPlan(p)
        for seeds,count in [([0,0,2],3),([0,1,2],2)]:
            p=plan();p['predeclared_seeds']=seeds;p['planned_trials']=count
            with self.assertRaises(ValueError):ExecutionPlan(p)

    def test_ui_mode_is_not_an_execution_plan_and_does_not_change_semantic_hash(self):
        p=plan();p['ui_mode']='review'
        with self.assertRaises(ValueError):ExecutionPlan(p)
        first=run_spec_hashes({'duration':10},{'interval':60})
        refined=run_spec_hashes({'duration':10},{'interval':7})
        self.assertEqual(first['semantic_hash'],refined['semantic_hash'])
        self.assertNotEqual(first['run_spec_hash'],refined['run_spec_hash'])
        self.assertNotEqual(first['presentation_hash'],refined['presentation_hash'])

    def test_frozen_plan_and_persistence_failure_never_replace_a_seed(self):
        value=plan();p=ExecutionPlan(value);value['predeclared_seeds'][0]=99
        self.assertEqual(p.to_dict()['predeclared_seeds'][0],0)
        calls=[]
        def save(record):raise OSError('cannot persist')
        with self.assertRaises(OSError):p.independent(lambda seed:(calls.append(seed) or {'status':'in_time'}),save)
        self.assertEqual(calls,[0])
