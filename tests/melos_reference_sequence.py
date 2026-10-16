"""Compare 24 synthetic choices with byte-pinned original NineTrial.choose.

Only the unchanged projection definitions, CFG and NineTrial class are compiled
from the approved AST. The scenario constructor/run entrypoints are never used.
"""
import ast
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import random
from types import SimpleNamespace

from engine.actions import Action
from engine.provider_boundary import encode_history, encode_rng, _json
from gapengine.genome import Genome
from gapengine.policy import Policy

SOURCE_SHA256 = 'ad6733142979e48655f7dcaa1d389a7d3a2c9737ea1109f663ecdd6b14a9d324'


def reference_sequence():
    # Import fixture constructors lazily; this module is not a test module.
    from test_melos_choice_provider import ACTORS, MelosChoiceProvider, choose, genome, offer
    path = Path(__file__).resolve().parents[1] / 'execution/approved_melos_sources/ninegene_melos.py'
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SOURCE_SHA256
    tree = ast.parse(raw, filename=str(path))
    wanted = {'ObservablePolicyWorld', 'observed', 'policy_action', 'reweight_observable', 'NineTrial'}
    definitions = [node for node in tree.body if
                   isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in wanted
                   or isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'CFG' for t in node.targets)]
    assert len(definitions) == 6
    namespace = dict(Trial=object, SimpleNamespace=SimpleNamespace, Counter=Counter,
                     deepcopy=deepcopy, random=random, math=math, Action=Action,
                     Genome=Genome, Policy=Policy,
                     digest=lambda value: hashlib.sha256(_json(value)).hexdigest())
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(path), 'exec'), namespace)
    original = namespace['NineTrial'].__new__(namespace['NineTrial'])
    original.rng = random.Random(19)
    original.policy = Policy(genome(), None, cfg=deepcopy(namespace['CFG']), eps=.02, lam=.7)
    original.policy_history = Counter()
    original.count = dict.fromkeys(ACTORS, 0)
    original.decisions = []
    original.rt = SimpleNamespace(now=10, people={actor: SimpleNamespace(vitality='alive') for actor in ACTORS})
    reference_rows, provider_rows = [], []

    def event(kind, actor, observers, **fields):
        row = {key: fields[key] for key in ('selected', 'rng_draw', 'calculation')}
        reference_rows.append(row)
        return {'id': 'reference-' + str(len(reference_rows))}

    original.event = event
    provider = MelosChoiceProvider(genome(), 19)
    comparisons = []
    for ordinal in range(4):
        for index, actor in enumerate(ACTORS):
            item = offer(actor, ordinal, 10 + 6 * ordinal + index, singleton=ordinal == 1)
            envelope = item.read()
            original.view = lambda who: deepcopy(envelope['view'])
            original.rt.now = envelope['view']['now']
            selected = original.choose(actor, 'synthetic-reference', envelope['offered'])
            receipt = choose(provider, item)
            provider_rows.append({key: receipt[key] for key in ('selected', 'rng_draw', 'calculation')})
            reference_row = reference_rows[-1]
            assert selected['id'] == receipt['selected']
            # Compare every field and binary float representation, no tolerance.
            assert _json(reference_row) == _json(provider_rows[-1]), (actor, ordinal)
            state = provider.export_state()
            assert encode_rng(original.rng) == state['rng'], (actor, ordinal, 'rng')
            assert encode_history(original.policy_history) == state['history'], (actor, ordinal, 'history')
            assert original.count == state['counts'], (actor, ordinal, 'counts')
            comparisons.append({'actor': actor, 'ordinal': ordinal, 'singleton': ordinal == 1,
                                'all_calculation_fields_equal': True, 'rng_equal': True,
                                'history_equal': True, 'counts_equal': True})
    reference = {'rows': reference_rows, 'history': encode_history(original.policy_history),
                 'rng': encode_rng(original.rng)}
    actual = {'rows': provider_rows, 'history': state['history'], 'rng': state['rng']}
    assert _json(reference) == _json(actual)
    return {'source_sha256': SOURCE_SHA256, 'choices': comparisons,
            'reference': reference, 'provider': actual,
            'digest': hashlib.sha256(_json(reference)).hexdigest()}
