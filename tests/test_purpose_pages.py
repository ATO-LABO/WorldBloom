from pathlib import Path
import tempfile, unittest
from viewer import data, pages, purpose_pages, server

class PurposePagesTests(unittest.TestCase):
    def test_shared_selector_keeps_existing_navigation(self):
        html=pages.document('テスト','<input name="draft">',show_phase_band=False)
        self.assertIn('data-purpose',html)
        self.assertIn('<input name="draft">',html)
        self.assertIn('/static/purpose-workspace.js',html)
        self.assertIn('/static/purpose-workspace.css',html)
        self.assertIn('href="/history"',html)

    def test_read_only_page_does_not_claim_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            repo=data.RunRepository(Path(temp))
            html=purpose_pages.render(repo)
        for text in ['新規レビュー実行は未接続','data-purpose-cancel disabled','data-purpose-panel="create"','data-purpose-panel="review"','物語制作','既存物語レビュー']:
            self.assertIn(text,html)
        self.assertNotIn('data-purpose-run',html)
        self.assertNotIn('期限内帰還100',html)

    def test_assets_are_explicitly_allowed(self):
        for name in ['purpose-workspace.js','purpose-workspace.css']:
            self.assertTrue(server.static_path(name).is_file())
        self.assertIn('if parts == ["modes"]:',Path(server.__file__).read_text(encoding='utf-8'))

if __name__=='__main__': unittest.main()
