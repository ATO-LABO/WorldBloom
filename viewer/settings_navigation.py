"""The same author-facing category order for saved worlds and new drafts."""
import html
import json
from urllib.parse import quote

SECTIONS = (
    ('overview', '概要と物語の前提'),
    ('people', '登場人物と関係'),
    ('places', '場所と移動'),
    ('items', '持ち物と情報'),
    ('time', '時間と出来事'),
    ('rules', '世界のルール'),
    ('checks', '確認・調査'),
    ('files', '設定ファイル'),
)


def payload():
    return html.escape(json.dumps(SECTIONS, ensure_ascii=False), quote=True)


def navigation(world_id, *, buttons=False, active=None):
    base='/worlds/'+quote(world_id,safe='')
    parts=[]
    for key,label in SECTIONS:
        attrs=f' data-settings-category="{key}"'
        if key==active:attrs+=' aria-current="page"'
        if buttons and key not in ('rules','files'):
            parts.append(f'<button type="button" data-screen="{key}"{attrs}>{label}</button>')
        else:
            href=base+'?view=advanced#'+('genres' if key=='rules' else 'files') if key in ('rules','files') else base+'#screen='+key
            parts.append(f'<a href="{html.escape(href,quote=True)}"{attrs}>{label}</a>')
    return ''.join(parts)
