import json
import unittest
from uuid import uuid4
import test_world_drafts as fixtures

class RefinementHTTPTests(unittest.TestCase):
    setUp = fixtures.DraftHTTPTests.setUp
    shutdown = fixtures.DraftHTTPTests.shutdown
    request = fixtures.DraftHTTPTests.request
    def test_preview_get_apply_and_boundaries(self):
        status, raw = self.request('/api/world-drafts',{'request_id':self.token,'name':'QA','overview':'A delivery'})
        self.assertEqual(status,200,raw); base=json.loads(raw)
        calls=[]
        def planner(record, inputs, caps):
            calls.append(inputs)
            return {'intent':'Richer profile','operations':[{'id':'p1','kind':'person.update_profile','target':base['draft']['people'][1]['name'],'values':{'description':'HTTP test profile'},'depends_on':[],'reason':'Requested'}],'unsupported':[],'clarification':[]}
        planner.review=lambda record,inputs,candidate:{'overview_matches':True,'issues':[],'goals':[{'person':p['name'],'matches':True,'reason':'Preserved'} for p in candidate['people']]}
        self.store.provider.refinement_factory=lambda:planner
        rid=uuid4().hex; url='/api/world-drafts/'+self.token
        request={'revision':base['revision'],'request_id':rid,'mode':'prompt','prompt':'Enrich guide','preset':None,'targets':[]}
        self.assertEqual(self.request(url+'/refine',request,client=False)[0],403)
        self.assertEqual(self.request(url+'/refine',{**request,'candidate':{}})[0],400)
        status, raw=self.request(url+'/refine',request); self.assertEqual(status,200,raw); proposal=json.loads(raw)
        self.assertEqual(proposal['state'],'ready',proposal)
        self.store.provider.refinement_factory=lambda:(_ for _ in ()).throw(AssertionError('No factory on replay'))
        self.assertEqual(self.request(url+'/refine',request)[0],200)
        self.assertEqual(self.request(url+'/refinements/'+rid)[0],200)
        self.assertEqual(self.request(url+'/refinements/capabilities')[0],200)
        status, raw=self.request(url+'/apply-refinement',{'revision':base['revision'],'request_id':rid,'candidate_id':proposal['candidate_id'],'candidate_hash':proposal['candidate_hash'],'acknowledged_warnings':proposal['warnings']})
        self.assertEqual(status,200,raw); self.assertEqual(json.loads(raw)['draft'],proposal['candidate'])
        self.assertEqual(len(calls),1)
        self.assertFalse((self.repo/'projects').exists())

if __name__=='__main__':unittest.main()
