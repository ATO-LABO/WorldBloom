"""Action-selection draws, independent of frozen character traits and goals."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import random

ACTORS = tuple(sorted(("蒼", "紗季", "蓮", "灯")))
TICKS = 22
LIMIT = 2**32


@dataclass(frozen=True)
class ChoiceGenome:
    choices: tuple[int, ...]

    def __post_init__(self):
        if len(self.choices) != len(ACTORS)*TICKS or any(type(x) is not int or not 0 <= x < LIMIT for x in self.choices):
            raise ValueError("Choice genome requires 88 uint32 draws")

    def to_dict(self):
        return {"codec":"festival_choices_v1", "actors":list(ACTORS),
                "ticks":TICKS, "choices":list(self.choices)}

    @classmethod
    def from_dict(cls, raw):
        if (not isinstance(raw,dict) or set(raw)!={"codec","actors","ticks","choices"}
                or raw["codec"]!="festival_choices_v1" or raw["actors"]!=list(ACTORS) or raw["ticks"]!=TICKS):
            raise ValueError("Invalid choice codec; personality parameters forbidden")
        return cls(tuple(raw["choices"]))

    @classmethod
    def random(cls,rng,*,rule_ids=(),plastic=False):
        if rule_ids or plastic: raise ValueError("Choice search cannot evolve rules/personality")
        return cls(tuple(rng.randrange(LIMIT) for _ in range(len(ACTORS)*TICKS)))

    @classmethod
    def crossover(cls,first,second,rng,*,plastic=False):
        if plastic: raise ValueError("Personality growth forbidden")
        return cls(tuple(a if rng.random()<.5 else b for a,b in zip(first.choices,second.choices)))

    @classmethod
    def mutate(cls,genome,rng,*,plastic=False,p=.3):
        if plastic: raise ValueError("Personality growth forbidden")
        return cls(tuple(rng.randrange(LIMIT) if rng.random()<p else x for x in genome.choices))

    @property
    def sha256(self):
        return hashlib.sha256(json.dumps(self.to_dict(),sort_keys=True,separators=(',',':')).encode()).hexdigest()


class ChoiceRandom(random.Random):
    """Control only Simulation's categorical choice; retain fixed utility weights.

Genes are quantiles, not literal verbs. A gene maps to whichever legal actions
the actor's current knowledge/state produces. Seed shifts are reproducible.
"""
    def __init__(self,seed,genome):
        super().__init__(seed)
        self.choice_seed=seed; self.choice_genome=genome; self.active=None;self.draws=[]

    def choices(self,population,weights=None,*,cum_weights=None,k=1):
        if self.active is None:
            return super().choices(population,weights,cum_weights=cum_weights,k=k)
        if k!=1 or weights is None or cum_weights is not None:
            raise ValueError("Choice adapter supports single weighted decisions only")
        actor,tick=self.active;self.active=None
        gene=self.choice_genome.choices[ACTORS.index(actor)*TICKS+tick]
        salt=int.from_bytes(hashlib.sha256(f'{self.choice_seed}:{actor}:{tick}'.encode()).digest()[:8],'big')/2**64
        quantile=((gene+.5)/LIMIT+salt)%1
        threshold=quantile*sum(weights)
        cumulative=0
        for item,weight in zip(population,weights):
            cumulative+=weight
            if threshold<cumulative:
                self.draws.append({'actor':actor,'t':tick,'gene':gene,'seed_shift':salt,
                    'quantile':quantile,'chosen_index':item,'positive_candidates':len(population),
                    'weight_probability':weight/sum(weights)})
                return [item]
        raise ValueError('Invalid categorical weight total')


class ChoicePolicy:
    def __init__(self,policy,control):self.policy,self.control=policy,control
    def __getattr__(self,name):return getattr(self.policy,name)
    def reweight(self,subject,world,present,weighted,*,turn,day):
        self.control.active=(subject.id,turn-1)
        self.policy.selection_semantics="GA genetic quantile over fixed subjective weights; no literal action list"
        return self.policy.reweight(subject,world,present,weighted,turn=turn,day=day)
    def record(self,subject,action):self.policy.record(subject,action)


def wrap_policies(policies,control):
    if tuple(sorted(policies))!=ACTORS:raise ValueError("Frozen actor set mismatch")
    return {name:ChoicePolicy(policy,control) for name,policy in policies.items()}
