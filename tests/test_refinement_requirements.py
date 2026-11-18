import unittest
from copy import deepcopy
from test_world_refinements import RefinementTests
from execution.refinement_requirements import validate_requirements,check_coverage
from execution.provenance import ConfigError

class CoverageTests(unittest.TestCase):
    def ledger(self):
        return {'requirements':[{'id':'r1','kind':'route.add','target':'','instruction':'Add outgoing route','required_values':{'from':'A','to':'B'}},{'id':'r2','kind':'route.add','target':'','instruction':'Add return route','required_values':{'from':'B','to':'A'}}], 'unsupported':[], 'clarification':[]}
    def test_settings_requirements_merge_without_losing_instructions(self):
        raw={'requirements':[{'id':'a','kind':'time.update','target':'time','instruction':'days 8','required_values':{}},{'id':'b','kind':'time.update','target':'time','instruction':'slots morning night','required_values':{}}], 'unsupported':[], 'clarification':[]}
        result=validate_requirements(raw)
        self.assertEqual(len(result['requirements']),1)
        self.assertIn('days 8',result['requirements'][0]['instruction']);self.assertIn('slots morning night',result['requirements'][0]['instruction'])
        self.assertEqual(len(raw['requirements']),2)
        for row in raw['requirements']: row['instruction']='x'*1100
        result=validate_requirements(raw); self.assertEqual(validate_requirements(result),result)
        check_coverage(raw,{'operations':[{'kind':'time.update','target':'time','values':{'days':8,'slots':['morning','night']}}]})
    def test_two_directions(self):
        p={'operations':[{'kind':'route.add','target':'','values':{'from':'B','to':'A'}},{'kind':'route.add','target':'','values':{'from':'A','to':'B'}}]}
        check_coverage(self.ledger(),p)
        p['operations'].pop()
        with self.assertRaises(ConfigError): check_coverage(self.ledger(),p)
    def test_same_direction_cannot_cover_both(self):
        p={'operations':[{'kind':'route.add','target':'','values':{'from':'A','to':'B'}}]*2}
        with self.assertRaises(ConfigError): check_coverage(self.ledger(),p)
    def test_unknown_and_empty(self):
        for update in ({'requirements':[]},{'unsupported':['arbitrary rule']},{'clarification':['which person?']}):
            r=self.ledger();r.update(update)
            with self.assertRaises(ConfigError): validate_requirements(r)

class SpanTests(unittest.TestCase):
    def test_lossless_bounded_segments(self):
        from execution.refinement_requirements import request_spans
        for prompt in ['Enrich guide. Add Harbor. Preserve goals.', 'a。b！c？', 'Version 3.8 stays. Change place.', 'x. '*100, '\nA\nB\n', '']:
            spans=request_spans(prompt);self.assertEqual(''.join(spans),prompt);self.assertLessEqual(len(spans),12)
    def test_same_person_across_spans_merged(self):
        from execution.refinement_requirements import combine_spans
        req={'id':'x','kind':'person.update_profile','target':'A','instruction':'enrich description','required_values':{}}
        row={'disposition':'change','reason':'requested','requirements':[req],'unsupported':[],'clarification':[]}
        row2=deepcopy(row);row2['requirements'][0]['instruction']='describe as dependable'
        result=combine_spans({'spans':{'s0':row,'s1':row2}},['one','two'])
        self.assertEqual(len(result['requirements']),1);self.assertIn('dependable',result['requirements'][0]['instruction'])
        check_coverage(result,{'operations':[{'kind':'person.update_profile','target':'A','values':{'description':'dependable guide'}}]})
    def test_missing_span_rejected(self):
        from execution.refinement_requirements import combine_spans
        with self.assertRaises(ConfigError):combine_spans({'spans':{}},['first','second'])
    def test_preserve_cannot_hide_operations(self):
        from execution.refinement_requirements import combine_spans
        row={'disposition':'preserve','reason':'keep','requirements':[{}],'unsupported':[],'clarification':[]}
        with self.assertRaises(ConfigError):combine_spans({'spans':{'s0':row}},['keep'])
    def test_all_spans_combined_and_ids_namespaced(self):
        from execution.refinement_requirements import combine_spans
        req={'id':'same','kind':'person.update_profile','target':'A','instruction':'enrich','required_values':{}}
        row={'disposition':'change','reason':'requested','requirements':[req],'unsupported':[],'clarification':[]}
        keep={'disposition':'preserve','reason':'keep goals','requirements':[],'unsupported':[],'clarification':[]}
        row2=deepcopy(row);row2['requirements'][0]['target']='B'
        result=combine_spans({'spans':{'s0':row,'s1':row2,'s2':keep}},['one','two','keep'])
        self.assertEqual([r['id'] for r in result['requirements']],['s0r0','s1r0'])

class DecompositionTests(RefinementTests):
    # Inherit fixture only, not base tests (filtered below).
    def test_three_reservations_and_replay(self):
        stages=[]
        class Planner:
            def decompose(inner,record,inputs,caps):
                stages.append(self.svc.get(self.token,self.rid)['model_calls_reserved']);return {'requirements':['fixture']}
            def __call__(inner,*args):
                stages.append(self.svc.get(self.token,self.rid)['model_calls_reserved']);return self.plan
            def review(inner,record,inputs,candidate):
                stages.append(self.svc.get(self.token,self.rid)['model_calls_reserved'])
                return {'overview_matches':True,'issues':[],'goals':[{'person':p['name'],'matches':True,'reason':'Preserved'} for p in candidate['people']]}
        self.svc.planner=Planner();p=self.preview();self.assertEqual(p['state'],'ready',p);self.assertEqual(stages,[1,2,3]);self.preview();self.assertEqual(stages,[1,2,3])
    def test_cancel_after_decomposition_no_generate(self):
        class Planner:
            def decompose(inner,*args):
                self.svc.cancel(self.token,self.rid);return {'requirements':['fixture']}
            def __call__(inner,*args):raise AssertionError('Cancelled')
        self.svc.planner=Planner();self.assertEqual(self.preview()['state'],'cancelled')
    def test_unknown_decomposition_no_replay(self):
        stages=[]
        class Planner:
            def decompose(inner,*args):stages.append(1);raise TimeoutError()
            def __call__(inner,*args):raise AssertionError('Unknown')
        self.svc.planner=Planner();self.assertEqual(self.preview()['state'],'unknown');self.preview();self.assertEqual(stages,[1])

# Reuse fixture without duplicating the full inherited suite.
for name in list(vars(RefinementTests)):
    if name.startswith('test_') and name not in vars(DecompositionTests):setattr(DecompositionTests,name,None)
del RefinementTests
if __name__=='__main__':unittest.main()
