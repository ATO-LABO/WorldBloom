"""CPU/API-zero mechanism witnesses for approved festival steps1--3.

These three explicit choice inputs are acceptance scenarios, not action search
or selected literary plots. They share the same frozen world and people.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import yaml
from engine.calendar_v2 import compile_calendar, minute_for
from engine.calendar_checkpoint import digest, strict_json
from engine.promises_v2 import PromiseRegistry
from engine.sim import Simulation
from engine.subject import Subject
from engine.world import World
from engine.calendar_inputs import load_project, freeze_project

PROJECT = ROOT/'projects/cultural_festival_v2'
ORIGINAL = ROOT/'projects/cultural_festival'
NAMES = ['蒼', '紗季', '蓮', '灯', '悠', '実']


def fixture(*, festival='2026-10-15', prep_days=2):
    if type(prep_days) is not int or not 1 <= prep_days <= 30:
        raise ValueError('Preparation days must be1..30 calendar days')
    last = date.fromisoformat(festival); first = last-timedelta(days=prep_days)
    normal = [{'id': 'morning', 'name': '朝', 'start': '07:45', 'end': '08:30',
               'overnight': False, 'interval_minutes': 15},
              {'id': 'lunch', 'name': '昼休み', 'start': '12:20', 'end': '13:00',
               'overnight': False, 'interval_minutes': 10},
              {'id': 'after', 'name': '放課後', 'start': '15:30', 'end': '18:00',
               'overnight': False, 'interval_minutes': 30}]
    weekend = [dict(normal[0], name='休日午前', start='09:00', end='12:00', interval_minutes=60),
               dict(normal[1], name='休日休憩', start='12:00', end='13:00', interval_minutes=30),
               dict(normal[2], name='休日午後', start='13:00', end='17:00', interval_minutes=60)]
    festival_bands = [dict(b, name='文化祭'+b['name'], start=s, end=e, interval_minutes=60)
                      for b, s, e in zip(normal, ['06:00', '12:00', '13:00'], ['12:00', '13:00', '17:00'])]
    calendar = {'schema_version': 1, 'period': {'start_date': first.isoformat(), 'end_date': last.isoformat()},
                'normal': {'bands': normal, 'weekdays': {'5': weekend, '6': weekend}, 'holidays': []},
                'specials': [{'id': 'festival', 'name': '文化祭当日', 'enabled': True,
                              'selector': {'kind': 'last', 'offset': 0}, 'priority': 10,
                              'bands': festival_bands}]}
    world = deepcopy(yaml.safe_load((ORIGINAL/'world.yaml').read_text(encoding='utf-8')))
    world['name'] = '凪野高校・文化祭 calendar_promises_v2（steps1〜3確認版）'
    world['time']['days'] = prep_days+1
    world['zones'] += [{'name': p} for p in ['教室', '実行委員室', '放送室']]
    graph = {z['name']: {} for z in world['zones']}
    for start, edges in world['routes'].items():
        for edge in edges:
            graph[start][edge['to']] = 10
    for a, b in [('部室', '教室'), ('展示室', '教室'), ('校門', '教室'),
                 ('部室', '実行委員室'), ('体育館', '放送室')]:
        graph[a][b] = graph[b][a] = 10
    world['routes'] = {p: [{'to': q, 'cost': 1} for q in sorted(edges)] for p, edges in graph.items()}
    world['scheduled_events'] = [
        dict(event, day=prep_days+1) for event in world['scheduled_events']
        if event['id'] != 'gate-opens']
    world['slot_execution'] = {'mode': 'calendar_promises_v2', 'contract': 'contract_v2.yaml',
                               'subjects': [{'id': name, 'path': ('subjects/' if i < 4 else '')+str(i+1).zfill(2)+'_'+name+'.yaml'}
                                            for i, name in enumerate(NAMES)]}
    world['cultural_festival'] = {'projection_only': False, 'contract': 'contract_v2.yaml',
                                 'runtime_note': '別版。v1の30分移動/pledgeを暗黙変換しない。'}
    roles = {'蒼': ['演劇主役', '展示制作'], '紗季': ['演劇進行'], '蓮': ['演劇相手役'],
             '灯': ['展示制作', '展示受付'], '悠': ['実行委員'], '実': ['放送']}
    for p in roles:
        roles[p].append('student:'+p)
    tasks = {}
    def task(ident, label, place, units, minutes, actor_roles, deps=(), resource=None, operation='work', materials=None):
        tasks[ident] = {'label': label, 'place': place, 'units': units, 'minutes_per_unit': minutes,
                        'roles': list(actor_roles), 'dependencies': list(deps),
                        'resource': resource or ident, 'materials_per_unit': materials or {}, 'operation': operation}
    task('panel-headline', 'P1の見出し取付1枚', '展示室', 1, 20, ['展示制作'], resource='panel-table')
    task('panel-cards', 'P1の説明カード4枚取付', '展示室', 4, 10, ['展示制作'],
         resource='panel-table', materials={'説明カード': 1})
    task('panel-photo', '完成P1確認写真1枚', '展示室', 1, 2, ['展示制作'],
         deps=['panel-headline', 'panel-cards'])
    task('photo-handoff', '灯へ完成写真1枚手渡し', '展示室', 1, 2, ['展示制作'],
         deps=['panel-photo'], operation='deliver')
    task('reception-signs', '展示受付札3枚', '展示室', 3, 10, ['展示受付'], materials={'受付札用紙': 1})
    task('costume-ready', '衣装準備', '部室', 1, 20, ['演劇進行', '演劇主役'])
    task('practice-ready', '骨子習得済み台詞の稽古', '部室', 1, 30, ['演劇主役', '演劇相手役'])
    task('sound-ready', '音響確認', '体育館', 1, 20, ['演劇進行'])
    task('venue-ready', '会場利用と案内確認', '実行委員室', 1, 20, ['実行委員'])
    task('broadcast-ready', '放送案内準備', '放送室', 1, 20, ['放送'], deps=['venue-ready'])
    days = [first+timedelta(days=i) for i in range(prep_days)]
    school_days = sum(d.weekday() < 5 for d in days)
    for p in NAMES:
        task('class-am:'+p, '午前授業への参加', '教室', max(1, school_days), 230, ['student:'+p], resource='seat:'+p)
        task('class-pm:'+p, '午後授業への参加', '教室', max(1, school_days), 150, ['student:'+p], resource='seat:'+p)
    contract = {'version': 3, 'calendar': calendar, 'timezone': 'Asia/Tokyo', 'start_time': '06:00',
                'end_time': '17:00', 'travel_minutes': graph, 'roles': roles, 'tasks': tasks,
                'materials': {'説明カード': 4, '受付札用紙': 3},
                'facility_hours': {p: {'opens': '08:00', 'closes': '18:00'} for p in graph if p != '自宅'},
                'observers': [{'id': 'play-opening-observation', 'event_id': 'schedule-3',
                               'verb': 'play_opening_observation', 'fields': {
                                   'primary_present': {'check': 'at', 'actor': '蒼', 'place': '体育館'},
                                   'partner_present': {'check': 'at', 'actor': '蓮', 'place': '体育館'},
                                   'preparation': {'group': {task: {'check': 'task_complete', 'task_id': task}
                                                            for task in ('costume-ready', 'practice-ready', 'sound-ready')}},
                                   'consent': {'literal': 'not_inferred_from_presence'},
                                   'performance_started': {'literal': False},
                                   'performance_internals': {'literal': 'unsupported'}}}],
                'appraisal': {'fulfilled': 0.05, 'unexplained_breach': -0.05},
                'initial_task_knowledge': {'蒼': ['panel-headline', 'panel-cards'], '灯': ['panel-headline', 'panel-cards']},
                'assumptions': [
                    '本人レビュー前のv2仮設定。初期4人の原YAML・性格・目的・知識・所持品は保持。',
                    '追加2人は悠=実行委員、実=放送。学校予定と担当工程を持つ固定人物。性格数値は中立寄り仮値。',
                    '教室/実行委員室/放送室を追加、隣接徒歩10分。v1の1枠移動を10分と再解釈しない。',
                    'P1下地は開始前に灯が完成済みという新v2仮定。残り見出し20分+説明カード4枚各10分。',
                    '写真撮影/手渡し各2分、受付札3枚各10分。作業は一人ずつ同一机を占有。',
                    '比較3ケースは明示行動入力の機構受入witness。行動探索・文学作品・自然な内面生成ではない。',
                    '授業は平日のみ、休日は自由時間窓。夜間宛先通知は受信と翌6時既読を区別。',
                    '信頼に対応する既存有向affinity軸を観測時のみ±.05。再相談そのものへの報酬なし。',
                    '共同運搬/複数人速度合成/公演内部/探索codec/作品本文は今回未実装。']}
    extras = []
    base = yaml.safe_load((ORIGINAL/'subjects/01_蒼.yaml').read_text(encoding='utf-8'))
    for ident, zone, goal in [('悠', '部室', '会場を調整する'), ('実', '部室', '案内放送を準備する')]:
        extra = deepcopy(base); extra['id'] = ident
        extra['traits'] = {'social': .6, 'stubbornness': .5, 'curiosity': .5, 'diligence': .8, 'temper': .2}
        extra['identity'] = {'true': ident, 'displayed': ident}
        extra['inventory'] = {}; extra['knowledge'] = ['共通予定']; extra['relations'] = {}
        extra['range']['entry'] = zone; extra['range']['zones'] = list(graph)
        extra['cultural_festival'] = {'goals': [goal], 'notes': '追加役割の仮値。全比較で固定、本人レビュー待ち。'}
        extras.append(extra)
    return world, contract, extras


def _write_fixture_files(path, world, contract, extras):
    for name, value in [('world.yaml', world), ('contract_v2.yaml', contract)]:
        (path/name).write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding='utf-8')
    (path/'subjects').mkdir()
    originals = [yaml.safe_load(p.read_text(encoding='utf-8'))
                 for p in sorted((ORIGINAL/'subjects').glob('*.yaml'))]
    people = originals + extras
    by_id = {person['id']: person for person in people}
    if set(by_id) != set(NAMES) or len(people) != len(NAMES):
        raise ValueError('Festival adapter requires its explicitly configured six-person fixture')
    for reference in world['slot_execution']['subjects']:
        person = deepcopy(by_id[reference['id']])
        # This adapter authors new project-local range settings. The original
        # project is never edited and the generic loader never expands ranges.
        person['range']['zones'] = [zone['name'] for zone in world['zones']]
        (path/reference['path']).write_text(yaml.safe_dump(person, allow_unicode=True, sort_keys=False), encoding='utf-8')


def write_fixture(path, **options):
    path = Path(path)
    if path.exists() and any(path.iterdir()):
        raise ValueError('Existing project is protected')
    path.mkdir(parents=True, exist_ok=True)
    _write_fixture_files(path, *fixture(**options))


def load(project=PROJECT):
    return load_project(Path(project)/'world.yaml')


def choices(world, case):
    if case not in ('fulfilled', 'partial', 'renegotiated'):
        raise ValueError('Unknown witness case')
    c = world.slot_contract; cal = compile_calendar(c['calendar'], timezone=c['timezone'], scheduled_events=list(world.scheduled_events))
    first = date.fromisoformat(cal['start_date']); last = date.fromisoformat(cal['end_date'])
    due_date = last-timedelta(days=1)
    at = lambda day, clock: minute_for(cal, str(day), clock)
    commands = []
    def add(ident, actor, day, clock, verb, **extra):
        commands.append({'id': ident, 'actor': actor, 'at': at(day, clock), 'verb': verb, **extra})
    # NPC/school witness rules are fixed across all three cases. Attendance is
    # ordinary duration-bearing work and movement, not schedule teleportation.
    for i in range((last-first).days):
        d = first+timedelta(days=i)
        if d.weekday() >= 5:
            continue
        for p in NAMES:
            start = '自宅' if p == '蒼' and d == first else ('展示室' if p == '灯' else '部室')
            if p == '蒼' and d == first:
                add(f'{i}-{p}-gate', p, d, '08:00', 'move', to='校門')
                add(f'{i}-{p}-club', p, d, '08:10', 'move', to='部室')
            # End-of-day positioning below makes all other starts explicit.
            add(f'{i}-{p}-classroom', p, d, '08:20', 'move', to='教室')
            add(f'{i}-{p}-class-am', p, d, '08:30', 'work', task_id='class-am:'+p, minutes=230)
            add(f'{i}-{p}-class-pm', p, d, '13:00', 'work', task_id='class-pm:'+p, minutes=150)
            dest = '展示室' if p in ('蒼', '灯') and d == due_date else '部室'
            add(f'{i}-{p}-after', p, d, '15:30', 'move', to=dest)
        if d == first:
            add('saki-costume', '紗季', d, '15:40', 'work', task_id='costume-ready', minutes=20)
            add('ren-practice', '蓮', d, '15:40', 'work', task_id='practice-ready', minutes=30)
            add('saki-gym', '紗季', d, '16:00', 'move', to='体育館')
            add('saki-sound', '紗季', d, '16:10', 'work', task_id='sound-ready', minutes=20)
            add('saki-return', '紗季', d, '16:30', 'move', to='部室')
            add('yu-office', '悠', d, '15:40', 'move', to='実行委員室')
            add('yu-venue', '悠', d, '15:50', 'work', task_id='venue-ready', minutes=20)
            add('yu-return', '悠', d, '16:10', 'move', to='部室')
            add('min-gym', '実', d, '16:20', 'move', to='体育館')
            add('min-booth', '実', d, '16:30', 'move', to='放送室')
            add('min-broadcast', '実', d, '16:40', 'work', task_id='broadcast-ready', minutes=20)
            add('min-gym-return', '実', d, '17:00', 'move', to='体育館')
            add('min-club-return', '実', d, '17:10', 'move', to='部室')
    # Agreement is made by real two-minute addressed actions; no automatic
    # promise fulfillment or automatic beneficiary plan selection.
    terms = {'promise_id': 'p-panel', 'version': 1, 'proposer': '蒼', 'promisor': '蒼', 'beneficiary': '灯',
             'acceptance_parties': ['蒼', '灯'], 'place': '展示室', 'start': at(first, '12:20'),
             'deadline': at(due_date, '17:00'), 'grace_until': None,
             'requirements': [{'task_id': 'panel-headline', 'units': 1, 'contribution_by': '蒼'},
                              {'task_id': 'panel-cards', 'units': 4, 'contribution_by': '蒼'},
                              {'task_id': 'photo-handoff', 'units': 1, 'contribution_by': '蒼', 'delivered_to': '灯'}]}
    reg = PromiseRegistry(set(NAMES), set(c['tasks']), set(world.zones)); th = reg.terms_hash(terms)
    add('panel-offer', '蒼', first, '12:20', 'offer', terms=terms)
    add('panel-accept-ao', '蒼', first, '12:22', 'accept', promise_id='p-panel', version=1, terms_hash=th)
    add('panel-accept-akari', '灯', first, '12:22', 'accept', promise_id='p-panel', version=1, terms_hash=th)
    add('akari-plan', '灯', first, '12:24', 'depend', promise_id='p-panel', plan_id='akari-reception',
        then_task='reception-signs', fallback_task='panel-cards')
    # Day1 ends in original-like locations, preserving a shared next morning.
    if first != due_date:
        add('akari-return-exhibit', '灯', first, '17:30', 'move', to='展示室')
    add('ao-headline', '蒼', due_date, '15:40', 'work', task_id='panel-headline', minutes=20)
    if case == 'fulfilled':
        add('ao-cards', '蒼', due_date, '16:00', 'work', task_id='panel-cards', minutes=40)
        add('ao-photo', '蒼', due_date, '16:40', 'work', task_id='panel-photo', minutes=2)
        add('ao-delivery', '蒼', due_date, '16:42', 'deliver', task_id='photo-handoff', target='灯')
        add('akari-signs', '灯', due_date, '17:00', 'work', task_id='reception-signs', minutes=30)
    elif case == 'partial':
        add('ao-cards', '蒼', due_date, '16:00', 'work', task_id='panel-cards', minutes=20)
        add('ao-other-choice', '蒼', due_date, '16:20', 'move', to='部室')
        add('ao-extra-practice', '蒼', due_date, '16:30', 'wait', minutes=30)
        add('akari-fallback', '灯', due_date, '17:00', 'work', task_id='panel-cards', minutes=20)
        # Missed reception opportunity remains real unfinished work.
    else:
        newer = deepcopy(terms); newer['version'] = 2
        newer['deadline'] = at(due_date, '16:30')
        newer['requirements'] = [{'task_id': 'panel-headline', 'units': 1, 'contribution_by': '蒼'},
                                {'task_id': 'panel-cards', 'units': 2, 'contribution_by': '蒼'}]
        nh = reg.terms_hash(newer)
        add('ao-change', '蒼', due_date, '12:20', 'change', terms=newer)
        add('change-ao', '蒼', due_date, '12:22', 'accept_change', promise_id='p-panel', version=2, terms_hash=nh)
        add('change-akari', '灯', due_date, '12:22', 'accept_change', promise_id='p-panel', version=2, terms_hash=nh)
        add('akari-replan', '灯', due_date, '12:24', 'replan', promise_id='p-panel',
            plan_id='akari-reception', then_task='reception-signs', fallback_task='panel-cards')
        add('ao-cards', '蒼', due_date, '16:00', 'work', task_id='panel-cards', minutes=20)
        add('akari-new-duty', '灯', due_date, '16:20', 'work', task_id='panel-cards', minutes=20)
        add('akari-signs', '灯', due_date, '17:00', 'work', task_id='reception-signs', minutes=30)
    for p in ('蒼',):
        if p == '蒼' and case == 'partial':
            continue
        add('return-'+p, p, due_date, '17:40', 'move', to='部室')
    # On the festival day all six roles physically take their places.
    for p in ('蒼', '紗季', '蓮', '悠', '実'):
        add('festival-gym-'+p, p, last, '08:00', 'move', to='体育館')
    return commands


def run_case(out, case, *, project=PROJECT, resume=False, until=None):
    world, subjects = load(project)
    simulation = Simulation(0, world, subjects, Path(out))
    simulation.slot_runtime.configure(choices(world, case))
    return simulation.slot_runtime.run(resume=resume, until=until)


def freeze_design(destination, design):
    """Explicit festival-only adapter; generic projects use freeze_project.

    The PR9 source design and its design-only flags are retained in provenance
    metadata. This creates a new school fixture; it is not arbitrary world
    transfer and does not edit an existing project or design.
    """
    if not isinstance(design, dict):
        raise ValueError('Calendar design must be an object')
    compile_calendar(design)
    settings = deepcopy(design.get('settings', design))
    first = date.fromisoformat(settings['period']['start_date'])
    last = date.fromisoformat(settings['period']['end_date'])
    world, contract, extras = fixture(festival=str(last), prep_days=(last-first).days)
    contract['calendar'] = settings
    compile_calendar(settings, scheduled_events=world['scheduled_events'])
    metadata = {'adapter': 'cultural_festival_sample', 'source_design': deepcopy(design),
                'source_world_revision': design.get('world_revision'), 'source_design_id': design.get('id'),
                'transfer': {'explicit': True, 'festival_date': str(last),
                             'scheduled_event_day': (last-first).days+1,
                             'note': 'New school fixture; source project/design/old runs unchanged'}}
    with tempfile.TemporaryDirectory(prefix='worldbloom-festival-adapter-') as temporary:
        project = Path(temporary)
        _write_fixture_files(project, world, contract, extras)
        return freeze_project(project/'world.yaml', destination, metadata=metadata)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--case', choices=('fulfilled', 'partial', 'renegotiated'), required=True)
    parser.add_argument('--project', type=Path, default=PROJECT)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--until-minute', type=int)
    parser.add_argument('--design', type=Path, help='PR9 settings/design version JSON for explicit transfer')
    parser.add_argument('--freeze-to', type=Path, help='New empty project for executable v2 fixed version')
    args = parser.parse_args()
    try:
        if bool(args.design) != bool(args.freeze_to):
            raise ValueError('--design and --freeze-to must be used together')
        if args.design:
            freeze_design(args.freeze_to, strict_json(args.design.read_text(encoding='utf-8')))
            args.project = args.freeze_to
        path = run_case(args.out, args.case, project=args.project, resume=args.resume, until=args.until_minute)
    except ValueError as exc:
        print(json.dumps({'status': 'invalid_config_or_resume', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({'status': 'completed_or_checkpointed', 'layers': str(path), 'api_calls': 0, 'gpu': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
