from html.parser import HTMLParser
import unittest
import test_home_workspace as home_fixtures
from viewer.creation_workflow import render


class Nodes(HTMLParser):
    def __init__(self):
        super().__init__(); self.stack=[]; self.nodes=[]
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs); self.nodes.append((tag,attrs,list(self.stack)))
        if tag not in {'input','img','br','hr','meta','link'}: self.stack.append((tag,attrs))
    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag:
                self.stack=self.stack[:i]; break


class FooterTests(unittest.TestCase):
    setUp=home_fixtures.HomeWorkspaceTests.setUp

    def test_normal_footer_only_saves_or_progresses_without_hidden_clone_or_export(self):
        page=render(self.store,self.jobs); parsed=Nodes(); parsed.feed(page)
        hooks=[]
        for tag,attrs,ancestors in parsed.nodes:
            if tag=='button' and any('ui-footer' in a.get('class','').split() for _,a in ancestors):
                hooks.extend(key for key in attrs if key.startswith('data-cw-'))
        self.assertEqual(hooks,['data-cw-save','data-cw-import','data-cw-next'])
        for hook in ['back','more','fork','export','original']:
            self.assertNotIn('data-cw-'+hook,page)
        self.assertIn('data-cw-home',page)
        for step in (1,2,3): self.assertIn('data-cw-step="'+str(step)+'"',page)
        self.assertIn('data-cw-continue',page)

    def test_recovery_and_existing_edit_tools_have_contextual_places(self):
        parsed=Nodes(); parsed.feed(render(self.store,self.jobs))
        def ancestors(hook):
            return next(a for _,attrs,a in parsed.nodes if 'data-cw-'+hook in attrs)
        self.assertFalse(any('ui-footer' in a.get('class','') for _,a in ancestors('save-check')))
        self.assertTrue(any('data-cw-recovery' in a for _,a in ancestors('save-check')))
        self.assertNotIn('data-cw-preserve',render(self.store,self.jobs))
        self.assertTrue(any(a.get('data-cw-panel')=='2' for _,a in ancestors('detail-editor')))
        self.assertTrue(any(a.get('data-cw-panel')=='2' for _,a in ancestors('json')))


if __name__=='__main__': unittest.main()
