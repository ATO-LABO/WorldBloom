"""World-specific roles/files and explicitly shared genre precedents."""
import json
from execution import world_editor, genre_editor
from viewer import pages, review_pages as ui, action_catalog, world_graph, library_pages


def render(world, store, jobs):
    snap=world_editor.snapshot(store,world['id'])
    genre=genre_editor.snapshot(store,world['genre']) if world.get('genre') else None
    files={rel:store.read('world',world['id'],rel) for rel in store.world_files(world['id'])}
    initial={'world':snap,'genre':genre,'files':files,'editable':jobs is not None}
    body='<div class="ux-shell" data-world-advanced data-initial="'+pages._escape(json.dumps(initial,ensure_ascii=False))+'">'
    body+=ui.heading('世界の詳細設定',ui.link('/worlds/'+pages._url_segment(world['id']),'← 世界設定に戻る'))
    body+='<p class="ux-muted">'+pages._escape(world.get('name') or world['id'])+' · 役割・状態と条件の定義・行動の定石を確認します。</p>'
    body+='<div class="wa-layout"><nav class="ux-advanced-nav" aria-label="詳細設定の項目">'+''.join(
        f'<button type="button" data-advanced-section="{key}" aria-current="'+('page' if key=='roles' else 'false')+f'">{label}</button>'
        for key,label in (('roles','役割と結末'),('concerns','状態・条件の定義'),('objectives','達成・失敗・判定期間'),('responses','目的と対処の行動'),('planning','目的の分解と再計画'),('canon','状況ごとの定石'),('actions','行動図鑑'),('files','設定ファイル')))
    body+='</nav><div class="wa-body"><section class="ux-advanced-list" aria-label="設定の一覧" data-advanced-list></section><section class="wa-reading" aria-label="設定の内容" data-advanced-reading></section></div></div>'
    effects=library_pages._genre_yaml(store.repo,world.get('genre'),'effects.yaml')
    body+='<template data-advanced-readouts>'+world_graph.character_readout_html(snap['world'],snap['people'],effects if isinstance(effects,list) else [],protagonist=world['protagonist'],antagonist=world['antagonist'])+'</template>'
    body+='<template data-advanced-canon>'+library_pages._canon_panel(world,snap['people'],store)+'</template>'
    body+='<template data-advanced-actions>' +action_catalog.catalog_panel_html(world,store.repo)+'</template>'
    body+='<footer class="ux-footer"><p role="status" data-advanced-status>'+('保存された設定を表示しています' if jobs else '閲覧専用です')+'</p></footer>'
    body+='<dialog class="ui-edit-dialog wa-dialog" aria-labelledby="wa-dialog-title" data-advanced-dialog><header class="ui-section-head"><h2 id="wa-dialog-title">編集</h2><button type="button" data-advanced-close aria-label="閉じる">×</button></header><p class="ui-hint" data-advanced-scope></p><form data-advanced-form><div class="wa-editor" data-advanced-detail></div><p class="ui-error" role="status" data-advanced-message></p><footer class="ui-edit-dialog-actions"><button type="button" data-advanced-close>キャンセル</button><button type="button" data-advanced-check>設定を確認</button><button type="submit" data-advanced-save class="ui-button ui-button--confirm">保存する</button></footer></form></dialog></div>'

    return ui.doc('世界の詳細設定',body,world={'id':world['id'],'name':world.get('name') or world['id']},phase='world',job_store=jobs, stylesheets=('world-advanced.css',), scripts=('concerns-editor.js','objectives-editor.js','responses-editor.js','planning-editor.js','world-advanced.js'))
