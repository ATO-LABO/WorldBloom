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

def inputs(name):
    folder=ROOT/'projects'/(name+'_shared_planning')
    return json.loads((folder/'world.yaml').read_text(encoding='utf8')), [json.loads(p.read_text(encoding='utf8')) for p in sorted((folder/'subjects').glob('*.yaml'))]

def run(raw,people,seed=0,out=None):
    with tempfile.TemporaryDirectory() as tmp:
        source=Path(tmp)/'world.yaml';source.write_text(json.dumps(raw,ensure_ascii=False),encoding='utf8')
        world=World(deepcopy(raw),source);cast={p['id']:Subject.from_definition(deepcopy(p)) for p in people};world.bind_subjects(cast)
        sim=Simulation(seed,world,cast,Path(out or tmp),record_explanations=True)
        rows=[json.loads(line) for line in sim.run().read_text(encoding='utf8').splitlines()]
    return world,cast,rows

def physical(rows,who):
    return [(r['turn'],r['verb'],r['args'],r['result']) for r in rows if r.get('subject')==who and r.get('kind')=='decision']

class SharedPlanningTests(unittest.TestCase):
    def test_four_authored_topics_reach_results_through_unforced_normal_choices(self):
        for name,root in [('reform','equal_eligibility'),('ship','beacon_sent'),('village','fence_built'),('melos','friend_saved')]:
            with self.subTest(name=name):
                raw,people=inputs(name);world,_,rows=run(raw,people)
                self.assertEqual(next(r for r in world.goal_rules.report(world) if r['condition']==root)['status'],'achieved')
                self.assertFalse(any(r.get('verb') in {'forced_action','force_action_fallback'} or r.get('forced') for r in rows))
                self.assertTrue(any(r.get('details',{}).get('social_event') for r in rows))

    def test_cooperation_refusal_uses_helpers_own_required_condition_then_requester_replans(self):
        raw,people=inputs('ship');people[1]['commitments'][1]['required']=True
        world,cast,rows=run(raw,people)
        events=[r.get('details',{}).get('social_event',{}) for r in rows]
        self.assertTrue(any(e.get('answer')=='refuse' for e in events))
        self.assertFalse(any(e.get('answer')=='accept' for e in events))
        self.assertEqual(cast[people[1]['id']].inventory['修理工具'],1)
        self.assertEqual(next(r for r in world.goal_rules.report(world) if r['condition']=='beacon_sent')['status'],'achieved')
        self.assertTrue(any(r.get('details',{}).get('response',{}).get('action')=='scan_debris' for r in rows))
        reasons=[r.get('details',{}).get('planning',{}).get('reason') for r in rows]
        self.assertIn('cooperation_refused',reasons)

    def test_acceptance_does_not_itself_create_resources_and_work_is_gated(self):
        raw,people=inputs('ship');world=World(raw,ROOT/'x.yaml');cast={p['id']:Subject.from_definition(p) for p in people};world.bind_subjects(cast)
        from engine.verbs import VerbEngine
        import random
        engine=VerbEngine(world,random.Random(0));requester,helper=(cast[p['id']] for p in people)
        outcome,_,_=engine.execute(helper,Action('respond',('prepare_reserve',)),turn=1,day=1)
        self.assertEqual(outcome,'invalid');self.assertFalse(requester.has_item('予備電池'))
        engine.execute(requester,Action('request_response',(helper.id,'prepare_reserve')),turn=1,day=1)
        engine.execute(helper,Action('answer_response',('reserve_help','accept')),turn=1,day=1)
        self.assertFalse(requester.has_item('予備電池'))
        self.assertEqual(world.social_planner.messages['reserve_help']['status'],'accept')

    def test_melos_observes_flood_replans_crossing_and_rest_then_supplies_real_evidence(self):
        raw,people=inputs('melos');world,cast,rows=run(raw,people)
        ds=physical(rows,'メロス');verbs=[v[1] for v in ds];args=[v[2] for v in ds]
        self.assertIn(['wedding'],args);self.assertIn(['prepare_crossing'],args);self.assertIn('rest',verbs)
        self.assertIn(['王','帰還の証拠'],args)
        self.assertEqual(sum(a==['王','帰還の証拠'] for a in args),1)
        self.assertIn('帰還の証拠',cast['王'].knowledge)
        decision=world.deliberation.records['return_review']['decision']
        self.assertEqual(decision['status'],'approved');self.assertLessEqual(decision['turn'],44)
        self.assertFalse(any(r.get('details',{}).get('activity',{}).get('phase')=='failed' for r in rows if r.get('subject')=='メロス'))
        self.assertTrue(any(change['coordinate']==['state','橋'] and change['after']=='流失' for r in rows
            for change in r.get('details',{}).get('planning_observation',{}).get('changes',[])))
        self.assertTrue(any(r.get('details',{}).get('planning',{}).get('observation_causes') for r in rows))

    def test_no_evidence_or_too_short_deadline_never_fabricates_approval(self):
        raw,people=inputs('melos');raw['goal_rules']['definitions'][0]['period']['end']=12
        world,_,rows=run(raw,people)
        self.assertEqual(world.goal_rules.report(world)[0]['status'],'failed')
        self.assertNotEqual(world.deliberation.records['return_review']['status'],'approved')
        self.assertTrue(all(r['status']!='achieved' for row in rows for r in row.get('details',{}).get('objective_outcomes',[])))

    def test_identifier_renaming_preserves_unforced_causal_trajectory(self):
        for topic in ['reform','ship','village','melos']:
            with self.subTest(topic=topic):
                raw,people=inputs(topic);original=run(raw,people)
                values=set(p['id'] for p in people)|set(raw['routes'])|set(raw['responses']['state'])
                values|={s['id'] for s in raw['responses']['actions']}|{c['id'] for c in raw['concerns']['conditions']}
                values|={r['id'] for r in raw['social_planning']['requests']}|{r['proposal'] for r in raw['social_planning']['proposals']}
                values|={f['id'] for f in raw.get('facts',[])}|{i['name'] for i in raw.get('items',[])}
                values|=set(raw.get('deliberation',{}).get('state',{}))
                mapping={v:'alias_'+str(i).zfill(3) for i,v in enumerate(sorted(values))}
                def rename(v):
                    if isinstance(v,str):
                        if v in mapping:return mapping[v]
                        for old,new in mapping.items():
                            v=v.replace('"'+old+'"','"'+new+'"').replace("'"+old+"'","'"+new+"'")
                        return v
                    if isinstance(v,list):return [rename(x) for x in v]
                    if isinstance(v,dict):return {mapping.get(k,k):rename(x) for k,x in v.items()}
                    return v
                other=run(rename(raw),rename(people));back={v:k for k,v in mapping.items()}
                def normalized(rows,who,inverse=None):
                    inverse=inverse or {};return [(t,v,[inverse.get(a,a) for a in args],r) for t,v,args,r in physical(rows,who)]
                self.assertEqual(normalized(original[2],people[0]['id']),normalized(other[2],mapping[people[0]['id']],back))

    def test_protocol_receipts_and_replan_causes_reference_actual_source_lines(self):
        raw,people=inputs('ship');people[1]['commitments'][1]['required']=True
        _,_,rows=run(raw,people)
        from gapengine.explanations import explain_rows
        from viewer.planning_view import model,render
        ex=explain_rows(rows,sha256='retained-test-hash',experiment='example',cell='actual')
        self.assertTrue(ex['social_records'])
        for item in ex['decisions']:
            for cause in item['outcome']['details'].get('planning',{}).get('causes',[]):
                self.assertLessEqual(cause['source']['line'],item['line'])
                self.assertEqual(cause['source']['sha256'],'retained-test-hash')
                if 'receipt_id' in cause:self.assertEqual(rows[cause['source']['line']-1]['details']['social_event']['receipt_id'],cause['receipt_id'])
                else:self.assertEqual(rows[cause['source']['line']-1]['details']['planning_observation']['id'],cause['observation_id'])
        self.assertTrue(any(e.get('social',{}).get('answer')=='refuse' for g in model(ex) for e in g['events']))
        html=render(ex);self.assertIn('協力を拒否',html);self.assertIn('実際に受け取った人物',html)
