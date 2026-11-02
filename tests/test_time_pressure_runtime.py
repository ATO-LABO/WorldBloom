"""Offline, isolated fixtures for opt-in subjective deadline weighting."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import importlib
import json
import math
import random
import shutil
import tempfile
import unittest
from unittest.mock import patch
import yaml

from engine.actions import Action
from engine.sim import Simulation
from engine.time_pressure import (PROFILE, ActorTimeEvidence, TimePressure,
    completion_at, load_sources, normalize_config, reweight, validate_runtime, validate_sources)
from execution.configs import ConfigStore, evolution_defaults, normalize
from execution.provenance import ConfigError
from test_goal_planner import load, inputs

ROOT = Path(__file__).resolve().parents[1]


def sources(world, kind="public"):
    awareness = {"kind": kind}
    if kind == "known_fact": awareness["fact_id"] = next(iter(world.facts))
    return {"version": 1, "sources": [{"id": "test-deadline", "anchor": {"kind": "goal_period", "id": "beacon_sent"},
        "response_goal_id": "beacon_sent", "actor_ids": [world.protagonist], "awareness": awareness,
        "estimate": {"duration":6,"fact_id":next(iter(world.facts))}}]}


def action(root="beacon_sent", duration=2, delay=1):
    steps = [{"verb": "respond", "args": ["a"], "duration": duration, "completion_delay": delay}]
    return Action("respond", ("a",), {"goal_plan": {"root": root, "status": "planned", "steps": steps, "assumptions": []}})


def packet(deadline=7, now=6, finish=7, duration=2):
    return ActorTimeEvidence("hero", "s", "beacon_sent", now, now, deadline, finish, duration, ("public:s",))


class PurePressureTests(unittest.TestCase):
    def test_normalization_off_absent_and_on(self):
        self.assertIsNone(normalize_config(None))
        self.assertIsNone(normalize_config({"enabled": False}))
        self.assertEqual(normalize_config({"enabled": True}), PROFILE)
        for bad in (False, {}, {"enabled": 1}, {"enabled": True, "version": True},
                    {"enabled": False, "profile": "future"}, {"enabled": True, "surprise": 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError): normalize_config(bad)

    def test_completion_adapter_instant_timed_and_multiple_steps(self):
        self.assertEqual(completion_at(6, [{"duration": 2, "completion_delay": 1}]), 7)
        self.assertEqual(completion_at(6, [{"duration": 2, "completion_delay": 2}]), 8)
        self.assertEqual(completion_at(6, [{"duration": 3}, {"duration": 2}]), 10)
        for bad in ([], [{"duration": True}], [{"duration": 0}], [{"duration": 2, "completion_delay": 3}]):
            with self.assertRaises(ValueError): completion_at(6, bad)

    def test_unknown_or_all_one_returns_original_object_and_weights(self):
        offered = [(action(), 1e308), (Action("rest"), 1e-300)]
        self.assertIs(reweight(offered, "hero", 6, ())[0], offered)
        self.assertIs(reweight(offered, "hero", 6, (packet(deadline=20),))[0], offered)

    def test_known_goal_progress_and_no_unrelated_or_rest_bonus(self):
        offered = [(action(), 2.0), (action(root="other"), 2.0), (Action("rest"), 2.0)]
        result, log = reweight(offered, "hero", 6, (packet(),), record=True)
        self.assertEqual([w for _, w in result], [4.0, 1.0, 1.0])
        self.assertEqual([a for a, _ in offered], [a for a, _ in result])
        self.assertEqual(log["candidates"][0]["subjective_slack"], 0)

    def test_overflow_safe_scaling_and_zero_remains_zero(self):
        result, log = reweight([(action(), 1e308), (Action("rest"), 1e308), (action(), 0)], "hero", 6, (packet(),), record=True)
        self.assertEqual([w for _, w in result], [4.0, 1.0, 0.0])
        self.assertTrue(all(math.isfinite(w) for _, w in result))
        self.assertEqual(log["common_divisor"], 1e308)
        self.assertNotIn("weight_times_multiplier", str(log))

    def test_bad_weights_rejected_only_on_effective_correction_path(self):
        for bad in (math.inf, math.nan, -1):
            with self.assertRaises(ValueError): reweight([(action(), bad)], "hero", 6, (packet(),))
        offered = [(action(), math.inf)]
        self.assertIs(reweight(offered, "hero", 6, ())[0], offered)
        zeros=[(action(),0)]
        self.assertIs(reweight(zeros,"hero",6,(packet(),))[0],zeros)

    def test_too_late_legal_action_not_removed_and_no_extra_rng(self):
        rng = random.Random(13); state = rng.getstate()
        offered = [(action(), 1), (Action("rest"), 1)]
        result, _ = reweight(offered, "hero", 6, (packet(deadline=5),))
        self.assertEqual(len(result), 2); self.assertGreater(result[0][1], 0)
        self.assertEqual(rng.getstate(), state)

    def test_conditional_and_wrong_estimate_do_not_receive_packet(self):
        offered = [(action(duration=3), 2)]
        self.assertIsNot(reweight(offered,"hero",6,(packet(),))[0],offered)
        offered[0][0].meta["goal_plan"]["status"] = "conditional"
        self.assertIs(reweight(offered,"hero",6,(packet(duration=3,finish=8),))[0],offered)

    def test_wrong_actor_or_future_observation_rejected(self):
        with self.assertRaises(ValueError): reweight([(action(), 1)], "other", 6, (packet(),))
        with self.assertRaises(ValueError): reweight([(action(), 1)], "hero", 5, (packet(),))


class SourceAndRuntimeTests(unittest.TestCase):
    def test_schema_raw_and_world_equivalence_and_invalid_refs(self):
        raw, cast=inputs();w,p,_=load();doc=sources(w)
        self.assertEqual(validate_sources(doc,w,p),validate_sources(doc,raw,{x["id"]:x for x in cast}))
        validate_runtime(w,p);validate_runtime(raw,{x["id"]:x for x in cast})
        for edit in (lambda d:d["sources"].append(deepcopy(d["sources"][0])),
                     lambda d:d["sources"][0].update(response_goal_id="missing"),
                     lambda d:d["sources"][0].update(actor_ids=[w.antagonist]),
                     lambda d:d["sources"][0]["awareness"].update(kind="secret")):
            bad=deepcopy(doc);edit(bad)
            with self.assertRaises(ValueError):validate_sources(bad,w,p)

    def test_unsupported_world_and_missing_sources_rejected(self):
        w,p,_=load();w.slot_execution={"mode":"special"}
        with self.assertRaises(ValueError):TimePressure(PROFILE,sources(w),w,p)
        w.slot_execution=None
        with self.assertRaises(ValueError):TimePressure(PROFILE,{"version":1,"sources":[]},w,p)

    def test_known_fact_unknown_neutral_then_known_corrects(self):
        w,p,_=load();actor=p[w.protagonist];doc=sources(w,"known_fact")
        actor.knowledge.discard(doc["sources"][0]["awareness"]["fact_id"])
        runtime=TimePressure(PROFILE,doc,w,p)
        plan=w.planner.search(w,actor,"beacon_sent",1)
        a=Action(plan["steps"][0]["verb"],tuple(plan["steps"][0]["args"]),{"goal_plan":plan})
        offered=[(a,2),(Action("rest"),1)]
        before=w.goal_rules.definitions["beacon_sent"]["period"]["end"]
        w.goal_rules.definitions["beacon_sent"]["period"]["end"]=completion_at(1,plan["steps"])
        self.assertIs(runtime.apply(w,actor,offered,1)[0],offered)
        actor.knowledge.add(doc["sources"][0]["awareness"]["fact_id"])
        result,explain,review=runtime.apply(w,actor,offered,1,record=True)
        self.assertGreater(result[0][1]/result[1][1],2)
        self.assertNotIn("objective_slack",str(explain));self.assertIsNone(review)
        self.assertIsNone(runtime.review(w,a,1)["sources"][0]["objective_slack"])
        # Hidden actual world state does not supply the actor's estimate.
        w.responses.state["battery"]="destroyed"
        self.assertEqual(runtime.apply(w,actor,offered,1,record=True)[0],result)

    def test_missing_or_unrecognized_estimate_is_valid_and_neutral(self):
        w,p,_=load();actor=p[w.protagonist];doc=sources(w)
        doc['sources'][0].pop('estimate')
        runtime=TimePressure(PROFILE,doc,w,p);offered=[(action(),1)]
        result,log,_=runtime.apply(w,actor,offered,1,record=True)
        self.assertIs(result,offered);self.assertEqual(log['source_reasons'][0]['reason'],'estimate_unknown')
        doc=sources(w);actor.knowledge.discard(doc['sources'][0]['estimate']['fact_id'])
        runtime=TimePressure(PROFILE,doc,w,p)
        self.assertIs(runtime.apply(w,actor,offered,1)[0],offered)
        for value in (0,True,10001,1.5):
            bad=deepcopy(doc);bad['sources'][0]['estimate']['duration']=value
            with self.assertRaises(ValueError):validate_sources(bad,w,p)

    def test_real_adapter_does_not_copy_hidden_planner_duration_into_actor_evidence(self):
        w,p,_=load();actor=p[w.protagonist];doc=sources(w)
        actor.knowledge.add(doc['sources'][0]['estimate']['fact_id'])
        runtime=TimePressure(PROFILE,doc,w,p)
        def offered():
            plan=w.planner.search(w,actor,'beacon_sent',1)
            step=plan['steps'][0]
            return [(Action(step['verb'],tuple(step['args']),{'goal_plan':plan}),2),(Action('rest'),1)]
        before=offered();first=runtime.apply(w,actor,before,1,record=True)
        w.responses.actions['retrieve_store']['steps'] += 1
        after=offered();self.assertNotEqual(before[0][0].meta['goal_plan']['eta'],after[0][0].meta['goal_plan']['eta'])
        second=runtime.apply(w,actor,after,1,record=True)
        self.assertEqual([v for _,v in first[0]],[v for _,v in second[0]])
        self.assertEqual(first[1],second[1])
        self.assertEqual(first[1]['candidates'][0]['estimated_duration'],6)
        self.assertNotEqual(runtime.review(w,before[0][0],1)['planner_duration_forecast_completion_at'],
                            runtime.review(w,after[0][0],1)['planner_duration_forecast_completion_at'])

    def test_scheduled_event_boundary_is_before_same_tick_choice(self):
        w,p,_=load();doc=sources(w);w.scheduled_events=({"id":"alarm","day":2,"slot":w.slots[0]},)
        doc["sources"][0]["anchor"]={"kind":"scheduled_event","id":"alarm"}
        runtime=TimePressure(PROFILE,doc,w,p)
        self.assertEqual(runtime._deadline(w,doc["sources"][0]),len(w.slots))

    def test_simulation_absent_false_and_neutral_keep_rng_and_logs(self):
        def sample(option=None,source=None):
            w,p,_=load();sim=Simulation(11,w,p,Path('.unused'),record_explanations=True,
                **({"time_pressure":option,"time_pressure_sources":source} if option is not None else {}))
            sim.turn=1;sim.day=1
            result=sim.choose_action(p[w.protagonist])
            return result[0].verb,result[0].args,result[1],sim.rng.getstate(),sim._decision_context
        absent=sample();self.assertEqual(absent,sample({"enabled":False}))
        w,p,_=load();doc=sources(w,"known_fact")
        # Remove knowledge from all copied subject fixtures through an unknown existing fact.
        doc["sources"][0]["awareness"]["fact_id"]="flight_manual"
        neutral=sample(PROFILE,doc)
        self.assertEqual(absent[:4],neutral[:4])
        self.assertNotIn("time_pressure",absent[4])

    def test_forced_selection_bypasses_pressure_and_contest_unchanged(self):
        w,p,_=load();sim=Simulation(1,w,p,Path('.unused'),time_pressure=PROFILE,time_pressure_sources=sources(w))
        sim.turn=1;sim.day=1
        with patch.object(sim._time_pressure,"apply",side_effect=AssertionError('must not run')):
            action,probability=sim.choose_action(p[w.protagonist],forced={"verb":"move","args":["保管庫"],"event_id":"test"})
        self.assertEqual(action.verb,"move");self.assertIsNone(probability)

    def test_full_fixture_off_matches_prechange_simulation_events_and_rng(self):
        import importlib.util
        before=ROOT/'tests/fixtures/time_pressure/sim_before.py'
        spec=importlib.util.spec_from_file_location('time_pressure_before_sim',before)
        old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
        def run(cls,options):
            w,p,_=load()
            with tempfile.TemporaryDirectory(prefix='pressure-trajectory-') as folder:
                sim=cls(31,w,p,Path(folder),record_explanations=True,**options)
                rows=[json.loads(l) for l in sim.run().read_text(encoding='utf-8').splitlines()]
                # A different source location has truthful source provenance;
                # compare every other byte-equivalent JSON value, never fake it.
                for row in rows:
                    if row.get('kind')=='header':row.pop('engine_hash',None)
                return rows,sim.rng.getstate()
        expected=run(old.Simulation,{})
        self.assertEqual(expected,run(Simulation,{}))
        self.assertEqual(expected,run(Simulation,{'time_pressure':{'enabled':False}}))


class ConfigAndWiringTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='pressure-config-');self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name).resolve();self.repo=self.base/'repo';self.repo.mkdir()
        for area in ('projects','templates','engine','gapengine','scripts','execution'):
            shutil.copytree(ROOT/area,self.repo/area,ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copyfile(ROOT/'requirements.txt',self.repo/'requirements.txt')
        self.store=ConfigStore(self.repo,self.base/'control',self.base/'runs')
        self.spec={"label":"pressure fixture","project_id":"ship_planning","template_id":"basic",
            "evolution":{"generations":1,"population":1,"seeds":1,"processes":1,"keep":"all","kappa":0}}
        w,p,_=load();self.doc=sources(w)
        self.path=self.repo/'projects/ship_planning/time-pressure.yaml'
        self.path.write_text(yaml.safe_dump(self.doc,allow_unicode=True),encoding='utf-8')

    def test_off_normalized_compilation_and_capture_ignore_sidecar(self):
        self.assertNotIn('time_pressure',evolution_defaults())
        self.assertEqual(normalize(self.spec),normalize({**self.spec,'time_pressure':{'enabled':False}}))
        a=self.store.preview(self.spec);self.path.write_text('broken: [',encoding='utf-8')
        self.assertEqual(a,self.store.preview({**self.spec,'time_pressure':{'enabled':False}}))
        cfg=self.store.save(self.spec,config_id='off')
        manifest=self.store.prepare_run(cfg['config_id'],run_id='off-run',job_id='job-off')
        self.assertNotIn('time_pressure',manifest)
        self.assertNotIn('--time-pressure',manifest['argv'])
        self.assertFalse((self.base/'runs/off-run/inputs/projects/ship_planning/time-pressure.yaml').exists())

    def test_on_save_reload_capture_cli_and_duplicate_off(self):
        on={**self.spec,'time_pressure':{'enabled':True}}
        cfg=self.store.save(on,config_id='on');self.assertEqual(self.store.get('on')['time_pressure'],PROFILE)
        manifest=self.store.prepare_run('on',run_id='on-run',job_id='job-on')
        self.assertEqual(manifest['time_pressure'],PROFILE);self.assertIn('--time-pressure',manifest['argv'])
        self.assertTrue((self.base/'runs/on-run/inputs/projects/ship_planning/time-pressure.yaml').is_file())
        off=self.store.duplicate('on',{'time_pressure':{'enabled':False}},new_id='off-again')
        self.assertNotIn('time_pressure',off)
        self.assertTrue(self.path.exists())
        self.assertFalse((self.base/'control/configs/off-again/inputs/projects/ship_planning/time-pressure.yaml').exists())

    def test_on_rejects_missing_source_and_unsupported_modes(self):
        self.path.unlink()
        with self.assertRaises(ConfigError):self.store.preview({**self.spec,'time_pressure':{'enabled':True}})
        spec={**self.spec,'time_pressure':{'enabled':True},'evolution':{'world_expansion':'expand'}}
        with self.assertRaises(ConfigError):normalize(spec)

    def test_cli_off_does_not_insert_new_cfg_and_on_passes_canonical(self):
        cli=importlib.import_module('scripts.evolve')
        argv=['--project',str(self.repo/'projects/ship_planning'),'--template',str(self.repo/'templates/basic'),'--out',str(self.base/'output')]
        for enabled in (False,True):
            with patch.object(cli,'evolve',return_value=SimpleNamespace(cells={})),patch.object(cli,'_show_optional_outputs') if hasattr(cli,'_show_optional_outputs') else patch('builtins.print'):
                with patch.object(cli,'evolve',return_value=SimpleNamespace(cells={})) as call:
                    cli.main(argv+(['--time-pressure'] if enabled else []))
                    cfg=call.call_args[0][0]
                    self.assertEqual(cfg.get('time_pressure'),PROFILE if enabled else None)
                    if not enabled:self.assertNotIn('time_pressure',cfg)

    def test_fingerprint_new_key_only_when_enabled(self):
        module=importlib.import_module('gapengine.evolve')
        kw=dict(project_dir=self.repo/'projects/ship_planning',template_dir=self.repo/'templates/basic',population_size=1,
                seed_count=1,seed_base=1,ga_seed=1,keep='all',coevolve=False,meta_evolution=False,target_ending=None,
                record_explanations=False,rationality_cfg=None)
        off=module._cfg_fingerprint(**kw)
        self.assertEqual(off,module._cfg_fingerprint(**kw,time_pressure_cfg=None))
        self.assertNotEqual(off,module._cfg_fingerprint(**kw,time_pressure_cfg={**PROFILE,'sources_sha256':'a'*64}))
        self.path.write_text('other sidecar',encoding='utf-8')
        self.assertEqual(off,module._cfg_fingerprint(**kw))

    def test_actual_worker_manifest_compiles_same_on_off_setting(self):
        adapter=importlib.import_module('execution.evolution_worker')
        for enabled in (False,True):
            spec={**self.spec,**({'time_pressure':{'enabled':True}} if enabled else {})}
            ident='worker-on' if enabled else 'worker-off'
            self.store.save(spec,config_id=ident)
            manifest=self.store.prepare_run(ident,run_id=ident+'-run',job_id=ident+'-job')
            run=self.base/'runs'/(ident+'-run')
            job={'run_id':run.name,'config_id':ident,'kind':'evolve','nonce':'fixture'}
            with patch.object(adapter,'__file__',str(run/'runtime/execution/evolution_worker.py')), \
                 patch('execution.worker.read_job',return_value=job), \
                 patch.object(adapter,'read_jev_api_key',return_value=None), \
                 patch.object(adapter,'EvolutionObserver'),patch.object(adapter,'evolve') as execute:
                self.assertEqual(adapter.main(['--run',str(run),'--control',str(self.base/'control'),'--job',ident+'-job']),0)
            cfg=execute.call_args[0][0]
            self.assertEqual(cfg.get('time_pressure'),PROFILE if enabled else None)
            if not enabled:self.assertNotIn('time_pressure',cfg)
            self.assertEqual(cfg['project'],run/'inputs/projects/ship_planning')

    def test_worker_still_rejects_execution_outside_frozen_runtime(self):
        adapter=importlib.import_module('execution.evolution_worker')
        with patch.object(adapter,'__file__',str(ROOT/'execution/evolution_worker.py')):
            with self.assertRaisesRegex(ValueError,'frozen run runtime'):
                adapter.main(['--run',str(self.base/'runs/outside'),
                              '--control',str(self.base/'control'),'--job','outside-job'])

    def test_real_single_generation_frozen_fixture_runs_without_model_calls(self):
        module=importlib.import_module('gapengine.evolve')
        out=self.base/'ga-output'
        cfg={'project':self.repo/'projects/ship_planning','template':self.repo/'templates/basic','out':out,
             'population':1,'generations':1,'seeds':1,'processes':1,'seed_base':11,'ga_seed':2,
             'keep':'all','record_explanations':True,'time_pressure':PROFILE,
             'rationality':{'kappa':0,'backend':'none'}}
        module.evolve(cfg)
        files=list(out.rglob('layers.jsonl'))
        self.assertTrue(files)
        rows=[json.loads(line) for line in files[0].read_text(encoding='utf-8').splitlines()]
        decisions=[r for r in rows if r.get('kind')=='decision' and r.get('subject')=='通信士']
        self.assertTrue(any('time_pressure' in (r.get('explanation') or {}) for r in decisions))
        self.assertTrue((out/'ga_state.json').is_file())


if __name__=='__main__':unittest.main()
