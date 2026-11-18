"""Private input-only capture witnesses; no public route or output guarantee."""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml

from engine import calendar_capture as cc
from engine.calendar_inputs import (FILE_LIMIT, _canonical, _digest, load_project,
    load_captured_project, subject_spec)
from engine.calendar_fs import PosixProjectDirectory
from engine.sim import Simulation
from engine.world import World

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT/'projects/calendar_workshop_example'


def put(path, value):
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding='utf-8')


def memory_run(runtime):
    runtime._tick(runtime.now)
    while runtime.now < runtime.end and not runtime.done:
        runtime._tick(runtime._next_boundary(runtime.now, runtime.end))
    return runtime.export_state()


@unittest.skipUnless(os.name == 'posix', 'native Linux private spool witnesses')
class CalendarCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wb-capture-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root/'project'
        shutil.copytree(EXAMPLE, self.project)
        self.path = self.project/'world.yaml'

    def capture(self, **kwargs):
        capture = cc.open_calendar_capture(self.path, **kwargs)
        self.addCleanup(capture.close)
        return capture

    def runtime(self, capture):
        w, p = load_captured_project(capture)
        return Simulation(0, w, p, self.root/'unused', calendar_capture=capture).slot_runtime

    def fixed(self, *, crlf=False):
        # Synthetic schema3 construction, no call to legacy output writers.
        raw = self.path.read_bytes()
        if crlf:
            for path in self.project.rglob('*.yaml'):
                path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n'))
            raw = self.path.read_bytes()
        with self.capture() as capture:
            world, people = load_captured_project(capture)
            calendar = Simulation(0, world, people, self.root/'unused').slot_runtime.calendar
            source_inputs = capture.core_manifest()
        definition = yaml.safe_load(raw)
        definition['slot_execution']['fixed_version'] = True
        put(self.path, definition)
        with self.path.open('rb') as stream:
            fixed = stream.read()
        inputs = deepcopy(source_inputs)
        inputs['world.yaml'] = {'size_bytes': len(fixed), 'sha256': hashlib.sha256(fixed).hexdigest()}
        version = {'schema_version': 3, 'contract': 'calendar_promises_v2', 'world': 'world.yaml',
            'configuration': definition['slot_execution'], 'calendar': calendar, 'inputs': inputs,
            'source_inputs': source_inputs, 'source_world': raw.decode('utf-8'), 'metadata': None,
            'engine_supported': True, 'execution_ready': True}
        version['id'] = _digest(version)
        (self.project/'execution-version.json').write_bytes(_canonical(version))
        (self.project/'choices.json').unlink()
        (self.project/'README.md').unlink()
        return version

    def test_reads_once_and_retains_native_observations_not_full_source_buffers(self):
        reads = []
        original = PosixProjectDirectory.read
        def read(owner, name, **kw):
            reads.append(name)
            return original(owner, name, **kw)
        with patch.object(PosixProjectDirectory, 'read', read):
            cap = self.capture()
            runtime = self.runtime(cap)
            runtime.inputs(); runtime.export_state(); runtime._summary_bytes()
        self.assertEqual(len(reads), len(set(reads)))
        self.assertEqual(set(reads), {'world.yaml','contract.yaml','subjects/ada.yaml','subjects/bo.yaml',
                                    'execution-version.json','purpose-preparation.json'})
        member = cap.member('world')
        self.assertEqual(member.source_observation.size_bytes, len(member.data))
        self.assertEqual(member.source_observation.backend, 'posix')
        self.assertFalse(hasattr(member.source_observation, 'data'))
        self.assertFalse(hasattr(member, 'owner'))
        self.assertFalse(hasattr(cap, '__fspath__'))
        with self.assertRaises(ValueError): cap.bytes('../world.yaml')

    def test_deleted_namespace_never_reopened_for_load_runtime_or_export(self):
        raw = yaml.safe_load(self.path.read_text())
        raw['gapengine'] = {'action_graph':'graph.data'}
        put(self.path, raw)
        put(self.project/'graph.data', {'nodes':[{'id':'rest','verb':'rest','label':'Captured'}], 'edges':[]})
        cap = self.capture(); expected = cap.core_manifest()
        shutil.rmtree(self.project)
        def forbidden(*args, **kwargs): raise AssertionError('Legacy project read')
        with ExitStack() as stack:
            for name in ('_input_files','_yaml','_world_path','safe_project_path','read_project_yaml','read_subjects','validate_fixed_version'):
                stack.enter_context(patch('engine.calendar_inputs.'+name, forbidden))
            for name in ('safe_project_path','read_project_yaml','validate_fixed_version'):
                stack.enter_context(patch('engine.calendar_runtime.'+name, forbidden))
            stack.enter_context(patch('engine.yaml_cache.load_yaml', forbidden))
            stack.enter_context(patch('engine.world.load_yaml', forbidden))
            stack.enter_context(patch('engine.phase2._load_effect_library', forbidden))
            stack.enter_context(patch('engine.purpose_preparation_runtime.read_record', forbidden))
            for name in ('open','read_bytes','read_text','stat','exists','resolve'):
                stack.enter_context(patch.object(Path, name, forbidden))
            runtime = self.runtime(cap)
            self.assertEqual(runtime.world.action_graph['nodes'][0]['label'], 'Captured')
            self.assertEqual(runtime.inputs()['input_provenance']['inputs'], expected)
            runtime.configure([]); runtime._tick(runtime.now)
            runtime.inputs(); runtime.export_state(); runtime._summary_bytes()
        self.assertFalse((self.root/'unused').exists())

    def test_replacement_changes_new_capture_not_held_values(self):
        cap = self.capture()
        self.path.write_bytes(self.path.read_bytes().replace(b'Night workshop', b'Light workshop'))
        a, _ = load_captured_project(cap)
        with self.capture() as latest:
            b, _ = load_captured_project(latest)
        self.assertEqual((a.name,b.name), ('Night workshop','Light workshop'))

    def test_world_discovery_uses_one_revision(self):
        original = PosixProjectDirectory.read
        def read(owner, name, **kw):
            result = original(owner,name,**kw)
            if name == 'world.yaml': self.path.write_bytes(self.path.read_bytes().replace(b'contract.yaml',b'outside.yaml'))
            return result
        with patch.object(PosixProjectDirectory,'read',read): cap = self.capture()
        self.assertEqual(cap.config['contract'], 'contract.yaml')
        self.assertEqual(self.runtime(cap).world.name,'Night workshop')

    def test_detached_values_and_authored_mutation_checks(self):
        cap = self.capture()
        cap.config['contract'] = 'wrong'; cap.yaml('world')['name'] = 'wrong'
        cap.core_manifest()['world.yaml']['sha256'] = 'wrong'
        for change in ('traits','zone','knowledge','contract','config','provenance'):
            w,p = load_captured_project(cap)
            if change == 'traits': p['Ada'].traits['diligence'] = .1
            elif change == 'zone': p['Ada'].zone = 'Store'
            elif change == 'knowledge': p['Ada'].knowledge.add('invented')
            elif change == 'contract': w.slot_contract['tasks']['pieces']['minutes_per_unit'] = 11
            elif change == 'config': w.slot_execution['contract'] = 'wrong'
            else: w.calendar_input_provenance['inputs']['world.yaml']['sha256'] = 'wrong'
            with self.subTest(change=change), self.assertRaises(ValueError): Simulation(0,w,p,self.root/'unused')

    def test_partial_world_and_conflicting_capture_reject(self):
        cap = self.capture(); other = self.capture()
        w,p = load_captured_project(cap)
        with self.assertRaises(ValueError): Simulation(0,w,p,self.root/'unused',calendar_capture=other)
        unaccepted = World(cap.yaml('world'),cap.display_source,calendar_capture=cap)
        with self.assertRaisesRegex(ValueError,'fully validated'): Simulation(0,unaccepted,p,self.root/'unused')
        raw = cap.yaml('world'); raw['name'] = 'modified'
        with self.assertRaises(ValueError): World(raw,cap.display_source,calendar_capture=cap)

    def test_close_invalidates_members_construction_execution_and_exports(self):
        cap = self.capture(); member = cap.member('world'); rt = self.runtime(cap)
        cap.close(); cap.close()
        for operation in (lambda: member.data, lambda: member.manifest(), lambda: load_captured_project(cap),
                          lambda: rt.inputs(),lambda: rt.export_state(),lambda: rt._summary_bytes(),
                          lambda: rt.configure([]),lambda: rt._tick(rt.now)):
            with self.assertRaisesRegex(ValueError,'closed'): operation()
        with self.assertRaises(TypeError): deepcopy(cap)

    def test_existing_save_writers_not_reachable_from_private_runtime(self):
        rt = self.runtime(self.capture())
        for operation in (lambda: rt.run(), lambda: rt._outputs(), lambda: rt._run_locked(rt.end,False)):
            with self.assertRaisesRegex(ValueError,'input-only'): operation()
        self.assertFalse((self.root/'unused').exists())

    def test_fixed_schema3_crlf_same_version_and_descriptor_as_legacy(self):
        version = self.fixed(crlf=True)
        old_w, old_p = load_project(self.path)
        old = Simulation(0,old_w,old_p,self.root/'unused').slot_runtime.inputs()
        cap = self.capture(); rt = self.runtime(cap)
        self.assertEqual(rt.inputs(),old)
        self.assertEqual(rt.inputs()['input_provenance']['fixed_version_id'],version['id'])
        self.assertIn('\r\n',version['source_world'])
        self.assertEqual(cap.bytes('contract'), (self.project/'contract.yaml').read_bytes())
        shutil.rmtree(self.project)
        self.assertEqual(rt.inputs(),old)

    def test_fixed_malformed_provenance_and_extra_tree_reject(self):
        version = self.fixed()
        path = self.project/'execution-version.json'
        for change in ('schema','unknown','source_world','source_inputs','calendar'):
            bad = deepcopy(version)
            if change == 'schema': bad['schema_version'] = 2
            elif change == 'unknown': bad['extra'] = True
            elif change == 'source_world': bad['source_world'] += '\n#forge'
            elif change == 'source_inputs': bad['source_inputs']['contract.yaml']['sha256'] = '0'*64
            else: bad['calendar']['duration_minutes'] += 1
            bad['id'] = _digest({k:v for k,v in bad.items() if k != 'id'})
            path.write_bytes(_canonical(bad))
            with self.subTest(change=change), self.assertRaises(ValueError): self.runtime(self.capture())
        path.write_bytes(_canonical(version))
        (self.project/'purpose-preparation.json').write_text('{"version":1,"enabled":false}')
        with self.assertRaisesRegex(ValueError,'file/directory'): self.capture()

    def test_preparation_absent_disabled_malformed_enabled_and_explicit(self):
        cap = self.capture()
        path = self.project/'purpose-preparation.json'
        path.write_text('invalid')
        self.assertIsNone(self.runtime(cap).sim._purpose_preparation)
        for data in ('invalid','[]','{"version":2,"enabled":false}', '{"version":1,"enabled":1}',
                     '{"version":1,"enabled":false,"enabled":true}', '{"version":1,"enabled":true}'):
            path.write_text(data)
            with self.subTest(data=data), self.assertRaises(ValueError): self.runtime(self.capture())
        for data in ('{"version":1,"enabled":false}', '{"version":1,"enabled":false}'+ ' '* (cc.MAX_BYTES-29)):
            path.write_text(data)
            self.assertIsNone(self.runtime(self.capture()).sim._purpose_preparation)
        path.write_text('malformed but captured')
        with self.capture() as c:
            w,p = load_captured_project(c)
            self.assertIsNone(Simulation(0,w,p,self.root/'unused',purpose_preparation=False)._purpose_preparation)
        path.write_bytes(b' '* (cc.MAX_BYTES+1))
        with self.assertRaises(ValueError): self.capture()
        override = {'enabled':False}
        with self.capture(purpose_preparation=override) as c:
            override['enabled'] = True
            self.assertIsNone(self.runtime(c).sim._purpose_preparation)
        with self.capture(purpose_preparation=False) as c:
            self.assertIsNone(self.runtime(c).sim._purpose_preparation)

    def test_effects_paths_reject_before_any_external_acquisition(self):
        base = yaml.safe_load(self.path.read_text()); original = PosixProjectDirectory.read
        for effects in ('/tmp/outside.yaml','../outside.yaml','empty.yaml','',42,[]):
            raw = deepcopy(base); raw['gapengine'] = {'effects':effects}; put(self.path,raw)
            reads = []
            def read(owner,name,**kw): reads.append(name); return original(owner,name,**kw)
            with patch.object(PosixProjectDirectory,'read',read), self.subTest(effects=effects), self.assertRaisesRegex(ValueError,'configured effects'):
                self.capture()
            self.assertEqual(reads,['world.yaml'])
        base['gapengine'] = {'effects':None}; put(self.path,base)
        self.runtime(self.capture())

    def test_yaml_duplicate_nonfinite_deep_cyclic_reject(self):
        original = self.path.read_bytes()
        for suffix in (b'\nname: duplicate\n', b'\nextra: .nan\n', b'\nextra: &a [*a]\n', b'\nextra: '+b'['*70+b'0'+b']'*70):
            self.path.write_bytes(original+suffix)
            with self.subTest(suffix=suffix[:30]), self.assertRaises(ValueError): self.capture()

    def test_json_duplicate_nonfinite_reject(self):
        self.fixed(); path=self.project/'execution-version.json'; original=path.read_bytes()
        for data in (original[:-1]+b',"schema_version":3}', original[:-1]+b',"extra":NaN}'):
            path.write_bytes(data)
            with self.assertRaises(ValueError): self.runtime(self.capture())

    def test_collision_and_prefix_reject_before_secondary_reads(self):
        base=yaml.safe_load(self.path.read_text()); original=PosixProjectDirectory.read
        for reference in ('world.yaml','WORLD.YAML','subjects','subjects/ada.yaml','execution-version.json'):
            raw=deepcopy(base); raw['gapengine']={'action_graph':reference}; put(self.path,raw)
            reads=[]
            def read(owner,name,**kw): reads.append(name); return original(owner,name,**kw)
            with patch.object(PosixProjectDirectory,'read',read), self.subTest(reference=reference), self.assertRaises(ValueError): self.capture()
            self.assertEqual(reads,['world.yaml'])

    def test_hardlink_before_read_and_optional_missing_parent_not_absent(self):
        alias=self.root/'outside'; os.link(self.path,alias)
        with patch('engine.calendar_fs.os.read',side_effect=AssertionError('must reject before bytes')):
            with self.assertRaisesRegex(ValueError,'hardlink'): self.capture()
        alias.unlink()
        raw=yaml.safe_load(self.path.read_text()); raw['slot_execution']['contract']='missing/contract.yaml'; put(self.path,raw)
        with self.assertRaises(FileNotFoundError): self.capture()

    def test_core_exact_limit_and_plus_one(self):
        raw=self.path.read_bytes(); self.path.write_bytes(raw+b'\n#'+b' '* (FILE_LIMIT-len(raw)-2))
        with self.capture() as cap: self.assertEqual(cap.member('world').size_bytes, FILE_LIMIT)
        with self.path.open('ab') as stream: stream.write(b' ')
        with self.assertRaises(ValueError): self.capture()

    def test_1000_subjects_over_64_mib_has_derived_budget(self):
        raw=yaml.safe_load(self.path.read_text())
        raw['slot_execution']['subjects']=[{'id':f'actor{i}','path':f'large/{i}.yaml'} for i in range(1000)]
        put(self.path,raw); (self.project/'large').mkdir()
        payload=b'id: synthetic\n#'+b'x'*70000
        for i in range(1000): (self.project/'large'/f'{i}.yaml').write_bytes(payload)
        with self.capture() as cap:
            self.assertGreater(sum(v['size_bytes'] for v in cap.core_manifest().values()),64*1024*1024)
            self.assertEqual(len(cap.core_manifest()),1002)

    def test_spool_failure_closes_once_and_preserves_primary_error(self):
        from engine.calendar_spool import PrivateSpool
        created=[]
        class Broken(PrivateSpool):
            def __init__(self,**kw): super().__init__(**kw); self.releases=0; created.append(self)
            def append(self,data): raise KeyboardInterrupt('capture cancelled')
            def close(self):
                if self._fd is not None: self.releases+=1
                return super().close()
        with patch.object(cc,'_spool',side_effect=lambda **kw: Broken(max_bytes=kw['max_bytes'],parent=self.root)):
            with self.assertRaises(KeyboardInterrupt): self.capture()
        self.assertEqual(created[0].releases,1)

    def test_acquisition_and_root_close_failure_preserve_cancellation(self):
        class BrokenRoot:
            exits = 0
            def __enter__(self): return self
            def read(self, *args, **kwargs): raise KeyboardInterrupt('interrupted read')
            def __exit__(self, *args):
                self.exits += 1
                raise OSError('root close uncertain')
        root = BrokenRoot()
        with patch.object(cc, 'open_project_directory', return_value=root):
            with self.assertRaisesRegex(KeyboardInterrupt, 'interrupted read') as found: self.capture()
        self.assertEqual(root.exits, 1)
        self.assertIsInstance(found.exception.__cause__, OSError)
        self.assertIn('source cleanup failed', found.exception.__notes__[0])

    def test_root_close_failure_never_returns_capture(self):
        original = PosixProjectDirectory.__exit__
        def fail(owner, *args):
            original(owner, *args)
            raise OSError('root close failed')
        with patch.object(PosixProjectDirectory, '__exit__', fail):
            with self.assertRaisesRegex(OSError, 'root close failed'): self.capture()

    def test_all_eight_calendar_fixtures_construct_and_export_from_capture(self):
        projects = ['calendar_workshop_example', 'calendar_promise_workshop', 'calendar_promise_rescue',
                    'cultural_festival_v2', 'cultural_festival_shared_planning', 'melos-input',
                    'joint_repair_example', 'joint_festival_example']
        for name in projects:
            target = self.root / ('fixture-' + name)
            shutil.copytree(ROOT / 'projects' / name, target)
            with self.subTest(project=name), cc.open_calendar_capture(target/'world.yaml') as cap:
                w,p = load_captured_project(cap)
                rt = Simulation(0,w,p,self.root/'unused').slot_runtime
                before = rt.inputs()
                shutil.rmtree(target)
                self.assertEqual(before, rt.inputs())
                rt.configure([]); rt._tick(rt.now); rt.export_state(); rt._summary_bytes()

    def test_festival_and_joint_in_memory_execution_match_legacy(self):
        from scripts.cultural_festival_v2 import choices
        for name in ('cultural_festival_v2', 'joint_repair_example', 'joint_festival_example'):
            target = self.root / ('run-' + name)
            shutil.copytree(ROOT/'projects'/name, target)
            old_w,old_p = load_project(target/'world.yaml')
            old = Simulation(0,old_w,old_p,self.root/'legacy-unused').slot_runtime
            commands = choices(old_w, 'fulfilled') if name == 'cultural_festival_v2' else json.loads((target/'choices.json').read_text())
            with self.subTest(project=name), cc.open_calendar_capture(target/'world.yaml') as cap:
                w,p = load_captured_project(cap)
                new = Simulation(0,w,p,self.root/'unused').slot_runtime
                old.configure(commands); new.configure(commands)
                self.assertEqual(old.inputs(), new.inputs())
                self.assertEqual(memory_run(old), memory_run(new))
                self.assertEqual(old._summary_bytes(), new._summary_bytes())
                fresh_w,fresh_p = load_captured_project(cap)
                restored = Simulation(0,fresh_w,fresh_p,self.root/'unused').slot_runtime
                restored.configure(commands); restored.restore_state(new.export_state())
                self.assertEqual(restored.export_state(), new.export_state())
        self.assertFalse((self.root/'legacy-unused').exists())

    def test_ordinary_world_and_public_loader_remain_separate(self):
        with self.assertRaises(ValueError): World.from_yaml(self.path)
        raw=yaml.safe_load(self.path.read_text()); raw.pop('slot_execution'); put(self.path,raw)
        with self.assertRaises(ValueError): self.capture()
        self.assertIsNone(getattr(World(raw, self.path),'slot_execution',None))

    def test_workshop_in_memory_run_restore_and_summary_match_legacy(self):
        commands=json.loads((self.project/'choices.json').read_text())
        old_w,old_p=load_project(self.path); old=Simulation(0,old_w,old_p,self.root/'old-unused').slot_runtime
        cap=self.capture(); new=self.runtime(cap)
        old.configure(commands); new.configure(commands)
        self.assertEqual(old.inputs(),new.inputs())
        self.assertEqual(memory_run(old),memory_run(new))
        self.assertEqual(old._summary_bytes(),new._summary_bytes())
        restored=self.runtime(cap); restored.configure(commands); restored.restore_state(new.export_state())
        self.assertEqual(restored.export_state(),new.export_state())
        self.assertFalse((self.root/'old-unused').exists())


class CalendarCaptureCleanupPrecedenceTests(unittest.TestCase):
    """Synthetic ownership failures, not native Windows qualification."""

    boundaries = ('root_enter', 'root_body', 'capture_exit', 'outer_spool')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wb-capture-cleanup-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'world.yaml'
        self.path.write_bytes((EXAMPLE / 'world.yaml').read_bytes())
        self.world_bytes = self.path.read_bytes()

    def exercise_failure(self, boundary, primary, cleanup, expected, *, status_on_primary=False):
        status = SimpleNamespace(creation='created', handle_release='unconfirmed',
            delete_on_close_requested=True, deletion_observed='unconfirmed',
            named_residue_possible=True, size=0, sealed=False,
            source_capture_release='unconfirmed', native_validation='NOT_RUN')
        status_error = primary if status_on_primary else cleanup
        status_error.spool_status = status
        root_calls, spool_calls = [], []
        payload = self.world_bytes

        class Root:
            def __enter__(self):
                if boundary == 'root_enter':
                    raise primary
                return self

            def read(self, *args, **kwargs):
                if boundary == 'root_body':
                    raise primary
                return SimpleNamespace(data=payload,
                    observation=SimpleNamespace(native_identity=('synthetic', 1)))

            def __exit__(self, kind, error, traceback):
                root_calls.append((kind, error))
                if boundary in ('root_enter', 'root_body'):
                    raise cleanup

        class Spool:
            def add(self, *args, **kwargs):
                raise primary

            def close(self):
                spool_calls.append('close')
                raise cleanup

        spool = Spool()
        if boundary == 'capture_exit':
            capture = object.__new__(cc.CalendarCapture)
            capture._spool = spool
            with self.assertRaises(BaseException) as caught:
                capture.__exit__(type(primary), primary, primary.__traceback__)
        else:
            with patch.object(cc, 'open_project_directory', return_value=Root()), \
                    patch.object(cc, '_spool', return_value=spool), \
                    patch.object(cc, 'CalendarCapture') as constructor:
                with self.assertRaises(BaseException) as caught:
                    cc.open_calendar_capture(self.path)
                constructor.assert_not_called()
            self.assertEqual(len(root_calls), 1)
            self.assertIs(root_calls[0][1], primary)
            self.assertEqual(spool_calls, ['close'] if boundary == 'outer_spool' else [])
        if boundary == 'capture_exit':
            self.assertIsNone(capture._spool)
            capture.close()
            self.assertEqual(spool_calls, ['close'])
            with self.assertRaisesRegex(ValueError, 'closed'):
                capture.assert_live()
        self.assertIs(caught.exception, expected)
        self.assertIs(caught.exception.spool_status, status)
        if primary is cleanup:
            self.assertIsNot(caught.exception.__cause__, caught.exception)
        else:
            other = cleanup if expected is primary else primary
            self.assertIs(caught.exception.__cause__, other)

    def test_cleanup_cancellation_overrides_ordinary_primary_at_each_boundary(self):
        for boundary in self.boundaries:
            for kind in (KeyboardInterrupt, SystemExit):
                with self.subTest(boundary=boundary, cleanup=kind.__name__):
                    primary, cleanup = ValueError('primary input failure'), kind('cleanup cancelled')
                    self.exercise_failure(boundary, primary, cleanup, cleanup)

    def test_earlier_cancellation_wins_at_each_boundary(self):
        for boundary in self.boundaries:
            for primary_kind in (KeyboardInterrupt, SystemExit):
                for cleanup_kind in (KeyboardInterrupt, SystemExit, OSError):
                    with self.subTest(boundary=boundary, primary=primary_kind.__name__,
                                      cleanup=cleanup_kind.__name__):
                        primary = primary_kind('primary cancelled')
                        cleanup = cleanup_kind('cleanup failure')
                        self.exercise_failure(boundary, primary, cleanup, primary)

    def test_ordinary_primary_survives_ordinary_cleanup_at_each_boundary(self):
        for boundary in self.boundaries:
            with self.subTest(boundary=boundary):
                primary, cleanup = ValueError('primary input failure'), OSError('cleanup failure')
                self.exercise_failure(boundary, primary, cleanup, primary)

    def test_primary_spool_status_survives_cleanup_cancellation(self):
        for boundary in self.boundaries:
            for kind in (KeyboardInterrupt, SystemExit):
                with self.subTest(boundary=boundary, cleanup=kind.__name__):
                    primary, cleanup = ValueError('primary input failure'), kind('cleanup cancelled')
                    self.exercise_failure(boundary, primary, cleanup, cleanup, status_on_primary=True)

    def test_same_exception_cleanup_never_creates_self_cause(self):
        for boundary in self.boundaries:
            for kind in (ValueError, KeyboardInterrupt, SystemExit):
                with self.subTest(boundary=boundary, error=kind.__name__):
                    error = kind('same failure object')
                    self.exercise_failure(boundary, error, error, error)

    def test_no_primary_cleanup_cancellation_propagates_and_consumes_ownership(self):
        for boundary in ('root_body', 'capture_exit'):
            for kind in (KeyboardInterrupt, SystemExit):
                with self.subTest(boundary=boundary, cleanup=kind.__name__):
                    cleanup = kind('normal exit cancelled')
                    status = SimpleNamespace(handle_release='unconfirmed', native_validation='NOT_RUN')
                    cleanup.spool_status = status
                    calls = []

                    class Owner:
                        def __enter__(self):
                            return self

                        def __exit__(self, *args):
                            calls.append('exit')
                            raise cleanup

                        def close(self):
                            calls.append('close')
                            raise cleanup

                    owner = Owner()
                    if boundary == 'root_body':
                        with patch.object(cc, 'open_project_directory', return_value=owner):
                            with self.assertRaises(BaseException) as caught:
                                with cc._owned_directory(self.path.parent):
                                    pass
                        self.assertEqual(calls, ['exit'])
                    else:
                        capture = object.__new__(cc.CalendarCapture)
                        capture._spool = owner
                        with self.assertRaises(BaseException) as caught:
                            capture.__exit__(None, None, None)
                        capture.close()
                        self.assertIsNone(capture._spool)
                        self.assertEqual(calls, ['close'])
                        with self.assertRaisesRegex(ValueError, 'closed'):
                            capture.assert_live()
                    self.assertIs(caught.exception, cleanup)
                    self.assertIs(caught.exception.spool_status, status)
                    self.assertIsNone(caught.exception.__cause__)


if __name__=='__main__': unittest.main()
