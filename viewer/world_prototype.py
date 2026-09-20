"""Opt-in, non-persistent world workspace for visual and interaction review."""
from __future__ import annotations

import html
import json
from urllib.parse import quote

from viewer import pages


def render(world, world_yaml, subjects, *, job_store=None, pin=None):
    """Reuse the production shell; no file writes or execution APIs in this preview."""
    model = {
        "id": world["id"], "name": world.get("name") or world["id"],
        "world": world_yaml, "people": subjects,
        "overview": "", "intro": "", "overviewExample": world["id"] == "momotaro", "introExample": world["id"] == "momotaro",
    }
    if world["id"] == "momotaro":
        model["overview"] = "村から鬼ヶ島へ。桃太郎と仲間たちが、宝物を取り戻す旅に出る。"
        model["intro"] = "村の宝物は、海の向こうの鬼ヶ島にある。桃太郎は宝物を取り戻すため、旅の支度をしている。\nきびだんごと勾玉を携え、まずは村を出ようとしていた。"
    payload = json.dumps(model, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    wid = quote(world["id"], safe="")
    config = f'/configs/new?project={wid}'
    if world.get("genre"):
        config += "&template=" + quote(world["genre"], safe="")
    body = f'''
    <div class="wp" data-world-prototype>
      <nav class="wp-nav" aria-label="世界設定">
        <button type="button" data-screen="overview" aria-current="page">世界の概要</button>
        <button type="button" data-screen="people">登場人物</button>
        <button type="button" data-screen="places">場所</button>
        <button type="button" data-screen="story">初期物語</button>
        <button type="button" data-screen="time">時間</button>
        <div class="wp-preview"><strong>操作プレビュー</strong><br>変更はこの画面内のみ<br>再読み込みで元に戻ります。<a href="/worlds/{wid}">現在の画面を開く ↗</a><button type="button" data-reset>試作の変更を戻す</button></div>
      </nav>
      <div class="wp-content" id="wp-content"></div>
      <footer class="wp-footer"><span id="wp-message" role="status">操作プレビュー · 変更は画面内のみ。実行条件には引き継がれません。</span><a class="wp-primary" href="{html.escape(config, quote=True)}">実行条件を決める <span aria-hidden="true">→</span></a></footer>
      <dialog class="wp-dialog" aria-labelledby="wp-dialog-title"><form id="wp-form"><div class="wp-section-head"><h2 id="wp-dialog-title">編集</h2><button type="button" data-close aria-label="閉じる">×</button></div><p class="wp-muted">操作プレビューです。元の世界設定には保存されません。</p><div id="wp-fields"></div><div class="wp-dialog-actions"><button type="button" data-close>キャンセル</button><button class="wp-primary" type="submit">画面に反映</button></div></form></dialog>
      <script type="application/json" id="wp-data">{payload}</script>
      <noscript>この操作プレビューにはJavaScriptが必要です。現在の画面へのリンクから設定を確認できます。</noscript>
    </div>'''
    doc = pages.document(model["name"], body, world={"id": world["id"], "name": model["name"]},
                         phase="world", page_class="world-prototype", job_store=job_store, pin=pin)
    doc = doc.replace('</head>', '<link rel="stylesheet" href="/static/world-prototype.css"><script src="/static/world-prototype.js" defer></script></head>')
    doc = doc.replace('data-wb="world-picker"', 'data-wb="world-picker" aria-label="世界"')
    return doc.replace('<a class="home-cell" href="/">⌂ ホーム</a>', '<a class="home-cell" href="/" aria-label="WorldBloom ホーム">WorldBloom</a>')
