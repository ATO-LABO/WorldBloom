"""Opt-in duration providers over the common action-window lifecycle.

No story identifiers or alternate chooser. Domain effects remain in VerbEngine.
"""
from copy import deepcopy

from engine.action_lifecycle import ActionLifecycle
from engine.concerns import shape


def step_timing(world, actor, verb, args, duration, cost, *, turn, elapsed=0, hops=1):
    runtime = getattr(world, 'action_timing', None)
    if runtime is None: return duration, cost, max(0, duration - 1)
    return runtime.plan_step(actor, verb, args, duration, cost, turn=turn, elapsed=elapsed, hops=hops)


class ActionTiming:
    def __init__(self, raw):
        shape(raw, {'version', 'rules'})
        if type(raw['version']) is not int or raw['version'] != 1:
            raise ValueError('Unsupported action timing version')
        if not isinstance(raw['rules'], list) or not 1 <= len(raw['rules']) <= 32:
            raise ValueError('Bounded action timing rules required')
        self.config = deepcopy(raw)
        self.rules = {}
        for rule in raw['rules']:
            shape(rule, {'verb', 'duration', 'interruptible'})
            verb, duration = rule['verb'], rule['duration']
            from engine.social_planning import VERBS as SOCIAL_VERBS
            if not isinstance(verb, str) or verb not in {'move', 'respond', 'rest'} | SOCIAL_VERBS or verb in self.rules:
                raise ValueError('Unknown or duplicate timed action provider')
            if not (type(duration) is int and 1 <= duration <= 32 or verb == 'respond' and duration == 'work_steps'
                    or verb=='await_response' and duration=='protocol_steps'):
                raise ValueError('Invalid action duration')
            if type(rule['interruptible']) is not bool or verb == 'move' and rule['interruptible']:
                raise ValueError('Movement needs an intermediate-location model before interruption')
            self.rules[verb] = deepcopy(rule)
        self.pending = {}
        self.history = []
        self.completing = None
        self.sequence = 0

    def bind(self, world):
        if world.slot_execution is not None and (getattr(world,'social_planner',None) is None or
                any(v in {'move','respond','rest'} for v in self.rules)):
            raise ValueError('Action timing rules currently integrate ordinary logical execution only')
        if 'respond' in self.rules and world.responses is None:
            raise ValueError('Timed response provider needs response definitions')
        if 'await_response' in self.rules and world.social_planner is None:
            raise ValueError('Reply waits require an explicit social protocol')

    def attach(self, simulation):
        self.sim = simulation
        self.lifecycle = ActionLifecycle(simulation.time_kernel, lambda: self.pending)

    def plan_step(self, actor, verb, args, duration, cost, *, turn, elapsed=0, hops=1):
        rule = self.rules.get(verb)
        if rule is None: return duration, cost, max(0, duration - 1)
        pending = self.pending.get(actor.id)
        if not elapsed and pending and (pending['action'].verb, pending['action'].args) == (verb, tuple(args)):
            remaining = max(0, pending['finish'] - (turn - 1))
            return remaining, max(0, pending['cost'] - pending['spent']), remaining
        duration = duration if rule['duration'] in {'work_steps','protocol_steps'} else rule['duration'] * (hops if verb == 'move' else 1)
        return duration, cost, duration

    def prepaid(self, actor, action):
        return self.completing is not None and self.completing['actor'] == actor.id and self.completing['action'] is action

    def _spec(self, world, actor, action, turn):
        from engine.phase2 import available_verbs
        rule = self.rules[action.verb]
        if actor.vitality not in {'alive', 'revived'} or action.verb not in available_verbs(world, actor, turn=turn,
                day=(turn - 1)//len(world.slots) + 1) or not world.genre_allows(action.verb):
            raise ValueError('actor_or_action_unavailable')
        if action.verb == 'move':
            if len(action.args) != 1: raise ValueError('invalid_destination')
            path = world.reachable_paths(actor).get(action.args[0])
            if path is None: raise ValueError('unreachable')
            duration, cost, _ = self.plan_step(actor, 'move', action.args, 1, path.cost, turn=turn, hops=path.hops)
            label = str(action.args[0]) + 'へ移動'
        elif action.verb == 'respond':
            if len(action.args) != 1 or action.args[0] not in world.responses.actions: raise ValueError('unknown_response_action')
            spec = world.responses.actions[action.args[0]]
            if actor.id not in spec['actors'] or actor.zone != spec['zone']: raise ValueError('response_site_or_capability_unavailable')
            if set(spec['requires']) - actor.knowledge or any(not actor.has_item(i,n) for i,n in spec['items'].items()):
                raise ValueError('response_requirements_unavailable')
            duration, cost, _ = self.plan_step(actor, 'respond', action.args, spec['steps'], spec['cost'] * spec['steps'], turn=turn)
            label = spec['label']
        elif action.verb=='rest':
            if action.args: raise ValueError('invalid_rest')
            duration, cost, _ = self.plan_step(actor, 'rest', (), 1, 0, turn=turn)
            label = '休息する'
        else:
            social=getattr(world,'social_planner',None)
            raw=social.wait_duration(world,action.args) if action.verb=='await_response' else 1
            duration,cost,_=self.plan_step(actor,action.verb,action.args,raw,0,turn=turn)
            label=(action.meta.get('goal_plan',{}).get('steps') or [{}])[0].get('label',action.verb)
        if actor.stamina < cost: raise ValueError('insufficient_stamina')
        return {'duration': duration, 'cost': cost, 'label': label, 'interruptible': rule['interruptible'],
                **({'route':list(path.route)} if action.verb=='move' else {})}

    def _reason(self, world, actor, pending):
        action = pending['action']
        if actor.vitality not in {'alive', 'revived'}: return 'actor_unavailable'
        if actor.zone != pending['origin']: return 'external_relocation'
        if action.verb == 'respond':
            spec = world.responses.actions[action.args[0]]
            if any(world.responses.state[k] != v for k,v in spec['preconditions'].items()):
                return 'actual_preconditions_changed'
            if set(spec['requires']) - actor.knowledge or any(not actor.has_item(i,n) for i,n in spec['items'].items()):
                return 'response_requirements_unavailable'
            auth = spec.get('authorization')
            if auth and not world.deliberation.decided(auth['proposal'], auth['status']): return 'authorization_pending'
        return None

    def _view(self, world, actor):
        return deepcopy(world.planner.view(world, actor)) if world.planner is not None and world.planner.owns(actor) else None

    def record(self, pending, phase, *, reason=None):
        return {'id': pending['id'], 'phase': phase, 'actor': pending['actor'], 'verb': pending['action'].verb,
            'args': list(pending['action'].args), 'label': pending['label'], 'origin': pending['origin'],
            'start': pending['start'], 'finish': pending['finish'], 'unit': self.sim.time_kernel.axis.unit,
            'start_label': self.sim.time_kernel.axis.label(pending['start']),
            'finish_label': self.sim.time_kernel.axis.label(pending['finish']),
            'selected_at': pending['start'] + 1, 'duration': pending['finish']-pending['start'],
            'spent': pending['spent'], 'cost': pending['cost'], 'interruptible': pending['interruptible'], 'reason': reason}

    def start(self, actor, action):
        world, turn = self.sim.world, self.sim.turn
        if actor.id in self.pending: return 'invalid', {'reason': 'actor_busy'}, []
        try:
            spec = self._spec(world, actor, action, turn)
            window = self.sim.time_kernel.window(spec['duration'])
            if world.planner is not None and action.meta.get('goal_plan'):
                if world.planner.validate(world, actor, action, turn)['excluded']: raise ValueError('plan_no_longer_justified')
            elif action.verb == 'respond':
                option = world.responses._option(world, actor, world.responses.actions[action.args[0]], turn, self.sim.day)
                if option['excluded']: raise ValueError(','.join(option['excluded']))
        except ValueError as exc:
            return 'invalid', {'reason': str(exc)}, []
        self.sequence += 1
        pending = {'id': 'activity-' + str(self.sequence), 'actor': actor.id, 'action': action,
            'origin': actor.zone, 'start': window.start, 'finish': window.finish, 'cursor': window.start,
            'cost': spec['cost'], 'spent': 0.0, 'label': spec['label'], 'interruptible': spec['interruptible']}
        if 'route' in spec: pending['route']=spec['route']
        reason = self._reason(world, actor, pending)
        if reason:
            details = {'response': {'action': action.args[0], 'reason': reason}} if action.verb == 'respond' else {'reason': reason}
            if world.social_planner is not None: world.social_planner.after_action(world,actor,action,'failed',details,turn)
            if world.planner is not None: world.planner.after_action(world, actor, action, 'failed', details, turn)
            return 'failed', details, []
        self.lifecycle.start(actor.id, pending, spec['duration'])
        actor.in_transit = action.verb == 'move'
        details = {'activity': self.record(pending, 'started')}
        if world.planner is not None: world.planner.after_action(world, actor, action, 'started', details, turn)
        pending['view'] = self._view(world, actor)
        self.history.append(deepcopy(details['activity']))
        return 'started', details, []

    def advance(self, writer):
        sim, world, now = self.sim, self.sim.world, self.sim.time_kernel.now
        for aid in sorted(list(self.pending)):
            pending = self.pending[aid]; actor = world.subjects[aid]; action = pending['action']
            if now <= pending['cursor']: continue
            before = sim._capture()
            fraction = (min(now, pending['finish']) - pending['start']) / (pending['finish'] - pending['start'])
            due = max(0, round(pending['cost'] * fraction - pending['spent'], 4))
            paid = -actor.change_stamina(-due)
            pending['spent'] = round(pending['spent'] + paid, 4); pending['cursor'] = now
            sim.verb_engine._update_exhausted(actor)
            reason = self._reason(world, actor, pending)
            if paid + 0.0001 < due: reason = 'insufficient_stamina'
            phase, result, details, markers = 'progress', 'ongoing', {}, []
            if reason:
                phase, result = 'failed', 'failed'
                details = {'response': {'action': action.args[0], 'reason': reason}} if action.verb == 'respond' else {'reason': reason}
            elif now >= pending['finish']:
                self.completing = pending
                try: result, details, markers = sim.verb_engine.execute(actor, action, turn=sim.turn, day=sim.day)
                finally: self.completing = None
                sim.verb_engine._update_exhausted(actor)
                phase = 'failed' if result in {'invalid','failed'} else 'completed'
            if phase != 'progress':
                self.lifecycle.interrupt(aid); actor.in_transit = False
                if phase=='failed' and 'social_event' not in details and world.social_planner is not None:
                    world.social_planner.after_action(world,actor,action,result,details,sim.turn)
                if phase == 'failed' and world.planner is not None and 'planning' not in details:
                    world.planner.after_action(world, actor, action, result, details, sim.turn)
            details['activity'] = self.record(pending, phase, reason=reason)
            self.history.append(deepcopy(details['activity']))
            sim._write_activity(writer, actor, action, result, details, markers, before)

    def reconsider(self, actor, writer):
        world = self.sim.world; pending = self.pending.get(actor.id)
        if pending is None or not pending['interruptible'] or pending['view'] is None: return
        current = self._view(world, actor)
        if current == pending['view']: return
        prior_view = pending['view']
        pending['view'] = current
        plans = world.planner.candidates(world, actor, self.sim.turn, self.sim.day)
        if any((a.verb,a.args)==(pending['action'].verb,pending['action'].args) for a,_ in plans): return
        before = self.sim._capture(); self.lifecycle.interrupt(actor.id); actor.in_transit = False
        details = {'activity': self.record(pending, 'interrupted', reason='new_actor_information')}
        details['activity']['new_information'] = [{'coordinate': list(k), 'before': prior_view.get(k), 'after': current.get(k)}
            for k in sorted(set(prior_view) | set(current)) if prior_view.get(k) != current.get(k)]
        world.planner.after_action(world, actor, pending['action'], 'interrupted', details, self.sim.turn)
        self.history.append(deepcopy(details['activity']))
        self.sim._write_activity(writer, actor, pending['action'], 'interrupted', details, [], before)

    def stop(self, writer):
        for aid, pending in list(self.pending.items()):
            actor = self.sim.subjects[aid]
            details = {'activity': self.record(pending, 'unfinished', reason='observation_stopped')}
            self.history.append(deepcopy(details['activity']))
            self.sim._write_activity(writer, actor, pending['action'], 'unfinished', details, [], self.sim._capture())

    def snapshot(self):
        return {'version': 1, 'pending': [self.record(p,'progress') for p in self.pending.values()], 'history': deepcopy(self.history)}
