"""The old helper is retired; world context and recorded results remain intact."""
import http.client
import unittest
import test_workbench_pages as fixture


class WorldReviewTests(unittest.TestCase):
    setUp=fixture.WorkbenchTests.setUp
    _start_server=fixture.WorkbenchTests._start_server
    _cleanup_temp=fixture.WorkbenchTests._cleanup_temp
    get_status=fixture.WorkbenchTests.get_status

    def test_legacy_url_redirects_and_keeps_explicit_purpose(self):
        for path,location in [('/modes','/worlds'),('/modes?mode=review','/worlds?mode=review'),('/modes?mode=create','/worlds?mode=create'),('/modes?mode=bad','/worlds')]:
            conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=30)
            try:
                conn.request('GET',path);res=conn.getresponse();res.read()
                self.assertEqual((res.status,res.getheader('Location')),(302,location))
            finally:conn.close()

    def test_review_is_world_scoped_and_rendering_preserves_saved_inputs(self):
        before=self.configs.get('cfg-test')
        status,body,_=self.get_status('/worlds/romance?view=review')
        self.assertEqual(status,200,body[:200]);self.assertIn('data-world-review="romance"',body)
        self.assertIn('data-purpose-world value="romance"',body)
        self.assertNotIn('<select data-purpose-world',body)
        for attr in ['data-purpose-note','data-purpose-import','data-purpose-result','data-independent-start','data-independent-cancel']:
            self.assertIn(attr,body)
        self.assertNotIn('data-independent-melos-profile',body)
        self.assertIn('cfg-test',body);self.assertEqual(self.configs.get('cfg-test'),before)
        self.assertEqual(self.fake.submitted,[])

    def test_world_page_and_list_offer_normal_entries(self):
        status,body,_=self.get_status('/worlds/romance')
        self.assertEqual(status,200);self.assertIn('data-settings-category="checks"',body)
        self.assertIn('id="wp-work-note"',body)
        status,body,_=self.get_status('/worlds?mode=review')
        self.assertEqual(status,200);self.assertIn('data-home-world',body)
        self.assertNotIn('href="/modes"',body)

    def test_unknown_world_is_not_silently_replaced(self):
        self.assertEqual(self.get_status('/worlds/missing?view=review')[0],404)
