"""Explicit world selection, old bookmarks, and read-only navigation boundaries."""
import http.client
import unittest
from unittest.mock import patch
import test_workbench_pages as fixture
from viewer import pages, run_browse


class RunEntryTests(unittest.TestCase):
    setUp = fixture.WorkbenchTests.setUp
    _start_server = fixture.WorkbenchTests._start_server
    _cleanup_temp = fixture.WorkbenchTests._cleanup_temp
    get_status = fixture.WorkbenchTests.get_status

    def raw_get(self,path):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=fixture.WorkbenchTests.HTTP_TIMEOUT)
        try:
            conn.request('GET',path)
            response=conn.getresponse()
            return response.status,response.getheader('Location'),response.read()
        finally:
            conn.close()

    def test_bare_and_empty_world_redirect_before_looking_at_running_jobs(self):
        self.fake.add(fixture._job('job-active','run-active','running'))
        with patch('viewer.workbench_pages._run_view',side_effect=AssertionError('must not borrow another world')):
            for path in ['/jobs','/jobs?world=','/jobs?world=&config=']:
                status,location,_=self.raw_get(path)
                self.assertEqual((status,location),(302,'/worlds'))
        self.assertEqual(self.fake.submitted,[])

    def test_unknown_world_or_config_returns_to_world_choice(self):
        for path in ['/jobs?world=not-a-world','/jobs?config=cfg-missing']:
            self.assertEqual(self.raw_get(path)[:2],(302,'/worlds'))
        self.assertEqual(self.fake.submitted,[])

    def test_an_explicit_saved_configuration_keeps_its_world(self):
        status,body,_=self.get_status('/jobs?config=cfg-test')
        self.assertEqual(status,200,body[:200])
        self.assertIn('data-config-id="cfg-test"',body)
        self.assertIn('放課後の約束',body)
        self.assertEqual(self.fake.submitted,[])

    def test_selected_world_does_not_borrow_a_config_from_another_world(self):
        status,body,_=self.get_status('/jobs?world=momotaro')
        self.assertEqual(status,200,body[:200])
        self.assertIn('桃太郎',body)
        self.assertNotIn('data-config-id="cfg-test"',body)
        self.assertEqual(self.fake.submitted,[])

    def test_existing_job_detail_stays_available(self):
        self.fake.add(fixture._job('job-old','run-old','completed'))
        status,body,_=self.get_status('/jobs/job-old')
        self.assertEqual(status,200,body[:200])
        self.assertEqual(self.fake.submitted,[])

    def test_navigation_without_a_world_points_to_worlds(self):
        self.assertEqual(pages._phase_href('run',world=None,run=None,output_run=None,phases=None),'/worlds')
        self.assertEqual(pages._phase_href('run',world={'id':'romance'},run=None,output_run=None,phases=None),'/jobs?world=romance')
        self.assertEqual(pages.next_action_for('example',{'next':'run'})[1],'/worlds')
        self.assertIn('href="/worlds"',run_browse.navigation('conditions'))

    def test_world_list_requires_opening_a_world_and_never_starts_execution(self):
        status,body,_=self.get_status('/worlds')
        self.assertEqual(status,200,body[:200])
        self.assertIn('href="/worlds/romance"',body)
        self.assertNotIn('href="/jobs"',body)
        self.assertNotIn('class="phase-band"',body)
        self.assertEqual(self.fake.submitted,[])
