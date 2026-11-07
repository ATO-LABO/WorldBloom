"""Actor-scoped adapters for existing item transactions and desired states.

No new character parameters or story-specific verbs. Negotiation forecasts a
conditional settlement, never the other person's consent or private values.
"""
from engine.concerns import coordinates, effect_coordinate


def enabled(world, actor):
    return (world.goal_rules is not None and world.concerns is not None
            and bool(world.concerns.commitments_for(world, actor.id)))


def item_goal(world, actor, cid, turn):
    if not enabled(world, actor) or cid not in world.goal_rules.definitions:
        return None
    if cid not in {c['condition'] for c in world.concerns.commitments_for(world, actor.id)}:
        return None
    test = world.concerns.conditions[cid]['test']
    if test['kind'] != 'inventory' or test['actor'] != actor.id:
        return None
    own = world.goal_rules.actor_view(world, actor.id, cid, turn)
    if (own['status'] in {'achieved', 'failed'} or not own['period']['start'] <= turn <= own['period']['end']
            or actor.inventory.get(test['item'], 0) >= test['minimum']):
        return None
    return test['item']


def offer_valid(world, claimant, offer, turn):
    if offer.get('planning_root') is not None:
        return world.planner is not None and world.planner.offer_valid(world, claimant, offer, turn)
    cid = offer.get('objective_goal')
    return (offer.get('status', 'pending') == 'pending' and turn <= offer.get('expires_at',offer['turn'] + 1)
            and item_goal(world, claimant, cid, turn) == offer.get('objective') if cid is not None
            else claimant.goal.target == offer.get('objective'))


def offered_assets(world, actor, item):
    return {name: count for name, count in sorted(actor.inventory.items())
            if count > 0 and name != item and world.items[name].get('lootable', False)}


def known_holder(world, actor, target, item):
    """A visible/remembered stock condition, never a scan of remote pockets."""
    for cid, definition in world.concerns.conditions.items():
        test = definition['test']
        if (test['kind'] == 'inventory' and test['actor'] == target.id and test['item'] == item
                and test['minimum'] >= 1 and world.concerns.evaluate(world, cid, actor=actor.id)['state'] == 'true'):
            return True
    return False


def _inventory(actor, item, value):
    return {'kind': 'inventory', 'actor': actor, 'item': item, 'value': value}


def settlement_effects(world, actor, claimant, holder, item, assets):
    """Project only the evaluating party's inventory and public inventories.

    A submitted payment is a ceiling. It may not be replaced by newly acquired
    property; execution requires the original quantities to remain available.
    """
    deltas = {(claimant.id, item): 1, (holder.id, item): -1}
    for name, count in assets.items():
        deltas[(claimant.id, name)] = deltas.get((claimant.id, name), 0) - count
        deltas[(holder.id, name)] = deltas.get((holder.id, name), 0) + count
    public = {tuple(coordinates(c['test'])[0]) for c in world.concerns.conditions.values()
              if c['test']['kind'] == 'inventory' and c['visibility'] == 'public'}
    return [_inventory(person, name, world.subjects[person].inventory.get(name, 0) + delta)
            for (person, name), delta in sorted(deltas.items())
            if person == actor.id or ('inventory', person, name) in public]


def assess(world, actor, effects, turn, *, condition=None, eta=1, conditional=False):
    """Shared own-value guard and ordinal comparison for causal forecasts."""
    before = world.concerns.assessment(world, actor.id)
    projection = {'effects': effects}
    after = world.concerns.assessment(world, actor.id, projection=projection)
    affected = {effect_coordinate(e) for e in effects}
    excluded = []
    gains = []
    for old, new in zip(before['entries'], after['entries']):
        cid = new['condition']
        touched = bool(affected & set(coordinates(world.concerns.conditions[cid]['test'])))
        rule = cid in world.goal_rules.definitions
        if rule:
            touched |= bool(affected & world.goal_rules.affected_coordinates(world, cid))
        if not touched:
            continue
        failure = new.get('objective_prediction', {}).get('failure', {}).get('state', 'false')
        if new['required'] and (new['state'] != 'true' or failure != 'false'):
            # A still-pending acquisition may progress one unit at a time.
            test = world.concerns.conditions[cid]['test']
            progressing = (test['kind'] == 'inventory' and test['actor'] == actor.id
                           and any(e['kind'] == 'inventory' and e['actor'] == actor.id
                                   and e['item'] == test['item'] and e['value'] > actor.inventory.get(test['item'], 0)
                                   for e in effects) and failure == 'false')
            if not progressing:
                excluded.append('required_condition_conflict:' + cid)
        delta = (1 if new['state'] == 'true' else -1 if new['state'] == 'false' else 0) - (
            1 if old['state'] == 'true' else -1 if old['state'] == 'false' else 0)
        if failure == 'true':
            delta = min(delta, -2)
        # Real partial inventory progress, not a prediction of full success.
        test = world.concerns.conditions[cid]['test']
        if not delta and test['kind'] == 'inventory' and test['actor'] == actor.id:
            e = next((e for e in effects if effect_coordinate(e) == ('inventory', actor.id, test['item'])), None)
            if e and test['minimum'] > 0:
                delta = (min(e['value'], test['minimum']) - min(actor.inventory.get(test['item'], 0), test['minimum'])) / test['minimum']
        gains.append((new['priority'], delta))
    vector = tuple(sum(delta for p, delta in gains if p == priority) for priority in range(10, 0, -1))
    if vector < (0,) * 10:
        excluded.append('higher_priority_harm')
    if condition is not None:
        own = world.goal_rules.actor_view(world, actor.id, condition, turn)
        if (own['status'] in {'achieved', 'failed'} or turn < own['period']['start']
                or turn + eta - 1 > own['period']['end']):
            excluded.append('objective_time_or_terminal')
        pred = world.goal_rules.predict(world, condition, actor=actor.id, projection=projection)
        if pred['failure']['state'] != 'false':
            excluded.append('objective_failure_not_excluded')
    return {'forecast': after, 'gain': list(vector), 'excluded': sorted(set(excluded)),
            'condition': condition, 'eta': eta, 'consent_required': conditional}


def transaction(world, actor, action, turn):
    """Return (effects, condition, conditional), or None for an invalid offer."""
    from engine.actions import _living_targets
    present = {p.id: p for p in _living_targets(actor, world.present_subjects(actor.zone))}
    target = present.get(action.args[0]) if action.args else None
    if target is None:
        return None
    if action.verb == 'negotiate':
        cid = action.meta.get('objective_goal')
        item = item_goal(world, actor, cid, turn) if cid is not None else actor.goal.target
        if (not item or not actor.objective_claimant or (not known_holder(world, actor, target, item)
                if cid is not None else world.holder(item) != target.id)):
            return None
        assets = offered_assets(world, actor, item)
        return settlement_effects(world, actor, actor, target, item, assets), cid, True
    if action.verb == 'concede':
        offer = world.offers.get((target.id, actor.id))
        if not offer or not offer_valid(world, target, offer, turn):
            return None
        item = offer['objective']
        if (not actor.has_item(item) if offer.get('objective_goal') is not None else world.holder(item) != actor.id):
            return None
        assets = offer.get('assets', {})
        # New agreements cannot silently shrink or substitute the payment.
        if offer.get('objective_goal') is not None and any(target.inventory.get(i, 0) < n for i, n in assets.items()):
            return None
        attractive = [i for i, n in assets.items() if n > 0 and target.has_item(i)
                      and not actor.has_item(i) and world.items[i].get('modifier')]
        payment = {i: min(n, target.inventory.get(i, 0)) for i, n in assets.items()} if attractive else {}
        return settlement_effects(world, actor, target, actor, item, payment), None, False
    if action.verb == 'give_item':
        if len(action.args) != 2 or not actor.has_item(action.args[1]):
            return None
        return settlement_effects(world, actor, target, actor, action.args[1], {}), None, False
    return None


def review_transaction(world, actor, action, turn):
    if action.verb == 'negotiate' and (action.meta.get('goal_plan') or action.meta.get('planning_root')):
        if world.planner is None or not action.meta.get('goal_plan') or not action.meta.get('planning_root'):
            return {'excluded': ['planning_binding_required']}
        return world.planner.validate(world, actor, action, turn)
    if not enabled(world, actor):
        return None
    if actor.vitality not in {'alive', 'revived'}:
        return {'excluded': ['actor_unavailable']}
    data = transaction(world, actor, action, turn)
    if data is None:
        return {'excluded': ['transaction_unavailable']}
    effects, cid, conditional = data
    return assess(world, actor, effects, turn, condition=cid, eta=2 if conditional else 1, conditional=conditional)


def augment_and_rank(world, actor, weighted, present, turn, allowed, day=1):
    """Keep legacy candidates, bind inventory purposes, then compare forecasts."""
    active = enabled(world, actor)
    incoming = any(holder == actor.id and offer.get('objective_goal') is not None
                   for (_, holder), offer in world.offers.items())
    if not active and not incoming:
        return weighted
    from engine.actions import Action, _permission_weight, _living_targets
    weighted = list(weighted)
    if active and 'negotiate' in allowed and world.genre_allows('negotiate') and actor.objective_claimant:
        for commitment in world.concerns.commitments_for(world, actor.id):
            cid = commitment['condition']
            item = item_goal(world, actor, cid, turn)
            if not item:
                continue
            for holder in _living_targets(actor, present):
                if not known_holder(world, actor, holder, item):
                    continue
                # Repeated offers are not progress. Wait for the recipient's turn;
                # after refusal/expiry this candidate can be reconsidered.
                offer = world.offers.get((actor.id, holder.id))
                if offer and offer.get('objective_goal') == cid:
                    if offer_valid(world, actor, offer, turn):
                        if 'rest' in allowed and world.genre_allows('rest'):
                            weighted.append((Action('rest', meta={'objective_wait': cid, 'holder': holder.id}), 24))
                        continue
                    if turn <= offer['turn'] + 4:
                        continue
                permission = _permission_weight(actor, holder, 'negotiate', world)
                if permission > 0:
                    weighted.append((Action('negotiate', (holder.id,), {'objective': item, 'objective_goal': cid,
                        'target': holder.id, 'stance_sign': 1}), (20 + 4 * actor.traits['social']) / 2 * permission))
    # Reuse normal gift execution, including its item-specific restrictions.
    if 'concede' in allowed and world.genre_allows('concede'):
        existing = {(a.verb, a.args) for a, _ in weighted}
        for claimant in _living_targets(actor, present):
            offer = world.offers.get((claimant.id, actor.id))
            if not offer or offer.get('objective_goal') is None or not offer_valid(world, claimant, offer, turn):
                continue
            if ('concede', (claimant.id,)) not in existing:
                permission = _permission_weight(actor, claimant, 'concede', world)
                if permission > 0:
                    weighted.append((Action('concede', (claimant.id,)), 24 * permission))
    if active and 'give_item' in allowed and world.genre_allows('give_item'):
        existing = {(a.verb, a.args) for a, _ in weighted}
        for target in _living_targets(actor, present):
            for item, count in sorted(actor.inventory.items()):
                if count <= 0 or ('give_item', (target.id, item)) in existing:
                    continue
                if set(world.items[item].get('give', {}).get('ineffective_for', [])) & set(target.tags):
                    continue
                action = Action('give_item', (target.id, item))
                review = review_transaction(world, actor, action, turn)
                permission = _permission_weight(actor, target, 'give_item', world)
                if review and not review['excluded'] and tuple(review['gain']) > (0,) * 10 and permission > 0:
                    weighted.append((action, (20 + 4 * actor.traits['social']) * permission))
    # Existing response-only worlds retain their candidate policy byte for byte.
    compare_responses = any(a.verb in {'negotiate', 'concede', 'give_item'} or a.meta.get('objective_wait') for a, _ in weighted)
    reviewed = []
    alternatives = []
    for action, weight in weighted:
        review = None
        if action.meta.get('objective_wait'):
            cid, holder = action.meta['objective_wait'], world.subjects[action.meta['holder']]
            offer = world.offers[(actor.id, holder.id)]
            effects = settlement_effects(world, actor, actor, holder, offer['objective'], offer['assets'])
            review = assess(world, actor, effects, turn, condition=cid, conditional=True)
        elif action.verb in {'negotiate', 'concede', 'give_item'}:
            review = review_transaction(world, actor, action, turn)
            offer = world.offers.get((action.args[0], actor.id)) if action.verb == 'concede' else None
            if offer and offer.get('objective_goal') is not None and offer_valid(world, world.subjects[action.args[0]], offer, turn):
                # Refusal is an actual answer, with no inventory effects.
                if review and review['excluded']:
                    review = {**review, 'refusal_basis': review['excluded'], 'excluded': [], 'gain': [0] * 10,
                              'answer': 'refuse'}
                weight = max(weight, 24)
        elif active and compare_responses and action.meta.get('response_plan') and world.responses is not None:
            spec = world.responses.actions[action.meta['response_plan']]
            review = assess(world, actor, spec['effects'], turn, condition=spec['condition'],
                            eta=action.meta['response_reason']['eta'])
        if review is not None:
            label = (world.responses.actions[action.meta['response_plan']]['label']
                     if action.meta.get('response_plan') and world.responses is not None else None)
            alternatives.append({'verb': action.verb, 'args': list(action.args), 'label': label, **review})
        reviewed.append((action, weight, review))
    positive = [tuple(r['gain']) for _, _, r in reviewed if r and not r['excluded'] and tuple(r['gain']) > (0,) * 10]
    best = max(positive, default=None)
    for option in alternatives:
        option['ranked_out'] = bool(best and not option['excluded'] and tuple(option['gain']) != best)
    if active and compare_responses and world.responses is not None:
        for option in world.responses.options(world, actor, turn, day):
            if option['excluded']:
                alternatives.append({'verb': 'respond', 'args': [option['action']], 'excluded': option['excluded'],
                                     'label': world.responses.actions[option['action']]['label'], 'ranked_out': False})
    result = []
    for action, weight, review in reviewed:
        if review is not None:
            if review['excluded']:
                continue
            if best and tuple(review['gain']) != best:
                continue
            action = Action(action.verb, action.args, {**action.meta, 'objective_choice': {**review,
                'alternatives': alternatives}})
            if action.verb == 'concede' and tuple(review['gain']) > (0,) * 10:
                weight = max(weight, 20 + 4 * actor.traits['social'])
        result.append((action, weight))
    return result
