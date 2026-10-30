"""Copy legacy stories into reviewable, opt-in shared-planning editions.

The source directories are never edited. Cast, world mechanisms and native
verbs are copied; this is an explicit protagonist migration, not a claim that
all supporting characters or all legacy calendar models have been converted.
"""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import yaml

ROOT = Path(__file__).resolve().parents[1]
FAMILIES = {'momotaro':'journey','momotaro_plus':'journey','momotaro_plus2':'journey',
            'momotaro_plus3':'journey','romance':'relationship','detective':'inquiry',
            'romance-x-detective':'relationship'}


def migrate(source):
    family = FAMILIES[source]
    folder = ROOT/'projects'/source
    raw = yaml.safe_load((folder/'world.yaml').read_text(encoding='utf8'))
    people = [(p.name,yaml.safe_load(p.read_text(encoding='utf8'))) for p in sorted((folder/'subjects').glob('*.yaml'))]
    hero = raw['protagonist']; hero_spec = next(p for _,p in people if p['id']==hero)
    end = raw['time']['days']*len(raw['time']['slots'])
    conditions=[]; outcomes=[]; changes=[]
    def condition(cid,label,test,visibility='observed'):
        conditions.append(dict(id=cid,label=label,mode='achieve',visibility=visibility,test=test)); return {'condition':cid}
    if family=='journey':
        foe=raw['antagonist']; item=hero_spec['goal']['target']; home=hero_spec['goal']['deliver_to']
        anchor=condition('journey_return','宝を故郷へ持ち帰る',dict(kind='zone',actor=hero,equals=home))
        treasure=condition('journey_treasure','宝を手に入れる',dict(kind='inventory',actor=hero,item=item,minimum=1))
        defeated=condition('journey_defeated','相手に実際に勝利した',dict(kind='state',key='native_victory',equals='done'))
        success={'all':[anchor,treasure,defeated]};root='journey_return'
        outcomes=[dict(key='native_victory',verb='fight',match={'winner':hero,'loser':foe},value='done')]
        # Initial location beliefs are authored knowledge, never a live remote feed.
        for who,zone in [(foe,next(p for _,p in people if p['id']==foe)['range']['entry'])]:
            cid='known_destination';condition(cid,'相手の居場所の見込み',dict(kind='zone',actor=who,equals=zone))
            hero_spec.setdefault('beliefs',{})[cid]={'value':'true','confidence':1.0}
        cid='known_treasure';condition(cid,'相手が宝を持つ見込み',dict(kind='inventory',actor=foe,item=item,minimum=1))
        hero_spec.setdefault('beliefs',{})[cid]={'value':'true','confidence':1.0}
        for event in raw.get('scheduled_events',[]):
            if hero in event.get('targets',[]) and event.pop('force_action',None): changes.append('removed protagonist force_action: '+event['id'])
        # Retain the original conditional home exclusion; it no longer forces departure.
        changes += ['added explicit actual victory requirement; old item-only ending retained in source edition',
                    'authored initial enemy location and treasure possession beliefs; subsequent movement is not public']
    elif family=='relationship':
        # This is the original ending definition, copied explicitly, not inferred consent.
        anchor=condition('relationship_mutual','互いの好意を確かめる',dict(kind='relation',observer='A',target='B',minimum=.6),'public')
        reciprocal=condition('relationship_reciprocal','相手からの好意',dict(kind='relation',observer='B',target='A',minimum=.6),'public')
        success={'all':[anchor,reciprocal]};root='relationship_mutual'
        changes += ['both relationship scores declared public in this edition, as a reviewable simplification',
                    'thresholds preserve legacy ending; no new consent protocol is inferred']
    else:
        root='inquiry_solved'; fact='culprit'
        anchor=condition(root,'証拠をもとに事件を解決する',dict(kind='state',key='native_exposed',equals='done'))
        success=anchor;outcomes=[dict(key='native_exposed',verb='confront',match={'actor':hero,'fact':fact,'result':'exposed'},value='done')]
        # An initially known stationary suspect roster, not identities from truth.
        for index,(_,person) in enumerate(people):
            if person['id']==hero or len(person['range']['zones'])!=1:continue
            cid=f'known_person_{index}'
            condition(cid,'所在を知っている人物',dict(kind='zone',actor=person['id'],equals=person['range']['entry']))
            hero_spec.setdefault('beliefs',{})[cid]={'value':'true','confidence':1.0}
        changes += ['declared initial stationary suspect locations; culprit truth is never supplied to planning']
    state={r['key']:'pending' for r in outcomes} or {'native_placeholder':'unused'}
    raw['name'] += '（既存物語・共通計画版）'
    raw['concerns']={'version':2,'conditions':conditions}
    raw['goal_rules']={'version':1,'definitions':[dict(condition=root,success=success,failure={'constant':False},period={'start':1,'end':end})]}
    raw['responses']=dict(version=2,state=state,locations={k:hero_spec['range']['entry'] for k in state},signals=[],actions=[],channels=[],events=[],windows=[])
    raw['planning']=dict(version=1,actors=[hero],max_depth=24,max_nodes=1024)
    raw['native_planning']=dict(version=1,outcomes=outcomes,inquiry_facts=[f['id'] for f in raw.get('facts',[]) if not f.get('values') and f.get('sources')])
    hero_spec['commitments']=[dict(condition=root,priority=10,required=False)]
    label=('鬼に勝利し、宝を村へ持ち帰った' if family=='journey' else next(iter(raw['ending']))['label'])
    raw['ending']=[dict(id='shared_goal',when=f"objective_achieved('{root}')",label=label)]
    raw['target_ending']=['shared_goal']
    target=ROOT/'projects'/(source+'_shared_planning')
    target.mkdir(exist_ok=True);(target/'subjects').mkdir(exist_ok=True)
    manifest=dict(source=source,target=target.name,family=family,planning_actors=[hero],
                  legacy_supporting_actors=[p['id'] for _,p in people if p['id']!=hero],changes=changes,
                  source_sha256={p.relative_to(folder).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in [folder/'world.yaml',*sorted((folder/'subjects').glob('*.yaml'))]})
    generated={target/'world.yaml':yaml.safe_dump(raw,allow_unicode=True,sort_keys=False),
               **{target/'subjects'/name:yaml.safe_dump(person,allow_unicode=True,sort_keys=False) for name,person in people},
               target/'migration.json':json.dumps(manifest,ensure_ascii=False,indent=2)+'\n'}
    # Check every output before writing any, so a user's edited edition is safe.
    for path,text in generated.items():
        if path.exists() and path.read_text(encoding='utf8')!=text:
            raise FileExistsError('Existing edition differs; preserve it and choose a new destination: '+str(path))
    for path,text in generated.items():
        if not path.exists():path.write_text(text,encoding='utf8',newline='\n')
    return manifest

if __name__=='__main__':
    for source in FAMILIES: print(json.dumps(migrate(source),ensure_ascii=False))
