"""Opt-in shared execution segments; no story names or alternate clock/chooser."""
from contextlib import contextmanager
from copy import copy, deepcopy
from fractions import Fraction
import hashlib
import json
import math

from engine.activity_bookings import ActivityBookings
from engine.time_kernel import ActionWindow
from engine.work_registry import WorkRegistry, rational, pair


TERMINAL = {'completed', 'canceled', 'interrupted'}
STATES = TERMINAL | {'proposed', 'agreed', 'reserved', 'running'}
VERBS = {'join_activity', 'decline_activity', 'leave_activity'}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def positive(value, name, maximum=1000000):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError('Invalid '+name)
    return value


class JointActivities:
    def __init__(self, raw):
        if (not isinstance(raw, dict) or set(raw)-{'version','definitions','workflows','instances'}
                or type(raw.get('version')) is not int or raw['version'] != 1):
            raise ValueError('Unsupported joint activities contract')
        self.config = deepcopy(raw)
        if not isinstance(raw.get('instances',[]),list) or len(raw.get('instances',[]))>256:
            raise ValueError('Bounded explicit joint proposals required')
        self.work = WorkRegistry(raw.get('workflows', []))
        # Individual workflow stages use the same segment/provider with one role.
        raw = deepcopy(raw)
        unit = raw['definitions'][0]['duration']['unit'] if raw.get('definitions') else 'minute'
        for tid,task in self.work.specs.items():
            if task['execution']['kind'] != 'individual': continue
            definition = 'work_'+tid
            task['execution']['definition'] = definition
            raw['definitions'].append({'id':definition,'label':task.get('label',tid),'provider':'joint_work','revision':1,
                'roles':[{'id':'worker','min':1,'max':1,'requires':{'verbs':['respond'],'facts':[], 'vitalities':['alive','revived']}}],
                'duration':{'unit':unit,'mode':'remaining_work','max_units':task['units']*task['minutes_per_unit']},
                'location_policy':'task_location','resources':[{'key':'workplace','binding':{'kind':'existing_task_resource','task':tid},
                    'quantity':1,'exclusive':True,'settlement':'retain'}],
                'costs':[{'role':'worker','per_unit':{'numerator':0,'denominator':1}}],
                'progress':{'task':tid,'model':'fixed_group_rate','per_unit':{'numerator':1,'denominator':1},
                            'required_roles':['worker'],'preserve_on_interrupt':True},
                'interruption':{'voluntary_exit':'at_boundary','forced_exit':'retain_work_then_close_segment'},
                'effects':{'on_start':None,'on_complete':{'kind':'task_units_receipt','task':tid,'units':task['units']}},
                'bindings_required':['target','target_revision']})
        self.definitions = {}
        if not isinstance(raw.get('definitions'), list) or not 1 <= len(raw['definitions']) <= 32:
            raise ValueError('Bounded joint definitions required')
        for definition in raw['definitions']:
            d = deepcopy(definition); ident = d['id']
            if not isinstance(ident, str) or not ident or ident in self.definitions:
                raise ValueError('Duplicate or invalid joint definition')
            if d['provider'] not in {'joint_move','joint_work'}:
                raise ValueError('Unknown joint provider')
            positive(d['revision'], 'definition revision')
            if not isinstance(d['roles'], list) or not 1 <= len(d['roles']) <= 8:
                raise ValueError('Bounded joint roles required')
            roles = {r['id']: r for r in d['roles']}
            if len(roles) != len(d['roles']):
                raise ValueError('Duplicate joint role')
            for role in roles.values():
                if (type(role['min']) is not int or type(role['max']) is not int
                        or not 0 <= role['min'] <= role['max'] <= 16):
                    raise ValueError('Invalid role cardinality')
                if set(role['requires']) != {'verbs','facts','vitalities'}:
                    raise ValueError('Invalid role eligibility')
            if not 1 <= sum(r['min'] for r in roles.values()) <= 16:
                raise ValueError('A segment needs a required participant')
            duration = d['duration']
            if duration['unit'] not in {'minute','logical_step'} or duration['mode'] not in {'fixed','remaining_work'}:
                raise ValueError('Invalid joint duration')
            positive(duration['units'] if duration['mode']=='fixed' else duration['max_units'], 'duration')
            if d['location_policy'] not in {'together_at_start','task_location','remote'}:
                raise ValueError('Unknown joint location policy')
            if d['interruption']['voluntary_exit'] not in {'at_boundary','at_safe_stop'}:
                raise ValueError('Unknown exit policy')
            if len({r['key'] for r in d['resources']}) != len(d['resources']):
                raise ValueError('Duplicate resource alias')
            for resource in d['resources']:
                positive(resource['quantity'], 'resource quantity')
                if resource['exclusive'] is not True or resource['settlement'] not in {'retain','consume_on_start','consume_on_complete'}:
                    raise ValueError('Unsupported resource settlement')
                if resource['binding']['kind'] not in {'inventory','existing_task_resource'}:
                    raise ValueError('Unknown resource provider')
            seen = set()
            for cost in d['costs']:
                if cost['role'] not in roles or cost['role'] in seen:
                    raise ValueError('Unknown or duplicate cost role')
                rational(cost['per_unit']); seen.add(cost['role'])
            progress = d['progress']
            if progress:
                if progress['task'] not in self.work.specs or progress['model'] not in {'fixed_group_rate','sum_role_rates'}:
                    raise ValueError('Unknown work progress provider')
                if progress['model'] == 'fixed_group_rate':
                    if not rational(progress['per_unit']): raise ValueError('Zero work rate')
                else:
                    if not set(progress['role_rates']) <= set(roles): raise ValueError('Unknown progress role')
                    for rate in progress['role_rates'].values(): rational(rate)
                    if 'cap' in progress: positive_rate = rational(progress['cap']); positive(positive_rate.numerator,'rate cap')
                if not set(progress['required_roles']) <= set(roles) or progress['preserve_on_interrupt'] is not True:
                    raise ValueError('Invalid work progress roles')
                if d['effects']['on_complete'].get('task')!=progress['task'] or d['effects']['on_complete'].get('units')!=self.work.specs[progress['task']]['units']:
                    raise ValueError('Work receipt differs from declared task quantity')
            if d['provider'] == 'joint_work' and progress is None:
                raise ValueError('Work provider needs progress')
            if d['provider'] == 'joint_move' and (progress or d['location_policy'] != 'together_at_start'
                    or not {'origin','destination','transit_zone'} <= set(d['bindings_required'])):
                raise ValueError('Movement requires actual transit bindings')
            expected = ('enter_transit','joint_arrival') if d['provider']=='joint_move' else (None,'task_units_receipt')
            if tuple((d['effects'].get(k) or {}).get('kind') for k in ('on_start','on_complete')) != expected:
                raise ValueError('Unknown joint effect provider')
            self.definitions[ident] = d
        for spec in self.work.specs.values():
            execution = spec['execution']
            if execution['kind'] not in {'individual','joint'}: raise ValueError('Unknown workflow execution kind')
            if execution['kind'] == 'joint' and execution['definition'] not in self.definitions:
                raise ValueError('Unknown work execution definition')
        self.reset()

    def reset(self):
        self.instances = {}; self.history = []; self.bookings = ActivityBookings(self._external)
        self.known_work = {}; self.energy = {}; self.sim = None; self.calendar = None
        self.emitted = 0

    def bind(self, world):
        if self.sim is not None:
            self.work=WorkRegistry(self.config.get('workflows',[]))
            self.reset()
        self.world = world
        if world.slot_execution and world.slot_execution.get('mode') != 'calendar_promises_v2':
            raise ValueError('Joint execution needs ordinary or calendar v2 time')
        for d in self.definitions.values():
            for r in d['roles']:
                if not set(r['requires']['facts']) <= set(world.facts):
                    raise ValueError('Unknown joint role knowledge')
            for res in d['resources']:
                binding = res['binding']
                if binding['kind']=='inventory' and binding['item'] not in world.items:
                    raise ValueError('Unknown joint item')
        for task in self.work.specs.values():
            if task.get('place') and task['place'] not in world.zones:
                raise ValueError('Unknown work place')
            artifact = task.get('completion_artifact')
            if artifact and artifact['fact'] not in world.facts:
                raise ValueError('Unknown work artifact')
            effect = task.get('completion_effect')
            if effect:
                if effect['kind']=='declared_inventory':
                    if effect['item'] not in world.items or not effect.get('owner_binding'):
                        raise ValueError('Unknown work product')
                    positive(effect['quantity'],'work product quantity')
                elif effect['kind'] != 'declared_state' or world.responses is None or effect['key'] not in world.responses.state:
                    raise ValueError('Unknown work completion state')
        self.signature = digest({'config': self.config, 'source':world.joint_source_hash, 'zones': world.zones,
                                 'profiles':{a:{'traits':p.traits,'verbs':sorted(p.verbs),'range':sorted(p.range_zones),
                                    'excluded':p.range_exclude,'commitments':p.commitments,'initial_knowledge':sorted(p.knowledge),
                                    'inventory':p.inventory,'companions':p.companions,'stamina_max':p.stamina_max,
                                    'base':p.base,'initial_zone':p.zone,'initial_stamina':p.stamina}
                                             for a,p in world.subjects.items()},
                                 'people': sorted(world.subjects), 'facts': world.facts,
                                 'conditions': world.concerns.config if world.concerns else None})

    def attach(self, simulation, calendar=None):
        self.sim = simulation; self.calendar = calendar
        self.kernel = simulation.time_kernel
        from engine.joint_planning import JointPlanning
        self.planning = JointPlanning(self)
        if calendar: self.work.bind_store(calendar)
        self.known_work = {a: {} for a in self.world.subjects}
        self.energy = {a: pair(s.stamina) for a,s in self.world.subjects.items()}
        for raw in self.config.get('instances', []):
            entry = deepcopy(raw); agreement = entry.pop('initial_agreement', None)
            entry['initial'] = agreement is not None
            self.propose(entry, authored=True)
            if agreement:
                if agreement['source'] != 'authored_initial_terms': raise ValueError('Unknown initial agreement source')
                ident = entry['id']; inst = self.instances[ident]
                if not set(agreement['accepted_by']) <= set(agreement['known_by']): raise ValueError('Unreceived initial consent')
                inst['known_by'] = sorted(set(agreement['known_by']))
                for actor in agreement['accepted_by']:
                    self.record_consent(ident, actor, inst['terms_hash'], True, authored=True)

    def _external(self, key, start, finish):
        if self.sim is None: return False
        if key.startswith('actor:'):
            actor = key[6:]
            if self.calendar:
                action = self.calendar.busy.get(actor)
                if action and start < action['finish'] and action['start'] < finish: return True
                if self.calendar.facts and self.calendar.facts.actor_busy(actor): return True
            timing = self.world.action_timing
            action = timing.pending.get(actor) if timing else None
            return bool(action and start < action['finish'] and action['start'] < finish)
        if self.calendar and key.startswith('resource:'):
            return any(a['verb']=='work' and 'resource:'+self.calendar.contract['tasks'][a['task_id']]['resource']==key
                       and start < a['finish'] and a['start'] < finish for a in self.calendar.busy.values())
        return False

    def people(self, inst):
        return sorted(a for actors in inst['roles'].values() for a in actors)

    def host(self, inst):
        return inst.get('proposer') or next(a for role in self.definitions[inst['definition_id']]['roles']
                                           for a in inst['roles'][role['id']])

    def _validate_terms(self, entry):
        if not isinstance(entry['id'],str) or not entry['id']: raise ValueError('Invalid segment identifier')
        d = self.definitions[entry['definition_id']]
        if entry['definition_revision'] != d['revision']: raise ValueError('Definition revision mismatch')
        positive(entry['terms_revision'], 'terms revision')
        if set(entry['roles']) != {r['id'] for r in d['roles']}: raise ValueError('Role roster differs')
        people = self.people(entry)
        if len(people) != len(set(people)) or not 1 <= len(people) <= 16 or not set(people) <= set(self.world.subjects):
            raise ValueError('Unknown, duplicate or excessive participant')
        if entry.get('proposer',people[0]) not in people: raise ValueError('Unknown proposal owner')
        for role in d['roles']:
            if not role['min'] <= len(entry['roles'][role['id']]) <= role['max']:
                raise ValueError('Missing required role or excess role count')
        if d['progress'] and any(not entry['roles'][r] for r in d['progress']['required_roles']):
            raise ValueError('Required work role is unfilled')
        if not set(d['bindings_required']) <= set(entry['bindings']): raise ValueError('Missing activity binding')
        if d['progress']:
            if not isinstance(entry['bindings'].get('target'),str) or not entry['bindings']['target']:
                raise ValueError('Work target must be explicit')
            positive(entry['bindings'].get('target_revision'),'target revision')
        if d['duration']['unit'] != self.kernel.axis.unit: raise ValueError('Joint time unit mismatch')
        for key in ('earliest_start','expires_at'): self.kernel.axis.check(entry[key])
        if entry['expires_at'] < entry['earliest_start']: raise ValueError('Activity expires before start')
        if d['provider']=='joint_move' and (any(entry['bindings'][k] not in self.world.zones for k in ('origin','destination','transit_zone'))
                or len({entry['bindings'][k] for k in ('origin','destination','transit_zone')}) != 3):
            raise ValueError('Invalid transit location')
        return d

    def propose(self, entry, *, authored=False, proposer=None):
        if len(self.instances)>=256: raise ValueError('Joint proposal limit reached')
        if entry['id'] in self.instances: raise ValueError('Segment identifiers cannot be reused')
        self._validate_terms(entry)
        inst = deepcopy(entry)
        inst['terms'] = {k:deepcopy(v) for k,v in entry.items() if k not in {'known_by','initial'}}
        known=sorted(set(entry.get('known_by',[])))
        if not set(known)<=set(self.people(entry)): raise ValueError('Unknown activity recipient')
        inst.update(status='proposed', consents={}, received={}, known_by=known, cursor=None, start=None, finish=None,
                    spent={a: pair(0) for a in self.people(entry)}, consumed=[], receipts=[], reason=None)
        inst['terms_hash'] = digest({'definition': self.definitions[entry['definition_id']], 'terms': inst['terms']})
        if not authored:
            if proposer not in self.people(inst): raise ValueError('Proposer is not a participant')
            inst['known_by'] = [a for a in self.people(inst) if self.co_present(a, proposer)]
        self.instances[entry['id']] = inst
        self._record(inst, 'proposed', audience=inst['known_by'])
        return inst['terms_hash']

    def co_present(self, first, second):
        a,b = self.world.subjects[first], self.world.subjects[second]
        if a.zone != b.zone: return False
        one = self.membership(first); two = self.membership(second)
        one=getattr(a,'shared_container',one);two=getattr(b,'shared_container',two)
        if one is not None or two is not None: return one is not None and one==two
        if getattr(a,'in_transit',False) or getattr(b,'in_transit',False): return False
        return True

    def membership(self, actor):
        return next((i['id'] for i in self.instances.values() if i['status']=='running'
                     and self.definitions[i['definition_id']]['provider']=='joint_move' and actor in self.people(i)), None)

    def deliver_terms(self, ident, sender, recipient):
        inst = self.instances[ident]
        if sender not in inst['known_by'] or recipient not in self.people(inst) or not self.co_present(sender,recipient):
            raise ValueError('Joint terms require an actual communication channel')
        inst['known_by'] = sorted(set(inst['known_by']) | {recipient})

    def record_consent(self, ident, actor, terms_hash, accepted, *, authored=False):
        inst = self.instances[ident]
        if (inst['status'] not in {'proposed','agreed','reserved'} or actor not in self.people(inst)
                or actor not in inst['known_by'] or terms_hash != inst['terms_hash'] or type(accepted) is not bool):
            raise ValueError('Invalid, unreceived or stale activity consent')
        if not authored and accepted:
            review = self.review(inst, self.world.subjects[actor])
            if review['excluded']: raise ValueError('Participant objective conflict')
        if inst['consents'].get(actor) is accepted and actor in inst['received']:
            return
        inst['consents'][actor] = accepted
        host=self.host(inst)
        audience=(inst['known_by'] if authored else sorted({actor,host}) if self.co_present(actor,host) else [actor])
        inst['received'][actor]=audience
        if not accepted:
            inst['status']='canceled'; inst['reason']='participant_declined'; self.bookings.release(ident)
        elif all(inst['consents'].get(a) and host in inst['received'].get(a,[]) for a in self.people(inst)):
            inst['status']='agreed'
        self._record(inst, 'consent', audience=audience)

    def _resource(self, inst, resource):
        binding = resource['binding']; kind = binding['kind']
        if kind == 'inventory':
            owner = (inst['roles'][binding['owner_role']][0] if 'owner_role' in binding
                     else inst['bindings'][binding['owner_binding']])
            if owner not in self.people(inst): raise ValueError('Resource owner must consent')
            return f'inventory:{owner}:{binding["item"]}', owner, binding['item']
        task = binding['task']
        if self.calendar:
            return 'resource:'+self.calendar.contract['tasks'][task]['resource'], None, None
        return 'resource:'+self.work.specs[task].get('resource', task), None, None

    def _rate(self, inst):
        p = self.definitions[inst['definition_id']]['progress']
        if not p: return Fraction(0)
        if p['model']=='fixed_group_rate': return rational(p['per_unit'])
        total = sum((len(inst['roles'][r])*rational(rate) for r,rate in p['role_rates'].items()), Fraction(0))
        return min(total, rational(p['cap'])) if 'cap' in p else total

    def _duration(self, inst):
        d = self.definitions[inst['definition_id']]; duration = d['duration']
        if duration['mode']=='fixed': return duration['units']
        rate = self._rate(inst)
        if not rate: raise ValueError('No contributing role')
        value = math.ceil(self.work.remaining(d['progress']['task']) / rate)
        return positive(value, 'remaining work duration', duration['max_units'])

    def projected_effects(self, inst, actor=None, state=None):
        d = self.definitions[inst['definition_id']]
        effects = []
        if d['provider']=='joint_move':
            effects += [{'kind':'zone','actor':a,'value':inst['bindings']['destination']} for a in self.people(inst)]
        else:
            task = d['progress']['task']; spec = self.work.specs[task]
            effects.append({'kind':'task','task':task,'value':spec['units']})
            effect = spec.get('completion_effect')
            if effect:
                if effect['kind']=='declared_state': effects.append({'kind':'state','key':effect['key'],'value':effect['value']})
                else:
                    owner=inst['bindings'][effect['owner_binding']]
                    if actor is None or owner==actor.id:
                        effects.append({'kind':'inventory','actor':owner,'item':effect['item'],
                            'value':(state or {}).get(('inventory',owner,effect['item']),self.world.subjects[owner].inventory.get(effect['item'],0))+effect['quantity']})
            artifact = spec.get('completion_artifact')
            if artifact: effects += [{'kind':'knowledge','actor':a,'fact':artifact['fact'],'value':True} for a in self.people(inst)]
        for resource in d['resources']:
            if resource['settlement']=='retain': continue
            _,owner,item = self._resource(inst,resource)
            if owner and (actor is None or owner==actor.id) and resource['key'] not in inst['consumed']:
                count=(state or {}).get(('inventory',owner,item),self.world.subjects[owner].inventory.get(item,0))
                effects.append({'kind':'inventory','actor':owner,'item':item,'value':max(0,count-resource['quantity'])})
        return effects

    def review(self, inst, actor):
        if self.world.concerns is None: return {'excluded': [], 'forecast': None}
        from engine.concerns import effect_coordinate
        effects = self.projected_effects(inst,actor)
        concerns = self.world.concerns
        before = concerns.assessment(self.world, actor.id)
        after = concerns.assessment(self.world, actor.id, projection={'effects':effects})
        changed = {effect_coordinate(e) for e in effects}
        blocked = []
        priority = max((c['priority'] for c in concerns.commitments_for(self.world,actor.id)
                        if self.world.goal_rules and c['condition'] in self.world.goal_rules.definitions
                        and changed & self.world.goal_rules.affected_coordinates(self.world,c['condition'])), default=0)
        for old,new in zip(before['entries'],after['entries']):
            if new['required'] and ((new['mode']=='maintain' and new['state']=='false')
                    or new.get('objective_prediction',{}).get('failure',{}).get('state')=='true'):
                blocked.append(new['condition'])
            elif old['state']=='true' and new['state']!='true' and new['priority']>=priority:
                blocked.append(new['condition'])
            objective=self.world.goal_rules
            if objective and old['condition'] in objective.definitions and old['state']!='true':
                finish=inst.get('finish') or max(self.kernel.now+1,inst['earliest_start'])+self._duration(inst)
                finish+=self.kernel.axis.unit=='logical_step'
                if finish>objective.definitions[old['condition']]['period']['end'] and (old['required'] or old['priority']>priority):
                    blocked.append(old['condition'])
        return {'excluded': blocked, 'forecast': after}

    def _check(self, inst, *, running=False):
        from engine.phase2 import available_verbs
        d = self.definitions[inst['definition_id']]; bindings = inst['bindings']; actors = self.people(inst)
        for role in d['roles']:
            req = role['requires']
            for aid in inst['roles'][role['id']]:
                actor = self.world.subjects[aid]
                allowed = available_verbs(self.world,actor,turn=self.now_point(),day=max(1,self.now_point()//len(self.world.slots)+1))
                if (actor.vitality not in req['vitalities'] or not set(req['facts']) <= actor.knowledge
                        or not set(req['verbs']) <= set(allowed) or any(not self.world.genre_allows(v) for v in req['verbs'])):
                    raise ValueError('Role or actor unavailable')
                if self.review(inst,actor)['excluded']: raise ValueError('Participant objective conflict')
                rate = next((rational(c['per_unit']) for c in d['costs'] if c['role']==role['id']), Fraction(0))
                need = rate * ((inst['finish']-self.kernel.now) if running else self._duration(inst))
                if rational(actor.stamina) < need: raise ValueError('Insufficient participant stamina')
        if d['provider']=='joint_move':
            if any(self.world.subjects[a].zone != bindings['transit_zone' if running else 'origin'] for a in actors):
                raise ValueError('Participant relocated')
            route = bindings.get('route',[bindings['origin'],bindings['destination']])
            if route[0]!=bindings['origin'] or route[-1]!=bindings['destination']: raise ValueError('Invalid route endpoints')
            for aid in actors:
                actor = self.world.subjects[aid]
                if any(z not in actor.range_zones or z in self.world._excluded_zones(actor) for z in route[1:]):
                    raise ValueError('Participant range excludes route')
                for origin,dest in zip(route,route[1:]):
                    edge = next((r for r in self.world.routes.get(origin,()) if r.destination==dest),None)
                    if edge is None: raise ValueError('No movement route')
                    if any(self.world.concerns.evaluate(self.world,c,actor=None)['state']!='true' for c in edge.requires_conditions):
                        raise ValueError('Route condition unavailable')
                    if edge.requires_item and not actor.has_item(edge.requires_item):
                        access = self.world.items[edge.requires_item].get('access',{}) or {}
                        if not (access.get('mode')=='owner' and 'companions' in access.get('allow',[])
                                and any(self.world.subjects[o].has_item(edge.requires_item)
                                        and aid in (self.world.subjects[o].companions or []) for o in actors)):
                            raise ValueError('Vessel access unavailable')
        elif d['location_policy']!='remote':
            task = self.work.specs[d['progress']['task']]
            place = (self.calendar.contract['tasks'][task['id']]['place'] if self.calendar else task.get('place',bindings.get('place')))
            if place not in self.world.zones or any(self.world.subjects[a].zone!=place for a in actors):
                raise ValueError('Work location unavailable')
            if any(not self.co_present(actors[0],a) for a in actors): raise ValueError('Work participants are in separate containers')
            if self.calendar:
                from engine.task_windows import permits
                tid = task['id']; native = self.calendar.contract['tasks'][tid]
                if not self.calendar.facilities.get(place,True): raise ValueError('Work facility closed')
                if any(not set(native['roles']) & set(self.calendar.contract['roles'][a]) for a in actors):
                    raise ValueError('Calendar work role unavailable')
                if not permits(self.calendar.task_windows,tid,self.kernel.now,inst['finish'] if running else self.kernel.now+self._duration(inst)):
                    raise ValueError('Outside task window')
                reserve=max(0,native['units']-self.work.read(tid)['reserved_units'])
                if any(self.calendar.ledger['materials'][item]<reserve*qty for item,qty in native['materials_per_unit'].items()):
                    raise ValueError('Calendar work materials unavailable')
        if d['progress']:
            tid = d['progress']['task']
            if not self.work.ready(tid): raise ValueError('Work dependency incomplete')
            self.work.validate_scope(tid,bindings)
            effect=self.work.specs[tid].get('completion_effect')
            if effect and effect['kind']=='declared_inventory' and bindings.get(effect['owner_binding']) not in actors:
                raise ValueError('Product owner must be a participant')
        keys = ['actor:'+a for a in actors]
        for resource in d['resources']:
            key,owner,item = self._resource(inst,resource)
            keys.append(key)
            capacity = resource.get('capacity')
            if capacity and len(actors) > capacity['maximum']: raise ValueError('Joint capacity exceeded')
            if owner and resource['key'] not in inst['consumed'] and not self.world.subjects[owner].has_item(item,resource['quantity']):
                raise ValueError('Reserved resource lost')
        if len(set(keys))!=len(keys): raise ValueError('Resource aliases overlap')
        return keys

    def now_point(self):
        return self.kernel.now + (self.kernel.axis.unit=='logical_step')

    def reserve(self, ident, *, initial=False):
        inst = self.instances[ident]
        if inst['status']!='agreed': return False
        if inst.get('attempted_at')==self.kernel.now: return False
        inst['attempted_at']=self.kernel.now
        start = max(inst['earliest_start'], self.kernel.now + (0 if initial else 1))
        if start > inst['expires_at']:
            inst.update(status='canceled',reason='start_expired'); self._record(inst,'canceled'); return False
        if self.world.concerns:
            for actor in self.people(inst):
                mine=self.review(inst,self.world.subjects[actor])
                def preference(review):
                    scores={v['priority']:v['score'] for v in (review.get('forecast') or {}).get('vector',[])}
                    return tuple(scores.get(p,0) for p in range(10,0,-1))
                for other in self.instances.values():
                    if other['id']==ident or other['status']!='agreed' or actor not in self.people(other) or actor not in other['known_by']: continue
                    other_start=max(other['earliest_start'],self.kernel.now+(not other.get('initial',False)))
                    try: overlaps=start<other_start+self._duration(other) and other_start<start+self._duration(inst)
                    except ValueError: continue
                    review=self.review(other,self.world.subjects[actor])
                    if overlaps and not review['excluded'] and preference(review)>preference(mine):
                        if inst['reason']!='participant_prefers_other_activity':
                            inst['reason']='participant_prefers_other_activity';self._record(inst,'reservation_failed')
                        return False
        try:
            finish = self.kernel.window(self._duration(inst),unit=self.definitions[inst['definition_id']]['duration']['unit'],start=start).finish
            keys = self._check(inst)
        except ValueError as exc:
            if inst['reason']!=str(exc):
                inst['reason'] = str(exc); self._record(inst,'reservation_failed')
            return False
        if not self.bookings.reserve_all(ident,start,finish,keys):
            inst['reason']='booking_conflict'; self._record(inst,'reservation_failed'); return False
        inst.update(status='reserved',start=start,finish=finish,reason=None)
        self._record(inst,'reserved'); return True

    def _consume(self, inst, when):
        for res in self.definitions[inst['definition_id']]['resources']:
            if res['settlement'] != when or res['key'] in inst['consumed']: continue
            _,owner,item = self._resource(inst,res)
            if owner is None or not self.world.subjects[owner].has_item(item,res['quantity']):
                raise ValueError('Missing consumable resource')
            self.world.subjects[owner].inventory[item] -= res['quantity']
            inst['consumed'].append(res['key'])

    @contextmanager
    def transaction(self):
        backup = self.snapshot()
        people = {a:deepcopy(p.__dict__) for a,p in self.world.subjects.items()}
        state = deepcopy(self.world.responses.state) if self.world.responses else None
        calendar = deepcopy(self.calendar.ledger) if self.calendar else None
        energy = deepcopy(self.calendar.energy) if self.calendar else None
        try:
            yield
        except BaseException:
            for a,p in people.items(): self.world.subjects[a].__dict__.clear(); self.world.subjects[a].__dict__.update(p)
            if state is not None: self.world.responses.state = state
            if self.calendar: self.calendar.ledger=calendar; self.calendar.energy=energy; self.work.tasks=self.calendar.ledger['tasks']
            self._restore_unchecked(backup)
            raise

    def start_due(self):
        with self.transaction():
            for ident,inst in sorted(self.instances.items()):
                if inst['status']=='agreed': self.reserve(ident,initial=inst.get('initial',False))
                if inst['status']!='reserved' or inst['start']!=self.kernel.now: continue
                try:
                    keys = self._check(inst)
                    if self._duration(inst)!=inst['finish']-inst['start']:
                        raise ValueError('Work changed after agreement')
                    if keys != self.bookings.entries[ident]['keys'] and sorted(keys)!=self.bookings.entries[ident]['keys']:
                        raise ValueError('Resource binding changed')
                    if any(self._external(k,inst['start'],inst['finish']) for k in keys): raise ValueError('External booking conflict')
                except ValueError as exc:
                    inst.update(status='canceled',reason=str(exc)); self.bookings.release(ident); self._record(inst,'canceled'); continue
                self._consume(inst,'consume_on_start')
                d=self.definitions[inst['definition_id']]
                if self.calendar and d['progress']:
                    tid=d['progress']['task']; native=self.calendar.contract['tasks'][tid]
                    reserve=max(0,native['units']-self.work.read(tid)['reserved_units'])
                    for item,qty in native['materials_per_unit'].items(): self.calendar.ledger['materials'][item]-=reserve*qty
                    self.work.read(tid)['reserved_units']+=reserve
                inst.update(status='running',cursor=inst['start'])
                for aid in self.people(inst): self.energy[aid]=pair(self.world.subjects[aid].stamina)
                if self.definitions[inst['definition_id']]['provider']=='joint_move':
                    for aid in self.people(inst):
                        self.world.subjects[aid].zone=inst['bindings']['transit_zone']; self.world.subjects[aid].in_transit=True
                        self.world.subjects[aid].shared_container=inst['id']
                self._record(inst,'started')

    def _settle(self, inst, at):
        elapsed = ActionWindow(inst['start'],inst['finish'],self.kernel.axis.unit).elapsed(inst['cursor'],at)
        if not elapsed: return
        d = self.definitions[inst['definition_id']]
        usable=Fraction(elapsed)
        for cost in d['costs']:
            rate=rational(cost['per_unit'])
            if rate:
                for aid in inst['roles'][cost['role']]: usable=min(usable,rational(self.world.subjects[aid].stamina)/rate)
        inst['funding_failed']=usable<elapsed
        for cost in d['costs']:
            for aid in inst['roles'][cost['role']]:
                person = self.world.subjects[aid]; due=usable*rational(cost['per_unit'])
                exact=rational(self.energy[aid])
                actual = exact if abs(float(exact)-person.stamina)<1e-10 else rational(person.stamina)
                remaining = max(Fraction(0),actual-due)
                self.energy[aid]=pair(remaining); person.stamina=float(remaining)
                if self.calendar: self.calendar.energy[aid]=pair(remaining)
                inst['spent'][aid]=pair(rational(inst['spent'][aid])+min(actual,due))
        if d['progress']:
            tid=d['progress']['task']
            receipts=self.work.commit_progress(tid,inst['id'],min(at,inst['finish']),usable*self._rate(inst),self.people(inst),inst['bindings'])
            inst['receipts'] += [r['id'] for r in receipts]
            for aid in self.people(inst): self.known_work[aid][tid]=self.work.read(tid)['units']
            if self.calendar:
                for receipt in receipts:
                    # One physical unit with all contributors, never N units.
                    for aid in self.people(inst):
                        event={'id':receipt['id']+':'+aid,'actor':aid,'task_id':tid,'units':1,'at':receipt['at'],
                               'place':self.calendar.contract['tasks'][tid]['place']}
                        self.calendar.ledger['completion_events'].append(event)
                    row=self.calendar.emit(receipt['id'],'work_unit_completed',self.people(inst)[0],
                        task_id=tid,units=1,participants=self.people(inst),task_progress=deepcopy(self.work.read(tid)))
                    self.calendar.observe(row,self.people(inst))
        inst['cursor']=min(at,inst['finish'])

    def _finish_work(self, inst):
        tid=self.definitions[inst['definition_id']]['progress']['task']
        if not self.work.commit_completion(tid): return
        task=self.work.specs[tid]
        artifact=task.get('completion_artifact')
        if artifact:
            for aid in self.people(inst): self.world.subjects[aid].knowledge.add(artifact['fact'])
        effect=task.get('completion_effect')
        if effect:
            if effect['kind']=='declared_state': self.world.responses.state[effect['key']]=effect['value']
            else:
                owner=self.world.subjects[inst['bindings'][effect['owner_binding']]]
                owner.inventory[effect['item']]=owner.inventory.get(effect['item'],0)+effect['quantity']

    def advance(self):
        with self.transaction():
            for inst in sorted(self.instances.values(),key=lambda i:i['id']):
                if inst['status']!='running': continue
                self._settle(inst,self.kernel.now)
                try:
                    if inst.get('funding_failed'): raise ValueError('Insufficient participant stamina during activity')
                    self._check(inst,running=True)
                except ValueError as exc:
                    self.interrupt(inst['id'],str(exc),settle=False); continue
                if self.kernel.now >= inst['finish']:
                    self._consume(inst,'consume_on_complete')
                    d=self.definitions[inst['definition_id']]
                    if d['provider']=='joint_move':
                        for aid in self.people(inst):
                            self.world.subjects[aid].zone=inst['bindings']['destination']; self.world.subjects[aid].in_transit=False
                            self.world.subjects[aid].shared_container=None
                    else: self._finish_work(inst)
                    inst.update(status='completed',reason=None); self.bookings.release(inst['id']); self._record(inst,'completed')

    def interrupt(self, ident, reason, *, settle=True, actor=None):
        inst=self.instances[ident]
        if inst['status']!='running': return False
        if actor is not None and (actor not in self.people(inst) or self.definitions[inst['definition_id']]['interruption']['voluntary_exit']!='at_boundary'):
            return False
        if settle: self._settle(inst,self.kernel.now)
        inst.update(status='interrupted',reason=reason)
        for aid in self.people(inst): self.world.subjects[aid].in_transit=False
        self.bookings.release(ident); self._record(inst,'interrupted'); return True

    def recheck_running(self):
        for inst in self.instances.values():
            if inst['status']=='running':
                try: self._check(inst,running=True)
                except ValueError as exc: self.interrupt(inst['id'],str(exc))

    def _record(self, inst, phase, audience=None):
        record={k:deepcopy(inst[k]) for k in ('id','definition_id','terms_revision','terms_hash','roles','bindings',
                 'status','start','finish','spent','receipts','reason')}
        record.update(phase=phase,at=self.kernel.now if self.sim else None,unit=self.kernel.axis.unit if self.sim else None,
                      participants=self.people(inst),audience=self.people(inst) if audience is None else sorted(audience))
        definition=self.definitions[inst['definition_id']]
        record.update(label=definition.get('label',definition['id']),
                      role_labels={r['id']:r.get('label',r['id']) for r in definition['roles']},
                      accepted_by=sorted(a for a,v in inst['consents'].items() if v),
                      awaiting=[a for a in self.people(inst) if a not in inst['consents']])
        for key in ('start','finish'):
            if inst[key] is not None:
                if self.calendar:
                    from engine.calendar_v2 import time_label
                    value=time_label(self.calendar.calendar,inst[key])
                    record[key+'_label']={'day':inst[key]//1440+1,'slot':value['date']+' '+value['time']}
                else: record[key+'_label']=self.kernel.axis.label(inst[key])
        if self.definitions[inst['definition_id']]['progress']:
            task=self.definitions[inst['definition_id']]['progress']['task']
            record.update(task=task,progress=deepcopy(self.work.read(task)),remaining=pair(self.work.remaining(task)))
        self.history.append(record)

    def flush(self, writer=None):
        while self.emitted < len(self.history):
            index=self.emitted; event=self.history[index]; self.emitted+=1
            if self.calendar:
                row=self.calendar.emit('joint:'+str(index),'joint_'+event['phase'],event['participants'][0],joint_activity=deepcopy(event))
                self.calendar.observe(row,event['audience'])
            elif writer is not None:
                writer.write(self.sim._event_row(verb='joint_'+event['phase'],subject=event['participants'][0],
                             delta={},details={'joint_activity':deepcopy(event)}))

    def points(self):
        result=set()
        for inst in self.instances.values():
            if inst['status']=='agreed':
                point=max(inst['earliest_start'],self.kernel.now+(not inst.get('initial',False)))
                if point>self.kernel.now or inst.get('attempted_at')!=self.kernel.now: result.add(point)
                if inst['expires_at']>self.kernel.now: result.add(inst['expires_at'])
            elif inst['status']=='reserved': result.add(inst['start'])
            elif inst['status']=='running': result.add(inst['finish'])
            if inst['status']=='running' and self.definitions[inst['definition_id']]['progress']:
                tid=self.definitions[inst['definition_id']]['progress']['task'];spec=self.work.specs[tid]
                effort=rational(self.work.read(tid)['effort_minutes']);rate=self._rate(inst)
                if rate and self.work.remaining(tid):
                    result.add(min(inst['finish'],self.kernel.now+math.ceil((spec['minutes_per_unit']-effort%spec['minutes_per_unit'])/rate)))
        return {p for p in result if self.kernel.now<=p<=self.kernel.axis.end}

    def snapshot(self):
        return {'version':1,'signature':getattr(self,'signature',None),'now':self.kernel.now,
                'instances':deepcopy(self.instances),'bookings':self.bookings.snapshot(),'work':self.work.snapshot(),
                'known_work':deepcopy(self.known_work),'energy':deepcopy(self.energy),
                'history':deepcopy(self.history),'emitted':self.emitted,
                'physical':None if self.calendar else {'people':{a:{'zone':p.zone,'stamina':p.stamina,
                    'inventory':deepcopy(p.inventory),'knowledge':sorted(p.knowledge),'vitality':p.vitality,
                    'in_transit':getattr(p,'in_transit',False)} for a,p in self.world.subjects.items()},
                    'state':deepcopy(self.world.responses.state) if self.world.responses else None}}

    def validate_snapshot(self, state, *, at=None):
        if set(state)!=set(self.snapshot()) or state['signature']!=self.signature or state['version']!=1 or state['now']!=(self.kernel.now if at is None else at):
            raise ValueError('Joint checkpoint version or time differs')
        self.work.validate(state['work']); staged=self.bookings.validate(state['bookings'])
        if self.calendar:
            if state['physical'] is not None: raise ValueError('Calendar owns the physical checkpoint')
        else:
            physical=state['physical']
            if not isinstance(physical,dict) or set(physical)!= {'people','state'} or set(physical['people'])!=set(self.world.subjects):
                raise ValueError('Missing physical activity checkpoint')
            for aid,person in physical['people'].items():
                if set(person)!= {'zone','stamina','inventory','knowledge','vitality','in_transit'} or person['zone'] not in self.world.zones:
                    raise ValueError('Invalid physical actor checkpoint')
                rational(person['stamina'])
                if (person['stamina']>self.world.subjects[aid].stamina_max or type(person['in_transit']) is not bool
                        or person['vitality'] not in {'alive','revived','downed','dead'}
                        or any(type(n) is not int or n<0 for n in person['inventory'].values())):
                    raise ValueError('Invalid physical actor state')
            if self.world.responses and (not isinstance(physical['state'],dict) or set(physical['state'])!=set(self.world.responses.state)):
                raise ValueError('Missing completion state checkpoint')
        if set(state['energy'])!=set(self.world.subjects) or set(state['known_work'])!=set(self.world.subjects): raise ValueError('Missing participant checkpoint')
        expected=set()
        for ident,inst in state['instances'].items():
            self._validate_terms(inst)
            if inst['terms_hash']!=digest({'definition':self.definitions[inst['definition_id']],'terms':inst['terms']}):
                raise ValueError('Restored agreement terms changed')
            if any(inst.get(k)!=v for k,v in inst['terms'].items()): raise ValueError('Restored roster or terms changed')
            if ident!=inst['id'] or inst['status'] not in STATES: raise ValueError('Invalid joint checkpoint state')
            if inst['status'] in {'reserved','running'}:
                expected.add(ident)
                if ident not in staged or (staged[ident]['start'],staged[ident]['finish'])!=(inst['start'],inst['finish']):
                    raise ValueError('Missing joint reservation')
                if {k for k in staged[ident]['keys'] if k.startswith('actor:')} != {'actor:'+a for a in self.people(inst)}:
                    raise ValueError('Restored participant pointers differ')
                if not all(inst['consents'].get(a) and a in inst['known_by'] and self.host(inst) in inst['received'].get(a,[])
                           for a in self.people(inst)): raise ValueError('Restored consent missing')
                expected_keys=['actor:'+a for a in self.people(inst)] + [self._resource(inst,r)[0] for r in self.definitions[inst['definition_id']]['resources']]
                if staged[ident]['keys']!=sorted(expected_keys): raise ValueError('Restored resource pointer differs')
            if inst['status']=='running' and not inst['start'] <= inst['cursor'] <= min(inst['finish'],state['now']):
                raise ValueError('Invalid joint progress cursor')
            if inst['cursor'] is not None:
                for cost in self.definitions[inst['definition_id']]['costs']:
                    for aid in inst['roles'][cost['role']]:
                        if rational(inst['spent'][aid])>rational(cost['per_unit'])*(inst['cursor']-inst['start']):
                            raise ValueError('Invalid restored participant cost')
            if any(r not in state['work']['receipts'] for r in inst['receipts']): raise ValueError('Missing shared progress receipt')
        if set(staged)!=expected: raise ValueError('Orphan joint reservation')
        if not 0 <= state['emitted'] <= len(state['history']): raise ValueError('Invalid activity log cursor')

    def _restore_unchecked(self, state):
        self.instances=deepcopy(state['instances']); self.bookings.entries=deepcopy(state['bookings']); self.work.restore(state['work'])
        self.known_work=deepcopy(state['known_work']); self.energy=deepcopy(state['energy'])
        self.history=deepcopy(state['history']); self.emitted=state['emitted']
        if not self.calendar:
            for actor,data in state['physical']['people'].items():
                person=self.world.subjects[actor]
                for key in ('zone','stamina','vitality','in_transit'):setattr(person,key,data[key])
                person.inventory=deepcopy(data['inventory']);person.knowledge=set(data['knowledge'])
            if self.world.responses:self.world.responses.state=deepcopy(state['physical']['state'])
        for actor,person in self.world.subjects.items():
            person.shared_container=None
            for inst in self.instances.values():
                if (actor in self.people(inst) and self.definitions[inst['definition_id']]['provider']=='joint_move'
                        and inst['status'] in {'running','interrupted'} and person.zone==inst['bindings']['transit_zone']):
                    person.shared_container=inst['id']

    def restore(self, state):
        self.kernel.axis.check(state['now'])
        self.validate_snapshot(state,at=state['now'])
        self.kernel.restore_position(state['now']); self._restore_unchecked(state)
