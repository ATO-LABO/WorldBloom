"""Opt-in desired conditions over existing state, with separate actor beliefs."""
from copy import deepcopy
import math


KINDS = {
    'vitality': {'kind','actor','allowed'}, 'state': {'kind','key','equals'},
    'knowledge': {'kind','fact','actors','rule'}, 'inventory': {'kind','actor','item','minimum'},
    'zone': {'kind','actor','equals'}, 'relation': {'kind','observer','target','minimum'},
}
VITALITIES = {'alive','downed','revived','dead'}


def identifier(value):
    if (not isinstance(value,str) or not value.strip() or len(value)>256
            or any(ord(c)<32 for c in value)):
        raise ValueError('Invalid concern text/reference')
    return value


def shape(value, keys):
    if not isinstance(value,dict) or set(value)!=set(keys):
        raise ValueError('Invalid concern fields')


def finite(value, low, high):
    return type(value) in (int,float) and low<=value<=high and math.isfinite(value)


def distinct(values, maximum=40):
    if not isinstance(values,list) or len(values)>maximum:
        raise ValueError('Invalid concern reference list')
    for value in values: identifier(value)
    if len(set(values))!=len(values): raise ValueError('Duplicate concern reference')


def coordinates(test):
    kind=test['kind']
    if kind in {'vitality','zone'}: return [(kind,test['actor'])]
    if kind=='state': return [(kind,test['key'])]
    if kind=='inventory': return [(kind,test['actor'],test['item'])]
    if kind=='relation': return [(kind,test['observer'],test['target'])]
    return [(kind,actor,test['fact']) for actor in test['actors']]


def effect_coordinate(effect):
    kind=effect['kind']
    if kind in {'vitality','zone'}: return (kind,effect['actor'])
    if kind=='state': return (kind,effect['key'])
    if kind=='inventory': return (kind,effect['actor'],effect['item'])
    if kind=='relation': return (kind,effect['observer'],effect['target'])
    return (kind,effect['actor'],effect['fact'])


class Concerns:
    def __init__(self,raw):
        shape(raw,{'version','conditions','commitments'})
        if type(raw['version']) is not int or raw['version']!=1:
            raise ValueError('Unsupported concerns version')
        if not isinstance(raw['conditions'],list) or not 1<=len(raw['conditions'])<=32:
            raise ValueError('Bounded concern conditions required')
        self.config=deepcopy(raw);self.conditions={}
        for definition in self.config['conditions']:
            shape(definition,{'id','label','mode','visibility','test'})
            cid=identifier(definition['id']);identifier(definition['label'])
            if cid in self.conditions: raise ValueError('Duplicate concern id')
            if (not isinstance(definition['mode'],str) or not isinstance(definition['visibility'],str)
                    or definition['mode'] not in {'maintain','achieve'} or definition['visibility'] not in {'public','observed'}):
                raise ValueError('Invalid concern mode/visibility')
            test=definition['test']
            if not isinstance(test,dict) or not isinstance(test.get('kind'),str) or test['kind'] not in KINDS:
                raise ValueError('Unknown concern state adapter')
            shape(test,KINDS[test['kind']])
            for key,value in test.items():
                if key not in {'minimum','allowed','actors'}:identifier(value)
            if test['kind']=='vitality':
                distinct(test['allowed'])
                if not test['allowed'] or not set(test['allowed'])<=VITALITIES:raise ValueError('Invalid vitality condition')
            if test['kind']=='knowledge':
                distinct(test['actors'])
                if not test['actors'] or test['rule'] not in {'none','all'}:raise ValueError('Invalid knowledge condition')
            if test['kind']=='inventory' and (type(test['minimum']) is not int or not 0<=test['minimum']<=100000):
                raise ValueError('Invalid inventory minimum')
            if test['kind']=='relation' and not finite(test['minimum'],-1,1):raise ValueError('Invalid relation minimum')
            self.conditions[cid]=definition
        commitments=self.config['commitments']
        if not isinstance(commitments,dict) or not 1<=len(commitments)<=40:raise ValueError('Explicit commitments required')
        for actor,values in commitments.items():
            identifier(actor)
            if not isinstance(values,list) or not 1<=len(values)<=32:raise ValueError('Bounded actor commitments required')
            seen=set()
            for value in values:
                shape(value,{'condition','priority','required'})
                cid=value['condition']
                if not isinstance(cid,str) or cid not in self.conditions or cid in seen:raise ValueError('Unknown/duplicate commitment')
                seen.add(cid)
                if type(value['priority']) is not int or not 1<=value['priority']<=10 or type(value['required']) is not bool:
                    raise ValueError('Invalid concern priority/requirement')

    def bind(self,world,people):
        collisions=set(self.conditions)&(set(world.facts)|set(world.items)|set(world.zones)|set(people))
        if collisions:raise ValueError('Concern ids collide with existing references')
        if not set(self.config['commitments'])<=set(people):raise ValueError('Unknown concern owner')
        for definition in self.conditions.values():self._validate_refs(world,people,definition['test'])

    def _validate_refs(self,world,people,test):
        for key in ('actor','observer','target'):
            if key in test and test[key] not in people:raise ValueError('Unknown concern person')
        if test['kind']=='knowledge':
            if not set(test['actors'])<=set(people) or test['fact'] not in world.facts:raise ValueError('Unknown concern knowledge')
            if world.facts[test['fact']].get('values'):raise ValueError('Knowledge concern requires a boolean fact')
        if test['kind']=='inventory' and test['item'] not in world.items:raise ValueError('Unknown concern item')
        if test['kind']=='zone' and test['equals'] not in world.zones:raise ValueError('Unknown concern place')
        if test['kind']=='state' and (world.deliberation is None or test['key'] not in world.deliberation.state):
            raise ValueError('Unknown concern state key')

    def validate_projections(self,world,spec):
        projections=spec['concern_projections']
        if not isinstance(projections,dict) or set(projections)!={a['id'] for a in spec['alternatives']}:
            raise ValueError('Every alternative needs an explicit concern projection')
        for projection in projections.values():
            shape(projection,{'requires','effects'});distinct(projection['requires'],20)
            if not set(projection['requires'])<=set(world.facts):raise ValueError('Unknown projection prerequisite')
            if any(world.facts[f].get('values') for f in projection['requires']):raise ValueError('Projection prerequisite requires boolean knowledge')
            if not isinstance(projection['effects'],list) or len(projection['effects'])>40:raise ValueError('Bounded projection effects required')
            seen=set()
            for effect in projection['effects']:
                if not isinstance(effect,dict) or not isinstance(effect.get('kind'),str) or effect['kind'] not in KINDS:raise ValueError('Unknown projection effect')
                kind=effect['kind']
                keys={'kind','value'}|({'observer','target'} if kind=='relation' else {'key'} if kind=='state'
                      else {'actor','fact'} if kind=='knowledge' else {'actor','item'} if kind=='inventory' else {'actor'})
                shape(effect,keys)
                for key in keys-{'value'}:identifier(effect[key])
                value=effect['value']
                if kind=='vitality':
                    if not isinstance(value,str) or value not in VITALITIES:raise ValueError('Invalid projected vitality')
                    test={'kind':kind,'actor':effect['actor'],'allowed':[value]}
                elif kind=='knowledge':
                    if type(value) is not bool:raise ValueError('Invalid projected knowledge')
                    test={'kind':kind,'fact':effect['fact'],'actors':[effect['actor']],'rule':'all'}
                elif kind=='inventory':
                    test={'kind':kind,'actor':effect['actor'],'item':effect['item'],'minimum':value}
                    if type(value) is not int or not 0<=value<=100000:raise ValueError('Invalid projected inventory')
                elif kind=='relation':
                    test={'kind':kind,'observer':effect['observer'],'target':effect['target'],'minimum':value}
                    if not finite(value,-1,1):raise ValueError('Invalid projected relation')
                elif kind=='zone':test={'kind':kind,'actor':effect['actor'],'equals':identifier(value)}
                else:test={'kind':kind,'key':effect['key'],'equals':identifier(value)}
                self._validate_refs(world,world.subjects,test)
                coordinate=effect_coordinate(effect)
                if coordinate in seen:raise ValueError('Duplicate projected assignment')
                seen.add(coordinate)

    def _read(self,world,coordinate):
        kind,*args=coordinate
        if kind=='state':return world.deliberation.state[args[0]]
        if kind=='vitality':return world.subjects[args[0]].vitality
        if kind=='zone':return world.subjects[args[0]].zone
        if kind=='knowledge':return args[1] in world.subjects[args[0]].knowledge
        if kind=='inventory':return world.subjects[args[0]].inventory.get(args[1],0)
        return world.relations.stance(*args)

    def _visible(self,world,actor,coordinate,public):
        if public:return True
        kind,*args=coordinate
        if kind in {'vitality','zone'}:
            return args[0]==actor or world.subjects[args[0]].zone==world.subjects[actor].zone
        if kind in {'inventory','knowledge','relation'}:return args[0]==actor
        return False

    def evaluate(self,world,cid,*,actor=None,projection=None,justified=True):
        definition=self.conditions[cid];test=definition['test'];keys=coordinates(test)
        overlay={effect_coordinate(e):e['value'] for e in (projection or {}).get('effects',[])}
        values=[];sources=[]
        for key in keys:
            if key in overlay:
                values.append(overlay[key] if justified else None);sources.append('declared_forecast' if justified else 'unjustified_forecast')
            elif actor is None or self._visible(world,actor,key,definition['visibility']=='public'):
                values.append(self._read(world,key));sources.append('world' if actor is None else 'direct')
            else:values.append(None);sources.append('unobserved')
        if test['kind']=='knowledge':
            wanted=False if test['rule']=='none' else True
            raw=False if any(v is not None and v!=wanted for v in values) else None if any(v is None for v in values) else True
        elif values[0] is None:raw=None
        elif test['kind']=='vitality':raw=values[0] in test['allowed']
        elif test['kind'] in {'relation','inventory'}:raw=values[0]>=test['minimum']
        else:raw=values[0]==test['equals']
        confidence=1.0 if raw is not None else 0.0
        # A stale belief is a view of current state, not evidence for an unseen
        # hypothetical effect. Never fill an unjustified changed property with it.
        if raw is None and actor is not None and not any(k in overlay for k in keys):
            belief=world.subjects[actor].beliefs.get(cid)
            if belief is not None and belief.confidence>0:
                raw=belief.value=='true';confidence=belief.confidence;sources=['belief']
        state='unknown' if raw is None else 'true' if raw else 'false'
        progress=('unknown' if raw is None else ('maintained' if raw else 'breached')
                  if definition['mode']=='maintain' else ('achieved' if raw else 'pending'))
        return {'condition':cid,'label':definition['label'],'mode':definition['mode'],
                'state':state,'progress':progress,'confidence':confidence,'sources':sources}

    def assessment(self,world,actor,*,projection=None,justified=True):
        entries=[];tiers={};blocked=[];unknown=[]
        for commitment in self.config['commitments'].get(actor,[]):
            result=self.evaluate(world,commitment['condition'],actor=actor,projection=projection,justified=justified)
            result.update(priority=commitment['priority'],required=commitment['required']);entries.append(result)
            score=1 if result['state']=='true' else -1 if result['state']=='false' else 0
            tiers[result['priority']]=tiers.get(result['priority'],0)+score
            if result['required']:
                if result['state']=='false':blocked.append(result['condition'])
                if result['state']=='unknown':unknown.append(result['condition'])
        vector=[{'priority':p,'score':tiers[p]} for p in sorted(tiers,reverse=True)]
        leading=next((tier['score'] for tier in vector if tier['score']),0)
        stance='undecided' if unknown or (entries and all(e['state']=='unknown' for e in entries)) else 'oppose' if blocked or leading<0 else 'support'
        return {'entries':entries,'vector':vector,'blocked':blocked,'unknown_required':unknown,'stance':stance}

    def gain(self,before,after):
        left={t['priority']:t['score'] for t in before['vector']};right={t['priority']:t['score'] for t in after['vector']}
        for priority in sorted(set(left)|set(right),reverse=True):
            delta=right.get(priority,0)-left.get(priority,0)
            if delta:
                count=sum(e['priority']==priority for e in after['entries'])
                return {'priority':priority,'delta':delta,'normalized':delta/max(1,2*count)}
        return {'priority':None,'delta':0,'normalized':0.0}

    def world_report(self,world):
        return [self.evaluate(world,cid) for cid in sorted(self.conditions)]

    def observe_disclosure(self,world,speaker,recipient,fact):
        """Only participants know a successful transfer; no omniscient broadcast."""
        from engine.subject import Belief
        updates=[]
        projection={'effects':[{'kind':'knowledge','actor':recipient,'fact':fact,'value':True}]}
        for cid,definition in self.conditions.items():
            test=definition['test']
            if test['kind']!='knowledge' or test['fact']!=fact or recipient not in test['actors']:continue
            for actor in sorted({speaker,recipient}):
                result=self.evaluate(world,cid,actor=actor,projection=projection)
                if result['state']=='unknown':continue
                old=world.subjects[actor].beliefs.get(cid)
                if old and old.value==result['state'] and old.confidence==1:continue
                before=None if old is None else {'value':old.value,'confidence':old.confidence}
                world.subjects[actor].beliefs[cid]=Belief(result['state'],1.0)
                updates.append({'actor':actor,'condition':cid,'before':before,
                                'after':{'value':result['state'],'confidence':1.0},'source':'participated_in_disclosure'})
        return updates
