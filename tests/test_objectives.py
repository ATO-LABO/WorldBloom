"""Terminal outcomes, epistemic boundaries and self-motivated conflicting actors."""
from copy import deepcopy
from pathlib import Path
import json
import random
import tempfile
import unittest

from engine.actions import Action
from engine.objectives import Objectives, references
from engine.sim import Simulation
from engine.subject import Subject
from engine.verbs import VerbEngine
from engine.world import World

ROOT = Path(__file__).resolve().parents[1]


def inputs(project='village_objectives'):
    folder = ROOT/'projects'/project
    return (json.loads((folder/'world.yaml').read_text(encoding='utf8')),
            [json.loads(p.read_text(encoding='utf8')) for p in sorted((folder/'subjects').glob('*.yaml'))])


def load(raw=None, people=None, project='village_objectives'):
    original, cast = inputs(project)
    w = World(deepcopy(raw if raw is not None else original), ROOT/'projects'/project/'world.yaml')
    ps = {p['id']: Subject.from_definition(p) for p in deepcopy(cast if people is None else people)}
    w.bind_subjects(ps)
    return w, ps, w.goal_rules


def states(g, w):
    return {r['condition']:r for r in g.report(w)}


class ObjectiveRuleTests(unittest.TestCase):
    def test_pending_is_not_failure_and_early_failure_is_terminal(self):
        w,p,g=load();g.record(w,1)
        self.assertEqual(states(g,w)['food_taken']['status'],'pending')
        w.responses.state['raid']='repelled';g.record(w,2)
        result=states(g,w)['food_taken']
        self.assertEqual((result['status'],result['reason'],result['decided_at']),('failed','failure_condition',2))
        p['鬼'].inventory['食料袋']=1;w.responses.state['raid']='ongoing';g.record(w,3)
        self.assertEqual(states(g,w)['food_taken'],result)

    def test_achievement_latches_but_failure_wins_on_same_observation(self):
        w,p,g=load();p['鬼'].inventory['食料袋']=1;g.record(w,1)
        self.assertEqual(states(g,w)['food_taken']['status'],'achieved')
        p['鬼'].inventory['食料袋']=0;w.responses.state['raid']='repelled';g.record(w,2)
        self.assertEqual(states(g,w)['food_taken']['status'],'achieved')
        raw,ps=inputs();raw['goal_rules']['definitions'][1]['failure']={'constant':True}
        w,p,g=load(raw,ps);p['鬼'].inventory['食料袋']=1;g.record(w,1)
        self.assertEqual(states(g,w)['food_taken']['status'],'failed')

    def test_maintenance_requires_complete_period_and_remembers_transient_breach(self):
        w,p,g=load()
        for turn in range(1,12):g.record(w,turn,close=True)
        self.assertEqual(states(g,w)['village_safe']['status'],'pending')
        g.record(w,12)
        self.assertEqual(states(g,w)['village_safe']['status'],'pending')
        g.record(w,12,close=True)
        self.assertEqual(states(g,w)['village_safe']['status'],'achieved')
        w,p,g=load();g.record(w,1);w.responses.state['damage']='yes';g.record(w,1)
        w.responses.state['damage']='no';g.record(w,1,close=True)
        self.assertEqual(states(g,w)['village_safe']['status'],'failed')

    def test_deadline_closes_after_last_actions_and_not_before(self):
        w,p,g=load()
        for turn in range(1,12):g.record(w,turn,close=True)
        g.record(w,12)
        self.assertEqual(states(g,w)['food_taken']['status'],'pending')
        p['鬼'].inventory['食料袋']=1;g.record(w,12)
        g.record(w,12,close=True)
        self.assertEqual(states(g,w)['food_taken']['status'],'achieved')
        w,p,g=load()
        for turn in range(1,13):g.record(w,turn,close=True)
        self.assertEqual(states(g,w)['food_taken']['reason'],'deadline_unmet')

    def test_not_started_and_partial_observation_never_claim_maintained(self):
        raw,ps=inputs();raw['goal_rules']['definitions'][0]['period']['start']=3
        w,p,g=load(raw,ps);g.record(w,1)
        self.assertEqual(states(g,w)['village_safe']['status'],'not_started')
        g.record(w,12,close=True)
        self.assertEqual(states(g,w)['village_safe']['status'],'unknown')
        self.assertEqual(states(g,w)['village_safe']['reason'],'incomplete_observation')

    def test_actor_cannot_read_remote_failure_or_author_terminal_history(self):
        w,p,g=load();g.record(w,1)
        before=g.actor_view(w,'鬼','food_taken',2)
        options=w.responses.options(w,p['鬼'],2)
        w.responses.state.update(raid='repelled',damage='yes');g.record(w,2)
        self.assertEqual(states(g,w)['food_taken']['status'],'failed')
        self.assertEqual(g.actor_view(w,'鬼','food_taken',2)['status'],before['status'])
        self.assertEqual(w.responses.options(w,p['鬼'],2),options)
        p['鬼'].zone='村';g.record(w,3)
        self.assertEqual(g.actor_view(w,'鬼','food_taken',3)['status'],'failed')

    def test_unknown_failure_is_not_false_and_expression_uses_three_values(self):
        w,p,g=load()
        self.assertEqual(g.expression(w,{'not':{'condition':'raid_repelled'}},actor='鬼')['state'],'unknown')
        self.assertEqual(g.expression(w,{'all':[{'constant':False},{'condition':'raid_repelled'}]},actor='鬼')['state'],'false')
        self.assertEqual(g.expression(w,{'any':[{'constant':True},{'condition':'raid_repelled'}]},actor='鬼')['state'],'true')

    def test_unavailable_actors_do_not_acquire_new_outcome_knowledge(self):
        w,p,g=load();p['鬼'].zone='村';g.record(w,1)
        before=deepcopy(g.actor_records['鬼'])
        p['鬼'].vitality='dead';w.responses.state['raid']='repelled';g.record(w,2)
        self.assertEqual(g.actor_records['鬼'],before)
        self.assertEqual(states(g,w)['food_taken']['status'],'failed')

    def test_multiple_explicit_observation_zones_do_not_make_state_globally_public(self):
        w,p,g=load(project='ship_objectives');person=p['通信士']
        self.assertEqual(w.concerns.evaluate(w,'power_lost',actor='通信士')['state'],'false')
        person.zone='通路'
        self.assertEqual(w.concerns.evaluate(w,'power_lost',actor='通信士')['state'],'unknown')
        person.zone='通信室'
        self.assertEqual(w.concerns.evaluate(w,'power_lost',actor='通信士')['state'],'false')
        raw,ps=inputs(project='ship_objectives');raw['responses']['locations']['power']=['不存在']
        with self.assertRaises(ValueError):load(raw,ps,project='ship_objectives')

    def test_corrected_hearsay_can_change_recognition_but_witnessed_failure_stays(self):
        from engine.subject import Belief
        w,p,g=load();p['鬼'].beliefs['raid_repelled']=Belief('true',.7);g.record(w,1)
        self.assertEqual(g.actor_records['鬼']['food_taken']['status'],'failed')
        self.assertEqual(states(g,w)['food_taken']['status'],'pending')
        p['鬼'].beliefs['raid_repelled']=Belief('false',.9);g.record(w,2)
        self.assertEqual(g.actor_records['鬼']['food_taken']['status'],'pending')
        self.assertEqual(g.actor_records['鬼']['food_taken']['revised_from']['status'],'failed')
        self.assertTrue(any(a.meta.get('response_plan')=='raid_store' for a,_ in w.responses.candidates(w,p['鬼'],2)))
        p['鬼'].zone='村';w.responses.state['raid']='repelled';g.record(w,3)
        self.assertEqual(g.actor_records['鬼']['food_taken']['status'],'failed')
        w.responses.state['raid']='ongoing';g.record(w,4)
        self.assertEqual(g.actor_records['鬼']['food_taken']['status'],'failed')

    def test_continuous_recognition_retains_earlier_uncertainty(self):
        w,p,g=load();g.record(w,1,close=True)
        p['桃太郎'].zone='村'
        for turn in range(2,13):g.record(w,turn,close=True)
        own=deepcopy(g.actor_records['桃太郎']['village_safe'])
        self.assertEqual(own['status'],'achieved');self.assertEqual(own['confidence'],.8)
        self.assertIn('belief',own['sources'])
        self.assertEqual(states(g,w)['village_safe']['confidence'],1)
        w.responses.state['damage']='yes';g.record(w,13,close=True)
        self.assertEqual(g.actor_records['桃太郎']['village_safe'],own)

    def test_goal_failure_in_forecast_blocks_required_values_and_is_pure(self):
        w,p,g=load();before=g.snapshot(w)
        p['鬼'].zone='村'
        projection={'effects':[{'kind':'state','key':'raid','value':'repelled'}]}
        score=w.concerns.assessment(w,'鬼',projection=projection)
        self.assertIn('food_taken',score['blocked'])
        self.assertEqual(g.snapshot(w),before)

    def test_input_validation_rejects_unbounded_unknown_and_conflicting_judges(self):
        for mutate in (lambda w:w['goal_rules'].update(version=True),
                       lambda w:w['goal_rules']['definitions'][0]['period'].update(start=True),
                       lambda w:w['goal_rules']['definitions'][0]['period'].update(end=17),
                       lambda w:w['goal_rules']['definitions'][0].update(failure={'eval':'x'}),
                       lambda w:w['goal_rules']['definitions'][0].update(failure={'condition':'absent'}),
                       lambda w:w['goal_rules']['definitions'][0].update(success={'constant':True}),
                       lambda w:w['goal_rules']['definitions'].append(deepcopy(w['goal_rules']['definitions'][0])),
                       lambda w:w['responses']['windows'].append(dict(condition='village_safe',start=1,end=12,mode='continuous'))):
            raw,ps=inputs();mutate(raw)
            with self.subTest(mutate=mutate),self.assertRaises(ValueError):load(raw,ps)
        value={'condition':'village_safe'}
        for _ in range(6):value={'not':value}
        with self.assertRaises(ValueError):references(value)
        with self.assertRaises(ValueError):references({'all':[]})


class ObjectiveActionTests(unittest.TestCase):
    def test_ogre_chooses_invasion_from_own_values_without_signal_or_forcing(self):
        w,p,g=load();ogre=p['鬼']
        options=w.responses.candidates(w,ogre,1)
        plan=next(a for a,_ in options if a.meta.get('response_plan')=='raid_store')
        self.assertEqual((plan.verb,plan.args),('move',('山道',)))
        self.assertEqual(plan.meta['response_reason']['signal']['source'],'own_commitment')
        ogre.commitments=[]
        self.assertFalse([a for a,_ in w.responses.candidates(w,ogre,1) if a.meta.get('response_plan')])

    def test_period_capability_resources_and_known_failure_gate_proactive_actions(self):
        w,p,g=load();ogre=p['鬼'];p['鬼'].zone='村';g.record(w,1)
        w.responses.state['raid']='repelled';g.record(w,2)
        self.assertIn('objective_terminal',w.responses.options(w,ogre,2)[1]['excluded'])
        w,p,g=load();self.assertIn('objective_too_late',w.responses.options(w,p['鬼'],12)[1]['excluded'])
        w,p,g=load(project='ship_objectives');p['通信士'].inventory.clear()
        self.assertIn('missing_resources',w.responses.options(w,p['通信士'],1)[0]['excluded'])

    def test_runtime_rechecks_causal_guard_no_free_loot_and_no_world_damage_event(self):
        w,p,g=load();p['鬼'].zone='村';p['桃太郎'].zone='村'
        e=VerbEngine(w,random.Random(0))
        e.execute(p['鬼'],Action('respond',('raid_store',)),turn=1,day=1)
        w.responses.state['raid']='repelled'
        result,details,_=e.execute(p['鬼'],Action('respond',('raid_store',)),turn=2,day=1)
        self.assertNotEqual(result,'responded');self.assertFalse(p['鬼'].has_item('食料袋'))
        self.assertEqual(p['倉番'].inventory['食料袋'],1)
        self.assertEqual(w.responses.events,{})

    def test_normal_execution_in_three_domains_retains_terminal_records(self):
        with tempfile.TemporaryDirectory() as temp:
            for project in ('village_objectives','ship_objectives','reform_objectives'):
                w,p,g=load(project=project);sim=Simulation(0,w,p,Path(temp)/project,record_explanations=True)
                rows=[json.loads(line) for line in sim.run().read_text(encoding='utf8').splitlines()]
                self.assertTrue(all(r['status'] in {'achieved','failed'} for r in g.report(w)))
                self.assertTrue(any(r.get('verb')=='objective_outcome' for r in rows))
                self.assertTrue(any(r.get('kind')=='snapshot' and 'goal_rules' in r for r in rows))
                if project=='village_objectives':
                    actions=[r for r in rows if r.get('subject') in ('桃太郎','鬼') and r.get('kind')=='decision']
                    self.assertTrue(all('forced' not in r for r in actions))
                    self.assertTrue(any(r['subject']=='鬼' and r['verb']=='move' for r in actions))
                    self.assertEqual(sum(s.inventory.get('食料袋',0) for s in p.values()),1)


if __name__=='__main__':unittest.main()
