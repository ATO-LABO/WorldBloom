"""State vs belief, cross-domain conditions and genuine proposal execution."""
from copy import deepcopy
import json
from pathlib import Path
import random
import tempfile
import unittest

from engine.actions import Action
from engine.concerns import Concerns
from engine.subject import Belief
from engine.verbs import VerbEngine
from gapengine.deliberation_episode import load_episode,run_batch

ROOT=Path(__file__).resolve().parents[1]


class ConcernTests(unittest.TestCase):
    def load(self,project='rescue_values'):
        world,people=load_episode(ROOT/'tests/fixtures/legacy_concerns'/project/'world.yaml')
        return world,people,world.concerns

    def test_three_contract_examples_and_maintain_vs_achieve(self):
        for project,cid,state,progress in [('reform_values','equal_eligibility','false','pending'),
                                           ('rescue_values','crew_alive','true','maintained'),
                                           ('secret_values','secret_kept','true','maintained')]:
            w,p,c=self.load(project);result=c.evaluate(w,cid)
            self.assertEqual((result['state'],result['progress']),(state,progress))
        w,p,c=self.load();p['レム'].vitality='dead'
        self.assertEqual(c.evaluate(w,'crew_alive')['progress'],'breached')

    def test_hidden_actual_state_and_mistaken_belief_are_independent(self):
        w,p,c=self.load();p['レム'].zone='遠方'
        self.assertEqual(c.evaluate(w,'crew_alive',actor='ナギ')['state'],'unknown')
        p['ナギ'].beliefs['crew_alive']=Belief('false',.9)
        self.assertEqual(c.evaluate(w,'crew_alive')['state'],'true')
        view=c.evaluate(w,'crew_alive',actor='ナギ')
        self.assertEqual(view['state'],'false');self.assertEqual(view['sources'],['belief'])
        p['レム'].vitality='dead'
        self.assertEqual(c.evaluate(w,'crew_alive',actor='ナギ'),view)

    def test_visible_state_supersedes_old_belief_but_does_not_silently_rewrite_it(self):
        w,p,c=self.load();p['ナギ'].beliefs['crew_alive']=Belief('false',.9)
        self.assertEqual(c.evaluate(w,'crew_alive',actor='ナギ')['state'],'true')
        self.assertEqual(p['ナギ'].beliefs['crew_alive'].value,'false')

    def test_unknown_and_zero_confidence_never_become_success(self):
        w,p,c=self.load();p['レム'].zone='遠方'
        p['ナギ'].beliefs['crew_alive']=Belief('true',0)
        result=c.evaluate(w,'crew_alive',actor='ナギ')
        self.assertEqual(result['state'],'unknown');self.assertEqual(result['progress'],'unknown')
        p['ナギ'].update_belief('crew_alive','true',.7)
        self.assertEqual(c.evaluate(w,'crew_alive',actor='ナギ')['state'],'true')

    def test_secret_truth_changes_without_changing_ignorant_actor_view(self):
        w,p,c=self.load('secret_values')
        view=c.evaluate(w,'secret_kept',actor='アオ')
        p['レン'].knowledge.add('避難先')
        self.assertEqual(c.evaluate(w,'secret_kept')['state'],'false')
        self.assertEqual(c.evaluate(w,'secret_kept',actor='アオ'),view)
        p['アオ'].beliefs.pop('secret_kept')
        self.assertEqual(c.evaluate(w,'secret_kept',actor='アオ')['state'],'unknown')

    def test_projection_does_not_read_hidden_truth_for_unchanged_properties(self):
        w,p,c=self.load('secret_values');s=w.deliberation.specs['main'];pr=s['concern_projections']['redacted']
        before=c.assessment(w,'アオ',projection=pr)
        p['レン'].knowledge.add('避難先')
        self.assertEqual(c.assessment(w,'アオ',projection=pr),before)
        self.assertEqual(c.evaluate(w,'secret_kept')['state'],'false')

    def test_unjustified_forecast_is_unknown_even_with_optimistic_existing_belief(self):
        w,p,c=self.load();pr=w.deliberation.specs['main']['concern_projections']['reserve']
        p['ナギ'].beliefs['crew_alive']=Belief('true',1)
        result=c.evaluate(w,'crew_alive',actor='ナギ',projection=pr,justified=False)
        self.assertEqual(result['state'],'unknown')
        self.assertEqual(result['sources'],['unjustified_forecast'])

    def test_each_reviewers_evidence_trust_is_separate_from_proposer_trust(self):
        w,p,c=self.load();rt=w.deliberation;s=rt.specs['main'];engine=VerbEngine(w,random.Random(0))
        engine.execute(p['ナギ'],Action('submit_proposal',('main','direct','0')),turn=1,day=1)
        engine.execute(p['レム'],Action('review_proposal',('main','1')),turn=1,day=1)
        terms=s['alternatives'][1]['terms']
        self.assertEqual(rt._assessment(w,s,'ナギ',terms)['stance'],'support')
        s['profiles']['イサ']['evidence_trust']['レム']=.1
        self.assertEqual(rt._assessment(w,s,'イサ',terms)['stance'],'undecided')
        s['choice_policy']['trust']['レム']=1.0
        self.assertEqual(rt._assessment(w,s,'イサ',terms)['stance'],'undecided')
        s['profiles']['イサ']['evidence_trust']['レム']=.9
        self.assertEqual(rt._assessment(w,s,'イサ',terms)['stance'],'support')

    def test_forecast_does_not_apply_live_effects(self):
        w,p,c=self.load('secret_values');pr=w.deliberation.specs['main']['concern_projections']['full']
        before=set(p['レン'].knowledge)
        self.assertEqual(c.evaluate(w,'secret_kept',actor='アオ',projection=pr)['state'],'false')
        self.assertEqual(p['レン'].knowledge,before)
        self.assertEqual(w.deliberation.state['支援'],'未確保')

    def test_actual_disclosure_updates_only_participants_from_the_visible_transfer(self):
        w,p,c=self.load('secret_values');p['レン'].zone='相談室'
        engine=VerbEngine(w,random.Random(0))
        result,details,_=engine.execute(p['ユラ'],Action('share_knowledge',('レン','避難先')),turn=1,day=1)
        self.assertEqual(result,'shared')
        self.assertEqual(c.evaluate(w,'secret_kept')['state'],'false')
        self.assertEqual(p['ユラ'].beliefs['secret_kept'].value,'false')
        self.assertEqual(p['レン'].beliefs['secret_kept'].value,'false')
        self.assertEqual(p['アオ'].beliefs['secret_kept'].value,'true')
        self.assertEqual({r['actor'] for r in details['concern_observations']},{'ユラ','レン'})

    def test_place_item_and_directed_bond_adapters_use_real_state(self):
        w,p,_=self.load('secret_values');w.items['薬']={};p['アオ'].inventory['薬']=2
        conditions=[
            {'id':'place_goal','label':'居場所','mode':'maintain','visibility':'observed','test':{'kind':'zone','actor':'アオ','equals':'相談室'}},
            {'id':'medicine','label':'薬の必要量','mode':'achieve','visibility':'observed','test':{'kind':'inventory','actor':'アオ','item':'薬','minimum':3}},
            {'id':'bond','label':'本人から相手への親しさ','mode':'maintain','visibility':'observed','test':{'kind':'relation','observer':'アオ','target':'サキ','minimum':.2}}]
        c=Concerns({'version':1,'conditions':conditions,'commitments':{'アオ':[{'condition':d['id'],'priority':1,'required':False} for d in conditions]}})
        c.bind(w,p)
        self.assertEqual(c.evaluate(w,'place_goal',actor='アオ')['state'],'true')
        self.assertEqual(c.evaluate(w,'medicine',actor='アオ')['progress'],'pending')
        p['アオ'].inventory['薬']=3
        self.assertEqual(c.evaluate(w,'medicine',actor='アオ')['progress'],'achieved')
        w.relations.change('アオ','サキ',affinity=.4)
        self.assertEqual(c.evaluate(w,'bond',actor='アオ')['state'],'true')
        self.assertEqual(c.evaluate(w,'bond',actor='ユラ')['state'],'unknown')

    def test_priority_tiers_dominate_lower_tiers_and_required_unknown_defers(self):
        w,p,c=self.load('secret_values');spec=w.deliberation.specs['main']
        full=c.assessment(w,'アオ',projection=spec['concern_projections']['full'])
        redacted=c.assessment(w,'アオ',projection=spec['concern_projections']['redacted'])
        self.assertEqual(full['stance'],'oppose');self.assertEqual(redacted['stance'],'support')
        gain=c.gain(full,redacted)
        self.assertEqual(gain['priority'],3);self.assertGreater(gain['normalized'],0)
        p['アオ'].beliefs.pop('secret_kept')
        self.assertEqual(c.assessment(w,'アオ',projection=spec['concern_projections']['redacted'])['stance'],'undecided')

    def test_boolean_knowledge_quantifiers_use_three_valued_logic(self):
        w,p,c=self.load('secret_values');d=c.conditions['secret_kept'];d['test']['actors']=['アオ','レン']
        p['アオ'].beliefs.pop('secret_kept')
        # Self knows the secret: one confirmed breach suffices despite unknown peer.
        self.assertEqual(c.evaluate(w,'secret_kept',actor='アオ')['state'],'false')
        p['アオ'].knowledge.clear()
        self.assertEqual(c.evaluate(w,'secret_kept',actor='アオ')['state'],'unknown')

    def test_condition_beliefs_validate_without_creating_second_truth_store(self):
        w,p,c=self.load('secret_values')
        self.assertNotIn('secret_kept',w.facts);self.assertNotIn('secret_kept',w.truth)
        w.bind_subjects(p)
        self.assertEqual(p['アオ'].beliefs['secret_kept'].value,'true')
        p['アオ'].beliefs['secret_kept'].value='maybe'
        with self.assertRaises(ValueError):w.bind_subjects(p)

    def test_invalid_schema_references_and_collisions_fail_closed(self):
        w,p,c=self.load('secret_values');raw=c.config
        mutations=[lambda d:d['conditions'][0].update(mode='guess'),
                   lambda d:d['conditions'][0].update(test={'kind':'eval','code':'True'}),
                   lambda d:d['commitments']['アオ'][0].update(priority=True),
                   lambda d:d['commitments']['アオ'][0].update(required=1),
                   lambda d:d['commitments']['アオ'].append(deepcopy(d['commitments']['アオ'][0]))]
        for mutate in mutations:
            changed=deepcopy(raw);mutate(changed)
            with self.assertRaises(ValueError):Concerns(changed)
        changed=deepcopy(raw);changed['conditions'][0]['test']['actors']=['nobody']
        with self.assertRaises(ValueError):Concerns(changed).bind(w,p)
        changed=deepcopy(raw);changed['conditions'][0]['id']='避難先';changed['commitments']={}
        with self.assertRaises(ValueError):Concerns(changed)

    def test_invalid_projection_does_not_reach_execution(self):
        w,p,c=self.load('secret_values');s=w.deliberation.specs['main']
        for bad in [{'kind':'knowledge','actor':'レン','fact':'避難先','value':'yes'},
                    {'kind':'vitality','actor':'missing','value':'alive'},
                    {'kind':'state','key':'missing','value':'x'},
                    {'kind':'zone','actor':'アオ','value':'missing'}]:
            altered=deepcopy(s);altered['concern_projections']['full']['effects']=[bad]
            with self.assertRaises(ValueError):c.validate_projections(w,altered)

    def test_same_seed_changes_actual_choice_when_protected_condition_changes(self):
        from engine.sim import Simulation
        selected=[]
        for care_about_secret in [True,False]:
            w,p,c=self.load('secret_values')
            if not care_about_secret:
                # Keep the world and every review identical; only proposer values differ.
                c.config['commitments']['アオ']=[{'condition':'help_received','priority':2,'required':True}]
            with tempfile.TemporaryDirectory() as temp:
                sim=Simulation(0,w,p,Path(temp)/'episode');rt=w.deliberation;s=rt.specs['main']
                engine=sim.verb_engine
                engine.execute(p['アオ'],Action('submit_proposal',('main','full','0')),turn=1,day=1)
                for actor in s['reviewers']:engine.execute(p[actor],Action('review_proposal',('main','1')),turn=1,day=1)
                sim.turn,sim.day=2,1
                selected.append(sim.choose_action(p['アオ'])[0].verb)
        self.assertEqual(selected,['amend_proposal','request_decision'])

    def test_normal_runs_three_domains_preserve_actual_outcomes_and_reason_records(self):
        with tempfile.TemporaryDirectory() as temp:
            for project in ['reform_values','rescue_values','secret_values']:
                target=Path(temp)/project;summary=run_batch(ROOT/'tests/fixtures/legacy_concerns'/project/'world.yaml',target,[0,4])
                self.assertEqual(summary['counts'],{'approved':2})
                for seed in [0,4]:
                    material=json.loads((target/f'seed-{seed:04}/scene-material.json').read_text(encoding='utf8'))
                    event=next(e for e in material['events'] if e['verb']=='amend_proposal')
                    self.assertEqual(event['choice_basis']['policy']['version'],2)
                    self.assertNotIn('goal_priority',event['choice_basis']['policy'])
                    judgment=next(e for e in material['events'] if e['verb']=='decide_proposal')
                    actual={r['condition']:r['progress'] for r in judgment['world_concerns']}
                    if project=='reform_values':self.assertEqual(actual['equal_eligibility'],'pending')
                    if project=='rescue_values':
                        self.assertEqual(actual['crew_alive'],'maintained');self.assertEqual(actual['survivors_helped'],'pending')
                    if project=='secret_values':
                        self.assertEqual(actual['secret_kept'],'maintained');self.assertEqual(actual['help_received'],'pending')

    def test_legacy_worlds_have_no_concerns_and_keep_old_mechanisms(self):
        w,p=load_episode(ROOT/'projects/reform_hearing/world.yaml')
        self.assertIsNone(w.concerns)
        self.assertEqual(w.deliberation.specs['main']['choice_policy']['version'],1)


if __name__=='__main__':unittest.main()
