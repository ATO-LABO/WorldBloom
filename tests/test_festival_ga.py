from copy import deepcopy
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from gapengine.evolve import _evolve,run_individual,_next_population
from gapengine.festival_choices import ChoiceGenome,ACTORS
from gapengine.festival_ga import ADAPTER,completed_evaluation
from gapengine.qd import Archive,read_rows
from scripts.cultural_festival_probe import input_manifest,source_manifest


class FestivalGATests(unittest.TestCase):
    def test_choice_codec_has_no_personality_and_breeds(self):
        rng=random.Random(3);g=ChoiceGenome.random(rng)
        self.assertEqual(ChoiceGenome.from_dict(g.to_dict()),g)
        bad=g.to_dict();bad['traits']={}
        with self.assertRaises(ValueError):ChoiceGenome.from_dict(bad)
        with self.assertRaises(ValueError):ChoiceGenome.random(rng,plastic=True)
        self.assertNotEqual(ChoiceGenome.mutate(g,rng,p=1),g)
        offspring=_next_population(2,Archive(),[],rng,genome_codec=ChoiceGenome)
        self.assertTrue(all(isinstance(x,ChoiceGenome) for x,_ in offspring))

    def test_run_individual_factory_temporal_metrics_and_replay(self):
        g=ChoiceGenome.random(random.Random(4));before=input_manifest();source=source_manifest()
        with tempfile.TemporaryDirectory() as tmp,patch('socket.socket.connect',side_effect=AssertionError('network forbidden')):
            root=Path(tmp)
            job={'search_adapter':ADAPTER,'index':0,'generation':0,'genome':g.to_dict(),
                'seeds':[0],'out_dir':str(root/'first'),'logical_root':str(root)}
            first=run_individual(job);job['out_dir']=str(root/'second');second=run_individual(job)
            self.assertEqual(first['runs'][0]['native_layers_sha256'],second['runs'][0]['native_layers_sha256'])
            result=first['runs'][0];self.assertEqual(result['input_manifest'],before)
            self.assertEqual(result['source_manifest'],source)
            self.assertEqual(result['audit']['decision_checks'],88)
            self.assertEqual(result['temporal_evaluation']['matched_completions'],88)
            self.assertGreater(len(result['effective_sequence']),0)
            self.assertEqual(result['temporal_evaluation']['absence_reward'],0)
            draws=json.loads((root/'first/seed-0/choice-draws.json').read_text())
            self.assertEqual({d['actor'] for d in draws},set(ACTORS));self.assertEqual(len(draws),88)
            rows=read_rows(root/'first/seed-0/layers.jsonl')
            broken=deepcopy(rows)
            next(r for r in broken if r.get('verb')=='action_completed')['details']['start_t']=100
            with self.assertRaises(ValueError):completed_evaluation(broken)
            present=deepcopy(rows);absent=deepcopy(rows)
            next(r for r in present if r.get('verb')=='opening_check')['details']['primary_presence']=True
            next(r for r in absent if r.get('verb')=='opening_check')['details']['primary_presence']=False
            self.assertAlmostEqual(completed_evaluation(present)['fitness']-completed_evaluation(absent)['fitness'],.1)
        self.assertEqual(before,input_manifest())

    def test_two_generations_reuse_ga_and_archive_codec(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cfg={'search_adapter':ADAPTER,'out':str(root/'first'),
                'population':2,'generations':2,'ga_seed':3,'seeds':[0],'processes':1}
            archive=_evolve(cfg);cfg['out']=str(root/'second');_evolve(cfg)
            first=json.loads((root/'first/summary.json').read_text())
            second=json.loads((root/'second/summary.json').read_text())
            self.assertEqual(first['source_manifest'],second['source_manifest'])
            self.assertTrue(first['ga_evolution']);self.assertEqual(first['individuals'],4)
            self.assertTrue(any(i['parents'] for i in first['history'][1]))
            self.assertEqual((root/'first/archive.json').read_bytes(),(root/'second/archive.json').read_bytes())
            hashes=lambda s:[r['native_layers_sha256'] for gen in s['history'] for ind in gen for r in ind['runs']]
            self.assertEqual(hashes(first),hashes(second))
            self.assertEqual(Archive.from_dict(archive.to_dict(),genome_codec=ChoiceGenome).to_dict(),archive.to_dict())

    def test_existing_job_paths_use_the_frozen_project_copy(self):
        import shutil
        from scripts.cultural_festival_probe import PROJECT
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);project=root/'copied-project';shutil.copytree(PROJECT,project)
            job={'search_adapter':ADAPTER,'index':0,'generation':0,
                'genome':ChoiceGenome.random(random.Random(0)).to_dict(),'seeds':[0],
                'world_path':str(project/'world.yaml'),'subjects_dir':str(project/'subjects'),
                'expected_input_manifest':input_manifest(), 'out_dir':str(root/'run'),'logical_root':str(root)}
            result=run_individual(job)
            self.assertEqual(result['runs'][0]['input_manifest'],input_manifest())
            (project/'world.yaml').write_text((project/'world.yaml').read_text()+'\n')
            job['out_dir']=str(root/'bad')
            with self.assertRaises(ValueError):run_individual(job)


if __name__=='__main__':unittest.main()
