"""Verified source factory for the approved Melos baseline, not an API route.

Original external source bytes remain unchanged. Two AST-only packaging edits
remove its top-level sys.path insertion and inject the explicit input parent as
ROOT. The isolated runner gets a hash-bound world loader. All scenario/action
code is compiled unchanged. No external trial runner
or main entrypoint is called. The default path requires melos-input; the private
capture path uses that name only as a lexical selector, never a filesystem mirror.
"""
import ast
from copy import deepcopy
import hashlib
import inspect
from pathlib import Path
import sys
import threading
import types

import yaml

from engine.execution_plan import digest
from engine.world import World
from execution.melos_session import MelosSession
from gapengine.genome import Genome
from gapengine.melos_choice_provider import MelosChoiceProvider

TRANSFORM_VERSION = 'approved_melos_root_binding_v2'
CAPTURE_TRANSFORM_VERSION = 'approved_melos_capture_binding_v1'
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


def load_approved_world(path):
    """Load only the exact frozen Melos world; regular saved-world gates still apply."""
    source = Path(path)
    if source.name != "world.yaml" or source.is_symlink():
        raise ValueError("Approved world.yaml required")
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _APPROVED_INPUTS["world.yaml"]:
        raise ValueError("World differs from the approved Melos world")
    return World(yaml.safe_load(raw), source)


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
    actual disk bytes on the default path. The private calendar_capture path
    instead borrows a caller-owned live capture and never revisits input paths.
    Its logical melos-input selector may differ from the diagnostic folder name.
    Installed executable sources remain path-verified on every create.
    """
    def __init__(self, source_dir, input_dir, *, approved_source_hashes,
                 approved_input_hashes, genome, calendar_capture=None):
        self._calendar_capture = calendar_capture
        if Path(source_dir).is_symlink() or (calendar_capture is None and Path(input_dir).is_symlink()):
            raise ValueError('Profile root links are unsupported')
        self.source_dir = Path(source_dir).resolve()
        if calendar_capture is None:
            self.input_dir = Path(input_dir).resolve()
            if self.input_dir.name != 'melos-input':
                raise ValueError('Approved melos-input layout required')
        else:
            # Display paths are labels only. Do not resolve, stat or open them.
            self.input_dir = Path(input_dir)
            if self.input_dir != calendar_capture.display_source.parent:
                raise ValueError('Input selector differs from captured project')
        self._logical_root = self.input_dir.parent
        self._transform_version = (TRANSFORM_VERSION if calendar_capture is None
                                   else CAPTURE_TRANSFORM_VERSION)
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
                                          'transform': self._transform_version})
        with _LOCK:
            module = types.ModuleType(name)
            module.__file__ = str(path)
            module.__dict__['_APPROVED_INPUT_PARENT'] = self._logical_root
            previous = sys.modules.get(name)
            sys.modules[name] = module
            try:
                exec(compile(_transform(source, str(path)), str(path), 'exec'), module.__dict__)
                if calendar_capture is None:
                    approved_path = self.input_dir / "world.yaml"
                    def from_yaml(world_path):
                        if Path(world_path) != approved_path:
                            raise ValueError("World is outside the approved Melos input")
                        return load_approved_world(approved_path)
                    module.World = types.SimpleNamespace(from_yaml=from_yaml)
                else:
                    self._bind_capture(module)
            except BaseException:
                if previous is None: sys.modules.pop(name, None)
                else: sys.modules[name] = previous
                raise
            self._module = module
        if module.ROOT != self._logical_root or Path(inspect.getsourcefile(module.Trial)) != path:
            raise ValueError('Isolated source/root identity unavailable')

    def _verify(self):
        _verified(self.source_dir, _APPROVED_SOURCES, '.py')
        if self._calendar_capture is None:
            _verified(self.input_dir, _APPROVED_INPUTS, '.yaml')
        else:
            capture = self._calendar_capture
            capture.assert_live()
            if capture.profile != 'melos' or capture.world_name != 'world.yaml':
                raise ValueError('Approved Melos capture required')
            capture.validate_profile()
            for name, expected in _APPROVED_INPUTS.items():
                role = ('subject:' + Path(name).stem if name.startswith('subjects/')
                        else Path(name).stem)
                if hashlib.sha256(capture.bytes(role)).hexdigest() != expected:
                    raise ValueError('Captured bytes differ from approved Melos inputs')

    def _bind_capture(self, module):
        from engine.calendar_inputs import load_captured_project, subjects_from_capture
        capture = self._calendar_capture
        # Frozen Trial spells melos-input literally. ROOT is a logical namespace,
        # not a materialized directory; World.source retains the captured label.
        selector = self._logical_root / 'melos-input/world.yaml'

        def from_yaml(world_path):
            capture.assert_live()
            if Path(world_path) != selector:
                raise ValueError('World is outside the approved Melos selector')
            world, _ = load_captured_project(capture, provenance_style='melos')
            return world

        def read_subjects(world_path, config):
            capture.assert_live()
            if Path(world_path) != capture.display_source or config != capture.config:
                raise ValueError('Subjects selector differs from captured project')
            return subjects_from_capture(capture)

        module.World = types.SimpleNamespace(from_yaml=from_yaml)
        module.read_subjects = read_subjects

    @property
    def identity(self):
        return {'profile': 'melos_approved_ninegene_v1', 'transform': self._transform_version,
                'transform_source_hash': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'sources': deepcopy(_APPROVED_SOURCES), 'inputs': deepcopy(_APPROVED_INPUTS),
                'genome': deepcopy(self._genome), 'product_active': True}

    def create(self, seed):
        if type(seed) is not int or not 0 <= seed <= 2147483647:
            raise ValueError('Bounded nonnegative integer seed required')
        self._verify()
        if self._module.ROOT != self._logical_root:
            raise ValueError('Factory input namespace changed')
        genome = Genome.from_dict(deepcopy(self._genome))
        if self._calendar_capture is None:
            trial = self._module.Trial(genome, seed)
        else:
            scenario = self._calendar_capture.yaml('scenario')
            if not isinstance(scenario, dict) or not scenario:
                raise ValueError('Nonempty approved scenario required')
            trial = self._module.Trial(genome, seed, scenario=deepcopy(scenario))
        session = MelosSession(trial, input_hashes=deepcopy(_APPROVED_INPUTS),
                               source_hashes=deepcopy(_APPROVED_SOURCES),
                               choice_provider=MelosChoiceProvider(genome, seed))
        # This binding is an in-memory staging capability, never checkpoint
        # data or a pathname fallback. The caller still owns the live capture.
        if self._calendar_capture is not None:
            session.stage._captured_factory = self
        return session
