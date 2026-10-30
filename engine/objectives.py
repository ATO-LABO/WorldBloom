"""Shared achievement/failure/period rules, evaluated separately from beliefs.

Expressions refer to existing instantaneous concern predicates. They never
refer to another objective's terminal history or execute authored Python.
"""
from copy import deepcopy

from engine.concerns import coordinates, identifier, shape


def references(expression, *, depth=0, budget=None):
    budget = [64] if budget is None else budget
    budget[0] -= 1
    if depth > 4 or budget[0] < 0 or not isinstance(expression, dict) or len(expression) != 1:
        raise ValueError('Objective expression must be a bounded predicate tree')
    kind, value = next(iter(expression.items()))
    if kind == 'condition':
        return {identifier(value)}
    if kind == 'constant':
        if type(value) is not bool:
            raise ValueError('Objective constant must be boolean')
        return set()
    if kind == 'not':
        return references(value, depth=depth+1, budget=budget)
    if kind not in {'all', 'any'} or not isinstance(value, list) or not 1 <= len(value) <= 16:
        raise ValueError('Unknown/empty objective expression')
    result = set()
    for child in value:
        result.update(references(child, depth=depth+1, budget=budget))
    return result


class Objectives:
    def __init__(self, raw):
        shape(raw, {'version', 'definitions'})
        if type(raw['version']) is not int or raw['version'] not in (1, 2):
            raise ValueError('Unsupported objectives version')
        if not isinstance(raw['definitions'], list) or not 1 <= len(raw['definitions']) <= 32:
            raise ValueError('Bounded objective definitions required')
        self.config = deepcopy(raw)
        self.definitions = {}
        for spec in self.config['definitions']:
            shape(spec, {'condition', 'success', 'failure', 'period'} | ({'completion'} if 'completion' in spec else set()))
            if (not isinstance(spec.get('completion','first_success'),str)
                    or spec.get('completion','first_success') not in {'first_success','period_end'}):
                raise ValueError('Unknown objective completion policy')
            cid = identifier(spec['condition'])
            if cid in self.definitions:
                raise ValueError('Duplicate objective condition')
            if cid not in references(spec['success']):
                raise ValueError('Achievement expression must include its valued condition')
            references(spec['failure'])
            shape(spec['period'], {'start', 'end'})
            start, end = spec['period']['start'], spec['period']['end']
            if raw['version'] == 2:
                for point in (start, end):
                    shape(point, {'date', 'time'})
                    identifier(point['date']); identifier(point['time'])
            elif type(start) is not int or type(end) is not int or not 1 <= start <= end <= 10000:
                raise ValueError('Invalid objective period')
            self.definitions[cid] = deepcopy(spec)
        self.world_records = {}
        self.actor_records = {}
        self.current_turn = 0
        self.last_closed = -1 if raw['version'] == 2 else 0

    def bind(self, world):
        if world.concerns is None:
            raise ValueError('Objectives require shared condition definitions')
        self.calendar_mode = self.config['version'] == 2
        if self.calendar_mode:
            if world.planner is None:
                raise ValueError('Calendar objectives require the declared shared planning integration')
            if (world.slot_execution or {}).get('mode') != 'calendar_promises_v2':
                raise ValueError('Calendar objectives require the minute runtime')
            from engine.calendar_v2 import compile_calendar, minute_for
            contract = world.slot_contract
            calendar = compile_calendar(contract['calendar'], timezone=contract['timezone'], scheduled_events=list(world.scheduled_events))
            first = minute_for(calendar, calendar['start_date'], contract['start_time'])
            last = minute_for(calendar, calendar['end_date'], contract['end_time'])
            for raw_spec in self.config['definitions']:
                spec = self.definitions[raw_spec['condition']]
                spec['period'] = {key: minute_for(calendar, value['date'], value['time']) for key, value in raw_spec['period'].items()}
                if not first <= spec['period']['start'] <= spec['period']['end'] <= last:
                    raise ValueError('Calendar objective outside observation range')
        elif world.slot_execution is not None:
            raise ValueError('Objectives v1 supports ordinary instant execution only')
        for cid, spec in self.definitions.items():
            refs = references(spec['success']) | references(spec['failure']) | {cid}
            if not refs <= set(world.concerns.conditions):
                raise ValueError('Unknown objective condition reference: ' + ', '.join(sorted(refs-set(world.concerns.conditions))))
            if not self.calendar_mode and spec['period']['end'] > world.days * len(world.slots):
                raise ValueError('Objective period exceeds the world time range')
            if world.responses is not None and cid in world.responses.windows:
                raise ValueError('An objective and a legacy response window cannot judge the same condition')

    def expression(self, world, expression, *, actor=None, projection=None, justified=True):
        kind, value = next(iter(expression.items()))
        if kind == 'condition':
            result = world.concerns.evaluate(world, value, actor=actor, projection=projection, justified=justified)
            return {key: deepcopy(result[key]) for key in ('state', 'confidence', 'sources')}
        if kind == 'constant':
            return {'state': 'true' if value else 'false', 'confidence': 1.0, 'sources': ['declared_constant']}
        if kind == 'not':
            result = self.expression(world, value, actor=actor, projection=projection, justified=justified)
            return dict(result, state={'true': 'false', 'false': 'true', 'unknown': 'unknown'}[result['state']])
        values = [self.expression(world, child, actor=actor, projection=projection, justified=justified) for child in value]
        decisive = 'false' if kind == 'all' else 'true'
        selected = [v for v in values if v['state'] == decisive]
        if selected:
            state = decisive
            confidence = max(v['confidence'] for v in selected)
            evidence = [max(selected, key=lambda v:(v['confidence'], 'belief' not in v['sources']))]
        elif any(v['state'] == 'unknown' for v in values):
            state, confidence = 'unknown', 0.0
            evidence = values
        else:
            state = 'true' if kind == 'all' else 'false'
            confidence = min(v['confidence'] for v in values)
            evidence = values
        return {'state': state, 'confidence': confidence,
                'sources': sorted({s for v in evidence for s in v['sources']})}

    def predict(self, world, cid, *, actor=None, projection=None, justified=True):
        spec = self.definitions[cid]
        return {'condition': cid, 'mode': world.concerns.conditions[cid]['mode'], 'period': deepcopy(spec['period']),
                **({'completion':spec['completion']} if 'completion' in spec else {}),
                **{key: self.expression(world, spec[key], actor=actor, projection=projection, justified=justified)
                   for key in ('success', 'failure')}}

    def affected_coordinates(self, world, cid):
        spec = self.definitions[cid]
        return {key for ref in references(spec['success']) | references(spec['failure'])
                for key in coordinates(world.concerns.conditions[ref]['test'])}

    @staticmethod
    def _new():
        return {'status': 'not_started', 'decided_at': None, 'reason': None,
                'confidence': 0.0, 'observation_gap': False, 'last_observed': None,
                'minimum_confidence': 1.0, 'period_sources': []}

    def _reconsider_hearsay(self, record, prediction, turn):
        # A mistaken report is not an irreversible event. Directly witnessed
        # terminal history stays fixed; tentative recognition may be corrected.
        if (turn <= prediction['period']['end'] and record['status'] in {'achieved', 'failed'} and 'belief' in record.get('sources', [])
                and record.get('basis') != {key: prediction[key] for key in ('success', 'failure')}):
            previous = {k:deepcopy(record[k]) for k in ('status','decided_at','reason','confidence')}
            record.clear();record.update(self._new(), observation_gap=True, revised_from=previous)

    def _advance(self, record, prediction, turn, *, close):
        # Terminal judgments retain their first cause even if the state is repaired.
        if record['status'] in {'achieved', 'failed'}:
            return
        start, end = (prediction['period'][k] for k in ('start', 'end'))
        if turn < start:
            return
        if turn > end:
            record.update(status='unknown', reason='period_not_observed', observation_gap=True)
            return
        previous = record['last_observed']
        if ((previous is None and turn > start) or (previous is not None and turn > previous + 1 and not getattr(self, 'calendar_mode', False))):
            record['observation_gap'] = True
        record['last_observed'] = turn
        success, failure = (prediction[k] for k in ('success', 'failure'))
        if success['state'] == 'unknown' or failure['state'] == 'unknown':
            record['observation_gap'] = True
        record['minimum_confidence'] = min(record['minimum_confidence'], success['confidence'], failure['confidence'])
        record['period_sources'] = sorted(set(record['period_sources']) | set(success['sources']) | set(failure['sources']))
        record['status'] = 'pending'
        # Failure wins when both predicates hold at the same observation point.
        if failure['state'] == 'true':
            status, reason, confidence = 'failed', 'failure_condition', failure['confidence']
        elif prediction['mode'] == 'maintain' and success['state'] == 'false':
            status, reason, confidence = 'failed', 'maintenance_breach', success['confidence']
        elif (prediction['mode'] == 'achieve' and prediction.get('completion','first_success')=='first_success'
              and success['state'] == 'true' and failure['state'] == 'false'):
            status, reason, confidence = 'achieved', 'achievement_condition', min(success['confidence'], failure['confidence'])
        elif close and turn == end:
            if record['observation_gap']:
                record.update(status='unknown', reason='incomplete_observation')
                return
            status = 'achieved' if success['state'] == 'true' and failure['state'] == 'false' else 'failed'
            reason = 'period_maintained' if status == 'achieved' else 'deadline_unmet'
            confidence = min(success['confidence'], failure['confidence'])
            if prediction['mode'] == 'maintain':
                confidence = min(confidence, record['minimum_confidence'])
        else:
            return
        evidence = [failure] if reason == 'failure_condition' else [success] if reason == 'maintenance_breach' else [success, failure]
        sources = sorted({s for item in evidence for s in item['sources']})
        if reason == 'period_maintained': sources = deepcopy(record['period_sources'])
        record.update(status=status, decided_at=turn, reason=reason, confidence=confidence,
                      sources=sources,
                      basis={key:deepcopy(prediction[key]) for key in ('success','failure')})

    def record(self, world, turn, *, close=False):
        if type(turn) is not int or turn < self.current_turn or turn <= self.last_closed:
            return []
        self.current_turn = turn
        changed = []
        for cid in self.definitions:
            prediction = self.predict(world, cid)
            record = self.world_records.setdefault(cid, self._new())
            before = record['status']
            self._advance(record, prediction, turn, close=close)
            if record['status'] != before:
                changed.append(cid)
            for actor in sorted(world.subjects):
                if world.subjects[actor].vitality not in {'alive', 'revived'}:
                    continue
                own = self.actor_records.setdefault(actor, {}).setdefault(cid, self._new())
                estimate = self.predict(world, cid, actor=actor)
                self._reconsider_hearsay(own, estimate, turn)
                self._advance(own, estimate, turn, close=close)
        if close:
            self.last_closed = turn
        reports = {entry['condition']: entry for entry in self.report(world)}
        return [{'verb': 'objective_outcome', 'details': {'objective_outcome': reports[cid]}}
                for cid in changed]

    def actor_view(self, world, actor, cid, turn):
        # Never use world_records, even if an author knows the outcome already.
        record = deepcopy(self.actor_records.get(actor, {}).get(cid, self._new()))
        prediction = self.predict(world, cid, actor=actor)
        self._reconsider_hearsay(record, prediction, turn)
        self._advance(record, prediction, turn, close=False)
        return dict(record, condition=cid, period=deepcopy(prediction['period']),
                    success=prediction['success'], failure=prediction['failure'])

    def report(self, world):
        return [dict(deepcopy(self.world_records.get(cid, self._new())), condition=cid,
                     label=world.concerns.conditions[cid]['label'], period=deepcopy(spec['period']))
                for cid, spec in sorted(self.definitions.items())]

    def snapshot(self, world):
        return {'version': 1, 'turn': self.current_turn, 'last_closed': self.last_closed,
                'world': self.report(world), 'actors': deepcopy(self.actor_records)}
