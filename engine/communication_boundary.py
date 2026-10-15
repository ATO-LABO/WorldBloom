"""Opt-in native message completion; no historical attribution or inference."""
from copy import deepcopy

def control(actor):
    return getattr(getattr(actor,'policy',None),'communication_repeat',None)

def selected(actor,action,tick):
    value=(action.meta.get('decision_basis') or {}).get('selected',{}).get('communication_repeat')
    if value is None:return None
    state=control(actor)
    if (state is None or value.get('actor_id')!=actor.id or value.get('decision_t')!=tick
            or value.get('epoch')!=state.config['epoch'] or state.pending.get(value.get('token'))!=value):
        raise ValueError('Communication selection is not owned by this actor/epoch/boundary')
    state.validate_decision(actor.id, value)
    if not value.get('allowed') or list(action.args) != [value[k] for k in ('to','topic','intent','channel')]:
        raise ValueError('Communication selection action mismatch')
    return deepcopy(value)

def request_reference(actor,meta,requests):
    if not meta:return None
    state=control(actor)
    state.validate_decision(actor.id,meta)
    payload=meta.get('communication_identity')
    if meta['intent']!='request':return None
    if payload is not None:
        original=payload['original_request_id']
        if original is None:return None
    elif meta['classification']=='automatic':return None
    state=control(actor);previous=state.sent.get(meta['key'],{})
    original=payload['original_request_id'] if payload is not None else previous.get('request_id')
    found=next((r for r in requests if r['id']==original and r['from']==actor.id
                and r['to']==meta['to'] and r['topic']==meta['topic']
                and r.get('epoch')==meta['epoch'] and r.get('version','unknown')==meta['request_version']),None)
    if found is None:raise ValueError('A request repeat needs its original actor/recipient/topic/epoch/version')
    return found['id']

def content(actor,meta):
    if not meta or meta['classification']!='update':return []
    records=getattr(actor,'slot_context',{}).get('communication_evidence',[])
    values=[]
    for ref in meta['evidence_refs']:
        matches=[e for e in records if e.get('evidence_id')==ref and e.get('actor_id')==actor.id
                 and e.get('issue_id')==meta['issue_id'] and e.get('epoch')==meta['epoch']
                 and ('communication_identity' not in meta or (isinstance(e.get('observed_t'),int)
                 and not isinstance(e.get('observed_t'),bool) and e['observed_t']<=meta['decision_t']))
                 and isinstance(e.get('message_content'),str) and e['message_content'].strip()]
        if not matches:raise ValueError('An update needs actual actor-owned evidence content')
        values.append({'evidence_id':ref,'message_content':matches[0]['message_content']})
    if not values:raise ValueError('An update needs new evidence content')
    return deepcopy(values)

def flush(actor,tick):
    state=control(actor)
    if state is None:return
    actor.policy.absorb(actor,tick)
    state.absorb(actor.id,getattr(actor,'slot_context',{}),tick)
