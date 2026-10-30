"""Bounded, opt-in concern-driven responses over the ordinary action loop.

Authored causal operators are not free-form planning or psychological inference.
Beliefs live on Subject; this runtime retains their provenance, plans and mail.
All objective state/history reports are kept out of candidate scoring.
"""
from copy import deepcopy

from engine.concerns import coordinates, effect_coordinate, finite, identifier, shape


VERBS = frozenset({'respond', 'inspect_response', 'request_response', 'answer_response'})
REQUEST_BLOCKERS = frozenset({'required_condition_conflict', 'higher_priority_harm',
                              'protection_not_justified', 'authorization_pending', 'known_precondition_failed',
                              'objective_terminal', 'objective_not_started', 'objective_too_late', 'objective_not_justified'})


def bounded_list(value, maximum=32):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError('Bounded response list required')
    return value


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('Response integer outside supported range')


class Responses:
    def __init__(self, raw):
        shape(raw, {'version', 'state', 'locations', 'signals', 'actions', 'channels', 'events', 'windows'})
        if type(raw['version']) is not int or raw['version'] not in (1, 2):
            raise ValueError('Unsupported responses version')
        self.config = deepcopy(raw)
        self.state = deepcopy(raw['state'])
        if not isinstance(self.state, dict) or not 1 <= len(self.state) <= 64:
            raise ValueError('Bounded response state required')
        for key, value in self.state.items():
            identifier(key); identifier(value)
        self.locations = raw['locations']
        if not isinstance(self.locations, dict) or set(self.locations) != set(self.state):
            raise ValueError('Every response state key needs an observation location')
        for value in self.locations.values():
            if self.config['version'] == 2 and isinstance(value, list):
                if not 1 <= len(value) <= 8 or len(set(value)) != len(value):
                    raise ValueError('Bounded distinct observation zones required')
                for zone in value: identifier(zone)
            else:
                identifier(value)
        self.signals = {}
        for signal in bounded_list(self.config['signals']):
            shape(signal, {'fact', 'condition', 'key', 'danger_values', 'max_age', 'threshold', 'deadline'})
            for key in ('fact', 'condition', 'key'): identifier(signal[key])
            if signal['fact'] in self.signals: raise ValueError('Duplicate response signal')
            if not bounded_list(signal['danger_values'], 16): raise ValueError('Danger values required')
            for value in signal['danger_values']: identifier(value)
            integer(signal['max_age'], 1, 10000)
            if not finite(signal['threshold'], 0.01, 1): raise ValueError('Invalid evidence threshold')
            if signal['deadline'] is not None: integer(signal['deadline'], 1, 10000)
            self.signals[signal['fact']] = signal
        self.actions = {}
        for spec in bounded_list(self.config['actions']):
            required = {'id', 'label', 'condition', 'signal', 'zone', 'actors', 'requires',
                        'items', 'cost', 'steps', 'preconditions', 'effects'}
            if not isinstance(spec, dict) or not required <= set(spec) or set(spec)-required-{'authorization'}:
                raise ValueError('Invalid response action fields')
            if spec.get('authorization') is not None:
                shape(spec['authorization'], {'proposal', 'status'})
                identifier(spec['authorization']['proposal'])
                if spec['authorization']['status'] != 'approved': raise ValueError('Implementation requires approval')
            for key in ('id', 'label', 'condition', 'zone'): identifier(spec[key])
            if raw['version'] == 1 or spec['signal'] is not None:
                identifier(spec['signal'])
            if spec['id'] in self.actions: raise ValueError('Duplicate response action')
            for key in ('actors', 'requires'):
                bounded_list(spec[key], 40)
                if len(set(spec[key])) != len(spec[key]): raise ValueError('Duplicate response reference')
                for value in spec[key]: identifier(value)
            if not isinstance(spec['items'], dict) or len(spec['items']) > 20:
                raise ValueError('Invalid response resource requirements')
            for item, count in spec['items'].items(): identifier(item); integer(count, 1, 100000)
            if not finite(spec['cost'], 0, 100000): raise ValueError('Invalid response effort')
            integer(spec['steps'], 1, 20)
            if not isinstance(spec['preconditions'], dict) or len(spec['preconditions']) > 32:
                raise ValueError('Invalid response preconditions')
            for key, value in spec['preconditions'].items(): identifier(key); identifier(value)
            self.actions[spec['id']] = spec
        self.channels = {}
        for channel in bounded_list(self.config['channels'], 80):
            shape(channel, {'from', 'to', 'delay'})
            identifier(channel['from']); identifier(channel['to']); integer(channel['delay'], 1, 100)
            pair = (channel['from'], channel['to'])
            if pair in self.channels or pair[0] == pair[1]: raise ValueError('Invalid response channel')
            self.channels[pair] = channel['delay']
        self.events = {}
        for event in bounded_list(self.config['events'], 100):
            shape(event, {'id', 'turn', 'unless', 'effects'})
            identifier(event['id']); integer(event['turn'], 1, 10000)
            if event['id'] in self.events or not isinstance(event['unless'], dict):
                raise ValueError('Invalid response event')
            for key, value in event['unless'].items(): identifier(key); identifier(value)
            self.events[event['id']] = event
        self.windows = {}
        for window in bounded_list(self.config['windows']):
            shape(window, {'condition', 'start', 'end', 'mode'})
            identifier(window['condition']); integer(window['start'], 1, 10000)
            integer(window['end'], window['start'], 10000)
            if window['condition'] in self.windows or window['mode'] not in {'continuous', 'by_deadline'}:
                raise ValueError('Invalid response temporal window')
            self.windows[window['condition']] = window
        self.observations = {}
        self.active = {}
        self.progress = {}
        self.requests = {}
        self.mail = []
        self.sent = set()
        self.applied = set()
        self.history = []
        self.temporal = {}
        self.last_tick = 0

    def bind(self, world):
        if world.concerns is None: raise ValueError('Responses require explicit concerns')
        if world.slot_execution is not None:
            raise ValueError('Responses v1 supports ordinary instant execution only')
        if world.deliberation and set(self.state) & set(world.deliberation.state):
            raise ValueError('Response and proposal state keys must be disjoint')
        if any(not set(self.observation_zones(key)) <= set(world.zones) for key in self.locations):
            raise ValueError('Unknown observation zone')
        people = set(world.subjects)
        for (sender, receiver) in self.channels:
            if {sender, receiver} - people: raise ValueError('Unknown communication participant')
        for fact, signal in self.signals.items():
            definition = world.facts.get(fact, {})
            if (not definition.get('values') or signal['key'] not in self.state
                    or signal['condition'] not in world.concerns.conditions):
                raise ValueError('Signal needs a valued fact, state key and condition')
            if not set(signal['danger_values']) <= set(definition['values']):
                raise ValueError('Unknown danger value')
            if self.state[signal['key']] not in definition['values']:
                raise ValueError('Signal initial state must be a declared fact value')
            if fact in world.truth_candidates or world.truth.get(fact, self.state[signal['key']]) != self.state[signal['key']]:
                raise ValueError('Signal truth must match its explicit initial state')
            world.truth[fact] = self.state[signal['key']]
        for spec in self.actions.values():
            authorization = spec.get('authorization')
            if authorization and (world.deliberation is None or authorization['proposal'] not in world.deliberation.specs):
                raise ValueError('Unknown implementation authorization')
            proactive = self.config['version'] == 2 and spec['signal'] is None
            if proactive and world.planner is None and (world.goal_rules is None or spec['condition'] not in world.goal_rules.definitions):
                raise ValueError('Proactive actions require an explicit objective contract')
            if ((not proactive and (spec['signal'] not in self.signals
                    or spec['condition'] != self.signals[spec['signal']]['condition']))
                    or spec['condition'] not in world.concerns.conditions
                    or spec['zone'] not in world.zones or not set(spec['actors']) <= people):
                raise ValueError('Invalid response action references')
            if not set(spec['items']) <= set(world.items): raise ValueError('Unknown response item')
            if not set(spec['preconditions']) <= set(self.state): raise ValueError('Unknown response precondition')
            self._validate_effects(world, spec['effects'], spec['requires'], authorized=bool(authorization))
            if proactive:
                continue
            signal = self.signals[spec['signal']]
            clear = next((e['value'] for e in spec['effects']
                          if e['kind'] == 'state' and e['key'] == signal['key']), None)
            if clear is None or clear in signal['danger_values']:
                raise ValueError('Response must explicitly resolve its declared signal')
        for event in self.events.values():
            if not set(event['unless']) <= set(self.state): raise ValueError('Unknown event guard')
            self._validate_effects(world, event['effects'], [])
        for window in self.windows.values():
            if window['condition'] not in world.concerns.conditions: raise ValueError('Unknown timed condition')

    def _validate_effects(self, world, effects, requires, *, authorized=False):
        # Reuse the typed projection validator rather than accept executable expressions.
        world.concerns.validate_projections(world, {'alternatives': [{'id': 'response'}],
            'concern_projections': {'response': {'requires': requires, 'effects': effects}}})
        for effect in effects:
            if effect['kind'] == 'zone':
                raise ValueError('Response effects cannot teleport people; use ordinary movement')
            if effect['kind'] == 'state' and effect['key'] not in self.state and not authorized:
                raise ValueError('Changing deliberation world state requires an authorization gate')
            for signal in self.signals.values():
                if effect_coordinate(effect) == ('state', signal['key']):
                    if effect['value'] not in world.facts[signal['fact']]['values']:
                        raise ValueError('Signal state must stay within fact values')

    def _observe(self, actor, fact, turn, source, *, observed_at=None):
        belief = actor.beliefs.get(fact)
        if belief is None: return
        self.observations[(actor.id, fact)] = {'value': belief.value, 'confidence': belief.confidence,
            'observed_at': turn if observed_at is None else observed_at, 'received_at': turn, 'source': source}

    def observation_zones(self, key):
        value = self.locations[key]
        return value if isinstance(value, list) else [value]

    def can_observe(self, actor, key):
        return actor.zone in self.observation_zones(key)

    def view(self, world, actor, fact, turn):
        signal = self.signals[fact]
        belief = actor.beliefs.get(fact)
        record = self.observations.get((actor.id, fact))
        # Configured initial beliefs are explicit inputs, not newly discovered truths.
        if record is None and belief is not None:
            record = {'value': belief.value, 'confidence': belief.confidence,
                      'observed_at': 0, 'received_at': 0, 'source': 'initial_belief'}
        if belief is None or record is None: return {'status': 'unknown', 'fact': fact}
        result = dict(record, fact=fact, value=belief.value, confidence=belief.confidence)
        if turn - record['observed_at'] > signal['max_age']: status = 'stale'
        elif belief.confidence < signal['threshold']: status = 'uncertain'
        else: status = 'danger' if belief.value in signal['danger_values'] else 'clear'
        return dict(result, status=status)

    def _apply(self, world, effects, turn):
        for effect in effects:
            kind, value = effect['kind'], effect['value']
            if kind == 'state':
                state = self.state if effect['key'] in self.state else world.deliberation.state
                state[effect['key']] = value
            elif kind == 'vitality':
                person = world.subjects[effect['actor']]
                if person.vitality != value:
                    person.vitality = value
                    person.downed_since = turn if value == 'downed' else None
            elif kind == 'zone': world.subjects[effect['actor']].zone = value
            elif kind == 'inventory': world.subjects[effect['actor']].inventory[effect['item']] = value
            elif kind == 'knowledge':
                facts = world.subjects[effect['actor']].knowledge
                facts.add(effect['fact']) if value else facts.discard(effect['fact'])
            elif kind == 'relation':
                before = world.relations.stance(effect['observer'], effect['target'])
                world.relations.change(effect['observer'], effect['target'], affinity=value-before)
        for fact, signal in self.signals.items(): world.truth[fact] = self.state[signal['key']]

    def tick(self, world, turn):
        """Environmental events and delayed communications happen before choices."""
        if turn <= self.last_tick: return []
        self.last_tick = turn
        markers = []
        for event in sorted(self.events.values(), key=lambda e: (e['turn'], e['id'])):
            if event['id'] in self.applied or event['turn'] > turn: continue
            self.applied.add(event['id'])
            prevented = bool(event['unless']) and all(self.state[k] == v for k, v in event['unless'].items())
            if not prevented: self._apply(world, event['effects'], turn)
            markers.append({'verb': 'response_environment', 'details': {'event': event['id'],
                'prevented': prevented, 'world_effects': [] if prevented else deepcopy(event['effects'])}})
        ready = [m for m in self.mail if m['due'] <= turn]
        self.mail = [m for m in self.mail if m['due'] > turn]
        for message in ready:
            recipient = world.subjects[message['to']]
            if recipient.vitality not in {'alive', 'revived'}:
                markers.append({'verb': 'response_delivery_failed', 'subject': message['to'],
                                'details': {'request': message['request'], 'reason': 'recipient_unavailable'}})
                continue
            if message['kind'] == 'request':
                request = self.requests[message['request']]
                request['status'] = 'delivered'
                trust = max(0, min(1, .5 + .5 * world.relations.stance(recipient.id, message['from'])))
                update = recipient.update_belief(message['fact'], message['value'], message['confidence'] * trust)
                if update['outcome'] != 'rejected':
                    self._observe(recipient, message['fact'], turn, message['from'], observed_at=message['observed_at'])
            else:
                # Sender learns a reply only when a return channel actually delivers it.
                self.requests[message['request']]['known_reply'] = message['reply']
                if message.get('fact'):
                    trust = max(0, min(1, .5 + .5 * world.relations.stance(recipient.id, message['from'])))
                    update = recipient.update_belief(message['fact'], message['value'], message['confidence'] * trust)
                    if update['outcome'] != 'rejected':
                        self._observe(recipient, message['fact'], turn, message['from'], observed_at=message['observed_at'])
            markers.append({'verb': 'response_message_received', 'subject': recipient.id,
                            'details': deepcopy(message)})
        from engine.subject import Belief
        for actor in world.subjects.values():
            if actor.vitality not in {'alive', 'revived'}: continue
            for fact, signal in self.signals.items():
                if self.can_observe(actor, signal['key']):
                    value = self.state[signal['key']]
                    actor.beliefs[fact] = Belief(value, 1.0)
                    self._observe(actor, fact, turn, 'direct')
        for aid, plan in list(self.active.items()):
            spec = self.actions[plan['action']]
            if plan.get('planning_root') and world.planner is not None:
                view = {'status': 'objective' if world.planner.active_goal(world, world.subjects[aid], plan['planning_root'], turn)
                        else 'objective_terminal'}
            else:
                view = self._motivation(world, world.subjects[aid], spec, turn)
            if view['status'] not in {'danger', 'objective'} or world.subjects[aid].vitality not in {'alive', 'revived'}:
                self.active.pop(aid); self.progress.pop((aid, spec['id']), None)
                markers.append({'verb': 'response_plan_cancelled', 'subject': aid,
                                'details': {'action': spec['id'], 'reason': view['status']}})
        self.record(world, turn)
        return markers

    def record(self, world, turn):
        """Evaluate actual results separately; never use temporal truth to score."""
        for cid, window in self.windows.items():
            if not window['start'] <= turn <= window['end']: continue
            value = world.concerns.evaluate(world, cid)['state']
            record = self.temporal.setdefault(cid, {'first_breach': None, 'achieved_at': None, 'complete': False})
            if value == 'false' and record['first_breach'] is None: record['first_breach'] = turn
            if value == 'true' and record['achieved_at'] is None: record['achieved_at'] = turn
            record['complete'] = turn >= window['end']
            if window['mode'] == 'continuous':
                record['status'] = ('violated' if record['first_breach'] is not None else
                                    'satisfied' if record['complete'] else 'pending')
            else:
                record['status'] = ('satisfied' if record['achieved_at'] is not None else
                                    'deadline_missed' if record['complete'] else 'pending')
        sample = {'turn': turn, 'world': world.concerns.world_report(world)}
        if not self.history or self.history[-1] != sample: self.history.append(sample)

    def _commitment(self, world, actor, cid):
        return next((c for c in world.concerns.commitments_for(world, actor.id)
                     if c['condition'] == cid), None)

    def _motivation(self, world, actor, spec, turn):
        if spec['signal'] is not None:
            return self.view(world, actor, spec['signal'], turn)
        if world.goal_rules is None or spec['condition'] not in world.goal_rules.definitions:
            return {'status': 'no_objective', 'confidence': 0}
        own = world.goal_rules.actor_view(world, actor.id, spec['condition'], turn)
        status = 'objective' if own['status'] not in {'achieved', 'failed'} else 'objective_terminal'
        return {'status': status, 'confidence': 1.0, 'source': 'own_commitment', 'objective': own}

    def _option(self, world, actor, spec, turn, day=1):
        """Pure actor-scoped forecast. Actual preconditions are checked at execution."""
        view = self._motivation(world, actor, spec, turn)
        commitment = self._commitment(world, actor, spec['condition'])
        result = {'action': spec['id'], 'condition': spec['condition'], 'signal': view, 'excluded': []}
        fail = result['excluded'].append
        if not commitment: fail('no_commitment')
        if view['status'] not in {'danger', 'objective'}: fail(view['status'])
        if actor.id not in spec['actors']: fail('capability_unavailable')
        authorization = spec.get('authorization')
        if authorization and not world.deliberation.decided(authorization['proposal'], authorization['status']):
            fail('authorization_pending')
        if 'respond' not in actor.verbs: fail('response_verb_unavailable')
        from engine.phase2 import available_verbs
        allowed = available_verbs(world, actor, turn=turn, day=day)
        if 'respond' not in allowed or not world.genre_allows('respond'): fail('response_phase_unavailable')
        if set(spec['requires']) - actor.knowledge: fail('missing_evidence')
        if any(not actor.has_item(item, n) for item, n in spec['items'].items()): fail('missing_resources')
        remaining = spec['steps'] - self.progress.get((actor.id, spec['id']), 0)
        if actor.stamina < spec['cost']: fail('insufficient_stamina')
        path = None
        if actor.zone != spec['zone']:
            path = world.reachable_paths(actor).get(spec['zone'])
            if path is None or 'move' not in allowed or not world.genre_allows('move'): fail('unreachable')
        hops = len(path.route)-1 if path else 0
        deadline = self.signals[spec['signal']]['deadline'] if spec['signal'] is not None else None
        if deadline is not None and turn + hops + remaining - 1 > deadline: fail('too_late')
        objective = world.goal_rules
        if objective is not None and spec['condition'] in objective.definitions:
            own = objective.actor_view(world, actor.id, spec['condition'], turn)
            result['objective_view'] = own
            if own['status'] in {'achieved', 'failed'}: fail('objective_terminal')
            if turn < own['period']['start']: fail('objective_not_started')
            if turn + hops + remaining - 1 > own['period']['end']: fail('objective_too_late')
        projection = {'effects': spec['effects']}
        forecast = world.concerns.assessment(world, actor.id, projection=projection)
        own_result = next((c for c in forecast['entries'] if c['condition'] == spec['condition']), None)
        if own_result is not None and own_result['state'] != 'true': fail('protection_not_justified')
        if objective is not None and spec['condition'] in objective.definitions:
            predicted = objective.predict(world, spec['condition'], actor=actor.id, projection=projection)
            result['objective_forecast'] = predicted
            if predicted['success']['state'] != 'true' or predicted['failure']['state'] != 'false':
                fail('objective_not_justified')
        known_failed = [key for key, value in spec['preconditions'].items()
                        if self.can_observe(actor, key) and self.state[key] != value]
        if known_failed: fail('known_precondition_failed')
        affected = {effect_coordinate(e) for e in spec['effects']}
        harms = [c for c in forecast['entries'] if c['required'] and c['state'] != 'true'
                 and affected & set(coordinates(world.concerns.conditions[c['condition']]['test']))]
        if harms: fail('required_condition_conflict')
        soft_harms = [c for c in forecast['entries'] if c['state'] == 'false'
                      and affected & set(coordinates(world.concerns.conditions[c['condition']]['test']))]
        if commitment and any(c['priority'] >= commitment['priority'] for c in soft_harms):
            fail('higher_priority_harm')
        if objective is not None:
            for value in forecast['entries']:
                cid = value['condition']
                if cid not in objective.definitions or not affected & objective.affected_coordinates(world, cid):
                    continue
                prediction = objective.predict(world, cid, actor=actor.id, projection=projection)
                if value['required'] and prediction['failure']['state'] != 'false':
                    fail('required_condition_conflict')
                elif commitment and prediction['failure']['state'] == 'true' and value['priority'] >= commitment['priority']:
                    fail('higher_priority_harm')
        result.update(forecast=forecast, eta=hops+remaining, priority=commitment['priority'] if commitment else 0,
                      required=commitment['required'] if commitment else False)
        if not result['excluded']:
            score = (20 + 4 * actor.traits['diligence']) * view['confidence'] / max(1, hops+remaining)
            score /= 1 + sum(c['priority'] for c in soft_harms) / max(1, commitment['priority'])
            if self.active.get(actor.id, {}).get('action') == spec['id']: score *= 2
            result['weight'] = score
            result['next_zone'] = path.route[1] if path else None
        return result

    def options(self, world, actor, turn, day=1):
        return [self._option(world, actor, spec, turn, day) for spec in self.actions.values()]

    def candidates(self, world, actor, turn, day=1):
        from engine.actions import Action
        from engine.phase2 import available_verbs
        allowed = available_verbs(world, actor, turn=turn, day=day)
        if actor.vitality not in {'alive', 'revived'}: return []
        if world.planner is not None and world.planner.owns(actor): return []
        options = self.options(world, actor, turn, day)
        feasible = [o for o in options if not o['excluded']]
        best = max((o['priority'] for o in feasible), default=0)
        weighted = []
        for option in feasible:
            if option['priority'] != best: continue
            spec = self.actions[option['action']]
            reason = {'condition': option['condition'], 'priority': best, 'signal': option['signal'],
                      'forecast': option['forecast'], 'eta': option['eta'],
                      'alternatives': [{'action': o['action'], 'excluded': o['excluded']} for o in options]}
            if option['next_zone'] is not None:
                verb, args = 'move', (option['next_zone'],)
                path = world.reachable_paths(actor).get(args[0])
                if path is None or path.cost > actor.stamina: continue
                meta = {'dest': args[0], 'cost': path.cost, 'hops': path.hops, 'route': list(path.route)}
            else:
                verb, args, meta = 'respond', (spec['id'],), {}
            if 'objective_view' in option:
                reason.update(objective_view=option['objective_view'], objective_forecast=option['objective_forecast'])
            meta.update(response_plan=spec['id'], response_reason=reason)
            if verb in allowed and world.genre_allows(verb):
                weighted.append((Action(verb, args, meta), option['weight']))
        # Inspection is local, and refreshes stale beliefs without inventing remote knowledge.
        if 'inspect_response' in allowed:
            for fact, signal in self.signals.items():
                if self.can_observe(actor, signal['key']) and self._commitment(world, actor, signal['condition']):
                    weighted.append((Action('inspect_response', (fact,)), .2))
        for rid, request in sorted(self.requests.items()):
            if request['to'] == actor.id and request['status'] == 'delivered' and 'answer_response' in allowed:
                option = next(o for o in options if o['action'] == request['action'])
                better = any(o['priority'] > option['priority'] for o in feasible)
                answer = 'accept' if not option['excluded'] and not better else 'refuse'
                weighted.append((Action('answer_response', (rid, answer), {'response_reason': option}), 60))
        if 'request_response' in allowed:
            for option in options:
                spec = self.actions[option['action']]
                if spec['signal'] is None: continue  # v2 proactive delegation is not an implicit testimony channel.
                if not self._commitment(world, actor, spec['condition']) or option['signal']['status'] != 'danger': continue
                if REQUEST_BLOCKERS.intersection(option['excluded']): continue
                for (sender, target), delay in sorted(self.channels.items()):
                    key = (actor.id, target, spec['id'], option['signal'].get('observed_at'))
                    if sender != actor.id or target not in spec['actors'] or key in self.sent: continue
                    # Do not inspect the other person's private goals, position or stamina.
                    weight = 4 + 4 * actor.traits['social']
                    if option['excluded']: weight *= 3
                    weighted.append((Action('request_response', (target, spec['id']),
                        {'response_reason': {'signal': option['signal'], 'condition': spec['condition'],
                                             'channel_delay': delay}}), weight))
        return weighted

    def execute(self, world, actor, action, turn, day):
        from engine.subject import Belief
        from engine.phase2 import available_verbs
        invalid = lambda reason: ('invalid', {'reason': reason}, [])
        if actor.vitality not in {'alive', 'revived'}: return invalid('actor_unavailable')
        if action.verb not in available_verbs(world, actor, turn=turn, day=day) or not world.genre_allows(action.verb):
            return invalid('response_verb_unavailable')
        if action.verb == 'inspect_response':
            if len(action.args) != 1 or action.args[0] not in self.signals: return invalid('unknown_signal')
            fact = action.args[0]; signal = self.signals[fact]
            if not self.can_observe(actor, signal['key']): return invalid('not_at_observation_site')
            actor.beliefs[fact] = Belief(self.state[signal['key']], 1)
            self._observe(actor, fact, turn, 'direct')
            return 'inspected', {'response': {'observation': self.view(world, actor, fact, turn)}}, []
        if action.verb == 'request_response':
            if len(action.args) != 2: return invalid('invalid_request')
            target, sid = action.args
            if (actor.id, target) not in self.channels or sid not in self.actions: return invalid('channel_or_action_unavailable')
            spec = self.actions[sid]
            if spec['signal'] is None: return invalid('proactive_request_requires_explicit_message')
            view = self.view(world, actor, spec['signal'], turn)
            assessment = self._option(world, actor, spec, turn, day)
            if REQUEST_BLOCKERS.intersection(assessment['excluded']): return invalid('request_conflicts_with_own_conditions')
            key = (actor.id, target, sid, view.get('observed_at'))
            if (target not in spec['actors'] or not self._commitment(world, actor, spec['condition'])
                    or view['status'] != 'danger' or key in self.sent): return invalid('request_not_justified')
            rid = 'request-' + str(len(self.requests)+1)
            self.sent.add(key)
            self.requests[rid] = {'from': actor.id, 'to': target, 'action': sid, 'status': 'in_transit',
                                  'sent_at': turn, 'known_reply': None}
            self.mail.append({'kind': 'request', 'from': actor.id, 'to': target, 'request': rid,
                'fact': spec['signal'], 'value': view['value'], 'confidence': view['confidence'],
                'observed_at': view['observed_at'], 'due': turn+self.channels[(actor.id, target)]})
            return 'requested', {'response': {'request': rid, 'status': 'in_transit', 'signal': view}}, []
        if action.verb == 'answer_response':
            if len(action.args) != 2: return invalid('invalid_answer')
            rid, answer = action.args; request = self.requests.get(rid)
            if request is None or request['to'] != actor.id or request['status'] != 'delivered' or answer not in {'accept', 'refuse'}:
                return invalid('request_not_available')
            option = self._option(world, actor, self.actions[request['action']], turn, day)
            better = any(not o['excluded'] and o['priority'] > option['priority'] for o in self.options(world, actor, turn, day))
            if answer == 'accept' and (option['excluded'] or better): return invalid('acceptance_not_feasible')
            request['status'] = answer
            if answer == 'accept': self.active[actor.id] = {'action': request['action'], 'since': turn, 'request': rid}
            if (actor.id, request['from']) in self.channels:
                self.mail.append({'kind': 'reply', 'from': actor.id, 'to': request['from'], 'request': rid,
                                  'reply': answer, 'due': turn+self.channels[(actor.id, request['from'])]})
            return 'answered', {'response': {'request': rid, 'answer': answer, 'assessment': option}}, []
        if action.verb != 'respond' or len(action.args) != 1 or action.args[0] not in self.actions:
            return invalid('unknown_response_action')
        spec = self.actions[action.args[0]]
        option = (world.planner.validate(world, actor, action, turn)
                  if world.planner is not None and action.meta.get('goal_plan')
                  else self._option(world, actor, spec, turn, day))
        if option['excluded']: return invalid(','.join(option['excluded']))
        if actor.zone != spec['zone']: return invalid('not_at_response_site')
        if any(self.state[k] != v for k, v in spec['preconditions'].items()):
            self.progress.pop((actor.id, spec['id']), None)
            if action.meta.get('goal_plan'):
                self.active.pop(actor.id, None)
            return 'failed', {'response': {'action': spec['id'], 'reason': 'actual_preconditions_changed', 'assessment': option}}, []
        actor.change_stamina(-spec['cost'])
        key = (actor.id, spec['id'])
        progress = self.progress.get(key, 0) + 1
        self.progress[key] = progress
        self.active[actor.id] = {'action': spec['id'], 'since': turn}
        if action.meta.get('goal_plan'):
            self.active[actor.id]['planning_root'] = action.meta['goal_plan']['root']
        details = {'action': spec['id'], 'label': spec['label'], 'progress': progress, 'steps': spec['steps'],
                   'assessment': option, 'effects': []}
        if progress < spec['steps']: return 'working', {'response': details}, []
        self._apply(world, spec['effects'], turn)
        signal = self.signals.get(spec['signal'])
        if signal is not None and self.can_observe(actor, signal['key']):
            actor.beliefs[spec['signal']] = Belief(self.state[signal['key']], 1)
            self._observe(actor, spec['signal'], turn, 'direct_execution')
        self.active.pop(actor.id, None); self.progress.pop(key, None)
        for rid, request in self.requests.items():
            if request['to'] == actor.id and request['action'] == spec['id'] and request['status'] == 'accept':
                request['status'] = 'completed'
                if (actor.id, request['from']) in self.channels:
                    self.mail.append({'kind': 'reply', 'from': actor.id, 'to': request['from'], 'request': rid,
                        'reply': 'completed', 'fact': spec['signal'], 'value': self.state[signal['key']],
                        'confidence': 1.0, 'observed_at': turn,
                        'due': turn+self.channels[(actor.id, request['from'])]})
        details['effects'] = deepcopy(spec['effects'])
        return 'responded', {'response': details}, []

    def after_action(self, world, actor, action, result, details, turn):
        if result == 'moved' and action.meta.get('response_plan') in self.actions:
            self.active[actor.id] = {'action': action.meta['response_plan'], 'since': turn}
        if action.verb == 'share_knowledge' and details.get('topic') in self.signals and details.get('outcome') != 'rejected':
            target = world.subjects.get(details.get('target'))
            if target is not None and result != 'invalid':
                fact = details['topic']; previous = self.observations.get((actor.id, fact), {})
                self._observe(target, fact, turn, actor.id, observed_at=previous.get('observed_at', 0))
        if action.verb == 'investigate' and result != 'invalid':
            for fact in self.signals:
                if fact in details.get('learned', []) or any(b.get('fact') == fact for b in details.get('beliefs', [])):
                    self._observe(actor, fact, turn, 'investigation')
        self.record(world, turn)
        if action.meta.get('response_reason'):
            details.setdefault('response', {})['choice_basis'] = deepcopy(action.meta['response_reason'])
        if action.verb in VERBS or action.meta.get('response_plan'):
            details.setdefault('response', {})['world_report'] = world.concerns.world_report(world)

    def snapshot(self):
        return {'version': self.config['version'], 'state': deepcopy(self.state), 'active': deepcopy(self.active),
                'requests': deepcopy(self.requests), 'mail': deepcopy(self.mail),
                'temporal': deepcopy(self.temporal), 'history': deepcopy(self.history),
                'observations': [{'actor': a, 'fact': f, **deepcopy(v)}
                                 for (a, f), v in sorted(self.observations.items())]}
