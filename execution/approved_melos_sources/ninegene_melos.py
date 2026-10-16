"""Verification-only nine-gene adapter; not an ExecutionPlan product adapter."""
from pathlib import Path
from types import SimpleNamespace
from collections import Counter
from copy import deepcopy
import random,math,json,hashlib,time
from melos_runner import Trial,ROOT,canonical,dump,digest
from engine.actions import Action
from gapengine.genome import Genome
from gapengine.policy import Policy

CFG={'nodes':[
 {'verb':'ritual','category':'V','subtype':'ritual','risk':'neutral','sign':0},
 {'verb':'share_knowledge','category':'III','subtype':'confide','risk':'neutral','sign':1},
 {'verb':'negotiate','category':'III','subtype':'negotiate','risk':'neutral','sign':1},
 {'verb':'move','category':'V','subtype':'crossing','risk':'risky','sign':0,'when':'crossing'},
 {'verb':'move','category':None,'subtype':'move','risk':'neutral','sign':0},
 {'verb':'fight','category':'I','subtype':'weaken_direct','risk':'risky','sign':-1},
 {'verb':'withdraw','category':'V','subtype':'stall','risk':'neutral','sign':0},
 {'verb':'rest','category':None,'subtype':'rest','risk':'neutral','sign':0}], 'edges':[]}

class ObservablePolicyWorld:
 """No engine World/ledger/future/other actor reference is retained.

 Only fixed public identity symbols, map zone symbols and neutral initial
 relations are supplied. No actor locations, vitality, hidden hostility,
 destination occupants or objective ownership are exposed. This verification
 holds relations fixed; it does not model learned relation changes.
 """
 def __init__(self,view,subject,targets):
  self.genres=frozenset();self.protagonist='Melos';self.antagonist='King'
  self.zones={z:None for z in view['public_map']}
  self.subjects={subject.id:subject,**{t:SimpleNamespace(id=t) for t in targets}}
  self.relations=SimpleNamespace(stance=lambda a,b:0.0)
  self.companionship={'threshold':.3}
 def target_role(self,subject,target):return 'neutral'
 def present_subjects(self,destination):return []
 def holder(self,target):raise AssertionError('No object-target goal in this projection')
 def namespace(self,subject,present,**kw):return {} # no rules/route/Jev

def observed(view,verb):return any(e['verb']==verb and e['at']<=view['now'] for e in view['evidence'])

def policy_action(c,view):
 ident=c['id'];meta={'candidate_id':ident}
 if ident=='wedding':return Action('ritual',('wedding',),meta=meta)
 if ident in ['confess','disclose']:
  return Action('share_knowledge',('Selinuntius' if ident=='confess' else 'Sister',),meta=meta)
 if ident.startswith('negotiate_'):return Action('negotiate',('Groom',),meta=meta)
 if ident=='swim':
  assert observed(view,'river_observed') and view['initial'].get('river_seen',{}).get('water')=='flood'
  return Action('move',('west_bank',),meta={**meta,'crossing':True})
 if ident=='fight':
  assert observed(view,'bandit_demand')
  return Action('fight',('Bandits',),meta={**meta,'under_threat':True})
 if ident in ['retreat','retreat_bandits']:
  threat=ident=='retreat_bandits'
  if threat:assert observed(view,'bandit_demand')
  return Action('withdraw',(),meta={**meta,'under_threat':threat})
 if ident in ['rest_arrival','sleep_dawn','drink']:
  return Action('rest',(),meta={**meta,'under_threat':False})
 if c['kind']=='move':return Action('move',(c['to'],),meta={**meta,'crossing':False})
 return Action(ident,(),meta=meta) # deliberate unclassified candidates

def reweight_observable(policy,view,offered,history):
 """The sole Policy boundary: arguments are only ActorView/candidates/self history."""
 person=SimpleNamespace(id=view['actor'],goal=SimpleNamespace(target=None),phase=frozenset(),
  vitality=view['own_vitality'],identity_displayed=view['actor'],decision_history=history)
 actions=[policy_action(c,view) for c in offered]
 targets={a.args[0] for a in actions if a.verb in ['negotiate','share_knowledge']}
 world=ObservablePolicyWorld(view,person,targets)
 weighted=[];utility=[]
 for c,a in zip(offered,actions):
  u=sum(view['goals'].get(k,0)*v for k,v in c['features'].items())
  w=math.exp(max(-6,min(6,u/5)));weighted.append((a,w));utility.append(u)
 result=policy.reweight(person,world,[person],weighted,turn=view['decision_ordinal'],day=view['now']//1440+1)
 rows=[]
 for c,u,(a,base),(b,final) in zip(offered,utility,weighted,result):
  assert a is b
  rows.append({'candidate':c['id'],'policy_verb':a.verb,'policy_args':list(a.args),
   'goal_features':c['features'],'base_utility':u,'base_weight':base,
   'classification':a.meta['classification'],'multipliers':a.meta['policy'],
   'final_weight':final,'normalized_weight':final/sum(w for _,w in result)})
 return person,result,rows

class NineTrial(Trial):
 def __init__(self,genome,seed=0):
  assert isinstance(genome,Genome)
  self.rng=random.Random(seed);self.policy=Policy(genome,None,cfg=CFG,eps=.02,lam=.7)
  self.policy_history=Counter()
  super().__init__(genome,seed,'baseline',False)
 def choose(self,actor,context,offered,forced=None):
  assert forced is None
  view=self.view(actor);ordinal=self.count[actor]
  if ordinal>=32:raise ValueError('decision budget exhausted')
  self.count[actor]+=1
  view['decision_ordinal']=ordinal
  # Own physical status is observable. No other subject is copied.
  view['own_vitality']=self.rt.people[actor].vitality
  if actor=='Melos':person,weighted,calculations=reweight_observable(self.policy,view,offered,self.policy_history)
  else:
   weighted=[];calculations=[]
   for c in offered:
    u=sum(view['goals'].get(k,0)*v for k,v in c['features'].items())
    w=math.exp(max(-6,min(6,u/5)));weighted.append((None,w))
    calculations.append({'candidate':c['id'],'base_utility':u,'base_weight':w,'final_weight':w,
     'multipliers':{'m_cat':1,'m_risk':1,'m_stance':1,'m_nov':1},'normalized_weight':None})
   total=sum(w for _,w in weighted)
   for c in calculations:c['normalized_weight']=c['final_weight']/total
  probe=random.Random();probe.setstate(self.rng.getstate());q=probe.random()
  idx=self.rng.choices(range(len(offered)),weights=[w for _,w in weighted],k=1)[0]
  selected=offered[idx]
  if actor=='Melos':self.policy.record(person,weighted[idx][0])
  row=self.event('subjective_choice',actor,[actor],context=context,actor_view=view,candidates=offered,
   selected=selected['id'],calculation=calculations,ordinal=ordinal,rng_draw=q,
   view_hash=digest(view),mechanism='actual_ninegene_policy' if actor=='Melos' else 'fixed_base_weight_rng',
   no_future_truth=True,no_jev=True,policy_information_boundary='ObservablePolicyWorld from ActorView only')
  self.decisions.append({'event_id':row['id'],'at':self.rt.now,'actor':actor,'context':context,'selected':selected['id']})
  return selected
 def run(self):
  result,rows=super().run()
  result['adapter']='verification NineTrial choose -> actual Genome/Policy.reweight -> unchanged CalendarRuntime'
  result['connection_scope']='not the product independent-job ExecutionPlan adapter'
  result['ninegene_adapter_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
  return result,rows

def saved(out,genome,seed):
 out=Path(out)
 if out.exists():raise ValueError('fresh output required')
 out.mkdir(parents=True);start=time.perf_counter();cpu=time.process_time()
 trial=NineTrial(genome,seed)
 result,rows=trial.run()
 dump(out/'result.json',result)
 (out/'layers.jsonl').write_text(''.join(canonical(r)+'\n' for r in rows),encoding='utf-8')
 telemetry={'cpu_seconds':time.process_time()-cpu,'wall_seconds':time.perf_counter()-start}
 dump(out/'telemetry.json',telemetry)
 if result['audit']['violations']:raise AssertionError(result['audit']['violations'])
 return result,rows,telemetry
