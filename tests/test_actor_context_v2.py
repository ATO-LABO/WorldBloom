"""Offline tests for P1-3 formatter repairs and the remaining candidate boundary."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from engine.actions import Action, _negotiate_candidates
from gapengine.knowledge_text import (
    ACTOR_CONTEXT, SUBJECTIVE_CONTEXT, LEGACY_CONTEXT, PowerKnowledge, TradeKnowledge,
    situation, render_situation, recipes_line, map_line, describe_candidate_coarse, context_key,
)
from gapengine.rationality import Rationality, RationalityTable
from test_knowledge_text import load_fixture
from test_subjective_objective import RecordingJudge


class ActorContextV2Tests(unittest.TestCase):
    def setUp(self):
        self.world, self.people = load_fixture()
        self.actor = self.people[self.world.protagonist]
        self.enemy = self.people[self.world.antagonist]
        self.present = self.world.present_subjects(self.actor.zone)
        self.world.items['交換品'] = {'lootable': True, 'modifier': {'value': 1}}
        self.actor.inventory['交換品'] = 1
        self.action = Action('negotiate', (self.enemy.id,), {'target': self.enemy.id})

    def packet(self, **kwargs):
        view = situation(self.actor, self.world, self.present, context_version=ACTOR_CONTEXT, **kwargs)
        return render_situation(view, self.world, recipe_lines=recipes_line(self.actor,self.world),
                                map_line=map_line(self.world))

    def offer(self, **kwargs):
        return describe_candidate_coarse(self.action,self.actor,self.world,[self.actor,self.enemy],
                                         describe_negotiate_offer=True,context_version=ACTOR_CONTEXT,**kwargs)

    def test_hidden_antagonist_vitality_cannot_change_packet(self):
        before = self.packet()
        self.enemy.vitality = 'dead'
        self.assertEqual(before,self.packet())
        self.assertIn('力関係: 分からない',before)

    def test_projected_roster_is_not_refiltered_by_unobserved_vitality(self):
        self.present=[self.actor,self.enemy]
        before=self.packet()
        self.enemy.vitality='dead'
        self.assertEqual(before,self.packet())

    def test_hidden_strength_and_inventory_do_not_change_power(self):
        before=self.packet()
        self.enemy.base=999
        self.enemy.inventory['交換品']=100
        self.enemy.modifiers.clear()
        with (patch('gapengine.knowledge_text.strength',side_effect=AssertionError('objective strength')),
              patch('gapengine.knowledge_text.believed_strength',side_effect=AssertionError('objective modifiers'))):
            self.assertEqual(before,self.packet())

    def test_explicit_actor_power_is_not_corrected_from_truth(self):
        self.enemy.vitality='dead'
        evidence=PowerKnowledge(self.actor.id,self.enemy.id,'互角',('heard:1',))
        self.assertIn('力関係: 互角',self.packet(power_knowledge=evidence))

    def test_power_evidence_identity_and_refs_are_validated(self):
        for actor_id,target_id in (('犬',self.enemy.id),(self.actor.id,'犬')):
            with self.assertRaises(ValueError):
                self.packet(power_knowledge=PowerKnowledge(actor_id,target_id,'劣る',('heard:1',)))
        with self.assertRaises(ValueError):
            PowerKnowledge(self.actor.id,self.enemy.id,'劣る',())

    def test_legacy_and_a1_power_behaviour_is_preserved(self):
        self.enemy.vitality='dead'
        for version in (LEGACY_CONTEXT,SUBJECTIVE_CONTEXT):
            self.assertEqual(situation(self.actor,self.world,self.present,context_version=version)['power'],'敵役なし')

    def test_unseen_peer_inventory_cannot_change_trade_text(self):
        before=self.offer()
        self.enemy.inventory['交換品']=1
        with patch.object(self.enemy,'has_item',side_effect=AssertionError('unobserved inventory')):
            self.assertEqual(before,self.offer())
        self.assertIn('交換品',before)
        self.assertIn('相手が応じるかは分からない',before)

    def test_known_peer_item_is_a_belief_not_truth_lookup(self):
        knowledge=TradeKnowledge(self.actor.id,self.enemy.id,('交換品',),('seen:2',))
        text=self.offer(trade_knowledge=knowledge)
        self.assertNotIn('交換品',text)
        self.enemy.inventory['交換品']=7
        self.assertEqual(text,self.offer(trade_knowledge=knowledge))

    def test_actor_own_offer_change_remains_visible(self):
        before=self.offer()
        self.actor.inventory['交換品']=0
        self.assertNotEqual(before,self.offer())

    def test_trade_evidence_cannot_be_for_another_actor_or_peer(self):
        for actor_id,target_id in (('犬',self.enemy.id),(self.actor.id,'犬')):
            with self.assertRaises(ValueError):
                self.offer(trade_knowledge=TradeKnowledge(actor_id,target_id,(),('seen:1',)))

    def test_v2_has_a_distinct_namespace_even_for_the_same_text(self):
        text='same packet'
        self.assertNotEqual(context_key(text,context_version=ACTOR_CONTEXT),
                            context_key(text,context_version=SUBJECTIVE_CONTEXT))

    def test_rationality_passes_v2_power_and_trade_contract(self):
        judge=RecordingJudge()
        rat=Rationality(kappa=.45,table=RationalityTable(),judge=judge,
                       context_version=ACTOR_CONTEXT,describe_negotiate_offer=True)
        rat.multipliers(self.actor,self.world,self.present,[self.action],
                        power_knowledge=PowerKnowledge(self.actor.id,self.enemy.id,'優る',('seen:1',)),
                        trade_knowledge={self.enemy.id:TradeKnowledge(self.actor.id,self.enemy.id,(),('seen:2',))})
        context,candidates=judge.calls[0]
        self.assertIn('力関係: 優る',context)
        self.assertIn('交換品',candidates[0])

    def test_legacy_candidate_generation_remains_an_explicit_limit(self):
        # This is a negative control: formatter repairs do not make engine
        # candidates actor-only. The caller must not assert that guarantee.
        own_before=deepcopy(self.actor.__dict__)
        target=self.actor.goal.target
        for peer in self.people.values():
            peer.inventory.pop(target,None)
        self.enemy.inventory[target]=1
        self.actor.verbs.add('negotiate')
        own_before=deepcopy(self.actor.__dict__)
        before=_negotiate_candidates(self.actor,self.world,[self.actor,self.enemy])
        self.enemy.inventory.pop(target)
        after=_negotiate_candidates(self.actor,self.world,[self.actor,self.enemy])
        self.assertEqual(own_before,self.actor.__dict__)
        self.assertNotEqual([(a.verb,a.args) for a,w in before],[(a.verb,a.args) for a,w in after])


if __name__=='__main__':
    unittest.main()
