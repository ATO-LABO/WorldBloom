"""Human review UI for new-world proposals. No generation at page render."""
from viewer.pages import _escape as E
from viewer import ui


def fragment(mode="waiting", notice=None):
    message = {"mock": "モック応答で動作確認中。固定の下案を使い、概要の意味は解釈しません。LLMは呼びません。",
               "live": "実LLMで下案を生成し、同じモデルへの別の問い合わせで概要・目標・達成条件を確認します。生成と確認で最大2回呼び出します。ローカル27Bモデルでは数分以上かかる場合があります。自動再試行はしません。下案は人が採用するまで世界に保存されません。",
               "waiting": "実LLM生成は待機中です。接続先・モデル・費用条件を確認してから有効化します。"}[mode]
    heading = ui.heading("概要から、新しい世界を作る", "下案を作り、人物・場所・関係・目標を確かめてから採用します。")
    name = ui.field("世界の名前", '<input name="name" maxlength="120" required>', required=True)
    overview = ui.field("世界の概要", '<textarea name="overview" rows="4" maxlength="8000" required placeholder="どんな世界で、誰が何を望んでいるか。決めていることを書いてください。"></textarea>', required=True)
    footer = ui.footer('<p>人物・場所・品物・経路・関係・目標を確認してから採用します。</p>'
        + ui.button('下案を作る' + ('（モック）' if mode == 'mock' else ''), kind='advance', attrs={'type':'submit','form':'wd-input','data-wd-generate':True,'disabled':mode=='waiting'})
        + ui.button('下案を確認する',kind='advance',attrs={'data-wd-validate':True,'hidden':True})
        + ui.button('採用して、新しい世界として保存', kind='confirm', attrs={'data-wd-adopt':True,'disabled':True,'hidden':True}))
    return f'''<section class="wd ui-workspace ui-workspace--form" data-world-draft data-mode="{E(mode)}">
      {heading}<div class="wd-scroll ui-content">
      <p class="wd-mode ui-notice ui-notice--warning" role="note">{E(message)}{(' ' + E(notice)) if notice else ''}</p>
      <ol class="wd-steps"><li>概要を入力</li><li>下案を確認・検証</li><li>採用して新規保存</li></ol>
      <form id="wd-input" data-wd-input>{name}{overview}</form>
      <p data-wd-status role="status" aria-live="polite">まだ下案はありません。既存の世界は変更しません。</p>
      <p class="ui-error" data-wd-errors role="alert"></p>
      <div class="wd-actions"><button class="ui-button" type="button" data-wd-check hidden>状態を確認</button><button class="ui-button" type="button" data-wd-cancel hidden>この下案を取り消す</button><button class="ui-button" type="button" data-wd-retry hidden>入力から新しい下案を作り直す</button></div>
      <section class="wd-review" data-wd-review hidden><h2>下案を確認する</h2><p>これは提案です。指定していない内容も含まれます。人物・場所・関係・目標を確認してください。</p>
      <div data-wd-summary></div>
      <section data-wd-goals aria-label="下案の修正"></section><div data-wd-context></div>
      <p class="wd-defaults ui-notice">概要に合わせて人物・場所・目標を作成します。目標の達成条件も確認してください。自動判定のない目標は文章として保存されます。共通の基本ルール／7日間・朝昼夕夜／数値特性は各0.5・基礎値50の既定値です。文章から推定した数値ではありません。</p>
      <p class="ui-hint">実生成の下案は、再確認のたびにAIで整合性を1回確認します。元の世界名と概要は保持します。</p>
      <details><summary>詳しく修正する（JSON）</summary><label class="ui-field">下案JSON<textarea data-wd-json rows="16" spellcheck="false"></textarea></label></details>
      <label class="wd-confirm ui-notice" data-wd-confirm-row><input type="checkbox" data-wd-confirm>人物・場所・品物・経路・関係・目標と既定値を確認し、この下案を採用します</label>
      <p class="ui-hint">採用するまでは世界一覧に追加しません。既存世界への上書きはできません。検証は到達可能性や面白さを保証しません。</p></section>
      <p data-wd-result></p></div>{footer}</section>'''
