from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from test_calendar_goal_planning import project, read, write, simulation
from test_social_planning import reform


class CalendarSocialTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.folder=self.root/'inputs'
        world,contract,person,_=project(self.folder,deadline='09:30')
        world['concerns']={'version':2,'conditions':[
            {'id':'ready','label':'Prepare the evidence','mode':'achieve','visibility':'observed','test':{'kind':'task','task':'pieces','minimum':3}},
            {'id':'approved','label':'Receive actual approval','mode':'achieve','visibility':'public','test':{'kind':'state','key':'approval','equals':'granted'}}]}
        world['goal_rules']['definitions']=[{'condition':'approved','success':{'condition':'approved'},'failure':{'constant':False},
            'period':{'start':{'date':'2026-11-01','time':'08:00'},'end':{'date':'2026-11-01','time':'09:30'}}}]
        raw,_=reform();spec=deepcopy(raw['deliberation']['proposals'][0])
        spec.update(id='review',title='Workshop review',place='Bench',proposer='Ada',authority='Bo',reviewers=['Bo'],required_support=['Bo'],quorum=1,
            profiles={'Ada':deepcopy(spec['profiles']['真壁']),'Bo':deepcopy(spec['profiles']['真壁'])},requires_state={})
        spec.pop('choice_policy',None);spec.pop('concern_projections',None)
        for profile in spec['profiles'].values(): profile['requires']=[]
        spec['outcomes']={s:{'approval':v} for s,v in [('approved','granted'),('rejected','denied'),('deferred','pending')]}
        world['deliberation']={'version':1,'state':{'approval':'pending'},'proposals':[spec]}
        world['social_planning']={'version':1,'proposals':[{'proposal':'review','requires':['ready'],'reopen':True}],'requests':[]}
        world['action_timing']={'version':1,'rules':[r for r in raw['action_timing']['rules'] if r['verb'] not in {'move','respond','rest'}]}
        person['commitments']=[{'condition':'approved','priority':9,'required':False}]
        person['stamina']={'max':100,'current':100,'recover_per_slot':1}
        verbs=['submit_proposal','present_proposal','review_proposal','amend_proposal','request_decision','decide_proposal','await_response']
        person['verbs']+=verbs
        peer=read(self.folder/'subjects/bo.yaml');peer['verbs']+=verbs
        contract['tasks']['pieces']['place']='Bench'
        write(self.folder/'subjects/ada.yaml',person);write(self.folder/'subjects/bo.yaml',peer)
        write(self.folder/'world.yaml',world);write(self.folder/'contract.yaml',contract)
        (self.folder/'choices.json').write_text('[]',encoding='utf8')

    def test_native_work_and_social_protocol_share_goal_search(self):
        sim=simulation(self.folder,self.root/'out');sim.slot_runtime.run()
        self.assertEqual(sim.slot_runtime.ledger['tasks']['pieces']['units'],3)
        self.assertEqual(sim.world.deliberation.records['review']['status'],'approved')
        self.assertEqual(sim.world.goal_rules.report(sim.world)[0]['status'],'achieved')
        choices=[r['details']['choice']['verb'] for r in sim.slot_runtime.rows if r['verb']=='decision']
        self.assertEqual(choices[:3],['work']*3)
        self.assertIn('review_proposal',choices);self.assertIn('decide_proposal',choices)

    def test_social_checkpoint_resume_is_exact_and_corruption_is_atomic(self):
        first=simulation(self.folder,self.root/'full');first.slot_runtime.run()
        split=simulation(self.folder,self.root/'split');split.slot_runtime.run(until=511)
        self.assertEqual(split.slot_runtime.busy['Ada']['verb'],'await_response')
        self.assertEqual(split.world.deliberation.records['review']['status'],'open')
        resumed=simulation(self.folder,self.root/'split');resumed.slot_runtime.run(resume=True)
        self.assertEqual(first.slot_runtime.export_state(),resumed.slot_runtime.export_state())
        saved=deepcopy(resumed.slot_runtime.export_state());changed=deepcopy(saved)
        changed['deliberation']['proposals']['review']['status']='rejected'
        with self.assertRaises(ValueError): resumed.slot_runtime.restore_state(changed)
        self.assertEqual(saved,resumed.slot_runtime.export_state())
