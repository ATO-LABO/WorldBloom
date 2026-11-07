"""Shared defaults, safe components and stable form ownership."""
from html.parser import HTMLParser
import ast
from pathlib import Path
import unittest
from viewer import pages, ui, world_draft, review_pages


class Elements(HTMLParser):
    def __init__(self, source):
        super().__init__(); self.rows=[]; self.feed(source)
    def handle_starttag(self, tag, attrs):
        self.rows.append((tag, dict(attrs)))


class SharedDesignTests(unittest.TestCase):
    def test_new_minimal_page_includes_design_without_custom_css(self):
        source=pages.document('New',ui.field('Name','<input name="name">')+ui.button('Save'))
        self.assertIn('layout-standard-workspace',source)
        assets=[attrs['href'] for tag,attrs in Elements(source).rows if tag=='link' and attrs.get('rel')=='stylesheet']
        self.assertEqual(assets[0],'/static/ui-tokens.css')
        self.assertEqual(assets[-1],'/static/ui.css')

    def test_every_layout_has_the_shared_screen_baseline(self):
        for layout in ('standard-workspace','run-observer','world-prototype','home-library','purpose-landing','form-workspace','settings-workspace'):
            source=pages.document('Screen','',page_class=layout)
            main=next(a for tag,a in Elements(source).rows if tag=='main')
            self.assertIn('ui-screen',main['class'].split())

    def test_review_extensions_keep_shared_styles_last_and_preserve_script_order(self):
        source=review_pages.doc('Goal','',stylesheets=('route-goal.css',),scripts=('ga_replay.js',))
        rows=Elements(source).rows
        css=[a['href'] for tag,a in rows if tag=='link' and a.get('rel')=='stylesheet']
        scripts=[a['src'] for tag,a in rows if tag=='script' and 'src' in a]
        self.assertEqual(css[-2:],['/static/route-goal.css','/static/ui.css'])
        self.assertLess(scripts.index('/static/review-workspace.js'),scripts.index('/static/ga_replay.js'))

    def test_no_renderer_can_append_assets_after_the_shared_styles(self):
        for path in Path(pages.__file__).parent.glob('*.py'):
            tree=ast.parse(path.read_text(encoding='utf8'))
            for node in ast.walk(tree):
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='replace' and node.args:
                    self.assertFalse(isinstance(node.args[0],ast.Constant) and node.args[0].value=='</head>',str(path))

    def test_shared_styles_follow_page_assets_and_reject_external_assets(self):
        source=pages.document('New','',stylesheets=('world-draft.css',))
        self.assertLess(source.index('/static/world-draft.css'),source.index('/static/ui.css'))
        for bad in ('../app.css','https://x/a.css','x.css" onload="x'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                pages.document('New','',stylesheets=(bad,))

    def test_component_text_and_attributes_are_escaped(self):
        source=ui.heading('<script>', 'a & b')+ui.button('"<img>',attrs={'data-label':'"<x>'})
        self.assertNotIn('<script>',source);self.assertNotIn('<img>',source)
        self.assertIn('&lt;script&gt;',source)
        with self.assertRaises(ValueError):ui.button('x',attrs={'onclick':'bad()'})
        for href in ('javascript:bad()','//other/app'):
            with self.assertRaises(ValueError):ui.button('x',href=href)

    def test_draft_footer_submit_has_explicit_form_owner_and_button_types(self):
        rows=Elements(world_draft.fragment('mock')).rows
        forms=[attrs for tag,attrs in rows if tag=='form']
        self.assertEqual([f['id'] for f in forms],['wd-input'])
        generate=next(a for tag,a in rows if 'data-wd-generate' in a)
        self.assertEqual((generate['type'],generate['form']),('submit','wd-input'))
        adopt=next(a for tag,a in rows if 'data-wd-adopt' in a)
        self.assertEqual(adopt['type'],'button')
        self.assertIn('hidden',adopt)
        self.assertIn('ui-footer',world_draft.fragment('mock'))


if __name__=='__main__':unittest.main()
