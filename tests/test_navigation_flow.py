"""Two explicit choices, no generation or mutation from navigation."""
import unittest
import test_world_drafts as fixtures

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

    def test_creation_choice_has_no_forms_and_each_method_has_only_its_form(self):
        status,choice=self.request('/worlds/new')
        self.assertEqual(status,200)
        self.assertIn(b'method=manual',choice)
        self.assertIn(b'method=ai',choice)
        self.assertNotIn(b'data-create-form',choice)
        self.assertNotIn(b'data-wd-input',choice)
        status,manual=self.request('/worlds/new?method=manual&mode=review')
        self.assertEqual(status,200)
        self.assertIn(b'data-create-form',manual)
        self.assertNotIn(b'data-world-draft',manual)
        self.assertNotIn(b'wd-legacy',manual)
        status,ai=self.request('/worlds/new?method=ai&mode=review')
        self.assertEqual(status,200)
        self.assertIn(b'data-wd-input',ai)
        self.assertNotIn(b'data-create-form',ai)
        self.assertIn('作成方法を選び直す'.encode(),ai)
        self.assertEqual(self.calls,0)
        self.assertFalse((self.repo/'projects').exists())

    def test_invalid_method_is_not_a_fallback_to_a_form(self):
        status,_=self.request('/worlds/new?method=unknown')
        self.assertEqual(status,400)
        self.assertEqual(self.calls,0)

if __name__=='__main__':unittest.main()
