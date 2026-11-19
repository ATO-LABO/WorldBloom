"""Read-only application genre catalog, shared by home and settings."""
import html
from engine.action_types import catalog
from viewer import pages, workbench_pages as wb


def panel(selected=None):
    rows = [g for g in catalog() if selected is None or g["id"] == selected]
    return '<section class="ui-content"><h1>行動の種類</h1><p>世界の行動設定で、行動の種類と個々の行動を設定できます。</p><h2>共通</h2><p>移動や会話など、移動や会話などの基本的な行動です。使用可否は行動一覧で設定します。</p>' + ''.join('<section><h2>'+html.escape(g['name'])+'</h2><p>'+html.escape(g['description'])+'</p></section>' for g in rows) + '<p><a href="/worlds">世界を選ぶ →</a></p></section>'


def show(handler, selected=None):
    handler._send_html(pages.document('行動の種類', panel(selected), phase='world', job_store=wb._job_store(handler)))
