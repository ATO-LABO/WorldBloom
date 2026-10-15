from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch
from engine.world import World
from engine.subject import Subject
from engine.sim import Simulation

ROOT=Path(__file__).resolve().parents[1]
class TargetObservationTests(unittest.TestCase):
    def fixture(self,out,observe=True):
        p=ROOT/'projects/momotaro';world=World.from_yaml(p/'world.yaml');world.days=1;world.slots=world.slots[:3]
        people={s.id:s for s in (Subject.from_yaml(f) for f in sorted((p/'subjects').glob('*.yaml')))}
        return Simulation(0,world,people,out,terminal_policy={'target_as_observation':observe,'death_stops':True})

    def test_goal_observed_then_more_actions_then_horizon_stop(self):
        with tempfile.TemporaryDirectory() as d:
            sim=self.fixture(Path(d));ending={'id':'goal-fixture','label':'already reached'}
            with patch.object(sim,'_ending',return_value=ending):sim.run()
            rows=[json.loads(line) for line in (Path(d)/'layers.jsonl').read_text(encoding='utf-8').splitlines()]
            goal=next(i for i,r in enumerate(rows) if r.get('verb')=='target_observed')
            self.assertTrue(any(r.get('kind')=='decision' for r in rows[goal+1:]))
            self.assertEqual(sum(r.get('verb')=='target_observed' for r in rows),1)
            record=sim.run_record();self.assertEqual(record['observed_through'],2)
            self.assertEqual(record['actual_terminal']['verb'],'observation_finished')
            self.assertEqual(record['stop_reason'],'observation_finished')
            self.assertEqual(record['observed_targets'][0]['event_id'],'goal-fixture')
            self.assertLess(record['observed_targets'][0]['line'],record['actual_terminal']['line'])

    def test_true_ending_stop_remains_terminal(self):
        with tempfile.TemporaryDirectory() as d:
            sim=self.fixture(Path(d),False)
            with patch.object(sim,'_ending',return_value={'id':'stop-fixture','label':'stop'}):sim.run()
            record=sim.run_record();self.assertEqual(record['observed_through'],0)
            self.assertEqual(record['actual_terminal']['verb'],'ending')
            self.assertEqual(record['observed_targets'],[])

    def test_saved_pre_fix_observation_is_not_misreported_as_stop(self):
        from engine.run_record import _saved_projection
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);rows=[{'kind':'header','terminal_policy':{'target_as_observation':True}},
                {'kind':'event','turn':1,'verb':'ending','id':'goal','details':{}},
                {'kind':'decision','turn':2,'verb':'move','details':{}}]
            (p/'layers.jsonl').write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
            record,_=_saved_projection(p);self.assertIsNone(record['actual_terminal']);self.assertEqual(record['stop_reason'],'unknown')
            self.assertEqual(record['observed_targets'][0]['event_id'],'goal')
