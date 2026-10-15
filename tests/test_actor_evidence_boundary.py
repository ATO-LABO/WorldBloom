from dataclasses import FrozenInstanceError
from types import SimpleNamespace
import unittest
from engine.actor_decision_evidence import ActorDecisionEvidence
from gapengine.knowledge_text import ObjectiveKnowledge,PowerKnowledge,TradeKnowledge

class ActorEvidenceBoundaryTests(unittest.TestCase):
    def packet(self):return ActorDecisionEvidence('actor','run-a',3,('actor','observed-peer'),('heard-1',),
                objective=ObjectiveKnowledge('actor','goal','hostile',('heard-1',)),
                trades=(TradeKnowledge('actor','observed-peer',('believed-item',),('heard-1',)),))
    def test_packet_is_immutable_actor_bound_and_never_resolves_live_truth(self):
        p=self.packet();before=p.sha256;roster,kwargs=p.for_actor(SimpleNamespace(id='actor'),3)
        self.assertEqual([s.id for s in roster],['actor','observed-peer'])
        self.assertTrue(all(not hasattr(s,'inventory') and not hasattr(s,'vitality') for s in roster))
        self.assertEqual(kwargs['trade_knowledge']['observed-peer'].owned_items,('believed-item',))
        kwargs['trade_knowledge'].clear();self.assertEqual(p.sha256,before)
        with self.assertRaises(FrozenInstanceError):p.actor_id='other'
    def test_wrong_actor_future_or_unbound_reference_refuse(self):
        p=self.packet()
        for actor,tick in [('other',3),('actor',2)]:
            with self.assertRaises(ValueError):p.for_actor(SimpleNamespace(id=actor),tick)
        with self.assertRaises(ValueError):ActorDecisionEvidence('actor','run',0,(),('own',),objective=ObjectiveKnowledge('other','goal','none',('own',)))
        with self.assertRaises(ValueError):ActorDecisionEvidence('actor','run',0,(),('own',),objective=ObjectiveKnowledge('actor','goal','none',('unbound',)))
        with self.assertRaises(ValueError):ActorDecisionEvidence('actor','run',0,[],('own',))
