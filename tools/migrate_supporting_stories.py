"""Add reviewable personal accompaniment purposes; preserve source editions.

This authoring tool selects an existing protagonist's mobile companions.
The engine has no story, role or character-name dispatch.
"""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import yaml

ROOT=Path(__file__).resolve().parents[1]
SOURCES=('momotaro','momotaro_plus','momotaro_plus2','momotaro_plus3')


def migrate(source, *, root=None):
    root=Path(root or ROOT)
    folder=root/'projects'/(source+'_shared_planning')
    original=[folder/'world.yaml',*sorted((folder/'subjects').glob('*.yaml'))]
    world=yaml.safe_load(original[0].read_text(encoding='utf8'))
    cast=[(p.name,yaml.safe_load(p.read_text(encoding='utf8'))) for p in original[1:]]
    hero=world['protagonist'];leader=next(p for _,p in cast if p['id']==hero)
    people=[p for _,p in cast if p['id']!=hero and p['id'] in leader.get('companions',[])
            and 'move' in p['verbs']]
    if not people:raise ValueError('No mobile companions to migrate')
    conditions=world['concerns']['conditions']
    definitions=world['goal_rules']['definitions']
    def condition(cid,label,test):
        conditions.append(dict(id=cid,label=label,mode='achieve',visibility='observed',test=test))
        return {'condition':cid}
    alive=condition('escort_leader_active',hero+'が行動できる',dict(kind='vitality',actor=hero,allowed=['alive','revived']))
    dead=condition('escort_leader_dead',hero+'が死亡した',dict(kind='vitality',actor=hero,allowed=['dead']))
    for index,person in enumerate(people,1):
        who=person['id'];cid='escort_'+str(index)
        together=condition(cid,who+'が'+hero+'に同行し支える',dict(kind='co_location',actor=who,target=hero))
        bond=condition(cid+'_accepted',who+'が'+hero+'に協力する親しさ',dict(kind='relation',observer=who,target=hero,minimum=world['companionship']['threshold']))
        definitions.append(dict(condition=cid,completion='period_end',
            success={'any':[{'not':bond},{'all':[together,alive]}]},
            failure={'all':[bond,dead]},period=deepcopy(definitions[0]['period'])))
        person['commitments']=[dict(condition=cid,priority=8,required=False)]
    world['planning']['actors']=[hero,*[p['id'] for p in people]]
    world['name']+='・仲間の目的も移行'
    target=root/'projects'/(source+'_companions_planning')
    manifest=dict(source=folder.name,target=target.name,
        planning_actors=world['planning']['actors'],
        legacy_supporting_actors=[p['id'] for _,p in cast if p['id'] not in world['planning']['actors']],
        authored_assumptions=[
            'Mobile declared companions prioritize accompaniment and rescue when their own affinity reaches the existing companionship threshold.',
            'This is an authored purpose, not inferred from the old treasure goal or personality.',
            'Below that threshold they wait; no acceptance or gift is forced.',
            'Temporary separation and downing can be repaired; death is terminal once observed.',
            'Existing routes, boat ownership, range and rescue permissions remain binding; no automatic group transport is added.'],
        source_sha256={p.relative_to(folder).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in original})
    outputs={'world.yaml':yaml.safe_dump(world,allow_unicode=True,sort_keys=False),
        **{'subjects/'+name:yaml.safe_dump(person,allow_unicode=True,sort_keys=False) for name,person in cast},
        'migration.json':json.dumps(manifest,ensure_ascii=False,indent=2)+'\n'}
    for rel,text in outputs.items():
        path=target/rel
        if path.exists() and path.read_text(encoding='utf8')!=text:raise FileExistsError('Preserve edited edition: '+str(path))
    for rel,text in outputs.items():
        path=target/rel;path.parent.mkdir(parents=True,exist_ok=True)
        if not path.exists():path.write_text(text,encoding='utf8',newline='\n')
    return manifest


if __name__=='__main__':
    for source in SOURCES:print(json.dumps(migrate(source),ensure_ascii=False))
