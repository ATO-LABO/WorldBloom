"""Explicit provider snapshots; no automatic reconstruction of hidden state."""
from copy import deepcopy
import hashlib,json,re,inspect

def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(v):return hashlib.sha256(canonical(v)).hexdigest()

def provider_snapshot(provider):
    identity=provider.checkpoint_identity()
    if not isinstance(identity,dict) or set(identity)!={'adapter','schema_version','source_hash','config_hash','table_hash','rng_owner'}:
        raise ValueError('Explicit provider version and source/config/table identities required')
    if type(identity['adapter']) is not str or not identity['adapter'] or type(identity['schema_version']) is not int or identity['schema_version']<1:
        raise ValueError('Invalid provider identity')
    for field in ('source_hash','config_hash'):
        if type(identity[field]) is not str or re.fullmatch('[0-9a-f]{64}',identity[field]) is None:raise ValueError('Invalid provider '+field)
    if identity['table_hash'] is not None and (type(identity['table_hash']) is not str or re.fullmatch('[0-9a-f]{64}',identity['table_hash']) is None):
        raise ValueError('Invalid provider table hash')
    if identity['rng_owner'] not in {'simulation','provider','none'}:raise ValueError('Explicit RNG owner required')
    # Include concrete adapters and base classes, not only a declared adapter ID.
    # Extensions may explicitly supply formatter/dependency paths as well.
    from pathlib import Path
    closure={}
    for cls in type(provider).__mro__:
        if cls is object:continue
        path=inspect.getsourcefile(cls)
        if path is None:raise ValueError('Provider implementation source is unavailable')
        closure[cls.__module__+'.'+cls.__qualname__]=hashlib.sha256(Path(path).read_bytes()).hexdigest()
    for key,path in getattr(provider,'checkpoint_sources',lambda:{})().items():
        if type(key) is not str or not key:raise ValueError('Invalid provider dependency identity')
        closure['dependency:'+key]=hashlib.sha256(Path(path).read_bytes()).hexdigest()
    identity=deepcopy(identity)
    identity['runtime_source_hash']=digest(closure)
    state=provider.export_state()
    if not isinstance(state,dict):raise ValueError('Provider state must be a mapping')
    if state != json.loads(canonical(state)):
        raise ValueError('Provider state must preserve exact JSON types and keys')
    return {'identity':deepcopy(identity),'state':deepcopy(state),'state_hash':digest(state)}

def staged_provider_restore(provider,snapshot):
    if not isinstance(snapshot,dict) or set(snapshot)!={'identity','state','state_hash'}:
        raise ValueError('Invalid provider snapshot')
    if snapshot['identity']!=provider_snapshot(provider)['identity'] or snapshot['state_hash']!=digest(snapshot['state']):
        raise ValueError('Changed provider implementation/config/table or corrupt state')
    staged=deepcopy(provider)
    staged.restore_state(deepcopy(snapshot['state']))
    if provider_snapshot(staged)!=snapshot:raise ValueError('Provider restore is not exact')
    return staged

class ExplicitChoiceProvider:
    """Optional stateful facade over frozen explicit command choices.

    The runtime still enforces legality and timing. This provider does not turn
    explicit witnesses into GA or autonomous action search.
    """
    def __init__(self,commands,*,table_hash=None):
        self.commands=deepcopy(sorted(commands,key=lambda c:(c['at'],c.get('priority',0),c['id'])))
        self.table_hash=table_hash
        self.consumed=[]
        ids=[c['id'] for c in commands]
        if len(ids)!=len(set(ids)):raise ValueError('Duplicate provider command')
        canonical(commands)

    def checkpoint_identity(self):
        from pathlib import Path
        return {'adapter':'explicit_choices_v1','schema_version':1,
                'source_hash':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'config_hash':digest(self.commands),'table_hash':self.table_hash,'rng_owner':'simulation'}

    def record_choice(self,command):
        if command not in self.commands:raise ValueError('Choice outside provider configuration')
        if command['id'] in self.consumed:raise ValueError('Duplicate provider effect')
        self.consumed.append(command['id'])

    def export_state(self):return {'consumed':list(self.consumed),'epoch_position':len(self.consumed)}

    def restore_state(self,state):
        if not isinstance(state,dict) or set(state)!={'consumed','epoch_position'}:
            raise ValueError('Unsupported provider state')
        ids=state['consumed'];known={c['id'] for c in self.commands}
        if (not isinstance(ids,list) or any(type(x) is not str or x not in known for x in ids)
                or len(set(ids))!=len(ids) or type(state['epoch_position']) is not int or state['epoch_position']!=len(ids)):
            raise ValueError('Invalid provider cursor/history')
        self.consumed=list(ids)
