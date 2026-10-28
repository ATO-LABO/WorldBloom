"""A registered-value editor for one-day survey goals."""
import json

from viewer import pages, review_pages as ui


def render(world, initial, *, job_store=None):
    model = {**initial, "editable": job_store is not None}
    payload = json.dumps(model, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    wid = pages._url_segment(world["id"])
    body = '<div class="ux-shell" data-goal-editor>'
    body += ui.heading('経路を調べる目標', ui.link('/worlds/' + wid, '← 世界設定へ'))
    body += '''<p>人物・場所・所持品・知識をすべて満たす条件を設定します。操作する人物は1人です。他の人物は初期状態と対応済み予定イベントに従います。</p>
    <p>時刻探索は1日内・等間隔の登録済み時刻に対応します。「時点一致」は早着後も指定時刻まで待機、「期限まで」は早着時点で成功します。通常の物語の結末や人物の配送設定は変更しません。</p>
    <p data-goal-source></p><p data-goal-error role="alert"></p>
    <form data-goal-form><fieldset data-goal-controls>
      <label>目標の種類 <select data-goal-type><option value="legacy">従来の配送目標</option><option value="generic">汎用目標（すべての条件を満たす）</option></select></label>
      <div data-goal-generic>
        <label>操作する人物 <select data-goal-actor></select></label>
        <fieldset><legend>すべて満たす条件（AND）</legend><div data-goal-conditions></div><button type="button" data-goal-add>条件を追加</button></fieldset>
        <label>判定時刻 <select data-goal-mode><option value="any">期間内の任意時点</option><option value="at">指定時点で一致</option><option value="by">指定時点まで</option></select></label>
        <label data-goal-time>1日目の登録時刻 <select data-goal-slot></select></label>
      </div>
    </fieldset><div class="goal-actions"><button type="button" data-goal-validate>検証・探索</button><button type="submit" data-goal-save>目標を保存</button><button type="button" data-goal-cancel>変更を戻す</button><button type="button" data-goal-reload>保存済みを再読込</button></div></form>
    <p data-goal-status role="status"></p><section aria-label="目標の探索結果" data-goal-result></section>
    <p>世界専用の設定はジャンル設定全体より優先します。保存では目標以外の経路設定を保ちます。従来の配送目標へ戻しても人物やジャンル設定は変わりません。</p>'''
    body += f'<p><a href="/worlds/{wid}">保存後の経路画面へ →</a></p><script type="application/json" id="goal-data">{payload}</script></div>'
    return ui.doc('経路の目標入力', body, world=world, phase='world', job_store=job_store, stylesheets=('route-goal.css',), scripts=('route-goal.js',))
