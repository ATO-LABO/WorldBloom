"""Actual HTTP write boundary for design sidecars (never run configurations)."""
from copy import deepcopy
import unittest
from execution import time_settings
from execution.provenance import ConfigError
import test_library as fixtures
from unittest.mock import patch


class TimeSettingsHttpTests(unittest.TestCase):
    http=fixtures.LibraryHttpBoundaryTests.http
    get=fixtures.LibraryHttpBoundaryTests.get
    setUp=fixtures.LibraryHttpBoundaryTests.setUp

    def body(self):
        status,snapshot=self.http('GET','/api/worlds/momotaro/time-settings',{})
        self.assertEqual(status,200,snapshot)
        return {'revision':snapshot['revision'],'settings':snapshot['settings']}

    def test_save_reload_invalid_values_stale_conflict_and_preserve_yaml(self):
        url='/api/worlds/momotaro/time-settings'
        project=self.repo/'projects/momotaro'
        original={p.relative_to(project).as_posix():p.read_bytes() for p in project.rglob('*.yaml')}
        body=self.body();body['settings']['normal']['bands'][0]['interval_minutes']=0
        status,result=self.http('POST',url,body)
        self.assertEqual(status,200,result);self.assertFalse(result['validation']['valid'])
        self.assertEqual(result,self.http('GET',url,{})[1])
        self.assertFalse(result['execution_ready']);self.assertFalse(result['engine_supported'])
        self.assertEqual(original,{p.relative_to(project).as_posix():p.read_bytes() for p in project.rglob('*.yaml')})
        before=(project/time_settings.DRAFT).read_bytes()
        status,result=self.http('POST',url,body)
        self.assertEqual(status,409,result);self.assertEqual(result['code'],'conflict')
        self.assertEqual(before,(project/time_settings.DRAFT).read_bytes())

    def test_boundary_requires_client_header_and_rejects_bad_envelope(self):
        body=self.body();url='/api/worlds/momotaro/time-settings'
        self.assertEqual(self.http('POST',url,body,client_header=False)[0],403)
        self.assertEqual(self.http('POST',url,{**body,'path':'../world.yaml'})[0],400)
        self.assertFalse((self.repo/'projects/momotaro'/time_settings.DRAFT).exists())
        for action in ('validate','archive_design','finalize'):
            self.assertEqual(self.http('POST',url+'/'+action,body,client_header=False)[0],403)

    def test_execution_finalize_refuses_and_design_versions_are_immutable(self):
        url='/api/worlds/momotaro/time-settings';body=self.body()
        body['settings']['period']={'start_date':'2026-10-12','end_date':'2026-10-13'}
        status,result=self.http('POST',url+'/validate',body)
        self.assertEqual(status,200,result);self.assertTrue(result['validation']['valid'])
        status,result=self.http('POST',url+'/finalize',body)
        self.assertEqual(status,422,result);self.assertEqual(result['code'],'unsupported')
        project=self.repo/'projects/momotaro'
        self.assertFalse((project/time_settings.DRAFT).exists())
        status,result=self.http('POST',url+'/archive_design',body)
        self.assertEqual(status,200,result);self.assertFalse(result['version']['execution_ready'])
        version_path=project/time_settings.VERSION_DIR/(result['version']['id']+'.json');before=version_path.read_bytes()
        status,again=self.http('POST',url+'/archive_design',body)
        self.assertEqual(status,200,again);self.assertEqual(version_path.read_bytes(),before)
        self.assertEqual(result['version'],again['version'])

    def test_readonly_get_validate_and_write_refusal(self):
        body=self.body();self.server.job_store=None
        with patch('viewer.data.ROOT',self.repo):
            self.assertEqual(self.http('GET','/api/worlds/momotaro/time-settings',{})[0],200)
            self.assertEqual(self.http('POST','/api/worlds/momotaro/time-settings/validate',body)[0],200)
            for suffix in ('','/archive_design','/finalize'):
                status,result=self.http('POST','/api/worlds/momotaro/time-settings'+suffix,body)
                self.assertEqual(status,503,result)
        self.assertFalse((self.repo/'projects/momotaro'/time_settings.DRAFT).exists())

    def test_existing_world_page_and_new_static_assets_are_served(self):
        status,text=self.get('/worlds/momotaro')
        self.assertEqual(status,200);self.assertIn('data-screen="time"',text);self.assertIn('/static/time-settings.js',text)
        for asset in ('time-settings.js','time-settings.css'):
            self.assertEqual(self.get('/static/'+asset)[0],200)

if __name__=='__main__':unittest.main()
