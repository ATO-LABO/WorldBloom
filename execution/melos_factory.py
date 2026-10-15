"""Verified source factory for the approved Melos baseline, not an API route.

Original external source bytes remain unchanged. Two AST-only packaging edits
remove its top-level sys.path insertion and inject the explicit input parent as
ROOT. All scenario/action code is compiled unchanged. No external trial runner
or main entrypoint is called. Inputs must retain the approved melos-input name.
"""
import ast
from copy import deepcopy
import hashlib
import inspect
from pathlib import Path
import sys
import threading
import types

from engine.execution_plan import digest
from execution.melos_session import MelosSession
from gapengine.genome import Genome
from gapengine.melos_choice_provider import MelosChoiceProvider

TRANSFORM_VERSION = 'approved_melos_root_binding_v1'
# Approved received sources, frozen 2026-10-14. Caller catalogs cannot expand
# this allowlist; a new source revision requires explicit reviewed code change.
_APPROVED_SOURCES = {
    'melos_runner.py': '1a2d150c5740a8898ee57dc05794b02c046feec8c4a2aba17f9aeb44f202fd12',
    'ninegene_melos.py': 'ad6733142979e48655f7dcaa1d389a7d3a2c9737ea1109f663ecdd6b14a9d324',
}
_APPROVED_INPUTS = {
    'contract.yaml': '50afcf07f3f33131e93c4dd8fc912c00a92187b6f10ca0831a46188f1a754664',
    'scenario.yaml': 'a8e896231121994410f4a321673e6ad79262d9a8fc1a5cf11b409f89f97a59fc',
    'subjects/Groom.yaml': 'e0cff366a0cd6c4e26c596523fc2510b3736239c6f1a0aa91ce81fc31eb16677',
    'subjects/King.yaml': 'a910884e2f0ca15fd306207b0d30e38f9156eb3a880b203d901c397ad9fb1a74',
    'subjects/Melos.yaml': '7aeedd5282098b1f9a5aa5a5aa748ad19c075b19c53b61f3fbcdbc15059f303d',
    'subjects/Philostratus.yaml': 'bbc6158755ae088e72adbcf1eea5d9e91c30cc3b01ead8b5068f04499ea9cfae',
    'subjects/Selinuntius.yaml': '246482229a47a3de3dce09c415b0941980274d9c0594a858262f45ddeb5be343',
    'subjects/Sister.yaml': 'b0d4bf1dc72b8336c1e2698793c23dbb55522dea3d58b9238789d1ade832f190',
    'world.yaml': '8055f1f4e12bf9606586936dd013a3b5a630c413914cae0057ac88ed30b97baa',
}
_LOCK = threading.RLock()


def approved_catalogs():
    return {'sources': deepcopy(_APPROVED_SOURCES), 'inputs': deepcopy(_APPROVED_INPUTS)}


def source_class_identity(cls):
    """Logical key only for the exact byte-approved external Trial class.

    The actual module remains isolated for execution/inspection. Actual source
    bytes are still hashed independently by each checkpoint source closure.
    """
    path = Path(inspect.getsourcefile(cls))
    if (cls.__qualname__ == 'Trial' and path.name == 'melos_runner.py' and
            hashlib.sha256(path.read_bytes()).hexdigest() == _APPROVED_SOURCES['melos_runner.py']):
        return 'approved_source:melos_runner.py:Trial'
    return cls.__module__+'.'+cls.__qualname__


def _verified(root, expected, suffix):
    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        raise ValueError('Explicit ordinary directory required')
    actual = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Profile links are unsupported')
        if path.is_file() and path.suffix == suffix:
            actual[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError('Actual profile catalog differs from approved files')


def _transform(source, filename):
    tree = ast.parse(source, filename=filename)
    root_shape = ast.dump(ast.parse('ROOT=Path(__file__).resolve().parent').body[0], include_attributes=False)
    path_shape = ast.dump(ast.parse("sys.path.insert(0,str(ROOT/'worldbloom'))").body[0], include_attributes=False)
    roots = [i for i, node in enumerate(tree.body) if ast.dump(node, include_attributes=False) == root_shape]
    paths = [i for i, node in enumerate(tree.body) if ast.dump(node, include_attributes=False) == path_shape]
    if len(roots) != 1 or len(paths) != 1 or paths[0] != roots[0]+1:
        raise ValueError('Approved source packaging shape changed')
    replacement = ast.parse('ROOT=_APPROVED_INPUT_PARENT').body[0]
    tree.body[roots[0]] = ast.copy_location(replacement, tree.body[roots[0]])
    del tree.body[paths[0]]
    return ast.fix_missing_locations(tree)


class MelosSessionFactory:
    """Own an isolated ROOT namespace and create only approved baseline slots.

    The explicit catalogs must equal the built-in approved revision as well as
    actual disk bytes. The namespace is path-bound: freeze/materialize the
    source and input roots before constructing a durable ExecutionPlan.
    """
    def __init__(self, source_dir, input_dir, *, approved_source_hashes,
                 approved_input_hashes, genome):
        if Path(source_dir).is_symlink() or Path(input_dir).is_symlink():
            raise ValueError('Profile root links are unsupported')
        self.source_dir = Path(source_dir).resolve()
        self.input_dir = Path(input_dir).resolve()
        if self.input_dir.name != 'melos-input':
            raise ValueError('Approved melos-input layout required')
        if approved_source_hashes != _APPROVED_SOURCES or approved_input_hashes != _APPROVED_INPUTS:
            raise ValueError('Caller catalogs do not authorize a different profile revision')
        if type(genome) is not Genome:
            raise ValueError('Explicit approved nine-gene Genome required')
        # Validate immutable provider configuration now; no choices are made.
        MelosChoiceProvider(genome, 0)
        self._genome = deepcopy(genome.to_dict())
        self._verify()
        path = self.source_dir/'melos_runner.py'
        source = path.read_bytes()
        if hashlib.sha256(source).hexdigest() != _APPROVED_SOURCES[path.name]:
            raise ValueError('Source changed during factory construction')
        name = '_worldbloom_melos_'+digest({'source': str(path), 'input': str(self.input_dir),
                                          'transform': TRANSFORM_VERSION})
        with _LOCK:
            module = types.ModuleType(name)
            module.__file__ = str(path)
            module.__dict__['_APPROVED_INPUT_PARENT'] = self.input_dir.parent
            previous = sys.modules.get(name)
            sys.modules[name] = module
            try:
                exec(compile(_transform(source, str(path)), str(path), 'exec'), module.__dict__)
            except BaseException:
                if previous is None: sys.modules.pop(name, None)
                else: sys.modules[name] = previous
                raise
            self._module = module
        if module.ROOT != self.input_dir.parent or Path(inspect.getsourcefile(module.Trial)) != path:
            raise ValueError('Isolated source/root identity unavailable')

    def _verify(self):
        _verified(self.source_dir, _APPROVED_SOURCES, '.py')
        _verified(self.input_dir, _APPROVED_INPUTS, '.yaml')

    @property
    def identity(self):
        return {'profile': 'melos_approved_ninegene_v1', 'transform': TRANSFORM_VERSION,
                'transform_source_hash': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'sources': deepcopy(_APPROVED_SOURCES), 'inputs': deepcopy(_APPROVED_INPUTS),
                'genome': deepcopy(self._genome), 'product_active': True}

    def create(self, seed):
        if type(seed) is not int or not 0 <= seed <= 2147483647:
            raise ValueError('Bounded nonnegative integer seed required')
        self._verify()
        if self._module.ROOT != self.input_dir.parent:
            raise ValueError('Factory input namespace changed')
        genome = Genome.from_dict(deepcopy(self._genome))
        trial = self._module.Trial(genome, seed)
        return MelosSession(trial, input_hashes=deepcopy(_APPROVED_INPUTS),
                            source_hashes=deepcopy(_APPROVED_SOURCES),
                            choice_provider=MelosChoiceProvider(genome, seed))
