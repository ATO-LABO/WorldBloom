"""Three-stage coauthor workspace. Rendering never invokes a model."""
import json
from viewer import pages, ui


def render(store, jobs, *, selected=None):
    e = pages._escape
    worlds = store.worlds()
    from viewer.home_pages import world_card
    cards = ''.join(world_card(store, w, {}, selection=True, selected_world=selected) for w in worlds)
    source_id = selected if selected in {w['id'] for w in worlds} else ''
    mode = getattr(jobs, 'world_draft_mode', 'waiting')
    mode_note = {'mock': 'モック応答で動作します。実AI・GPUは呼びません。', 'fixture': '固定応答で動作します。実AI・GPUは呼びません。', 'waiting': 'AIは待機中です。手入力・保存・採用はAIなしで進められます。', 'live': 'AIへの相談を実行したときだけ、設定中のAIに問い合わせます。'}.get(mode, 'AIの接続状態を確認してください。')
    body = '''<section class="cw ui-workspace ui-workspace--form" data-creation-workflow data-world-draft data-mode="'''+e(mode)+'''">
    <div class="cw-scroll"><div class="cw-shell">
    <nav class="ui-breadcrumb"><a href="/worlds?mode=create">← 世界一覧に戻る</a><a href="/worlds/new?mode=create&amp;screen=entry" data-cw-home hidden>作成の入口へ戻る</a></nav>
    '''+ui.heading('新しい世界を作成', '始め方を選んでください。')+'''
    <nav class="cw-steps" aria-label="作成の段階" data-cw-steps hidden><button type="button" data-cw-step="1" aria-current="step">① 下書きを用意</button><button type="button" data-cw-step="2">② 内容を整える</button><button type="button" data-cw-step="3">③ 確認して採用</button></nav>
    <p class="ui-hint cw-mode" data-cw-mode-note>'''+e(mode_note)+'''</p><p class="ui-error" role="alert" data-cw-error></p><p role="status" aria-live="polite" data-cw-notice></p><section class="ui-notice cw-recovery" data-cw-recovery hidden aria-label="処理結果の確認"><p data-cw-recovery-note></p><button class="ui-button" type="button" data-cw-compare hidden>保存内容を比較する</button><button class="ui-button" type="button" data-cw-save-check>送信した処理の結果を確認</button></section>
    <section class="ui-notice" data-cwg-recovery hidden aria-label="AI入力の結果確認"><p role="status" data-cwg-status></p><p class="ui-hint">入力と送信IDを保持しています。AIは再送せず、状態を確認できます。</p><button type="button" class="ui-button" data-cwg-recover>AI入力の状態を確認</button></section>
    <dialog class="ui-edit-dialog cw-generation-dialog" data-cwg-dialog data-cw-generation-dialog aria-labelledby="cw-generation-title" aria-describedby="cw-generation-body"><h2 id="cw-generation-title" data-cwg-title></h2><div id="cw-generation-body" data-cwg-body role="status" aria-live="polite"></div><div class="cw-generation-actions" data-cwg-actions></div></dialog>
    <section class="ui-notice cw-readonly" data-cw-readonly hidden><h2>作成済みの取込下書き（閲覧専用）</h2><p>この記録は編集・保存・AI相談・再作成を行わずに閲覧できます。編集は作成した世界の設定から行ってください。</p><div class="cw-actions"><a class="ui-button" data-cw-created-world href="#">作成した世界の設定を開く</a><a class="ui-button" data-cw-start-existing href="#">既存世界から始める</a></div><p class="ui-hint">リンクを開いただけで取込や世界の作成は実行しません。</p></section>
    <section data-cw-screen="entry" aria-label="作成の入口">
      <p class="ui-notice" data-cw-held hidden></p><button type="button" class="ui-button" data-cw-continue hidden>編集中の下書きへ戻る</button>
      <div class="cw-entry-grid">
        <button type="button" class="ui-button cw-entry-choice" data-cw-enter="world-import" aria-label="既存の世界から始める"><strong>既存の世界から始める</strong><span class="ui-hint">元の設定を保持した、別の下書きを作ります。</span><span aria-hidden="true">→</span></button>
        <button type="button" class="ui-button cw-entry-choice" data-cw-enter="zip-import" aria-label="ZIPから始める"><strong>ZIPから始める</strong><span class="ui-hint">世界のZIPを取り込み、別の下書きを作ります。</span><span aria-hidden="true">→</span></button>
        <button type="button" class="ui-button cw-entry-choice" data-cw-enter="resume" aria-label="保存した下書きを再開"><strong>保存した下書きを再開</strong><span class="ui-hint">保存した内容と編集段階を読み込みます。</span><span aria-hidden="true">→</span></button>
        <button type="button" class="ui-button cw-entry-choice" data-cw-enter="new" aria-label="新規作成"><strong>新規作成</strong><span class="ui-hint">世界の名前・概要から下書きを用意します。</span><span aria-hidden="true">→</span></button>
      </div>
    </section>
    <section data-cw-screen="world-import" aria-label="既存の世界から始める" hidden>
      <h2>既存の世界から始める</h2><p class="ui-hint">元の世界を変更せず、未対応の項目・関連ファイルも保持した下書きを作ります。AIは呼びません。</p>
      <section class="hl-shell cw-world-library ui-settings-frame ui-settings-theme" data-native-draft data-cw-world-picker aria-label="元の世界を選択">
      <input type="hidden" data-cw-source value="'''+e(source_id)+'''">
      <div class="hl-toolbar"><div class="hl-filters"><label><span class="hl-sr">世界名・人物で検索</span><input type="search" data-cw-world-search placeholder="世界名・人物で検索"></label><span class="hl-count" data-cw-world-count role="status" aria-live="polite">'''+str(len(worlds))+'''件</span><button class="ui-button" type="button" data-cw-world-reset hidden>絞り込みを解除</button></div></div>
      <div class="hl-grid" role="group" aria-label="元の世界の一覧">'''+cards+'''</div>
      '''+('' if worlds else '<div class="hl-empty"><h3>まだ世界がありません</h3><p>作成の入口から新規作成、またはZIPから始められます。</p></div>')+'''
      <div class="hl-empty" data-cw-world-empty hidden><h3>条件に合う世界がありません</h3><p>検索する言葉を変えてください。選択中の世界は保持しています。</p></div></section>
    </section>
    <section data-cw-screen="zip-import" aria-label="ZIPから始める" hidden>
      <h2>ZIPから始める</h2><p class="ui-hint">世界のZIPから、未対応の項目・関連ファイルも保持した下書きを作ります。原本ZIPは変更せず、AIは呼びません。</p>
      <section class="cw-card cw-source-form" data-native-draft><h3>ZIPを取り込む</h3><label class="ui-field">世界のZIP（10MB以内）<input type="file" accept=".zip,application/zip" data-cw-zip></label><button class="ui-button ui-button--advance" type="button" data-cw-import-zip>ZIPから下書きを作る</button></section>
    </section>
    <section data-cw-screen="resume" aria-label="保存した下書きを再開" hidden>
      <div class="cw-card cw-resume-card"><div class="cw-resume-toolbar"><h2>保存した下書き <span class="ui-hint" data-cw-list-count>取得中</span></h2><button class="ui-button" type="button" data-cw-refresh>一覧を更新</button></div>
      <p role="status" aria-live="polite" data-cw-list-status>一覧を読み込んでいます。</p><ul class="cw-resume-list" data-cw-list aria-label="保存した下書き" aria-busy="true"></ul></div>
      <p class="ui-hint">再開しても、検証・AIへの相談・世界への採用は自動実行しません。</p>
    </section>
    <div data-cw-workflow hidden>
    <section data-cw-panel="1" aria-label="下書きを用意">
      <div class="cw-card"><h2>どんな世界を作りますか？</h2>
      <form data-cw-prepare>
      '''+ui.field('世界の名前','<input name="name" maxlength="120" autocomplete="off" placeholder="仮の名前でも、未定のままでも保存できます">')+'''
      <fieldset class="cw-inspiration"><legend>考え始める手がかり</legend><div class="cw-inspiration-options">
        <div class="cw-inspiration-card"><label><input type="radio" name="storyType" value="" checked><span><strong>自由に考える</strong><small>自分のアイデアから組み立てる</small></span></label></div>
        <div class="cw-inspiration-card"><label><input type="radio" name="storyType" value="threat-response"><span><strong>世界の脅威への対応</strong><small>脅威と、それに向き合う人々から考える</small></span></label><span class="cw-help-wrap"><button class="ui-button cw-help" type="button" data-cw-help aria-label="世界の脅威への対応の説明" aria-expanded="false" aria-controls="cw-threat-help" aria-describedby="cw-threat-help">?</button><span role="tooltip" id="cw-threat-help" data-cw-help-text hidden>世界の脅威と、それを知った人々の対応を考えるための手がかりです。選んだ型はAIに相談するときの参考情報になります。選ぶだけで生成は始まりません。脅威・人物の認識・目的・行動制限などの実行条件は自動設定されません。必要な設定は②で整えます。</span></span></div>
      </div><p class="ui-hint" data-cw-old-type hidden></p></fieldset>
      '''+ui.field('概要','<textarea name="overview" rows="3" maxlength="8000" placeholder="どんな世界で、誰が何を望んでいるか。決まっていることを書いてください。"></textarea>')+'''
      <label class="ui-field"><span>ジャンル <small>任意</small></span><select name="genre"><option value="">まだ決めない</option><option>ファンタジー</option><option>ミステリー</option><option>恋愛</option><option>SF</option><option>日常</option><option>その他</option></select></label>
      </form><p class="ui-hint">物語の期間・時間帯と導入文は、次の「② 内容を整える」で設定します。</p><p class="ui-hint">概要だけでも始められます。あとから手入力で整え、必要な項目だけAIに相談できます。</p></div>
    </section>
    <section data-cw-panel="2" aria-label="内容を整える" hidden><h2 data-cw-title></h2><p class="ui-hint" data-cw-edit-note>項目を選んで編集できます。AIへの相談は任意です。</p>
      <div class="cw-edit"><nav class="cw-categories ui-nav" aria-label="編集する項目" data-cw-categories></nav><section class="cw-card cw-fields" data-cw-fields></section>
      <aside class="cw-card cw-ai" data-cw-ai><h2>✧ AIに相談</h2><p class="ui-hint" data-cw-ai-note></p><label class="ui-field">相談する項目<select data-cw-ai-target></select></label><label class="ui-field">修正の要望<textarea rows="3" maxlength="4000" data-cw-ai-instruction></textarea></label>
      <button class="ui-button ui-button--advance" type="button" data-cw-ai-propose>提案を作る</button><p class="ui-hint">提案は差分を確認し、適用するまで反映されません。</p><p role="status" aria-live="polite" data-cw-ai-status></p>
      <div class="cw-actions"><button class="ui-button" type="button" data-cw-ai-cancel>提案を取り消す</button><button class="ui-button" type="button" data-cw-ai-check>提案の状態を確認</button></div>
      <details data-cw-diff><summary>変更案を確認</summary><div data-cw-diff-content></div><button class="ui-button ui-button--confirm" type="button" data-cw-ai-apply>この提案を下書きへ適用</button></details>
      <details><summary>世界全体の下案をAIに相談</summary><p class="ui-hint">新規下書きで利用できます。現在の手修正は保存した別の下書きに残し、名前と概要から別案を作ります。</p><button class="ui-button" type="button" data-cw-generate>AIに別の下案を作ってもらう</button></details></aside></div>
      <details class="cw-advanced" data-cw-new-tools><summary>詳細編集</summary>    <button class="ui-button" type="button" data-cw-detail-editor>目標の達成条件・関係・導入文を編集</button>
    <details><summary>下案JSON（対応する新規下書きのみ）</summary><label class="ui-field">下案JSON<textarea rows="10" spellcheck="false" data-cw-json></textarea></label><button class="ui-button" type="button" data-cw-json-apply>JSONを下書きへ反映</button></details></details>
    </section>
    <section data-cw-panel="3" aria-label="確認して採用" hidden><h2 data-cw-review-title>この内容で新しい世界を作成します</h2><p class="ui-hint">保存後、実行画面から物語を始められます。</p><div class="cw-card" data-cw-summary></div>
      <div class="ui-notice" data-cw-validation>まだ設定の確認を実行していません。</div><div class="cw-actions" data-cw-check-actions><button class="ui-button" type="button" data-cw-check>設定と実行入力を確認</button></div>
      <details class="cw-defaults"><summary>未設定の項目・初期値・非対応の詳細</summary><div data-cw-defaults></div></details>
      <label class="ui-field" data-cw-consent-row><span><input type="checkbox" data-cw-consent>検証結果・初期値と内容を確認し、新しい世界として保存します</span></label><p class="ui-hint">元の世界への上書きや物語の実行は行いません。</p><div data-cw-result></div>
    </section></div></div></div>
    '''+ui.footer('''<span role="status" aria-live="polite" data-cw-saved>まだ下書きを保存していません</span>
    <span class="ui-hint cw-world-picked" role="status" aria-live="polite" data-cw-world-picked hidden></span>
    <div class="cw-footer-actions"><button class="ui-button" type="button" data-cw-save hidden>下書きを保存</button><button class="ui-button ui-button--advance" type="button" data-cw-import hidden>この世界から下書きを作る</button><button class="ui-button ui-button--advance" type="button" data-cw-next hidden>内容を整える →</button></div>''')+'''</section>'''
    return pages.document('新しい世界を作成',body,phase='world',job_store=jobs,page_class='form-workspace',
        stylesheets=('home-workspace.css','creation-workflow.css','items-workspace.css','creation-refinement.css'),
        scripts=('items-workspace.js','draft-goal-editor.js','draft-settings-forms.js','creation-workflow-model.js','creation-workflow-list.js','creation-world-picker.js','creation-conflict.js','creation-prepare.js','creation-generation.js','creation-refinement.js','creation-workflow.js'))
