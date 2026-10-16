"""Offline noninterference and versioned replay tests for the a1 boundary."""
from __future__ import annotations

import hashlib
import unittest
from unittest.mock import patch

from engine.actions import Action
from gapengine.knowledge_text import (
    LEGACY_CONTEXT, SUBJECTIVE_CONTEXT, ObjectiveKnowledge, _objective_state,
    context_key, map_line, recipes_line, render_situation, situation,
)
from gapengine.rationality import Rationality, RationalityTable, TableOnlyJudge, RationalityTableMissError
from test_knowledge_text import load_fixture


class RecordingJudge:
    backend_name = "offline-test"
    model = "fixture"

    def __init__(self):
        self.calls = []

    def score(self, context, candidates, *, max_calls=None):
        self.calls.append((context, tuple(candidates)))
        return [0.5] * len(candidates), len(candidates), False


class SubjectiveObjectiveTests(unittest.TestCase):
    def setUp(self):
        self.world, self.people = load_fixture()
        self.actor = self.people[self.world.protagonist]
        self.target = self.actor.goal.target
        self.actions = [Action("rest"), Action("move", ("森",))]
        self.present = self.world.present_subjects(self.actor.zone)

    def packet(self, **kwargs):
        sit = situation(self.actor, self.world, self.present,
                        context_version=SUBJECTIVE_CONTEXT, **kwargs)
        return render_situation(sit, self.world, recipe_lines=recipes_line(self.actor, self.world),
                                map_line=map_line(self.world))

    def rationality(self, judge, table=None, **kwargs):
        return Rationality(kappa=0.45, judge=judge,
                          table=table if table is not None else RationalityTable(),
                          context_version=SUBJECTIVE_CONTEXT, **kwargs)

    def test_hidden_holder_changes_neither_packet_nor_cache_key(self):
        packets = []
        for holder in (None, self.world.antagonist, "犬", "村"):
            with patch.object(self.world, "holder", return_value=holder) as lookup:
                packets.append(self.packet())
                lookup.assert_not_called()
        self.assertEqual(len(set(packets)), 1)
        self.assertIn("目的物: 所持者は分からない", packets[0])
        keys = [context_key(p, context_version=SUBJECTIVE_CONTEXT) for p in packets]
        self.assertEqual(len(set(keys)), 1)

    def test_hidden_peer_inventory_changes_do_not_change_packet(self):
        before = self.packet()
        for person in self.people.values():
            if person.id != self.actor.id:
                person.inventory[self.target] = 777
        self.assertEqual(before, self.packet())

    def test_self_uses_own_inventory_without_world_lookup(self):
        self.actor.inventory[self.target] = 1
        with patch.object(self.world, "holder", side_effect=AssertionError("objective lookup")):
            self.assertEqual(_objective_state(self.actor), "self")
            self.assertIn("目的物: 自分が持っている", self.packet())
        self.actor.inventory[self.target] = 0
        self.assertEqual(_objective_state(self.actor), "unknown")

    def test_no_target_is_none(self):
        self.actor.goal.target = None
        self.assertEqual(_objective_state(self.actor), "none")

    def test_explicit_actor_evidence_includes_wrong_beliefs(self):
        for state, label in (("none", "誰も持っていない"), ("ally", "味方が持っている"),
                             ("hostile", "敵対者が持っている"), ("other", "第三者が持っている")):
            evidence = ObjectiveKnowledge(self.actor.id, self.target, state, ("received:fixture-1",))
            with patch.object(self.world, "holder", side_effect=AssertionError("truth correction")):
                self.assertIn(f"目的物: {label}", self.packet(objective_knowledge=evidence))

    def test_evidence_for_other_actor_or_target_is_rejected(self):
        for actor_id, target_id in (("犬", self.target), (self.actor.id, "別の物")):
            with self.assertRaises(ValueError):
                self.packet(objective_knowledge=ObjectiveKnowledge(actor_id, target_id, "ally", ("seen:1",)))

    def test_nonself_without_evidence_is_rejected(self):
        with self.assertRaises(ValueError):
            ObjectiveKnowledge(self.actor.id, self.target, "hostile", ())
        with self.assertRaises(ValueError):
            ObjectiveKnowledge(self.actor.id, self.target, "self", ("seen:1",))

    def test_legacy_default_matches_explicit_version(self):
        self.assertEqual(situation(self.actor, self.world, self.present),
                         situation(self.actor, self.world, self.present, context_version=LEGACY_CONTEXT))
        text = "old context\n候補: old candidate"
        self.assertEqual(context_key(text), hashlib.sha256(text.encode()).hexdigest()[:16])
        self.assertNotEqual(context_key(text), context_key(text, context_version=SUBJECTIVE_CONTEXT))

    def test_invalid_context_version_is_rejected(self):
        with self.assertRaises(ValueError):
            situation(self.actor, self.world, self.present, context_version="typo")
        with self.assertRaises(ValueError):
            context_key("text", context_version="typo")

    def test_legacy_cannot_silently_discard_actor_evidence(self):
        evidence = ObjectiveKnowledge(self.actor.id, self.target, "ally", ("seen:1",))
        with self.assertRaises(ValueError):
            situation(self.actor, self.world, self.present, objective_knowledge=evidence)

    def test_new_rationality_passes_safe_packet_and_reuses_safe_cache(self):
        for method in ("noul", "choice"):
            with self.subTest(method=method):
                judge, table = RecordingJudge(), RationalityTable()
                rat = self.rationality(judge, table, method=method)
                with patch.object(self.world, "holder", side_effect=AssertionError("hidden holder")):
                    original = rat.multipliers(self.actor, self.world, self.present, self.actions)
                    replay = rat.multipliers(self.actor, self.world, self.present, self.actions)
                self.assertEqual(original, replay)
                self.assertEqual(len(judge.calls), 1)
                self.assertIn("所持者は分からない", judge.calls[0][0])
                self.assertEqual(rat.meta["context_version"], SUBJECTIVE_CONTEXT)

    def test_safe_and_legacy_cache_never_cross_even_for_same_self_text(self):
        self.actor.inventory[self.target] = 1
        for method in ("noul", "choice"):
            with self.subTest(method=method):
                table, legacy_judge, safe_judge = RationalityTable(), RecordingJudge(), RecordingJudge()
                legacy = Rationality(kappa=0.45, table=table, judge=legacy_judge, method=method)
                legacy.multipliers(self.actor, self.world, self.present, self.actions)
                safe = self.rationality(safe_judge, table, method=method)
                safe.multipliers(self.actor, self.world, self.present, self.actions)
                self.assertEqual(legacy_judge.calls[0][0], safe_judge.calls[0][0])
                self.assertEqual(len(safe_judge.calls), 1)
                self.assertTrue(set(legacy.new_entries).isdisjoint(safe.new_entries))

    def test_old_table_only_replay_is_preserved_and_safe_miss_stops(self):
        table = RationalityTable()
        legacy = Rationality(kappa=0.45, table=table, judge=RecordingJudge())
        original = legacy.multipliers(self.actor, self.world, self.present, self.actions)
        replay = Rationality(kappa=0.45, table=table, judge=TableOnlyJudge(backend_name="fixture"))
        self.assertEqual(original, replay.multipliers(self.actor, self.world, self.present, self.actions))
        safe = self.rationality(TableOnlyJudge(backend_name="fixture"), table)
        with self.assertRaises(RationalityTableMissError):
            safe.multipliers(self.actor, self.world, self.present, self.actions)

    def test_kappa_zero_does_not_read_world_or_call_judge(self):
        rat = Rationality(kappa=0, table=RationalityTable(), judge=TableOnlyJudge(backend_name="fixture"),
                          context_version=SUBJECTIVE_CONTEXT)
        with patch.object(self.world, "holder", side_effect=AssertionError("no render")):
            self.assertEqual(rat.multipliers(self.actor, self.world, self.present, self.actions),
                             ([1.0, 1.0], [None, None]))
        self.assertEqual(rat.meta["judge_calls"], 0)


if __name__ == "__main__":
    unittest.main()
