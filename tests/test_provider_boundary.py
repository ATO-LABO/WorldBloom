"""Pure contract checks. No world execution, model, network, or provider activation."""
from collections import Counter
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
import math
import random
import unittest
from engine.provider_boundary import DecisionOffer, encode_history, decode_history, encode_rng, decode_rng


def fixture():
    view = dict(actor='Melos', now=10, zone='village', stamina=90.0,
                goals={'honor': 2}, initial={}, own={}, evidence=[{'at': 9, 'verb': 'observed'}],
                public_map={}, decision_ordinal=0, own_vitality='alive')
    offered = [dict(id='leave', label='leave', kind='move', minutes=30,
                    features={'honor': 1}, to='court', cost=3)]
    return view, offered


class ProviderBoundaryTests(unittest.TestCase):
    def test_original_candidate_and_nested_values_are_detached(self):
        view, offered = fixture(); offer = DecisionOffer.create(view, offered)
        original = deepcopy(offered[0]); view['goals']['honor'] = 99; offered[0]['id'] = 'changed'
        selected = offer.selected('leave', offer_hash=offer.identity, actor='Melos', now=10, ordinal=0)
        self.assertEqual(selected, original)
        selected['features']['honor'] = 100
        self.assertEqual(offer.read()['offered'][0], original)
        with self.assertRaises(FrozenInstanceError): offer.payload = b'{}'

    def test_selection_rejects_stale_owner_time_ordinal_and_foreign_id(self):
        offer = DecisionOffer.create(*fixture())
        kwargs = dict(offer_hash=offer.identity, actor='Melos', now=10, ordinal=0)
        for key, value in [('offer_hash', '0'*64), ('actor', 'King'), ('now', 11), ('ordinal', 1), ('ordinal', False)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                offer.selected('leave', **{**kwargs, key: value})
        with self.assertRaises(ValueError): offer.selected('other', **kwargs)

    def test_invalid_view_or_candidate_rejected(self):
        edits = [lambda v,c: v.update(world={}), lambda v,c: v.update(decision_ordinal=32),
                 lambda v,c: v.update(now=True), lambda v,c: v['evidence'][0].update(at=11),
                 lambda v,c: c.append(deepcopy(c[0])), lambda v,c: c[0].update(minutes=False),
                 lambda v,c: c[0].update(hidden_ledger={}), lambda v,c: c[0].update(cost=float('nan')),
                 lambda v,c: c[0]['features'].update(honor=True), lambda v,c: v.update(own=object()), lambda v,c: c[0].pop('to')]
        for i, edit in enumerate(edits):
            view, candidates = fixture(); edit(view, candidates)
            with self.subTest(edit=i), self.assertRaises(ValueError): DecisionOffer.create(view,candidates)

    def test_payload_rejects_noncanonical_and_unknown_schema(self):
        for data in (b'null', b'[]', b'{}', b'{"schema":"unknown","view":{},"offered":[]}', b'NaN'):
            with self.subTest(data=data), self.assertRaises(ValueError): DecisionOffer(data)
        offer = DecisionOffer.create(*fixture())
        with self.assertRaises(ValueError): DecisionOffer(b' '+offer.payload)

    def test_identity_covers_order_and_exact_candidate_content(self):
        view,candidates=fixture(); a=DecisionOffer.create(view,candidates)
        candidates[0]['minutes']+=1; b=DecisionOffer.create(view,candidates)
        self.assertNotEqual(a.identity,b.identity)
        self.assertEqual(a.identity,DecisionOffer.create(*fixture()).identity)
        second = deepcopy(candidates[0]); second['id'] = 'leave-slow'; second['minutes'] = 60
        candidates.append(second)
        self.assertNotEqual(DecisionOffer.create(view,candidates).identity,
                            DecisionOffer.create(view,list(reversed(candidates))).identity)

    def test_history_exact_roundtrip_with_zero_and_nested_tuple_keys(self):
        context=((),False,'none','alive','neutral',False)
        history=Counter({(context,('-','move','none')):0,(context,('V','ritual','none')):3})
        rows=json.loads(json.dumps(encode_history(history)))
        restored=decode_history(rows)
        self.assertEqual(dict(restored),dict(history)); self.assertEqual(encode_history(restored),rows)
        with self.assertRaises(ValueError): decode_history(list(reversed(rows)))
        rows[0]['count']=9
        self.assertEqual(dict(restored),dict(history))

    def test_history_rejects_duplicates_bool_and_malformed_keys(self):
        context=((),False,'none','alive','neutral',False)
        good=encode_history(Counter({(context,('V','ritual','none')):3}))
        bad_count=deepcopy(good);bad_count[0]['count']=True
        bad_context=deepcopy(good);bad_context[0]['key'][0][1]=0
        bad_action=deepcopy(good);bad_action[0]['key'][1][0]=None
        for rows in [good+good,bad_count,bad_context,bad_action,[{'key':['V','ritual',None],'count':3}]]:
            with self.subTest(rows=rows),self.assertRaises(ValueError): decode_history(rows)

    def test_history_roundtrips_real_policy_record(self):
        from types import SimpleNamespace
        from engine.actions import Action
        from gapengine.genome import Genome
        from gapengine.policy import Policy
        policy=Policy(Genome.neutral(),None)
        subject=SimpleNamespace(decision_history=Counter())
        action=Action('move',('court',),meta={'classification':{'category':None,'target_role':'none'},
                     'policy':{'ctx':[[],False,'none','alive','neutral',False]}})
        policy.record(subject,action)
        restored=decode_history(json.loads(json.dumps(encode_history(subject.decision_history))))
        self.assertEqual(dict(restored),dict(subject.decision_history))
        self.assertEqual(policy._history_table(SimpleNamespace(decision_history=restored)).counts,
                         policy._history_table(subject).counts)

    def test_rng_roundtrip_preserves_next_draws_without_consumption(self):
        rng=random.Random(19);rng.gauss(0,1)
        before=rng.getstate(); value=json.loads(json.dumps(encode_rng(rng)))
        restored=decode_rng(value)
        self.assertEqual(rng.getstate(),before);self.assertEqual(restored.getstate(),before)
        self.assertEqual([rng.random() for _ in range(12)],[restored.random() for _ in range(12)])

    def test_rng_rejects_malformed_state(self):
        good=encode_rng(random.Random(2))
        edits=[lambda x:x.update(version=True),lambda x:x.update(schema='unknown'),
               lambda x:x['internal'].__setitem__(0,False),lambda x:x['internal'].__setitem__(-1,625),
               lambda x:x.update(gaussian=float('inf')),lambda x:x.update(extra=1)]
        for edit in edits:
            value=deepcopy(good);edit(value)
            with self.assertRaises(ValueError):decode_rng(value)

if __name__=='__main__':unittest.main()
