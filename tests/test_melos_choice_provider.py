"""Pure opt-in provider tests; no runtime, network, model, or activation."""
import ast
from collections import Counter
from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
import inspect
import json
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch

from engine.decision_provider import provider_snapshot, staged_provider_restore
from engine.provider_boundary import DecisionOffer, _json, encode_history, encode_rng, decode_history
from gapengine.genome import Genome
from gapengine import melos_choice_provider as module
from gapengine.melos_choice_provider import ACTORS, MelosChoiceProvider


def genome():
    return Genome({'I': .8, 'II': .4, 'III': .7, 'IV': .3, 'V': .9, 'VI': .2}, .8, -.2, .7)


def offer(actor='Melos', ordinal=0, now=10, singleton=False):
    view = dict(actor=actor, now=now, zone='village', stamina=90., goals={'honor': 2, 'self': 3},
                initial={'river_seen': {'water': 'flood'}}, own={},
                evidence=[{'at': 1, 'verb': 'river_observed'}, {'at': 2, 'verb': 'bandit_demand'}],
                public_map={'village': {}, 'court': {}, 'west_bank': {}},
                decision_ordinal=ordinal, own_vitality='alive')
    ids = ['wedding', 'confess', 'disclose', 'negotiate_terms', 'swim', 'fight',
           'retreat', 'retreat_bandits', 'rest_arrival', 'sleep_dawn', 'drink', 'leave', 'wait_deadline']
    candidates = [dict(id=x, label=x, kind='wait', minutes=2,
                       features={'honor': (i % 3) - 1, 'self': i % 2})
                  for i, x in enumerate(ids)]
    candidates[11].update(kind='move', to='court')
    return DecisionOffer.create(view, candidates[:1] if singleton else candidates)


def choose(provider, item):
    v = item.read()['view']
    return provider.choose(item, actor=v['actor'], now=v['now'], ordinal=v['decision_ordinal'])


def digest(value):
    return hashlib.sha256(_json(value)).hexdigest()


class MelosChoiceProviderTests(unittest.TestCase):
    def test_projection_is_exact_approved_ast(self):
        # ast.dump's representation changes across supported Python versions.
        # Pin the received source bytes, then compare both ASTs with the same
        # interpreter. Read the checked-in reference without importing it.
        reference_path = (Path(__file__).resolve().parents[1] /
                          'execution/approved_melos_sources/ninegene_melos.py')
        reference_bytes = reference_path.read_bytes()
        self.assertEqual(hashlib.sha256(reference_bytes).hexdigest(), APPROVED_SOURCE)
        reference = ast.parse(reference_bytes.decode('utf-8'))
        for name in APPROVED_PROJECTIONS:
            definitions = [node for node in reference.body
                           if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name == name]
            self.assertEqual(len(definitions), 1, name)
            expected = ast.Module(body=definitions, type_ignores=[])
            source = inspect.getsource(getattr(module, name))
            self.assertEqual(ast.dump(ast.parse(source)), ast.dump(expected), name)
        self.assertEqual(digest(json.loads(module._CFG_JSON)), APPROVED_CFG)

    def test_same_input_history_draw_and_calculation_as_approved_algorithm(self):
        # First verify all 24 choices against unchanged byte-approved original
        # NineTrial.choose under this interpreter, including every float field.
        from melos_reference_sequence import reference_sequence
        reference = reference_sequence()
        provider = MelosChoiceProvider(genome(), 19)
        rows = []
        for ordinal in range(4):
            for index, actor in enumerate(ACTORS):
                receipt = choose(provider, offer(actor, ordinal, 10 + 6 * ordinal + index,
                                                 singleton=ordinal == 1))
                rows.append({k: receipt[k] for k in ('selected', 'rng_draw', 'calculation')})
        state = provider.export_state()
        self.assertEqual({'rows': rows, 'history': state['history'], 'rng': state['rng']},
                         reference['reference'])
        self.assertEqual(digest({'rows': rows, 'history': state['history'], 'rng': state['rng']}), APPROVED_SEQUENCE)
        self.assertEqual(state['counts'], dict.fromkeys(ACTORS, 4))
        self.assertEqual(sum(row['count'] for row in state['history']), 4)

    def test_determinism_and_checkpoint_real_history(self):
        a = MelosChoiceProvider(genome(), 17)
        b = MelosChoiceProvider(genome(), 17)
        for i in range(5):
            self.assertEqual(choose(a, offer(ordinal=i, now=10+i)), choose(b, offer(ordinal=i, now=10+i)))
        snapshot = provider_snapshot(a)
        restored = staged_provider_restore(MelosChoiceProvider(genome(), 17), snapshot)
        history = decode_history(restored.export_state()['history'])
        self.assertEqual(type(history), Counter)
        self.assertTrue(all(type(key[0]) is tuple and type(key[1]) is tuple for key in history))
        self.assertEqual(provider_snapshot(restored), snapshot)
        self.assertEqual(choose(a, offer(ordinal=5, now=20)), choose(restored, offer(ordinal=5, now=20)))

    def test_singleton_consumes_one_draw_shared_by_all_actors(self):
        p = MelosChoiceProvider(genome(), 23)
        rng = random.Random(23)
        for actor in ACTORS:
            receipt = choose(p, offer(actor, now=10, singleton=True))
            self.assertEqual(receipt['rng_draw'], rng.random())
        self.assertEqual(p.export_state()['rng'], encode_rng(rng))

    def test_duplicate_offer_including_after_other_actor_never_resamples(self):
        p = MelosChoiceProvider(genome(), 7)
        item = offer()
        receipt = choose(p, item)
        choose(p, offer('King', now=11))
        before = p.export_state()
        # A downstream admission rejection has no rollback/retry entry point.
        self.assertEqual(choose(p, item), receipt)
        self.assertEqual(p.export_state(), before)
        changed = item.read(); changed['offered'][0]['minutes'] += 1
        with self.assertRaises(ValueError):
            choose(p, DecisionOffer.create(changed['view'], changed['offered']))
        self.assertEqual(p.export_state(), before)

    def test_detached_inputs_config_receipts_and_snapshots(self):
        g = genome(); p = MelosChoiceProvider(g, 0)
        original_config = p.checkpoint_identity()
        g.category_weight['I'] = .1
        self.assertEqual(p.checkpoint_identity(), original_config)
        with self.assertRaises(FrozenInstanceError): p.config.seed = 4
        with self.assertRaises(AttributeError): p.config = None
        item = offer(); receipt = choose(p, item); original = deepcopy(receipt)
        receipt['candidate']['features']['honor'] = 100
        receipt['calculation'][0]['candidate'] = 'forged'
        state = p.export_state(); state['records'].clear(); state['counts']['Melos'] = 20
        self.assertEqual(choose(p, item), original)
        self.assertEqual(original['candidate'], next(c for c in item.read()['offered'] if c['id'] == original['selected']))

    def test_wrong_binding_unknown_actor_and_nonmonotonic_time_are_atomic(self):
        p = MelosChoiceProvider(genome())
        item = offer(); before = p.export_state()
        for field, value in [('actor', 'King'), ('now', 11), ('now', True), ('ordinal', 1), ('ordinal', False)]:
            kwargs = dict(actor='Melos', now=10, ordinal=0); kwargs[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                p.choose(item, **kwargs)
            self.assertEqual(p.export_state(), before)
        with self.assertRaises(ValueError): choose(p, offer('Unknown'))
        with self.assertRaises(ValueError): choose(p, offer(ordinal=1))
        self.assertEqual(p.export_state(), before)
        choose(p, item); before = p.export_state()
        with self.assertRaises(ValueError): choose(p, offer('King', now=9))
        self.assertEqual(p.export_state(), before)

    def test_32_decision_limit(self):
        p = MelosChoiceProvider(genome())
        for i in range(32): choose(p, offer(ordinal=i, now=10+i, singleton=True))
        before = p.export_state()
        with self.assertRaises(ValueError): offer(ordinal=32, now=50)
        with self.assertRaises(ValueError): choose(p, offer(ordinal=31, now=50))
        self.assertEqual(p.export_state(), before)

    def test_bad_projection_does_not_mutate_state(self):
        p = MelosChoiceProvider(genome())
        envelope = offer().read(); envelope['view']['evidence'] = []
        before = p.export_state()
        with self.assertRaises(ValueError): choose(p, DecisionOffer.create(envelope['view'], envelope['offered']))
        self.assertEqual(p.export_state(), before)

    def test_fixed_other_actors_are_genome_independent(self):
        a = MelosChoiceProvider(genome(), 3)
        b = MelosChoiceProvider(Genome.neutral(), 3)
        for actor in ACTORS:
            if actor != 'Melos': self.assertEqual(choose(a, offer(actor)), choose(b, offer(actor)))
        self.assertEqual(a.export_state()['history'], [])

    def test_invalid_genomes_rejected(self):
        for edit in [lambda g: g.category_weight.update(I=float('nan')),
                     lambda g: g.category_weight.update(I=True),
                     lambda g: g.category_weight.update(I=1.1),
                     lambda g: g.category_weight.update(VII=.5),
                     lambda g: g.rule_bits.update(rule=True)]:
            g = genome(); edit(g)
            with self.assertRaises(ValueError): MelosChoiceProvider(g)
        with self.assertRaises(ValueError): MelosChoiceProvider(Genome(**{**genome().__dict__, 'plasticity': .1}))
        with self.assertRaises(ValueError): MelosChoiceProvider(genome(), True)

    def test_changed_config_source_or_snapshot_hash_rejected(self):
        p = MelosChoiceProvider(genome(), 1); choose(p, offer()); snapshot = provider_snapshot(p)
        for other in (MelosChoiceProvider(genome(), 2), MelosChoiceProvider(Genome.neutral(), 1)):
            before = other.export_state()
            with self.assertRaises(ValueError): staged_provider_restore(other, snapshot)
            with self.assertRaises(ValueError): other.restore_state(snapshot['state'])
            self.assertEqual(other.export_state(), before)
        for field in ('source_hash', 'runtime_source_hash', 'config_hash', 'rng_owner'):
            changed = deepcopy(snapshot); changed['identity'][field] = 'changed'
            with self.assertRaises(ValueError): staged_provider_restore(p, changed)
        changed = deepcopy(snapshot); changed['state_hash'] = '0' * 64
        with self.assertRaises(ValueError): staged_provider_restore(p, changed)
        with patch.object(p, 'checkpoint_sources', return_value={}):
            with self.assertRaises(ValueError): p.restore_state(snapshot['state'])

    def test_malformed_state_rejects_without_mutation(self):
        p = MelosChoiceProvider(genome(), 8); choose(p, offer())
        good = p.export_state()
        edits = [lambda s: s.update(extra=0), lambda s: s.update(counts=[]),
                 lambda s: s['counts'].update(Melos=True), lambda s: s['counts'].update(King=1),
                 lambda s: s.update(last_minute=False), lambda s: s['history'][0].update(count=2),
                 lambda s: s['history'][0].update(count=True),
                 lambda s: s['rng']['internal'].__setitem__(0, 123),
                 lambda s: s['rng'].update(gaussian=1.),
                 lambda s: s['records'].append(deepcopy(s['records'][0])),
                 lambda s: s['records'][0]['receipt'].update(selected='foreign'),
                 lambda s: s['records'][0]['receipt'].update(rng_draw=0.),
                 lambda s: s['records'][0]['receipt']['candidate'].update(minutes=999),
                 lambda s: s['records'][0]['offer']['view'].update(actor='King'),
                 lambda s: s['identity'].update(config_hash='0'*64)]
        for i, edit in enumerate(edits):
            bad = deepcopy(good); edit(bad)
            with self.subTest(edit=i), self.assertRaises(ValueError): p.restore_state(bad)
            self.assertEqual(p.export_state(), good)


# Replaced at authoring time from the received approved source, not the provider.
APPROVED_SOURCE = 'ad6733142979e48655f7dcaa1d389a7d3a2c9737ea1109f663ecdd6b14a9d324'
APPROVED_PROJECTIONS = ('ObservablePolicyWorld', 'observed', 'policy_action', 'reweight_observable')
APPROVED_CFG = '9c6c881ace64a43c7aa29f9e95122b0802ccef7a265db6c6bb8115ab02b38969'
# Verified against original 24-choice rows on Windows and Ubuntu, both versions.
# Python 3.12+ sum() changes 25 normalized_weight last bits; selection, raw
# weights, RNG and history remain identical. Keep exact goldens, no tolerance.
APPROVED_SEQUENCE = ('7fb33eb112d2a5f37db520adade6831a4f4d2ccd51c5de36e16ca668bcc209d4'
                     if sys.version_info < (3, 12) else
                     '6ba6fd25fa55d4d7f5502678323884fb197ab4b8e46c1df90657a16e568b0dbe')

if __name__ == '__main__': unittest.main()
