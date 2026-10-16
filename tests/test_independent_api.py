from types import SimpleNamespace
from email.message import Message
from copy import deepcopy
from unittest.mock import Mock
import unittest
from viewer.independent_api import dispatch

class Handler:
    def __init__(self,jobs,path,body=None):
        self.server=SimpleNamespace(job_store=jobs,server_port=8000)
        self.client_address=('127.0.0.1',1000);self.path=path;self.connection=Mock()
        self.headers=Message();self.headers['Host']='127.0.0.1:8000';self.headers['Origin']='http://127.0.0.1:8000'
        self.headers['X-WorldBloom-Client']='1';self.headers['Content-Type']='application/json';self.headers['Content-Length']='2'
        self.body=body or {};self.reply=None
    def _request_json(self):return deepcopy(self.body)
    def _send_json(self,status,value):self.reply=(int(status),deepcopy(value))

class RouteTests(unittest.TestCase):
    def test_start_progress_cancel_result_use_same_job_identity(self):
        jobs=Mock();jobs.submit.return_value=({'job_id':'job-one','state':'queued'},True)
        h=Handler(jobs,'/api/independent/jobs',{'request_id':'request-one','kind':'independent_trials'})
        self.assertTrue(dispatch(h,['api','independent','jobs'],'POST'));self.assertEqual(h.reply[0],202)
        jobs.get.return_value={'job_id':'job-one','state':'running','kind':'independent_trials'}
        h=Handler(jobs,'/api/independent/jobs/job-one');dispatch(h,['api','independent','jobs','job-one'],'GET')
        jobs.get.assert_called_once_with('job-one')
        jobs.cancel.return_value={'job_id':'job-one','state':'stopping','termination_confirmed':False}
        h=Handler(jobs,'/api/independent/jobs/job-one/cancel');dispatch(h,['api','independent','jobs','job-one','cancel'],'POST')
        self.assertFalse(h.reply[1]['termination_confirmed']);jobs.cancel.assert_called_once_with('job-one')
        jobs.independent_result.return_value={'id':'job-one','schema':'worldbloom_independent_v2'}
        h=Handler(jobs,'/api/independent/jobs/job-one/result');dispatch(h,['api','independent','jobs','job-one','result'],'GET')
        jobs.independent_result.assert_called_once_with('job-one')
    def test_cross_origin_and_missing_client_header_never_launch(self):
        for field,value in [('Origin','http://evil.invalid'),('X-WorldBloom-Client','0')]:
            jobs=Mock();h=Handler(jobs,'/api/independent/jobs')
            h.headers.replace_header(field,value)
            dispatch(h,['api','independent','jobs'],'POST')
            self.assertEqual(h.reply[0],403);jobs.submit.assert_not_called()
    def test_cancel_rejects_mutable_payload(self):
        jobs=Mock();h=Handler(jobs,'/api/independent/jobs/job-one/cancel',{'new_world':'other'})
        dispatch(h,['api','independent','jobs','job-one','cancel'],'POST')
        self.assertEqual(h.reply[0],400);jobs.cancel.assert_not_called()
    def test_other_job_kinds_cannot_launch_or_cancel_through_this_route(self):
        jobs=Mock();h=Handler(jobs,'/api/independent/jobs',{'kind':'generate'})
        dispatch(h,['api','independent','jobs'],'POST');self.assertEqual(h.reply[0],400);jobs.submit.assert_not_called()
        jobs.get.return_value={'kind':'evolve','job_id':'job-other'}
        h=Handler(jobs,'/api/independent/jobs/job-other/cancel')
        dispatch(h,['api','independent','jobs','job-other','cancel'],'POST');self.assertEqual(h.reply[0],400);jobs.cancel.assert_not_called()
