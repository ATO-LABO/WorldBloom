"""Native calendar operators for the shared bounded GoalPlanner search.

No new chooser or scenario identifiers. Forecasts read actor-received evidence;
native execution and the PromiseRegistry remain authoritative.
"""
from copy import deepcopy
import json
import hashlib
from pathlib import Path

from engine.actions import Action
from engine.concerns import coordinates, effect_coordinate
from engine.calendar_checkpoint import digest
from engine.calendar_v2 import time_label
from engine.promises_v2 import TERMINAL


def aux(*parts):
    return ('state', json.dumps(parts, ensure_ascii=False, separators=(',', ':')))


def assignment(coord, value):
    kind, *args = coord
    result = {'kind': kind, 'value': value}
    if kind == 'state': result['key'] = args[0]
    elif kind == 'task': result['task'] = args[0]
    elif kind == 'promise': result.update(promise=args[0], version=int(args[1]))
    else: result['actor'] = args[0]
    return result


class CalendarPlanning:
    def __init__(self, runtime):
        self.runtime = runtime
        self.world = runtime.world
        self.planner = self.world.planner
        self.last_choice = {}
        self.last_assessment = {}
        self.world.planning_domain = self
        identity = {'adapter': 'shared_goal_planner', 'schema_version': 1,
                    'source_hash': digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                           for p in sorted(Path(__file__).parent.glob('*.py'))}),
                    'config_hash': digest({'planning': self.planner.config, 'goals': self.world.goal_rules.config,
                                           'concerns': self.world.concerns.config})}
        self.identity = identity

    def known_promise(self, actor, ident, version):
        rt = self.runtime
        key = f'{ident}:{version}'
        if key not in rt.known_terms[actor]: return None
        record = rt.registry.get(ident, version)
        if rt.known_terms[actor][key] != record['terms_hash']: return None
        known = [r['details']['promise_transition'] for r in rt.rows
                 if r['id'] in rt.observed[actor] and 'promise_transition' in r['details']
                 and r['details']['promise_transition']['promise_id'] == ident
                 and r['details']['promise_transition']['version'] == version]
        if not known: return None
        result = {'terms': record['terms'], **deepcopy(known[-1])}
        # A registry transition is emitted once per status. Later work-unit
        # evidence can update progress without a second "partial" transition.
        for row in rt.rows:
            if row['id'] not in rt.observed[actor] or row['absolute_minute'] <= result['at']: continue
            detail = row['details']; choice = detail.get('choice', {})
            if row['verb'] != 'work_unit_completed' and not (row['verb']=='action_completed' and choice.get('verb')=='deliver'): continue
            task = detail.get('task_id')
            if not task or not result['terms']['start'] <= row['absolute_minute'] <= (result['terms']['grace_until'] or result['terms']['deadline']): continue
            if rt.contract['tasks'][task]['place'] != result['terms']['place']: continue
            for progress in result['progress']:
                if (progress['task_id']==task and progress['contribution_by']==row['subject']
                        and (progress['delivered_to'] is None or progress['delivered_to']==detail.get('delivered_to'))):
                    progress['completed_units'] = min(progress['required_units'], progress['completed_units']+detail.get('units',1))
        return result

    def read(self, coord, actor=None):
        kind, ident, *tail = coord
        rt = self.runtime
        if kind == 'task':
            entry = rt.ledger['tasks'].get(ident) if actor is None else rt.known_tasks[actor].get(ident)
            if actor is not None and rt.people[actor].zone == rt.contract['tasks'][ident]['place']:
                entry = rt.ledger['tasks'][ident]
            return None if entry is None else entry['units']
        if actor is None:
            try: return rt.registry.get(ident, int(tail[0]))['status']
            except ValueError: return 'absent'
        record = self.known_promise(actor, ident, int(tail[0]))
        return None if record is None else record['status']

    def visible(self, actor):
        rt = self.runtime
        state = {('zone', actor.id): actor.zone}
        for who, person in rt.people.items():
            if person.zone == actor.zone and person.zone != '移動中': state[('zone', who)] = person.zone
            elif (actor.zone != '移動中' and self.planner.memory.get(actor.id, {}).get('known', {}).get(('zone', who)) == actor.zone):
                state[('zone', who)] = None  # Observed absence does not reveal a remote destination.
        known_tasks = {**rt.known_tasks[actor.id], **{task:entry for task,entry in rt.ledger['tasks'].items()
                      if actor.zone == rt.contract['tasks'][task]['place']}}
        for task, entry in known_tasks.items():
            state[('task', task)] = entry['units']
            state[aux('effort', task)] = entry['effort_minutes']
            state[aux('reserved', task)] = entry['reserved_units']
        # Materials are a declared shared stock. They are checked again at admission.
        for item, count in rt.ledger['materials'].items(): state[aux('material', item)] = count
        for definition in self.world.concerns.conditions.values():
            for coord in coordinates(definition['test']):
                if coord[0] == 'promise':
                    record = self.known_promise(actor.id, coord[1], int(coord[2]))
                    if record:
                        state[coord] = record['status']
                        for i, progress in enumerate(record['progress']):
                            state[aux('promise_progress', coord[1], coord[2], i)] = progress['completed_units']
                elif self.world.concerns._visible(self.world, actor.id, coord, definition['visibility'] == 'public'):
                    value = self.world.concerns._read(self.world, coord, actor.id)
                    if value is not None: state[coord] = value
        return state

    def _relevant(self, actor, root):
        from engine.objectives import references
        tasks = set()
        for cid in references(self.world.goal_rules.definitions[root]['success']):
            test = self.world.concerns.conditions[cid]['test']
            if test['kind'] == 'task': tasks.add(test['task'])
            if test['kind'] == 'promise':
                record = self.known_promise(actor.id, test['promise'], test['version'])
                if record and record['status'] not in TERMINAL | {'offered'}:
                    tasks.update(r['task_id'] for r in record['terms']['requirements'] if r['contribution_by'] == actor.id)
        for _ in range(len(self.runtime.contract['tasks'])):
            before = set(tasks)
            for task in before: tasks.update(self.runtime.contract['tasks'][task]['dependencies'])
            if tasks == before: break
        return tasks

    def _promise_effects(self, actor, state, effects, finish, task=None, units=0, target=None):
        future = {**state, **{effect_coordinate(e): e['value'] for e in effects}}
        result = []
        for definition in self.world.concerns.conditions.values():
            test = definition['test']
            if test['kind'] != 'promise': continue
            record = self.known_promise(actor.id, test['promise'], test['version'])
            if not record or record['status'] in TERMINAL | {'offered'}: continue
            terms = record['terms']; coord = coordinates(test)[0]
            if not terms['start'] <= finish <= (terms['grace_until'] or terms['deadline']): continue
            complete = True
            for i, req in enumerate(terms['requirements']):
                key = aux('promise_progress', coord[1], coord[2], i)
                have = state.get(key, 0)
                if (task == req['task_id'] and req['contribution_by'] == actor.id
                        and self.runtime.contract['tasks'][task]['place'] == terms['place']
                        and (req['delivered_to'] is None or req['delivered_to'] == target)):
                    have += units
                    result.append(assignment(key, have))
                complete &= have >= req['units']
            if complete: result.append(assignment(coord, 'fulfilled'))
        return result

    def steps(self, actor, root, state, turn, elapsed, spent):
        from engine.phase2 import available_verbs
        rt = self.runtime; point = turn + elapsed
        allowed = available_verbs(self.world, actor, turn=point, day=point//1440+1)
        zone = state[('zone', actor.id)]
        energy = rt.energy_rules
        stamina = min(actor.stamina_max, actor.stamina-spent)
        def step(verb, args, label, duration, cost, effects, meta, assumptions=None, recovery=0):
            if point+duration > rt.end or cost > stamina+1e-9: return
            extra = self._promise_effects(actor, state, effects, point+duration)
            yield {'verb': verb, 'args': args, 'label': label, 'duration': duration,
                   'completion_delay': duration, 'cost': cost, 'recovery': recovery,
                   'effects': effects+extra, 'assumptions': assumptions or [], 'meta': meta}
        if 'move' in allowed and self.world.genre_allows('move'):
            for dest, minutes in sorted(rt.contract['travel_minutes'].get(zone, {}).items()):
                if dest not in actor.range_zones or dest in self.world._excluded_zones(actor): continue
                # Predict only authored facility hours, never future hidden events.
                if not self._open(dest, point+minutes): continue
                yield from step('move', [dest], dest+'へ移動', minutes, minutes*energy['move_per_minute'],
                                [assignment(('zone', actor.id), dest)], {'to': dest})
        if 'rest' in allowed and self.world.genre_allows('rest'):
            recovery = min(actor.stamina_max-stamina, energy['rest_minutes']*energy['rest_per_minute'])
            if recovery > 1e-9:
                yield from step('rest', [], '休息する', energy['rest_minutes'], 0, [],
                                {'minutes': energy['rest_minutes']}, recovery=recovery)
        # Native task roles authorize work; the character still controls whether it works.
        if 'respond' not in allowed or not self.world.genre_allows('respond'): return
        for task in sorted(self._relevant(actor, root)):
            spec = rt.contract['tasks'][task]
            if (spec['place'] != zone or not set(spec['roles']) & set(rt.contract['roles'][actor.id])
                    or ('task', task) not in state): continue
            if any(state.get(('task', dep), -1) < rt.contract['tasks'][dep]['units'] for dep in spec['dependencies']): continue
            remaining = spec['units']*spec['minutes_per_unit']-state.get(aux('effort', task), 0)
            if remaining <= 0: continue
            if spec['operation'] == 'deliver':
                targets = sorted(who for who in rt.people if who != actor.id and state.get(('zone', who)) == zone)
                for target in targets:
                    effects = [assignment(('task', task), state[('task', task)]+1),
                               assignment(aux('effort', task), state.get(aux('effort', task), 0)+spec['minutes_per_unit'])]
                    effects += self._promise_effects(actor, state, effects, point+spec['minutes_per_unit'], task, 1, target)
                    yield from step('deliver', [task, target], spec['label'], spec['minutes_per_unit'], 0, effects,
                                    {'task_id': task, 'target': target}, [{'consent': target, 'requires_presence': True}])
                continue
            # Work one unit at a time: other tasks and recovery may be interleaved.
            duration = min(remaining, spec['minutes_per_unit']-state.get(aux('effort', task), 0)%spec['minutes_per_unit'])
            if not self._open(zone, point, point+duration): continue
            reserve = max(0, state[('task', task)]+1-state.get(aux('reserved', task),0))
            if any(state.get(aux('material', item), 0) < reserve*n for item,n in spec['materials_per_unit'].items()): continue
            effects = [assignment(('task', task), state[('task', task)]+1),
                       assignment(aux('effort', task), state.get(aux('effort', task), 0)+duration),
                       assignment(aux('reserved', task), state.get(aux('reserved', task),0)+reserve)]
            effects += [assignment(aux('material', item), state[aux('material', item)]-reserve*n) for item,n in spec['materials_per_unit'].items()]
            effects += self._promise_effects(actor, state, effects, point+duration, task, 1)
            yield from step('work', [task], spec['label'], duration, duration*energy['work_per_minute'], effects,
                            {'task_id': task, 'minutes': duration})

    def _open(self, zone, at, finish=None):
        hours = self.runtime.contract['facility_hours'].get(zone)
        if hours is None: return True
        label = time_label(self.runtime.calendar, at)
        if not hours['opens'] <= label['time'] < hours['closes']: return False
        return finish is None or (finish//1440 == at//1440 and time_label(self.runtime.calendar, finish)['time'] <= hours['closes'])

    def choose(self):
        rt = self.runtime
        for who in sorted(self.planner.config['actors']):
            actor = rt.people[who]
            if who in rt.busy or self.last_choice.get(who) == rt.now or actor.vitality not in {'alive','revived'}: continue
            self.last_choice[who] = rt.now
            choices = self.planner.candidates(self.world, actor, rt.now, rt.now//1440+1)
            feasible = [(action, weight) for action,weight in choices if action.meta['goal_plan']['steps']]
            if not feasible:
                if not choices: continue
                plan = deepcopy(choices[0][0].meta['goal_plan'])
                fingerprint = digest({'root':plan['root'],'status':plan['status'],
                                      'view':self.planner.effects(self.planner.view(self.world,actor))})
                if self.last_assessment.get(who) == fingerprint: continue
                self.last_assessment[who] = fingerprint
                self.planner.before_action(self.world,actor)
                row = rt.emit(f'assessment:{who}:{rt.now}', 'planning_assessment', who,
                              selection_mechanism='bounded_goal_assessment', action_started=False,assessment_hash=fingerprint)
                self.planner.after_action(self.world,actor,Action('rest',meta={'goal_plan':plan}),
                                          'no_action',row['details'],rt.now)
                row['details']['planning_memory'] = self.planner.snapshot()['actors'][who]
                continue
            action, _ = max(feasible, key=lambda entry: entry[1])
            self.planner.before_action(self.world, actor)
            plan = action.meta['goal_plan']; first = plan['steps'][0]
            ident = f'planned:{who}:{rt.now}'
            command = {'id': ident, 'actor': who, 'at': rt.now, 'verb': first['verb'], **first['meta']}
            rt.admit_adapter_command(command, proposal_id=ident, candidate_id=digest(plan))
            decision = next(row for row in rt.rows if row['id'] == 'decision:'+ident)
            detail = decision['details']
            self.planner.after_action(self.world, actor, action, 'started' if who in rt.busy else 'failed', detail, rt.now)
            detail['selection_mechanism'] = 'bounded_goal_search'
            detail['calculation'] = {'priority': plan['priority'], 'duration_minutes': first['duration']}
            detail['planning']['time_unit'] = 'minute'
            detail['planning']['deadline_label'] = time_label(rt.calendar, self.world.goal_rules.definitions[plan['root']]['period']['end'])
            detail['planning_memory'] = self.planner.snapshot()['actors'][who]
            detail['activity'] = self.activity(command, first, 'started', rt.now, rt.now+first['duration'])
            detail['activity_result'] = 'started'

    def activity(self, command, step, phase, start, finish):
        rt = self.runtime
        def label(at):
            value = time_label(rt.calendar, at)
            return {'day':at//1440+1, 'slot':value['date']+' '+value['time']}
        duration = max(1, finish-start)
        return {'id':command['id'], 'actor':command['actor'], 'verb':step['verb'], 'args':deepcopy(step['args']),
                'phase':phase, 'unit':'minute', 'start':start, 'finish':finish,
                'start_label':label(start),'finish_label':label(finish), 'cost':step['cost'],
                'spent':step['cost']*min(1,max(0,rt.now-start)/duration)}

    def completed(self, action, details):
        selected = next((r for r in self.runtime.rows if r['id']=='decision:'+action['id']
                         and r['details'].get('selection_mechanism')=='bounded_goal_search'),None)
        if selected is None: return
        plan = selected['details']['planning']; step = plan['steps'][0]
        phase = 'failed' if 'failure_reason' in details else 'completed'
        details['planning'] = deepcopy(plan)
        details['activity'] = self.activity(action,step,phase,action['start'],action['finish'])
        if action['verb']=='rest' and phase=='completed': details['activity']['recovered'] = details['stamina_delta']
        details['activity_result'] = ('failed' if phase=='failed' else 'responded' if action['verb']=='work'
                                      else 'moved' if action['verb']=='move' else 'rested' if action['verb']=='rest' else 'delivered')

    def validate_snapshot(self, state, rows, now):
        if not isinstance(state,dict) or set(state) != {'last_choice','last_assessment','planner','objectives'}:
            raise ValueError('Invalid calendar planning checkpoint')
        if (not set(state['last_choice']) <= set(self.planner.config['actors'])
                or any(type(v) is not int or not self.runtime.time_kernel.axis.start <= v <= now for v in state['last_choice'].values())):
            raise ValueError('Invalid calendar choice checkpoint')
        decisions = [r for r in rows if r['details'].get('selection_mechanism') in {'bounded_goal_search','bounded_goal_assessment'}]
        history = [r['details']['planning'] for r in decisions]
        memory = {r['subject']:r['details']['planning_memory'] for r in decisions}
        expected_assessments = {r['subject']:r['details']['assessment_hash'] for r in decisions
                                if r['details']['selection_mechanism']=='bounded_goal_assessment'}
        if state['last_assessment'] != expected_assessments:
            raise ValueError('Assessment cache differs from recorded assessments')
        if state['planner'] != {'version':1,'history':history,'actors':memory}:
            raise ValueError('Planner checkpoint differs from selected decisions')
        goals = state['objectives']
        if (not isinstance(goals,dict) or set(goals) != {'version','turn','last_closed','world','actors'}
                or goals['version'] != 1 or not 0 <= goals['turn'] <= now or not -1 <= goals['last_closed'] <= now
                or {r['condition'] for r in goals['world']} != set(self.world.goal_rules.definitions)
                or not set(goals['actors']) <= set(self.runtime.people)):
            raise ValueError('Invalid calendar objective checkpoint')
        for record in goals['world']:
            definition = self.world.goal_rules.definitions[record['condition']]
            if record['period'] != definition['period'] or record['status'] not in {'not_started','pending','achieved','failed','unknown'}:
                raise ValueError('Invalid objective checkpoint verdict')

    def snapshot(self):
        return {'last_choice': deepcopy(self.last_choice), 'last_assessment':deepcopy(self.last_assessment), 'planner': self.planner.snapshot(),
                'objectives': self.world.goal_rules.snapshot(self.world)}

    def restore(self, state):
        # State is protected by the native checkpoint transaction and journal.
        self.last_choice = deepcopy(state['last_choice'])
        self.last_assessment = deepcopy(state['last_assessment'])
        saved = state['planner']; self.planner.history = deepcopy(saved['history']); self.planner.memory = {}
        for who, value in saved['actors'].items():
            self.planner.memory[who] = {'known': {tuple(r['coordinate']):r['value'] for r in value['known']},
                'negative': {(tuple(r['coordinate']),r['not']) for r in value['negative']},
                'blocked': set(), 'inspected': {tuple(r) for r in value['inspected']}, 'last': deepcopy(value['last'])}
        goals = state['objectives']; objective = self.world.goal_rules
        objective.current_turn = goals['turn']; objective.last_closed = goals['last_closed']
        objective.world_records = {r['condition']:{k:deepcopy(v) for k,v in r.items() if k not in {'condition','label','period'}} for r in goals['world']}
        objective.actor_records = deepcopy(goals['actors'])
