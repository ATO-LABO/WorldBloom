"""Navigation and entry routes do not generate or save world data."""
import unittest
import test_world_drafts as fixtures
from test_creation_footer import Nodes

class NavigationTests(unittest.TestCase):
    setUp=fixtures.DraftHTTPTests.setUp
    shutdown=fixtures.DraftHTTPTests.shutdown
    request=fixtures.DraftHTTPTests.request
    def test_purpose_landing_then_worlds_are_distinct_pages(self):
        status,home=self.request('/')
        self.assertEqual(status,200)
        self.assertIn('今日は何を進めますか'.encode(),home)
        self.assertIn(b'/worlds?mode=create',home)
        self.assertIn(b'/worlds?mode=review',home)
        self.assertNotIn(b'data-home-library',home)
        status,worlds=self.request('/worlds?mode=review')
        self.assertEqual(status,200)
        self.assertIn(b'data-home-library',worlds)
        self.assertIn('目的を選び直す'.encode(),worlds)
        self.assertNotIn('今日は何を進めますか'.encode(),worlds)
        self.assertEqual(self.calls,0)

    def test_creation_choices_stay_separate_from_the_shared_preparation_form(self):
        before = {path.relative_to(self.repo): path.read_bytes()
                  for path in self.repo.rglob('*') if path.is_file()}
        for route in ('/worlds/new', '/worlds/new?method=manual&mode=review',
                      '/worlds/new?method=ai&mode=review'):
            with self.subTest(route=route):
                status, page = self.request(route)
                self.assertEqual(status, 200)
                parsed = Nodes()
                parsed.feed(page.decode('utf-8'))
                choices = [attrs['data-cw-enter'] for _, attrs, ancestors in parsed.nodes
                           if 'data-cw-enter' in attrs and
                           any(parent.get('data-cw-screen') == 'entry' for _, parent in ancestors)]
                self.assertEqual(choices, ['world-import', 'zip-import', 'resume', 'new'])
                forms = [ancestors for tag, _, ancestors in parsed.nodes if tag == 'form']
                self.assertFalse(any(parent.get('data-cw-screen') == 'entry'
                                     for ancestors in forms for _, parent in ancestors))
                preparation = [ancestors for tag, attrs, ancestors in parsed.nodes
                               if tag == 'form' and 'data-cw-prepare' in attrs]
                self.assertEqual(len(preparation), 1)
                prepare = preparation[0]
                self.assertTrue(any('data-cw-workflow' in parent and 'hidden' in parent
                                    for _, parent in prepare))
                self.assertTrue(any(parent.get('data-cw-panel') == '1' for _, parent in prepare))
                self.assertNotIn(b'data-create-form', page)
                self.assertNotIn(b'data-wd-input', page)
        self.assertEqual(self.calls, 0)
        self.assertEqual(before, {path.relative_to(self.repo): path.read_bytes()
                                  for path in self.repo.rglob('*') if path.is_file()})

    def test_invalid_method_is_not_a_fallback_to_a_form(self):
        status,_=self.request('/worlds/new?method=unknown')
        self.assertEqual(status,400)
        self.assertEqual(self.calls,0)

if __name__=='__main__':unittest.main()
