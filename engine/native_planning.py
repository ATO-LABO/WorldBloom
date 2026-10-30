"""Native verbs exposed as operators to the common GoalPlanner.

Execution stays in VerbEngine. Forecasts use the actor's inventory, received
evidence, authored affordances and believed strength, never sampled wins.
"""
from copy import copy, deepcopy
import json
import math

from engine.actions import Action
from engine.concerns import coordinates, effect_coordinate, shape, identifier, distinct


def aux(*parts):
    return ('state', '@native:' + json.dumps(parts, ensure_ascii=False, separators=(',', ':')))


def assign(coord, value):
    kind, *args = coord
    effect = {'kind':kind, 'value':value}
    if kind == 'state': effect['key'] = args[0]
    elif kind == 'relation': effect.update(observer=args[0], target=args[1])
    else:
        effect['actor'] = args[0]
        if kind == 'inventory': effect['item'] = args[1]
        if kind == 'knowledge': effect['fact'] = args[1]
    return effect


class NativePlanning:
    def __init__(self, raw):
        shape(raw, {'version','outcomes','inquiry_facts'})
        if raw['version'] != 1 or type(raw['version']) is not int:
            raise ValueError('Unsupported native planning version')
        if not isinstance(raw['outcomes'],list) or len(raw['outcomes']) > 32:
            raise ValueError('Bounded native outcomes required')
        self.config = deepcopy(raw)
        self.keys = set()
        for rule in raw['outcomes']:
            shape(rule, {'key','verb','match','value'})
            identifier(rule['key']); identifier(rule['value'])
            if rule['verb'] not in {'fight','confront'} or not isinstance(rule['match'],dict):
                raise ValueError('Unsupported native outcome rule')
            if not set(rule['match']) <= {'actor','target','result','winner','loser','fact'} or not rule['match']:
                raise ValueError('Bounded native outcome match required')
            if rule['key'] in self.keys: raise ValueError('Duplicate native outcome')
            self.keys.add(rule['key'])
            for value in rule['match'].values(): identifier(value)
        if not isinstance(raw['inquiry_facts'],list) or len(raw['inquiry_facts']) > 80:
            raise ValueError('Bounded inquiry facts required')
        distinct(raw['inquiry_facts'],80)
        self.received = {}
        self.history = []
        self.observations = {}
        self.visits = {}
        self.blocked_relations = set()
        self.rethought = set()

    def bind(self, world):
        if world.slot_execution is not None or world.planner is None or world.responses is None or world.action_timing is not None:
            raise ValueError('Native planning requires ordinary shared planning')
        if not self.keys <= set(world.responses.state): raise ValueError('Unknown native outcome state')
        if not set(self.config['inquiry_facts']) <= set(world.facts): raise ValueError('Unknown inquiry fact')
        if any(world.facts[f].get('values') for f in self.config['inquiry_facts']):
            raise ValueError('Inquiry operators require boolean evidence sources')
        for rule in self.config['outcomes']:
            for key, value in rule['match'].items():
                if key in {'actor','target','winner','loser'} and value not in world.subjects:
                    raise ValueError('Unknown native outcome person')
                if key == 'fact' and value not in world.facts: raise ValueError('Unknown outcome fact')
        self.world = world
        self.received = {who:{key:world.responses.state[key] for key in self.keys} for who in world.subjects}
        world.planning_domain = self

    def handles(self, coord):
        return coord[0] == 'state' and coord[1] in self.keys

    def read(self, coord, actor=None):
        if not self.handles(coord): return None
        return self.world.responses.state[coord[1]] if actor is None else self.received[actor].get(coord[1])

    def visible(self, actor):
        world = self.world
        state = world.planner._ordinary_visible(world, actor)
        state[('vitality',actor.id)]=actor.vitality
        remembered=world.planner.memory.get(actor.id,{}).get('known',{})
        for peer in world.subjects.values():
            if peer.id!=actor.id and peer.zone!=actor.zone and remembered.get(('zone',peer.id))==actor.zone:
                state[('zone',peer.id)]=None # Seeing an empty place invalidates a stale presence.
        state.update({('state',key):value for key,value in self.received[actor.id].items()})
        state[aux('base',actor.id)] = actor.base
        for key,value in actor.gathered.items(): state[aux('gathered',actor.id,key)] = value
        for key,value in actor.gather_progress.items(): state[aux('progress',actor.id,key)] = value
        for peer in world.subjects:
            state[aux('observations',actor.id,peer)] = self.observations.get((actor.id,peer),0)
            if peer==actor.id:continue
            if world.subjects[peer].zone==actor.zone:
                state[('zone',peer)]=actor.zone
                state[('vitality',peer)]=world.subjects[peer].vitality
                # A present owner's usable vehicle is visible to its permitted
                # borrowers. Other possessions are not made public.
                for item,spec in world.items.items():
                    access=spec.get('access') or {}
                    if (spec.get('vehicle') and access.get('mode')=='owner'
                            and 'companions' in access.get('allow',[])
                            and actor.id in (world.subjects[peer].companions or [])):
                        state[('inventory',peer,item)]=world.subjects[peer].inventory.get(item,0)
                from engine.contest import believed_strength
                state[aux('rival',actor.id,peer)]=believed_strength(actor,world.subjects[peer],world,world.present_subjects(actor.zone))
                belief=actor.beliefs_about.get(peer)
                for modifier in world.subjects[peer].all_modifiers(world,world.present_subjects(actor.zone)):
                    if modifier.visible or belief and modifier.source in belief.known_modifiers:
                        state[aux('modifier',peer,modifier.source)]=modifier.value if modifier.active else 0
                        state[aux('item_modifier',peer,modifier.source)]=modifier.kind=='item'
                        if modifier.kind=='item' and modifier.source in world.items:
                            state[('inventory',peer,modifier.source)]=world.subjects[peer].inventory.get(modifier.source,0)
                        state[aux('disabled',peer,modifier.source)]=not modifier.active
                        if modifier.affinity_cap is not None and (not modifier.affinity_cap_targets or actor.id in modifier.affinity_cap_targets):
                            state[aux('cap',peer,modifier.source)]=modifier.affinity_cap if modifier.active else 1.0
            elif aux('rival',actor.id,peer) not in self.world.planner.memory.get(actor.id,{}).get('known',{}):
                belief=actor.beliefs_about.get(peer)
                state[aux('rival',actor.id,peer)]=world.default_strength_prior if belief is None else belief.base_estimate
        for zone in world.zones:state[aux('visits',actor.id,zone)]=self.visits.get((actor.id,zone),0)
        for threshold in world.thresholds:state[aux('phase',threshold['id'])]=threshold['id'] in actor.phase
        for peer in world.subjects:
            state[aux('blocked_relation',peer,actor.id)]=(actor.id,peer,actor.id) in self.blocked_relations
        state[aux('reviewed',actor.id,len(actor.knowledge))]=(actor.id,tuple(sorted(actor.knowledge))) in self.rethought
        return state

    def _shadow(self, actor, state, spent, point):
        shadow = copy(actor)
        shadow.zone = state[('zone',actor.id)]
        shadow.inventory = {i:state.get(('inventory',actor.id,i),0) for i in self.world.items}
        shadow.knowledge = {f for f in self.world.facts if state.get(('knowledge',actor.id,f),False)}
        shadow.stamina = max(0,actor.stamina-spent)
        shadow.base = state.get(aux('base',actor.id),actor.base)
        shadow.phase = set(actor.phase)
        shadow.phase.update(t['id'] for t in self.world.thresholds if state.get(aux('phase',t['id'])))
        shadow.phase.update(self._crossed(shadow,state,point))
        return shadow

    def _crossed(self, subject, state, point):
        """Evaluate projected phases using received coordinates, never world truth."""
        class UnknownForecast(Exception):
            pass
        def read(*coordinate):
            if coordinate not in state or state[coordinate] is None:raise UnknownForecast()
            return state[coordinate]
        def unknown(*args):raise UnknownForecast()
        world=self.world
        namespace=world.namespace(subject,[],turn=point,day=(point-1)//len(world.slots)+1)
        for name,value in list(namespace.items()):
            if callable(value):namespace[name]=unknown
        namespace.update(zone=lambda who:read('zone',who),
                         holds=lambda who,item:read('inventory',who,item)>0,
                         known=lambda who,fact:read('knowledge',who,fact),
                         vitality=lambda who:read('vitality',who),
                         stance=lambda observer,target:read('relation',observer,target))
        crossed=[]
        for threshold in world.thresholds:
            if threshold['id'] in subject.phase:continue
            try:
                if threshold['predicate'].evaluate(namespace):crossed.append(threshold['id'])
            except UnknownForecast:
                continue
        return crossed

    def _route_access(self, subject, route, state):
        world=self.world;projection={'effects':world.planner.effects(state)}
        if any(world.concerns.evaluate(world,cid,actor=subject.id,projection=projection)['state']!='true'
               for cid in route.requires_conditions):return False,None
        item=route.requires_item
        if item is None or subject.has_item(item):return True,None
        access=world.items[item].get('access') or {}
        if access.get('mode')!='owner' or 'companions' not in access.get('allow',[]):return False,None
        for owner in world.subjects.values():
            if (subject.id in (owner.companions or []) and state.get(('zone',owner.id))==subject.zone
                    and state.get(('inventory',owner.id,item),0)>0):return True,owner.id
        return False,None

    def _needs(self, actor, root):
        world = self.world
        needed = set(world.goal_rules.affected_coordinates(world,root))
        # Route prerequisites are existing authored affordances, not extra values.
        for routes in world.routes.values():
            for route in routes:
                if route.requires_item: needed.add(('inventory',actor.id,route.requires_item))
        changed = True
        while changed:
            before = set(needed)
            for kind,*args in list(needed):
                if kind == 'inventory' and args[0] == actor.id:
                    for item in world.recipes.get(args[1],{}): needed.add(('inventory',actor.id,item))
                    fact=(world.items[args[1]].get('requires') or {}).get('knowledge')
                    if fact:needed.add(('knowledge',actor.id,fact))
            changed = before != needed
        return needed

    def _outcomes(self, verb, data):
        return [assign(('state',r['key']),r['value']) for r in self.config['outcomes']
                if r['verb']==verb and all(data.get(k)==v for k,v in r['match'].items())]

    def heuristic(self, actor, root, state):
        """Optimistic distance to an unresolved native goal/prerequisite.

        This orders a bounded search; it never grants an item, evidence or win.
        Neither optimality nor the success of a forecast contest is promised.
        """
        world=self.world; zone=state[('zone',actor.id)]
        distances={zone:0}; pending=[zone]
        for origin in pending:
            for edge in world.routes.get(origin,()):
                if edge.destination not in distances:
                    distances[edge.destination]=distances[origin]+1;pending.append(edge.destination)
        anchor=world.concerns.conditions[root]['test']
        if anchor['kind']=='co_location' and actor.id in {anchor['actor'],anchor['target']}:
            peer=anchor['target'] if anchor['actor']==actor.id else anchor['actor']
            destination=state.get(('zone',peer))
            if destination is not None:
                return distances.get(destination,50)+(state.get(('vitality',peer))=='downed')
        needed=self._needs(actor,root)
        # Resolve transport recipes before heading toward a blocked crossing.
        for item,recipe in sorted(world.recipes.items()):
            if ('inventory',actor.id,item) not in needed or state.get(('inventory',actor.id,item),0):continue
            missing={i:n-state.get(('inventory',actor.id,i),0) for i,n in recipe.items()
                     if state.get(('inventory',actor.id,i),0)<n}
            if missing:
                sites=[(distances.get(s.get('zone'),50),int(s.get('count',1)))
                       for i in missing for s in world.items[i].get('sources',[]) if s.get('zone')]
                return 10+sum(missing.values())+min((d+c for d,c in sites),default=50)
            return 8+distances.get(world.items[item].get('craft_zone',zone),50)
        targets=[]
        for rule in self.config['outcomes']:
            if ('state',rule['key']) not in needed or state.get(('state',rule['key']))==rule['value']:continue
            target=rule['match'].get('loser') or rule['match'].get('target')
            if target and ('zone',target) in state:targets.append(state[('zone',target)])
        if targets:return 3+min(distances.get(z,50) for z in targets)
        outstanding=[]
        from engine.objectives import references
        for cid in references(world.goal_rules.definitions[root]['success']):
            test=world.concerns.conditions[cid]['test']
            if test['kind']=='zone' and test['actor']==actor.id and state.get(('zone',actor.id))!=test['equals']:
                outstanding.append(distances.get(test['equals'],50))
        return min(outstanding,default=0)

    def steps(self, actor, root, state, turn, elapsed, spent):
        from engine.phase2 import available_verbs
        from engine.contest import strength, believed_strength
        world = self.world; point = turn+elapsed
        shadow = self._shadow(actor,state,spent,point)
        allowed = available_verbs(world,shadow,turn=point,day=(point-1)//len(world.slots)+1)
        needed = self._needs(actor,root)
        relevant_outcomes = [r for r in self.config['outcomes'] if ('state',r['key']) in needed]
        fight_targets = {r['match']['loser'] for r in relevant_outcomes if r['verb']=='fight' and 'loser' in r['match']}
        def step(verb,args,label,effects,assumptions=None,meta=None,cost=0,recovery=0):
            if verb not in allowed or not world.genre_allows(verb) or cost > shadow.stamina: return
            peer=world.subjects.get(str(args[0])) if args else None
            if peer is not None and world.action_graph_enabled:
                if world.permission(verb,world.target_role(actor,peer))<=0:return
                observed=state.get(aux('observations',actor.id,peer.id),0)>=2
                if not world.prerequisite_ok(verb,actor,peer,source=str(args[1]) if verb=='neutralize' else None):
                    if verb!='confront' or not observed:return
            yield {'verb':verb,'args':args,'label':label,'duration':1,'cost':cost,'recovery':recovery,
                   'effects':[*effects,*[assign(aux('phase',phase),True) for phase in sorted(shadow.phase)]],
                   'assumptions':assumptions or [],'meta':meta or {}}
        if shadow.stamina < actor.stamina_max:
            yield from step('rest',[],'休息する',[],recovery=min(actor.stamina_max-shadow.stamina,actor.stamina_recover))
        sought={c[1] for c in needed if c[0]=='relation' and c[1]!=actor.id} | {c[2] for c in needed if c[0]=='relation' and c[2]!=actor.id}
        sought.update(c[1] for c in needed if c[0]=='zone' and c[1]!=actor.id)
        unknown_people=any(state.get(('zone',who)) is None for who in sought)
        routes=sorted(world.routes.get(shadow.zone,()),key=lambda r:(state.get(aux('visits',actor.id,r.destination),0),r.destination))
        for route in routes:
            access,lender=self._route_access(shadow,route,state)
            if (route.destination not in shadow.range_zones or route.destination in world._excluded_zones(shadow) or not access):continue
            arrived=copy(shadow);arrived.zone=route.destination
            crossed=self._crossed(arrived,{**state,('zone',actor.id):route.destination},point)
            yield from step('move',[route.destination],route.destination+'へ移動',
                [assign(('zone',actor.id),route.destination),*[assign(aux('phase',phase),True) for phase in crossed]],cost=route.cost,
                assumptions=[] if lender is None else [{'requires_presence':lender,'zone':shadow.zone,'item':route.requires_item}],
                meta={'dest':route.destination,'hops':1,'cost':route.cost,'route':[shadow.zone,route.destination],
                      'native_information':unknown_people,'observation_site':route.destination})
        # Item quantities are bounded by authored needs, recipes and stock limits.
        limits = {}
        for definition in world.concerns.conditions.values():
            test = definition['test']
            if test['kind']=='inventory' and test['actor']==actor.id:
                limits[test['item']] = max(limits.get(test['item'],0),test['minimum'])
        for recipe in world.recipes.values():
            for item,count in recipe.items(): limits[item]=max(limits.get(item,0),count)
        gathered = []; info = []
        for item,spec in sorted(world.items.items()):
            if ('inventory',actor.id,item) not in needed or shadow.inventory.get(item,0) >= limits.get(item,1): continue
            for index,source in enumerate(spec.get('sources',[])):
                if source.get('type')!='investigate' or source.get('zone')!=shadow.zone: continue
                key=f'item:{item}:{shadow.zone}:{index}'; cap=int(source.get('max',1))
                have=state.get(aux('gathered',actor.id,key),0)
                if have>=cap: continue
                progress_key='progress:'+key
                progress=state.get(aux('progress',actor.id,progress_key),0)+1
                done=progress>=int(source.get('count',1))
                gathered.append(assign(aux('progress',actor.id,progress_key),0 if done else progress))
                if done:
                    gathered += [assign(aux('gathered',actor.id,key),have+1),
                                 assign(('inventory',actor.id,item),shadow.inventory.get(item,0)+1)]
        inquiry=set(self.config['inquiry_facts']) if any(r['verb']=='confront' for r in relevant_outcomes) else set()
        inquiry.update(c[2] for c in needed if c[0]=='knowledge' and c[1]==actor.id)
        if any(r['verb']=='confront' for r in relevant_outcomes):
            if any(world.facts[f].get('implies') or world.facts[f].get('refutes') for f in actor.knowledge):
                if not state.get(aux('reviewed',actor.id,len(actor.knowledge))):
                    yield from step('rethink',[],'集めた証拠を再検討する',
                        [assign(aux('reviewed',actor.id,len(actor.knowledge)),True)],
                        meta={'native_information':True,'observation_site':shadow.zone})
        if inquiry:
            for fact in sorted(inquiry):
                if fact in shadow.knowledge: continue
                for index,source in enumerate(world.facts[fact].get('sources',[])):
                    present = source.get('agent') and state.get(('zone',source['agent']))==shadow.zone
                    if source.get('type')!='investigate' or not (source.get('zone')==shadow.zone or present): continue
                    key=f'fact:{fact}:{index}'; progress=state.get(aux('progress',actor.id,key),0)+1
                    info.append(assign(aux('progress',actor.id,key),progress))
                    if progress>=int(source.get('count',1)): info.append(assign(('knowledge',actor.id,fact),True))
        if gathered or info:
            yield from step('investigate',[shadow.zone],shadow.zone+'で調べる',gathered+info,
                            meta={'native_information':bool(info),'observation_site':shadow.zone})
        for item,recipe in sorted(world.recipes.items()):
            if ('inventory',actor.id,item) not in needed or shadow.inventory.get(item,0)>=limits.get(item,1): continue
            spec=world.items[item]; fact=(spec.get('requires') or {}).get('knowledge')
            if (spec.get('craft_zone',shadow.zone)!=shadow.zone or fact and fact not in shadow.knowledge
                    or any(shadow.inventory.get(i,0)<n for i,n in recipe.items())): continue
            effects=[assign(('inventory',actor.id,i),shadow.inventory[i]-n) for i,n in recipe.items()]
            yield from step('craft',[item],item+'を作る',[*effects,assign(('inventory',actor.id,item),shadow.inventory.get(item,0)+1)])
        present=[p for p in world.subjects.values() if p.id!=actor.id and state.get(('zone',p.id))==shadow.zone]
        for peer in present:
            target=peer.id
            if (('vitality',target) in needed and state.get(('vitality',target))=='downed'
                    and state.get(('relation',actor.id,target),-1)>=.3):
                yield from step('rescue',[target],target+'を救助する',[
                    assign(('vitality',target),'revived')])
            relation_needed = ('relation',target,actor.id) in needed or ('relation',actor.id,target) in needed
            blocked_relation=state.get(aux('blocked_relation',target,actor.id),False)
            known=actor.beliefs_about.get(target)
            known_sources=set() if known is None else set(known.known_modifiers)
            caps=[state[aux('cap',target,source)] for source in known_sources
                  if aux('cap',target,source) in state and not state.get(aux('disabled',target,source))]
            cap=min(caps,default=1.0)
            if relation_needed and blocked_relation and cap==1:
                attempts=state.get(aux('observations',actor.id,target),0)
                if attempts < 6:
                    yield from step('observe',[target],target+'の事情を確かめる',
                        [assign(aux('observations',actor.id,target),attempts+1)],
                        meta={'native_information':True,'observation_site':shadow.zone})
                continue
            for source in sorted(known_sources):
                if state.get(aux('disabled',target,source)) or not (target in fight_targets or relation_needed and state.get(aux('cap',target,source),1)<1):continue
                effects=[assign(aux('disabled',target,source),True)]
                effects.append(assign(aux('blocked_relation',target,actor.id),False))
                if ('relation',target,actor.id) in state:
                    effects.append(assign(('relation',target,actor.id),max(-1,state[('relation',target,actor.id)]-.2)))
                if (world.items.get(source,{}).get('lootable') and state.get(aux('item_modifier',target,source))
                        and state.get(('inventory',target,source),0)>0):
                    effects.append(assign(('inventory',actor.id,source),shadow.inventory.get(source,0)+1))
                    effects.append(assign(('inventory',target,source),max(0,state.get(('inventory',target,source),1)-1)))
                yield from step('neutralize',[target,source],target+'の'+source+'を無力化する',effects)
            companion = bool(fight_targets) and target in (actor.companions or []) and target != actor.id
            for item,count in sorted(shadow.inventory.items()):
                delivery_needed=('inventory',target,item) in needed
                if count<1 or not (delivery_needed or relation_needed or companion): continue
                definition=world.items[item]; give=definition.get('give',{})
                if not delivery_needed and not give: continue
                if set(give.get('ineffective_for',[])) & set(peer.tags): continue
                a=state.get(('relation',actor.id,target));b=state.get(('relation',target,actor.id))
                if not delivery_needed and (b is None or b>=world.companionship['threshold']): continue
                effects=[assign(('inventory',actor.id,item),count-1),assign(('inventory',target,item),state.get(('inventory',target,item),0)+1)]
                if a is not None:effects.append(assign(('relation',actor.id,target),min(1,a+give.get('giver_affinity',.05))))
                if b is not None:effects.append(assign(('relation',target,actor.id),max(b,min(cap,b+give.get('receiver_affinity',.2)))))
                yield from step('give_item',[target,item],target+'に'+item+'を渡す',effects)
            if relation_needed:
                a=state.get(('relation',actor.id,target));b=state.get(('relation',target,actor.id))
                effects=[]
                if a is not None:effects.append(assign(('relation',actor.id,target),min(1,a+.04)))
                if b is not None:effects.append(assign(('relation',target,actor.id),max(b,min(cap,b+round(.08*(1+actor.traits['social']),4)))))
                if effects:yield from step('persuade',[target],target+'に気持ちを伝える',effects)
            if target in fight_targets and state.get(('vitality',target),'alive') in {'alive','revived'}:
                attempts=state.get(aux('observations',actor.id,target),0)
                if attempts<2:
                    yield from step('observe',[target],target+'を観察する',
                        [assign(aux('observations',actor.id,target),attempts+1)],
                        meta={'native_information':True,'observation_site':shadow.zone})
                own=strength(shadow,world,[shadow])
                # Equipment comes from projected inventory. Support uses only
                # received relationship information, never a remote reverse tie.
                for friend in present:
                    future=state.get(('relation',friend.id,actor.id),-1)
                    if future>=world.companionship['threshold']:own+=friend.ally_value
                rival=state.get(aux('rival',actor.id,target),world.default_strength_prior)
                for source in known_sources:
                    if state.get(aux('disabled',target,source)):
                        rival-=state.get(aux('modifier',target,source),0)
                chance=1/(1+math.exp(max(-700,min(700,(rival-own)/world.contest['tau']))))
                # Existing temper controls willingness to attempt a risky fight.
                # This is a planning policy, not a probability of actual victory.
                if chance >= .1*(1-actor.traits['temper']):
                    # A success forecast must not hide a known losing branch
                    # that breaks an explicit non-negotiable commitment.
                    loss=[assign(('vitality',actor.id),'downed')]
                    loss += [assign(('inventory',actor.id,item),0) for item,count in shadow.inventory.items()
                             if count and world.items[item].get('lootable') and not world.items[item].get('vehicle')]
                    losing=dict(state);losing.update({effect_coordinate(e):e['value'] for e in loss})
                    if world.planner._safe(world,actor,root,losing,{effect_coordinate(e) for e in loss}) is None:continue
                    effects=[assign(('vitality',target),'downed')]
                    effects+=self._outcomes('fight',{'actor':actor.id,'target':target,'winner':actor.id,'loser':target,'result':'won'})
                    for item,definition in world.items.items():
                        count=state.get(('inventory',target,item),0)
                        if count and definition.get('lootable') and not definition.get('vehicle'):
                            effects += [assign(('inventory',actor.id,item),shadow.inventory.get(item,0)+count),assign(('inventory',target,item),0)]
                    yield from step('fight',[target],target+'と戦う',effects,
                        [{'native_contest':target,'success_probability':round(chance,6)}])
            for rule in relevant_outcomes:
                if rule['verb']!='confront':continue
                fact=rule['match'].get('fact');belief=actor.beliefs.get(fact);definition=world.facts.get(fact,{})
                if belief is None or belief.value!=target or belief.confidence<definition.get('act_threshold',.6):continue
                observed=actor.beliefs_about.get(target)
                if not (observed and observed.identity_seen) and state.get(aux('observations',actor.id,target),0)<2:
                    yield from step('observe',[target],target+'を観察する',
                        [assign(aux('observations',actor.id,target),state.get(aux('observations',actor.id,target),0)+1)],
                        meta={'native_information':True,'observation_site':shadow.zone})
                effects=self._outcomes('confront',{'actor':actor.id,'target':target,'result':'exposed','fact':fact})
                yield from step('confront',[target,fact],target+'に証拠を示す',effects,[{'belief_check':fact,'expected':target}])
        if fight_targets and shadow.base<90:
            yield from step('train',[],'力を鍛える',[assign(aux('base',actor.id),round(shadow.base+4*(1-shadow.base/100),2))],cost=1)

    def information(self, actor, root, turn):
        return None  # Partial information paths are found by the same search.

    def after_action(self, world, actor, action, result, details, turn):
        if action.verb=='move' and result=='moved':
            # A witnessed single-edge departure supplies a last-seen direction,
            # not a remote live location feed or a multi-hop destination.
            route=details.get('route',[]);origin=details.get('origin')
            for witness in world.subjects.values():
                if (witness.id==actor.id or not world.planner.owns(witness)
                        or witness.vitality not in {'alive','revived'}):continue
                if witness.zone not in {origin,actor.zone}:continue
                memory=world.planner.memory.setdefault(witness.id,{'known':{},'negative':set(),'blocked':set(),'last':None})
                memory['known'][('zone',actor.id)]=actor.zone if witness.zone==actor.zone or len(route)==2 else None
                memory['known'][('vitality',actor.id)]=actor.vitality
                details.setdefault('native_departure_observations',[]).append({
                    'observer':witness.id,'actor':actor.id,'turn':turn,
                    'origin':origin,'last_seen_zone':memory['known'][('zone',actor.id)]})
        if action.verb=='neutralize' and result=='neutralized':
            self.blocked_relations={v for v in self.blocked_relations if v[:2]!=(actor.id,str(action.args[0]))}
        if action.verb=='rethink' and result in {'rethought','unchanged'}:
            self.rethought.add((actor.id,tuple(sorted(actor.knowledge))))
        if result in {'given','persuaded'} and action.meta.get('goal_plan',{}).get('steps'):
            before=world.planner.memory.get(actor.id,{}).get('known',{})
            for effect in action.meta['goal_plan']['steps'][0]['effects']:
                if effect['kind']!='relation':continue
                observer,target=effect['observer'],effect['target']; prior=before.get(('relation',observer,target))
                actual=world.relations.stance(observer,target)
                if prior is not None and effect['value']>prior and actual<=prior:
                    self.blocked_relations.add((actor.id,observer,target))
                    details.setdefault('native_observations',[]).append({'kind':'relationship_not_improved','observer':observer,'target':target,'before':prior,'after':actual})
        if action.verb=='move' and result=='moved':
            pair=(actor.id,actor.zone);self.visits[pair]=self.visits.get(pair,0)+1
        if action.verb=='observe' and result=='observed':
            pair=(actor.id,str(action.args[0]));self.observations[pair]=self.observations.get(pair,0)+1
        data={'actor':actor.id,'result':result,**details}
        effects=self._outcomes(action.verb,data)
        if not effects:return
        audience={p.id for p in world.subjects.values() if p.zone==actor.zone}
        records=[]
        for effect in effects:
            key=effect['key'];value=effect['value'];world.responses.state[key]=value
            for who in audience:self.received[who][key]=value
            record={'id':'native:'+str(turn)+':'+actor.id+':'+key,'key':key,'value':value,
                    'actor':actor.id,'verb':action.verb,'result':result,'turn':turn,'received_by':sorted(audience)}
            self.history.append(record);records.append(record)
        details['native_outcomes']=records

    def snapshot(self):
        return {'received':deepcopy(self.received),'history':deepcopy(self.history),
                'blocked_relations':[list(v) for v in sorted(self.blocked_relations)],
                'rethought':[{'actor':a,'evidence':list(b)} for a,b in sorted(self.rethought)],
                'visits':[{'actor':a,'zone':b,'count':n} for (a,b),n in sorted(self.visits.items())],
                'observations':[{'actor':a,'target':b,'count':n} for (a,b),n in sorted(self.observations.items())]}
