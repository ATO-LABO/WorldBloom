"""No model calls: actor-known original identity across decision boundaries."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest

from engine.actions import Action
from engine import communication_boundary as boundary
from engine.sim import Simulation
from gapengine.communication_repeat import (
    CommunicationIdentity, CommunicationRepeat, DECISION_VERSION, IDENTITY_VERSION,
    VERSION, decision_identity, digest, completion_digest,
)
from gapengine.knowledge_text import describe_candidate_coarse, context_key
from gapengine.festival_policy import build_policies
from scripts.cultural_festival_probe import load


def config(version=DECISION_VERSION, **changes):
    return dict(version=version, epoch='decision-fixture', completion_mode='explicit',
                actor_tendencies={'actor': 'reminder'}, **changes)


class DecisionIdentityTests(unittest.TestCase):
    def setUp(self):
        self.control = CommunicationRepeat(config())
        self.actor = SimpleNamespace(id='actor', policy=SimpleNamespace(communication_repeat=self.control), slot_context={})

    def assess(self, tick=0, channel='contact', context=None):
        action = Action('dialogue', ('peer', 'topic', 'request', channel))
        meta = self.control.assess('actor', action, context or {}, tick)
        action.meta['decision_basis'] = {'selected': {'communication_repeat': meta}}
        return action, meta

    def complete(self, original='request-A'):
        action, meta = self.assess()
        self.control.record('actor', action)
        event = dict(kind='own_communication_completed', result='completed', subject='actor',
                     communication_repeat=deepcopy(meta), canonical_request_id=original, t=1)
        self.control.absorb('actor', {'perceptions': [event]}, 1)
        return action, meta

    def render(self, action):
        return describe_candidate_coarse(action, self.actor, object(), [])

    def test_first_null_frozen_identity_and_no_topic_inference(self):
        self.control.absorb('actor', {'perceptions': [dict(kind='speech', subject='actor',
            to='peer', topic='topic', intent='request', request_id='unproven', t=0)]}, 0)
        _, meta = self.assess()
        self.assertIsNone(meta['communication_identity']['original_request_id'])
        identity = decision_identity(meta, 'actor')
        with self.assertRaises(FrozenInstanceError):
            identity.original_request_id = 'unproven'
        self.assertIn('"original_request_id":null', self.render(self.assess()[0]))

    def test_distinct_originals_produce_distinct_descriptions_and_cache_keys(self):
        descriptions = []
        for original in ('request-A', 'request-B'):
            self.setUp()
            self.complete(original)
            action, meta = self.assess(5)
            self.assertEqual(meta['communication_identity']['original_request_id'], original)
            descriptions.append(self.render(action))
        self.assertNotEqual(*descriptions)
        self.assertNotEqual(*map(context_key, descriptions))

    def test_transport_preserves_identity_and_new_request_differs_from_repeat(self):
        first, _ = self.complete()
        contact, a = self.assess(5)
        room, b = self.assess(5, 'room')
        self.assertEqual(a['communication_identity'], b['communication_identity'])
        self.assertEqual(a['key'], b['key'])
        self.assertNotEqual(a['token'], b['token'])
        self.assertNotEqual(self.render(first), self.render(contact))

    def test_selected_copy_is_detached_and_tampering_fails_before_start(self):
        for field, wrong in (('actor_id', 'other'), ('epoch', 'other'), ('version', 'future'),
                             ('request_version', '2'), ('original_request_id', 'other')):
            with self.subTest(field=field):
                self.setUp()
                action, meta = self.assess()
                self.control.record('actor', action)
                self.assertEqual(boundary.selected(self.actor, action, 0), meta)
                meta['communication_identity'][field] = wrong
                with self.assertRaises(ValueError):
                    boundary.selected(self.actor, action, 0)
                with self.assertRaises(ValueError):
                    self.render(action)

    def test_rehashed_forged_original_rejected_by_actor_state(self):
        self.complete()
        action, meta = self.assess(5)
        meta['communication_identity']['original_request_id'] = 'another-request'
        meta['token'] = digest({k: v for k, v in meta.items() if k != 'token'})
        with self.assertRaises(ValueError):
            self.control.record('actor', action)

    def test_start_requires_exact_original_direction_epoch_and_version(self):
        self.complete()
        _, meta = self.assess(5)
        request = dict(id='request-A', **{'from': 'actor'}, to='peer', topic='topic',
                       epoch='decision-fixture', version='unknown')
        self.assertEqual(boundary.request_reference(self.actor, meta, [request]), 'request-A')
        for field, value in (('id', 'other'), ('from', 'peer'), ('to', 'actor'),
                             ('epoch', 'other'), ('version', '2'), ('topic', 'other')):
            with self.subTest(field=field), self.assertRaises(ValueError):
                boundary.request_reference(self.actor, meta, [{**request, field: value}])

    def test_missing_canonical_receipt_blocks_linkage(self):
        self.complete(None)
        _, meta = self.assess(5)
        self.assertFalse(meta['allowed'])
        self.assertEqual(meta['repeat_reason'], 'original_request_not_observed')

    def test_only_actor_observed_evidence(self):
        self.complete()
        _, meta = self.assess(5)
        base = dict(actor_id='actor', issue_id=meta['issue_id'], epoch='decision-fixture',
                    observed_t=3, evidence_id='e1', message_content='new fact')
        for changes in ({'actor_id': 'other'}, {'epoch': 'other'}, {'observed_t': 6}, {'observed_t': True}):
            _, result = self.assess(5, context={'communication_evidence': [{**base, **changes}]})
            self.assertEqual(result['evidence_refs'], [])
        _, update = self.assess(5, context={'communication_evidence': [base]})
        self.assertEqual(update['classification'], 'update')
        self.actor.slot_context = {'communication_evidence': [{**base, 'observed_t': 6}]}
        with self.assertRaises(ValueError):
            boundary.content(self.actor, update)
        self.actor.slot_context = {'communication_evidence': [base]}
        self.assertEqual(boundary.content(self.actor, update)[0]['evidence_id'], 'e1')

    def test_snapshot_roundtrip_and_cross_version_rejection(self):
        self.complete()
        action, _ = self.assess(5)
        self.control.record('actor', action)
        snapshot = json.loads(json.dumps(self.control.export_state()))
        restored = CommunicationRepeat(config())
        restored.restore_state(snapshot)
        self.assertEqual(restored.export_state(), snapshot)
        for version in (VERSION,):
            with self.assertRaises(ValueError):
                CommunicationRepeat(config(version)).restore_state(snapshot)
        damaged = deepcopy(snapshot)
        next(iter(damaged['pending'].values()))['communication_identity']['epoch'] = 'other'
        with self.assertRaises(ValueError):
            restored.restore_state(damaged)
        self.assertEqual(restored.export_state(), snapshot)

    def test_first_completion_restore_rejects_missing_or_replaced_request_id_atomically(self):
        self.complete()
        snapshot = self.control.export_state()
        for replacement in (None, 'forged-request', 'unknown', ''):
            for rehash in (False, True):
                damaged = deepcopy(snapshot)
                entry = next(iter(damaged['sent'].values()))
                if replacement is None:
                    del entry['request_id']
                else:
                    entry['request_id'] = replacement
                    if rehash:
                        entry['completion_sha256'] = completion_digest(entry)
                with self.subTest(replacement=replacement, rehash=rehash):
                    for target in (self.control, CommunicationRepeat(config())):
                        before = target.export_state()
                        with self.assertRaises(ValueError):
                            target.restore_state(damaged)
                        self.assertEqual(target.export_state(), before)

    def test_first_completion_receipt_required_and_bound_to_decision(self):
        self.complete()
        snapshot = self.control.export_state()
        for field, value in (('subject', 'other'), ('t', 9),
                             ('canonical_request_id', 'forged-request'),
                             ('communication_repeat', {})):
            damaged = deepcopy(snapshot)
            entry = next(iter(damaged['sent'].values()))
            entry['completion_receipt'][field] = value
            entry['completion_sha256'] = completion_digest(entry)
            with self.subTest(field=field), self.assertRaises(ValueError):
                CommunicationRepeat(config()).restore_state(damaged)
        for field in ('completion_receipt', 'completion_sha256'):
            damaged = deepcopy(snapshot)
            del next(iter(damaged['sent'].values()))[field]
            with self.assertRaises(ValueError):
                CommunicationRepeat(config()).restore_state(damaged)

    def test_unknown_receipt_roundtrip_remains_blocked_and_cannot_become_new(self):
        self.complete(None)
        snapshot = self.control.export_state()
        restored = CommunicationRepeat(config())
        restored.restore_state(snapshot)
        action = Action('dialogue', ('peer', 'topic', 'request', 'room'))
        result = restored.assess('actor', action, {}, 5)
        self.assertFalse(result['allowed'])
        self.assertEqual(result['repeat_reason'], 'original_request_not_observed')
        for missing in (False, True):
            damaged = deepcopy(snapshot)
            entry = next(iter(damaged['sent'].values()))
            if missing:
                del entry['request_id']
            else:
                entry['request_id'] = 'invented'
                entry['completion_sha256'] = completion_digest(entry)
            with self.assertRaises(ValueError):
                restored.restore_state(damaged)

    def test_in_memory_missing_id_fails_closed_before_assessment(self):
        self.complete()
        del next(iter(self.control.sent.values()))['request_id']
        with self.assertRaises(ValueError):
            self.assess(5)

    def test_completion_cannot_replace_original(self):
        self.complete()
        action, meta = self.assess(5)
        self.control.record('actor', action)
        event = dict(kind='own_communication_completed', result='completed', subject='actor',
                     communication_repeat=deepcopy(meta), canonical_request_id='other', t=6)
        before = deepcopy(self.control.sent)
        with self.assertRaises(ValueError):
            self.control.absorb('actor', {'perceptions': [event]}, 6)
        self.assertEqual(self.control.sent, before)

    def test_v1_snapshot_cannot_be_loaded_as_v2(self):
        legacy = CommunicationRepeat(config(VERSION)).export_state()
        with self.assertRaises(ValueError):
            self.control.restore_state(legacy)

    def test_legacy_description_unchanged(self):
        self.control = CommunicationRepeat(config(VERSION))
        action, meta = self.assess()
        self.assertNotIn('communication_identity', meta)
        bare = Action(action.verb, action.args)
        self.assertEqual(self.render(action), self.render(bare))
        self.assertEqual(context_key(self.render(action)), context_key(self.render(bare)))

    def test_runtime_first_and_repeat_completion_keep_one_original(self):
        with tempfile.TemporaryDirectory() as temp:
            world, people = load()
            actor_id = world.slot_contract['actions']['sound_check']['actor']
            cfg = dict(version=DECISION_VERSION, epoch='native', completion_mode='explicit',
                       actor_tendencies={actor_id: 'reminder'})
            policies = build_policies(world.slot_contract, communication_config=cfg)
            sim = Simulation(0, world, people, Path(temp), policies=policies)
            rt = sim.slot_runtime
            actor, peer = people[actor_id], people[rt.lead]
            rt.writer = SimpleNamespace(write=lambda row: None)
            original = None
            for tick, channel in ((0, 'contact'), (5, 'room')):
                # Room transport requires co-location, already true in this fixture.
                if channel == 'room':
                    peer.zone = actor.zone
                sim.turn, sim.slot = tick + 1, world.slots[tick]
                actor.slot_context = {'perceptions': []}
                action = Action('dialogue', (peer.id, '出演', 'request', channel))
                meta = actor.policy.communication_repeat.assess(actor.id, action, {}, tick)
                self.assertEqual(meta['communication_identity']['original_request_id'], original)
                action.meta['decision_basis'] = {'selected': {'communication_repeat': meta}}
                actor.policy.record(actor, action)
                # Tamper after publication/record: actual runtime must reject
                # before allocating a pending action or changing requests.
                for field, bad in (('actor_id', 'other'), ('epoch', 'other'),
                                   ('version', 'future'), ('request_version', '2')):
                    forged = deepcopy(action)
                    forged.meta['decision_basis']['selected']['communication_repeat']['communication_identity'][field] = bad
                    before_requests = deepcopy(rt.state['requests'])
                    with self.assertRaises(ValueError):
                        rt.start(actor, forged, 1)
                    self.assertEqual(rt.state['requests'], before_requests)
                    self.assertFalse(rt.pending)
                rt.start(actor, action, 1)
                pending = rt.lifecycle.interrupt(actor.id)
                self.assertEqual(pending['action'].meta['original_request_id'], original)
                sim.turn, sim.slot = tick + 2, world.slots[tick + 1]
                rt.complete(pending)
                actor.slot_context = {'perceptions': deepcopy(rt.perceptions[actor.id])}
                boundary.flush(actor, tick + 1)
                rt.perceptions[actor.id].clear()
                original = rt.state['requests'][0]['id']
                self.assertEqual(len(rt.state['requests']), 1)
                self.assertEqual(actor.policy.communication_repeat.sent[meta['key']]['request_id'], original)


if __name__ == '__main__':
    unittest.main()
