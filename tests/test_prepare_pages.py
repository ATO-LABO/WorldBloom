import tempfile
import unittest
from pathlib import Path
from scripts.prepare_pages import prepare, viewer_index, synopsis_text, PublicationError

class PagesPreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pub = self.root / "published"; self.pub.mkdir()
        (self.pub / "stories").mkdir(); (self.pub / "docs").mkdir()
        (self.pub / "index.html").write_text('<a href="stories/momotaro-I-low.html">Read</a>')
        (self.pub / "stories/momotaro-I-low.html").write_text('<a href="../index.html">Back</a><section><p>公開済み &amp; 安全</p></section><section class="story-body">本文</section>', encoding="utf-8")
        (self.pub / "docs/index.html").write_bytes(b"docs unchanged")
        (self.pub / ".nojekyll").write_bytes(b"")
        self.out = self.root / "stage"
    def test_prepare_preserves_docs_and_story_except_back(self):
        before = (self.pub / "stories/momotaro-I-low.html").read_text()
        report = prepare(self.pub, self.out)
        self.assertTrue(report["docs_unchanged"])
        self.assertEqual((self.out / "docs/index.html").read_bytes(), b"docs unchanged")
        self.assertEqual((self.pub / "stories/momotaro-I-low.html").read_text(), before)
        self.assertEqual((self.out / "stories/momotaro-I-low.html").read_text(), before.replace('../index.html', '../viewer/'))
        self.assertIn('href="../stories/', (self.out / 'viewer/index.html').read_text())
        self.assertIn('公開済み &amp; 安全', (self.out / 'index.html').read_text())
    def test_existing_output_is_untouched(self):
        self.out.mkdir(); sentinel = self.out / 'saved'; sentinel.write_text('keep')
        with self.assertRaises(PublicationError): prepare(self.pub, self.out)
        self.assertEqual(sentinel.read_text(), 'keep')
    def test_source_overlap_rejected(self):
        with self.assertRaises(PublicationError): prepare(self.pub, self.pub / 'out')
    def test_symlink_input_rejected(self):
        (self.pub / 'link').symlink_to(self.pub / 'index.html')
        with self.assertRaises(PublicationError): prepare(self.pub, self.out)
        self.assertFalse(self.out.exists())
    def test_existing_viewer_rejected(self):
        (self.pub / 'viewer').mkdir(); (self.pub / 'viewer/index.html').write_text('keep')
        with self.assertRaises(PublicationError): prepare(self.pub, self.out)
    def test_unexpected_backlink_rejected_before_write(self):
        (self.pub / 'stories/momotaro-I-low.html').write_text('<section>story</section>')
        with self.assertRaises(PublicationError): prepare(self.pub, self.out)
        self.assertFalse(self.out.exists())
    def test_unsafe_input_rejected(self):
        (self.pub / '.env').write_text('dummy')
        with self.assertRaises(PublicationError): prepare(self.pub, self.out)
    def test_unknown_viewer_relative_asset_rejected(self):
        for text in ('<a href="other.html">x</a>', '<img src="x.png">', '<base href="/">'):
            with self.assertRaises(PublicationError): viewer_index(text)
    def test_missing_synopsis_rejected(self):
        with self.assertRaises(PublicationError): synopsis_text('<p>not a synopsis</p>')

if __name__ == '__main__': unittest.main()
