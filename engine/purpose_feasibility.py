"""Bounded goal feasibility through the real Simulation and replayed legal choices.

This is an existence checker, not a replacement actor policy. No production
project is written. A negative sampled rollout is never an impossibility proof.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import fields
import hashlib
import heapq
import itertools
import json
import math
from pathlib import Path
import random
import tempfile
import time
from typing import Any

import yaml

from engine.sim import Simulation, _plain
from engine.subject import Subject
from engine.world import World

VERSION = 1
KINDS = ['zone','holds','holder','present','vitality','known','knows_modifier',
         'strength','believed_strength','hostile_present','confront_success',
         'stance','awareness','bonds','proposal_decided','proposal_state',
         'objective_achieved','turn','day','phase']
DEFAULT_LIMITS = {'max_seconds': 20, 'max_rollouts': 200, 'max_decisions': 5000,
                  'max_frontier': 4000, 'seeds': [0, 1, 2]}
SIMPLE_VERBS = {'move','rest','investigate','craft','give_item'}


def _json(value):
    return json.dumps(_plain(value), ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


class _Stop(Exception):
    def __init__(self, code): self.code = code


class _Diverged(Exception): pass


class _Rng(random.Random):
    """Keep actual effect draws; record when fixed-seed scope is incomplete."""
    def __init__(self, state):
        super().__init__(); self.setstate(state); self.selecting=False; self.effect_draws=0
    def random(self):
        if not self.selecting: self.effect_draws += 1
        return super().random()
    def getrandbits(self, k):
        if not self.selecting: self.effect_draws += 1
        return super().getrandbits(k)


def _target_tree(raw, people):
    """Typed AST is only a heuristic/proof aid; engine ending remains authoritative."""
    try:
        from execution.ending_settings import parse_when
        ids=raw.get('target_ending',[]);ids=[ids] if isinstance(ids,str) else list(ids)
        endings={e['id']:e for e in raw.get('ending',[]) if isinstance(e,dict)}
        trees=[parse_when(endings[i]['when'],raw,people,existing=True)[0] for i in ids]
        return trees[0] if len(trees)==1 else {'kind':'any','children':trees}
    except (KeyError,TypeError,ValueError): return None


def _branches(tree, budget=256):
    if tree is None: return None
    if tree.get('kind')=='any':
        result=[]
        for child in tree['children']:
            parts=_branches(child,budget)
            if parts is None or len(result)+len(parts)>budget:return None
            result.extend(parts)
        return result
    if tree.get('kind')=='all':
        result=[[]]
        for child in tree['children']:
            parts=_branches(child,budget)
            if parts is None or len(result)*len(parts)>budget:return None
            result=[left+right for left in result for right in parts]
        return result
    return [[tree]]


def _closed(world, people):
    """Conservative no-unmodelled-writer profile for structural certificates."""
    allowed={'name','overview','initial_story','world_format_version','action_usage','mechanics',
             'time','protagonist','antagonist','zones','routes','movement','stamina',
             'vitality','thresholds','ending','target_ending','items','facts',
             'default_strength_prior','contest','companionship','pulls','grief','gapengine'}
    if set(world)-allowed:return False
    flags=['slot_execution','action_timing','joint_activities','deliberation','responses',
           'goal_rules','planning','native_planning','social_planning','phase2',
           'effect_library','effects','scheduled_events','daily_events','concerns',
           'trials','disguises','phase_rules']
    if any(world.get(k) for k in flags): return False
    if world.get('gapengine'):return False
    if any(ref.split(":",1)[-1] not in SIMPLE_VERBS for ref in (world.get("action_usage") or {}).get("enabled",[])):return False
    for item in world.get('items',[]):
        if isinstance(item,dict) and item.get('made_from') is not None:
            recipe=item['made_from']
            if not isinstance(recipe,dict) or any(type(q) is not int or q<=0 for q in recipe.values()):return False
    return all(set(p.get('verbs') or []) <= SIMPLE_VERBS for p in people)


def _logical_failure(branch, world):
    values={};intervals={};zones={};enums={};numeric={};functions={};explanations=[]
    for atom in branch:
        k=atom['kind'];key=None;allowed=None
        if k=='zone':
            # Never assume transit/other engine modes restrict zone to the initial catalog.
            actor=atom['actor'];entry=zones.setdefault(actor,{'equal':None,'excluded':set()})
            if atom.get('cmp','==')=='==':
                if entry['equal'] is not None and entry['equal']!=atom['place']:explanations.append({'kind':'mutually_exclusive_locations','actor':actor})
                entry['equal']=atom['place']
            else:entry['excluded'].add(atom['place'])
            if entry['equal'] is not None and entry['equal'] in entry['excluded']:explanations.append({'kind':'equal_and_excluded_location','actor':actor})
        elif k=='vitality':
            key=(k,atom['actor']);entry=enums.setdefault(key,{'positive':None,'excluded':set()});states=set(atom['states'])
            if atom.get('cmp','in') in ['!=','not in']:entry['excluded'].update(states)
            else:entry['positive']=states if entry['positive'] is None else entry['positive']&states
            if entry['positive'] is not None and not entry['positive']-entry['excluded']:explanations.append({'kind':'mutually_exclusive_states','condition':str(key)})
            key=None
        elif k in ['holds','known','knows_modifier','present','hostile_present','confront_success','proposal_decided','proposal_state','objective_achieved','phase']:
            key=(k,_json({a:v for a,v in atom.items() if a not in ['expression','index','negate','kind']}));allowed={not atom.get('negate',False)}
        elif k in ['turn','day']:
            lower,upper=(1,int(world.get('time',{}).get('days',1))*(len(world.get('time',{}).get('slots',[])))) if k=='turn' else (1,int(world.get('time',{}).get('days',1)))
            # These bounds are only the ordinary logical kernel's actual horizon.
            if world.get('slot_execution'): continue
            if k not in intervals:intervals[k]=[lower,upper,set()]
            lo,hi,excluded=intervals[k];v=atom['value'];op=atom.get('cmp','>=')
            if op=='==':lo=max(lo,v);hi=min(hi,v)
            elif op=='!=':excluded.add(v)
            elif op=='>=':lo=max(lo,v)
            elif op=='>':lo=max(lo,math.floor(v)+1)
            elif op=='<=':hi=min(hi,v)
            elif op=='<':hi=min(hi,math.ceil(v)-1)
            intervals[k]=[lo,hi,excluded]
            if lo>hi or lo==hi and lo in excluded:explanations.append({'kind':'horizon_or_numeric_contradiction','condition':k,'lower':lo,'upper':hi})
        elif k=='holder':
            key=(k,atom['item']);entry=functions.setdefault(key,{'has_equal':False,'equal':None,'excluded':set()});value=atom['value']
            if atom.get('cmp','==')=='==':
                if entry['has_equal'] and entry['equal']!=value:explanations.append({'kind':'mutually_exclusive_function_values','condition':str(key)})
                entry.update(has_equal=True,equal=value)
            else:entry['excluded'].add(value)
            if entry['has_equal'] and entry['equal'] in entry['excluded']:explanations.append({'kind':'equal_and_excluded_function_value','condition':str(key)})
            key=None
        elif k in ['strength','believed_strength','stance','awareness','bonds']:
            key=(k,atom.get('actor'),atom.get('other'));entry=numeric.setdefault(key,{'lo':-math.inf,'hi':math.inf,'lc':False,'hc':False,'excluded':set()});value=atom['value'];op=atom.get('cmp','>=')
            if op=='!=':entry['excluded'].add(value)
            if op in ['>','>=','==']:
                closed=op in ['>=','==']
                if value>entry['lo']:entry['lo']=value;entry['lc']=closed
                elif value==entry['lo']:entry['lc']&=closed
            if op in ['<','<=','==']:
                closed=op in ['<=','==']
                if value<entry['hi']:entry['hi']=value;entry['hc']=closed
                elif value==entry['hi']:entry['hc']&=closed
            if entry['lo']>entry['hi'] or entry['lo']==entry['hi'] and (not entry['lc'] or not entry['hc'] or entry['lo'] in entry['excluded']):explanations.append({'kind':'contradictory_numeric_bounds','condition':str(key)})
            key=None
        if key is not None:
            values[key]=allowed if key not in values else values[key]&allowed
            if not values[key]:explanations.append({'kind':'mutually_exclusive_conditions','condition':str(key)})
    return explanations


def _structural_failure(branch, raw, people):
    if not _closed(raw,people):return []
    items={v['name']:v for v in raw.get('items',[]) if isinstance(v,dict)}
    initial={name:sum(max(0,int(p.get('inventory',{}).get(name,0))) for p in people) for name in items}
    possible={name for name,count in initial.items() if count>0}
    knowledge={fact for p in people for fact in p.get('knowledge',[])}
    facts={f['id']:f for f in raw.get('facts',[]) if isinstance(f,dict)}
    reachable={p['id']:{p.get('range',{}).get('entry')} for p in people}
    if any(s.get('type') not in ['investigate','labor'] for d in items.values() for s in d.get('sources',[]) or []):return []
    if any(s.get('type')!='investigate' for d in facts.values() for s in d.get('sources',[]) or []):return []
    changed=True
    while changed:
        changed=False
        for p in people:
            reached=reachable[p['id']]
            if 'move' in p.get('verbs',[]):
                for origin,routes in raw.get('routes',{}).items():
                    if origin not in reached:continue
                    for route in routes or []:
                        if route.get('requires_item') and route['requires_item'] not in possible:continue
                        if route['to'] not in reached:reached.add(route['to']);changed=True
        for name,spec in facts.items():
            available=bool(spec.get('known_by') or spec.get('secret_of'))
            for source in spec.get('sources',[]) or []:
                if source.get('zone'):
                    available|=any('investigate' in p.get('verbs',[]) and source['zone'] in reachable[p['id']] for p in people)
                if source.get('agent') in reachable:
                    available|=any('investigate' in p.get('verbs',[]) and bool(reachable[p['id']] & reachable[source['agent']]) for p in people)
            if available and name not in knowledge:knowledge.add(name);changed=True
        for name,spec in items.items():
            available=bool(spec.get('shop_zone')) # overapproximate purchases, never prune from absent funds
            for source in spec.get('sources',[]) or []:
                available|=any(source.get('type') in p.get('verbs',[]) and source.get('zone') in reachable[p['id']] for p in people)
            recipe=spec.get('made_from')
            if recipe is not None and set(recipe)<=possible and ((spec.get('requires') or {}).get('knowledge') in knowledge or not (spec.get('requires') or {}).get('knowledge')):
                available|=any('craft' in p.get('verbs',[]) and (not spec.get('craft_zone') or spec['craft_zone'] in reachable[p['id']]) for p in people)
            if available and name not in possible:possible.add(name);changed=True
    why=[]
    for a in branch:
        if a['kind']=='holds' and not a.get('negate') and a['item'] not in possible:
            why.append({'kind':'no_possible_item_source','actor':a['actor'],'item':a['item'],'initial_quantity':initial.get(a['item'],0)})
        if a['kind']=='known' and not a.get('negate'):
            name=a['fact']
            if name not in knowledge:why.append({'kind':'no_information_source','fact':name})
        if a['kind']=='zone' and a.get('cmp','==')=='==':
            person=next((p for p in people if p['id']==a['actor']),None)
            if person:
                reached=reachable[person['id']]
                if a['place'] not in reached:why.append({'kind':'disconnected_location_upper_bound','actor':a['actor'],'target':a['place'],'overapprox_reachable':sorted(z for z in reached if z is not None)})
    # Exact conserved resource vectors for acyclic recipes without external production.
    if not any(s.get('sources') or s.get('shop_zone') for s in items.values()):
        memo={}
        def vector(name,stack=()):
            if name in memo:return memo[name]
            if name in stack:raise ValueError('cyclic resource potential')
            recipe=items[name].get('made_from')
            if recipe is None:memo[name]={name:1};return memo[name]
            result={}
            for material,amount in recipe.items():
                for base,q in vector(material,stack+(name,)).items():result[base]=result.get(base,0)+int(amount)*q
            memo[name]=result;return result
        try:
            for name in items:vector(name)
            total={};needed={};seen=set()
            for name,count in initial.items():
                for base,q in memo[name].items():total[base]=total.get(base,0)+q*count
            for a in branch:
                if a['kind']=='holds' and not a.get('negate') and (a['actor'],a['item']) not in seen:
                    seen.add((a['actor'],a['item']))
                    for base,q in memo[a['item']].items():needed[base]=needed.get(base,0)+q
            for base,q in needed.items():
                if q>total.get(base,0):why.append({'kind':'conserved_resource_shortage','resource':base,'minimum_required':q,'total_initial_potential':total.get(base,0)})
        except (KeyError,TypeError,ValueError):pass
    return why


def _certificate(tree, raw, people):
    branches=_branches(tree)
    if not branches:return None
    proof=[]
    for index,branch in enumerate(branches):
        reasons=_logical_failure(branch,raw)+_structural_failure(branch,raw,people)
        if not reasons:return None
        proof.append({'branch':index+1,'reasons':reasons})
    return {'verified':True,'kind':'all_disjuncts_refuted','type':'all_disjuncts_refuted','branches':proof,'scope':'logical constraints or closed structural upper bounds independent of sampled choices','structural_profile':'closed_item_knowledge_move_craft' if _closed(raw,people) else 'logical_constraints_only'}


def _needs(sim, tree):
    """Author-side ordering only; never alters actor knowledge, goals or candidates."""
    if tree is None:return []
    if tree['kind']=='any':
        from engine.predicate import compile_predicate,PREDICATE_NAMES
        protagonist=sim.subjects[sim.world.protagonist];namespace=sim.world.namespace(protagonist,sim._present_for(protagonist),turn=sim.turn,day=sim.day)
        def currently_true(n):
            if n['kind']=='all':return all(currently_true(c) for c in n['children'])
            if n['kind']=='any':return any(currently_true(c) for c in n['children'])
            try:return bool(compile_predicate(n['expression'],set(PREDICATE_NAMES)).evaluate(namespace))
            except (KeyError,TypeError,ValueError):return False
        if any(currently_true(n) for n in tree['children']):return []
        branches=[tasks for n in tree['children'] if (tasks:=_needs(sim,n))]
        return min(branches,key=lambda n:len(n)) if branches else []
    if tree['kind']=='all':
        nodes=tree['children'];tasks=[]
        for n in nodes:
            if n.get('kind')!='zone':tasks.extend(_needs(sim,n))
        if tasks:return tasks
        return [t for n in nodes for t in _needs(sim,n)]
    world=sim.world;k=tree['kind'];person=sim.subjects.get(tree.get('actor'))
    def at(actor,zone,verb,args=()):return {'actor':actor.id,'zone':zone,'verb':verb,'args':tuple(args)}
    def travel(actor,zone,task,stack=()):
        if actor.zone==zone or zone in world.reachable_paths(actor):return [task]
        queue=[actor.zone];paths={actor.zone:[]}
        for origin in queue:
            for route in world.routes.get(origin,()):
                if route.destination not in paths:
                    paths[route.destination]=paths[origin]+[route];queue.append(route.destination)
        for route in paths.get(zone,[]):
            if route.requires_item and not actor.has_item(route.requires_item):
                preparation=item(actor,route.requires_item,1,stack)
                if preparation:return preparation
        return [task]
    def fact(actor,name,stack=()):
        if name in actor.knowledge:return []
        if 'fact:'+name in stack:return []
        if 'investigate' not in actor.verbs:return []
        for source in world.facts.get(name,{}).get('sources',[]) or []:
            if source.get('type')!='investigate':continue
            place=source.get('zone') or (sim.subjects.get(source.get('agent')).zone if source.get('agent') in sim.subjects else None)
            if place:return travel(actor,place,at(actor,place,'investigate',(place,)),stack+('fact:'+name,))
        return []
    def item(actor,name,count=1,stack=()):
        if actor.inventory.get(name,0)>=count or name in stack:return []
        spec=world.items.get(name,{})
        if name in world.recipes:
            knowledge=(spec.get('requires') or {}).get('knowledge')
            if knowledge and knowledge not in actor.knowledge:
                tasks=fact(actor,knowledge,stack+(name,))
                if tasks:return tasks
                return []
            for material,amount in sorted(world.recipes[name].items()):
                if actor.inventory.get(material,0)<int(amount):
                    tasks=item(actor,material,int(amount),stack+(name,))
                    if tasks:return tasks
            if all(actor.inventory.get(m,0)>=int(q) for m,q in world.recipes[name].items()):
                zone=spec.get('craft_zone') or actor.zone
                return travel(actor,zone,at(actor,zone,'craft',(name,)),stack+(name,))
        for source in spec.get('sources',[]) or []:
            if source.get('type') not in ['investigate','labor']:continue
            if source['type'] not in set(actor.verbs)|world.global_action_grants() or not world.genre_allows(source['type']):continue
            zone=source.get('zone')
            if not zone:continue
            index=(spec.get('sources') or []).index(source)
            key='item:'+name+':'+zone+':'+str(index)
            if actor.gathered.get(key,0)>=int(source.get('max',1)):continue
            verb=source['type'];args=() if verb=='labor' else (zone,)
            return travel(actor,zone,at(actor,zone,verb,args),stack+(name,))
        if spec.get('shop_zone'):
            for currency,amount in ((spec.get('price') or {}) if world.mechanics_enabled('budgeted_purchases') else {}).items():
                if actor.inventory.get(currency,0)<int(amount):
                    tasks=item(actor,currency,int(amount),stack+(name,))
                    if tasks:return tasks
            return travel(actor,spec['shop_zone'],at(actor,spec['shop_zone'],'buy',(name,)),stack+(name,))
        holder=next((p for p in sim.subjects.values() if p.id!=actor.id and p.has_item(name)),None)
        if holder:
            # Preparations are ranking hints for existing candidates, never grants.
            # Avoid repeatedly forcing an obviously outmatched fight when authored
            # equipment/training can improve it through real actions.
            if spec.get('lootable',False) and not spec.get('vehicle',False):
                from engine.contest import strength
                future=[p for p in sim._present_for(holder) if p.id!=actor.id]+[actor]
                own=strength(actor,world,future);opponent=strength(holder,world,future)
                if own<opponent*.9:
                    equipment=sorted(((float((v.get('modifier') or {}).get('value',0)),n) for n,v in world.items.items() if isinstance(v.get('modifier'),dict) and float(v['modifier'].get('value',0))>0 and not v.get('vehicle') and (v.get('sources') or v.get('shop_zone') or v.get('made_from'))),reverse=True)
                    for _,gear in equipment:
                        if gear in stack or actor.has_item(gear):continue
                        preparation=item(actor,gear,1,stack+(name,))
                        if preparation:return preparation
                    if 'train' in actor.verbs and actor.base<98:
                        def safe(zone):return not any(p.id!=actor.id and (world.relations.stance(actor.id,p.id)<-.2 or world.perceived_name(actor.id,p.id) in actor.goal.obstacles) for p in world.present_subjects(zone))
                        zones=([actor.zone] if safe(actor.zone) else [])+[z for z in world.reachable_paths(actor) if z!=actor.zone and safe(z)]
                        if zones:return [at(actor,zones[0],'train')]
            fight=at(actor,holder.zone,'fight',(holder.id,));prepared=travel(actor,holder.zone,fight,stack+(name,))
            return prepared if prepared!=[fight] else [fight,at(actor,holder.zone,'persuade',(holder.id,))]
        return []
    if k=='holds' and person and not tree.get('negate'):return item(person,tree['item'])
    if k=='known' and person and not tree.get('negate'):return fact(person,tree['fact'])
    if k=='zone' and person and tree.get('cmp','==')=='==' and person.zone!=tree['place']:
        # Required route items are preparation, not a move pretending the route is open.
        paths=world.reachable_paths(person)
        if tree['place'] not in paths:
            for origin,routes in sorted(world.routes.items()):
                for route in routes:
                    if route.requires_item and not person.has_item(route.requires_item):
                        tasks=item(person,route.requires_item)
                        if tasks:return tasks
        return [at(person,tree['place'],'move',(tree['place'],))]
    if k=='knows_modifier' and person and not tree.get('negate'):
        other=sim.subjects.get(tree['other']);return [at(person,other.zone,'observe',(other.id,))] if other else []
    return []


def _score(sim, subject, action, tree, tasks=None):
    if tasks is None:tasks=_needs(sim,tree)
    mine=[t for t in tasks if t['actor']==subject.id]
    if not mine:return 0.0
    task=mine[0]
    if subject.zone!=task['zone']:
        if action.verb=='move' and str(action.args[0])==task['zone']:return 100.0
        return -5.0 if action.verb=='move' else 0.0
    if action.verb==task['verb'] and tuple(map(str,action.args))[:len(task['args'])]==tuple(map(str,task['args'])):return 100.0
    if action.verb=='rest' and subject.stamina < max(1,subject.stamina_max*.5):return 10.0
    return -10.0 if action.verb=='move' else 0.0


class _AnalysisSimulation(Simulation):
    def __init__(self,*args,script,tree,guard,decision_limit,alternative_limit,**kwargs):
        super().__init__(*args,**kwargs);self.script=script;self.goal_tree=tree;self.guard=guard
        self.choices=[];self.alternatives=[];self.goal_capture=None;self.trace_hash=hashlib.sha256();self.steps=[];self.goal_steps=[];self.relevant_events=[];self.relevant_event_rows=0;self.goal_decision_rows=0;self.trace_rows=0;self.decision_rows=0;self.analysis_decision_limit=decision_limit;self.alternative_limit=alternative_limit;self.alternatives_truncated=False
        def actors(node):
            if not node:return set()
            return {node[k] for k in ['actor','other'] if node.get(k) in self.subjects} | ({node['value']} if node.get('kind')=='holder' and node.get('value') in self.subjects else set()) | set().union(*(actors(c) for c in node.get('children',[])))
        self.goal_actors=actors(tree)|{self.world.protagonist}
        self.analysis_rng=_Rng(self.rng.getstate());self.rng=self.analysis_rng;self.verb_engine.rng=self.rng
    def choose_action(self,subject,*,forced=None):
        self.guard();self.analysis_rng.selecting=True
        try:return super().choose_action(subject,forced=forced)
        finally:self.analysis_rng.selecting=False
    def _purpose_analysis_choose(self,subject,weighted,weights,default_index):
        self.guard()
        if len(self.choices)>=self.analysis_decision_limit:raise _Stop('decision_limit')
        context={'actor':subject.id,'turn':self.turn,'day':self.day,'slot':self.slot}
        offered=[];seen=set();tasks=_needs(self,self.goal_tree)
        for index,(action,weight) in enumerate(weighted):
            if weights[index]<=0:continue
            payload={'verb':action.verb,'args':_plain(action.args),'meta':_plain(action.meta)};key=_digest(payload)
            if key in seen:continue
            seen.add(key);record={**context,'choice':key,**payload};offered.append((index,record,_score(self,subject,action,self.goal_tree,tasks),float(weights[index])))
        position=len(self.choices)
        if position<len(self.script):
            expected=self.script[position]
            selected=next((row for row in offered if all(row[1].get(k)==expected.get(k) for k in ['actor','turn','day','slot','choice'])),None)
            if selected is None:raise _Diverged('Choice prefix no longer matches the exact engine candidate set')
        else:
            ranked=sorted(offered,key=lambda row:(-row[2],-row[3],row[1]['choice']))
            selected=ranked[0]
        for alternative in offered:
            if alternative[1]['choice']!=selected[1]['choice']:
                if len(self.alternatives)>=self.alternative_limit:self.alternatives_truncated=True;continue
                # Choice records are immutable after construction: share them instead
                # of deep-copying O(depth^2) metadata through every frontier branch.
                self.alternatives.append((tuple(self.choices)+(alternative[1],),alternative[2]))
        self.choices.append(selected[1]);return selected[0]
    def _time_day_start(self,frame,writer):self.guard();return super()._time_day_start(frame,writer)
    def _observe_row(self,row):
        self.guard();super()._observe_row(row)
        if row.get('kind') in ['decision','event']:
            self.trace_hash.update(_json(row).encode('utf-8'));self.trace_rows+=1
            if row.get('kind')=='event' and self.goal_capture is None:
                # Global events can enable the goal indirectly. Retain them rather
                # than deciding relevance from a potentially incomplete delta.
                self.relevant_event_rows+=1
                if len(self.relevant_events)<200:self.relevant_events.append(_plain(row))
            if row.get('kind')=='decision':
                self.decision_rows+=1
                if len(self.steps)<200:self.steps.append({k:_plain(row.get(k)) for k in ['turn','day','slot','subject','verb','args','result','details']})
                if row.get('subject') in self.goal_actors:
                    self.goal_decision_rows+=1
                    if len(self.goal_steps)<200:self.goal_steps.append({k:_plain(row.get(k)) for k in ['turn','day','slot','subject','verb','args','result','details']})
    def _ending(self):
        self.guard();ending=super()._ending()
        if ending is not None and self.goal_capture is None:
            self.goal_capture={'ending_id':ending['id'],'turn':self.turn,'day':self.day,'slot':self.slot,
                'capture':_plain(self._capture()),'subjects':{key:_plain({f.name:getattr(p,f.name) for f in fields(p) if f.name!='policy'}) for key,p in self.subjects.items()},
                'truth':deepcopy(self.world.drawn_truth),'delivered':deepcopy(self.world.delivered)}
        return ending


def analyze(world_definition, people_definitions, source:Path, *, limits=None,
            cancelled=lambda:False, progress=lambda value:None, out_root:Path|None=None):
    started=time.monotonic();settings=dict(DEFAULT_LIMITS);settings.update(limits or {});metrics={'rollouts':0,'replays':0,'frontier':0,'decisions':0,'elapsed_seconds':0};reasons=[]
    hints={'zone':'route_and_required_item_preparation','holds':'material_knowledge_currency_equipment_preparation','known':'authored_information_sources','knows_modifier':'offered_observe_action'}
    coverage=[{'kind':kind,'evaluation':'World.ending_reached','search':'bounded_existing_positive_weight_candidates','ordering_hint':hints.get(kind,'generic_existing_candidate_weight_order'),'reachability_claim':'requires_replayed_witness','limitations':['ordinary_kernel_only','existing_actor_candidate_rules','no_external_policies']+([] if kind in hints else ['no_predicate_specific_backchaining'])} for kind in KINDS]
    scope={'version':VERSION,'engine':'Simulation.run','action_space':'existing positive-weight candidates; all actors retained','policy':'no added policy/provider; existing world planners retained','goal_check':'normal engine ending boundary before ending effects','seeds':settings.get('seeds'),'randomness':'seeded effect draws; sampled seeds are not exhaustive random-outcome proofs','state_merging':'none','input_hash':None}
    def result(outcome,code,message,**extra):
        metrics.update(elapsed_seconds=round(time.monotonic()-started,4))
        return {'outcome':outcome,'reasons':[{'code':code,'message':message}]+reasons,'witness':None,'proof':None,'scope':scope,'coverage':coverage,'metrics':dict(metrics),**extra}
    try:
        if getattr(Simulation,'purpose_analysis_selector_version',0)!=1:
            return result('incomplete','selector_adapter_not_loaded','合法な選択肢を列挙するエンジンadapterが読み込まれていません。')
        if set(settings)!=set(DEFAULT_LIMITS):raise ValueError('Unknown feasibility limits')
        for key,maximum in [('max_rollouts',5000),('max_frontier',30000),('max_decisions',20000)]:
            if type(settings[key]) is not int or not 1<=settings[key]<=maximum:raise ValueError('Invalid '+key)
        if type(settings['max_seconds']) not in [int,float] or not 0<float(settings['max_seconds'])<=120 or not math.isfinite(settings['max_seconds']):raise ValueError('Invalid max_seconds')
        if not isinstance(settings['seeds'],list) or not 1<=len(settings['seeds'])<=16 or any(type(s)is not int or not 0<=s<2**31 for s in settings['seeds']):raise ValueError('Invalid effect seeds')
        raw=deepcopy(world_definition);people=deepcopy(people_definitions);source=Path(source).resolve()
        if not isinstance(raw,dict) or not isinstance(people,list) or not people or not source.is_file():raise ValueError('Frozen world, people and source are required')
        if yaml.safe_load(source.read_text(encoding='utf-8'))!=raw:raise ValueError('Frozen source differs from the requested world definition')
        if out_root is not None:
            out_root=Path(out_root).resolve()
            if out_root==source.parent or source.parent in out_root.parents:raise ValueError('Analysis output must be outside the frozen project')
            out_root.mkdir(parents=True,exist_ok=True)
        # Validate the complete world and cast before offering a proof.
        check_world=World(deepcopy(raw),source);cast={p['id']:Subject.from_definition(deepcopy(p)) for p in people}
        if len(cast)!=len(people):raise ValueError('Duplicate actor IDs')
        check_world.resolve_truth(settings['seeds'][0]);check_world.bind_subjects(cast)
        if check_world.protagonist not in cast or check_world.antagonist not in cast:raise ValueError('Protagonist and antagonist must be registered')
        scope['input_hash']=_digest({'world':raw,'people':people});scope['limits']=settings
        if cancelled():return result('cancelled','cancelled','検証を取り消しました。')
        if raw.get('slot_execution') is not None:return result('incomplete','execution_mode_adapter_missing','このslot/calendar実行方式には専用の到達探索adapterがまだありません。通常実行へ読み替えていません。')
        tree=_target_tree(raw,people)
        initialized_people=[{**p,'range':{**p.get('range',{}),'entry':cast[p['id']].zone},
                             'inventory':dict(cast[p['id']].inventory),'knowledge':sorted(cast[p['id']].knowledge),
                             'verbs':sorted(cast[p['id']].verbs)} for p in people]
        proof=_certificate(tree,raw,initialized_people)
        if proof:return result('impossible','certified_contradiction_or_closed_resource_bound','すべての達成分岐に、論理矛盾または閉じた資源・経路の不成立根拠があります。',proof=proof)
    except (KeyError,TypeError,ValueError,OSError,OverflowError,yaml.YAMLError) as error:
        return result('input_error','invalid_input',str(error))
    deadline=started+float(settings['max_seconds']);cutoffs=set();effectful=False;truth_sampling=bool(check_world.truth_candidates);restricted=bool(check_world.planner or check_world.joint_activities or check_world.native_planning);seen=set();queue=[];serial=itertools.count();completed=0
    def guard():
        if cancelled():raise _Stop('cancelled')
        if time.monotonic()>=deadline:raise _Stop('time_limit')
    def run(seed,script,replay=False):
        with tempfile.TemporaryDirectory(prefix='purpose-rollout-',dir=str(out_root) if out_root else None,ignore_cleanup_errors=True) as temporary:
            w=World(deepcopy(raw),source);ps={p['id']:Subject.from_definition(deepcopy(p)) for p in people}
            sim=_AnalysisSimulation(seed,w,ps,Path(temporary),script=script,tree=tree,guard=guard,decision_limit=settings['max_decisions'],alternative_limit=settings['max_frontier'],record_explanations=False)
            try:sim.run()
            except _Stop as stop:
                if stop.code=='cancelled':raise
                return sim,stop.code
            if len(script)>len(sim.choices):raise _Diverged('Script contains choices after the engine terminated')
            return sim,None
    try:
        for seed in settings['seeds']:
            if len(queue)>=settings['max_frontier']:cutoffs.add('frontier_limit');break
            key=_digest([seed,[]]);seen.add(key);heapq.heappush(queue,(-math.inf,0,next(serial),seed,[]))
        while queue and metrics['rollouts']<settings['max_rollouts']:
            guard();_,_,_,seed,script=heapq.heappop(queue);metrics['frontier']=len(queue)
            try:sim,cut=run(seed,script)
            except _Diverged as error:cutoffs.add('choice_replay_diverged');continue
            metrics['rollouts']+=1;metrics['decisions']+=len(sim.choices);effectful|=bool(sim.analysis_rng.effect_draws)
            if cut:cutoffs.add(cut)
            else:completed+=1
            if sim.alternatives_truncated:cutoffs.add('frontier_limit')
            if sim.goal_capture is not None:
                replay,replay_cut=run(seed,deepcopy(sim.choices),True);metrics['replays']+=1
                if replay_cut or replay.goal_capture is None or _digest(replay.goal_capture)!=_digest(sim.goal_capture) or replay.trace_hash.hexdigest()!=sim.trace_hash.hexdigest() or replay.choices!=sim.choices:
                    cutoffs.add('witness_replay_failed')
                else:
                    return result('reachable','exact_engine_witness_replayed','既存エンジンの合法な選択・作用を再生し、達成条件が成立する手順を確認しました。',witness={
                        'verified':True,'type':'legal_choice_sequence_with_seeded_effects','seed':seed,'choices':sim.choices,'steps':sim.steps,'steps_truncated':sim.decision_rows>len(sim.steps),
                        'goal_steps':sim.goal_steps,'goal_actor_steps':sim.goal_steps,'goal_actor_step_count':sim.goal_decision_rows,'goal_steps_truncated':sim.goal_decision_rows>len(sim.goal_steps),'goal_actor_steps_truncated':sim.goal_decision_rows>len(sim.goal_steps),'goal_actors':sorted(sim.goal_actors),
                        'relevant_events':sim.relevant_events,'relevant_event_count':sim.relevant_event_rows,'relevant_events_truncated':sim.relevant_event_rows>len(sim.relevant_events),'events_scope':'all pre-goal simulation events; includes indirectly relevant events',
                        'goal':sim.goal_capture,'goal_state_hash':_digest(sim.goal_capture),'trace_sha256':sim.trace_hash.hexdigest(),'trace_rows':sim.trace_rows,'replay_verified':True,
                        'drawn_truth':sim.world.drawn_truth,'effect_random_draws':sim.analysis_rng.effect_draws,'note':'この手順を自動方針が必ず選ぶ、または全乱数で成功するという保証ではありません。'})
            for prefix,score in sim.alternatives:
                guard()
                if len(queue)>=settings['max_frontier']:cutoffs.add('frontier_limit');break
                key=_digest([seed,prefix])
                if key in seen:continue
                seen.add(key)
                heapq.heappush(queue,(-score,len(prefix),next(serial),seed,prefix))
            metrics['frontier']=len(queue);progress({'outcome':'running',**metrics})
        if queue:cutoffs.add('rollout_limit')
        if not cutoffs and not effectful and not truth_sampling and not restricted and _closed(raw,people):
            return result('impossible','finite_deterministic_candidate_tree_exhausted','設定された期間・既存の候補生成規則内で、全人物の正の重みの選択肢を尽くしました。',proof={'verified':True,'kind':'finite_deterministic_full_choice_tree','type':'finite_deterministic_full_choice_tree','completed_rollouts':completed,'seeds_equivalent_without_effect_randomness':True,'horizon':{'days':check_world.days,'slots':list(check_world.slots)},'scope':'current enabled candidate-generation rules; not a proof after changing actor goals or world rules'})
        if effectful:cutoffs.add('effect_randomness_not_exhausted')
        if truth_sampling:cutoffs.add('initial_truth_scenarios_not_exhausted')
        if restricted:cutoffs.add('planner_candidate_scope_restricted')
        codes=sorted(cutoffs) or ['unsupported_global_negative_proof_profile']
        reasons.extend({'code':code,'message':code} for code in codes[1:])
        return result('incomplete',codes[0],'到達例はまだ確認できません。探索上限または未検証の分岐が残るため、到達不能とは判定していません。')
    except _Stop as stop:
        return result('cancelled' if stop.code=='cancelled' else 'incomplete',stop.code,'検証を取り消しました。' if stop.code=='cancelled' else '探索時間の上限に達しました。到達不能とは判定していません。')
    except (TypeError,ValueError,KeyError,OSError,OverflowError,IndexError,AttributeError,RecursionError) as error:
        return result('incomplete','engine_or_adapter_error','実エンジン再生を完了できませんでした：'+str(error))
