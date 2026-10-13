"""Objective agreement, real completion evidence and resume contracts."""
from copy import deepcopy
import hashlib
import json
import unittest

from engine.promises_v2 import PromiseRegistry


def terms(promise_id="cards-promise", **updates):
    value = {"promise_id": promise_id, "version": 1, "proposer": "蒼", "promisor": "蒼",
             "beneficiary": "灯", "acceptance_parties": ["蒼", "灯"], "place": "展示室",
             "start": 10, "deadline": 100, "grace_until": None,
             "requirements": [{"task_id": "cards", "units": 4, "contribution_by": "蒼"}]}
    value.update(updates)
    return value


def completion(event_id="work1", **updates):
    value = {"id": event_id, "task_id": "cards", "actor": "蒼", "units": 4,
             "at": 100, "place": "展示室"}
    value.update(updates)
    return value


def registry():
    return PromiseRegistry({"蒼", "灯", "紗季", "蓮"}, {"cards", "heading", "photo"}, {"展示室", "体育館"})


def dump(reg):
    return json.dumps(reg.export_state(), ensure_ascii=False, sort_keys=True)


class PromiseTests(unittest.TestCase):
    def accepted(self, reg=None, value=None, at=0):
        reg = registry() if reg is None else reg
        value = terms() if value is None else value
        offer = reg.offer(value, at, value["promise_id"] + "-offer")
        for actor in value["acceptance_parties"]:
            result = reg.accept(value["promise_id"], value["version"], actor, offer["terms_hash"], at,
                                value["promise_id"] + "-accept-" + actor)
        return reg, result

    def test_bilateral_same_hash_required_and_future_start(self):
        reg = registry()
        offered = reg.offer(terms(), 0, "offer")
        baseline = dump(reg)
        for actor, digest in [("蓮", offered["terms_hash"]), ("灯", "wrong")]:
            with self.subTest(actor=actor), self.assertRaises(ValueError):
                reg.accept("cards-promise", 1, actor, digest, 1, "bad")
            self.assertEqual(dump(reg), baseline)
        reg.accept("cards-promise", 1, "蒼", offered["terms_hash"], 1, "a1")
        self.assertEqual(reg.get("cards-promise")["status"], "offered")
        reg.accept("cards-promise", 1, "灯", offered["terms_hash"], 2, "a2")
        self.assertEqual(reg.get("cards-promise")["status"], "accepted")
        reg.evaluate(9, {"completion_events": []})
        self.assertEqual(reg.get("cards-promise")["status"], "accepted")
        changes = reg.evaluate(10, {"completion_events": []})
        self.assertEqual([c["status"] for c in changes], ["active"])

    def test_exact_deadline_completion_precedes_deadline_failure(self):
        reg, _ = self.accepted()
        ledger = {"completion_events": [completion()], "tasks": {"cards": {"complete": True}}}
        before = deepcopy(ledger)
        changes = reg.evaluate(100, ledger)
        result = reg.get("cards-promise")
        self.assertEqual(result["status"], "fulfilled")
        self.assertTrue(result["due_met"])
        self.assertEqual(result["closed_at"], 100)
        self.assertEqual(result["evidence_ids"], ["work1"])
        self.assertNotIn("breached", [c["status"] for c in changes])
        self.assertEqual(ledger, before)

    def test_partial_work_before_deadline_and_breach_without_grace(self):
        reg, _ = self.accepted()
        ledger = {"completion_events": [completion(units=2, at=30)]}
        reg.evaluate(50, ledger)
        self.assertEqual(reg.get("cards-promise")["status"], "active")
        self.assertEqual(reg.get("cards-promise")["progress"][0]["completed_units"], 2)
        changes = reg.evaluate(100, ledger)
        self.assertEqual([c["status"] for c in changes], ["partial", "breached"])
        result = reg.get("cards-promise")
        self.assertTrue(result["is_overdue"])
        self.assertFalse(result["due_met"])
        self.assertEqual(result["progress"][0]["completed_units"], 2)
        self.assertEqual(result["outcome_reason"], "choice_or_unconfirmed")

    def test_no_progress_overdue_then_breach_at_grace(self):
        reg, _ = self.accepted(value=terms(grace_until=130))
        reg.evaluate(100, {"completion_events": []})
        result = reg.get("cards-promise")
        self.assertEqual(result["status"], "overdue")
        self.assertIsNone(result["closed_at"])
        reg.evaluate(130, {"completion_events": []})
        self.assertEqual(reg.get("cards-promise")["status"], "breached")

    def test_late_fulfillment_at_grace_retains_partial_history(self):
        reg, _ = self.accepted(value=terms(grace_until=130))
        events = [completion("half", units=2, at=50), completion("finish", units=2, at=130)]
        changes = reg.evaluate(130, {"completion_events": events})
        result = reg.get("cards-promise")
        self.assertEqual(result["status"], "fulfilled")
        self.assertFalse(result["due_met"])
        self.assertTrue(result["was_overdue"])
        self.assertIn("partial", [c["status"] for c in changes])
        self.assertNotIn("breached", [c["status"] for c in changes])
        self.assertEqual(result["closed_at"], 130)

    def test_after_grace_completion_cannot_reopen_terminal(self):
        reg, _ = self.accepted(value=terms(grace_until=130))
        reg.evaluate(140, {"completion_events": [completion(at=131)]})
        result = reg.get("cards-promise")
        self.assertEqual(result["status"], "breached")
        self.assertEqual(result["closed_at"], 130)
        self.assertEqual(result["progress"][0]["completed_units"], 0)

    def test_completion_matches_actor_place_window_and_delivery(self):
        value = terms(requirements=[{"task_id": "photo", "units": 1, "contribution_by": "蒼", "delivered_to": "灯"}])
        reg, _ = self.accepted(value=value, at=12)
        wrong = [completion("early", task_id="photo", units=1, at=11, delivered_to="灯"),
                 completion("actor", task_id="photo", actor="紗季", units=1, at=15, delivered_to="灯"),
                 completion("place", task_id="photo", place="体育館", units=1, at=16, delivered_to="灯"),
                 completion("delivery", task_id="photo", units=1, at=17, delivered_to="蓮")]
        reg.evaluate(20, {"completion_events": wrong, "contributions": {"蒼": 100}})
        self.assertEqual(reg.get("cards-promise")["progress"][0]["completed_units"], 0)
        reg.evaluate(21, {"completion_events": wrong + [completion("real", task_id="photo", units=1, at=21, delivered_to="灯")]})
        self.assertEqual(reg.get("cards-promise")["status"], "fulfilled")
        self.assertEqual(reg.get("cards-promise")["evidence_ids"], ["real"])

    def test_change_proposal_does_not_release_old_obligation(self):
        reg, old = self.accepted(value=terms(grace_until=150))
        proposal = reg.propose_change("cards-promise", terms(version=2, deadline=200), 20, "change")
        reg.accept_change("cards-promise", 2, "蒼", proposal["terms_hash"], 30, "c-a")
        reg.evaluate(100, {"completion_events": []})
        self.assertEqual(reg.get("cards-promise")["status"], "overdue")
        self.assertEqual(reg.get("cards-promise")["version"], 1)
        reg.accept_change("cards-promise", 2, "灯", proposal["terms_hash"], 110, "c-b")
        new = reg.get("cards-promise")
        old = reg.get("cards-promise", 1)
        self.assertEqual(new["version"], 2)
        self.assertEqual(new["replaces_version"], 1)
        self.assertEqual(old["status"], "renegotiated")
        self.assertTrue(old["was_overdue"])
        self.assertFalse(old["due_met"])
        self.assertIn("overdue", [r["status"] for r in old["history"]])

    def test_reduced_new_terms_reuse_real_partial_evidence_without_new_work(self):
        reg, _ = self.accepted()
        ledger = {"completion_events": [completion("half", units=2, at=30)]}
        original_ledger = deepcopy(ledger)
        reg.evaluate(30, ledger)
        proposal = reg.propose_change("cards-promise", terms(version=2, deadline=70,
            requirements=[{"task_id": "cards", "units": 2, "contribution_by": "蒼"}]), 35, "reduce")
        for actor in ("蒼", "灯"):
            reg.accept_change("cards-promise", 2, actor, proposal["terms_hash"], 40, "reduce-" + actor)
        reg.evaluate(40, ledger)
        result = reg.get("cards-promise")
        self.assertEqual(result["status"], "fulfilled")
        self.assertEqual(result["closed_at"], 40)
        self.assertEqual(result["evidence_ids"], ["half"])
        self.assertEqual(reg.get("cards-promise", 1)["progress"][0]["completed_units"], 2)
        self.assertEqual(ledger, original_ledger)
        self.assertEqual(PromiseRegistry.from_state(reg.export_state()).export_state(), reg.export_state())

    def test_new_window_excludes_old_partial_outside_explicit_terms(self):
        reg, _ = self.accepted()
        ledger = {"completion_events": [completion("half", units=2, at=30)]}
        reg.evaluate(30, ledger)
        proposal = reg.propose_change("cards-promise", terms(version=2, start=35, deadline=70,
            requirements=[{"task_id": "cards", "units": 2, "contribution_by": "蒼"}]), 35, "reduce")
        for actor in ("蒼", "灯"):
            reg.accept_change("cards-promise", 2, actor, proposal["terms_hash"], 40, "reduce-" + actor)
        reg.evaluate(40, ledger)
        self.assertEqual(reg.get("cards-promise")["progress"][0]["completed_units"], 0)
        self.assertEqual(reg.get("cards-promise")["status"], "active")

    def test_change_cannot_farm_unrelated_pre_change_work(self):
        reg, _ = self.accepted()
        ledger = {"completion_events": [completion("cards-half", units=2, at=30),
                                        completion("old-heading", task_id="heading", units=1, at=30)]}
        reg.evaluate(30, ledger)
        proposal = reg.propose_change("cards-promise", terms(version=2, deadline=70,
            requirements=[{"task_id": "heading", "units": 1, "contribution_by": "蒼"}]), 35, "heading-instead")
        for actor in ("蒼", "灯"):
            reg.accept_change("cards-promise", 2, actor, proposal["terms_hash"], 40, "heading-" + actor)
        reg.evaluate(40, ledger)
        self.assertEqual(reg.get("cards-promise")["progress"][0]["completed_units"], 0)
        self.assertEqual(reg.get("cards-promise")["status"], "active")

    def test_renegotiation_after_breach_keeps_past_failure(self):
        reg, _ = self.accepted()
        reg.evaluate(100, {"completion_events": []})
        offer = reg.propose_change("cards-promise", terms(version=2, start=120, deadline=200), 110, "new")
        for actor in ("蒼", "灯"):
            reg.accept_change("cards-promise", 2, actor, offer["terms_hash"], 115, "new-" + actor)
        self.assertEqual(reg.get("cards-promise")["status"], "accepted")
        old = reg.get("cards-promise", 1)
        self.assertEqual(old["status"], "renegotiated")
        self.assertIn("breached", [r["status"] for r in old["history"]])
        self.assertEqual(next(r["at"] for r in old["history"] if r["status"] == "breached"), 100)

    def test_unilateral_cancel_not_release_and_unanimous_preserves_progress(self):
        reg, offered = self.accepted()
        reg.evaluate(30, {"completion_events": [completion(units=1, at=30)]})
        reg.cancel("cards-promise", 1, "蒼", offered["terms_hash"], 40, "cancel-a")
        self.assertEqual(reg.get("cards-promise")["status"], "active")
        reg.cancel("cards-promise", 1, "灯", offered["terms_hash"], 50, "cancel-b")
        self.assertEqual(reg.get("cards-promise")["status"], "cancelled")
        self.assertEqual(reg.get("cards-promise")["progress"][0]["completed_units"], 1)
        changes = reg.evaluate(200, {"completion_events": []})
        self.assertEqual([row["status"] for row in changes], ["cancelled"])
        self.assertEqual(reg.get("cards-promise")["status"], "cancelled")

    def test_duplicate_concrete_offer_and_completed_object_repromise_rejected(self):
        reg, _ = self.accepted()
        before = dump(reg)
        with self.assertRaises(ValueError):
            reg.offer(terms("clone"), 0, "duplicate")
        self.assertEqual(dump(reg), before)
        reg.evaluate(100, {"completion_events": [completion()]})
        before = dump(reg)
        with self.assertRaises(ValueError):
            reg.offer(terms("clone", deadline=200), 110, "again")
        self.assertEqual(dump(reg), before)

    def test_quantity_variant_requires_version_instead_of_duplicate_promise(self):
        reg, _ = self.accepted()
        before = dump(reg)
        with self.assertRaises(ValueError):
            reg.offer(terms("clone", requirements=[{"task_id": "cards", "units": 5, "contribution_by": "蒼"}]), 1, "more")
        self.assertEqual(dump(reg), before)

    def test_old_fulfillment_while_change_pending_cannot_be_replaced(self):
        reg, _ = self.accepted()
        proposal = reg.propose_change("cards-promise", terms(version=2, deadline=200), 20, "change")
        reg.evaluate(50, {"completion_events": [completion(at=50)]})
        before = dump(reg)
        with self.assertRaises(ValueError):
            reg.accept_change("cards-promise", 2, "蒼", proposal["terms_hash"], 60, "new-a")
        self.assertEqual(dump(reg), before)

    def test_cached_future_completion_cannot_be_overwritten_by_change(self):
        reg, _ = self.accepted()
        reg.evaluate(20, {"completion_events": [completion(at=50)]})
        proposal = reg.propose_change("cards-promise", terms(version=2, deadline=200), 20, "change")
        reg.accept_change("cards-promise", 2, "蒼", proposal["terms_hash"], 30, "new-a")
        before = dump(reg)
        with self.assertRaises(ValueError):
            reg.accept_change("cards-promise", 2, "灯", proposal["terms_hash"], 60, "new-b")
        self.assertEqual(dump(reg), before)
        reg.evaluate(60, {"completion_events": [completion(at=50)]})
        self.assertEqual(reg.get("cards-promise")["status"], "fulfilled")

    def test_expired_acceptance_is_rejected_before_mutation(self):
        reg = registry()
        proposal = reg.offer(terms(), 0, "offer")
        before = dump(reg)
        with self.assertRaises(ValueError):
            reg.accept("cards-promise", 1, "蒼", proposal["terms_hash"], 101, "late")
        self.assertEqual(dump(reg), before)

    def test_replay_command_and_duplicate_completion_have_no_effect(self):
        reg = registry()
        offered = reg.offer(terms(), 0, "offer")
        before = dump(reg)
        self.assertEqual(reg.offer(terms(), 0, "offer"), offered)
        self.assertEqual(dump(reg), before)
        reg.accept("cards-promise", 1, "蒼", offered["terms_hash"], 0, "a1")
        accepted = reg.accept("cards-promise", 1, "灯", offered["terms_hash"], 0, "a2")
        reg.evaluate(30, {"completion_events": [completion(units=2, at=30)] * 2})
        self.assertEqual(reg.get("cards-promise")["progress"][0]["completed_units"], 2)
        before = dump(reg)
        self.assertEqual(reg.accept("cards-promise", 1, "灯", offered["terms_hash"], 0, "a2"), accepted)
        self.assertEqual(dump(reg), before)
        with self.assertRaises(ValueError):
            reg.accept("cards-promise", 1, "蒼", offered["terms_hash"], 0, "a2")
        self.assertEqual(dump(reg), before)

    def test_invalid_terms_rejected_without_mutation(self):
        bad_values = []
        for key, value in [("promise_id", ""), ("version", True), ("proposer", "?"), ("place", "?"),
                           ("start", -1), ("deadline", True), ("deadline", 9), ("grace_until", 99),
                           ("acceptance_parties", []), ("acceptance_parties", ["蒼"]),
                           ("acceptance_parties", ["蒼", "灯", "灯"]),
                           ("acceptance_parties", ["蒼", {}]), ("requirements", []),
                           ("preconditions", [{"material": "secret"}]), ("outcome_reason", "malice")]:
            bad_values.append(terms(**{key: value}))
        for key, value in [("task_id", "?"), ("contribution_by", "?"), ("units", True),
                           ("units", 0), ("units", -1), ("delivered_to", "?")]:
            requirement = deepcopy(terms()["requirements"][0]); requirement[key] = value
            bad_values.append(terms(requirements=[requirement]))
        missing = terms(); missing.pop("place"); bad_values.append(missing)
        unsupported = terms(); unsupported["OR"] = []; bad_values.append(unsupported)
        bad_values.append(terms(requirements=terms()["requirements"] * 2))
        for value in bad_values:
            reg = registry(); before = dump(reg)
            with self.subTest(value=value), self.assertRaises(ValueError):
                reg.offer(value, 0, "invalid")
            self.assertEqual(dump(reg), before)

    def test_invalid_evidence_batch_is_atomic_and_forged_id_rejected(self):
        reg, _ = self.accepted()
        baseline = dump(reg)
        for bad in [completion("bad", units=True), completion("bad", actor="?"),
                    completion("bad", at=-1), {"id": "generic", "kind": "rumor"}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                reg.evaluate(30, {"completion_events": [completion(units=1, at=20), bad]})
            self.assertEqual(dump(reg), baseline)
        reg.evaluate(20, {"completion_events": [completion(units=1, at=20)]})
        baseline = dump(reg)
        with self.assertRaises(ValueError):
            reg.evaluate(30, {"completion_events": [completion(units=4, at=20)]})
        self.assertEqual(dump(reg), baseline)

    def test_late_insertion_after_a_processed_boundary_is_rejected(self):
        reg, _ = self.accepted()
        reg.evaluate(100, {"completion_events": []})
        before = dump(reg)
        with self.assertRaises(ValueError):
            reg.evaluate(100, {"completion_events": [completion()]})
        self.assertEqual(dump(reg), before)

    def test_resume_repeated_evaluation_is_byte_identical(self):
        reg, _ = self.accepted(value=terms(grace_until=130))
        first = [completion("half", units=2, at=20)]
        reg.evaluate(100, {"completion_events": first})
        saved = json.loads(dump(reg))
        resumed = PromiseRegistry.from_state(saved)
        baseline = dump(resumed)
        self.assertEqual(resumed.evaluate(100, {"completion_events": first}), [])
        self.assertEqual(dump(resumed), baseline)
        ledger = {"completion_events": first + [completion("finish", units=2, at=120)]}
        self.assertEqual(reg.evaluate(120, ledger), resumed.evaluate(120, ledger))
        self.assertEqual(dump(reg), dump(resumed))
        ids = [r["id"] for r in resumed.export_state()["transitions"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_pending_command_transitions_are_reported_once_after_resume(self):
        reg, _ = self.accepted()
        resumed = PromiseRegistry.from_state(reg.export_state())
        expected = ["offered", "accepted", "active"]
        # active starts at10, accepted was issued at0 by the command.
        rows = resumed.evaluate(10, {"completion_events": []})
        self.assertEqual([r["status"] for r in rows], expected)
        self.assertEqual(reg.evaluate(10, {"completion_events": []}), rows)
        checkpoint = dump(resumed)
        self.assertEqual(resumed.evaluate(10, {"completion_events": []}), [])
        self.assertEqual(dump(resumed), checkpoint)
        self.assertEqual(dump(reg), dump(resumed))

    def test_checkpoint_reporting_cursor_types_and_bounds_are_strict(self):
        reg, _ = self.accepted()
        original = reg.export_state()
        for cursor in (True, -1, len(original["transitions"]) + 1, "0", 0.5):
            invalid = deepcopy(original); invalid["reported_transitions"] = cursor
            body = {k: v for k, v in invalid.items() if k != "state_hash"}
            invalid["state_hash"] = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
                                                             separators=(",", ":")).encode()).hexdigest()
            with self.subTest(cursor=cursor), self.assertRaises(ValueError):
                PromiseRegistry.from_state(invalid)

    def test_all_concrete_requirements_and_delivery_must_hold(self):
        value = terms(requirements=[{"task_id": "cards", "units": 2, "contribution_by": "蒼"},
                                    {"task_id": "heading", "units": 1, "contribution_by": "蒼"},
                                    {"task_id": "photo", "units": 1, "contribution_by": "蒼", "delivered_to": "灯"}])
        reg, _ = self.accepted(value=value)
        ledger = {"completion_events": [completion("cards-done", units=2, at=50),
                                        completion("heading-done", task_id="heading", units=1, at=70)]}
        reg.evaluate(70, ledger)
        self.assertEqual(reg.get("cards-promise")["status"], "active")
        ledger["completion_events"].append(completion("photo-received", task_id="photo", units=1, at=95, delivered_to="灯"))
        reg.evaluate(95, ledger)
        result = reg.get("cards-promise")
        self.assertEqual(result["status"], "fulfilled")
        self.assertEqual(set(result["evidence_ids"]), {"cards-done", "heading-done", "photo-received"})

    def test_same_all_terms_hash_is_order_independent_but_quantity_sensitive(self):
        reg = registry()
        original = terms(acceptance_parties=["蒼", "灯"])
        reordered = dict(reversed(list(original.items()))); reordered["acceptance_parties"] = ["灯", "蒼"]
        self.assertEqual(reg.terms_hash(original), reg.terms_hash(reordered))
        altered = terms(requirements=[{"task_id": "cards", "units": 5, "contribution_by": "蒼"}])
        self.assertNotEqual(reg.terms_hash(original), reg.terms_hash(altered))

    def test_multiple_promises_jump_and_split_transition_order_match(self):
        one, _ = self.accepted(value=terms(grace_until=130))
        self.accepted(one, terms("heading-promise", requirements=[{"task_id": "heading", "units": 1,
                                                                 "contribution_by": "蒼"}], deadline=80, grace_until=120))
        split = PromiseRegistry.from_state(one.export_state())
        ledger = {"completion_events": [completion("half", units=2, at=30),
                                        completion("heading", task_id="heading", units=1, at=90),
                                        completion("finish", units=2, at=110)]}
        one.evaluate(130, ledger)
        for now in (10, 30, 80, 90, 100, 110, 120, 130):
            split.evaluate(now, ledger)
        self.assertEqual(dump(one), dump(split))

    def test_invalid_change_cannot_drop_existing_party(self):
        reg, _ = self.accepted()
        before = dump(reg)
        with self.assertRaises(ValueError):
            reg.propose_change("cards-promise", terms(version=2, promisor="紗季", acceptance_parties=["紗季", "灯"]), 20, "drop")
        self.assertEqual(dump(reg), before)

    def test_future_event_not_counted_and_time_reversal_rejected(self):
        reg, _ = self.accepted()
        ledger = {"completion_events": [completion(at=60)]}
        reg.evaluate(30, ledger)
        self.assertEqual(reg.get("cards-promise")["progress"][0]["completed_units"], 0)
        before = dump(reg)
        with self.assertRaises(ValueError):
            reg.evaluate(29, ledger)
        self.assertEqual(dump(reg), before)
        reg.evaluate(60, ledger)
        self.assertEqual(reg.get("cards-promise")["status"], "fulfilled")

    def test_checkpoint_corruption_and_version_refused(self):
        reg, _ = self.accepted()
        original = reg.export_state()
        for key, value in [("schema_version", True), ("schema_version", 2), ("clock", -1),
                           ("contract", "v1"), ("transitions", []), ("promises", {"bad": {}})]:
            invalid = deepcopy(original); invalid[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                PromiseRegistry.from_state(invalid)
        self.assertEqual(PromiseRegistry.from_state(original).export_state(), original)

    def test_structurally_invalid_rehashed_checkpoint_is_rejected(self):
        reg, _ = self.accepted()
        original = reg.export_state()
        for key, value in [("effective_at", -1), ("status", "fulfilled"), ("due_met", 1),
                           ("progress", [{"completed_units": True}]), ("closed_at", 1)]:
            invalid = deepcopy(original)
            invalid["promises"]["cards-promise"]["versions"]["1"][key] = value
            body = {k: v for k, v in invalid.items() if k != "state_hash"}
            invalid["state_hash"] = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
                                                             separators=(",", ":")).encode()).hexdigest()
            with self.subTest(key=key), self.assertRaises(ValueError):
                PromiseRegistry.from_state(invalid)


if __name__ == "__main__":
    unittest.main()
