"""Counterfactual information boundaries and actual concern-driven execution."""
from copy import deepcopy
import json
from pathlib import Path
import random
import tempfile
import unittest

from engine.actions import Action, candidates
from engine.subject import Subject, Belief
from engine.world import World
from engine.verbs import VerbEngine
from engine.sim import Simulation

ROOT = Path(__file__).resolve().parents[1]


def inputs(project='village_response'):
    folder = ROOT / 'projects' / project
    return (json.loads((folder/'world.yaml').read_text(encoding='utf8')),
            [json.loads(p.read_text(encoding='utf8')) for p in sorted((folder/'subjects').glob('*.yaml'))])


def load(raw=None, people=None, project='village_response'):
    original, definitions = inputs(project)
    w = World(deepcopy(raw if raw is not None else original), ROOT/'projects'/project/'world.yaml')
    ps = {p['id']: Subject.from_definition(p) for p in deepcopy(people if people is not None else definitions)}
    w.bind_subjects(ps)
    return w, ps, w.responses, VerbEngine(w, random.Random(3))


class ResponseTests(unittest.TestCase):
    def test_heard_report_produces_response_but_hidden_truth_does_not(self):
        w,p,r,e=load();hero=p['桃太郎']
        self.assertFalse([a for a,_ in r.candidates(w,hero,1) if a.meta.get('response_plan')])
        e.execute(p['知らせ人'],Action('share_knowledge',('桃太郎','danger_report')),turn=1,day=1)
        before=r.candidates(w,hero,2)
        self.assertTrue(any(a.meta.get('response_plan') for a,_ in before))
        w.responses.state['danger']='resolved';w.responses.state['protected']='no'
        after=r.candidates(w,hero,2)
        self.assertEqual(before,after)

    def test_distrusted_report_and_old_report_do_not_activate_plan(self):
        w,p,r,e=load();w.relations.change('桃太郎','知らせ人',affinity=-1.5)
        e.execute(p['知らせ人'],Action('share_knowledge',('桃太郎','danger_report')),turn=1,day=1)
        self.assertEqual(r.view(w,p['桃太郎'],'danger_report',2)['status'],'uncertain')
        p['桃太郎'].beliefs['danger_report']=Belief('active',1)
        self.assertEqual(r.view(w,p['桃太郎'],'danger_report',31)['status'],'stale')

    def test_public_condition_without_commitment_does_not_create_defense_goal(self):
        w,p,r,e=load();p['知らせ人'].verbs.add('respond')
        self.assertIn('no_commitment',r.options(w,p['知らせ人'],1)[0]['excluded'])

    def test_actual_share_keeps_original_observation_time(self):
        w,p,r,e=load();e.execute(p['知らせ人'],Action('share_knowledge',('桃太郎','danger_report')),turn=8,day=1)
        self.assertEqual(r.view(w,p['桃太郎'],'danger_report',8)['observed_at'],0)
        self.assertEqual(r.view(w,p['桃太郎'],'danger_report',8)['received_at'],8)

    def test_response_moves_one_edge_and_arrival_is_not_protection(self):
        raw,people=inputs();raw['movement']['action_weight']=0
        w,p,r,e=load(raw,people);h=p['桃太郎'];h.beliefs['danger_report']=Belief('active',1)
        a=next(a for a,_ in r.candidates(w,h,1) if a.meta.get('response_plan'))
        self.assertEqual(a.args,('峠',))
        self.assertEqual(e.execute(h,a,turn=1,day=1)[0],'moved')
        self.assertEqual(h.zone,'峠');self.assertEqual(r.state['danger'],'active')
        a=next(a for a,_ in r.candidates(w,h,2) if a.meta.get('response_plan'))
        e.execute(h,a,turn=2,day=1)
        self.assertEqual(h.zone,'村');self.assertEqual(r.state['danger'],'active')
        self.assertEqual(e.execute(h,Action('respond',('secure',)),turn=3,day=1)[0],'working')
        self.assertEqual(r.state['danger'],'active')
        self.assertEqual(e.execute(h,Action('respond',('secure',)),turn=4,day=1)[0],'responded')
        self.assertEqual(r.state['danger'],'resolved')

    def test_deadline_route_resources_evidence_and_stamina_exclude_impossible_plans(self):
        for expected,mutate in [
            ('too_late',lambda w,p,r:None),
            ('unreachable',lambda w,p,r:p['桃太郎'].range_zones.clear()),
            ('insufficient_stamina',lambda w,p,r:setattr(p['桃太郎'],'stamina',0)),
            ('missing_evidence',lambda w,p,r:r.actions['secure']['requires'].append('danger_report'))]:
            w,p,r,e=load();p['桃太郎'].beliefs['danger_report']=Belief('active',1)
            mutate(w,p,r)
            self.assertIn(expected,r.options(w,p['桃太郎'],12 if expected=='too_late' else 1)[0]['excluded'])

    def test_required_conflicting_effect_is_not_offered(self):
        raw,people=inputs();raw['concerns']['conditions'].append({'id':'other_safe','label':'別の保護対象',
            'mode':'maintain','visibility':'observed','test':{'kind':'state','key':'other','equals':'yes'}})
        raw['responses']['state']['other']='yes';raw['responses']['locations']['other']='村'
        raw['concerns']['commitments']['桃太郎'].append({'condition':'other_safe','priority':10,'required':True})
        raw['responses']['actions'][0]['effects'].append({'kind':'state','key':'other','value':'no'})
        w,p,r,e=load(raw,people);p['桃太郎'].beliefs['danger_report']=Belief('active',1)
        self.assertIn('required_condition_conflict',r.options(w,p['桃太郎'],1)[0]['excluded'])
        self.assertFalse(any(a.verb=='request_response' for a,_ in r.candidates(w,p['桃太郎'],1)))
        self.assertEqual(e.execute(p['桃太郎'],Action('request_response',('犬','secure')),turn=1,day=1)[0],'invalid')

    def test_priority_prefers_more_valued_threat(self):
        raw,people=inputs();second=deepcopy(raw['concerns']['conditions'][0]);second['id']='other'
        raw['concerns']['conditions'].append(second)
        raw['concerns']['commitments']['桃太郎'].append({'condition':'other','priority':2,'required':False})
        sig=deepcopy(raw['responses']['signals'][0]);sig['fact']='other_report';sig['condition']='other'
        raw['facts'].append({'id':'other_report','values':['active','resolved'],'sources':[]})
        raw['responses']['signals'].append(sig)
        act=deepcopy(raw['responses']['actions'][0]);act.update(id='other_action',condition='other',signal='other_report')
        raw['responses']['actions'].append(act)
        w,p,r,e=load(raw,people)
        for fact in r.signals:p['桃太郎'].beliefs[fact]=Belief('active',1)
        offered=[a.meta['response_plan'] for a,_ in r.candidates(w,p['桃太郎'],1) if a.meta.get('response_plan')]
        self.assertEqual(offered,['secure'])

    def test_correction_cancels_plan_and_keeps_actual_world_unchanged(self):
        w,p,r,e=load();p['桃太郎'].beliefs['danger_report']=Belief('active',1)
        a=next(a for a,_ in r.candidates(w,p['桃太郎'],1) if a.meta.get('response_plan'))
        e.execute(p['桃太郎'],a,turn=1,day=1)
        p['桃太郎'].beliefs['danger_report']=Belief('resolved',1)
        markers=r.tick(w,2)
        self.assertNotIn('桃太郎',r.active);self.assertEqual(r.state['danger'],'active')
        self.assertTrue(any(m['verb']=='response_plan_cancelled' for m in markers))

    def test_actual_precondition_failure_has_no_predicted_effect(self):
        w,p,r,e=load();p['桃太郎'].zone='村';p['桃太郎'].beliefs['danger_report']=Belief('active',1)
        # A hidden physical precondition can fail without changing the report.
        r.state['protected']='no';r.actions['secure']['preconditions']['protected']='yes'
        r.locations['protected']='旅先'
        before=r.state.copy();result,detail,_=e.execute(p['桃太郎'],Action('respond',('secure',)),turn=1,day=1)
        self.assertEqual(result,'failed');self.assertEqual(r.state,before)

    def test_request_waits_for_delivery_receiver_decides_and_reply_is_delayed(self):
        w,p,r,e=load();p['桃太郎'].beliefs['danger_report']=Belief('active',1)
        p['犬'].zone='村'
        result,detail,_=e.execute(p['桃太郎'],Action('request_response',('犬','secure')),turn=1,day=1)
        self.assertEqual(result,'requested');self.assertNotIn('犬',r.active)
        rid=detail['response']['request'];self.assertEqual(r.requests[rid]['status'],'in_transit')
        self.assertEqual(e.execute(p['犬'],Action('answer_response',(rid,'accept')),turn=2,day=1)[0],'invalid')
        r.tick(w,3)
        self.assertEqual(r.requests[rid]['status'],'delivered')
        result,_,_=e.execute(p['犬'],Action('answer_response',(rid,'accept')),turn=3,day=1)
        self.assertEqual(result,'answered');self.assertEqual(r.state['danger'],'active')
        self.assertIsNone(r.requests[rid]['known_reply'])
        r.tick(w,5);self.assertEqual(r.requests[rid]['known_reply'],'accept')
        e.execute(p['犬'],Action('respond',('secure',)),turn=5,day=1)
        e.execute(p['犬'],Action('respond',('secure',)),turn=6,day=1)
        self.assertEqual(r.requests[rid]['status'],'completed')
        self.assertEqual(r.requests[rid]['known_reply'],'accept')
        r.tick(w,8);self.assertEqual(r.requests[rid]['known_reply'],'completed')

    def test_receiver_can_refuse_for_own_values(self):
        w,p,r,e=load();p['桃太郎'].beliefs['danger_report']=Belief('active',1)
        w.concerns.config['commitments'].pop('犬')
        _,d,_=e.execute(p['桃太郎'],Action('request_response',('犬','secure')),turn=1,day=1)
        r.tick(w,3);rid=d['response']['request']
        answers=[a.args[1] for a,_ in r.candidates(w,p['犬'],3) if a.verb=='answer_response']
        self.assertEqual(answers,['refuse'])
        self.assertEqual(e.execute(p['犬'],Action('answer_response',(rid,'accept')),turn=3,day=1)[0],'invalid')

    def test_channel_does_not_leak_receiver_private_state_to_sender_candidates(self):
        w,p,r,e=load();p['桃太郎'].beliefs['danger_report']=Belief('active',1)
        before=[x for x in r.candidates(w,p['桃太郎'],1) if x[0].verb=='request_response']
        p['犬'].zone='旅先';p['犬'].stamina=0;w.concerns.config['commitments'].pop('犬')
        after=[x for x in r.candidates(w,p['桃太郎'],1) if x[0].verb=='request_response']
        self.assertEqual(before,after)

    def test_continuous_violation_is_not_erased_by_later_repair(self):
        w,p,r,e=load();r.state['protected']='no';r.record(w,1)
        r.state['protected']='yes';r.record(w,16)
        self.assertEqual(r.temporal['village_safe']['first_breach'],1)
        self.assertEqual(r.temporal['village_safe']['status'],'violated')

    def test_by_deadline_has_explicit_pending_success_and_failure(self):
        raw,people=inputs();raw['responses']['windows'][0]['mode']='by_deadline'
        w,p,r,e=load(raw,people);r.state['protected']='no';r.record(w,1)
        self.assertEqual(r.temporal['village_safe']['status'],'pending')
        r.record(w,16);self.assertEqual(r.temporal['village_safe']['status'],'deadline_missed')
        w,p,r,e=load(raw,people);r.record(w,1);r.state['protected']='no';r.record(w,16)
        self.assertEqual(r.temporal['village_safe']['status'],'satisfied')

    def test_environment_damage_is_prevented_only_by_actual_resolution(self):
        w,p,r,e=load();r.tick(w,13)
        self.assertEqual(r.state['protected'],'no')
        w,p,r,e=load();r.state['danger']='resolved';markers=r.tick(w,13)
        self.assertEqual(r.state['protected'],'yes')
        self.assertTrue(next(m for m in markers if m['verb']=='response_environment')['details']['prevented'])

    def test_execution_rechecks_verbs_location_and_stamina(self):
        w,p,r,e=load();hero=p['桃太郎'];hero.beliefs['danger_report']=Belief('active',1)
        self.assertEqual(e.execute(hero,Action('respond',('secure',)),turn=1,day=1)[0],'invalid')
        hero.zone='村';hero.verbs.remove('respond')
        self.assertEqual(e.execute(hero,Action('respond',('secure',)),turn=1,day=1)[0],'invalid')

    def test_declared_vitality_effect_keeps_lifecycle_timestamp_consistent(self):
        from engine import vitality
        w,p,r,e=load();r._apply(w,[{'kind':'vitality','actor':'犬','value':'downed'}],4)
        self.assertEqual(p['犬'].downed_since,4)
        # The ordinary recovery loop can now process this authored downing.
        vitality.tick(p['犬'],w,40,list(p.values()))
        self.assertEqual(p['犬'].vitality,'revived');self.assertIsNone(p['犬'].downed_since)
        r._apply(w,[{'kind':'vitality','actor':'犬','value':'dead'}],41)
        self.assertIsNone(p['犬'].downed_since)

    def test_bad_and_teleport_definitions_are_rejected(self):
        for mutation in [lambda r:r['responses'].update(version=True),
                         lambda r:r['responses']['actions'][0]['effects'].append({'kind':'zone','actor':'犬','value':'旅先'}),
                         lambda r:r['responses']['signals'][0].update(condition='missing'),
                         lambda r:r['responses']['actions'][0].update(steps=0)]:
            raw,people=inputs();mutation(raw)
            with self.assertRaises(ValueError):load(raw,people)

    def test_three_domains_run_in_ordinary_simulation_with_recorded_causes(self):
        with tempfile.TemporaryDirectory() as temp:
            for project in ('village_response','ship_response','reform_response'):
                w,p,r,e=load(project=project)
                sim=Simulation(2,w,p,Path(temp)/project,record_explanations=True)
                rows=[json.loads(line) for line in sim.run().read_text(encoding='utf8').splitlines()]
                self.assertTrue(any(row.get('verb')=='respond' and row.get('result')=='responded' for row in rows))
                self.assertEqual(r.state['danger'],'resolved')
                self.assertTrue(any('responses' in row for row in rows if row.get('kind')=='snapshot'))

    def test_candidates_are_pure_and_old_worlds_do_not_opt_in(self):
        w,p,r,e=load();before=r.snapshot();r.candidates(w,p['桃太郎'],1);r.candidates(w,p['桃太郎'],1)
        self.assertEqual(r.snapshot(),before)
        old=World.from_yaml(ROOT/'projects/secret_values/world.yaml')
        self.assertIsNone(old.responses)

    def test_ordinary_simulation_returns_and_works_without_forcing_hero(self):
        raw,people=inputs();raw['responses']['actions'][0]['actors']=['桃太郎']
        with tempfile.TemporaryDirectory() as temp:
            w,p,r,e=load(raw,people)
            sim=Simulation(0,w,p,Path(temp),record_explanations=True)
            rows=[json.loads(line) for line in sim.run().read_text(encoding='utf8').splitlines()]
            actions=[row for row in rows if row.get('kind')=='decision' and row['subject']=='桃太郎']
            moves=[row for row in actions if row['verb']=='move']
            self.assertEqual([row['args'][0] for row in moves[:2]],['峠','村'])
            self.assertTrue(all('forced' not in row for row in actions))
            self.assertTrue(any(row['verb']=='respond' and row['result']=='responded' for row in actions))
            self.assertEqual(r.state['protected'],'yes')

    def test_cooperation_can_complete_in_ordinary_simulation(self):
        raw,people=inputs();raw['responses']['actions'][0]['actors']=['犬']
        with tempfile.TemporaryDirectory() as temp:
            completed=[]
            for seed in range(6):
                w,p,r,e=load(raw,people)
                sim=Simulation(seed,w,p,Path(temp)/str(seed),record_explanations=True);sim.run()
                completed.extend(q for q in r.requests.values() if q['status']=='completed')
            self.assertTrue(completed)

    def test_proposal_approval_does_not_implement_but_can_authorize_local_work(self):
        from gapengine.deliberation_episode import load_episode
        from engine.responses import Responses
        w,p=load_episode(ROOT/'projects/reform_values/world.yaml')
        raw,_=inputs();config=deepcopy(raw['responses'])
        hero=w.protagonist;site=p[hero].zone
        config['locations']={k:site for k in config['state']}
        config['channels']=[];config['events']=[];config['windows']=[]
        config['signals'][0]['condition']='implementation'
        config['actions'][0].update(condition='implementation',actors=[hero],zone=site,
            authorization={'proposal':'main','status':'approved'})
        config['state']['protected']='marital';config['actions'][0]['effects'][1]['value']='individual'
        from engine.concerns import Concerns
        values=deepcopy(w.concerns.config)
        values['conditions'].append({'id':'implementation','label':'実際の適用資格','mode':'achieve','visibility':'observed',
                                    'test':{'kind':'state','key':'protected','equals':'individual'}})
        values['commitments'][hero].append({'condition':'implementation','priority':10,'required':True})
        w.facts['danger_report']={'id':'danger_report','values':['active','resolved'],'sources':[]}
        w.truth['danger_report']='active';w.responses=Responses(config);w.concerns=Concerns(values)
        w.concerns.bind(w,p);w.responses.bind(w);p[hero].verbs.add('respond')
        p[hero].beliefs['danger_report']=Belief('active',1);p[hero].stamina=p[hero].stamina_max=10
        self.assertIn('authorization_pending',w.responses.options(w,p[hero],1)[0]['excluded'])
        # Controlled status fixture isolates the gate; it is not a full reform episode.
        w.deliberation.records['main']['status']='approved'
        self.assertEqual(w.responses.state['protected'],'marital')
        engine=VerbEngine(w,random.Random(0))
        self.assertEqual(engine.execute(p[hero],Action('respond',('secure',)),turn=2,day=1)[0],'working')
        self.assertEqual(engine.execute(p[hero],Action('respond',('secure',)),turn=3,day=1)[0],'responded')
        self.assertEqual(w.responses.state['protected'],'individual')

    def test_real_reform_deliberation_precedes_actual_implementation(self):
        from gapengine.response_episode import load_response
        with tempfile.TemporaryDirectory() as temp:
            world,people=load_response(ROOT/'projects/reform_implementation/world.yaml')
            sim=Simulation(0,world,people,Path(temp),record_explanations=True)
            rows=[json.loads(line) for line in sim.run().read_text(encoding='utf8').splitlines()]
            approval=next(r for r in rows if r.get('verb')=='decide_proposal' and r.get('kind')=='decision')
            implemented=next(r for r in rows if r.get('verb')=='respond' and r.get('result')=='responded')
            self.assertLess(approval['turn'],implemented['turn'])
            self.assertEqual(world.deliberation.state['給付資格'],'個人')
            conditions=approval['details']['deliberation_event']['world_concerns']
            self.assertEqual(next(c for c in conditions if c['condition']=='equal_eligibility')['state'],'false')
            self.assertEqual(world.responses.state['implementation_stage'],'implemented')


if __name__ == '__main__':unittest.main()
