"""World-scoped review workspace. Rendering never starts an execution."""
import html
import json
from urllib.parse import quote
from viewer import pages, purpose_pages, ui
from viewer.settings_navigation import navigation


def work_note(world_id, *, review=False):
    ident=html.escape(world_id,quote=True)
    field=(' data-purpose-note' if review else '')
    return (f'<details class="ui-section" data-world-notes="{ident}"><summary>作業メモ・確認したいこと</summary>'
            '<p class="ui-hint">このブラウザーに世界ごとに保存します。実行条件や人物の行動には使いません。</p>'
            +ui.field('目的・注目点',f'<textarea rows="3" maxlength="3000" data-world-note-field{field}></textarea>')
            +'<p class="ui-hint" role="status" data-world-note-status></p></details>')


def render(world, *, job_store=None):
    wid=quote(world['id'],safe='');base='/worlds/'+wid
    name=world.get('name') or world['id']
    # Names are admitted as legacy aliases only when they identify one world.
    names=pages._library_worlds(job_store)
    aliases=[world['id']]+([name] if sum((w.get('name') or w['id'])==name for w in names)==1 else [])
    nav=ui.navigation(navigation(world['id'],active='checks'),label='世界設定')
    content=(ui.heading('世界のレビュー',name+'の条件で試し、保存した結果を確認します。')
             +f'<p class="ui-notice">対象の世界：<strong>{html.escape(name)}</strong></p>'
             +ui.button('制作の実行条件',href='/jobs?world='+wid)
             +f'<input type="hidden" data-purpose-world value="{html.escape(world["id"],quote=True)}">'
             +work_note(world['id'],review=True)+purpose_pages.review_panel(job_store,world["id"]).replace(' hidden','',1)
             +'<p class="ui-hint" role="status" data-purpose-storage></p>')
    labels={c['config_id']:c['label'] for c in job_store.configs.list() if c['project_id']==world['id']} if job_store else {}
    attrs=ui.attributes({'data-purpose-root':True,'data-world-review':world['id'],'data-world-aliases':json.dumps(aliases,ensure_ascii=False),'data-world-config-labels':json.dumps(labels,ensure_ascii=False)})
    body='<div class="ui-workspace ui-workspace--nav wr-shell">'+nav+'<div class="wr-main"><section class="ui-content"'+attrs+'>'+content+'</section>'+ui.footer(ui.button('世界の概要へ戻る',href=base))+'</div></div>'
    return pages.document('世界のレビュー',body,world={'id':world['id'],'name':name},phase='world',job_store=job_store,page_class='settings-workspace',stylesheets=('world-review.css',),scripts=('world-notes.js','purpose-independent.js') if job_store else ('world-notes.js',))
