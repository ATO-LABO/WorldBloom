from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from engine.actions import Action
from engine.sim import Simulation
from engine.subject import Subject
from engine.world import World

ROOT=Path(__file__).resolve().parents[1]


def reform():
    folder=ROOT/'projects/reform_objectives'
    raw=json.loads((folder/'world.yaml').read_text(encoding='utf8'))
    people=[json.loads(p.read_text(encoding='utf8')) for p in sorted((folder/'subjects').glob('*.yaml'))]
    raw['time']={'days':8,'slots':['朝','昼','夕','夜']}
    raw['responses']['version']=2
    raw['responses']['signals']=[];raw['responses']['channels']=[];raw['responses']['windows']=[]
    for action in raw['responses']['actions']: action['signal']=None
    raw['responses']['events']=[]
    for goal in raw['goal_rules']['definitions']: goal['period']={'start':1,'end':30}
    raw['planning']={'version':1,'actors':['真壁'],'max_depth':12,'max_nodes':2048}
    raw['social_planning']={'version':1,'proposals':[{'proposal':'main','requires':[],'reopen':True}],'requests':[]}
    verbs=['submit_proposal','present_proposal','review_proposal','amend_proposal','request_decision','decide_proposal']
    raw['action_timing']={'version':1,'rules':[{'verb':'move','duration':1,'interruptible':False},
        {'verb':'respond','duration':'work_steps','interruptible':True},{'verb':'rest','duration':1,'interruptible':True},
        {'verb':'await_response','duration':'protocol_steps','interruptible':False}]+[
        {'verb':v,'duration':1,'interruptible':False} for v in verbs]}
    for person in people:
        if person['id']=='真壁': person['verbs']=list(set(person['verbs'])|{'await_response'})
        person['stamina']={'max':50,'current':50,'recover_per_slot':1}
    return raw,people


def load(raw,people):
    world=World(deepcopy(raw),ROOT/'projects/reform_objectives/world.yaml')
    cast={p['id']:Subject.from_definition(deepcopy(p)) for p in people}
    world.bind_subjects(cast);world.goal_rules.record(world,1)
    return world,cast


def run(raw,people,seed=0):
    world,cast=load(raw,people)
    with tempfile.TemporaryDirectory() as folder:
        sim=Simulation(seed,world,cast,Path(folder),record_explanations=True)
        rows=[json.loads(line) for line in sim.run().read_text(encoding='utf8').splitlines()]
    return world,cast,rows


class SocialPlanningTests(unittest.TestCase):
    def test_common_plan_preserves_existing_situated_preference_for_revision_or_formal_judgment(self):
        from engine.verbs import VerbEngine
        import random
        chosen=[]
        for stubborn in [0,1]:
            raw,people=reform()
            for p in people:
                if p['id']=='真壁':p['traits']['stubbornness']=stubborn;p['traits']['diligence']=.1
            world,cast=load(raw,people);engine=VerbEngine(world,random.Random(0))
            engine.execute(cast['真壁'],Action('submit_proposal',('main','original','0')),turn=1,day=1)
            for who in world.deliberation.specs['main']['reviewers']:
                engine.execute(cast[who],Action('review_proposal',('main','1')),turn=2,day=1)
            options=world.planner.candidates(world,cast['真壁'],3,1)
            chosen.append(max(options,key=lambda entry:entry[1])[0].verb)
        self.assertEqual(chosen,['amend_proposal','request_decision'])

    def test_goal_search_includes_real_submission_wait_and_implementation(self):
        raw,people=reform();world,cast=load(raw,people)
        plan=world.planner.search(world,cast['真壁'],'equal_eligibility',1)
        self.assertTrue(plan['steps'])
        self.assertEqual(plan['steps'][0]['verb'],'submit_proposal')
        self.assertIn('await_response',[s['verb'] for s in plan['steps']])
        self.assertEqual(plan['steps'][-1]['verb'],'respond')
        self.assertTrue(plan['assumptions'])
        self.assertEqual(world.deliberation.records['main']['status'],'draft')
        self.assertEqual(world.responses.state['implementation_stage'],'pending')

    def test_wait_does_not_apply_hypothetical_approval(self):
        raw,people=reform();world,cast=load(raw,people)
        from engine.verbs import VerbEngine
        import random
        result,details,_=VerbEngine(world,random.Random(0)).execute(cast['真壁'],Action('await_response',('proposal','main','decision','0')),turn=1,day=1)
        self.assertEqual(result,'waited');self.assertFalse(details['social_wait']['forecast_applied'])
        self.assertEqual(world.deliberation.records['main']['status'],'draft')

    def test_unforced_episode_finishes_only_after_peer_judgment_and_real_work(self):
        raw,people=reform();world,cast,rows=run(raw,people)
        decisions=[r for r in rows if r['kind']=='decision' and r['subject']=='真壁']
        self.assertTrue(any(r['verb']=='submit_proposal' for r in decisions))
        self.assertEqual(world.goal_rules.report(world)[0]['status'],'achieved')
        self.assertEqual(world.deliberation.records['main']['status'],'approved')
        self.assertEqual(world.responses.state['implementation_stage'],'implemented')
        self.assertTrue(any(r.get('details',{}).get('social_event',{}).get('verb')=='decide_proposal' for r in rows))

    def test_private_reviewer_weights_do_not_change_proposers_initial_forecast(self):
        raw,people=reform();world,cast=load(raw,people)
        first=world.planner.search(world,cast['真壁'],'equal_eligibility',1)
        changed=deepcopy(raw)
        for who,profile in changed['deliberation']['proposals'][0]['profiles'].items():
            if who!='真壁': profile['minimum']=9999
        other,others=load(changed,people)
        second=other.planner.search(other,others['真壁'],'equal_eligibility',1)
        self.assertEqual(first,second)

    def test_knowledge_prerequisite_enters_common_causal_search(self):
        raw,people=reform()
        raw['concerns']['conditions'].append({'id':'dossier_ready','label':'資料が揃う','mode':'achieve','visibility':'observed',
            'test':{'kind':'knowledge','fact':'移行費用試算','actors':['真壁'],'rule':'all'}})
        raw['social_planning']['proposals'][0]['requires']=['dossier_ready']
        for person in people:
            if person['id']=='真壁': person['knowledge']=[]
        raw['deliberation']['proposals'][0]['profiles']['真壁']['requires']=['移行費用試算']
        raw['responses']['actions'].append({'id':'collect_dossier','label':'資料を揃える','condition':'dossier_ready',
            'signal':None,'zone':raw['zones'][0]['name'],'actors':['真壁'],'requires':[],'items':{},'cost':1,'steps':1,
            'preconditions':{},'effects':[{'kind':'knowledge','actor':'真壁','fact':'移行費用試算','value':True}]})
        world,cast=load(raw,people)
        plan=world.planner.search(world,cast['真壁'],'equal_eligibility',1)
        self.assertTrue(plan['steps']);self.assertEqual(plan['steps'][0]['args'],['collect_dossier'])
