"""World-specific roles/files and explicitly shared genre precedents."""
import json
from execution import world_editor, genre_editor
from viewer import pages, ui, action_catalog, world_graph, library_pages
from viewer.settings_navigation import navigation


def render(world, store, jobs):
    snap=world_editor.snapshot(store,world['id'])
    genre=genre_editor.snapshot(store,world['genre']) if world.get('genre') else None
    files={rel:store.read('world',world['id'],rel) for rel in store.world_files(world['id'])}
    from engine.story_genres import catalog
    initial={'story_genre_catalog':catalog(),'world':snap,'genre':genre,'files':files,'editable':jobs is not None}
    body='<div class="wa-layout ui-workspace ui-workspace--nav" data-world-advanced data-initial="'+pages._escape(json.dumps(initial,ensure_ascii=False))+'">'
    body+='<nav class="ui-nav" aria-label="世界設定">'+navigation(world['id'])+'</nav><div class="wa-main">'
    body+=ui.heading('世界設定',(world.get('name') or world['id'])+'の設定です。保存した変更はこの世界で次に実行を始めるときから使われます。過去の実行結果は変更されません。')
    body+='<div class="wa-body ui-content"><nav class="ui-navigation" data-advanced-sections aria-label="この分類の項目">'+''.join(
        f'<button class="ui-button" type="button" data-advanced-section="{key}" aria-current="'+('page' if key=='roles' else 'false')+f'">{label}</button>'
        for key,label in (('roles','役割と結末'),('concerns','状態・条件の定義'),('objectives','達成・失敗・判定期間'),('responses','目的と対処の行動'),('planning','目的の分解と再計画'),('genres','ジャンル'),('canon','状況ごとの定石'),('actions','行動図鑑')))
    body+='</nav><section class="ux-advanced-list" aria-label="設定の一覧" data-advanced-list></section><section class="wa-reading" aria-label="設定の内容" data-advanced-reading></section></div>'
    effects=library_pages._genre_yaml(store.repo,world.get('genre'),'effects.yaml')
    body+='<template data-advanced-readouts>'+world_graph.character_readout_html(snap['world'],snap['people'],effects if isinstance(effects,list) else [],protagonist=world['protagonist'],antagonist=world['antagonist'])+'</template>'
    body+='<template data-advanced-canon>'+library_pages._canon_panel(world,snap['people'],store)+'</template>'
    body+='<template data-advanced-actions>' +action_catalog.catalog_panel_html(world,store.repo)+'</template>'
    body+=ui.footer('<p role="status" data-advanced-status>'+('保存された設定を表示しています' if jobs else '閲覧専用です')+'</p>')+'</div>'
    body+='<dialog class="ui-edit-dialog wa-dialog" aria-labelledby="wa-dialog-title" data-advanced-dialog><header class="ui-section-head"><h2 id="wa-dialog-title">編集</h2><button type="button" data-advanced-close aria-label="閉じる">×</button></header><p class="ui-hint" data-advanced-scope></p><form data-advanced-form><div class="wa-editor" data-advanced-detail></div><p class="ui-error" role="status" data-advanced-message></p><footer class="ui-edit-dialog-actions"><button type="button" data-advanced-close>キャンセル</button><button type="button" data-advanced-check>設定を確認</button><button type="submit" data-advanced-save class="ui-button ui-button--confirm">保存する</button></footer></form></dialog></div>'

    return pages.document('世界設定',body,world={'id':world['id'],'name':world.get('name') or world['id']},phase='world',job_store=jobs,page_class='settings-workspace',stylesheets=('review-workspace.css','world-advanced.css',),scripts=('review-workspace.js','concerns-editor.js','objectives-editor.js','responses-editor.js','planning-editor.js','world-advanced.js'))
