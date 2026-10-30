"""Bounded actor-scoped causal planning over existing authored operators.

Intermediate tasks are runtime plans, never additions to personal commitments.
Unknown preconditions remain explicit assumptions, not newly acquired facts.
"""
from copy import copy, deepcopy
import heapq
import itertools
import json

from engine.concerns import coordinates, effect_coordinate, shape, distinct
from engine.action_timing import step_timing


class GoalPlanner:
    def __init__(self, raw):
        shape(raw, {'version', 'actors', 'max_depth', 'max_nodes'})
        if type(raw['version']) is not int or raw['version'] != 1:
            raise ValueError('Unsupported planning version')
        distinct(raw['actors'])
        if not raw['actors'] or type(raw['max_depth']) is not int or not 1 <= raw['max_depth'] <= 24:
            raise ValueError('Bounded planning actors/depth required')
        if type(raw['max_nodes']) is not int or not 16 <= raw['max_nodes'] <= 8192:
            raise ValueError('Bounded planning search required')
        self.config = deepcopy(raw)
        self.memory = {}
        self.history = []

    def bind(self, world):
        if (world.slot_execution or {}).get('mode') == 'calendar_promises_v2':
            if world.goal_rules is None or world.goal_rules.config['version'] != 2 or world.responses is not None:
                raise ValueError('Calendar planning requires calendar objectives and native task operators')
            if not set(self.config['actors']) <= set(world.subjects):
                raise ValueError('Unknown planning actor')
            return
        if (world.goal_rules is None or world.responses is None or world.responses.config['version'] != 2
                or world.responses.signals or world.responses.channels or world.slot_execution is not None):
            raise ValueError('Planning requires ordinary proactive responses and explicit goal rules')
        if not set(self.config['actors']) <= set(world.subjects):
            raise ValueError('Unknown planning actor')

    def owns(self, actor):
        return actor.id in self.config['actors']

    def before_action(self, world, actor):
        if not self.owns(actor) or actor.vitality not in {'alive', 'revived'}: return
        memory = self.memory.setdefault(actor.id, {'known': {}, 'negative': set(), 'blocked': set(), 'last': None})
        visible = self._visible(world, actor)
        memory['known'].update(visible)
        memory['negative'] = {n for n in memory['negative'] if n[0] not in visible}

    def active_goal(self, world, actor, root, turn):
        if actor.vitality not in {'alive', 'revived'} or not self.owns(actor) or root not in world.goal_rules.definitions:
            return False
        if root not in {c['condition'] for c in world.concerns.commitments_for(world, actor.id)}:
            return False
        view = world.goal_rules.actor_view(world, actor.id, root, turn)
        return view['status'] not in {'achieved', 'failed'} and view['period']['start'] <= turn <= view['period']['end']

    def _visible(self, world, actor):
        if getattr(world, 'planning_domain', None) is not None:
            return world.planning_domain.visible(actor)
        return self._ordinary_visible(world,actor)

    def _ordinary_visible(self, world, actor):
        values = {('zone', actor.id): actor.zone}
        for person in ([] if getattr(actor, 'in_transit', False) else world.present_subjects(actor.zone, include_downed=False)):
            values[('zone', person.id)] = person.zone
        for item in world.items:
            values[('inventory', actor.id, item)] = actor.inventory.get(item, 0)
        for fact in world.facts:
            values[('knowledge', actor.id, fact)] = fact in actor.knowledge
        for key, value in world.responses.state.items():
            if world.responses.can_observe(actor, key):
                values[('state', key)] = value
        for definition in world.concerns.conditions.values():
            for coord in coordinates(definition['test']):
                if world.concerns._visible(world, actor.id, coord, definition['visibility'] == 'public'):
                    value = world.concerns._read(world, coord)
                    if value is not None: values[tuple(coord)] = value
        if getattr(world,'social_planner',None) is not None:
            values.update(world.social_planner.visible(world,actor))
        return values

    def view(self, world, actor):
        state = dict(self.memory.get(actor.id, {}).get('known', {}))
        # Positive authored beliefs justify a value; negative beliefs do not
        # identify which alternative value actually holds.
        for cid, definition in world.concerns.conditions.items():
            test = definition['test']; belief = actor.beliefs.get(cid)
            if belief and belief.value == 'true' and belief.confidence > 0 and test['kind'] in {'state', 'zone'}:
                coord = tuple(coordinates(test)[0])
                if (coord, test['equals']) not in self.memory.get(actor.id, {}).get('negative', set()):
                    state.setdefault(coord, test['equals'])
            elif belief and belief.value == 'true' and belief.confidence > 0 and test['kind'] == 'inventory':
                state.setdefault(tuple(coordinates(test)[0]), test['minimum'])
        state.update(self._visible(world, actor))
        return state

    @staticmethod
    def effects(state):
        result = []
        for coord, value in sorted(state.items()):
            kind, *args = coord
            effect = {'kind': kind, 'value': value}
            if kind == 'state': effect['key'] = args[0]
            elif kind == 'task': effect['task'] = args[0]
            elif kind == 'promise': effect.update(promise=args[0], version=int(args[1]))
            elif kind == 'relation': effect.update(observer=args[0], target=args[1])
            else:
                effect['actor'] = args[0]
                if kind == 'inventory': effect['item'] = args[1]
                if kind == 'knowledge': effect['fact'] = args[1]
            result.append(effect)
        return result

    def _safe(self, world, actor, root, state, changed, *, final=False):
        projection = {'effects': self.effects(state)}
        assessment = world.concerns.assessment(world, actor.id, projection=projection)
        own = next(c for c in world.concerns.commitments_for(world, actor.id) if c['condition'] == root)
        for entry in assessment['entries']:
            cid = entry['condition']
            touched = bool(changed & set(coordinates(world.concerns.conditions[cid]['test'])))
            if cid in world.goal_rules.definitions:
                touched |= bool(changed & world.goal_rules.affected_coordinates(world, cid))
            if not touched:
                continue
            failure = entry.get('objective_prediction', {}).get('failure', {}).get('state', 'false')
            if failure == 'true' and (entry['required'] or entry['priority'] >= own['priority']):
                return None
            if entry['state'] != 'true' and (entry['required'] or entry['priority'] >= own['priority']):
                # The root can be pending while its prerequisites are obtained.
                if cid == root and not final and failure == 'false':
                    continue
                if (cid==root and not final and failure=='unknown' and not entry['required']
                        and getattr(world,'native_planning',None) is not None):
                    # Information gathering may leave an unrelated, unknown
                    # failure predicate untouched. It does not make it false.
                    from engine.objectives import references
                    failure_coords={c for ref in references(world.goal_rules.definitions[root]['failure'])
                                    for c in coordinates(world.concerns.conditions[ref]['test'])}
                    if not changed & failure_coords:continue
                return None
        return assessment

    def _relevant(self, world, actor, root):
        needed = set(world.goal_rules.affected_coordinates(world, root))
        operators = set()
        while True:
            before = len(operators), len(needed)
            if getattr(world,'social_planner',None) is not None: world.social_planner.expand(world,actor,needed)
            for routes in world.routes.values():
                for route in routes:
                    for cid in route.requires_conditions:
                        needed.update(coordinates(world.concerns.conditions[cid]['test']))
            for spec in world.responses.actions.values():
                if actor.id not in spec['actors'] or not needed.intersection(map(effect_coordinate, spec['effects'])):
                    continue
                operators.add(spec['id'])
                needed.update(('inventory', actor.id, item) for item in spec['items'])
                needed.update(('knowledge', actor.id, fact) for fact in spec['requires'])
                needed.update(('state', key) for key in spec['preconditions'])
                if spec.get('authorization') and getattr(world,'social_planner',None) is not None:
                    auth=spec['authorization']
                    needed.update(('state',k) for k in world.deliberation.specs[auth['proposal']]['outcomes'][auth['status']])
            if before == (len(operators), len(needed)):
                return operators, needed

    def _item_needs(self, world, actor, root):
        from engine.objectives import references
        relevant, _ = self._relevant(world, actor, root)
        needs = {}
        for sid in relevant:
            for item, count in world.responses.actions[sid]['items'].items():
                needs[item] = max(needs.get(item, 0), count)
        for cid in references(world.goal_rules.definitions[root]['success']):
            test = world.concerns.conditions[cid]['test']
            if test['kind'] == 'inventory' and test['actor'] == actor.id:
                needs[test['item']] = max(needs.get(test['item'], 0), test['minimum'])
        return needs

    def _steps(self, world, actor, root, state, turn, elapsed, spent):
        native=getattr(world,'native_planning',None)
        if getattr(world, 'planning_domain', None) is not None:
            yield from world.planning_domain.steps(actor, root, state, turn, elapsed, spent)
            if native is None:return
        from engine.phase2 import available_verbs
        from engine.objective_actions import offered_assets
        shadow = copy(actor)
        shadow.inventory = {i: state.get(('inventory', actor.id, i), 0) for i in world.items}
        shadow.knowledge = {f for f in world.facts if state.get(('knowledge', actor.id, f), False)}
        shadow.zone = state[('zone', actor.id)]
        shadow.stamina = max(0, actor.stamina - spent)
        point = turn + elapsed
        if native is not None:shadow=native._shadow(actor,state,spent,point)
        day = (max(1, point) - 1) // len(world.slots) + 1
        allowed = available_verbs(world, shadow, turn=point, day=day)
        negatives = self.memory.get(actor.id, {}).get('negative', set())
        negatives = set(negatives)
        for cid, definition in world.concerns.conditions.items():
            test = definition['test']; belief = actor.beliefs.get(cid)
            if test['kind'] == 'state' and belief and belief.value == 'false' and belief.confidence > 0:
                negatives.add((('state', test['key']), test['equals']))
        relevant, needed = self._relevant(world, actor, root)
        social=getattr(world,'social_planner',None)
        if social is not None: yield from social.steps(world,actor,root,state,turn,elapsed,spent)
        timing = world.action_timing
        if native is None and timing is not None and 'rest' in timing.rules and 'rest' in allowed and world.genre_allows('rest'):
            recovery = min(actor.stamina_max - shadow.stamina, actor.stamina_recover)
            if recovery > 1e-9:
                duration, _, completion = step_timing(world, actor, 'rest', (), 1, 0, turn=turn, elapsed=elapsed)
                yield {'verb': 'rest', 'args': [], 'label': '休息する', 'duration': duration,
                       'completion_delay': completion, 'cost': 0, 'recovery': recovery,
                       'effects': [], 'assumptions': [], 'meta': {}}
        if native is None and 'move' in allowed and world.genre_allows('move'):
            excluded = world._excluded_zones(shadow)
            actual_paths = world.reachable_paths(shadow,projection={'effects':self.effects(state)}) if world.action_timing is not None else None
            for route in world.routes.get(shadow.zone, ()):
                if not world._route_allowed(shadow,route,projection={'effects':self.effects(state)}): continue
                path = actual_paths.get(route.destination) if actual_paths is not None else None
                if actual_paths is not None and path is None: continue
                raw_cost = path.cost if path is not None else route.cost
                hops = path.hops if path is not None else 1
                duration, cost, completion = step_timing(world, actor, 'move', (route.destination,), hops, raw_cost,
                    turn=turn, elapsed=elapsed, hops=hops)
                if (route.destination not in shadow.range_zones or route.destination in excluded
                        or cost > shadow.stamina or route.requires_item and not shadow.has_item(route.requires_item)):
                    continue
                yield {'verb': 'move', 'args': [route.destination], 'label': route.destination,
                       'duration': duration, 'cost': cost,
                       **({'completion_delay': completion} if world.action_timing is not None else {}),
                       'effects': [{'kind': 'zone', 'actor': actor.id, 'value': route.destination}],
                       'assumptions': [], 'meta': {'dest': route.destination, 'cost': raw_cost, 'hops': hops,
                                                  'route': list(path.route) if path is not None else [shadow.zone, route.destination]}}
        if 'respond' in allowed and world.genre_allows('respond'):
            for spec in world.responses.actions.values():
                if spec['id'] not in relevant or shadow.zone != spec['zone']:
                    continue
                if social is not None and not social.can_work(actor.id,spec['id']): continue
                guard_view = tuple((k, state.get(('state', k))) for k in sorted(spec['preconditions']))
                if (spec['id'], guard_view) in self.memory.get(actor.id, {}).get('blocked', set()):
                    continue
                if any(not shadow.has_item(i, n) for i, n in spec['items'].items()) or set(spec['requires']) - shadow.knowledge:
                    continue
                auth = spec.get('authorization')
                if auth:
                    from engine.social_planning import key as protocol_key
                    granted=(state.get(protocol_key(auth['proposal'],'phase'))==auth['status'] if social is not None
                             else world.deliberation.decided(auth['proposal'],auth['status']))
                    if not granted: continue
                assumptions = []
                for key, wanted in spec['preconditions'].items():
                    coord = ('state', key)
                    if coord in state and state[coord] != wanted or (coord, wanted) in negatives and coord not in state:
                        break
                    if coord not in state:
                        assumptions.append({'key': key, 'expected': wanted})
                else:
                    remaining = spec['steps'] - (world.responses.progress.get((actor.id, spec['id']), 0) if not elapsed else 0)
                    duration, cost, completion = step_timing(world, actor, 'respond', (spec['id'],), remaining,
                        spec['cost'] * remaining, turn=turn, elapsed=elapsed)
                    if spec['cost'] * remaining <= shadow.stamina:
                        yield {'verb': 'respond', 'args': [spec['id']], 'label': spec['label'],
                               'duration': duration, 'cost': cost,
                               **({'completion_delay': completion} if world.action_timing is not None else {}),
                               'effects': deepcopy(spec['effects']), 'assumptions': assumptions, 'meta': {}}
        if 'negotiate' not in allowed or not world.genre_allows('negotiate') or not actor.objective_claimant:
            return
        for item, count in sorted(self._item_needs(world, actor, root).items()):
            if shadow.inventory[item] >= count:
                continue
            for holder in sorted(world.subjects.values(), key=lambda p: p.id):
                if holder.id == actor.id:
                    continue
                known_zone = state.get(('zone', holder.id))
                stock = state.get(('inventory', holder.id, item))
                if known_zone != shadow.zone or stock is None or stock < 1:
                    continue
                offer = world.offers.get((actor.id, holder.id))
                assets = offered_assets(world, shadow, item)
                if (offer and offer.get('objective') == item
                        and (offer.get('status') == 'refused' or turn > offer.get('expires_at',offer['turn'] + 1))
                        and offer.get('assets') == assets and offer.get('planning_stock') == stock):
                    continue
                if offer and offer.get('objective') == item and point <= offer['turn'] + 4:
                    continue
                if world.permission('negotiate', world.target_role(actor, holder)) <= 0:
                    continue
                effects = [{'kind': 'inventory', 'actor': actor.id, 'item': item, 'value': shadow.inventory[item] + 1}]
                effects.append({'kind': 'inventory', 'actor': holder.id, 'item': item, 'value': stock - 1})
                effects += [{'kind': 'inventory', 'actor': actor.id, 'item': name, 'value': 0} for name in assets]
                duration=2;completion=1
                if world.action_timing is not None:
                    own,_,own_finish=step_timing(world,actor,'negotiate',(holder.id,),1,0,turn=turn,elapsed=elapsed)
                    reply,_,_=step_timing(world,holder,'concede',(actor.id,),1,0,turn=turn+elapsed+own)
                    duration=own+reply+1;completion=own_finish+reply+1
                yield {'verb': 'negotiate', 'args': [holder.id], 'label': 'negotiate ' + item,
                       'duration': duration, 'completion_delay':completion, 'cost': 0, 'effects': effects,
                       'assumptions': [{'consent': holder.id}],
                       'meta': {'target': holder.id, 'objective': item, 'objective_goal': root, 'planning_root': root}}

    def search(self, world, actor, root, turn, *, initial=None):
        if not self.active_goal(world, actor, root, turn):
            return {'status': 'inactive', 'steps': []}
        start = self.view(world, actor) if initial is None else dict(initial)
        period = world.goal_rules.definitions[root]['period']
        current = world.goal_rules.predict(world, root, actor=actor.id, projection={'effects': self.effects(start)})
        if current['success']['state'] == 'true' and current['failure']['state'] == 'false':
            return {'status': 'condition_met', 'root': root, 'steps': [], 'eta': 0, 'assumptions': [],
                    'forecast': world.concerns.assessment(world, actor.id, projection={'effects': self.effects(start)}),
                    'expanded': 0, 'max_nodes': self.config['max_nodes']}
        commitments = world.concerns.commitments_for(world, actor.id)
        ceiling = tuple(sum(c['priority'] == p for c in commitments) for p in range(10, 0, -1))
        best_plan = None; best_rank = None
        information_plan = None
        serial = itertools.count()
        queue = [(0, next(serial), 0, 0.0, start, [], [])]
        visited = {}; expanded = 0; queued = 1; truncated = False
        while queue and expanded < self.config['max_nodes']:
            _, _, elapsed, spent, state, steps, assumptions = heapq.heappop(queue)
            token = tuple(sorted(state.items()))
            previous = visited.setdefault(token, [])
            budget = elapsed, spent, len(assumptions)
            if any(all(a <= b for a, b in zip(old, budget)) for old in previous): continue
            previous[:] = [old for old in previous if not all(a <= b for a, b in zip(budget, old))]
            previous.append(budget); expanded += 1
            if (getattr(world,'native_planning',None) is not None and steps
                    and steps[-1]['meta'].get('native_information') and information_plan is None):
                information_plan = {'status':'seeking_information','root':root,'steps':steps,'eta':elapsed,
                    'assumptions':assumptions,'observation_site':steps[-1]['meta']['observation_site'],
                    'intermediate_tasks':[s['label'] for s in steps[:-1]], 'expanded':expanded,
                    'max_nodes':self.config['max_nodes']}
            if steps:
                forecast = world.goal_rules.predict(world, root, actor=actor.id, projection={'effects': self.effects(state)})
                if forecast['success']['state'] == 'true' and forecast['failure']['state'] == 'false':
                    changed = {effect_coordinate(e) for s in steps for e in s['effects']}
                    assessment = self._safe(world, actor, root, state, changed, final=True)
                    if assessment is not None:
                        scores = {v['priority']: v['score'] for v in assessment['vector']}
                        vector = tuple(scores.get(p, 0) for p in range(10, 0, -1))
                        candidate = {'status': 'conditional' if assumptions else 'planned', 'root': root,
                                'steps': steps, 'eta': elapsed, 'assumptions': assumptions, 'forecast': assessment,
                                'intermediate_tasks': [s['label'] for s in steps[:-1]],
                                'expanded': expanded, 'max_nodes': self.config['max_nodes']}
                        rank = vector, -len(assumptions), -elapsed, -spent
                        if best_rank is None or rank > best_rank:
                            best_plan, best_rank = candidate, rank
                        if vector == ceiling and not assumptions:
                            return candidate
                        if getattr(world,'native_planning',None) is not None and vector == ceiling:
                            return candidate
                    continue
            if len(steps) >= self.config['max_depth']:
                truncated = True
                continue
            for step in self._steps(world, actor, root, state, turn, elapsed, spent):
                duration = step['duration']
                if turn + elapsed + step.get('completion_delay', duration - 1) > period['end']: continue
                changed = {effect_coordinate(e) for e in step['effects']}
                future = dict(state)
                for assumption in step['assumptions']:
                    if 'key' in assumption:
                        coord = ('state', assumption['key'])
                        future[coord] = assumption['expected']
                        changed.add(coord)
                future.update({effect_coordinate(e): e['value'] for e in step['effects']})
                if (future == state and not step.get('recovery')
                        or self._safe(world, actor, root, future, changed) is None): continue
                # Assumptions are conditional branches, not actor knowledge.
                pending = [*assumptions, *step['assumptions']]
                if queued >= self.config['max_nodes']:
                    truncated = True
                    continue
                domain=getattr(world,'native_planning',None)
                estimate=0 if domain is None else 3*domain.heuristic(actor,root,future)
                heapq.heappush(queue, (elapsed + duration + 3 * len(pending) + estimate, next(serial),
                    elapsed + duration, spent + step['cost'] - step.get('recovery', 0), future, [*steps, step], pending))
                queued += 1
        if best_plan is not None:
            best_plan.update(expanded=expanded, search_truncated=bool(queue or truncated))
            return best_plan
        if information_plan is not None:
            information_plan.update(expanded=expanded,search_truncated=bool(queue or truncated))
            return information_plan
        return {'status': 'search_limit' if queue or truncated else 'no_known_plan', 'root': root, 'steps': [],
                'expanded': expanded, 'max_nodes': self.config['max_nodes']}

    def _pending(self, world, actor, root, turn):
        if getattr(world, 'planning_domain', None) is not None and getattr(world,'native_planning',None) is None:return None
        for (claimant, holder), offer in sorted(world.offers.items()):
            if claimant == actor.id and offer.get('planning_root') == root and self.offer_valid(world, actor, offer, turn):
                return holder
        return None

    def _information(self, world, actor, root, turn, day=1):
        """Travel to declared observation sites without reading their contents."""
        domain = getattr(world, 'planning_domain', None)
        if domain is not None and getattr(world,'native_planning',None) is None:
            return domain.information(actor, root, turn) if hasattr(domain, 'information') else None
        from engine.phase2 import available_verbs
        if 'move' not in available_verbs(world, actor, turn=turn, day=day) or not world.genre_allows('move'):
            return None
        _, needed = self._relevant(world, actor, root)
        zones = {zone for kind, *args in needed if kind == 'state' and args[0] in world.responses.locations
                 and not (getattr(world,'native_planning',None) and world.native_planning.handles(('state',args[0])))
                 for zone in world.responses.observation_zones(args[0])}
        memory = self.memory.get(actor.id, {})
        epoch = memory.get('failures', 0)
        inspected = memory.get('inspected', set())
        paths = world.reachable_paths(actor)
        deadline = world.goal_rules.definitions[root]['period']['end']
        for zone in sorted(zones, key=lambda z: (paths[z].hops if z in paths else 100000, z)):
            if zone not in paths or (root, zone, epoch) in inspected:
                continue
            route = paths[zone]
            duration = step_timing(world, actor, 'move', (zone,), route.hops, route.cost, turn=turn, hops=route.hops)[0]
            if turn + duration > deadline: continue
            # Runtime movement remains adjacent and uses its real cost.
            edge = next(r for r in world.routes[actor.zone] if r.destination == route.route[1])
            return {'status': 'seeking_information', 'root': root, 'steps': [], 'observation_site': zone,
                    'next_zone': route.route[1], 'eta': duration, 'cost': edge.cost}
        return None

    def _fill_wait(self, world, actor, plans, turn):
        """Use scheduled slack only if every displaced higher goal stays feasible.

        No future cooperation is promised. Presence-guarded handoff is allowed:
        its time is bounded and actual delivery still checks the recipient.
        Resource costs and recovery are projected before rechecking.
        """
        feasible = [p for p in plans if p['steps']]
        if not feasible: return None
        priority = max(p['priority'] for p in feasible)
        highest = [p for p in feasible if p['priority'] == priority]
        slack = min(sum(s['duration'] for s in p['steps'] if s['verb']=='wait') for p in highest)
        if not slack: return None
        for lower in sorted(feasible, key=lambda p: (-p['priority'], p.get('eta', 0), p['root'])):
            if (lower['priority'] >= priority or lower.get('eta',slack+1) > slack
                    or any(not a.get('requires_presence') for a in lower.get('assumptions',[]))): continue
            future = self.view(world, actor); shadow = copy(actor)
            for step in lower['steps']:
                future.update({effect_coordinate(e):e['value'] for e in step['effects']})
                shadow.stamina = min(actor.stamina_max, shadow.stamina-step['cost']+step.get('recovery',0))
            displaced = [p for p in feasible if p['priority'] > lower['priority']]
            safe = True
            for higher in displaced:
                later = self.search(world, shadow, higher['root'], turn+lower['eta'], initial=future)
                if not later.get('steps') and later['status'] != 'condition_met': safe = False; break
            if safe:
                lower['uses_waiting_slack'] = [p['root'] for p in displaced]
                return lower
        return None

    def candidates(self, world, actor, turn, day):
        from engine.actions import Action
        from engine.phase2 import available_verbs
        allowed = available_verbs(world, actor, turn=turn, day=day)
        plans = []
        for value in world.concerns.commitments_for(world, actor.id):
            root = value['condition']
            if not self.active_goal(world, actor, root, turn): continue
            holder = self._pending(world, actor, root, turn)
            plan = ({'status': 'awaiting_consent', 'root': root, 'steps': [], 'holder': holder}
                    if holder else self.search(world, actor, root, turn))
            if plan['status'] in {'no_known_plan', 'search_limit'}:
                social=getattr(world,'social_planner',None)
                plan = (social.fallback(world,actor,root,turn) if social else None) or self._information(world, actor, root, turn, day) or plan
            plan['goal_label'] = world.concerns.conditions[root]['label']
            plan['priority'] = value['priority']; plans.append(plan)
        if not plans: return []
        gap_plan = self._fill_wait(world, actor, plans, turn)
        if gap_plan is not None: plans = [gap_plan]
        feasible = [p for p in plans if p['steps'] or p['status'] in {'awaiting_consent', 'seeking_information'}]
        best = max(p['priority'] for p in feasible or plans)
        result = []
        for plan in plans:
            if plan['priority'] != best: continue
            if plan['steps']:
                step = plan['steps'][0]
                action = Action(step['verb'], tuple(step['args']), {**step['meta'], 'goal_plan': plan})
                result.append((action, (30 + 4 * actor.traits['diligence']) / max(1, step['duration'])))
            elif plan['status'] == 'seeking_information':
                result.append((Action('move', (plan['next_zone'],), {'goal_plan': plan,
                    'dest': plan['next_zone'], 'cost': plan['cost'], 'hops': 1,
                    'route': [actor.zone, plan['next_zone']]}), 30))
            elif 'rest' in allowed and world.genre_allows('rest'):
                result.append((Action('rest', meta={'goal_plan': plan}), 20))
        return result

    def validate(self, world, actor, action, turn):
        root = action.meta.get('goal_plan', {}).get('root')
        if world.action_timing is not None and world.action_timing.prepaid(actor,action):
            # Work already paid for is checked for current safety, not selected
            # again as if its full cost and duration were still outstanding.
            effects=(world.responses.actions[action.args[0]]['effects'] if action.verb=='respond' else
                     [{'kind':'zone','actor':actor.id,'value':action.args[0]}] if action.verb=='move' else [])
            state=self.view(world,actor);state.update({effect_coordinate(e):e['value'] for e in effects})
            assessment=self._safe(world,actor,root,state,{effect_coordinate(e) for e in effects})
            valid=self.active_goal(world,actor,root,turn) and assessment is not None
            return {'excluded':[] if valid else ['completion_no_longer_safe'],'forecast':assessment,
                    'condition':root,'plan':action.meta['goal_plan']}
        if action.meta.get('goal_plan',{}).get('status')=='seeking_judgment' and getattr(world,'social_planner',None):
            plan=world.social_planner.fallback(world,actor,root,turn)
            valid=bool(plan and self.active_goal(world,actor,root,turn) and
                       (plan['steps'][0]['verb'],tuple(plan['steps'][0]['args']))==(action.verb,action.args))
            return {'excluded':[] if valid else ['judgment_request_changed'],'plan':plan}
        if action.meta.get('goal_plan', {}).get('status') == 'seeking_information' and not action.meta['goal_plan'].get('steps'):
            info = self._information(world, actor, root, turn, (turn - 1) // len(world.slots) + 1)
            valid = self.active_goal(world, actor, root, turn) and info and action.verb == 'move' and action.args == (info['next_zone'],)
            return {'excluded': [] if valid else ['observation_route_changed'], 'plan': info}
        plan = self.search(world, actor, root, turn)
        if plan['status']=='condition_met' and not plan['steps'] and action.verb=='rest':
            return {'excluded':[],'plan':plan,'forecast':plan.get('forecast'),'condition':root}
        if not plan['steps'] or (plan['steps'][0]['verb'], tuple(plan['steps'][0]['args'])) != (action.verb, action.args):
            return {'excluded': ['plan_no_longer_justified'], 'plan': plan}
        if action.verb == 'negotiate' and any(action.meta.get(k) != plan['steps'][0]['meta'][k]
                                             for k in ('objective', 'objective_goal', 'planning_root')):
            return {'excluded': ['acquisition_binding_mismatch'], 'plan': plan}
        return {'excluded': [], 'plan': plan, 'forecast': plan.get('forecast'), 'condition': root,
                'consent_required': action.verb == 'negotiate'}

    def offer_valid(self, world, actor, offer, turn):
        return (offer.get('status', 'pending') == 'pending' and turn <= offer.get('expires_at',offer['turn'] + 1)
                and self.active_goal(world, actor, offer['planning_root'], turn)
                and actor.inventory.get(offer['objective'], 0) < self._item_needs(world, actor, offer['planning_root']).get(offer['objective'], 0))

    def consent(self, world, actor, offer, effects, turn):
        state = self.view(world, actor)
        state.update({effect_coordinate(e): e['value'] for e in effects})
        root = offer['planning_root']
        safe = self._safe(world, actor, root, state, {effect_coordinate(e) for e in effects})
        if safe is None: return {'excluded': ['required_or_higher_priority_conflict']}
        plan = self.search(world, actor, root, turn, initial=state)
        if not plan['steps'] and plan['status'] != 'condition_met':
            return {'excluded': ['continuation_not_justified'], 'plan': plan}
        return {'excluded': [], 'forecast': plan['forecast'], 'condition': root, 'plan': plan}

    def after_action(self, world, actor, action, result, details, turn):
        if action.verb == 'concede' and action.args and action.args[0] in self.config['actors']:
            peer_memory = self.memory.setdefault(action.args[0], {'known': {}, 'negative': set(), 'blocked': set(), 'last': None})
            peer_memory['reply'] = result
        if not self.owns(actor): return
        memory = self.memory.setdefault(actor.id, {'known': {}, 'negative': set(), 'blocked': set(), 'last': None})
        before = dict(memory['known'])
        visible = self._visible(world, actor)
        memory['known'].update(visible)
        for coord in visible:
            memory['negative'] = {n for n in memory['negative'] if n[0] != coord}
        failed = result in {'failed', 'invalid', 'refused', 'lost', 'misjudged'}
        if failed:
            memory['failures'] = memory.get('failures', 0) + 1
        if failed and action.verb == 'respond' and action.args[0] in world.responses.actions:
            spec = world.responses.actions[action.args[0]]
            if details.get('response', {}).get('reason') == 'actual_preconditions_changed':
                if len(spec['preconditions']) == 1:
                    key, value = next(iter(spec['preconditions'].items()))
                    coord = ('state', key)
                    if coord not in visible:
                        memory['known'].pop(coord, None)
                        memory['negative'].add((coord, value))
                        from engine.subject import Belief
                        for cid, definition in world.concerns.conditions.items():
                            test = definition['test']
                            if test['kind'] == 'state' and test['key'] == key and test['equals'] == value:
                                actor.beliefs[cid] = Belief('false', 1)
                else:
                    state = self.view(world, actor)
                    memory['blocked'].add((spec['id'], tuple((k, state.get(('state', k))) for k in sorted(spec['preconditions']))))
        if action.meta.get('goal_plan'):
            plan = deepcopy(action.meta['goal_plan'])
            previous = memory['last']
            reason = ('initial_plan' if previous is None else 'action_interrupted' if previous.get('interrupted') else 'previous_action_failed' if previous['failed']
                      else 'reply_received' if memory.get('reply') else
                      'new_information' if before != memory['known'] else 'continuing')
            receipts=[];social=getattr(world,'social_planner',None)
            selected=result=='started' or world.action_timing is None or action.verb not in world.action_timing.rules
            observations=list((previous or {}).get('observations',[]))
            if social is not None and not selected:
                own_effects=({effect_coordinate(e) for e in world.responses.actions[action.args[0]]['effects']}
                             if action.verb=='respond' and world.responses is not None else set())
                changes=[{'coordinate':list(k),'before':before.get(k),'after':v} for k,v in visible.items()
                    if before.get(k)!=v and k not in own_effects and
                    not (k[0]=='state' and k[1].startswith('@protocol:')) and
                    not (k[0] in {'zone','inventory','vitality'} and k[1]==actor.id)]
                if changes:
                    ident='observation:'+actor.id+':'+str(turn)
                    details['planning_observation']={'id':ident,'actor':actor.id,'turn':turn,'changes':changes}
                    observations.append(ident)
            if social is not None and selected and previous is not None and observations: reason='new_information'
            if social is not None and selected:
                cursor=(previous or {}).get('social_cursor',0)
                receipts=[deepcopy(e) for e in social.history[cursor:] if actor.id in e['received_by']
                          and e.get('binding')=={'actor':actor.id,'root':plan['root']}]
                if previous is not None and receipts:
                    last=receipts[-1]
                    reason=('cooperation_refused' if last.get('answer')=='refuse' else
                            'cooperation_failed' if last.get('answer')=='failed' else
                            'cooperation_accepted' if last.get('answer')=='accept' else
                            'cooperation_completed' if last.get('answer')=='completed' else
                            'review_received' if last['verb']=='review_proposal' else
                            'judgment_received' if last['verb']=='decide_proposal' else
                            'information_shared' if last['kind']=='disclosure' else 'protocol_progress')
            elif social is not None and not failed: reason='action_completed'
            record = {'turn': turn, 'actor': actor.id, 'reason': reason, 'result': result, **plan}
            if social is not None:
                record['received_events']=[{'receipt_id':e['receipt_id'],'turn':e['turn'],'kind':e['kind'],
                    'verb':e['verb'],'received_by':e['received_by']} for e in receipts]
                record['observation_causes']=observations if selected else []
            self.history.append(record)
            details['planning'] = record
            if plan['status'] == 'seeking_information' and actor.zone == plan['observation_site']:
                memory.setdefault('inspected', set()).add((plan['root'], actor.zone, memory.get('failures', 0)))
            memory['last'] = {'failed': failed, 'root': plan['root']}
            if social is not None:
                memory['last']['social_cursor']=len(social.history) if selected else (previous or {}).get('social_cursor',0)
                memory['last']['observations']=[] if selected else observations
            if world.action_timing is not None: memory['last']['interrupted'] = result == 'interrupted'
            memory.pop('reply', None)

    def snapshot(self):
        return {'version': 1, 'history': deepcopy(self.history), 'actors': {
            actor: {'known': [{'coordinate': list(k), 'value': v} for k, v in sorted(m['known'].items())],
                    'negative': [{'coordinate': list(k), 'not': v} for k, v in sorted(m['negative'])],
                    'blocked': [list(v) for v in sorted(m.get('blocked', set()), key=repr)],
                    'inspected': [list(v) for v in sorted(m.get('inspected', set()))],
                    'last': deepcopy(m['last'])} for actor, m in sorted(self.memory.items())}}
