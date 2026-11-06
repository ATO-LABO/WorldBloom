"""Read-only application genre catalog, shared by home and settings."""
import html
from engine.story_genres import catalog
from viewer import pages, workbench_pages as wb


def panel(selected=None):
    rows = [g for g in catalog() if selected is None or g["id"] == selected]
    return '<section class="ui-content"><h1>ジャンル</h1><p>共通の行動は常に有効です。世界の詳細設定で、必要なジャンルを複数選んだり解除したりできます。</p><h2>共通</h2><p>移動や会話など、どの組み合わせでも使う基本の行動です。</p>' + ''.join('<section><h2>'+html.escape(g['name'])+'</h2><p>'+html.escape(g['description'])+'</p></section>' for g in rows) + '<p><a href="/worlds">世界を選ぶ →</a></p></section>'


def show(handler, selected=None):
    handler._send_html(pages.document('ジャンル', panel(selected), phase='world', job_store=wb._job_store(handler)))
