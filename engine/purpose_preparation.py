"""Pure preparation catalogue derived from the single authored ending.

No I/O, execution-layer imports, actor knowledge grants, action execution or
feasibility claims. Managed candidates are attention/pressure references, never
additional terminal constraints. Pass initial subjects, not live turn state.
"""
from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import json

VERSION = 1
MAX_CANDIDATES = 256
MAX_PATHS = 32
MAX_ROUTE_DEPTH = 16
MAX_ROUTE_STATES = 2048
MAX_DEPENDENCY_DEPTH = 32


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def when_hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest() if isinstance(value, str) else digest(value)


def _raw_world(world):
    if isinstance(world, dict): return deepcopy(world)
    raw = getattr(world, 'purpose_preparation_definition', None)
    if not isinstance(raw, dict): raise ValueError('Initial authored world definition is required')
    return deepcopy(raw)


def _people(people):
    result = []
    rows = people.values() if isinstance(people, dict) else people
    for p in rows:
        get = p.get if isinstance(p, dict) else lambda key, default=None: getattr(p, key, default)
        location = get('range', {})
        entry = location.get('entry') if isinstance(location, dict) else None
        if entry is None: entry = get('zone')
        inventory = get('inventory', {}) or {}
        if not isinstance(inventory, dict): raise ValueError('Initial inventory must be a mapping')
        normalized = {}
        for key, value in inventory.items():
            count = int(value)
            if count < 0: raise ValueError('Initial inventory count must be nonnegative')
            normalized[str(key)] = count
        result.append({'id': str(get('id', '')), 'entry': entry, 'inventory': normalized,
                       'knowledge': sorted(set(get('knowledge', []) or [])),
                       'verbs': sorted(set(get('verbs', []) or [])),
                       'vitality': get('vitality', 'alive')})
    result.sort(key=lambda p: p['id'])
    if any(not p['id'] for p in result) or len({p['id'] for p in result}) != len(result):
        raise ValueError('Initial subject IDs must be unique and nonempty')
    return result


def dependency_hash(world, people):
    return digest({'world': _raw_world(world), 'people': _people(people)})


def _rows(value, key):
    if isinstance(value, dict):
        return {str(k): dict(v, **{key: str(k)}) if isinstance(v, dict) else {key: str(k)} for k, v in value.items()}
    if not isinstance(value, list): return {}
    return {str(v.get(key) if isinstance(v, dict) else v): deepcopy(v) if isinstance(v, dict) else {key: str(v)} for v in value}


def _root_tree(source):
    """Small inverse-planning domain; all other legal ending atoms stay unsupported."""
    raw = ast.parse(source, mode='eval')
    def ref(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, str): return n.value
        if isinstance(n, ast.Name) and n.id not in {'self', 'target', 'planter'}: return n.id
        raise ValueError('Registered literal reference required')
    def visit(n):
        if isinstance(n, ast.BoolOp) and isinstance(n.op, (ast.And, ast.Or)):
            return {'kind': 'all' if isinstance(n.op, ast.And) else 'any', 'children': [visit(c) for c in n.values]}
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'holds' and len(n.args) == 2 and not n.keywords:
            return {'kind': 'holds', 'actor': ref(n.args[0]), 'item': ref(n.args[1]), 'negate': False}
        if (isinstance(n, ast.Compare) and len(n.ops) == 1 and isinstance(n.ops[0], ast.Eq)
                and len(n.comparators) == 1 and isinstance(n.left, ast.Call)
                and isinstance(n.left.func, ast.Name) and n.left.func.id == 'zone'
                and len(n.left.args) == 1 and not n.left.keywords):
            return {'kind': 'zone', 'actor': ref(n.left.args[0]), 'place': ref(n.comparators[0]), 'cmp': '=='}
        return {'kind': 'unsupported', 'expression': ast.unparse(n)}
    return visit(raw.body)


def root_matches(world, record):
    try:
        w = _raw_world(world);ids = w.get('target_ending')
        if not isinstance(ids, list) or len(ids) != 1: return False
        endings = [e for e in w.get('ending', []) if isinstance(e, dict) and e.get('id') == ids[0]]
        return len(endings) == 1 and record.get('root_id') == ids[0] and record.get('root_when_hash') == when_hash(endings[0].get('when'))
    except (ValueError, TypeError, KeyError): return False


def _normalized_tree(node):
    """Remove parser-only expression/index data so API and runtime hashes agree."""
    kind = node.get('kind')
    if kind in {'all', 'any'} or node.get('op') in {'and', 'or'}:
        return {'kind': kind if kind in {'all', 'any'} else 'all' if node['op'] == 'and' else 'any',
                'children': [_normalized_tree(child) for child in node.get('children', [])]}
    if kind == 'zone': return {'kind': kind, 'actor': node.get('actor'), 'place': node.get('place', node.get('zone')), 'cmp': node.get('cmp', '==')}
    if kind == 'holds': return {'kind': kind, 'actor': node.get('actor'), 'item': node.get('item'), 'negate': node.get('negate', False)}
    return {k: deepcopy(v) for k, v in node.items() if k not in {'index'}}


def derive(world, people, tree=None):
    w = _raw_world(world);persons = _people(people);actors = {p['id']: p for p in persons}
    items = _rows(w.get('items', []), 'name');zones = _rows(w.get('zones', []), 'name');facts = _rows(w.get('facts', []), 'id')
    diagnostics = [];candidates = {};available = True
    def diagnostic(code, message, **extra):
        row = {'code': code, 'message': message, **extra}
        if row not in diagnostics: diagnostics.append(row)
    result = {'version': VERSION, 'available': False, 'supported': False, 'root': None, 'tree': None,
              'dependency_hash': digest({'world': w, 'people': persons}), 'candidates': [], 'diagnostics': diagnostics,
              'scope': {'conditions': ['positive zone equality', 'positive holds', 'AND', 'OR'],
                        'derivation': ['route requires_item', 'craft materials/knowledge/place', 'declared sources'],
                        'is_feasibility_check': False, 'actor_knowledge_granted': False}}
    ids = w.get('target_ending', [])
    if not isinstance(ids, list) or len(ids) != 1:
        diagnostic('single_root_required', '現在の最終条件が1件に定まっていません。旧設定を保持しています。');return result
    endings = [e for e in w.get('ending', []) if isinstance(e, dict) and e.get('id') == ids[0]]
    if len(endings) != 1:
        diagnostic('root_reference_missing', '最終条件の参照先が一意に登録されていません。');return result
    ending = endings[0];source = ending.get('when');root = {'id': ids[0], 'when': deepcopy(source), 'when_hash': when_hash(source)};result['root'] = root
    if w.get('slot_execution') is not None:
        diagnostic('unsupported_execution_mode', 'この実行方式では目標と準備の実行接続は未対応です。既存設定を保持し、有効化しません。');return result
    if not isinstance(source, str):
        diagnostic('unsupported_root', 'この旧形式の最終条件から準備を導出する処理は未対応です。');return result
    try:
        from engine.predicate import compile_predicate, PREDICATE_NAMES
        compile_predicate(source, set(PREDICATE_NAMES) | set(actors) | set(items) | set(zones) | set(facts))
    except (ValueError, SyntaxError, TypeError, RecursionError):
        diagnostic('invalid_root', '最終条件の構文または参照を確認してください。');return result
    try: tree = _normalized_tree(deepcopy(tree) if tree is not None else _root_tree(source))
    except (ValueError, SyntaxError, TypeError, RecursionError):
        diagnostic('unsupported_root', 'この有効な結末条件の準備逆算は未対応です。元の条件は保持します。');return result
    result['tree'] = tree
    root_hash = root['when_hash']
    def add(predicate, category, label, code, message, path, value=None, alternatives=None, parent=None, supported=True):
        alt = [];branches = {}
        for part in alternatives or []:
            if part['group'] in branches:
                if branches[part['group']] != part['branch']: return None
                continue
            branches[part['group']] = part['branch'];alt.append(deepcopy(part))
        key = {'predicate': predicate};ident = 'prep_' + digest(key)[:24]
        if ident not in candidates:
            if len(candidates) >= MAX_CANDIDATES:
                diagnostic('candidate_limit', '準備候補の導出上限に達しました。省略範囲の確認が必要です。');return None
            candidates[ident] = {'id': ident, 'semantic_key': key, 'predicate': deepcopy(predicate), 'label': label,
                'category': category, 'categories': [category], 'supported': supported, 'reasons': [],
                'source': {'root_when_hash': root_hash, 'evidence': []}, 'source_hash': '',
                'alternative_paths': [], 'prerequisite_for': [], 'status': 'current'}
        row = candidates[ident]
        if category not in row['categories']: row['categories'].append(category)
        reason = {'code': code, 'message': message, 'source': path}
        if reason not in row['reasons']: row['reasons'].append(reason)
        evidence = {'path': path, 'value': deepcopy(value)}
        if evidence not in row['source']['evidence']: row['source']['evidence'].append(evidence)
        if alt not in row['alternative_paths']: row['alternative_paths'].append(alt)
        if parent and parent not in row['prerequisite_for']: row['prerequisite_for'].append(parent)
        row['supported'] = row['supported'] and supported
        return ident
    root_id = add({'kind': 'ending', 'ending_id': root['id'], 'when': source}, 'final_goal', '最終条件全体を満たす',
                  'root_condition', '現在の最終条件全体です。採用の有無によって判定条件を削除しません。', 'world.target_ending', root)
    leaves = []
    def visit(node, path='root', alternatives=None):
        nonlocal available
        alternatives = alternatives or []
        if node.get('kind') in {'all', 'any'} or node.get('op') in {'and', 'or'}:
            kind = node.get('kind', 'all' if node.get('op') == 'and' else 'any')
            children = node.get('children', [])
            if not isinstance(children, list) or len(children) < 2:
                available = False;diagnostic('invalid_root_tree', '条件の組合せを確認してください。');return
            for index, child in enumerate(children):
                alt = alternatives + ([{'group': path, 'branch': index}] if kind == 'any' else [])
                visit(child, path + '/' + str(index), alt)
            return
        kind = node.get('kind');actor = node.get('actor')
        if kind not in {'holds', 'zone'} or node.get('negate') or kind == 'zone' and node.get('cmp', '==') != '==':
            available = False;diagnostic('unsupported_goal_atom', 'この条件種類の準備逆算は未対応です。判定対応と準備生成対応は別です。', expression=node.get('expression'), path=path);return
        target = node.get('item') if kind == 'holds' else node.get('place', node.get('zone'))
        if actor not in actors or target not in (items if kind == 'holds' else zones):
            available = False;diagnostic('reference_missing', '条件が未登録の人物・場所・品物を参照しています。', path=path);return
        predicate = {'kind': kind, 'actor': actor, **({'item': target, 'minimum': 1} if kind == 'holds' else {'place': target})}
        label = actor + ('が' + target + 'を1個以上持つ' if kind == 'holds' else 'が' + target + 'にいる')
        ident = add(predicate, 'objective', label, 'root_atom', '最終条件の一部です。' + ('場所の一致だけで帰還履歴とは扱いません。' if kind == 'zone' else ''), path, node, alternatives, root_id)
        leaves.append({'id': ident, 'kind': kind, 'actor': actor, 'target': target, 'alternatives': alternatives})
    visit(tree)
    _derive_preparations(w, actors, items, zones, facts, leaves, add, diagnostic)
    if root_id and not available: candidates[root_id]['supported'] = False
    for row in candidates.values():
        row['source']['evidence'].sort(key=lambda v: canonical(v));row['reasons'].sort(key=lambda v: canonical(v));row['alternative_paths'].sort(key=lambda v: canonical(v));row['prerequisite_for'].sort();row['categories'].sort()
        row['source_hash'] = digest(row['source'])
    result.update(available=available, supported=available, candidates=list(candidates.values()))
    return result


def _derive_preparations(world, actors, items, zones, facts, leaves, add, diagnostic):
    routes = {}
    for origin, values in (world.get('routes') or {}).items():
        if not isinstance(values, list):
            diagnostic('invalid_route_list', '経路一覧の形式を確認してください。', origin=origin);continue
        routes[origin] = []
        for index, raw in enumerate(values):
            edge = {'to': raw} if isinstance(raw, str) else raw
            if not isinstance(edge, dict) or edge.get('to') not in zones or origin not in zones:
                diagnostic('route_reference_missing', '経路の場所参照を確認してください。', origin=origin, index=index);continue
            routes[origin].append((edge['to'], deepcopy(edge), 'world.routes.' + origin + '/' + str(index)))
        routes[origin].sort(key=lambda e: (e[0], canonical(e[1])))

    def paths(origin, target):
        if origin == target: return [[]]
        if origin not in zones or target not in zones:
            diagnostic('initial_location_unknown', '準備経路の開始場所または対象場所が未登録です。', origin=origin, target=target);return []
        found = [];queue = [(origin, [], {origin})];limited = False;expanded = 0
        while queue:
            if expanded >= MAX_ROUTE_STATES:
                limited = True;break
            expanded += 1
            position, edges, seen = queue.pop(0)
            if len(edges) >= MAX_ROUTE_DEPTH:
                limited = True;continue
            for destination, edge, source_path in routes.get(position, []):
                if destination in seen: continue
                next_edges = edges + [(position, destination, edge, source_path)]
                if destination == target:
                    found.append(next_edges)
                    if len(found) >= MAX_PATHS: limited = bool(queue) or len(routes.get(position, [])) > 1;queue = [];break
                else: queue.append((destination, next_edges, seen | {destination}))
        if limited: diagnostic('route_search_limited', '経路候補の導出上限に達しました。未発見を到達不能とは扱いません。', origin=origin, target=target)
        elif not found: diagnostic('no_declared_route', '宣言された経路から接続を見つけられません。到達不能の証明ではありません。', origin=origin, target=target)
        return found

    def route_requirements(actor, origin, target, parent, alternatives, stack):
        options = paths(origin, target)
        group = 'route:' + digest({'actor': actor, 'origin': origin, 'target': target})[:16]
        for index, path in enumerate(options):
            alternative = alternatives + ([{'group': group, 'branch': index}] if len(options) > 1 else [])
            for start, destination, edge, source_path in path:
                if edge.get('requires_conditions'):
                    diagnostic('route_guard_runtime_check', 'この経路の追加条件は実行時の既存判定が必要です。準備候補だけで通行可能とは判定しません。', source=source_path, conditions=deepcopy(edge['requires_conditions']))
                required = edge.get('requires_item')
                if required:
                    if not isinstance(required, str):
                        diagnostic('invalid_route_item', '経路の必要品物は登録名で指定してください。', source=source_path);continue
                    ident = add({'kind': 'holds', 'actor': actor, 'item': required, 'minimum': 1}, 'route_item',
                        actor + 'が' + required + 'を用意する', 'route_requires_item', start + '→' + destination + 'の経路に必要な品物です。',
                        source_path + '/requires_item', edge, alternative, parent, required in items)
                    if required not in items: diagnostic('item_reference_missing', '経路の必要品物が未登録です。', item=required, source=source_path)
                    elif ident: acquire_item(actor, required, 1, ident, alternative, stack)

    def location(actor, place, parent, alternatives, reason, source_path, value, stack, origin=None):
        valid = place in zones
        ident = add({'kind': 'zone', 'actor': actor, 'place': place}, 'preparation_place', actor + 'が' + str(place) + 'にいる',
            reason, '宣言された準備・入手場所です。場所の一致だけで移動完了や帰還履歴を仮定しません。', source_path, value, alternatives, parent, valid)
        if not valid: diagnostic('place_reference_missing', '準備に使う場所が未登録です。', place=place, source=source_path)
        elif ident: route_requirements(actor, origin if origin is not None else actors[actor]['entry'], place, ident, alternatives, stack)
        return ident

    def source_locations(spec):
        result = []
        for index, source in enumerate(spec.get('sources') or []):
            if not isinstance(source, dict): continue
            if source.get('zone') is not None: result.append((source['zone'], 'sources/' + str(index), source))
            elif source.get('agent') in actors: result.append((actors[source['agent']]['entry'], 'sources/' + str(index), source))
            elif source.get('agent') is not None: diagnostic('source_actor_missing', '入手元の人物が未登録です。', actor=source['agent'])
        if spec.get('shop_zone') is not None: result.append((spec['shop_zone'], 'shop_zone', {'type': 'buy', 'price': deepcopy(spec.get('price')), 'zone': spec['shop_zone']}))
        return result

    def source_requirements(actor, source, parent, alternatives, source_path, stack):
        requires = source.get('requires') or {}
        if not isinstance(requires, dict):
            diagnostic('unsupported_source_precondition', '入手元の前提形式は未対応です。', source=source_path);return
        for key, value in requires.items():
            if key == 'knowledge':
                names = [value] if isinstance(value, str) else value if isinstance(value, list) else []
                for fact in names: need_fact(actor, fact, parent, alternatives, source_path + '/requires/knowledge', stack)
            elif key in {'item', 'items'}:
                requirements = {value: 1} if isinstance(value, str) else value if isinstance(value, dict) else {}
                for item, count in requirements.items(): need_item(actor, item, count, parent, alternatives, source_path + '/requires/' + key, stack)
            else: diagnostic('unsupported_source_precondition', 'この入手前提の候補化は未対応です。元の制約は実行時に維持されます。', source=source_path, field=key)

    def need_fact(actor, fact, parent, alternatives, source_path, stack):
        if not isinstance(fact, str):
            diagnostic('fact_reference_missing', '必要知識は登録名で指定してください。', source=source_path);return
        valid = fact in facts and facts[fact].get('values') is None
        ident = add({'kind': 'known', 'actor': actor, 'fact': fact}, 'required_knowledge', actor + 'が' + str(fact) + 'を知っている',
            'requires_knowledge', '製作・入手のため宣言された知識です。設定上の所在を人物へ自動付与しません。', source_path, fact, alternatives, parent, valid)
        if not valid: diagnostic('fact_reference_missing', '必要知識がboolean情報として登録されていません。', fact=fact, source=source_path);return
        if fact in actors[actor]['knowledge']: return
        if len(stack) >= MAX_DEPENDENCY_DEPTH:
            diagnostic('dependency_depth_limit', '知識・製作依存の導出上限に達しました。未発見を不可能とは扱いません。');return
        key = ('fact', actor, fact)
        if key in stack: diagnostic('dependency_cycle', '知識・入手前提に循環があります。', dependency=list(key));return
        branches = [('source', place, path, spec) for place, path, spec in source_locations(facts[fact])]
        branches += [('knower', p['entry'], 'subjects/' + p['id'] + '/knowledge', {'agent': p['id']}) for p in actors.values() if p['id'] != actor and fact in p['knowledge']]
        if not branches: diagnostic('knowledge_source_unknown', '必要知識の宣言された入手元を見つけられません。勝手に知識を与えず、計画不能とも断定しません。', actor=actor, fact=fact)
        for index, (_, place, path, spec) in enumerate(branches):
            alt = alternatives + ([{'group': 'knowledge:' + actor + ':' + fact, 'branch': index}] if len(branches) > 1 else [])
            location(actor, place, ident, alt, 'knowledge_source_location', 'world.facts.' + fact + '/' + path, spec, stack + (key,))
            source_requirements(actor, spec, ident, alt, 'world.facts.' + fact + '/' + path, stack + (key,))

    def need_item(actor, item, count, parent, alternatives, source_path, stack):
        valid_count = type(count) is int and 1 <= count <= 1000000000
        valid = item in items and valid_count
        if not valid_count:
            diagnostic('invalid_material_count', '材料数は正の整数で、対応する上限内である必要があります。', item=item, source=source_path);return
        predicate = {'kind': 'holds' if count == 1 else 'inventory', 'actor': actor, 'item': item, 'minimum': count}
        ident = add(predicate, 'required_material', actor + 'が' + str(item) + 'を' + str(count) + '個以上用意する',
            'craft_material', '1回の製作等に消費する宣言済みの材料数です。製作回数や消費競合は別途の計画が必要で、同じ在庫の二重使用や全量の同時所持は仮定しません。', source_path, count, alternatives, parent, valid)
        if not valid: diagnostic('item_reference_missing', '必要な材料が未登録です。', item=item, source=source_path)
        elif ident: acquire_item(actor, item, count, ident, alternatives, stack)

    def acquire_item(actor, item, minimum, parent, alternatives, stack):
        if actors[actor]['inventory'].get(item, 0) >= minimum: return
        if len(stack) >= MAX_DEPENDENCY_DEPTH:
            diagnostic('dependency_depth_limit', '知識・製作依存の導出上限に達しました。未発見を不可能とは扱いません。');return
        key = ('item', actor, item)
        if key in stack: diagnostic('dependency_cycle', '製作・経路の準備に循環があります。循環を成功手順として扱いません。', dependency=list(key));return
        stack = stack + (key,);spec = items[item];branches = []
        if spec.get('made_from'): branches.append(('craft', None, 'made_from', spec))
        branches += [('source', place, path, source) for place, path, source in source_locations(spec)]
        branches += [('holder', p['entry'], 'subjects/' + p['id'] + '/inventory/' + item, {'agent': p['id'], 'count': p['inventory'][item]}) for p in actors.values() if p['id'] != actor and p['inventory'].get(item, 0) > 0]
        if not branches: diagnostic('item_source_unknown', '宣言された品物の入手方法や所持者を見つけられません。未定義の取得行動は追加しません。', actor=actor, item=item)
        for index, (kind, place, path, source) in enumerate(branches):
            alt = alternatives + ([{'group': 'acquire:' + actor + ':' + item + ':' + str(minimum), 'branch': index}] if len(branches) > 1 else [])
            prefix = 'world.items.' + item + '/' + path
            if kind == 'craft':
                if 'craft' not in actors[actor]['verbs']: diagnostic('action_not_enabled', 'この人物のcraft行動は設定されていません。他の入手方法や役割分担が必要です。', actor=actor, item=item)
                materials = spec.get('made_from')
                if not isinstance(materials, dict): diagnostic('unsupported_recipe', '製作材料の形式は未対応です。', item=item);continue
                for material, count in sorted(materials.items()):
                    need_item(actor, material, count, parent, alt, 'world.items.' + item + '/made_from/' + material, stack)
                requirements = spec.get('requires') or {}
                if isinstance(requirements, dict) and requirements.get('knowledge') is not None:
                    values = requirements['knowledge'];values = [values] if isinstance(values, str) else values if isinstance(values, list) else []
                    for fact in values: need_fact(actor, fact, parent, alt, 'world.items.' + item + '/requires/knowledge', stack)
                for field in requirements if isinstance(requirements, dict) else []:
                    if field != 'knowledge': diagnostic('unsupported_craft_precondition', '製作の追加前提は実行時の既存判定が必要です。', item=item, field=field)
                if spec.get('craft_zone') is not None:
                    location(actor, spec['craft_zone'], parent, alt, 'craft_location', 'world.items.' + item + '/craft_zone', spec['craft_zone'], stack)
            else:
                location(actor, place, parent, alt, 'item_source_location' if kind == 'source' else 'holder_location', prefix, source, stack)
                source_requirements(actor, source, parent, alt, prefix, stack)
                if kind == 'holder': diagnostic('transfer_method_not_assumed', '所持者の場所は入手の手掛かりです。戦闘勝利・譲渡・交換の成功を最終条件や必須行動へ勝手に追加しません。', item=item, holder=source['agent'])
                if kind == 'source' and isinstance(source.get('max'), int) and source['max'] < minimum - actors[actor]['inventory'].get(item, 0):
                    diagnostic('declared_source_shortfall', 'この入手元だけでは表示された必要数に届かない可能性があります。他の在庫・入手元を含む資源確認が必要です。', item=item, required=minimum, source=prefix)

    for leaf in leaves:
        if not leaf['id']: continue
        actor = leaf['actor'];target = leaf['target'];alternatives = leaf['alternatives']
        if leaf['kind'] == 'zone': route_requirements(actor, actors[actor]['entry'], target, leaf['id'], alternatives, ())
        else:
            acquire_item(actor, target, 1, leaf['id'], alternatives, ())
            source_places = {p['entry'] for p in actors.values() if p['id'] != actor and p['inventory'].get(target, 0) > 0}
            source_places.update(place for place, _, _ in source_locations(items[target]))
            if items[target].get('craft_zone'): source_places.add(items[target]['craft_zone'])
            for final in leaves:
                if final['kind'] == 'zone' and final['actor'] == actor:
                    chosen = {part['group']: part['branch'] for part in alternatives}
                    if any(part['group'] in chosen and chosen[part['group']] != part['branch'] for part in final['alternatives']): continue
                    for place in sorted(p for p in source_places if isinstance(p, str)):
                        route_requirements(actor, place, final['target'], final['id'], alternatives + final['alternatives'], ())


def compile_plan(world, people, record, tree=None):
    """Validate saved binding against initial input; never silently rebind IDs."""
    current = derive(world, people, tree);record = record if isinstance(record, dict) else {}
    saved = type(record.get('version')) is int and record.get('version') == VERSION
    needs_review = saved and (not root_matches(world, record) or record.get('dependency_hash') != current['dependency_hash'])
    raw_selected = record.get('managed_ids', [])
    invalid_ids = not isinstance(raw_selected, list) or any(not isinstance(v, str) for v in raw_selected)
    selected = set(raw_selected) if not invalid_ids else set()
    if saved and invalid_ids: needs_review = True
    raw_stored = record.get('candidates', [])
    if not isinstance(raw_stored, list): raw_stored = [];needs_review = saved
    known = {c['id']: c for c in current['candidates']};stored = {c.get('id'): c for c in raw_stored if isinstance(c, dict) and isinstance(c.get('id'), str)}
    for ident in selected:
        if ident not in known or ident not in stored or not known[ident]['supported'] or stored[ident].get('source_hash') != known[ident]['source_hash']: needs_review = True
    candidates = []
    for row in current['candidates']:
        value = deepcopy(row);value['status'] = 'changed' if row['id'] in stored and stored[row['id']].get('source_hash') != row['source_hash'] else 'current'
        value['management'] = ('deferred' if needs_review else 'adopted') if row['id'] in selected else 'unmanaged';candidates.append(value)
    for ident, old in stored.items():
        if ident not in known:
            value = deepcopy(old);value['status'] = 'stale';value['management'] = 'deferred' if ident in selected else 'unmanaged';candidates.append(value)
    pressure = record.get('pressure') if isinstance(record.get('pressure'), dict) else {'enabled': False, 'entries': []}
    raw_entries = pressure.get('entries', []);raw_entries = raw_entries if isinstance(raw_entries, list) else []
    w = _raw_world(world);clock = w.get('time') or {};days = clock.get('days');slots = clock.get('slots');facts = _rows(w.get('facts', []), 'id');seen = set()
    if pressure.get('enabled') is True:
        for entry in raw_entries:
            if not isinstance(entry, dict): needs_review = True;continue
            ident = entry.get('candidate_id')
            if not isinstance(ident, str) or ident not in selected or ident not in known or not known[ident]['supported']: continue
            awareness = entry.get('awareness');estimate = entry.get('estimate')
            valid = (ident not in seen and type(days) is int and days > 0 and isinstance(slots, list)
                     and type(entry.get('day')) is int and 1 <= entry['day'] <= days and entry.get('slot') in slots
                     and isinstance(awareness, dict) and awareness.get('kind') in {'public', 'known_fact'})
            if valid and awareness['kind'] == 'known_fact': valid = isinstance(awareness.get('fact_id'), str) and awareness['fact_id'] in facts and facts[awareness['fact_id']].get('values') is None
            if valid and estimate is not None: valid = isinstance(estimate, dict) and type(estimate.get('duration')) is int and 1 <= estimate['duration'] <= days * len(slots) and isinstance(estimate.get('fact_id'), str) and estimate['fact_id'] in facts and facts[estimate['fact_id']].get('values') is None
            if not valid: needs_review = True
            seen.add(ident)
    enabled = bool(saved and record.get('enabled') is True and current['available'] and not needs_review)
    entries = []
    for entry in raw_entries:
        if (enabled and pressure.get('enabled') is True and isinstance(entry, dict)
                and entry.get('candidate_id') in selected and entry['candidate_id'] in known and known[entry['candidate_id']]['supported']):
            entries.append({'candidate_id': entry['candidate_id'], 'deadline': {'day': entry.get('day'), 'slot': entry.get('slot')},
                            'awareness': deepcopy(entry.get('awareness')), **({'estimate': deepcopy(entry['estimate'])} if 'estimate' in entry else {})})
    return {**current, 'enabled': enabled, 'saved': saved, 'needs_review': needs_review, 'candidates': candidates,
            'managed_ids': sorted(selected), 'pressure': {'enabled': bool(enabled and pressure.get('enabled') is True and entries), 'entries': entries}}


def rebind_frozen_record(record, original_world, frozen_world, people):
    """Rebind only an attested capture-time path rewrite, never a world change.

    The caller must first verify that both references denote the same captured
    asset bytes using capture-manifest SHA evidence. This pure function neither
    resolves paths nor verifies external contents. It permits only the exact two
    capture fields and changes only dependency_hash in a copied sidecar record.
    """
    original = _raw_world(original_world);frozen = _raw_world(frozen_world)
    left = deepcopy(original);right = deepcopy(frozen)
    left_gap = left.get('gapengine');right_gap = right.get('gapengine')
    if left_gap is not None or right_gap is not None:
        if not isinstance(left_gap, dict) or not isinstance(right_gap, dict) or set(left_gap) != set(right_gap):
            raise ValueError('Frozen world changed gapengine structure')
        for field in ('action_graph', 'effects'):
            if field in left_gap and left_gap[field] != right_gap[field]:
                if not isinstance(left_gap[field], str) or not left_gap[field] or not isinstance(right_gap[field], str) or not right_gap[field]:
                    raise ValueError('Only existing path strings may be rebound')
                left_gap[field] = '<verified-captured-path>';right_gap[field] = '<verified-captured-path>'
    if canonical(left) != canonical(right): raise ValueError('Frozen world has semantic changes beyond permitted path references')
    if not isinstance(record, dict) or type(record.get('version')) is not int or record['version'] != VERSION:
        raise ValueError('Unsupported preparation record')
    before = derive(original, people);after = derive(frozen, people)
    if (not before['available'] or not after['available'] or not root_matches(original, record)
            or record.get('dependency_hash') != before['dependency_hash']
            or compile_plan(original, people, record)['needs_review']):
        raise ValueError('Preparation source was stale or unsupported before capture')
    signature = lambda result: {c['id']: {'predicate': c['predicate'], 'source_hash': c['source_hash']} for c in result['candidates']}
    if signature(before) != signature(after) or before['root'] != after['root']:
        raise ValueError('Preparation candidate meaning changed during capture')
    result = deepcopy(record);result['dependency_hash'] = after['dependency_hash'];return result
