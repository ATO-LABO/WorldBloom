"""Caller-owned immutable evidence for Jev rendering; no truth projection.

This boundary does not certify legacy action existence or base weighting. Its
scope is recorded explicitly rather than replacing the ordinary nine genes.
"""
from dataclasses import dataclass,asdict
from types import SimpleNamespace
from engine.decision_provider import digest
from gapengine.knowledge_text import ObjectiveKnowledge,PowerKnowledge,TradeKnowledge

@dataclass(frozen=True)
class ActorDecisionEvidence:
    actor_id:str
    epoch:str
    observed_t:int
    observed_roster:tuple[str,...]
    evidence_refs:tuple[str,...]
    objective:ObjectiveKnowledge|None=None
    power:PowerKnowledge|None=None
    trades:tuple[TradeKnowledge,...]=()

    def __post_init__(self):
        if (not isinstance(self.actor_id,str) or not self.actor_id or not isinstance(self.epoch,str) or not self.epoch
                or type(self.observed_t) is not int or self.observed_t<0):raise ValueError('Actor evidence requires actor, epoch and observation position')
        for value in (self.observed_roster,self.evidence_refs,self.trades):
            if type(value) is not tuple:raise ValueError('Actor evidence must be immutable')
        if not self.evidence_refs or any(type(v) is not str or not v for v in self.evidence_refs+self.observed_roster):raise ValueError('Actor evidence references/roster are invalid')
        if len(set(self.observed_roster))!=len(self.observed_roster):raise ValueError('Duplicate observed actor')
        if self.objective is not None and not isinstance(self.objective,ObjectiveKnowledge):raise ValueError('Invalid objective evidence type')
        if self.power is not None and not isinstance(self.power,PowerKnowledge):raise ValueError('Invalid power evidence type')
        if any(not isinstance(v,TradeKnowledge) or type(v.owned_items) is not tuple for v in self.trades):raise ValueError('Invalid immutable trade evidence')
        if len({v.target_id for v in self.trades})!=len(self.trades):raise ValueError('Duplicate trade target')
        for value in ([self.objective] if self.objective else [])+([self.power] if self.power else [])+list(self.trades):
            if type(value.evidence_refs) is not tuple:raise ValueError('Evidence references must be immutable')
            if value.actor_id!=self.actor_id:raise ValueError('Evidence belongs to another actor')
            if not set(value.evidence_refs)<=set(self.evidence_refs):raise ValueError('Evidence reference is outside this packet')

    @property
    def sha256(self):return digest(asdict(self))

    def for_actor(self,actor,tick):
        if actor.id!=self.actor_id or self.observed_t>tick:raise ValueError('Wrong actor or future evidence')
        # v2 needs observed identities only. Do not resolve them to live peers,
        # or filter an observed belief by objective vitality/inventory.
        roster=[SimpleNamespace(id=n) for n in self.observed_roster]
        return roster,{'objective_knowledge':self.objective,'power_knowledge':self.power,
                       'trade_knowledge':{v.target_id:v for v in self.trades}}
