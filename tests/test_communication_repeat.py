"""Actor-only repetition, completion boundaries, and opt-in compatibility."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine.actions import Action
from engine.sim import Simulation
from gapengine.communication_repeat import CommunicationRepeat, VERSION
from gapengine.festival_policy import build_policies
from scripts.cultural_festival_probe import load


def config(**extra):
    return {"version": VERSION, "epoch": "fixture-run-1", "completion_mode": "native_one_slot", **extra}


def choice(action, t):
    return dict(kind="own_choice", verb="dialogue", args=list(action.args), t=t, duration_slots=1)


def speech(actor, action, t):
    peer, topic, intent, channel = action.args
    return dict(kind="speech", subject=actor, to=peer, topic=topic, intent=intent, channel=channel, t=t)


class RepeatStateTests(unittest.TestCase):
    def setUp(self):
        self.control = CommunicationRepeat(config())
        self.action = Action("dialogue", ("receiver", "issue", "consult", "contact"))

    def assess(self, t, context=None, action=None):
        return self.control.assess("actor", action or self.action, context or {}, t)

    def record(self, t=0, context=None, action=None):
        action = action or self.action
        assessment = self.assess(t, context, action)
        action.meta["decision_basis"] = {"selected": {"communication_repeat": assessment}}
        self.control.record("actor", action)
        return assessment

    def complete(self, t=0, context=None, action=None):
        action = action or self.action
        self.record(t, context, action)
        self.control.absorb("actor", {"perceptions": [choice(action, t), speech("actor", action, t + 1)]}, t + 1)

    def evidence(self, evidence_id="new", **extra):
        return {"actor_id": "actor", "epoch": "fixture-run-1",
                "issue_id": self.assess(0)["issue_id"], "evidence_id": evidence_id, "observed_t": 3, **extra}

    def deadline(self, deadline_t=6, **extra):
        return {"actor_id": "actor", "epoch": "fixture-run-1",
                "issue_id": self.assess(0)["issue_id"], "observed_t": 2, "deadline_t": deadline_t, **extra}

    def test_first_transmission_and_channel_switch(self):
        self.assertTrue(self.assess(0)["allowed"])
        self.complete()
        room = Action("dialogue", ("receiver", "issue", "consult", "room"))
        self.assertFalse(self.assess(100, action=room)["allowed"])
        self.assertEqual(self.assess(100, action=room)["repeat_reason"], "unchanged_automatic_repeat")
        self.assertEqual(self.assess(100)["reply_expected"], "unknown")

    def test_record_is_pending_not_sent(self):
        self.record()
        self.assertFalse(self.control.sent)
        self.assertEqual(self.assess(0)["repeat_reason"], "own_selected_pending")

    def test_native_completion_requires_choice_and_all_exact_fields(self):
        for field, value in (("subject", "other"), ("to", "other"), ("topic", "other"),
                             ("intent", "request"), ("channel", "room"), ("t", 2)):
            with self.subTest(field=field):
                self.setUp()
                self.record()
                event = speech("actor", self.action, 1)
                event[field] = value
                self.control.absorb("actor", {"perceptions": [choice(self.action, 0), event]}, 1)
                self.assertFalse(self.control.sent)
                self.assertFalse(self.control.pending)
                self.assertTrue(self.assess(2)["allowed"])
        self.setUp()
        self.record()
        self.control.absorb("actor", {"perceptions": [speech("actor", self.action, 1)]}, 1)
        self.assertFalse(self.control.sent)

    def test_failure_interruption_or_epoch_mismatch_not_sent(self):
        for perceptions, epoch in (([], "fixture-run-1"),
                                  ([choice(self.action, 0), speech("actor", self.action, 1)], "another-run")):
            self.setUp()
            self.record()
            self.control.absorb("actor", {"perceptions": perceptions, "communication_epoch": epoch}, 1)
            self.assertFalse(self.control.sent)

    def test_observed_new_evidence_consumed_only_after_completion(self):
        self.complete()
        evidence = self.evidence()
        context = {"communication_evidence": [evidence]}
        update = self.assess(5, context)
        self.assertTrue(update["allowed"])
        self.assertEqual(update["classification"], "update")
        self.assertEqual(update["evidence_refs"], ["new"])
        self.complete(5, context)
        self.assertFalse(self.assess(10, context)["allowed"])

    def test_unmatched_future_or_other_actor_evidence_not_new(self):
        self.complete()
        for changes in ({"actor_id": "other"}, {"issue_id": "unmatched"},
                        {"epoch": "other"}, {"observed_t": 20}, {"evidence_id": ""}):
            with self.subTest(changes=changes):
                self.assertFalse(self.assess(5, {"communication_evidence": [self.evidence(**changes)]})["allowed"])

    def test_old_evidence_reappearance_after_reminder_is_not_an_update(self):
        old = self.evidence("old", observed_t=0)
        self.complete(context={"communication_evidence": [old]})
        reminder_context = {"communication_deadlines": [self.deadline()]}
        self.assertEqual(self.assess(5, reminder_context)["classification"], "reminder")
        self.complete(5, reminder_context)
        latest = next(iter(self.control.sent.values()))
        self.assertEqual(latest["evidence_refs"], [])
        self.assertEqual(latest["consumed_evidence_refs"], ["old"])
        reappeared = self.assess(10, {"communication_evidence": [old]})
        self.assertFalse(reappeared["allowed"])
        self.assertEqual(reappeared["repeat_reason"], "unchanged_automatic_repeat")
        restored = CommunicationRepeat(config())
        restored.restore_state(json.loads(json.dumps(self.control.export_state())))
        self.assertFalse(restored.assess("actor", self.action, {"communication_evidence": [old]}, 10)["allowed"])

    def test_only_known_near_deadline_allows_reminder(self):
        self.complete()
        self.assertEqual(self.assess(5, {"communication_deadlines": [self.deadline()]})["classification"], "reminder")
        for due in (None, "unknown", 100, 4, True):
            self.assertFalse(self.assess(5, {"communication_deadlines": [self.deadline(due)]})["allowed"])
        for extra in ({"actor_id": "other"}, {"issue_id": "other"}, {"observed_t": 10}, {"epoch": "other"}):
            self.assertFalse(self.assess(5, {"communication_deadlines": [self.deadline(**extra)]})["allowed"])

    def test_only_explicit_actor_tendency_and_frequency(self):
        for tendency in ("reminder", "reconfirm"):
            self.control = CommunicationRepeat(config(actor_tendencies={"actor": tendency}))
            self.complete()
            self.assertFalse(self.assess(4)["allowed"])
            self.assertEqual(self.assess(5)["classification"], tendency)
            self.assertTrue(self.assess(5)["allowed"])
        self.control = CommunicationRepeat(config(actor_tendencies={"other": "reminder"}))
        self.complete()
        self.assertFalse(self.assess(5)["allowed"])

    def test_request_direction_id_and_version_not_reused_for_consult(self):
        request = Action("dialogue", ("receiver", "issue", "request", "contact"))
        self.complete(action=request)
        sent = self.control.view()["completed_sent"][0]
        self.assertEqual(sent["request_id"], "0:actor:receiver:issue")
        self.assertEqual(sent["request_version"], "unknown")
        self.assertEqual(sent["content_version"], 1)
        self.assertTrue(self.assess(5)["allowed"])
        self.control.absorb("actor", {"own_requests": [dict(id="reverse", **{"from": "receiver"}, to="actor", topic="issue")],
            "own_responses": [dict(request_id="reverse", **{"from": "actor"}, to="receiver", topic="issue")],
            "perceptions": [dict(kind="speech", subject="receiver", to="actor", topic="issue", intent="accept", t=5)]}, 5)
        self.assertFalse(self.assess(10, action=request)["allowed"])
        self.assertTrue(self.assess(10)["allowed"])
        observations = self.control.view()["directional_observations"]
        old = next(o for o in observations if o.get("subject") == "receiver")
        self.assertEqual(old["classification"], "unknown")
        self.assertEqual(old["issue_id"], "unknown")

    def test_historical_speech_is_unknown_even_if_same_topic(self):
        self.control.absorb("actor", {"perceptions": [speech("actor", self.action, 1)]}, 5)
        self.assertFalse(self.control.sent)
        self.assertTrue(self.assess(5)["allowed"])
        self.assertEqual(self.control.view()["directional_observations"][0]["reply_expected"], "unknown")

    def test_explicit_completion_hook_and_terminal_failure(self):
        for result in ("completed", "failed", "interrupted"):
            self.control = CommunicationRepeat(config(completion_mode="explicit"))
            pending = self.record()
            self.control.absorb("actor", {"perceptions": [dict(kind="own_communication_completed", subject="actor",
                result=result, communication_repeat=deepcopy(pending), t=1)]}, 1)
            self.assertEqual(bool(self.control.sent), result == "completed")
            self.assertFalse(self.control.pending)

    def test_export_restore_roundtrip_and_config_actor_epoch_validation(self):
        self.complete()
        state = json.loads(json.dumps(self.control.export_state()))
        restored = CommunicationRepeat(config())
        restored.restore_state(state)
        self.assertEqual(self.control.view(), restored.view())
        self.assertFalse(restored.assess("actor", self.action, {}, 20)["allowed"])
        with self.assertRaises(ValueError):
            CommunicationRepeat(config(epoch="another-run")).restore_state(state)
        with self.assertRaises(ValueError):
            CommunicationRepeat(config(deadline_window_slots=9)).restore_state(state)
        with self.assertRaises(ValueError):
            restored.assess("another-actor", self.action, {}, 20)
        bad = deepcopy(state)
        next(iter(bad["sent"].values()))["actor_id"] = "another-actor"
        with self.assertRaises(ValueError):
            CommunicationRepeat(config()).restore_state(bad)

    def test_pending_restore_can_complete_at_next_boundary(self):
        self.record()
        restored = CommunicationRepeat(config())
        restored.restore_state(json.loads(json.dumps(self.control.export_state())))
        restored.absorb("actor", {"perceptions": [choice(self.action, 0), speech("actor", self.action, 1)]}, 1)
        self.assertTrue(restored.sent)

    def test_invalid_config_rejected(self):
        for extra in ({"epoch": ""}, {"version": "unknown"}, {"deadline_window_slots": True},
                      {"minimum_repeat_interval_slots": -1}, {"actor_tendencies": {"actor": "unknown"}},
                      {"global_reminder_tendency": True}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                CommunicationRepeat(config(**extra))


class PolicyConnectionTests(unittest.TestCase):
    def fixture(self, configured=True, completion_mode="native_one_slot"):
        world, people = load()
        policies = build_policies(world.slot_contract, communication_config=config(completion_mode=completion_mode) if configured else None)
        lead = world.slot_contract["play"]["lead"]
        actor = people[lead]
        actor.slot_context = dict(own_practice_completed=0, own_costume_prepared=False,
                                  own_requests=[], own_responses=[], perceptions=[])
        organizer = world.slot_contract["actions"]["sound_check"]["actor"]
        # Take the actual native vocabulary, avoiding a guessed issue mapping.
        sim = Simulation(0, world, people, Path("/tmp/unused"))
        action = next(a for a, _ in policies[lead].reweight(
                      actor, object(), [], sim.slot_runtime.candidates(actor), turn=1, day=1)
                      if a.verb == "dialogue" and a.args[0] == organizer and a.args[2] == "consult")
        return world, people, actor, policies[lead], action

    def test_basis_has_classification_and_started_log_retains_it(self):
        world, people, actor, policy, action = self.fixture()
        weighted = policy.reweight(actor, object(), [], [(action, 1), (Action("wait"), 1)], turn=1, day=1)
        action = next(a for a, _ in weighted if a.verb == "dialogue")
        selected = action.meta["decision_basis"]["selected"]["communication_repeat"]
        self.assertEqual(selected["classification"], "automatic")
        self.assertEqual(selected["reply_expected"], "unknown")
        rows = []
        sim = Simulation(0, world, people, Path("/tmp/unused"))
        sim.turn, sim.slot = 1, world.slots[0]
        sim.slot_runtime.writer = type("Writer", (), {"write": lambda self, row: rows.append(row)})()
        actor.policy=policy  # The runtime requires the actual actor-owned producer.
        policy.record(actor, action)
        sim.slot_runtime.start(actor, action, 1.0)
        self.assertEqual(rows[0]["details"]["decision_basis"]["selected"]["communication_repeat"], selected)
        self.assertFalse(policy.communication_repeat.sent)
        sim.turn, sim.slot = 2, world.slots[1]
        sim.slot_runtime.complete(sim.slot_runtime.pending[0])
        actor.slot_context["perceptions"] = sim.slot_runtime.perceptions[actor.id]
        policy.reweight(actor, object(), [], [(deepcopy(action), 1), (Action("wait"), 1)], turn=2, day=1)
        self.assertTrue(policy.communication_repeat.sent)

    def test_automatic_repeat_removed_wait_survives_empty_falls_back(self):
        world, people, actor, policy, action = self.fixture()
        selected = policy.reweight(actor, object(), [], [(action, 1)], turn=1, day=1)[0][0]
        policy.record(actor, selected)
        actor.slot_context["perceptions"] = [choice(selected, 0), speech(actor.id, selected, 1)]
        weighted = policy.reweight(actor, object(), [], [(deepcopy(action), 1), (Action("wait"), 1)], turn=2, day=1)
        self.assertEqual([a.verb for a, _ in weighted], ["wait"])
        actor.slot_context["perceptions"] = []
        self.assertEqual(policy.reweight(actor, object(), [], [(deepcopy(action), 1)], turn=10, day=1), [])
        self.assertEqual(policy.audits[-1]["positive_count"], 0)

    def test_explicit_producer_uses_only_published_selected_metadata(self):
        world, people, actor, policy, action = self.fixture(completion_mode="explicit")
        selected = policy.reweight(actor, object(), [], [(action, 1)], turn=1, day=1)[0][0]
        published = json.loads(json.dumps(selected.meta["decision_basis"]["selected"]["communication_repeat"]))
        frozen_audit = json.dumps(policy.audits[-1], sort_keys=True)
        self.assertIn("token", published)
        policy.record(actor, selected)
        self.assertEqual(json.dumps(policy.audits[-1], sort_keys=True), frozen_audit)
        self.assertEqual(selected.meta["decision_basis"]["selected"]["communication_repeat"], published)
        actor.slot_context["perceptions"] = [dict(kind="own_communication_completed", subject=actor.id,
            result="completed", t=1, communication_repeat=published)]
        result = policy.reweight(actor, object(), [], [(deepcopy(action), 1), (Action("wait"), 1)], turn=2, day=1)
        self.assertTrue(policy.communication_repeat.sent)
        self.assertFalse(policy.communication_repeat.pending)
        self.assertEqual([a.verb for a, _ in result], ["wait"])

    def test_reversed_15_o_clock_request_answer_does_not_resolve_consult(self):
        world, people, actor, policy, action = self.fixture()
        actor.slot_context["perceptions"] = [dict(kind="speech", subject=action.args[0], to=actor.id,
            topic=action.args[1], intent="accept", t=18, request_id="3:organizer:lead:performance")]
        offered = policy.reweight(actor, object(), [], [(action, 1), (Action("wait"), 1)], turn=19, day=1)
        consultation = next(a for a, _ in offered if a.verb == "dialogue")
        self.assertEqual(consultation.meta["decision_basis"]["selected"]["communication_repeat"]["repeat_reason"],
                         "first_transmission")

    def test_wrong_request_direction_or_version_not_a_reply_candidate(self):
        world, people, actor, policy, action = self.fixture()
        peer, topic = action.args[:2]
        response = Action("dialogue", (peer, topic, "accept", "contact"), {"request_id": "req", "request_version": 2})
        for wrong in ({"from": "someone"}, {"to": "someone"}, {"topic": "other"}, {"version": 1}):
            actor.slot_context["own_requests"] = [dict(id="req", **{"from": peer}, to=actor.id, topic=topic, version=2)]
            actor.slot_context["own_requests"][0].update(wrong)
            result = policy.reweight(actor, object(), [], [(deepcopy(response), 1), (Action("wait"), 1)], turn=1, day=1)
            self.assertFalse(any(a.verb == "dialogue" for a, _ in result))

    def test_default_legacy_has_no_repeat_metadata(self):
        world, people, actor, policy, action = self.fixture(configured=False)
        result = policy.reweight(actor, object(), [], [(action, 1), (Action("wait"), 1)], turn=1, day=1)
        self.assertNotIn("communication_repeat", policy.audits[-1]["basis"])
        self.assertTrue(all("communication_repeat" not in a.meta["decision_basis"]["selected"] for a, _ in result))
        self.assertIsNone(policy.export_communication_state())

    def test_native_run_without_network_keeps_movement_and_activity_options(self):
        world, people = load()
        policies = build_policies(world.slot_contract, communication_config=config())
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp, patch("socket.socket.connect", side_effect=AssertionError("network forbidden")):
            sim = Simulation(0, world, people, Path(tmp), policies=policies)
            sim.run()
            self.assertTrue(all(audit["positive_count"] > 0 for policy in policies.values() for audit in policy.audits))
            self.assertTrue(any(policy.communication_repeat.sent for policy in policies.values()))
            self.assertTrue(all(not a["basis"].get("own_completed_sent_messages") for policy in policies.values() for a in policy.audits))


if __name__ == "__main__":
    unittest.main()
