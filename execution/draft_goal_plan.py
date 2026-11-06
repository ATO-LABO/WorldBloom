"""Confirmed atomic objectives, kept outside the runtime world's goal format."""
from copy import deepcopy
from collections import Counter
from pathlib import Path
import re
import tempfile

from execution.provenance import ConfigError, canonical
from execution.draft_generation_contract import draft_schema, obj, rows, text
from execution.draft_goals import compile_goal


def fail(path, message):
    raise ConfigError(path, message)


def extraction_schema():
    condition = draft_schema()['properties']['people']['items']['properties']['goal']['properties']['conditions']['items']
    return obj({'people': rows(text(), 8, 2), 'places': rows(text(), 8, 1),
        'items': rows(text(), 8), 'facts': rows(text(), 8),
        'initial_items': rows(obj({'person': text(), 'item': text(),
            'count': {'type': 'integer', 'minimum': 1, 'maximum': 10000}, 'source_note': text(1600)}), 16),
        'goals': rows(obj({'id': {'type': 'string', 'pattern': '^g[0-9]{1,2}$'},
            'actor': text(), 'source_note': text(1600), 'description': text(2000),
            'conditions': rows(condition, 1),
            'initial_status': {'type': 'string', 'enum': ['unachieved', 'achieved', 'unspecified']}}), 16, 1),
        'assumptions': rows(text(1000), 16)})


def normalize(plan, overview):
    if not isinstance(plan, dict) or set(plan) != {'people', 'places', 'items', 'facts', 'initial_items', 'goals', 'assumptions'}:
        fail('plan', '目的抽出の保存形式を確認してください')
    p = deepcopy(plan)
    for key, minimum in [('people', 2), ('places', 1), ('items', 0), ('facts', 0)]:
        values = p[key]
        if (not isinstance(values, list) or not minimum <= len(values) <= 8 or
                any(not isinstance(v, str) or not v.strip() or len(v) > 120 for v in values) or
                len(set(values)) != len(values)):
            fail('plan.' + key, '名前を重複なく指定してください')
    if not isinstance(p['initial_items'], list) or len(p['initial_items']) > 16:
        fail('plan.initial_items', '初期所持の前提を確認してください')
    holdings = set()
    for row in p['initial_items']:
        if (not isinstance(row, dict) or set(row) != {'person', 'item', 'count', 'source_note'} or
                row['person'] not in p['people'] or row['item'] not in p['items'] or
                type(row['count']) is not int or not 1 <= row['count'] <= 10000 or
                not isinstance(row['source_note'], str) or not row['source_note'] or len(row['source_note']) > 1600):
            fail('plan.initial_items', '概要にある初期所持と根拠を指定してください')
        pair = (row['person'], row['item'])
        if pair in holdings: fail('plan.initial_items', '同じ初期所持を重複して指定できません')
        holdings.add(pair)
    if not isinstance(p['goals'], list) or not 1 <= len(p['goals']) <= 16:
        fail('plan.goals', '目的は1〜16件に分けてください')
    seen = set()
    for i, row in enumerate(p['goals']):
        path = 'plan.goals.' + str(i)
        if not isinstance(row, dict) or set(row) != {'id', 'actor', 'source_note', 'description', 'conditions', 'initial_status'}:
            fail(path, '目的の項目を確認してください')
        if not isinstance(row['id'], str) or not re.fullmatch(r'g[0-9]{1,2}', row['id']) or row['id'] in seen:
            fail(path, '目的IDは重複なく指定してください')
        seen.add(row['id'])
        if row['actor'] not in p['people'] or row['initial_status'] not in ('unachieved', 'achieved', 'unspecified'):
            fail(path, '人物と開始時の期待状態を確認してください')
        quote = row['source_note']
        if not isinstance(quote, str) or not quote or len(quote) > 1600 :
            fail(path + '.source_note', 'AIが理解した内容を短い文章で残してください')
        if not isinstance(row['conditions'], list) or len(row['conditions']) > 1:
            fail(path + '.conditions', '達成事項を一つずつ別の目的に分けてください')
        compile_goal({'description': row['description'], 'conditions': row['conditions']}, row['actor'],
            set(p['people']), set(p['places']), set(p['items']), set(p['facts']), path,
            required=True, days=10000)
    assumptions = p['assumptions']
    if not isinstance(assumptions, list) or len(assumptions) > 16 or any(not isinstance(v, str) or not v or len(v) > 1000 for v in assumptions):
        fail('plan.assumptions', 'AIが補った前提を短い文章で残してください')
    if any(sum(len(g['conditions']) for g in p['goals'] if g['actor'] == actor) > 8 for actor in p['people']):
        fail('plan.goals', '人物ごとの自動判定条件は最大8件です')
    return p


def actor_goal(plan, actor):
    goals = [g for g in plan['goals'] if g['actor'] == actor]
    return {'description': ' / '.join(g['description'] for g in goals),
            'conditions': [] if any(not g['conditions'] for g in goals) else [deepcopy(c) for g in goals for c in g['conditions']]}


def generation_schema(plan):
    """Compile confirmed goals into constants BEFORE generation; never repair a response."""
    s = draft_schema()
    for key in ('places', 'items', 'facts'):
        s['properties'][key]['minItems'] = len(plan[key])
        s['properties'][key]['maxItems'] = len(plan[key])
        if plan[key]:
            s['properties'][key]['items']['properties']['name'] = {'type': 'string', 'enum': plan[key]}
    person = s['properties']['people']['items']
    alternatives = []
    for actor in plan['people']:
        row = deepcopy(person)
        row['properties']['name'] = {'type': 'string', 'const': actor}
        row['properties']['entry'] = {'type': 'string', 'enum': plan['places']}
        row['properties']['goal'] = {'type': 'object', 'const': actor_goal(plan, actor)}
        alternatives.append(row)
    s['properties']['people'] = rows({'oneOf': alternatives}, len(plan['people']), len(plan['people']))
    s['properties']['protagonist'] = {'type': 'string', 'enum': plan['people']}
    s['properties']['antagonist'] = {'type': 'string', 'enum': plan['people']}
    return s


def check_draft(repo, plan, value):
    from execution.world_drafts import validate
    from execution.library import LibraryStore
    from gapengine.route_paths import _load_world_and_subjects
    from engine.predicate import compile_predicate
    import shutil
    draft, world, subjects, summary = validate(repo, value, allow_initial_goal_achieved=True)
    for key in ('people', 'places', 'items', 'facts'):
        if Counter(v['name'] for v in draft.get(key, [])) != Counter(plan[key]):
            fail('plan.' + key, '確定した人物・場所・品物・情報の対応が変わっています')
    for person in draft['people']:
        expected = actor_goal(plan, person['name'])
        actual = person['goal']
        if (actual.get('description') != expected['description'] or
                Counter(canonical(c) for c in actual.get('conditions', [])) != Counter(canonical(c) for c in expected['conditions'])):
            fail('plan.coverage', '確定した目的の条件が欠落・追加・変更されています: ' + person['name'])
    for premise in plan['initial_items']:
        item = next(i for i in draft['items'] if i['name'] == premise['item'])
        count = sum(h['count'] for h in item.get('holders', []) if h['person'] == premise['person'])
        if count < premise['count']:
            fail('plan.initial_items', '確定した初期所持の前提が失われています')
    reports = []
    with tempfile.TemporaryDirectory(prefix='wb-goal-plan-') as tmp:
        root = Path(tmp)
        shutil.copytree(Path(repo) / 'templates/basic', root / 'templates/basic')
        LibraryStore(root)._publish_world('preview', world=world, subjects=subjects)
        iw, actors = _load_world_and_subjects(root / 'projects/preview', root / 'templates/basic')
        iw.bind_subjects(actors)
        for g in plan['goals']:
            _, expression = compile_goal({'description': g['description'], 'conditions': g['conditions']},
                g['actor'], set(actors), set(plan['places']), set(plan['items']), set(plan['facts']),
                'plan.' + g['id'], days=iw.days, slots_per_day=len(iw.slots))
            if expression is None:
                fail('plan.' + g['id'], 'この目的の自動判定は未対応です。内容確認が必要です')
            achieved = bool(compile_predicate(expression).evaluate(iw.namespace(actors[g['actor']], list(actors.values()), turn=1, day=1)))
            if g['initial_status'] != 'unspecified' and achieved != (g['initial_status'] == 'achieved'):
                fail('plan.' + g['id'], '確定した目的の開始時の期待状態と一致しません')
            condition = g['conditions'][0]
            path_checked = condition['type'] in ('reach', 'deliver')
            if path_checked:
                start = next(p['entry'] for p in draft['people'] if p['name'] == g['actor'])
                visited, todo = set(), [start]
                while todo:
                    place = todo.pop()
                    if place in visited: continue
                    visited.add(place)
                    todo.extend(r['to'] for r in draft['routes'] if r['from'] == place)
                if condition['place'] not in visited:
                    fail('plan.' + g['id'] + '.route', '目的地へ向かう有向経路がありません')
            reports.append({'id': g['id'], 'actor': g['actor'], 'initially_achieved': achieved,
                'expected': g['initial_status'], 'structural_route_checked': path_checked})
    return draft, summary, {'goals': reports, 'coverage': 'all confirmed atomic objectives retained',
        'reachability_limit': 'Graph connectivity only; time, item/action constraints and eventual success are not proven.'}


def annotations(plan, overview):
    return {'version':1,'origin':'ai_interpretation','notes':[{'id':g['id'],'actor':g['actor'],
        'text':g['source_note'],'kind':'same_wording' if g['source_note'] in overview else 'interpretation'} for g in plan['goals']],
        'supplemented_names':{k:[n for n in plan[k] if n not in overview] for k in ['people','places','items','facts']},
        'assumptions':deepcopy(plan['assumptions']),
        'notice':'AIが概要を解釈・補完した案です。原文の引用や確認済みの事実とは限りません。設定画面で修正できます。'}


def atomic_review_schema(draft):
    """Separate three-stage audit envelope; the legacy review contract is unchanged."""
    from execution.draft_generation_contract import review_schema
    condition = extraction_schema()['properties']['goals']['items']['properties']['conditions']['items']
    return obj({'alignment': review_schema(draft), 'coverage_complete': {'type': 'boolean'},
        'atomic_objectives': rows(obj({'actor': text(120), 'source_note': text(1600),
            'conditions': rows(condition, 1),
            'initial_status': {'type': 'string', 'enum': ['unachieved', 'achieved', 'unspecified']},
            'plan_id': {'type': 'string', 'maxLength': 4},
            'verdict': {'type': 'string', 'enum': ['matched', 'missing', 'uncertain', 'unsupported']}}), 16, 1)})


def atomic_review_instructions():
    return (
        '\nTHREE-STAGE ATOMIC AUDIT: Return an envelope with alignment (the usual review), '
        'coverage_complete (boolean), and atomic_objectives. First independently enumerate ALL '
        'atomic objectives expressed in the original overview, for ALL actors, not just objectives '
        'already present in the plan. A description mentioning two aims does not prove two executable '
        'conditions. For example, going to a place AND befriending someone requires separate reach '
        'and befriend objectives. Do not infer goals from background events, negations or mere means. '
        'Deliver already combines possession and arrival; do not split it into invented extra aims. '
        'Then compare each objective against the plan and the actual draft conditions. Each row has '
        'actor, source_note (brief evidence/interpretation), conditions (exactly one existing typed '
        'condition, or empty for unsupported), initial_status, plan_id (empty if missing), and verdict '
        '(matched/missing/uncertain/unsupported). Keep interpretations and supplemented names allowed. '
        'If the overview specifies no numeric threshold, use the plan proposed threshold rather than '
        'inventing another. Explicit numeric requirements must be checked against the overview. '
        'Use missing for any omitted objective, even when the description mentions it. Use uncertain '
        'when coverage cannot be established. coverage_complete is true only if every overview '
        'objective and every planned objective is accounted for with no omission. No silent repair. '
        'The plan is an AI interpretation, NOT authoritative evidence of complete overview coverage.\n')


def checked_atomic_review(value, plan, draft):
    """Fail closed on structured audit disagreement; this is not a semantic proof."""
    from execution.draft_alignment import checked_review
    def bad():
        fail('alignment.atomic_coverage', '概要の各目的と実際の達成条件の対応を確認できませんでした。目的の抜けや対応を確認してください。')
    if (not isinstance(value, dict) or set(value) != {'alignment', 'coverage_complete', 'atomic_objectives'}
            or value['coverage_complete'] is not True):
        bad()
    audit = value['atomic_objectives']
    if not isinstance(audit, list) or not 1 <= len(audit) <= 16:
        bad()
    goals = {g['id']: g for g in plan['goals']}
    seen, signatures = set(), set()
    actual = {p['name']: Counter(canonical(c) for c in p['goal']['conditions']) for p in draft['people']}
    required = {}
    for row in audit:
        if not isinstance(row, dict) or set(row) != {'actor', 'source_note', 'conditions', 'initial_status', 'plan_id', 'verdict'}:
            bad()
        if (not isinstance(row['actor'], str) or row['actor'] not in plan['people']
                or not isinstance(row['source_note'], str) or not row['source_note'].strip() or len(row['source_note']) > 1600
                or not isinstance(row['plan_id'], str) or row['plan_id'] not in goals or row['plan_id'] in seen
                or row['verdict'] != 'matched' or row['initial_status'] not in ('unachieved', 'achieved', 'unspecified')
                or not isinstance(row['conditions'], list) or len(row['conditions']) != 1):
            bad()
        compile_goal({'description': row['source_note'], 'conditions': row['conditions']}, row['actor'],
            set(plan['people']), set(plan['places']), set(plan['items']), set(plan['facts']),
            'alignment.atomic_coverage', required=True, days=10000)
        goal = goals[row['plan_id']]
        # A purpose description is not an implementation of a condition. Keep this
        # bounded guard on goal descriptions, never arbitrary overview/background.
        from execution.draft_alignment import goal_intents, implied
        described = goal_intents(goal['description'], goal['actor'], draft['people'])
        if re.search(r'(?:へ|に)向か(?:いたい|う(?:こと)?(?:[。、]|$)|い[、,])', goal['description']):
            described.add('reach')
        # Atomic rows may retain overlapping prose. Check described intentions
        # against all this actor's conditions, while exact row mapping stays below.
        actor_types = {c['type'] for g in plan['goals'] if g['actor'] == goal['actor']
                       for c in g['conditions']}
        if described - implied(actor_types):
            bad()
        if (goal['actor'] != row['actor'] or goal['initial_status'] != row['initial_status']
                or canonical(goal['conditions']) != canonical(row['conditions'])):
            bad()
        signature = (row['actor'], canonical(row['conditions']))
        if signature in signatures:
            bad()
        signatures.add(signature)
        seen.add(row['plan_id'])
        required.setdefault(row['actor'], Counter()).update(canonical(c) for c in row['conditions'])
    if seen != set(goals) or any(required.get(actor, Counter()) != conditions for actor, conditions in actual.items()):
        bad()
    return checked_review(value['alignment'], draft)
