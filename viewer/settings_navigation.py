"""The same author-facing category order for saved worlds and new drafts."""
import html
import json
from urllib.parse import quote
from viewer import ui


def checks_navigation(world_id, active):
    base='/worlds/'+quote(world_id,safe='')
    entries=(('checks','確認の概要',base+'#screen=checks'),('routes','結末までの経路',base+'#screen=routes'),('goal','調査の条件',base+'/goal'),('review','世界のレビュー',base+'?view=review'))
    return ui.tabs(''.join(ui.button(label,href=href,attrs={
        'data-checks-tab':key,'data-screen':key if key in ('checks','routes') else None,
        'aria-current':'page' if key==active else None}) for key,label,href in entries),label='確認・調査の切り替え')

SECTIONS = (
    ('overview', '概要と物語の前提'),
    ('description', '話の説明'),
    ('people', '登場人物と関係'),
    ('places', '場所と移動'),
    ('items', '持ち物と情報'),
    ('time', '時間と出来事'),
    ('actions', '行動設定'),
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
        if buttons and key not in ('genres','rules','files','actions'):
            parts.append(f'<button type="button" data-screen="{key}"{attrs}>{label}</button>')
        else:
            href=base+'?view=actions' if key=='actions' else base+'?view=advanced#'+({'rules':'concerns'}.get(key,key)) if key in ('genres','rules','files') else base+'#screen='+key
            parts.append(f'<a href="{html.escape(href,quote=True)}"{attrs}>{label}</a>')
    return ''.join(parts)
