"""Explicit continuations for the approved Melos scenario on the common runtime.

No alternative physical engine, clock, category mapping, or outcome selection.
The caller supplies the initialized approved Trial and its declared inputs.
Not registered in the product API. Disk publication is a separate integration.
"""
from copy import deepcopy
import hashlib
import math
import sys
from pathlib import Path
from engine.provider_boundary import DecisionOffer
from gapengine.genome import Genome
from gapengine.melos_choice_provider import MelosChoiceProvider
from execution.melos_adapter import MelosActionStage, _hash, _json
from engine.calendar_checkpoint import CheckpointStore, atomic_bytes
from execution.provenance import canonical
from engine.decision_provider import provider_snapshot


def candidate(ident, label, kind='wait', minutes=1, features=None, **kw):
    return {'id':ident,'label':label,'kind':kind,'minutes':minutes,'features':features or {},**kw}


class MelosSession:
    CONTINUATIONS = {'village','marriage','outbound','city','release','swim','river',
                     'bandits','pass','spring','collapse','report','return_west'}

    def __init__(self, trial, *, input_hashes, source_hashes, choice_provider):
        if trial.reference is not False or trial.arm != 'baseline':
            raise ValueError('Only the approved unselected baseline profile is supported')
        if type(trial.genome) is not Genome or type(choice_provider) is not MelosChoiceProvider or choice_provider.config.seed!=trial.seed or choice_provider.config.genome!=canonical(trial.genome.to_dict()):
            raise ValueError('The actual nine-gene Genome and trial/provider seed must match')
        self.stage = MelosActionStage(trial, input_hashes=input_hashes,
                                     source_hashes={**source_hashes, 'melos_session': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}, choice_provider=choice_provider)
        self.phase = 'choose_step'
        self.step_index = 0
        self.step_history = []
        self.advance_target = None
        self.advance_started = None
        self.advance_outcome = None
        self.finished = False

    @property
    def trial(self): return self.stage.trial
    @property
    def rt(self): return self.stage.rt

    def _choose(self, actor, context, offered):
        t=self.trial; view=t.view(actor); ordinal=t.count[actor]
        view['decision_ordinal']=ordinal
        view['own_vitality']=self.rt.people[actor].vitality
        offer=DecisionOffer.create(view,offered)
        receipt=self.stage.provider.choose(offer,actor=actor,now=self.rt.now,ordinal=ordinal)
        t.count[actor]+=1
        row=t.event('subjective_choice',actor,[actor],context=context,actor_view=view,candidates=offered,
                    selected=receipt['selected'],calculation=receipt['calculation'],ordinal=ordinal,
                    rng_draw=receipt['rng_draw'],view_hash=_hash(view),
                    mechanism='actual_ninegene_policy' if actor=='Melos' else 'fixed_base_weight_rng',
                    no_future_truth=True,no_jev=True,
                    policy_information_boundary='ObservablePolicyWorld from ActorView only')
        t.decisions.append({'event_id':row['id'],'at':self.rt.now,'actor':actor,
                            'context':context,'selected':receipt['selected']})
        return receipt['candidate']

    def _action(self, c, branch, **local):
        if branch not in self.CONTINUATIONS: raise ValueError('Unknown continuation')
        if self.stage.start_action(c,continuation={'branch':branch,**local}):
            self.phase='action'
        else:
            self.phase='finalize'

    def _advance(self, target, outcome=None):
        if type(target) is not int or target<self.rt.now or target>self.rt.end:
            raise ValueError('Unsupported or backwards scenario advance')
        self.advance_started=self.rt.now;self.advance_target=target;self.advance_outcome=outcome;self.phase='advance_only'

    def _marriage_response(self):
        t=self.trial;s=t.state;p=t.p
        s['negotiated']=True
        t.event('wedding_request','Melos',['Sister','Groom','Melos'],request_at=self.rt.now,
                proposed_at=p['wedding_at'],reason='hostage disclosed' if s['disclosed'] else 'city business',sources=['S4','S5'])
        for a in ['Sister','Groom']:t.info[a]['wedding_request']=True
        sister=self._choose('Sister','marriage_response',[
            candidate('agree','婚礼に同意',features={'marry':2,'care':1}),
            candidate('delay','婚礼を延期',features={'marry':-1,'care':1})])
        groom=self._choose('Groom','marriage_response',[
            candidate('agree','準備不足でも婚礼に同意',features={'prepared':-1,'care':2 if s['disclosed'] else 1}),
            candidate('delay','葡萄の季節へ延期を望む',features={'prepared':1,'care':0})])
        s['agreed']=sister['id']==groom['id']=='agree'
        t.event('marriage_response',audience=['Sister','Groom','Melos'],sister=sister['id'],groom=groom['id'],
                agreed=s['agreed'],basis='A8/A9',not_wedding_completion=True)

    def _court_response(self):
        t=self.trial;s=t.state;p=t.p
        s['returned_at']=self.rt.now
        t.event('return_observed','Melos',['King','Selinuntius','Melos'],returned_at=self.rt.now,
                deadline=p['deadline'],promise_status=self.rt.registry.get('return-hostage')['status'])
        if s['dead_friend']:
            s['outcome']='late_return_friend_executed'
            t.event('late_release','King',['Melos','King'],target='Melos',basis='A7 King late offer enforcement; no friend resurrection')
            return
        s['friend_released']=True
        t.event('hostage_released','King',['King','Selinuntius','Melos'],target='Selinuntius',basis='A7 substitute surrender')
        t.info['King']['return_seen']=True
        t.info['King']['crowd_present']=self.rt.now>=p['execution_preparation_at']
        melos_confessed=False
        if s['own_doubt']:
            mc=self._choose('Melos','confession',[
                candidate('confess','途中の迷いを認める',features={'honor':2,'friend':1}),
                candidate('silent','迷いについて黙る',features={'self':1,'honor':-.5})])
            melos_confessed=mc['id']=='confess'
            if melos_confessed:t.event('melos_confession','Melos',['Melos','Selinuntius','King'],content='実際に生じた迷いを認める',basis='A9')
        offered=[candidate('silent','黙って再会を受け止める',features={'trust':1,'honesty':-.2})]
        if t.info['Selinuntius']['own_doubt']:
            offered.insert(0,candidate('confess','自分も一度疑ったと認める',features={'honesty':2,'trust':2}))
        response=self._choose('Selinuntius','friend_response',offered)
        s['confessed']=response['id']=='confess'
        t.event('friend_response','Selinuntius',['Melos','Selinuntius','King'],response=response['id'])
        t.info['King']['mutual_confession_seen']=s['confessed'] and melos_confessed
        crowd=t.info['King']['crowd_present'];recognition=2+int(t.info['King']['mutual_confession_seen'])+int(crowd)
        verdict=self._choose('King','verdict',[
            candidate('execute','刑をメロスへ移して執行',features={'control':1,'recognize':-1}),
            candidate('pardon','目撃した信実を認め赦す',features={'control':-.5,'recognize':recognition})])
        s['pardon']=verdict['id']=='pardon';s['dead_melos']=not s['pardon']
        if s['dead_melos']:self.rt.people['Melos'].vitality='dead'
        s['outcome']='both_alive_pardoned' if s['pardon'] else 'friend_saved_melos_executed'
        t.event('royal_verdict','King',['King','Selinuntius','Melos'],verdict=verdict['id'],
                observed_return=True,mutual_confession=t.info['King']['mutual_confession_seen'],crowd=crowd,basis='A7/A9 conditional hypothesis')

    def _after_action(self, completed):
        t=self.trial;s=t.state;p=t.p;c=completed['candidate'];local=completed['continuation'];branch=local['branch']
        self.phase='choose_step'
        if branch=='marriage':self._marriage_response()
        elif branch=='village':
            if c['id']=='disclose':
                s['disclosed']=True
                for a in ['Sister','Groom']:t.info[a].update(hostage='Selinuntius',deadline=p['deadline'])
                t.event('hostage_disclosed','Melos',['Sister','Groom','Melos'],deadline=p['deadline'],hostage='Selinuntius')
            for ident,field in [('rest_arrival','rested_arrival'),('wedding','wedding'),('banquet','banquet'),('sleep_dawn','slept')]:
                if c['id']==ident:s[field]=True
            if c['id'].startswith('leave_'):s['direction']='return';t.observe_river()
            if c['id']=='stay':s['outcome']='village_survival_friend_executed'
        elif branch=='outbound':
            if local['to']=='village':t.event('village_arrival','Melos',['Sister','Groom','Melos'],city_business_claim=True,death_sentence_disclosed=False,source='S4')
        elif branch=='city':
            if c['id']=='stop':s['outcome']='abandoned_friend_executed'
        elif branch=='collapse':
            if c['id']=='drink':s['water_taken']=True;s['exhausted']=False
            else:s['outcome']='abandoned_friend_executed'
        elif branch=='swim':
            success=local['success']
            t.event('swim_result','Melos',['Melos'],success=success,energy_at_start=local['energy'],threshold=p['swim_min_energy'],
                    required_cost=p['swim_cost'],basis='A3 threshold and ability to pay exertion cost')
            if not success:
                s['dead_melos']=True;self.rt.people['Melos'].vitality='dead'
                t.event('melos_drowned','Melos',[],basis='A3 failed crossing, not canon')
                self._advance(p['deadline']+1,'drowned_friend_executed')
        elif branch=='river':
            if c['id']=='retreat':s['direction']='return'
            if c['id']=='wait_flood':s['flood_waited']=True
        elif branch=='bandits':
            if c['id']=='fight':
                success=local['success']
                t.event('bandit_result','Melos',['Melos'],success=success,energy_at_start=local['energy'],threshold=p['fight_min_energy'],basis='A3')
                if not success:
                    s['dead_melos']=True;self.rt.people['Melos'].vitality='dead'
                    self._advance(p['deadline']+1,'bandits_killed_friend_executed')
                else:s['bandits_resolved']=True
        elif branch=='pass':
            if self.rt.people['Melos'].zone=='spring' and self.rt.people['Melos'].stamina<18:
                s['exhausted']=True;s['own_doubt']=True
                t.event('collapse','Melos',['Melos'],source='S10',energy=self.rt.people['Melos'].stamina,basis='A3 exhaustion threshold18')
                t.event('private_doubt','Melos',['Melos'],content='疲労で約束を諦めかける',basis='S10/A9')
        elif branch=='spring':
            if c['id']=='drink':s['water_taken']=True
        elif branch=='report':self._court_response()

    def _choose_step(self):
        before=len(self.rt.rows);at=self.rt.now;previous=self.step_index
        self._choose_step_impl()
        if self.step_index>previous:
            self.step_history.append({'step':self.step_index,'at':at,'row_start':before,
                                      'row_end':len(self.rt.rows)})

    def _choose_step_impl(self):
        t=self.trial;s=t.state;p=t.p;now=self.rt.now;zone=self.rt.people['Melos'].zone;energy=self.rt.people['Melos'].stamina
        if self.step_index>=40:s['outcome']='decision_budget_observation_incomplete';self.phase='finalize';return
        if s['outcome'] is not None:self.phase='finalize';return
        self.step_index+=1
        if now>=self.rt.end-600 and zone!='court' and now>p['deadline'] and s['exhausted']:
            s['outcome']='exhausted_friend_executed';self.phase='finalize';return
        if s['exhausted']:
            if zone=='spring' and not s['water_taken']:
                t.event('spring_heard','Melos',['Melos'],source='S10',water_present=True)
                c=self._choose('Melos','collapse',[
                    candidate('drink','足もとの泉を飲んで再出発',minutes=30,recover=p['spring_recovery'],features={'friend':2,'honor':1,'self':1}),
                    candidate('give_up','動くことを諦める',minutes=max(1,p['deadline']+1-now),features={'self':2,'friend':-1,'honor':-1})])
                self._action(c,'collapse');return
            self._advance(p['deadline']+1 if now<=p['deadline'] else now,'exhausted_friend_executed');return
        if zone=='court':self._action(candidate('report','王へ帰還を申し出る',kind='work',task='return',minutes=2),'report');return
        if zone=='village':
            s['visited_village']=True;c=self._choose('Melos','village',t.village_choices())
            self._action(c,'marriage' if c['id'].startswith('negotiate') else 'village');return
        if zone=='outskirts':
            if s['direction']=='return':
                if self.rt.people['Philostratus'].zone=='outskirts' and not s['warned']:
                    s['warned']=True;t.event('disciple_warning','Philostratus',['Melos','Philostratus'],
                        claim='teacher was being prepared for execution when I left',kind_of_information='reported, not actual death',basis='S11/A11')
                c=self._choose('Melos','city_return',[
                    candidate('enter_court','進んで刑を引き受ける',kind='move',to='court',minutes=30,cost=3,features={'friend':2,'honor':2,'self':-1}),
                    candidate('stop','城に戻らず生存を選ぶ',minutes=max(1,p['deadline']+1-now),features={'self':2,'friend':-2,'honor':-2})])
                self._action(c,'city')
            else:
                c=self._choose('Melos','release',[
                    candidate('go_village','妹の村へ向かう',kind='move',to='pass',minutes=150,cost=6,features={'friend':1,'family':2,'honor':1,'self':1}),
                    candidate('surrender_now','村へ向かわず王のもとへ引き返す',kind='move',to='court',minutes=30,cost=1,features={'friend':2,'family':-1,'honor':2,'self':-2})])
                self._action(c,'release')
            return
        if s['direction']=='outbound':
            to={'pass':'west_bank','west_bank':'east_bank','east_bank':'village'}[zone]
            minutes=self.rt.contract['travel_minutes'][zone][to]
            c=self._choose('Melos','outbound_'+zone,[candidate('continue','村への既知の道を進む',kind='move',to=to,minutes=minutes,cost=minutes/30,features={'family':1})])
            self._action(c,'outbound',to=to);return
        if zone=='east_bank':
            flood=t.info['Melos'].get('river_seen',{}).get('bridge')=='destroyed'
            if not flood:
                c=self._choose('Melos','river_intact',[candidate('bridge','残っている橋を渡る',kind='move',to='west_bank',minutes=30,cost=3,features={'friend':1})])
                self._action(c,'river');return
            offered=[candidate('swim','激流を泳いで渡る',minutes=p['swim_minutes'],cost=p['swim_cost'],features={'friend':2,'honor':2,'self':-2}),
                     candidate('retreat','来た道を村へ戻る',kind='move',to='village',minutes=330,cost=12,features={'family':1,'self':2,'friend':-2,'honor':-1})]
            if not s['flood_waited']:offered.insert(1,candidate('wait_flood','水位低下を待つ',minutes=180,features={'friend':-.5,'self':2}))
            if p['detour_enabled']:offered.append(candidate('detour','仮定した600分の浅瀬へ迂回',kind='move',to='west_bank',minutes=600,cost=18,features={'friend':1,'honor':1,'self':1}))
            c=self._choose('Melos','flood',offered)
            if c['id']=='swim':
                success=energy>=max(p['swim_min_energy'],p['swim_cost']);crossing=deepcopy(c)
                if success:crossing.update(kind='move',to='west_bank')
                self._action(crossing,'swim',success=success,energy=energy)
            else:self._action(c,'river')
            return
        if zone=='west_bank':
            c=self._choose('Melos','return_west',[candidate('to_pass','峠へ進む',kind='move',to='pass',minutes=90,cost=5,features={'friend':1})]);self._action(c,'return_west');return
        if zone=='pass':
            if now>=p['bandits_active_at'] and not s['bandits_resolved']:
                t.event('bandit_demand','Melos',['Melos'],demand='life',royal_connection='unknown',sources=['S9'])
                t.info['Melos']['royal_bandits']={'kind':'suspicion','value':True,'objective_confirmation':False}
                c=self._choose('Melos','bandits',[
                    candidate('fight','棍棒を奪い突破を試みる',minutes=p['fight_minutes'],cost=p['fight_cost'],features={'friend':2,'honor':1,'self':-1}),
                    candidate('retreat_bandits','川岸へ引き返す',kind='move',to='west_bank',minutes=150,cost=6,features={'self':2,'friend':-1})])
                self._action(c,'bandits',success=energy>=p['fight_min_energy'],energy=energy)
            else:
                c=self._choose('Melos','pass_exit',[candidate('to_spring','峠を下る',kind='move',to='spring',minutes=60,cost=12,features={'friend':1})]);self._action(c,'pass')
            return
        if zone=='spring':
            t.event('spring_heard','Melos',['Melos'],water_present=True,source='S10')
            offered=[candidate('to_city','城下まで走る',kind='move',to='outskirts',minutes=118,cost=18,features={'friend':2,'honor':1,'self':-1})]
            if not s['water_taken']:offered.append(candidate('drink','泉の水を飲む',minutes=30,recover=p['spring_recovery'],features={'friend':1,'self':2}))
            self._action(self._choose('Melos','spring',offered),'spring');return
        raise ValueError('Unknown Melos scenario phase/place')

    def _advance_only(self, until):
        target=min(until,self.advance_target);p=self.trial.p
        while self.rt.now<target:
            points=[x for x in self.rt._points() if self.rt.now<x<=target]
            points += [x for x in [p['rain_at'],p['bridge_destroyed_at'],p['execution_preparation_at'],p['deadline'],p['deadline']+1] if self.rt.now<x<=target]
            self.rt._tick(min(points+[target]));self.stage._environment()
        self.stage._environment()
        if self.rt.now==self.advance_target:
            if self.advance_outcome is not None:self.trial.state['outcome']=self.advance_outcome
            self.advance_target=self.advance_outcome=self.advance_started=None;self.phase='finalize'

    def _finalize(self):
        t=self.trial;s=t.state
        if s['outcome'] is None:s['outcome']='observation_incomplete'
        if s['returned_at'] is None and not s['dead_friend'] and self.rt.now<=t.p['deadline']:
            self._advance(t.p['deadline']+1);return
        t.event('observation_result',outcome=s['outcome'],state=deepcopy(s),
                unresolved_future=['王の統治','村の反応','観測後の生活'],not_model_generated=True)
        self.finished=True;self.phase='finished'

    def export_state(self):
        state={'schema':'melos_session_v1','stage':self.stage.export_state(),'phase':self.phase,
               'step_index':self.step_index,'step_history':deepcopy(self.step_history),'advance_target':self.advance_target,'advance_started':self.advance_started,
               'advance_outcome':self.advance_outcome,'finished':self.finished}
        return {'state':state,'sha256':_hash(state)}

    def restore_state(self,snapshot):
        if type(snapshot) is not dict or set(snapshot)!={'state','sha256'}:raise ValueError('Invalid session envelope')
        s=_json(snapshot['state'])
        if snapshot['sha256']!=_hash(s) or set(s)!=set(self.export_state()['state']) or s['schema']!='melos_session_v1':raise ValueError('Invalid session state')
        if s['phase'] not in {'choose_step','action','advance_only','finalize','finished'} or type(s['step_index']) is not int or not 0<=s['step_index']<=40 or type(s['finished']) is not bool:
            raise ValueError('Invalid session cursor')
        if s['finished']!=(s['phase']=='finished'):raise ValueError('Inconsistent terminal cursor')
        core=s['stage']['state'];now=core['runtime']['now'];rows=core['runtime']['rows']
        history=s['step_history']
        if type(history) is not list or len(history)!=s['step_index']:raise ValueError('Step cursor differs from transition history')
        def loop_witness(row):
            d=row['details']
            return (row['verb']=='subjective_choice' and row['subject']=='Melos' and d.get('context')!='confession' or
                    row['verb']=='action_started' and d.get('choice',{}).get('actor')=='Melos' and d.get('choice',{}).get('verb')=='work' and d.get('choice',{}).get('task_id')=='return' or
                    row['verb']=='observation_horizon_stop' and d.get('candidate')=='report')
        previous_end=0;previous_time=-1;covered=[]
        for i,record in enumerate(history,1):
            if type(record) is not dict or set(record)!={'step','at','row_start','row_end'} or record['step']!=i or any(type(record[k]) is not int for k in record):raise ValueError('Invalid step history')
            if not previous_time<=record['at']<=now or not previous_end<=record['row_start']<=record['row_end']<=len(rows):raise ValueError('Invalid step history range')
            if any(r['absolute_minute']!=record['at'] for r in rows[record['row_start']:record['row_end']]):raise ValueError('Step history time mismatch')
            witnesses=[r['id'] for r in rows[record['row_start']:record['row_end']] if loop_witness(r)]
            if len(witnesses)!=1:
                exhausted=core['external']['state']['exhausted']
                evidence=any(r['verb'] in ('exhaustion','collapse') and r['absolute_minute']<=record['at'] for r in rows[:record['row_start']])
                final_empty=(not witnesses and i==len(history) and record['row_start']==record['row_end'] and exhausted and evidence and
                             (s['phase']=='advance_only' and s['advance_outcome']=='exhausted_friend_executed' or
                              s['phase'] in ('finalize','finished') and core['external']['state']['outcome']=='exhausted_friend_executed'))
                if not final_empty:raise ValueError('Step lacks a unique executed decision/action witness')
            covered.extend(witnesses)
            previous_time=record['at'];previous_end=record['row_end']
        if covered!=[r['id'] for r in rows if loop_witness(r)]:raise ValueError('Step history omits executed decisions')
        provider_state=core['provider']['state']
        records=provider_state['records'];choices=[r for r in rows if r['verb']=='subjective_choice']
        decisions=core['external']['decisions']
        if len(records)!=len(choices) or len(decisions)!=len(choices) or core['external']['count']!=provider_state['counts']:raise ValueError('Provider and observed choices disagree')
        for record,row,decision in zip(records,choices,decisions):
            receipt=record['receipt'];view=record['offer']['view'];d=row['details']
            if (row['subject']!=receipt['actor'] or row['absolute_minute']!=receipt['now'] or d['actor_view']!=view or d['candidates']!=record['offer']['offered']
                    or d['selected']!=receipt['selected'] or d['calculation']!=receipt['calculation'] or d['ordinal']!=receipt['ordinal'] or d['rng_draw']!=receipt['rng_draw']
                    or decision!={'event_id':row['id'],'at':row['absolute_minute'],'actor':row['subject'],'context':d['context'],'selected':d['selected']}):raise ValueError('Choice receipt/event binding differs')
        terminal=[r for r in rows if r['verb']=='observation_result']
        if len(terminal)!=(1 if s['finished'] else 0):raise ValueError('Terminal cursor/result mismatch')
        if s['phase']=='advance_only':
            start=s['advance_started'];target=s['advance_target'];outcome=s['advance_outcome'];state=core['external']['state'];deadline=self.trial.p['deadline']
            if type(start) is not int or type(target) is not int or not self.rt.time_kernel.axis.start<=start<=now<=target<=self.rt.end:raise ValueError('Invalid awaited time')
            allowed={None,'drowned_friend_executed','bandits_killed_friend_executed','exhausted_friend_executed'}
            if outcome not in allowed:raise ValueError('Invalid advance outcome')
            expected=max(deadline+1,start) if outcome=='exhausted_friend_executed' else deadline+1
            if target!=expected:raise ValueError('Advance target differs from approved continuation')
            if outcome=='exhausted_friend_executed' and not state['exhausted']:raise ValueError('Missing exhaustion evidence')
            if outcome in ('drowned_friend_executed','bandits_killed_friend_executed') and not state['dead_melos']:raise ValueError('Missing death evidence')
            if outcome is None and (state['returned_at'] is not None or state['outcome'] is None):raise ValueError('Invalid final deadline advance')
        elif any(s[k] is not None for k in ('advance_target','advance_outcome','advance_started')):raise ValueError('Unexpected awaited time')
        if (s['phase']=='action') != (core['phase']!='idle'):raise ValueError('Inconsistent action continuation')
        if s['phase']=='action':
            c=core['pending']['continuation']
            if type(c) is not dict or c.get('branch') not in self.CONTINUATIONS:raise ValueError('Invalid branch continuation')
            fields={'branch'} | ({'to'} if c['branch']=='outbound' else {'energy','success'} if c['branch'] in ('swim','bandits') else set())
            if set(c)!=fields:raise ValueError('Invalid branch local fields')
            if 'to' in c and c['to'] not in self.rt.world.zones:raise ValueError('Invalid outbound destination')
            if 'energy' in c:
                if type(c['energy']) not in (int,float) or not math.isfinite(c['energy']) or not 0<=c['energy']<=100 or type(c['success']) is not bool:raise ValueError('Invalid action-start branch evidence')
                threshold=max(self.trial.p['swim_min_energy'],self.trial.p['swim_cost']) if c['branch']=='swim' else self.trial.p['fight_min_energy']
                if c['success']!=(c['energy']>=threshold):raise ValueError('Inconsistent branch success')
            pending=core['pending'];chosen=pending['candidate'];branch=c['branch'];command=pending['command']
            entry=core['runtime']['adapter_journal']['entries'][pending['receipt']['proposal_id']]
            if chosen['id']=='report':
                if branch!='report' or chosen!=candidate('report','王へ帰還を申し出る',kind='work',task='return',minutes=2) or entry['source']!='court':raise ValueError('Invalid fixed report continuation')
            else:
                melos=[(record,row) for record,row in zip(records,choices) if record['receipt']['actor']=='Melos']
                if not melos:raise ValueError('Action lacks original choice')
                record,row=melos[-1];original=deepcopy(record['receipt']['candidate']);context=row['details']['context']
                mapping={'release':'release','city_return':'city','collapse':'collapse','river_intact':'river','return_west':'return_west','bandits':'bandits','pass_exit':'pass','spring':'spring'}
                expected=('outbound' if context.startswith('outbound_') else 'marriage' if context=='village' and original['id'].startswith('negotiate') else 'village' if context=='village' else 'swim' if context=='flood' and original['id']=='swim' else 'river' if context=='flood' else mapping.get(context))
                if branch!=expected or command['at']!=record['receipt']['now']:raise ValueError('Branch differs from original decision context')
                if branch=='swim' and c['success']:original.update(kind='move',to='west_bank')
                if chosen!=original:raise ValueError('Action candidate differs from original selection')
                if branch=='outbound' and c['to']!=chosen.get('to'):raise ValueError('Outbound target differs')
                if 'energy' in c and round(c['energy'],6)!=record['offer']['view']['stamina']:raise ValueError('Branch energy differs from own observation')
        state=core['external']['state'];vitality=core['external']['vitality']
        if state['dead_melos']!=(vitality['Melos']=='dead') or state['dead_friend']!=(vitality['Selinuntius']=='dead'):
            raise ValueError('Death flags differ from actual vitality')
        for flag,verb in [('rain','rain_begins'),('flood','bridge_destroyed'),('preparation','execution_preparation'),
                          ('negotiated','wedding_request'),('disclosed','hostage_disclosed'),('friend_released','hostage_released')]:
            if state[flag] and not any(r['verb']==verb for r in rows):raise ValueError('Scenario flag lacks actual event')
        if state['agreed'] and not any(r['verb']=='marriage_response' and r['details'].get('agreed') is True and r['details'].get('sister')==r['details'].get('groom')=='agree' for r in rows):
            raise ValueError('Agreement lacks both observed responses')
        if state['wedding']:
            ledger=core['runtime']['ledger']['tasks']['wedding']
            unit=any(r['verb']=='work_unit_completed' and r['subject']=='Melos' and r['details'].get('task_id')=='wedding' for r in rows)
            completion=any(r['verb']=='action_completed' and r['result']=='applied' and r['details'].get('choice',{}).get('task_id')=='wedding' and r['details'].get('duration_minutes')==120 for r in rows)
            if not state['agreed'] or ledger['units']<1 or not unit or not completion:raise ValueError('Wedding flag lacks completed agreed work')
        if state['returned_at'] is not None:
            observed=[r for r in rows if r['verb']=='return_observed']
            if len(observed)!=1 or observed[0]['absolute_minute']!=state['returned_at'] or observed[0]['details'].get('returned_at')!=state['returned_at']:
                raise ValueError('Return time lacks its actual observation')
            reports=[r for r in rows if r['verb']=='action_completed' and r['result']=='applied' and r['details'].get('choice',{}).get('task_id')=='return' and r['details'].get('duration_minutes')==2 and r['absolute_minute']==state['returned_at']]
            if len(reports)!=1 or core['runtime']['ledger']['tasks']['return']['units']<1:raise ValueError('Return lacks fixed completed report')
        if state['pardon'] and not any(r['verb']=='royal_verdict' and r['details'].get('verdict')=='pardon' for r in rows):raise ValueError('Pardon lacks actual verdict')
        if terminal and terminal[0]['details'].get('state')!=state:raise ValueError('Final observation differs from scenario state')
        if s['finished'] and (core['external']['state']['outcome'] is None or not core['runtime']['rows'] or core['runtime']['rows'][-1]['verb']!='observation_result'):
            raise ValueError('Missing committed observation result')
        self.stage.restore_state(s['stage'])
        for field in ('phase','step_index','step_history','advance_target','advance_started','advance_outcome','finished'):setattr(self,field,deepcopy(s[field]))

    def run(self, *, until=None, _commit=None):
        limit=self.rt.end if until is None else until
        if type(limit) is not int or not self.rt.now<=limit<=self.rt.end:raise ValueError('Invalid pause time')
        while not self.finished:
            if limit<self.rt.end and self.rt.now>=limit and (self.phase=='choose_step' or
                    self.phase=='advance_only' and self.advance_target>self.rt.now):break
            before=self.export_state()
            try:
                if self.phase=='choose_step':self._choose_step()
                elif self.phase=='action':
                    if self.stage.phase=='physical':
                        self.stage.advance(min(limit,self.stage.pending['finish']))
                        if self.stage.phase=='physical':
                            if _commit is not None:_commit(self.export_state())
                            break
                    while self.stage.phase not in ('branch','idle'):self.stage.step_effect()
                    self._after_action(self.stage.finish_action())
                elif self.phase=='advance_only':
                    self._advance_only(limit)
                    if self.phase=='advance_only':
                        if _commit is not None:_commit(self.export_state())
                        break
                elif self.phase=='finalize':self._finalize()
                if _commit is not None:_commit(self.export_state())
            except BaseException:
                self.restore_state(before)
                raise
        return deepcopy(self.trial.state)

    def run_saved(self, out, *, until=None, resume=False):
        """One locked atomic authoritative snapshot; JSONL/results are derived.

        A crash before the next commit may redo in-memory work, never published
        effects. This does not register a product independent-trials profile.
        """
        expected={'family':'melos_approved_session_v1','identity':self.stage.identity,
                  'provider':provider_snapshot(self.stage.provider)['identity'],
                  'python':list(sys.version_info[:3])}
        store=CheckpointStore(out,expected)
        with store.session():
            if resume:self.restore_state(store.load())
            else:store.initialize(self.export_state())
            self.run(until=until,_commit=store.save)
            # Even a no-op resume reconstructs stale derived exports from the
            # committed state. No inference from existing output files.
            committed=store.load()
            if committed!=self.export_state():raise ValueError('Session differs from committed snapshot')
            rows=self.rt.rows
            atomic_bytes(store.out/'layers.jsonl',b''.join(canonical(r)+b'\n' for r in rows))
            result={'schema':'melos_approved_session_result_v1','completion':'complete' if self.finished else 'paused',
                    'seed':self.trial.seed,'state':deepcopy(self.trial.state),
                    'decisions':deepcopy(self.trial.decisions),'counts':deepcopy(self.trial.count),
                    'audit':self.trial.audit() if self.finished else None,
                    'product_profile_active':True,'checkpoint_sha256':committed['sha256']}
            atomic_bytes(store.out/'result.json',canonical(result)+b'\n')
            return result
