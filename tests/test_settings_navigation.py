import html,json,re,unittest
from pathlib import Path
from execution.library import LibraryStore
from viewer import settings_navigation,world_prototype,world_advanced,world_draft

class SettingsNavigationTests(unittest.TestCase):
    def test_saved_rules_and_draft_share_the_same_category_registry(self):
        repo=Path(__file__).resolve().parents[1];store=LibraryStore(repo)
        world=next(w for w in store.worlds() if w['id']=='momotaro_plus3_new')
        normal=world_prototype.render(world,{},[])
        advanced=world_advanced.render(world,store,None)
        expected=[key for key,_ in settings_navigation.SECTIONS]
        for page in (normal,advanced):
            self.assertEqual(re.findall(r'data-settings-category="([^"]+)"',page),expected)
        draft=world_draft.fragment('mock')
        value=re.search(r'data-settings-sections="([^"]+)"',draft).group(1)
        self.assertEqual(json.loads(html.unescape(value)),[list(row) for row in settings_navigation.SECTIONS])
        self.assertNotIn('詳細設定・設定ファイル',normal)
        self.assertNotIn('aria-label="詳細設定の項目"',advanced)

    def test_world_links_escape_ids_and_keep_the_selected_world(self):
        text=settings_navigation.navigation('a&b')
        self.assertIn('/worlds/a%26b?view=actions',text)
        self.assertIn('/worlds/a%26b#screen=people',text)
        self.assertIn('/worlds/a%26b?view=advanced#files',text)
        self.assertEqual(re.findall(r'data-settings-category="files"',text),['data-settings-category="files"'])
