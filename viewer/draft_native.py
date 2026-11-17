from viewer import ui
from viewer.pages import _escape as E

def fragment(worlds, selected=None,mode="waiting"):
    options='<option value="">元の世界を選択</option>'+''.join('<option value="'+E(w['id'])+'"'+(' selected' if w['id']==selected else '')+'>'+E(w['name'])+'</option>' for w in worlds)
    markup='''<section class="wd ui-workspace ui-workspace--form" data-native-draft>
    <details data-native-source><summary>既存世界・ZIPから、設定を保持した下書きを作る</summary>
    <p class="ui-hint">元世界は変更しません。未対応の項目や関連ファイルも保全します。AIは呼びません。</p>
    <label class="ui-field">元の世界<select data-native-world>'''+options+'''</select></label>
    <button class="ui-button" type="button" data-native-import-world>この世界を下書きに取り込む</button>
    <label class="ui-field">世界のZIP（10MB以内・展開後8MB以内）<input type="file" accept=".zip,application/zip" data-native-zip></label>
    <button class="ui-button" type="button" data-native-import-zip>ZIPを下書きに取り込む</button>
    <label class="ui-field">保存した既存設定の下書き<select data-native-list><option value="">選択してください</option></select></label>
    <div class="wd-actions"><button class="ui-button" type="button" data-native-refresh>一覧を更新</button><button class="ui-button" type="button" data-native-open>再開する</button><button class="ui-button" type="button" data-native-status>保存結果を確認</button></div>
    </details><p role="status" aria-live="polite" data-native-notice></p><p role="alert" class="ui-error" data-native-error></p>
    <section data-native-editor hidden>'''+ui.heading('既存設定の下書き','変更していない項目・ファイルは保持し、元世界へ自動で上書きしません。')+'''
    <p data-native-summary></p><nav class="ui-tabs" aria-label="下書きの設定" data-native-tabs></nav><div class="ui-content native-content"><div data-native-fields></div><section class="ui-section" data-native-ai data-ai-mode="__NATIVE_AI_MODE__"><h2>この下書きの修正候補</h2><p class="ui-hint" data-nai-note></p><label class="ui-field">修正する項目<select data-nai-target></select></label><label class="ui-field">修正の要望<textarea rows="2" maxlength="4000" data-nai-instruction></textarea></label><div class="wd-actions"><button class="ui-button" data-nai-propose type="button">修正候補を作る</button><button class="ui-button" data-nai-status type="button">候補の状態を確認</button><button class="ui-button" data-nai-cancel type="button">候補を取り消す</button></div><p role="status" aria-live="polite" data-nai-status-text></p><div data-nai-diff></div><button class="ui-button ui-button--confirm" data-nai-apply type="button">この候補を下書きへ適用・保存</button></section></div>
    <label class="ui-field"><span><input type="checkbox" data-native-confirm>検証結果と警告を確認し、新しい世界として保存する</span></label>
    <div class="wd-actions"><button class="ui-button" type="button" data-native-back>新規下案の入力へ戻る</button><button class="ui-button" type="button" data-native-fork>別の下書きとして複製</button></div>
    '''+ui.footer(ui.button('下書きを保存',attrs={'data-native-save':True})+ui.button('実行入力を確認',attrs={'data-native-check':True})+ui.button('ZIPを出力',attrs={'data-native-export':True})+ui.button('原本ZIPを出力',attrs={'data-native-export-original':True})+ui.button('新しい世界として保存',kind='confirm',attrs={'data-native-publish':True,'disabled':True}))+'''
    </section></section>'''
    return markup.replace('__NATIVE_AI_MODE__',E(mode)).replace('data-native-source>', 'data-native-source open>' if selected else 'data-native-source>')
