"""Dependencies spanning frozen cultural input, route survey, and Linux hosting."""
from pathlib import Path
import shutil
import tempfile
import unittest

from execution.configs import ConfigStore,normalize,_capture_inputs
from execution.provenance import ConfigError
from saved_world_fixtures import install_current_worlds

ROOT=Path(__file__).resolve().parents[1]


class ThreeWayIntegrationTests(unittest.TestCase):
    def test_cultural_festival_legacy_world_is_rejected_before_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = normalize({'label':'文化祭統合点検','project_id':'cultural_festival','template_id':'basic',
                'evolution':{'generations':1,'population':2,'seeds':1}})
            store = ConfigStore(ROOT, root/'control', root/'runs')
            for load in (lambda: store.preview(spec), lambda: _capture_inputs(ROOT, spec)):
                with self.assertRaisesRegex(ConfigError, '旧世界形式'):
                    load()
            self.assertFalse(store.control.exists())
            self.assertFalse(store.runs.exists())

    def test_project_route_goal_survives_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);repo=root/'repo';(repo/'projects').mkdir(parents=True)
            shutil.copytree(ROOT/'samples/route_goals/cultural_festival',repo/'projects/festival')
            shutil.copytree(ROOT/'templates/basic',repo/'templates/basic')
            shutil.copytree(repo/'templates/basic',repo/'templates/festival')
            install_current_worlds(repo, 'festival')
            spec=normalize({'label':'経路凍結','project_id':'festival','template_id':'basic','evolution':{}})
            blobs,_=_capture_inputs(repo,spec)
            self.assertEqual(blobs['projects/festival/route.yaml'],(repo/'projects/festival/route.yaml').read_bytes())

    def test_contract_escape_is_rejected_before_read(self):
        import yaml
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp);shutil.copytree(ROOT/'projects',repo/'projects');shutil.copytree(ROOT/'templates/basic',repo/'templates/basic')
            path=repo/'projects/cultural_festival/world.yaml';raw=yaml.safe_load(path.read_text())
            raw['slot_execution']['contract']='../other.yaml';path.write_text(yaml.safe_dump(raw,allow_unicode=True))
            spec=normalize({'label':'拒否','project_id':'cultural_festival','template_id':'basic','evolution':{}})
            with self.assertRaises(ConfigError):_capture_inputs(repo,spec)


if __name__=='__main__':unittest.main()
