"""Human review UI for new-world proposals. No generation at page render."""
from viewer.pages import _escape as E
from viewer import ui
from viewer.settings_navigation import payload


def fragment(mode="waiting", notice=None, worlds=()):
    message = {"fixture": "固定応答で共同作成の経路を検証中です。生成・確認・修正候補は保存用の固定データで、実AI・GPUを呼びません。", "mock": "モック応答で動作確認中。固定の下案を使い、概要の意味は解釈しません。LLMは呼びません。",
               "live": "実LLMで下案を生成し、同じモデルへの別の問い合わせで概要・目標・達成条件を確認します。生成と確認で最大2回呼び出します。ローカル27Bモデルでは数分以上かかる場合があります。自動再試行はしません。下案は人が採用するまで世界に保存されません。",
               "waiting": "実LLM生成は待機中です。接続先・モデル・費用条件を確認してから有効化します。"}[mode]
    heading = ui.heading("世界の共同下書き", "AIに下案や修正候補を相談し、人が同じ下書きの設定を編集します。採用するまでは世界へ反映しません。")
    name = ui.field("世界の名前", '<input name="name" maxlength="120" required>', required=True)
    overview = ui.field("世界の概要", '<textarea name="overview" rows="4" maxlength="8000" required placeholder="どんな世界で、誰が何を望んでいるか。決めていることを書いてください。"></textarea>', required=True)
    notes=''.join(ui.field(label,'<textarea rows="2" maxlength="1600" data-wd-story-field="'+key+'"></textarea>') for key,label in [('sign','前触れ'),('condition','被害につながる条件'),('target','影響を受ける対象'),('analyst','観測・分析する役割'),('protagonist','主に追いたい人物・目的')])
    authoring=('<details data-wd-authoring><summary>型や導入のメモから考える（任意）</summary>'
        +ui.field('考え始める手がかり','<select data-wd-story-type><option value="">自由に考える・まだ決めない</option><option value="threat-response">世界の脅威への対応</option></select>')
        +'<p class="ui-hint">型は制作の手がかりです。全員へ同じ脅威の評価や対処を強制しません。恋愛や個人の目的も共存できます。メモはAIへの依頼と下書きに保持し、実行条件へ自動変換しません。</p>'
        +'<div data-wd-story-notes hidden>'+notes+'</div><button class="ui-button" type="button" data-wd-import-authoring>以前の導入メモを取り込む</button><p role="status" data-wd-authoring-status></p></details>')
    source_options='<option value="">原本を追加しない</option>'+''.join('<option value="'+E(w['id'])+'">'+E(w['name'])+'</option>' for w in worlds)
    time_input = ('<details><summary>期間と時間帯を指定する（任意）</summary>'
        '<p class="ui-hint">概要の文章だけでは時間設定を変更しません。指定しない場合は7日間・朝／昼／夕方／夜で始めます。</p>'
        '<label class="ui-field"><span><input type="checkbox" data-wd-time-explicit>期間と時間帯を明示して下案に使う</span></label>'
        '<div data-wd-time-fields hidden>'+ui.field('期間（日）','<input type="number" min="1" max="10000" step="1" value="7" data-wd-time-days disabled>')
        +ui.field('時間帯（1行に一つ）','<textarea rows="4" data-wd-time-slots disabled>朝\n昼\n夕方\n夜</textarea>')+'</div></details>')
    footer = ui.footer(ui.button('下書きを保存',attrs={'data-wd-save-work':True,'type':'button'})+'<p>人物・場所・品物・経路・関係・目標を確認してから採用します。</p>'
        + ui.button('下案を作る' + ('（モック）' if mode == 'mock' else '（固定応答）' if mode=='fixture' else ''), kind='advance', attrs={'type':'submit','form':'wd-input','data-wd-generate':True,'disabled':mode=='waiting'})
        + ui.button('下案を確認する',kind='advance',attrs={'data-wd-validate':True,'hidden':True})
        + ui.button('採用して、新しい世界として保存', kind='confirm', attrs={'data-wd-adopt':True,'disabled':True,'hidden':True}))
    return f'''<section class="wd ui-workspace ui-workspace--form" data-world-draft data-mode="{E(mode)}">
      {heading}<div class="wd-scroll ui-content">
      <details data-wd-saved><summary>保存した下書きを再開</summary><p class="ui-hint">未完成の入力も保存できます。下書き保存ではAIの呼出し・世界への採用・実行を行いません。</p>
      <label class="ui-field">初回保存時に保全する世界（任意）<select data-wd-source-world>{source_options}</select></label><p class="ui-hint" data-wd-source-note>原本のファイル一式を読み取り専用で保持します。AI入力や下案への変換・既存世界の更新は行いません。</p>
      <label class="ui-field">下書き<select data-wd-work-list><option value="">選択してください</option></select></label>
      <div class="wd-actions"><button class="ui-button" type="button" data-wd-work-refresh>一覧を更新</button><button class="ui-button" type="button" data-wd-work-open>再開する</button><button class="ui-button" type="button" data-wd-work-fork>現在の入力を別の下書きとして保存</button><button class="ui-button" type="button" data-wd-work-check hidden>下書きの保存結果を確認</button></div>
      </details>
      <form id="wd-input" data-wd-input>{name}{overview}{authoring}{time_input}</form>
      <p role="status" aria-live="polite" data-wd-work-status>下書きはまだ保存していません。</p>
      <p class="wd-mode ui-notice ui-notice--warning" role="note">{E(message)}{(' ' + E(notice)) if notice else ''}</p>
      <ol class="wd-steps"><li>概要を入力</li><li>下案を確認・検証</li><li>採用して新規保存</li></ol>
      <p data-wd-status role="status" aria-live="polite">まだ下案はありません。既存の世界は変更しません。</p>
      <p class="ui-error" data-wd-errors role="alert"></p>
      <div class="wd-actions"><button class="ui-button" type="button" data-wd-check hidden>状態を確認</button><button class="ui-button" type="button" data-wd-cancel hidden>この下案を取り消す</button><button class="ui-button" type="button" data-wd-retry hidden>入力から新しい下案を作り直す</button></div>
      <section class="wd-review" data-wd-review hidden><h2>下案を確認する</h2><p>これは提案です。指定していない内容も含まれます。人物・場所・関係・目標を確認してください。</p>
      <div data-wd-summary></div><div class="wd-workspace" data-wd-workspace data-settings-sections="{payload()}"></div>
      <section data-wd-goals aria-label="下案の修正"></section><div data-wd-context></div>
      <p class="wd-defaults ui-notice">概要に合わせて人物・場所・目標を作成します。時間と予定は「時間と出来事」、人物と時間帯の許可は「行動設定」で確認してください。自動判定のない目標は文章として保存されます。優先度・必須指定は目標の文章から補いません。未指定の時間は7日間・朝昼夕夜、数値特性は各0.5・基礎値50の既定値です。</p>
      <p class="ui-hint">{'固定応答で候補と保存経路を検証しています。実モデルは呼びません。' if mode=='fixture' else '実生成の下案は、再確認のたびにAIで整合性を1回確認します。元の世界名と概要は保持します。'}</p>
      <details data-wd-raw hidden><summary>下案JSON</summary><label class="ui-field">下案JSON<textarea data-wd-json rows="16" spellcheck="false"></textarea></label></details>
      <label class="wd-confirm ui-notice" data-wd-confirm-row><input type="checkbox" data-wd-confirm>人物・場所・品物・経路・関係・目標と既定値を確認し、この下案を採用します</label>
      <p class="ui-hint">採用するまでは世界一覧に追加しません。既存世界への上書きはできません。検証は到達可能性や面白さを保証しません。</p></section>
      <p data-wd-result></p></div>{footer}</section>'''
