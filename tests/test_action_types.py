"""Current display bundles have no stored selection or implicit actor grants."""
from pathlib import Path
import tempfile,unittest
from engine import action_types
from engine.phase2 import available_verbs
from test_action_usage import load_fixture

class ActionTypeTests(unittest.TestCase):
    def test_catalog_has_unique_action_owners_and_no_actor_grants(self):
        rows=action_types.catalog();ids=[r['id'] for r in rows]
        self.assertEqual(len(ids),len(set(ids)))
        verbs=[v for r in rows for v in r['actions']]
        self.assertEqual(len(verbs),len(set(verbs)))
        self.assertFalse(any('grant_actions' in r for r in rows))
        for row in rows:
            for verb in row['actions']:self.assertEqual(action_types.owner(verb)['id'],row['id'])

    def test_registry_is_part_of_frozen_execution_closure(self):
        from execution.provenance import code_snapshot
        from engine.sim import _engine_source_hash
        root=Path(__file__).resolve().parents[1];blobs,_=code_snapshot(root)
        self.assertEqual(blobs['engine/action_types.json'],(root/'engine/action_types.json').read_bytes())
        self.assertNotIn('engine/story_genres.json',blobs)
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);(folder/'example.py').write_text('example')
            data=folder/'action_types.json';data.write_text('[]');_engine_source_hash.cache_clear()
            before=_engine_source_hash(folder);data.write_text('[{}]');_engine_source_hash.cache_clear()
            self.assertNotEqual(before,_engine_source_hash(folder))

    def test_bundles_derive_from_actual_individual_values(self):
        raw={'action_usage':{'enabled':['verb:labor']},'mechanics':{'budgeted_purchases':False}}
        self.assertIn('economy',action_types.for_world(raw))
        raw['action_usage']['enabled']=[];raw['mechanics']['budgeted_purchases']=True
        self.assertIn('economy',action_types.for_world(raw))
        raw['mechanics']['budgeted_purchases']=False
        self.assertNotIn('economy',action_types.for_world(raw))

    def test_enabled_world_action_does_not_grant_person_ability(self):
        world,people=load_fixture();a=next(iter(people.values()))
        world.action_usage['enabled'].append('verb:start_relationship')
        world.action_usage['constraints']['verb:start_relationship']['actors']=None
        a.verbs.discard('start_relationship')
        self.assertNotIn('start_relationship',available_verbs(world,a,turn=1,day=1))
        self.assertEqual(world.global_action_grants(),set())

if __name__=='__main__':unittest.main()
