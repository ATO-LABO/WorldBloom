"""Bounded, declarative state observations attached to registered events.

Observers inspect facts only; they never choose actions or infer consent,
performance, motivation, or story outcomes. Any literal is authored metadata.
"""
from __future__ import annotations

from copy import deepcopy
import math


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 200


def validate_observers(observers, calendar, actors, places, tasks):
    if not isinstance(observers, list) or len(observers) > 32:
        raise ValueError('Observers must be a bounded list')
    events = {event['id'] for event in calendar['events']}
    reserved_prefixes = ('scheduled:', 'calendar:', 'facility:', 'start:', 'complete:',
                         'reject:', 'interrupt:', 'work:', 'promise:', 'appraisal:',
                         'received:', 'read:', 'skip:', 'decision:', 'promise-effect-', 'unit:', 'delivery:')
    identities = set()
    nodes = 0

    def fields(value, depth, top=False):
        if not isinstance(value, dict) or not value or len(value) > 100:
            raise ValueError('Observer fields must be a bounded nonempty mapping')
        reserved = {'at', 'ident', 'verb', 'actor', 'result', 'caused_by'}
        for key, expression in value.items():
            if not _text(key) or (top and key in reserved):
                raise ValueError('Invalid or reserved observer output field')
            validate(expression, depth + 1)

    def validate(expression, depth):
        nonlocal nodes
        nodes += 1
        if depth > 8 or nodes > 1000 or not isinstance(expression, dict):
            raise ValueError('Observer expression exceeds its bounded schema')
        if set(expression) == {'literal'}:
            literal = expression['literal']
            if not (literal is None or type(literal) in (bool, int, float, str)):
                raise ValueError('Observer literal must be a JSON scalar')
            if type(literal) is float and not math.isfinite(literal):
                raise ValueError('Observer literal must be finite')
            if type(literal) is int and not -10**15 <= literal <= 10**15:
                raise ValueError('Observer integer literal is out of bounds')
            if isinstance(literal, str) and len(literal) > 1000:
                raise ValueError('Observer literal is too long')
        elif set(expression) == {'group'}:
            fields(expression['group'], depth)
        elif set(expression) == {'check', 'actor', 'place'} and expression['check'] == 'at':
            if not _text(expression['actor']) or expression['actor'] not in actors or not _text(expression['place']) or expression['place'] not in places:
                raise ValueError('Observer references an unknown actor/place')
        elif set(expression) == {'check', 'task_id'} and expression['check'] == 'task_complete':
            if not _text(expression['task_id']) or expression['task_id'] not in tasks:
                raise ValueError('Observer references an unknown task')
        else:
            raise ValueError('Unsupported observer expression')

    for observer in observers:
        if not isinstance(observer, dict) or set(observer) != {'id', 'event_id', 'verb', 'fields'}:
            raise ValueError('Invalid observer definition')
        if (not _text(observer['id']) or observer['id'] in identities or observer['id'].startswith(reserved_prefixes)
                or not _text(observer['event_id']) or observer['event_id'] not in events
                or not _text(observer['verb']) or observer['verb'] in ('action_started', 'action_completed', 'work_interrupted', 'work_unit_completed')):
            raise ValueError('Observer identity/event must be distinct and registered')
        identities.add(observer['id'])
        fields(observer['fields'], 0, top=True)
    return deepcopy(observers)


def evaluate_observer(observer, people, ledger, tasks, at):
    def evaluate(expression):
        if 'literal' in expression:
            return expression['literal']
        if 'group' in expression:
            return {key: evaluate(value) for key, value in expression['group'].items()}
        if expression['check'] == 'at':
            return people[expression['actor']].zone == expression['place']
        task = expression['task_id']
        return ledger['tasks'][task]['units'] >= tasks[task]['units']
    return {'at': at, **{key: evaluate(value) for key, value in observer['fields'].items()}}


def validate_observation_state(state, observers, calendar, rows, now):
    """Validate persisted observations against their exact emitted rows."""
    if not isinstance(state, dict):
        raise ValueError('Invalid observer checkpoint collection')
    definitions = {observer['id']: observer for observer in observers}
    if not set(state) <= set(definitions):
        raise ValueError('Unknown checkpoint observer')
    event_minutes = {event['id']: event['minute'] for event in calendar['events']}
    def shape(fields, values):
        if not isinstance(values, dict) or set(values) != set(fields):
            raise ValueError('Observation fields differ from their configured schema')
        for key, expression in fields.items():
            value = values[key]
            if 'group' in expression:
                shape(expression['group'], value)
            elif 'literal' in expression:
                if type(value) is not type(expression['literal']) or value != expression['literal']:
                    raise ValueError('Authored observation literal changed')
            elif type(value) is not bool:
                raise ValueError('Observation check must be a boolean fact')
    for identity, observer in definitions.items():
        matching = [row for row in rows if row.get('id') == identity]
        if identity not in state:
            if matching:
                raise ValueError('Missing checkpoint observation')
            continue
        value = state[identity]
        at = event_minutes[observer['event_id']]
        if (not isinstance(value, dict) or type(value.get('at')) is not int
                or value['at'] != at or at > now or len(matching) != 1
                or matching[0].get('verb') != observer['verb']
                or matching[0].get('absolute_minute') != at
                or matching[0].get('details') != {'caused_by': [], **value}):
            raise ValueError('Observation/event checkpoint mismatch')
        shape(observer['fields'], {key: item for key, item in value.items() if key != 'at'})
