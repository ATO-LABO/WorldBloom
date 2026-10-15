"""Opt-in Melos nine-gene selection only; never executes or admits an action.

Projection/mapping are copied from the approved ninegene_melos.py contract.
No runtime/API registration, reference precedent, outcome learning, or new
classification is introduced. Callers remain responsible for observed evidence.
A selection is committed once even if downstream admission rejects the action.
"""
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import random
import sys
from types import SimpleNamespace

from engine.actions import Action
from engine.provider_boundary import (
    DecisionOffer, _json, encode_history, decode_history, encode_rng, decode_rng,
)
from gapengine.genome import CATEGORIES, Genome
from gapengine.policy import Policy

ACTORS = ('Groom', 'King', 'Melos', 'Philostratus', 'Selinuntius', 'Sister')
_CFG_JSON = b'{"edges":[],"nodes":[{"category":"V","risk":"neutral","sign":0,"subtype":"ritual","verb":"ritual"},{"category":"III","risk":"neutral","sign":1,"subtype":"confide","verb":"share_knowledge"},{"category":"III","risk":"neutral","sign":1,"subtype":"negotiate","verb":"negotiate"},{"category":"V","risk":"risky","sign":0,"subtype":"crossing","verb":"move","when":"crossing"},{"category":null,"risk":"neutral","sign":0,"subtype":"move","verb":"move"},{"category":"I","risk":"risky","sign":-1,"subtype":"weaken_direct","verb":"fight"},{"category":"V","risk":"neutral","sign":0,"subtype":"stall","verb":"withdraw"},{"category":null,"risk":"neutral","sign":0,"subtype":"rest","verb":"rest"}]}'

class ObservablePolicyWorld:
 """No engine World/ledger/future/other actor reference is retained.

 Only fixed public identity symbols, map zone symbols and neutral initial
 relations are supplied. No actor locations, vitality, hidden hostility,
 destination occupants or objective ownership are exposed. This verification
 holds relations fixed; it does not model learned relation changes.
 """
 def __init__(self,view,subject,targets):
  self.genres=frozenset();self.protagonist='Melos';self.antagonist='King'
  self.zones={z:None for z in view['public_map']}
  self.subjects={subject.id:subject,**{t:SimpleNamespace(id=t) for t in targets}}
  self.relations=SimpleNamespace(stance=lambda a,b:0.0)
  self.companionship={'threshold':.3}
 def target_role(self,subject,target):return 'neutral'
 def present_subjects(self,destination):return []
 def holder(self,target):raise AssertionError('No object-target goal in this projection')
 def namespace(self,subject,present,**kw):return {} # no rules/route/Jev

def observed(view,verb):return any(e['verb']==verb and e['at']<=view['now'] for e in view['evidence'])

def policy_action(c,view):
 ident=c['id'];meta={'candidate_id':ident}
 if ident=='wedding':return Action('ritual',('wedding',),meta=meta)
 if ident in ['confess','disclose']:
  return Action('share_knowledge',('Selinuntius' if ident=='confess' else 'Sister',),meta=meta)
 if ident.startswith('negotiate_'):return Action('negotiate',('Groom',),meta=meta)
 if ident=='swim':
  assert observed(view,'river_observed') and view['initial'].get('river_seen',{}).get('water')=='flood'
  return Action('move',('west_bank',),meta={**meta,'crossing':True})
 if ident=='fight':
  assert observed(view,'bandit_demand')
  return Action('fight',('Bandits',),meta={**meta,'under_threat':True})
 if ident in ['retreat','retreat_bandits']:
  threat=ident=='retreat_bandits'
  if threat:assert observed(view,'bandit_demand')
  return Action('withdraw',(),meta={**meta,'under_threat':threat})
 if ident in ['rest_arrival','sleep_dawn','drink']:
  return Action('rest',(),meta={**meta,'under_threat':False})
 if c['kind']=='move':return Action('move',(c['to'],),meta={**meta,'crossing':False})
 return Action(ident,(),meta=meta) # deliberate unclassified candidates

def reweight_observable(policy,view,offered,history):
 """The sole Policy boundary: arguments are only ActorView/candidates/self history."""
 person=SimpleNamespace(id=view['actor'],goal=SimpleNamespace(target=None),phase=frozenset(),
  vitality=view['own_vitality'],identity_displayed=view['actor'],decision_history=history)
 actions=[policy_action(c,view) for c in offered]
 targets={a.args[0] for a in actions if a.verb in ['negotiate','share_knowledge']}
 world=ObservablePolicyWorld(view,person,targets)
 weighted=[];utility=[]
 for c,a in zip(offered,actions):
  u=sum(view['goals'].get(k,0)*v for k,v in c['features'].items())
  w=math.exp(max(-6,min(6,u/5)));weighted.append((a,w));utility.append(u)
 result=policy.reweight(person,world,[person],weighted,turn=view['decision_ordinal'],day=view['now']//1440+1)
 rows=[]
 for c,u,(a,base),(b,final) in zip(offered,utility,weighted,result):
  assert a is b
  rows.append({'candidate':c['id'],'policy_verb':a.verb,'policy_args':list(a.args),
   'goal_features':c['features'],'base_utility':u,'base_weight':base,
   'classification':a.meta['classification'],'multipliers':a.meta['policy'],
   'final_weight':final,'normalized_weight':final/sum(w for _,w in result)})
 return person,result,rows

@dataclass(frozen=True)
class _Configuration:
    genome: bytes
    seed: int
    cfg: bytes = _CFG_JSON
    actors: tuple = ACTORS
    focus_actor: str = 'Melos'
    rng_owner: str = 'provider'


class MelosChoiceProvider:
    """Bounded, deterministic selection receipts with one shared owned RNG.

    choose() requires the caller's actor/time/ordinal binding. Identical retries
    return the original receipt; changed offers at an already used ordinal fail.
    Configuration is immutable canonical bytes, and every exposed record is a
    detached JSON value. Restore validates the complete <=192-choice transcript
    by replay before replacing any live state.
    """

    def __init__(self, genome, seed=0):
        if type(genome) is not Genome or type(seed) is not int:
            raise ValueError('Exact Genome and integer seed required')
        if (type(genome.category_weight) is not dict
                or set(genome.category_weight) != set(CATEGORIES)
                or type(genome.rule_bits) is not dict or genome.rule_bits
                or type(genome.plasticity) not in (int, float) or genome.plasticity != 0):
            raise ValueError('Only the approved nine scalar genome is supported')
        values = [(v, .05, 1) for v in genome.category_weight.values()]
        values += [(genome.risk_tolerance, 0, 1),
                   (genome.stance_shift_bias, -1, 1), (genome.novelty_drive, 0, 1)]
        if any(type(v) not in (int, float) or not math.isfinite(v) or not lo <= v <= hi
               for v, lo, hi in values):
            raise ValueError('Finite in-range nine-gene scalars required')
        self._config = _Configuration(_json(genome.to_dict()), seed)
        self._rng = random.Random(seed)
        self._history = Counter()
        self._counts = dict.fromkeys(ACTORS, 0)
        self._last_minute = None
        self._records = []
        self._receipts = {}

    @property
    def config(self):
        return self._config

    def _policy(self):
        # Fresh Policy prevents uncheckpointed mutable Policy configuration.
        return Policy(Genome.from_dict(json.loads(self._config.genome)), None,
                      cfg=json.loads(self._config.cfg), eps=.02, lam=.7)

    def checkpoint_sources(self):
        # Conservative source closure: includes every local engine/gapengine
        # module (including transitive imports) rather than omitting a helper.
        root = Path(__file__).resolve().parents[1]
        return {str(p.relative_to(root)): str(p)
                for package in ('engine', 'gapengine')
                for p in sorted((root / package).rglob('*.py'))}

    def checkpoint_identity(self):
        closure = {name: hashlib.sha256(Path(path).read_bytes()).hexdigest()
                   for name, path in self.checkpoint_sources().items()}
        config = {'genome': json.loads(self._config.genome),
                  'cfg': json.loads(self._config.cfg), 'seed': self._config.seed,
                  'actors': list(self._config.actors), 'focus_actor': self._config.focus_actor,
                  'rng_owner': self._config.rng_owner, 'eps': .02, 'lam': .7,
                  'rule_bits': {}, 'plasticity': 0, 'reference': None, 'limit': 32,
                  'python': list(sys.version_info[:3])}
        return {'adapter': 'melos_ninegene_choices_v1', 'schema_version': 1,
                'source_hash': hashlib.sha256(_json(closure)).hexdigest(),
                'config_hash': hashlib.sha256(_json(config)).hexdigest(),
                'table_hash': None, 'rng_owner': 'provider'}

    def choose(self, offer, *, actor, now, ordinal):
        if type(offer) is not DecisionOffer:
            raise ValueError('An immutable DecisionOffer is required')
        envelope = offer.read()
        view, offered = envelope['view'], envelope['offered']
        # Validate caller binding even on an idempotent retry.
        offer.selected(offered[0]['id'], offer_hash=offer.identity,
                       actor=actor, now=now, ordinal=ordinal)
        if actor not in self._counts:
            raise ValueError('Actor outside the approved Melos contract')
        if offer.identity in self._receipts:
            return deepcopy(self._receipts[offer.identity])
        if ordinal != self._counts[actor] or ordinal >= 32:
            raise ValueError('Stale, skipped, or exhausted actor ordinal')
        if self._last_minute is not None and now < self._last_minute:
            raise ValueError('Decision minute moved backwards')

        history = self._history.copy()
        rng = decode_rng(encode_rng(self._rng))
        policy = self._policy()
        if actor == self._config.focus_actor:
            # Keep the exact approved mapping below, but make its evidence
            # preconditions mandatory even when Python runs with -O.
            try:
                for candidate in offered:
                    if candidate['id'] == 'swim' and not (
                            observed(view, 'river_observed')
                            and view['initial'].get('river_seen', {}).get('water') == 'flood'):
                        raise ValueError('Swim requires observed flood evidence')
                    if candidate['id'] in ('fight', 'retreat_bandits') and not observed(view, 'bandit_demand'):
                        raise ValueError('Bandit action requires observed demand')
                person, weighted, calculations = reweight_observable(policy, view, offered, history)
            except (AssertionError, KeyError, TypeError, AttributeError, IndexError, OverflowError) as exc:
                raise ValueError('Invalid approved Melos projection') from exc
        else:
            weighted, calculations = [], []
            for c in offered:
                u = sum(view['goals'].get(k, 0) * v for k, v in c['features'].items())
                w = math.exp(max(-6, min(6, u / 5)))
                weighted.append((None, w))
                calculations.append({'candidate': c['id'], 'base_utility': u,
                                     'base_weight': w, 'final_weight': w,
                                     'multipliers': {'m_cat': 1, 'm_risk': 1, 'm_stance': 1, 'm_nov': 1},
                                     'normalized_weight': None})
            total = sum(w for _, w in weighted)
            for c in calculations:
                c['normalized_weight'] = c['final_weight'] / total
        probe = decode_rng(encode_rng(rng))
        draw = probe.random()
        index = rng.choices(range(len(offered)), weights=[w for _, w in weighted], k=1)[0]
        selected = offer.selected(offered[index]['id'], offer_hash=offer.identity,
                                  actor=actor, now=now, ordinal=ordinal)
        if actor == self._config.focus_actor:
            policy.record(person, weighted[index][0])
        receipt = {'offer_hash': offer.identity, 'actor': actor, 'now': now,
                   'ordinal': ordinal, 'selected': selected['id'], 'candidate': selected,
                   'rng_draw': draw, 'calculation': calculations}
        # Reject nonfinite derived values before touching any owned state.
        _json(receipt)
        encode_history(history)
        self._rng, self._history = rng, history
        self._counts[actor] += 1
        self._last_minute = now
        self._records.append({'offer': envelope, 'receipt': receipt})
        self._receipts[offer.identity] = receipt
        return deepcopy(receipt)

    def export_state(self):
        return deepcopy({'schema': 'melos_ninegene_state_v1',
                         'identity': self.checkpoint_identity(),
                         'counts': self._counts, 'last_minute': self._last_minute,
                         'history': encode_history(self._history), 'rng': encode_rng(self._rng),
                         'records': self._records})

    def restore_state(self, state):
        # Exact JSON prevents bool/int coercion, tuples, NaN and hidden aliases.
        encoded = _json(state)
        if (type(state) is not dict
                or set(state) != {'schema', 'identity', 'counts', 'last_minute', 'history', 'rng', 'records'}
                or state['schema'] != 'melos_ninegene_state_v1'
                or _json(state['identity']) != _json(self.checkpoint_identity())
                or type(state['records']) is not list or len(state['records']) > 192):
            raise ValueError('Invalid provider state or changed source/configuration')
        decode_history(state['history'])
        decode_rng(state['rng'])
        staged = MelosChoiceProvider(Genome.from_dict(json.loads(self._config.genome)), self._config.seed)
        for record in state['records']:
            if type(record) is not dict or set(record) != {'offer', 'receipt'}:
                raise ValueError('Invalid selection record')
            offer = DecisionOffer(_json(record['offer']))
            view = offer.read()['view']
            receipt = staged.choose(offer, actor=view['actor'], now=view['now'],
                                    ordinal=view['decision_ordinal'])
            if _json(receipt) != _json(record['receipt']):
                raise ValueError('Selection receipt does not match deterministic replay')
        if _json(staged.export_state()) != encoded:
            raise ValueError('Counts, time, history, RNG or transcript differ from replay')
        self._rng, self._history = staged._rng, staged._history
        self._counts, self._last_minute = staged._counts, staged._last_minute
        self._records, self._receipts = staged._records, staged._receipts
