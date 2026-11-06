import unittest
from viewer.global_settings import output_panel

class ThreeStageSettingsUI(unittest.TestCase):
    def test_only_gemma_can_be_selected_from_the_settings_screen(self):
        view={'backend':'ollama','model':'old-model','limits':{'max_calls':1,'call_timeout_seconds':120,'wall_seconds':500,'max_saved_response_bytes':2048}}
        before=repr(view)
        html=output_panel(view,False)
        self.assertIn('data-gs-gemma-profile',html)
        for hook in ('f-backend', 'gs-model', 'output-model-list', 'data-gs-test', 'data-gs-key'):
            self.assertNotIn(hook, html)
        self.assertNotIn('old-model', html)
        self.assertIn('Gemma 4を選んで「設定を保存」', html)
        self.assertIn('① 概要からの設定作成',html)
        self.assertIn('class="ui-tabs"', html)
        self.assertIn('使用するAI', html)
        self.assertIn('aria-controls="gs-panel-limits"', html)
        self.assertIn('data-gs-output-panel="limits" hidden', html)
        self.assertIn('data-field="limits.max_saved_response_bytes"', html)
        self.assertIn('<details class="ui-settings-help" data-gemma-guide>', html)
        self.assertEqual(repr(view),before)

if __name__=='__main__':unittest.main()
