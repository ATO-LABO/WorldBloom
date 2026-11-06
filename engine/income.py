"""Quantities for existing labor and one-time item rewards; no new actions."""

MAX_AMOUNT = 1_000_000_000


def amount(value):
    if type(value) is not int or not 1 <= value <= MAX_AMOUNT:
        raise ValueError('入手量は1〜1000000000の整数で指定してください')
    return value


def trial_id(trial):
    return str(trial.get('id', str(trial['giver']) + ':' + ','.join(
        f'{key}:{trial["grants"][key]}' for key in sorted(trial['grants'])
    )))


def labor_projection(item, definition, subject, zone, actions):
    """Project finite receipt events using the same keys/timing as _labor_gather."""
    rows = []
    for index, source in enumerate(definition.get('sources') or []):
        if source.get('type') != 'labor' or source.get('zone') != zone:
            continue
        key = f'item:{item}:{zone}:{index}'
        remaining = max(0, int(source.get('max', 1)) - subject.gathered.get(key, 0))
        if not remaining:
            continue
        count = max(1, int(source.get('count', 1)))
        progress = subject.gather_progress.get('progress:' + key, 0)
        first = max(1, count - progress)
        receipts = min(remaining, 1 + (actions - first) // count) if actions >= first else 0
        worked = min(actions, first + (remaining - 1) * count)
        after_progress = progress + worked if not receipts else worked - (first + (receipts - 1) * count)
        rows.append({'key': key, 'receipts': receipts,
                     'gained': receipts * source.get('amount', 1),
                     'progress': after_progress,
                     'last': first + (remaining - 1) * count})
    return rows


def labor_plan(item, definition, subject, zone, deficit):
    """Minimum labor actions for the asset, including parallel sources at a zone."""
    rows = labor_projection(item, definition, subject, zone, 0)
    if not rows:
        return None
    upper = max(row['last'] for row in rows)
    if sum(row['gained'] for row in labor_projection(item, definition, subject, zone, upper)) < deficit:
        return None
    lower = 1
    while lower < upper:
        middle = (lower + upper) // 2
        if sum(row['gained'] for row in labor_projection(item, definition, subject, zone, middle)) >= deficit:
            upper = middle
        else:
            lower = middle + 1
    return lower


def advance_labor_stock(subject, world, actions):
    """Stock-only legacy route preview; actual execution also applies stamina."""
    for item, definition in sorted(world.items.items()):
        for row in labor_projection(item, definition, subject, subject.zone, actions):
            if row['receipts']:
                subject.gathered[row['key']] = subject.gathered.get(row['key'], 0) + row['receipts']
            subject.gather_progress['progress:' + row['key']] = row['progress']
            subject.add_item(item, row['gained'])


def quantity_asset(world, item):
    """Scope quantity-aware route changes to the configured income/payment model."""
    if item == (getattr(world, 'economy', None) or {}).get('currency', {}).get('asset'):
        return True
    if any(item in (definition.get('price') or {}) for definition in world.items.values()):
        return True
    if any(source.get('type') == 'labor' for source in world.items.get(item, {}).get('sources') or []):
        return True
    return any('item_amount' in trial and (trial.get('grants') or {}).get('item') == item for trial in world.trials)


def needed_quantity(item, best_kinds, world):
    """Quantity consumed by still-open recipes or actual item prices, not reference prices."""
    from engine.economy import purchasable
    consumers = [world.recipes[product][item] for product in best_kinds.get('has_item', ())
                 if product in world.recipes and item in world.recipes[product]]
    if world.mechanics_enabled('budgeted_purchases'):
        consumers += [int(world.items[product]['price'][item]) for product in best_kinds.get('has_item', ())
                      if world.items.get(product, {}).get('price')
                      and purchasable(world.items[product], True)
                      and item in world.items[product]['price']]
    return max(consumers, default=1)
