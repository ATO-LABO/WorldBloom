"""World settings workspace with targeted, persistent editing."""
from __future__ import annotations

import html
import json
from urllib.parse import quote

from viewer import pages


def render(world, world_yaml, subjects, *, job_store=None, pin=None, revision=None, experiments=()):
    """Render saved settings in the shared production shell. Rendering never writes."""
    model = {
        "id": world["id"], "name": world.get("name") or world["id"],
        "world": world_yaml, "people": subjects,
        "overview": world_yaml.get("overview") or "", "intro": world_yaml.get("initial_story") or "",
        "editable": job_store is not None and revision is not None, "revision": revision,
    }
    wid = quote(world["id"], safe="")
    config = f'/configs/new?project={wid}'
    if world.get("genre"):
        config += "&template=" + quote(world["genre"], safe="")
    people_ids = {p.get("id") for p in subjects}
    missing = []
    if not subjects: missing.append("登場人物")
    if not world_yaml.get("zones"): missing.append("場所")
    if not world_yaml.get("protagonist") or world_yaml["protagonist"] not in people_ids: missing.append("主人公")
    if not world_yaml.get("antagonist") or world_yaml["antagonist"] not in people_ids: missing.append("敵役")
    if not world_yaml.get("target_ending"): missing.append("目標の結末")
    if any(not (p.get("range") or {}).get("entry") for p in subjects): missing.append("人物の初期位置")
    model["configUrl"] = config
    payload = json.dumps(model, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    next_href = f"/worlds/{wid}?view=advanced" if missing else config
    next_label = "未設定の項目を確認 →" if missing else "実行条件を決める →"
    status = ("未設定：" + "・".join(missing)) if missing else "編集した項目ごとに保存できます。"
    if not model["editable"]: status = "閲覧モード · 保存には実行管理の設定が必要です。"
    saved = "".join(f'<a class="wp-primary" href="/exp/{quote(name, safe="")}">' + ("保存された物語を見る →" if len(experiments) == 1 else html.escape(name) + " の物語を見る →") + "</a> " for name in experiments)
    body = f'''
    <div class="wp" data-world-prototype>
      <nav class="wp-nav" aria-label="世界設定">
        <button type="button" data-screen="overview" aria-current="page">世界の概要</button>
        <button type="button" data-screen="people">登場人物</button>
        <button type="button" data-screen="places">場所</button>
        <button type="button" data-screen="story">初期物語</button>
        <button type="button" data-screen="time">時間</button>
        <div class="wp-preview"><strong>世界設定</strong><br>変更は次の実行から使われます。<a href="/worlds/{wid}?view=advanced">詳細設定・設定ファイル ↗</a></div>
      </nav>
      <div class="wp-content" id="wp-content"></div>
      <footer class="wp-footer"><span id="wp-message" role="status">{html.escape(status)}</span><a class="wp-primary" data-next href="{html.escape(next_href, quote=True)}" {"hidden" if not model["editable"] else ""}>{next_label}</a>{saved}</footer>
      <dialog class="wp-dialog" aria-labelledby="wp-dialog-title"><form id="wp-form"><div class="wp-section-head"><h2 id="wp-dialog-title">編集</h2><button type="button" data-close aria-label="閉じる">×</button></div><p class="wp-muted">保存すると世界設定を更新します。過去の実行結果は変わりません。</p><div id="wp-fields"></div><div class="wp-dialog-actions"><button type="button" data-close>キャンセル</button><button class="wp-primary is-confirm" type="submit">保存する</button></div></form></dialog>
      <script type="application/json" id="wp-data">{payload}</script>
      <noscript>この表示にはJavaScriptが必要です。<a href="/worlds/{wid}?view=advanced">詳細設定を開く</a></noscript>
    </div>'''
    doc = pages.document(model["name"], body, world={"id": world["id"], "name": model["name"]},
                         phase="world", page_class="world-prototype", job_store=job_store, pin=pin)
    doc = doc.replace('</head>', '<link rel="stylesheet" href="/static/world-prototype.css"><script src="/static/world-prototype.js" defer></script></head>')
    doc = doc.replace('data-wb="world-picker"', 'data-wb="world-picker" aria-label="世界"')
    return doc.replace('<a class="home-cell" href="/">⌂ ホーム</a>', '<a class="home-cell" href="/" aria-label="WorldBloom ホーム">WorldBloom</a>')
