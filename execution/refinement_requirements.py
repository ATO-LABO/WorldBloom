"""Bounded model requirement ledger and deterministic no-omission checks."""
from execution.refinement_contract import OPERATIONS, fail

def schema():
    text={'type':'string'}
    variants=[]
    for kind in OPERATIONS:
        keys=['from','to'] if kind=='route.add' else (['name'] if kind in ('place.add','item.add') else [])
        values={'type':'object','additionalProperties':False,'required':keys,'properties':{k:text for k in keys}}
        variants.append({'type':'object','additionalProperties':False,'required':['id','kind','target','instruction','required_values'],
                        'properties':{'id':text,'kind':{'const':kind},'target':({'const':''} if kind.endswith('.add') else text),'instruction':text,'required_values':values}})
    row={'anyOf':variants}
    return {'type':'object','additionalProperties':False,'required':['requirements','unsupported','clarification'],
            'properties':{'requirements':{'type':'array','items':row},'unsupported':{'type':'array','items':text},'clarification':{'type':'array','items':text}}}

def validate_requirements(raw):
    if not isinstance(raw,dict) or set(raw)!={'requirements','unsupported','clarification'}:
        fail('requirements','Invalid decomposition envelope')
    for key in ('unsupported','clarification'):
        if not isinstance(raw[key],list) or any(not isinstance(v,str) for v in raw[key]): fail(key,'Expected text list')
        if raw[key]: fail(key,'; '.join(raw[key]), 'unsupported' if key=='unsupported' else 'needs_clarification')
    rows=raw['requirements']
    if not isinstance(rows,list) or not 1<=len(rows)<=16: fail('requirements','Expected 1-16 requirements')
    seen=set()
    for row in rows:
        if not isinstance(row,dict) or set(row)!={'id','kind','target','instruction','required_values'}: fail('requirements','Invalid fields')
        for key in ('id','kind','target','instruction'):
            if not isinstance(row[key],str) or len(row[key])>(32015 if key=='instruction' else 2000): fail('requirements','Invalid text')
        if not row['id'] or row['id'] in seen or not row['instruction']: fail('requirements','Duplicate or empty identifier/instruction')
        seen.add(row['id'])
        if row['kind'] not in OPERATIONS: fail('requirements','Unsupported kind','unsupported')
        if row['kind'].endswith('.add') and row['target']: fail('requirements','Add target must be empty')
        values=row['required_values']
        allowed={'from','to'} if row['kind']=='route.add' else ({'name'} if row['kind'] in ('place.add','item.add') else set())
        if not isinstance(values,dict) or set(values)!=allowed or any(not isinstance(v,str) or not v.strip() for v in values.values()):
            fail('requirements','Required entity identifiers missing or invalid')
    # Updates are one atomic write per semantic target, even if the model
    # decomposed days/slots or actors/slots into separate requirements.
    from copy import deepcopy
    result=deepcopy(raw); merged=[]; indexes={}
    for row in result['requirements']:
        key=(row['kind'],row['target'])
        if not row['kind'].endswith('.add'):
            if key in indexes:
                merged[indexes[key]]['instruction'] += '\n' + row['instruction']
                if len(merged[indexes[key]]['instruction'])>32015: fail('requirements','Combined instructions exceed bounded limit')
                continue
            indexes[key]=len(merged)
        merged.append(row)
    result['requirements']=merged
    return result

def check_coverage(requirements, plan):
    requirements=validate_requirements(requirements)
    if not isinstance(plan,dict) or not isinstance(plan.get('operations'),list): fail('coverage','Plan operations missing')
    ops=plan['operations']
    rows=requirements['requirements']
    if len(ops)!=len(rows): fail('coverage','Every requirement needs exactly one operation; no silent omission or extra operation')
    # Bipartite matching handles two operations of the same kind without greedy ambiguity.
    matches={}
    def assign(i,seen):
        row=rows[i]
        for j,op in enumerate(ops):
            if j in seen or not isinstance(op,dict): continue
            vals=op.get('values',{})
            if op.get('kind')!=row['kind'] or op.get('target')!=row['target'] or not isinstance(vals,dict): continue
            if any(vals.get(k)!=v for k,v in row['required_values'].items()): continue
            seen.add(j)
            if j not in matches or assign(matches[j],seen): matches[j]=i; return True
        return False
    if not all(assign(i,set()) for i in range(len(rows))): fail('coverage','Required target or relation omitted/changed')


def request_spans(prompt):
    """Mechanical segmentation only: preserve every input character and its order.

    No interpretation of intent is performed here. Each span has a required
    response slot, preventing the model from responding only to the first sentence.
    """
    import re
    spans=[part for part in re.split(r'(?<=[。！？])|(?<=[.!?])(?=\s|$)|(?<=\n)',prompt) if part]
    # Bound schema growth without dropping tail text; batch excess into final span.
    if len(spans)>12: spans=spans[:11]+[''.join(spans[11:])]
    if not spans: spans=[prompt]
    return spans

def span_schema(spans):
    row=schema()
    text={'type':'string'}
    row['required']+=['disposition','reason']
    row['properties'].update(disposition={'enum':['change','preserve','unsupported','clarification']},reason=text)
    return {'type':'object','additionalProperties':False,'required':['spans'],
            'properties':{'spans':{'type':'object','additionalProperties':False,
              'required':['s'+str(i) for i in range(len(spans))],
              'properties':{'s'+str(i):row for i in range(len(spans))}}}}

def combine_spans(raw,spans):
    if not isinstance(raw,dict) or set(raw)!={'spans'} or not isinstance(raw['spans'],dict) or set(raw['spans'])!={'s'+str(i) for i in range(len(spans))}:
        fail('requirements','Every source span must be addressed')
    combined={'requirements':[],'unsupported':[],'clarification':[]}
    for i,text in enumerate(spans):
        row=raw['spans']['s'+str(i)]
        if not isinstance(row,dict) or set(row)!={'requirements','unsupported','clarification','disposition','reason'}:
            fail('requirements','Invalid span result')
        if not isinstance(row['reason'],str) or not row['reason'].strip() or len(row['reason'])>2000:
            fail('requirements','Span disposition needs an explanation')
        for key in combined:
            if not isinstance(row[key],list):fail('requirements','Invalid span list')
        mode=row['disposition']
        if mode=='change':
            if not row['requirements'] or row['unsupported'] or row['clarification']:fail('requirements','Change span needs operations only')
        elif mode=='preserve':
            if any(row[k] for k in combined):fail('requirements','Preserve span cannot change data')
        elif mode=='unsupported':
            if not row['unsupported']:fail('requirements','Unsupported span needs a reason')
        elif mode=='clarification':
            if not row['clarification']:fail('requirements','Clarification span needs a question')
        else:fail('requirements','Unknown disposition')
        for j,req in enumerate(row['requirements']):
            if not isinstance(req,dict):fail('requirements','Invalid requirement')
            req=dict(req);req['id']='s'+str(i)+'r'+str(j)
            combined['requirements'].append(req)
        for key in ('unsupported','clarification'):combined[key].extend(row[key])
    return validate_requirements(combined)
