"""Optional metadata must preserve numeric relations and safe edit contracts."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import yaml
from execution.library import LibraryStore
from execution.provenance import ConfigError
from execution import world_editor
from engine.relations import Relations


class RelationPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='relation-test-')
        self.addCleanup(self.temp.cleanup)
        self.repo=Path(self.temp.name)/'repo'
        base=self.repo/'projects'/'test'
        (base/'subjects').mkdir(parents=True)
        self.base=base
        (base/'world.yaml').write_text(yaml.safe_dump({'name':'Test','zones':['村'],'unknown':{'kept':True}}),encoding='utf-8')
        self.a={'id':'A','description':'before','range':{'entry':'村'},'traits':{'social':.7},'relations':{'B':{'affinity':.9,'awareness':0,'extra':{'keep':1}}}}
        self.b={'id':'B','relations':{'A':{'affinity':-.4,'awareness':.2}}}
        for filename,p in [('a',self.a),('b',self.b)]:
            (base/'subjects'/f'{filename}.yaml').write_text(yaml.safe_dump(p,allow_unicode=True),encoding='utf-8')
        self.store=LibraryStore(self.repo)

    def current(self):return world_editor.snapshot(self.store,'test')
    def save(self,op,values,target=None,revision=None):
        return world_editor.save(self.store,'test',{'revision':revision or self.current()['revision'],'operation':op,'target':target,'values':values})
    def bytes(self):return {p.relative_to(self.base).as_posix():p.read_bytes() for p in self.base.rglob('*.yaml')}
    def person(self,id):return next(p for p in self.current()['people'] if p['id']==id)

    def test_affiliation_roundtrip_and_legacy_update_preserve(self):
        fields={'description':'after','personality':'gentle','target':'','deliver_to':''}
        before=self.bytes()
        self.save('person',{**fields,'affiliation':'旅の仲間'},'A')
        self.assertEqual(self.person('A')['affiliation'],'旅の仲間')
        self.assertEqual(self.person('A')['relations'],self.a['relations'])
        self.save('person',fields,'A')
        self.assertEqual(self.person('A')['affiliation'],'旅の仲間')
        self.save('person',{**fields,'affiliation':''},'A')
        self.assertNotIn('affiliation',self.person('A'))
        self.assertEqual(before['world.yaml'],self.bytes()['world.yaml'])
        self.assertEqual(before['subjects/b.yaml'],self.bytes()['subjects/b.yaml'])

    def test_affiliation_add_and_legacy_add(self):
        self.save('add-person',{'name':'C','description':'','entry':'村','affiliation':'村'})
        self.assertEqual(self.person('C')['affiliation'],'村')
        self.save('add-person',{'name':'D','description':'','entry':''})
        self.assertNotIn('affiliation',self.person('D'))

    def test_labels_are_directed_and_only_label_changes(self):
        before=self.bytes()
        self.save('relation',{'label':'仲間'},{'source':'A','target':'B'})
        self.assertEqual(self.person('A')['relations']['B'],{**self.a['relations']['B'],'label':'仲間'})
        self.assertEqual(self.bytes()['subjects/b.yaml'],before['subjects/b.yaml'])
        self.save('relation',{'label':'脅威'},{'source':'B','target':'A'})
        self.assertEqual(self.person('A')['relations']['B']['label'],'仲間')
        self.assertEqual(self.person('B')['relations']['A']['label'],'脅威')
        self.save('relation',{'label':''},{'source':'A','target':'B'})
        self.assertEqual(self.person('A'),self.a)
        self.assertEqual(self.bytes()['world.yaml'],before['world.yaml'])
        # The simulation consumes only numeric keys; optional labels cannot
        # enter affinity calculations or change the numeric snapshot.
        labeled=deepcopy(self.a);labeled['relations']['B']['label']='家族'
        self.assertEqual(Relations({'A':self.a['relations'],'B':self.b['relations']}).snapshot(),Relations({'A':labeled['relations'],'B':self.b['relations']}).snapshot())

    def test_saved_metadata_can_initialize_the_real_simulation(self):
        import shutil
        from engine.world import World
        from engine.subject import Subject
        from engine.sim import Simulation
        root=Path(__file__).resolve().parents[1]
        project=self.repo/'projects/momotaro'
        shutil.copytree(root/'projects/momotaro',project)
        graph=root/'templates/momotaro/action_graph.yaml'
        def load():
            world=World.from_yaml(project/'world.yaml',action_graph_path=graph)
            people={s.id:s for s in [Subject.from_yaml(p) for p in sorted((project/'subjects').glob('*.yaml'))]}
            Simulation(123,world,people,Path(self.temp.name)/'unused-simulation-output')
            return world.relations.snapshot()
        before=load()
        snap=world_editor.snapshot(self.store,'momotaro')
        monkey=next(p for p in snap['people'] if p['id']=='猿')
        world_editor.save(self.store,'momotaro',{'revision':snap['revision'],'operation':'person','target':'猿',
            'values':{'description':monkey.get('description',''),'personality':monkey.get('personality',''),
                      'target':monkey.get('goal',{}).get('target',''),'deliver_to':'','affiliation':'旅の仲間'}})
        snap=world_editor.snapshot(self.store,'momotaro')
        world_editor.save(self.store,'momotaro',{'revision':snap['revision'],'operation':'relation',
            'target':{'source':'桃太郎','target':'犬'},'values':{'label':'仲間'}})
        self.assertEqual(load(),before,'metadata must not change simulation numeric relations')
        self.assertFalse((Path(self.temp.name)/'unused-simulation-output').exists(),'no generation started')

    def test_invalid_targets_and_labels_never_write(self):
        cases=[('relation',{'label':'x'},None),('relation',{'label':'x'},{'source':'A','target':'missing'}),
               ('relation',{'label':'x'},{'source':'A','target':'A'}),('relation',{'label':'x'},{'source':[],'target':'B'}),
               ('relation',{'label':'x'},{'source':'../A','target':'B'}),('relation',{'label':7},{'source':'A','target':'B'}),
               ('relation',{'label':'x'*121},{'source':'A','target':'B'}),('relation',{'label':'x','affinity':1},{'source':'A','target':'B'})]
        for op,values,target in cases:
            with self.subTest(target=target,values=values):
                before=self.bytes()
                with self.assertRaises(ConfigError):self.save(op,values,target)
                self.assertEqual(self.bytes(),before)

    def test_missing_direction_rejected(self):
        self.save('add-person',{'name':'C','description':'','entry':''})
        before=self.bytes()
        with self.assertRaises(ConfigError):self.save('relation',{'label':'new'},{'source':'A','target':'C'})
        self.assertEqual(self.bytes(),before)

    def test_string_boundaries_and_bad_affiliation(self):
        fields={'description':'','personality':'','target':'','deliver_to':''}
        self.save('person',{**fields,'affiliation':'あ'*120},'A')
        self.save('relation',{'label':'あ'*120},{'source':'A','target':'B'})
        for affiliation in [None,42,[],{},'あ'*121]:
            with self.subTest(affiliation=affiliation):
                before=self.bytes()
                with self.assertRaises(ConfigError):self.save('person',{**fields,'affiliation':affiliation},'A')
                self.assertEqual(self.bytes(),before)
        with self.assertRaises(ConfigError):self.save('person',{**fields,'unknown':'x'},'A')

    def test_stale_revision_and_atomic_failure(self):
        rev=self.current()['revision']
        self.save('relation',{'label':'仲間'},{'source':'A','target':'B'})
        before=self.bytes()
        with self.assertRaises(ConfigError) as err:self.save('relation',{'label':'敵対'},{'source':'A','target':'B'},rev)
        self.assertEqual(err.exception.code,'conflict')
        self.assertEqual(self.bytes(),before)
        with patch('execution.library.os.replace',side_effect=OSError('test disk failure')):
            with self.assertRaises(OSError):self.save('relation',{'label':'敵対'},{'source':'A','target':'B'})
        self.assertEqual(self.bytes(),before)

if __name__=='__main__':unittest.main()
