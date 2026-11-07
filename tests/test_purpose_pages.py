from pathlib import Path
import tempfile, unittest
from viewer import data, pages, purpose_pages, server

class PurposePagesTests(unittest.TestCase):
    def test_shared_purpose_context_is_read_only_and_keeps_navigation(self):
        html=pages.document('テスト','<input name="draft">',show_phase_band=False)
        self.assertIn('data-purpose-label',html)
        self.assertNotIn('<select data-purpose ',html)
        self.assertNotIn('class="purpose-picker"',html)
        self.assertIn('<input name="draft">',html)
        self.assertIn('/static/purpose-workspace.js',html)
        self.assertIn('/static/purpose-workspace.css',html)
        self.assertIn('href="/history"',html)
        self.assertNotIn('目的の作業画面',html)
        self.assertNotIn('href="/modes',html)
        self.assertNotIn('class="purpose-workspace"',html)

    def test_only_initial_landing_offers_purpose_choices(self):
        landing=purpose_pages.landing()
        self.assertIn('未選択',landing)
        self.assertIn('data-purpose-link="create"',landing)
        self.assertIn('data-purpose-link="review"',landing)
        self.assertNotIn('data-purpose-label',landing)
        self.assertNotIn('class="purpose-workspace"',landing)
        self.assertIn('/worlds?mode=create',landing)
        self.assertIn('/worlds?mode=review',landing)
        self.assertNotIn('/modes',purpose_pages.choices())
        with tempfile.TemporaryDirectory() as temp:
            work=purpose_pages.render(data.RunRepository(Path(temp)))
        self.assertNotIn('data-purpose-link=',work)
        self.assertNotIn('目的を切り替えても',work)

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
