"""Local scenario adapter over unmodified WorldBloom CalendarRuntime and GA.
No model/network/GPU. The source commit is frozen separately in source-bae011c8.zip.
"""
from __future__ import annotations
from pathlib import Path
from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
import argparse, hashlib, json, math, random, statistics, sys, time
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'worldbloom'))
import yaml
from engine.world import World
from engine.sim import Simulation
from engine.calendar_inputs import read_subjects
from gapengine.qd import Archive, Elite, Descriptor
from gapengine.evolve import _next_population

ACTORS=tuple(sorted(['Melos','King','Selinuntius','Sister','Groom','Philostratus']))
LIMIT=2**32
class HorizonStop(Exception):pass
def canonical(v): return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(v):return hashlib.sha256(canonical(v).encode()).hexdigest()
def dump(p,v):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(canonical(v)+'\n',encoding='utf-8')
@dataclass(frozen=True)
class MelosGenome:
 choices:tuple[int,...]
 def __post_init__(self):
  if len(self.choices)!=len(ACTORS)*32 or any(type(x)!=int or not 0<=x<LIMIT for x in self.choices):raise ValueError('192 uint32 genes required')
 def to_dict(self):return {'codec':'melos_choices_v1','actors':list(ACTORS),'slots_per_actor':32,'choices':list(self.choices)}
 @classmethod
 def from_dict(cls,v):
  if v.get('codec')!='melos_choices_v1' or v.get('actors')!=list(ACTORS) or v.get('slots_per_actor')!=32:raise ValueError('codec mismatch')
  return cls(tuple(v['choices']))
 @classmethod
 def random(cls,rng,**kw):return cls(tuple(rng.randrange(LIMIT) for _ in range(len(ACTORS)*32)))
 @classmethod
 def crossover(cls,a,b,rng,**kw):return cls(tuple(x if rng.random()<.5 else y for x,y in zip(a.choices,b.choices)))
 @classmethod
 def mutate(cls,a,rng,p=.3,**kw):return cls(tuple(rng.randrange(LIMIT) if rng.random()<p else x for x in a.choices))

def candidate(ident,label,kind='wait',minutes=1,features=None,**kw):
 return {'id':ident,'label':label,'kind':kind,'minutes':minutes,'features':features or {},**kw}

class Trial:
 def __init__(self,genome,seed=0,arm='baseline',reference=False,scenario=None):
  self.scenario=deepcopy(scenario or yaml.safe_load((ROOT/'melos-input/scenario.yaml').read_text(encoding='utf-8')))
  self.p=self.scenario['parameters'];self.goals=deepcopy(self.scenario['goals'])
  if arm=='self_priority':self.goals['Melos'].update(self=12,honor=1)
  if arm=='detour':self.p['detour_enabled']=True
  if arm=='harder_swim':self.p['swim_min_energy']=95
  if arm not in ['baseline','self_priority','detour','harder_swim']:raise ValueError(arm)
  self.genome=genome;self.seed=seed;self.arm=arm;self.reference=reference
  self.count={a:0 for a in ACTORS};self.decisions=[];self.serial=0
  world=World.from_yaml(ROOT/'melos-input/world.yaml')
  people=read_subjects(world.source,world.slot_execution)
  self.sim=Simulation(seed,world,people,ROOT/'unused-output')
  self.rt=self.sim.slot_runtime
  self.info=deepcopy(self.scenario['initial_information']);self.evidence={a:[] for a in ACTORS}
  self.state={'direction':'outbound','visited_village':False,'negotiated':False,'agreed':False,
   'disclosed':False,'rested_arrival':False,'banquet':False,'slept':False,'wedding':False,
   'bandits_resolved':False,'water_taken':False,'returned_at':None,'dead_melos':False,'dead_friend':False,
   'pardon':False,'outcome':None,'friend_released':False,'exhausted':False,'rain':False,'flood':False,
   'preparation':False,'disciple_started':False,'warned':False,'confessed':False,'own_doubt':False,'flood_waited':False}
  self.notes=[]
  self.initialize()

 def event(self,verb,actor=None,audience=(),**details):
  self.serial+=1
  row=self.rt.emit(f'melos:{self.serial:04d}',verb,actor,adapter='melos_branch_v1',**details)
  # Scenario observation is explicit, including awake night actors. Do not use
  # the workshop's implicit 06..22 sleep model for a man travelling all night.
  for a in audience:
   if a not in ACTORS:raise ValueError('unknown audience')
   # Do not recursively include earlier ActorViews inside an evidence item.
   observed_details=({k:details[k] for k in ['context','selected']} if verb=='subjective_choice' else details)
   self.evidence[a].append({'event_id':row['id'],'at':self.rt.now,'verb':verb,'content':deepcopy(observed_details)})
   self.rt.observe(row,[a],source='scenario_direct')
  return row

 def initialize(self):
  self.event('initial_information',audience=ACTORS,scope='各人物の初期情報はscenario.initial_information。世界全情報の配信ではない')
  # Established agreement is the common starting state, not a searched opening.
  terms={'promise_id':'return-hostage','version':1,'proposer':'King','promisor':'Melos',
   'beneficiary':'Selinuntius','acceptance_parties':['Melos','King','Selinuntius'],
   'place':'court','start':0,'deadline':self.p['deadline'],'grace_until':None,
   'requirements':[{'task_id':'return','units':1,'contribution_by':'Melos'}]}
  reg=self.rt.registry;reg.offer(terms,0,'initial-offer');h=reg.terms_hash(terms)
  for a in terms['acceptance_parties']:reg.accept('return-hostage',1,a,h,0,'initial-accept-'+a)
  self.rt._tick(0)
  self.event('hostage_agreement',audience=['Melos','King','Selinuntius'],deadline=self.p['deadline'],
   friend='Selinuntius',late_offer={'kind':'King_claim','content':'遅刻なら友処刑・メロス放免'},sources=['S2','S3'])

 def set_energy(self,value,reason):
  a='Melos';before=self.rt.people[a].stamina;value=max(0,min(100,value));f=Fraction(str(round(value,6)))
  self.rt.energy[a]=[f.numerator,f.denominator];self.rt.people[a].stamina=float(f)
  self.event('energy_change',a,[a],before=before,after=float(f),reason=reason,basis='A3 numerical assumption')

 def advance(self,target):
  if target<self.rt.now:raise ValueError('time reversal')
  while self.rt.now<target:
   points=[x for x in self.rt._points() if self.rt.now<x<=target]
   for x in [self.p['rain_at'],self.p['bridge_destroyed_at'],self.p['execution_preparation_at'],self.p['deadline'],self.p['deadline']+1]:
    if self.rt.now<x<=target:points.append(x)
   at=min(points+[target]);self.rt._tick(at);self.environment()
  self.environment()

 def environment(self):
  now=self.rt.now;s=self.state
  if now>=self.p['rain_at'] and not s['rain']:
   s['rain']=True;aud=[a for a,p in self.rt.people.items() if p.zone=='village']
   self.event('rain_begins',audience=aud,sources=['S6'],environment_assumption='fixed rain at day2 noon')
  if now>=self.p['bridge_destroyed_at'] and not s['flood']:
   s['flood']=True;aud=[a for a,p in self.rt.people.items() if p.zone in ['east_bank','west_bank']]
   self.event('bridge_destroyed',audience=aud,boats='washed_away',ferrykeeper='absent',sources=['S7'])
  if now>=self.p['execution_preparation_at'] and not s['preparation'] and s['returned_at'] is None:
   s['preparation']=True
   self.info['Philostratus']['execution_status']='preparing, not completed'
   self.event('execution_preparation',audience=['King','Selinuntius','Philostratus'],executed=False,basis='A11')
   self.info['Selinuntius']['own_doubt']=True
   self.event('friend_private_doubt','Selinuntius',['Selinuntius'],basis='A9 long wait and execution preparation; not known by Melos or King')
  if s['preparation'] and not s['disciple_started']:
   s['disciple_started']=True
   self.rt._start({'id':'disciple-walk','actor':'Philostratus','at':now,'verb':'move','to':'outskirts'})
  if now>self.p['deadline'] and s['returned_at'] is None and not s['dead_friend']:
   s['dead_friend']=True
   self.rt.people['Selinuntius'].vitality='dead'
   self.event('hostage_executed','King',['King','Philostratus'] if self.rt.people['Philostratus'].zone=='court' else ['King'],
    target='Selinuntius',deadline=self.p['deadline'],basis='A7 explicit enforcement rule',
    caused_by=[r['id'] for r in self.rt.rows if r['verb']=='promise_transition' and r['details']['promise_transition']['status']=='breached'])

 def view(self,actor):
  # No World/ledger/other actors/hidden environment references are passed to chooser.
  person=self.rt.people[actor]
  own={}
  if actor=='Melos':
   own={k:self.state[k] for k in ['direction','visited_village','negotiated','agreed','disclosed','rested_arrival','banquet','slept','wedding','bandits_resolved','water_taken','exhausted','own_doubt','flood_waited']}
  return {'actor':actor,'now':self.rt.now,'zone':person.zone,'stamina':round(person.stamina,6),
   'goals':deepcopy(self.goals.get(actor,{})),'initial':deepcopy(self.info[actor]),
   'own':own,'evidence':deepcopy(self.evidence[actor]),
   'public_map':deepcopy(self.rt.contract['travel_minutes']) if actor=='Melos' else {}}

 def choose(self,actor,context,offered,forced=None):
  if not offered:raise ValueError('No candidates')
  view=self.view(actor);i=self.count[actor]
  if i>=32:raise ValueError('decision budget exhausted')
  gene=self.genome.choices[ACTORS.index(actor)*32+i];self.count[actor]+=1
  weights=[];calculation=[]
  for c in offered:
   utility=sum(view['goals'].get(k,0)*v for k,v in c['features'].items())
   weight=math.exp(max(-6,min(6,utility/5)))
   weights.append(weight);calculation.append({'candidate':c['id'],'goal_features':c['features'],'utility':utility,'weight':weight})
  salt=int.from_bytes(hashlib.sha256(f'{self.seed}:{actor}:{i}'.encode()).digest()[:8],'big')/2**64
  q=((gene+.5)/LIMIT+salt)%1
  cumulative=0;selected=offered[-1]
  for c,w in zip(offered,weights):
   cumulative+=w
   if q*sum(weights)<cumulative:selected=c;break
  if forced is not None:
   selected=next(c for c in offered if c['id']==forced)
  row=self.event('subjective_choice',actor,[actor],context=context,actor_view=view,candidates=offered,
   selected=selected['id'],calculation=calculation,gene=gene,ordinal=i,quantile=q,
   view_hash=digest(view),mechanism='reference_witness' if forced else 'genetic_quantile_fixed_subjective_weights',
   no_future_truth=True,no_jev=True)
  self.decisions.append({'event_id':row['id'],'at':self.rt.now,'actor':actor,'context':context,'selected':selected['id']})
  return selected

 def act(self,c):
  if self.rt.now+c['minutes']>self.rt.end:
   self.state['outcome']='observation_incomplete'
   self.event('observation_horizon_stop','Melos',['Melos'],candidate=c['id'],
    requested_minutes=c['minutes'],available_minutes=self.rt.end-self.rt.now,completed=False)
   raise HorizonStop()
  self.serial+=1
  rt=self.rt;a='Melos';ident=f'melos-action-{self.serial}';cost=c.get('cost',0)
  if c['kind']=='move' and cost>rt.people[a].stamina:
   # Attempt fails at the source; do not teleport or finish an unaffordable move.
   self.wait(30,'collapse')
   self.state['exhausted']=True
   self.event('exhaustion',a,[a],failed_candidate=c['id'],zone=rt.people[a].zone,basis='A3')
   return
  command={'id':ident,'actor':a,'at':rt.now,'verb':'move' if c['kind']=='move' else 'work' if c['kind']=='work' else 'wait'}
  if c['kind']=='move':
   command['to']=c['to'];source=rt.people[a].zone
   # Local adapter duration override recorded in candidate and start event.
   old=rt.contract['travel_minutes'][source].get(c['to'])
   if old is None:raise ValueError('unregistered route')
   rt.contract['travel_minutes'][source][c['to']]=c['minutes']
   rt._start(command);rt.contract['travel_minutes'][source][c['to']]=old
  elif c['kind']=='work':command.update(task_id=c['task'],minutes=c['minutes']);rt._start(command)
  else:command['minutes']=c['minutes'];rt._start(command)
  if a not in rt.busy:raise ValueError('action unexpectedly rejected')
  finish=rt.busy[a]['finish'];self.advance(finish)
  completion=next(r for r in reversed(rt.rows) if r['verb']=='action_completed' and r['details']['choice']['id']==ident)
  if completion['result']!='applied':raise ValueError('core action failed: '+str(completion))
  self.set_energy(rt.people[a].stamina-cost,c['id'])
  self.event('scenario_action_completed',a,[a],candidate=c['id'],core_completion_id=completion['id'],
   place=rt.people[a].zone,duration=c['minutes'],energy_cost=cost)
  if c.get('recover'):self.set_energy(rt.people[a].stamina+c['recover'],c['id']+' recovery')

 def wait(self,minutes,label):
  self.serial+=1;ident=f'wait-{label}-{self.serial}'
  self.rt._start({'id':ident,'actor':'Melos','at':self.rt.now,'verb':'wait','minutes':minutes})
  self.advance(self.rt.now+minutes)

 def observe_river(self):
  self.info['Melos']['river_seen']={'bridge':'destroyed' if self.state['flood'] else 'intact',
   'boats':'washed_away' if self.state['flood'] else 'not offered','water':'flood' if self.state['flood'] else 'normal'}
  if self.p['detour_enabled']:self.info['Melos']['experimental_ford']='known 600 minute assumed route'
  self.event('river_observed','Melos',['Melos'],**self.info['Melos']['river_seen'],sources=['S7'],
   detour=self.p['detour_enabled'])

 def marriage(self,c):
  self.act(c);self.state['negotiated']=True
  self.event('wedding_request','Melos',['Sister','Groom','Melos'],request_at=self.rt.now,
   proposed_at=self.p['wedding_at'],reason='hostage disclosed' if self.state['disclosed'] else 'city business',sources=['S4','S5'])
  for a in ['Sister','Groom']:self.info[a]['wedding_request']=True
  sister=self.choose('Sister','marriage_response',[
   candidate('agree','婚礼に同意',features={'marry':2,'care':1}),candidate('delay','婚礼を延期',features={'marry':-1,'care':1})],
   'agree' if self.reference else None)
  groom=self.choose('Groom','marriage_response',[
   candidate('agree','準備不足でも婚礼に同意',features={'prepared':-1,'care':2 if self.state['disclosed'] else 1}),
   candidate('delay','葡萄の季節へ延期を望む',features={'prepared':1,'care':0})], 'agree' if self.reference else None)
  self.state['agreed']=sister['id']==groom['id']=='agree'
  self.event('marriage_response',audience=['Sister','Groom','Melos'],sister=sister['id'],groom=groom['id'],
   agreed=self.state['agreed'],basis='A8/A9',not_wedding_completion=True)

 def village_choices(self):
  s=self.state;now=self.rt.now;p=self.p;c=[]
  if not s['negotiated']:
   if not s['rested_arrival'] and now<1320:
    c.append(candidate('rest_arrival','夜まで眠る',minutes=1320-now,recover=(1320-now)/60*p['rest_per_hour'],features={'self':2,'family':.5}))
   if not s['disclosed']:
    c.append(candidate('disclose','妹と婿へ人質・期限を告げる',minutes=2,features={'friend':1,'honor':1,'family':-.5}))
   if now+90<=p['wedding_at']:
    c.append(candidate('negotiate_short','前倒しの婚礼を頼む',minutes=90,features={'family':2,'friend':.3}))
   if now+540<=p['wedding_at']:
    c.append(candidate('negotiate_long','長く説得を続ける',minutes=540,features={'family':2,'honor':.4,'self':-.5}))
  elif s['agreed'] and not s['wedding'] and now<=p['wedding_at']:
   if now<p['wedding_at']:
    c.append(candidate('wait_wedding','婚礼の時刻まで待つ',minutes=p['wedding_at']-now,
     recover=(p['wedding_at']-now)/60*4,features={'family':2,'friend':-.5,'self':1}))
   else:c.append(candidate('wedding','婚礼に参加',kind='work',task='wedding',minutes=120,cost=4,features={'family':3,'friend':-.5}))
  if s['wedding']:
   if not s['banquet'] and now<2700:
    c.append(candidate('banquet','夜まで宴に残る',minutes=2700-now,features={'family':1.5,'self':1,'friend':-.5}))
   if not s['slept'] and now<p['dawn']:
    c.append(candidate('sleep_dawn','薄明まで眠る',minutes=p['dawn']-now,recover=(p['dawn']-now)/60*p['rest_per_hour'],features={'self':2,'friend':1,'honor':.5,'family':.5}))
  c.extend([candidate('leave_normal','友のもとへ出発',kind='move',to='east_bank',minutes=330,cost=12,features={'friend':2,'honor':2,'self':-.5}),
   candidate('leave_fast','消耗を増して急ぐ',kind='move',to='east_bank',minutes=240,cost=24,features={'friend':2.4,'honor':2,'self':-1.5}),
   candidate('leave_slow','ゆっくり村を離れる',kind='move',to='east_bank',minutes=420,cost=8,features={'friend':1,'honor':1,'self':1}),
   candidate('stay','村に残り生き延びる',minutes=max(1,p['deadline']+1-now),features={'friend':-2,'honor':-2,'self':2,'family':1})])
  return c

 def final_court(self):
  s=self.state
  self.act(candidate('report','王へ帰還を申し出る',kind='work',task='return',minutes=2))
  s['returned_at']=self.rt.now
  self.event('return_observed','Melos',['King','Selinuntius','Melos'],returned_at=self.rt.now,
   deadline=self.p['deadline'],promise_status=self.rt.registry.get('return-hostage')['status'])
  if s['dead_friend']:
   s['outcome']='late_return_friend_executed'
   self.event('late_release','King',['Melos','King'],target='Melos',basis='A7 King late offer enforcement; no friend resurrection')
   return
  s['friend_released']=True
  self.event('hostage_released','King',['King','Selinuntius','Melos'],target='Selinuntius',basis='A7 substitute surrender')
  self.info['King']['return_seen']=True
  self.info['King']['crowd_present']=self.rt.now>=self.p['execution_preparation_at']
  # Confessions require a private doubt event that actually happened, and a choice.
  melos_confessed=False
  if s['own_doubt']:
   mc=self.choose('Melos','confession',[
    candidate('confess','途中の迷いを認める',features={'honor':2,'friend':1}),
    candidate('silent','迷いについて黙る',features={'self':1,'honor':-.5})], 'confess' if self.reference else None)
   melos_confessed=mc['id']=='confess'
   if melos_confessed:self.event('melos_confession','Melos',['Melos','Selinuntius','King'],content='実際に生じた迷いを認める',basis='A9')
  offered=[candidate('silent','黙って再会を受け止める',features={'trust':1,'honesty':-.2})]
  if self.info['Selinuntius']['own_doubt']:
   offered.insert(0,candidate('confess','自分も一度疑ったと認める',features={'honesty':2,'trust':2}))
  response=self.choose('Selinuntius','friend_response',offered,'confess' if self.reference else None)
  s['confessed']=response['id']=='confess'
  self.event('friend_response','Selinuntius',['Melos','Selinuntius','King'],response=response['id'])
  self.info['King']['mutual_confession_seen']=s['confessed'] and melos_confessed
  crowd=self.info['King']['crowd_present'];recognition=2+int(self.info['King']['mutual_confession_seen'])+int(crowd)
  verdict=self.choose('King','verdict',[
   candidate('execute','刑をメロスへ移して執行',features={'control':1,'recognize':-1}),
   candidate('pardon','目撃した信実を認め赦す',features={'control':-.5,'recognize':recognition})], 'pardon' if self.reference else None)
  s['pardon']=verdict['id']=='pardon';s['dead_melos']=not s['pardon']
  if s['dead_melos']:self.rt.people['Melos'].vitality='dead'
  s['outcome']='both_alive_pardoned' if s['pardon'] else 'friend_saved_melos_executed'
  self.event('royal_verdict','King',['King','Selinuntius','Melos'],verdict=verdict['id'],
   observed_return=True,mutual_confession=self.info['King']['mutual_confession_seen'],crowd=crowd,basis='A7/A9 conditional hypothesis')

 def run_choices(self):
  s=self.state
  for step in range(40):
   if s['outcome']:break
   now=self.rt.now;zone=self.rt.people['Melos'].zone;energy=self.rt.people['Melos'].stamina
   if now>=self.rt.end-600 and zone!='court':
    # Observation end is explicit, never complete pending actions by narration.
    if now>self.p['deadline'] and s['exhausted']:
     s['outcome']='exhausted_friend_executed';break
   if s['exhausted']:
    if zone=='spring' and not s['water_taken']:
     self.event('spring_heard','Melos',['Melos'],source='S10',water_present=True)
     c=self.choose('Melos','collapse',[
      candidate('drink','足もとの泉を飲んで再出発',minutes=30,recover=self.p['spring_recovery'],features={'friend':2,'honor':1,'self':1}),
      candidate('give_up','動くことを諦める',minutes=max(1,self.p['deadline']+1-now),features={'self':2,'friend':-1,'honor':-1})], 'drink' if self.reference else None)
     self.act(c)
     if c['id']=='drink':s['water_taken']=True;s['exhausted']=False
     else:s['outcome']='abandoned_friend_executed'
     continue
    self.advance(self.p['deadline']+1 if now<=self.p['deadline'] else now)
    s['outcome']='exhausted_friend_executed';break
   if zone=='court':self.final_court();continue
   if zone=='village':
    s['visited_village']=True
    forced=None
    if self.reference:
     if not s['rested_arrival']:forced='rest_arrival'
     elif not s['negotiated']:forced='negotiate_long'
     elif not s['wedding']:forced='wait_wedding' if now<self.p['wedding_at'] else 'wedding'
     elif not s['banquet']:forced='banquet'
     elif not s['slept']:forced='sleep_dawn'
     else:forced='leave_normal'
    c=self.choose('Melos','village',self.village_choices(),forced)
    if c['id'].startswith('negotiate'):self.marriage(c)
    else:
     self.act(c)
     if c['id']=='disclose':
      s['disclosed']=True
      for a in ['Sister','Groom']:self.info[a].update(hostage='Selinuntius',deadline=self.p['deadline'])
      self.event('hostage_disclosed','Melos',['Sister','Groom','Melos'],deadline=self.p['deadline'],hostage='Selinuntius')
     if c['id']=='rest_arrival':s['rested_arrival']=True
     if c['id']=='wedding':s['wedding']=True
     if c['id']=='banquet':s['banquet']=True
     if c['id']=='sleep_dawn':s['slept']=True
     if c['id'].startswith('leave_'):s['direction']='return';self.observe_river()
     if c['id']=='stay':s['outcome']='village_survival_friend_executed'
    continue
   if zone=='outskirts':
    if s['direction']=='return':
     if self.rt.people['Philostratus'].zone=='outskirts' and not s['warned']:
      s['warned']=True
      self.event('disciple_warning','Philostratus',['Melos','Philostratus'],claim='teacher was being prepared for execution when I left',
       kind_of_information='reported, not actual death',basis='S11/A11')
     offered=[candidate('enter_court','進んで刑を引き受ける',kind='move',to='court',minutes=30,cost=3,features={'friend':2,'honor':2,'self':-1}),
      candidate('stop','城に戻らず生存を選ぶ',minutes=max(1,self.p['deadline']+1-now),features={'self':2,'friend':-2,'honor':-2})]
     c=self.choose('Melos','city_return',offered,'enter_court' if self.reference else None);self.act(c)
     if c['id']=='stop':s['outcome']='abandoned_friend_executed'
    else:
     c=self.choose('Melos','release',[
      candidate('go_village','妹の村へ向かう',kind='move',to='pass',minutes=150,cost=6,features={'friend':1,'family':2,'honor':1,'self':1}),
      candidate('surrender_now','村へ向かわず王のもとへ引き返す',kind='move',to='court',minutes=30,cost=1,features={'friend':2,'family':-1,'honor':2,'self':-2})], 'go_village' if self.reference else None)
     self.act(c)
    continue
   if s['direction']=='outbound':
    to={'pass':'west_bank','west_bank':'east_bank','east_bank':'village'}[zone]
    minutes=self.rt.contract['travel_minutes'][zone][to]
    c=self.choose('Melos','outbound_'+zone,[candidate('continue','村への既知の道を進む',kind='move',to=to,minutes=minutes,cost=minutes/30,features={'family':1})])
    self.act(c)
    if to=='village':
     self.event('village_arrival','Melos',['Sister','Groom','Melos'],city_business_claim=True,death_sentence_disclosed=False,source='S4')
    continue
   if zone=='east_bank':
    flood=self.info['Melos'].get('river_seen',{}).get('bridge')=='destroyed'
    if not flood:
     c=self.choose('Melos','river_intact',[candidate('bridge','残っている橋を渡る',kind='move',to='west_bank',minutes=30,cost=3,features={'friend':1})]);self.act(c)
    else:
     offered=[candidate('swim','激流を泳いで渡る',minutes=self.p['swim_minutes'],cost=self.p['swim_cost'],features={'friend':2,'honor':2,'self':-2}),
      candidate('retreat','来た道を村へ戻る',kind='move',to='village',minutes=330,cost=12,features={'family':1,'self':2,'friend':-2,'honor':-1})]
     if not s['flood_waited']:offered.insert(1,candidate('wait_flood','水位低下を待つ',minutes=180,features={'friend':-.5,'self':2}))
     if self.p['detour_enabled']:
      offered.append(candidate('detour','仮定した600分の浅瀬へ迂回',kind='move',to='west_bank',minutes=600,cost=18,features={'friend':1,'honor':1,'self':1}))
     c=self.choose('Melos','flood',offered,'swim' if self.reference else None)
     if c['id']=='swim':
      success=energy>=max(self.p['swim_min_energy'],self.p['swim_cost'])
      crossing=deepcopy(c)
      if success:crossing.update(kind='move',to='west_bank')
      self.act(crossing)
      self.event('swim_result','Melos',['Melos'],success=success,energy_at_start=energy,threshold=self.p['swim_min_energy'],
       required_cost=self.p['swim_cost'],basis='A3 threshold and ability to pay exertion cost')
      if not success:
       s['dead_melos']=True;self.rt.people['Melos'].vitality='dead'
       self.event('melos_drowned','Melos',[],basis='A3 failed crossing, not canon')
       self.advance(self.p['deadline']+1);s['outcome']='drowned_friend_executed'
     else:
      self.act(c)
      if c['id']=='retreat':s['direction']='return'
      if c['id']=='wait_flood':s['flood_waited']=True
    continue
   if zone=='west_bank':
    c=self.choose('Melos','return_west',[candidate('to_pass','峠へ進む',kind='move',to='pass',minutes=90,cost=5,features={'friend':1})]);self.act(c);continue
   if zone=='pass':
    if now>=self.p['bandits_active_at'] and not s['bandits_resolved']:
     self.event('bandit_demand','Melos',['Melos'],demand='life',royal_connection='unknown',sources=['S9'])
     self.info['Melos']['royal_bandits']={'kind':'suspicion','value':True,'objective_confirmation':False}
     c=self.choose('Melos','bandits',[
      candidate('fight','棍棒を奪い突破を試みる',minutes=self.p['fight_minutes'],cost=self.p['fight_cost'],features={'friend':2,'honor':1,'self':-1}),
      candidate('retreat_bandits','川岸へ引き返す',kind='move',to='west_bank',minutes=150,cost=6,features={'self':2,'friend':-1})], 'fight' if self.reference else None)
     if c['id']=='fight':
      success=energy>=self.p['fight_min_energy'];self.act(c)
      self.event('bandit_result','Melos',['Melos'],success=success,energy_at_start=energy,threshold=self.p['fight_min_energy'],basis='A3')
      if not success:
       s['dead_melos']=True;self.rt.people['Melos'].vitality='dead';self.advance(self.p['deadline']+1);s['outcome']='bandits_killed_friend_executed'
      else:s['bandits_resolved']=True
     else:self.act(c)
    else:
     c=self.choose('Melos','pass_exit',[candidate('to_spring','峠を下る',kind='move',to='spring',minutes=60,cost=12,features={'friend':1})]);self.act(c)
     if self.rt.people['Melos'].zone=='spring' and self.rt.people['Melos'].stamina<18:
      s['exhausted']=True;s['own_doubt']=True
      self.event('collapse','Melos',['Melos'],source='S10',energy=self.rt.people['Melos'].stamina,basis='A3 exhaustion threshold18')
      self.event('private_doubt','Melos',['Melos'],content='疲労で約束を諦めかける',basis='S10/A9')
    continue
   if zone=='spring':
    self.event('spring_heard','Melos',['Melos'],water_present=True,source='S10')
    offered=[candidate('to_city','城下まで走る',kind='move',to='outskirts',minutes=118,cost=18,features={'friend':2,'honor':1,'self':-1})]
    if not s['water_taken']:offered.append(candidate('drink','泉の水を飲む',minutes=30,recover=self.p['spring_recovery'],features={'friend':1,'self':2}))
    c=self.choose('Melos','spring',offered);self.act(c)
    if c['id']=='drink':s['water_taken']=True
    continue
  else:s['outcome']='decision_budget_observation_incomplete'
 def run(self):
  try:self.run_choices()
  except HorizonStop:pass
  s=self.state
  if s['outcome'] is None:s['outcome']='observation_incomplete'
  if s['returned_at'] is None and not s['dead_friend'] and self.rt.now<=self.p['deadline']:
   self.advance(self.p['deadline']+1)
  self.event('observation_result',outcome=s['outcome'],state=deepcopy(s),
   unresolved_future=['王の統治','村の反応','観測後の生活'],not_model_generated=True)
  audit=self.audit()
  components={'friend_alive':int(not s['dead_friend']),'melos_alive':int(not s['dead_melos']),
   'wedding_completed':int(s['wedding']),'promise_fulfilled':int(self.rt.registry.get('return-hostage')['status']=='fulfilled')}
  # Every valid path is equally fit: no original ending, no success filtering.
  result={'arm':self.arm,'seed':self.seed,'reference_witness':self.reference,'genome':self.genome.to_dict(),
   'state':deepcopy(s),'components':components,'fitness':1.0 if not audit['violations'] else 0.0,
   'fitness_scope':'execution integrity only; outcome-neutral; no canon similarity',
   'decisions':self.decisions,'path_signature':digest([(d['actor'],d['context'],d['selected']) for d in self.decisions]),
   'audit':audit,'actor_information_final':self.info,'api_calls':0,'jev_calls':0,'gpu':False,
   'adapter':'local melos_branch_v1 over unmodified CalendarRuntime',
   'effective_model':{'parameters':deepcopy(self.p),'goals':deepcopy(self.goals)},
   'provenance':{'source_commit':self.scenario['source_commit'],
    'scenario_sha256':hashlib.sha256((ROOT/'melos-input/scenario.yaml').read_bytes()).hexdigest(),
    'adapter_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    'input_hashes':{p.relative_to(ROOT/'melos-input').as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
     for p in sorted((ROOT/'melos-input').rglob('*.yaml'))}},
   'registry':self.rt.registry.export_state()}
  return result,self.rt.rows

 def audit(self):
  rows=self.rt.rows;violations=[];starts={};completions={}
  ids=[r['id'] for r in rows]
  if len(ids)!=len(set(ids)):violations.append('duplicate event id')
  if [r['absolute_minute'] for r in rows]!=sorted(r['absolute_minute'] for r in rows):violations.append('nonchronological log')
  for row in rows:
   if row['verb']=='action_started':starts[row['details']['choice']['id']]=row
   if row['verb']=='action_completed':
    d=row['details'];c=d['choice'];completions[c['id']]=row
    start=starts.get(c['id'])
    if start is None or row['absolute_minute']!=start['details']['finish_at'] or d['started_at']!=start['absolute_minute']:
     violations.append('completion timing '+c['id'])
   if row['verb']=='subjective_choice':
    view=row['details']['actor_view']
    if view['actor']!=row['subject']:violations.append('view owner mismatch')
    if any(e['at']>row['absolute_minute'] for e in view['evidence']):violations.append('future evidence')
    if not any(c['id']==row['details']['selected'] for c in row['details']['candidates']):violations.append('unoffered choice')
    if row['details']['view_hash']!=digest(view):violations.append('view hash mismatch')
  pending=sorted(set(starts)-set(completions))
  # The disciple can still be walking when an early trial terminates; never count
  # this as a completed journey. Keep explicit pending evidence.
  if any(starts[x]['subject']=='Melos' for x in pending):violations.append('uncompleted Melos action')
  if self.state['wedding'] and (not self.state['agreed'] or self.rt.ledger['tasks']['wedding']['units']!=1):violations.append('unconsented/uncompleted wedding')
  if self.state['returned_at'] is not None and self.rt.ledger['tasks']['return']['units']!=1:violations.append('uncompleted return')
  return {'violations':violations,'matched_completions':len(completions),'pending_actions':pending,
   'events':len(rows),'scope':'timing/identity/view ownership/known evidence/consent/completion; semantic tests separately'}

def run_saved(out,genome,seed,arm='baseline',reference=False):
 start=time.perf_counter();cpu=time.process_time();trial=Trial(genome,seed,arm,reference)
 result,rows=trial.run();telemetry={'wall_seconds':time.perf_counter()-start,'cpu_seconds':time.process_time()-cpu}
 out=Path(out);out.mkdir(parents=True,exist_ok=True)
 dump(out/'result.json',result);dump(out/'telemetry.json',telemetry)
 (out/'layers.jsonl').write_text(''.join(canonical(r)+'\n' for r in rows),encoding='utf-8')
 if result['audit']['violations']:raise ValueError(result['audit']['violations'])
 return result,telemetry

def evolve(out,population=8,generations=4,ga_seed=20261014,seeds=(0,1),arm='baseline'):
 out=Path(out)
 if out.exists():raise ValueError('fresh GA output required')
 out.mkdir(parents=True);rng=random.Random(ga_seed);archive=Archive();history=[];previous=[]
 pop=[(MelosGenome.random(rng),[]) for _ in range(population)]
 start=time.perf_counter();cpu=time.process_time()
 for g in range(generations):
  if g:pop=_next_population(population,archive,previous,rng,genome_codec=MelosGenome)
  generation=[]
  for i,(genome,parents) in enumerate(pop):
   runs=[]
   for seed in seeds:
    result,t=run_saved(out/f'g{g}/ind-{i}/seed-{seed}',genome,seed,arm)
    result['layers_path']=f'g{g}/ind-{i}/seed-{seed}/layers.jsonl';runs.append(result)
   volatility=statistics.pstdev([len(r['decisions']) for r in runs])
   generation.append({'index':i,'generation':g,'genome':genome.to_dict(),'parents':parents,
    'quality':1.0,'shaped':1.0,'reach_rate':0.0,'volatility':volatility,'runs':runs})
  if g==0:archive.freeze_thresholds([v['volatility'] for v in generation])
  for v in generation:
   # Category is observed consequence, not a prescribed target ending.
   r=v['runs'][0];course=('surrender' if not r['state']['visited_village'] else 'wedding' if r['state']['wedding'] else 'village_without_wedding')
   category=r['state']['outcome']+':'+course;vb=archive.bin_for(v['volatility'])
   v['cell']=[category,vb]
   archive.insert(Elite(MelosGenome.from_dict(v['genome']),1.0,Descriptor(category,v['volatility'],vb),0.0,
    {'layers_path':r['layers_path'],'candidate':f'g{g}/ind-{v["index"]}'},g,tuple(v['parents'])))
   dump(out/f'g{g}/ind-{v["index"]}/result.json',v)
  dump(out/f'g{g}/results.json',generation);history.extend(generation);previous=generation
 archive.save(out/'archive.json')
 all_runs=[r for v in history for r in v['runs']]
 summary={'arm':arm,'population':population,'generations':generations,'ga_seed':ga_seed,'seeds':list(seeds),
  'evaluations':len(all_runs),'unique_paths':len(set(r['path_signature'] for r in all_runs)),
  'outcomes':dict(__import__('collections').Counter(r['state']['outcome'] for r in all_runs)),
  'archive_cells':len(archive.cells),'mutation_probability':.3,'crossover':'uniform 0.5',
  'breeding':'unmodified gapengine.evolve._next_population / _choose_parents; 10% random immigrants',
  'fitness':'all physically valid outcomes = 1; reach_rate=0 for tie neutrality; unsuccessful logs retained',
  'frequency_scope':'candidate appearance in this search only; not real-world occurrence probability',
  'wall_seconds':time.perf_counter()-start,'cpu_seconds':time.process_time()-cpu,
  'api_calls':0,'jev_calls':0,'gpu':False}
 dump(out/'summary.json',summary)
 return summary

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--out',required=True);parser.add_argument('--mode',choices=['smoke','reference','ga'],default='smoke')
 parser.add_argument('--arm',default='baseline');parser.add_argument('--population',type=int,default=8);parser.add_argument('--generations',type=int,default=4)
 args=parser.parse_args()
 if args.mode=='ga':r=evolve(args.out,args.population,args.generations,arm=args.arm)
 else:r,t=run_saved(args.out,MelosGenome.random(random.Random(20261014)),0,args.arm,args.mode=='reference');r={'outcome':r['state']['outcome'],'audit':r['audit'],'performance':t,'components':r['components']}
 print(canonical(r))
