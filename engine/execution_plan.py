"""Execution selection is outside the shared world clock and outside UI mode."""
from copy import deepcopy
import hashlib,json,re


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')


def digest(value):return hashlib.sha256(canonical(value)).hexdigest()


def _hash(value,name):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}',value) is None:
        raise ValueError('Invalid '+name)


def _identity(value,name):
    if not isinstance(value,dict) or set(value)!={'id','version','source_hash','config_hash'}:
        raise ValueError('Incomplete '+name+' identity')
    if type(value['id']) is not str or not value['id'] or type(value['version']) is not int or value['version']<1:
        raise ValueError('Invalid '+name+' adapter/version')
    _hash(value['source_hash'],name+' source');_hash(value['config_hash'],name+' config')


class ExecutionPlan:
    """Frozen single-run controller contract, independent of ResultProjection.

    The sampler/provider implementation and classification are identified, not
    guessed from a UI label. The independent controller never filters outcomes.
    """
    def __init__(self,value):
        required={'schema_version','execution_kind','sampler','provider','predeclared_seeds',
                  'planned_trials','selection','observation','classification'}
        if not isinstance(value,dict) or set(value)!=required or type(value['schema_version']) is not int or value['schema_version']!=1:
            raise ValueError('Unsupported ExecutionPlan')
        if value['execution_kind'] not in {'production_search','independent_trials','explicit_witness','replay'}:
            raise ValueError('Unknown execution kind')
        for name in ('sampler','provider','classification'):_identity(value[name],name)
        seeds=value['predeclared_seeds']
        if not isinstance(seeds,list) or not seeds or len(seeds)>10000 or any(type(s) is not int or s<0 for s in seeds):
            raise ValueError('Predeclared nonnegative seeds required')
        trials=value['planned_trials']
        if type(trials) is not int or not 1<=trials<=100000000:
            raise ValueError('Invalid declared trial count')
        selection=value['selection']
        flags={'fitness_selection','breeding','keep_filter','canon_feedback'}
        if not isinstance(selection,dict) or set(selection)!=flags or any(type(x) is not bool for x in selection.values()):
            raise ValueError('Explicit selection flags required')
        if value['execution_kind']=='independent_trials':
            if trials!=len(seeds) or len(set(seeds))!=len(seeds) or any(selection.values()):
                raise ValueError('Independent trials cannot select, breed, filter, or use canon feedback')
        observation=value['observation']
        if (not isinstance(observation,dict) or set(observation)!={'unit','start','end','stop_contract_hash'}
                or observation['unit'] not in {'minute','logical_step'}
                or any(type(observation[k]) is not int for k in ('start','end'))
                or not 0<=observation['start']<=observation['end']):
            raise ValueError('Explicit observation/stop contract required')
        _hash(observation['stop_contract_hash'],'stop contract')
        canonical(value)
        self._value=deepcopy(value)

    def to_dict(self):return deepcopy(self._value)

    @property
    def sha256(self):return digest(self._value)

    def independent(self,evaluate,save):
        """Keep every predeclared slot, including failure and unresolved results.

        Exceptions from evaluation are evidence; persistence exceptions propagate
        so an unsaved slot is never silently reported complete or replaced.
        """
        if self._value['execution_kind']!='independent_trials':
            raise ValueError('This controller requires independent_trials')
        records=[]
        for ordinal,seed in enumerate(self._value['predeclared_seeds']):
            try:
                outcome=evaluate(seed)
                if not isinstance(outcome,dict) or type(outcome.get('status')) is not str:
                    raise ValueError('Classified outcome required')
                canonical(outcome)
            except Exception as error:
                outcome={'status':'execution_failure','error_type':type(error).__name__,'error':str(error)}
            record={'ordinal':ordinal,'seed':seed,'execution_plan_hash':self.sha256,'outcome':deepcopy(outcome)}
            save(record)
            records.append(record)
        return records


def run_spec_hashes(semantic,presentation):
    """The compiler supplies explicit meaning and presentation closures.

    Never strip arbitrary fields or display-like labels heuristically. Full run
    identity changes on either closure; semantic comparisons use meaning only.
    """
    return {'run_spec_hash':digest({'semantic':semantic,'presentation':presentation}),
            'semantic_hash':digest(semantic),'presentation_hash':digest(presentation)}
