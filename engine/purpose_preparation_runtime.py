"""Opt-in, actor-scoped preparation guidance over existing legal actions.

This adapter never adds a final goal, creates an action, executes a forecast,
changes character knowledge, or declares success. It backchains public
affordances to *next* legal choices and re-evaluates after the real transition.
The ordinary ending evaluator remains authoritative.
"""
from collections import deque
from copy import copy, deepcopy
import hashlib
import json
import math
from pathlib import Path
import re

FILENAME = 'purpose-preparation.json'
VERSION = 1
MAX_BYTES = 1048576
MAX_CANDIDATES = 512
MAX_BRANCHES = 64
MAX_DEPTH = 16
GUIDANCE_MULTIPLIER = 2.0


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate preparation JSON key')
        result[key] = value
    return result


def read_record(project):
    """Read once for GA freeze. False fixes absent/disabled semantics for trials."""
    base = Path(project).resolve()
    path = base / FILENAME
    if not path.exists() and not path.is_symlink():
        return False
    if path.is_symlink() or path.resolve().parent != base or not path.is_file():
        raise ValueError('Unsafe preparation source path')
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('Preparation settings exceed size limit')
    try:
        record = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_unique,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite preparation JSON')))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError('Invalid preparation JSON') from error
    if not isinstance(record, dict) or type(record.get('version')) is not int or record['version'] != VERSION or type(record.get('enabled')) is not bool:
        raise ValueError('Invalid preparation settings version/enabled')
    return record if record['enabled'] else False


def load_runtime(world, initial_people, provided=None):
    """None: read source; False: frozen off; dict: captured immutable source."""
    record = read_record(world.source.parent) if provided is None else deepcopy(provided)
    if record is False or record is None or isinstance(record, dict) and record.get('enabled') is False:
        return None
    return PurposePreparationRuntime(world, initial_people, record)


def _record_shape(record):
    """Storage-compatible history validation; old references need not be active."""
    from engine.purpose_preparation import digest
    def require(test):
        if not test: raise ValueError('Invalid preparation record/history shape')
    def shape(value, fields):
        require(isinstance(value, dict) and set(value) == set(fields))
    ident = lambda value: isinstance(value, str) and re.fullmatch(r'prep_[0-9a-f]{24}', value) is not None
    hashed = lambda value: isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None
    shape(record, {'version','enabled','root_id','root_when_hash','dependency_hash','managed_ids','candidates','pressure'})
    require(type(record['version']) is int and record['version'] == VERSION and type(record['enabled']) is bool)
    require(isinstance(record['root_id'], str) and bool(record['root_id']) and hashed(record['root_when_hash']) and hashed(record['dependency_hash']))
    selected = record['managed_ids']
    require(isinstance(selected, list) and len(selected) <= MAX_CANDIDATES and all(ident(i) for i in selected) and len(set(selected)) == len(selected))
    rows = record['candidates']; require(isinstance(rows, list) and len(rows) <= MAX_CANDIDATES)
    seen = set()
    for row in rows:
        require(isinstance(row, dict) and {'id','semantic_key','predicate','source','source_hash','supported'} <= set(row))
        require(ident(row['id']) and row['id'] not in seen and type(row['supported']) is bool)
        require(row['semantic_key'] == {'predicate': row['predicate']} and row['id'] == 'prep_'+digest(row['semantic_key'])[:24] and row['source_hash'] == digest(row['source']))
        seen.add(row['id'])
    require(set(selected) <= seen)
    pressure = record['pressure']; shape(pressure, {'enabled','entries'})
    require(type(pressure['enabled']) is bool and isinstance(pressure['entries'], list) and len(pressure['entries']) <= MAX_CANDIDATES)
    deadlines = set()
    for entry in pressure['entries']:
        require(isinstance(entry, dict) and {'candidate_id','day','slot','awareness'} <= set(entry) <= {'candidate_id','day','slot','awareness','estimate'})
        require(isinstance(entry['candidate_id'], str) and entry['candidate_id'] in seen and entry['candidate_id'] not in deadlines)
        deadlines.add(entry['candidate_id'])
        require(type(entry['day']) is int and entry['day'] >= 1 and isinstance(entry['slot'], str) and bool(entry['slot']))
        awareness = entry['awareness']; require(isinstance(awareness, dict) and awareness.get('kind') in {'public','known_fact'})
        shape(awareness, {'kind','fact_id'} if awareness['kind'] == 'known_fact' else {'kind'})
        if awareness['kind'] == 'known_fact': require(isinstance(awareness['fact_id'], str))
        if 'estimate' in entry:
            estimate = entry['estimate']; shape(estimate, {'duration','fact_id'})
            require(type(estimate['duration']) is int and estimate['duration'] >= 1 and isinstance(estimate['fact_id'], str))


def _key(action):
    return action.verb, tuple(action.args)


def _predicate_key(predicate):
    p = dict(predicate)
    if p.get('kind') == 'holds':
        p['kind'] = 'inventory'
        p.setdefault('minimum', 1)
    return _canonical(p)


def _branches(node):
    kind = node.get('kind')
    if kind not in {'all', 'any'}:
        if kind not in {'zone', 'holds'}:
            raise ValueError('Unsupported preparation inverse condition')
        return [[node]]
    result = [] if kind == 'any' else [[]]
    for child in node['children']:
        rows = _branches(child)
        result = result + rows if kind == 'any' else [left + right for left in result for right in rows]
        if len(result) > MAX_BRANCHES or any(len(row) > MAX_BRANCHES for row in result):
            raise ValueError('Preparation branch limit exceeded')
    return result


def actor_story_evidence(value, actor_id):
    """Whitelist the actor-known, actually applied correction for prose input."""
    if not isinstance(value, dict) or value.get('version') != VERSION or value.get('actor_id') != actor_id:
        return None
    pressure = value.get('deadline_pressure')
    if not isinstance(pressure, dict) or pressure.get('actor_id') != actor_id or pressure.get('is_actual_completion') is not False:
        return None
    multiplier = pressure.get('multiplier')
    if type(multiplier) not in (int, float) or not math.isfinite(multiplier) or not 1 < multiplier <= 4.000000001:
        return None
    integers = ('observed_turn', 'deadline_turn', 'deadline_day', 'estimated_duration', 'perceived_completion_turn', 'subjective_slack')
    if any(type(pressure.get(k)) is not int for k in integers) or pressure['estimated_duration'] < 1:
        return None
    refs = pressure.get('evidence_refs')
    if (not isinstance(refs, list) or not refs or len(refs) > 8 or
            any(not isinstance(ref, str) or len(ref) > 256 or not ref.startswith(('public:', 'fact:', 'estimate:')) for ref in refs)):
        return None
    safe = {k: deepcopy(pressure[k]) for k in (*integers, 'multiplier', 'evidence_refs')}
    safe.update(actor_id=actor_id, deadline_slot=str(pressure.get('deadline_slot', ''))[:120],
                deadline_boundary='end_of_selected_logical_round', is_actual_completion=False)
    if isinstance(pressure.get('deadline_date'), str):
        safe['deadline_date'] = pressure['deadline_date'][:10]
    return {'version': VERSION, 'actor_id': actor_id, 'deadline_pressure': safe}


class PurposePreparationRuntime:
    def __init__(self, world, initial_people, record):
        from engine.purpose_preparation import compile_plan
        if world.slot_execution is not None:
            raise ValueError('Preparation guidance supports ordinary execution only')
        if not isinstance(record, dict) or type(record.get('version')) is not int or record.get('version') != VERSION or record.get('enabled') is not True:
            raise ValueError('Explicit enabled preparation source required')
        if len(_canonical(record)) > MAX_BYTES:
            raise ValueError('Preparation settings exceed size limit')
        _record_shape(record)
        raw = deepcopy(getattr(world, 'purpose_preparation_definition', None))
        if not isinstance(raw, dict):
            raise ValueError('Preparation execution requires the captured world definition')
        raw['target_ending'] = [world.target_ending] if isinstance(world.target_ending, str) else list(world.target_ending)
        self.plan = compile_plan(raw, initial_people, record)
        if self.plan.get('needs_review') or not self.plan.get('enabled') or not self.plan.get('available'):
            raise ValueError('Preparation root/dependencies are stale or inverse planning is unsupported; review settings')
        self.world = world
        self.record = deepcopy(record)
        self.source_hash = hashlib.sha256(_canonical(record)).hexdigest()
        self.root = self.plan['root']
        self.branches = _branches(self.plan['tree'])
        self.candidates = {row['id']: row for row in self.plan['candidates'] if row.get('supported') and row.get('status') != 'stale'}
        self.pressure = self._pressure_entries(record)
        self.diagnostics = {}

    def header(self):
        return {'version': VERSION, 'root_id': self.root['id'], 'root_when_hash': self.root['when_hash'],
                'source_sha256': self.source_hash, 'scope': 'actor_public_next_legal_action_v1',
                'deadline_boundary': 'end_of_selected_logical_round', 'creates_final_goals': False}

    def _pressure_entries(self, record):
        pressure = self.plan.get('pressure')
        if not isinstance(pressure, dict) or type(pressure.get('enabled')) is not bool or not isinstance(pressure.get('entries'), list):
            raise ValueError('Invalid preparation pressure settings')
        if not pressure['enabled']:
            return []
        if len(pressure['entries']) > MAX_CANDIDATES:
            raise ValueError('Too many preparation deadlines')
        managed = set(record.get('managed_ids') or [])
        rows = []
        for entry in pressure['entries']:
            if not isinstance(entry, dict) or entry.get('candidate_id') not in managed or entry['candidate_id'] not in self.candidates:
                raise ValueError('Deadline must refer to a current managed preparation')
            day, slot = entry['deadline']['day'], entry['deadline']['slot']
            if type(day) is not int or not 1 <= day <= self.world.days or slot not in self.world.slots:
                raise ValueError('Preparation deadline outside the logical clock')
            awareness = entry.get('awareness')
            if not isinstance(awareness, dict) or awareness.get('kind') not in {'public', 'known_fact'}:
                raise ValueError('Explicit deadline awareness required')
            if awareness['kind'] == 'known_fact' and awareness.get('fact_id') not in self.world.facts:
                raise ValueError('Unknown deadline awareness fact')
            estimate = entry.get('estimate')
            if estimate is not None and (not isinstance(estimate, dict) or type(estimate.get('duration')) is not int
                    or not 1 <= estimate['duration'] <= self.world.days*len(self.world.slots) or estimate.get('fact_id') not in self.world.facts):
                raise ValueError('Explicit registered duration estimate required')
            rows.append({**deepcopy(entry), 'day': day, 'slot': slot, 'deadline_tick': (day-1)*len(self.world.slots) + self.world.slots.index(slot) + 1})
        return rows

    def _truth(self, predicate, actor):
        if predicate.get('actor') != actor.id:
            return None  # Never inspect a remote actor to choose this actor's plan.
        kind = predicate['kind']
        if kind == 'zone':
            return actor.zone == predicate['place']
        if kind in {'holds', 'inventory'}:
            return actor.inventory.get(predicate['item'], 0) >= predicate.get('minimum', 1)
        if kind == 'known':
            return predicate['fact'] in actor.knowledge
        return None

    def _diagnostic(self, actor, code):
        self.diagnostics.setdefault(actor.id, set()).add(code)

    def _matching(self, legal, verb, args=None):
        return [action for action, weight in legal if weight > 0 and action.verb == verb
                and (args is None or tuple(action.args) == tuple(args))]

    def _direct(self, legal, verb, args, chain, reason, uncertain=False):
        return [{'key': _key(a), 'chain': deepcopy(chain), 'reason': reason, 'uncertain': uncertain}
                for a in self._matching(legal, verb, args)]

    def _paths(self, actor, target):
        queue = deque([(actor.zone, [], frozenset({actor.zone}))])
        result, expanded = [], 0
        while queue and expanded < 2048 and len(result) < 8:
            zone, path, seen = queue.popleft(); expanded += 1
            if zone == target:
                result.append(path); continue
            if len(path) >= 12:
                continue
            for route in sorted(self.world.routes.get(zone, ()), key=lambda r: r.destination):
                if route.destination not in seen and route.destination in actor.range_zones:
                    queue.append((route.destination, path + [route], seen | {route.destination}))
        if queue:
            self._diagnostic(actor, 'public_route_search_limit')
        return result

    def _need(self, predicate, actor, legal, stack=(), chain=()):
        if self._truth(predicate, actor) is True:
            return []
        key = _predicate_key(predicate)
        if key in stack or len(stack) >= MAX_DEPTH:
            self._diagnostic(actor, 'preparation_cycle_or_depth_limit'); return []
        stack, chain = stack + (key,), chain + (predicate,)
        kind = predicate['kind']
        if predicate.get('actor') != actor.id:
            return []
        if kind == 'zone':
            target = predicate['place']
            direct = self._direct(legal, 'move', [target], chain, 'move_toward_known_requirement')
            if direct:
                return direct
            result = []
            for path in self._paths(actor, target):
                for route in path:
                    item = route.requires_item
                    if item and not actor.has_item(item):
                        result.extend(self._need({'kind': 'inventory', 'actor': actor.id, 'item': item, 'minimum': 1}, actor, legal, stack, chain))
                        break
                    conditions = getattr(route, 'requires_conditions', ())
                    unmet = []
                    for cid in conditions:
                        concerns = self.world.concerns
                        if concerns is None:
                            self._diagnostic(actor, 'route_condition_unknown'); continue
                        view = concerns.evaluate(self.world, cid, actor=actor.id)
                        if view['state'] == 'true':
                            continue
                        test = concerns.conditions[cid]['test']
                        translated = {'kind': 'inventory', 'actor': test['actor'], 'item': test['item'], 'minimum': test['minimum']} if test['kind'] == 'inventory' else None
                        if test['kind'] == 'zone': translated = {'kind': 'zone', 'actor': test['actor'], 'place': test['equals']}
                        if translated is not None and translated['actor'] == actor.id:
                            unmet.append(translated)
                        else:
                            self._diagnostic(actor, 'route_condition_unplanned')
                    if unmet:
                        for need in unmet: result.extend(self._need(need, actor, legal, stack, chain))
                        break
            if not result:
                self._diagnostic(actor, 'no_current_legal_route_or_preparation')
            return result
        if kind == 'known':
            result = []
            definition = self.world.facts.get(predicate['fact'], {})
            if definition.get('values') is not None:
                self._diagnostic(actor, 'valued_knowledge_inverse_unsupported'); return []
            present = {person.id for person in self.world.present_subjects(actor.zone)}
            for source in definition.get('sources', ()):
                if source.get('type') != 'investigate': continue
                place = source.get('zone')
                if place and place != actor.zone:
                    result.extend(self._need({'kind': 'zone', 'actor': actor.id, 'place': place}, actor, legal, stack, chain))
                elif place == actor.zone or source.get('agent') in present:
                    result.extend(self._direct(legal, 'investigate', [actor.zone], chain, 'learn_from_public_source'))
            if not result: self._diagnostic(actor, 'knowledge_source_not_known_or_available')
            return result
        if kind in {'holds', 'inventory'}:
            item = predicate['item']; result = []; definition = self.world.items.get(item, {})
            recipe = self.world.recipes.get(item)
            if recipe and 'craft' in actor.verbs and self.world.genre_allows('craft'):
                prerequisites = []
                required = (definition.get('requires') or {}).get('knowledge')
                if required and required not in actor.knowledge:
                    prerequisites.append({'kind': 'known', 'actor': actor.id, 'fact': required})
                prerequisites.extend({'kind': 'inventory', 'actor': actor.id, 'item': material, 'minimum': count}
                                     for material, count in sorted(recipe.items()) if actor.inventory.get(material, 0) < count)
                place = definition.get('craft_zone')
                if not prerequisites and place and place != actor.zone:
                    prerequisites.append({'kind': 'zone', 'actor': actor.id, 'place': place})
                for need in prerequisites: result.extend(self._need(need, actor, legal, stack, chain))
                if not prerequisites: result.extend(self._direct(legal, 'craft', [item], chain, 'craft_public_recipe'))
            for index, source in enumerate(definition.get('sources', ())):
                verb, place = source.get('type'), source.get('zone')
                if verb not in {'investigate', 'labor'} or not place or verb not in actor.verbs or not self.world.genre_allows(verb): continue
                gathered = actor.gathered.get(f'item:{item}:{place}:{index}', 0)
                if gathered >= int(source.get('max', 1)): continue
                if actor.zone != place:
                    result.extend(self._need({'kind': 'zone', 'actor': actor.id, 'place': place}, actor, legal, stack, chain))
                else:
                    result.extend(self._direct(legal, verb, [actor.zone] if verb == 'investigate' else [], chain, 'collect_from_public_source'))
            if definition.get('shop_zone') and 'buy' in actor.verbs and self.world.genre_allows('buy'):
                from engine.economy import purchasable
                if purchasable(definition, self.world.mechanics_enabled('budgeted_purchases')):
                    price = definition.get('price') or {} if self.world.mechanics_enabled('budgeted_purchases') else {}
                    missing = [{'kind': 'inventory', 'actor': actor.id, 'item': currency, 'minimum': count}
                               for currency, count in sorted(price.items()) if actor.inventory.get(currency, 0) < count]
                    if not missing and actor.zone != definition['shop_zone']:
                        missing.append({'kind': 'zone', 'actor': actor.id, 'place': definition['shop_zone']})
                    for need in missing: result.extend(self._need(need, actor, legal, stack, chain))
                    if not missing: result.extend(self._direct(legal, 'buy', [item], chain, 'buy_from_public_shop'))
            # Current co-presence is observable; remote pockets/locations are never scanned.
            for person in self.world.present_subjects(actor.zone):
                if person.id == actor.id or person.inventory.get(item, 0) <= 0: continue
                if definition.get('lootable', False):
                    result.extend(self._direct(legal, 'fight', [person.id], chain, 'visible_acquisition_uncertain', uncertain=True))
                for action, weight in legal:
                    if action.verb == 'negotiate' and action.args and action.args[0] == person.id and item in action.args:
                        result.append({'key': _key(action), 'chain': deepcopy(chain), 'reason': 'visible_exchange_uncertain', 'uncertain': True})
            if not result: self._diagnostic(actor, 'item_method_or_location_unknown')
            return result
        self._diagnostic(actor, 'inverse_condition_unsupported'); return []

    def augment(self, actor, weighted, turn, day):
        """Annotate and favour existing next legal actions; never append candidates."""
        self.diagnostics[actor.id] = set()
        proposals = []
        for index, branch in enumerate(self.branches):
            if all(self._truth(p, actor) is True for p in branch):
                return weighted  # This OR branch already satisfies the entire known root.
            own = [p for p in branch if p.get('actor') == actor.id and self._truth(p, actor) is not True]
            inventory = [p for p in own if p['kind'] == 'holds']
            for need in inventory or own:
                for proposal in self._need(need, actor, weighted):
                    proposal['branch'] = index; proposals.append(proposal)
        # Explicitly shared, adopted preparations are intentions, not revelations
        # about where a remote holder currently is or what is in their pockets.
        for entry in self.pressure:
            awareness = entry['awareness']
            if awareness['kind'] == 'known_fact' and awareness['fact_id'] not in actor.knowledge:
                continue
            predicate = self.candidates[entry['candidate_id']]['predicate']
            if predicate.get('actor') != actor.id or predicate['kind'] not in {'zone', 'holds', 'inventory', 'known'}:
                continue
            for proposal in self._need(predicate, actor, weighted):
                proposal['branch'] = -1; proposals.append(proposal)
        by_action = {}
        # Finish a presently available preparation before rewarding travel to a
        # different missing prerequisite. This avoids village/forest oscillation
        # while keeping every ordinary travel candidate available to sampling.
        if any(proposal['key'][0] != 'move' for proposal in proposals):
            proposals = [proposal for proposal in proposals if proposal['key'][0] != 'move']
        for proposal in proposals:
            by_action.setdefault(proposal['key'], []).append(proposal)
        if not by_action:
            return weighted
        maximum = max((float(weight) for _, weight in weighted), default=0)
        if not math.isfinite(maximum) or maximum <= 0:
            raise ValueError('Preparation input weights must be finite positive')
        output = []
        for action, weight in weighted:
            if not math.isfinite(float(weight)) or weight < 0:
                raise ValueError('Preparation input weights must be finite nonnegative')
            matches = by_action.get(_key(action), [])
            if matches:
                changed = copy(action); changed.meta = dict(action.meta)
                changed.meta['purpose_preparation'] = {'version': VERSION, 'actor_id': actor.id,
                    'root_id': self.root['id'], 'root_when_hash': self.root['when_hash'],
                    'observed_turn': turn, 'guidance_multiplier': GUIDANCE_MULTIPLIER,
                    'next_step_only': True, 'branches': sorted({p['branch'] for p in matches}),
                    'requirements': [dict(p) for p in matches[0]['chain']],
                    'reason': matches[0]['reason'], 'uncertain': all(p['uncertain'] for p in matches)}
                action = changed
            output.append((action, float(weight)/maximum*(GUIDANCE_MULTIPLIER if matches else 1.0)))
        return output

    def reweight(self, actor, weighted, turn, record=False):
        """Apply only actor-known deadline/estimate evidence for the same prefix."""
        multipliers, output, explanations = [], [], []
        for action, weight in weighted:
            annotation = action.meta.get('purpose_preparation')
            choices = []
            if annotation and not annotation['uncertain'] and action.verb != 'rest':
                chain = {_predicate_key(p) for p in annotation['requirements']}
                for entry in self.pressure:
                    candidate = self.candidates[entry['candidate_id']]; predicate = candidate['predicate']
                    owner = predicate.get('actor', self.world.protagonist)
                    if owner != actor.id or (predicate['kind'] != 'ending' and _predicate_key(predicate) not in chain): continue
                    awareness, estimate = entry['awareness'], entry.get('estimate')
                    if awareness['kind'] == 'known_fact' and awareness['fact_id'] not in actor.knowledge: continue
                    if not estimate or estimate['fact_id'] not in actor.knowledge: continue
                    finish = turn + estimate['duration'] - 1
                    slack = entry['deadline_tick'] - finish
                    urgency = min(1., max(0., 1.-slack/estimate['duration']))
                    refs = ['public:'+entry['candidate_id']] if awareness['kind'] == 'public' else ['fact:'+awareness['fact_id']]
                    evidence = {'candidate_id': entry['candidate_id'], 'actor_id': actor.id, 'observed_turn': turn,
                        'deadline_day': entry['day'], 'deadline_slot': entry['slot'],
                        'deadline_turn': entry['deadline_tick'], 'estimated_duration': estimate['duration'],
                        'perceived_completion_turn': finish, 'subjective_slack': slack,
                        'evidence_refs': refs+['estimate:'+estimate['fact_id']], 'multiplier': 4.**urgency,
                        'is_actual_completion': False}
                    context = self.world.time_context(entry['day']) if hasattr(self.world, 'time_context') else {}
                    if context.get('date'): evidence['deadline_date'] = context['date']
                    choices.append(evidence)
            chosen = sorted(choices, key=lambda e: (-e['multiplier'], e['candidate_id']))[0] if choices else None
            multipliers.append(chosen['multiplier'] if chosen else 1.)
            explanations.append(chosen)
        positive = {m for m, (_, w) in zip(multipliers, weighted) if w > 0}
        effective = len(positive) > 1 and any(m > 1. for m in positive)
        maximum = max((float(w) for _, w in weighted), default=0)
        for (action, weight), multiplier, evidence in zip(weighted, multipliers, explanations):
            if evidence and multiplier > 1.:
                changed = copy(action); changed.meta = dict(action.meta)
                changed.meta['purpose_preparation'] = {**action.meta['purpose_preparation'], 'deadline_pressure': evidence}
                action = changed
            output.append((action, float(weight)/maximum*multiplier if effective and maximum > 0 else weight))
        return (output if effective else weighted), ({'applied': effective, 'unit': 'logical_step',
            'reason_codes': sorted(self.diagnostics.get(actor.id, ()))} if record else None)

    def selected_evidence(self, action):
        """Already actor-scoped data only; never add raw root/source/world truth."""
        value = action.meta.get('purpose_preparation')
        return deepcopy(value) if value else None
