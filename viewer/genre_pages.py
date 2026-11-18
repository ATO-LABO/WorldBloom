"""Full-width base-rule editing workspace."""
from http import HTTPStatus
import json
from urllib.parse import quote
from viewer import pages, data
from execution import genre_editor as editor
from execution.library import LibraryStore
from execution.provenance import ConfigError
E = pages._escape
SECTIONS = [("basic", "基本情報", [editor.META]), ("actions", "行動", ["action_graph.yaml", "action_graph.antagonist.yaml"]),
            ("canon", "定石", ["canon.yaml", "canon.antagonist.yaml"]), ("effects", "効果", ["effects.yaml"]),
            ("rules", "ルール", ["rules.yaml"]), ("qd", "候補の分類", ["qd.yaml"])]


def document(handler, title, body):
    jobs = handler.server.job_store
    doc = pages.document(title, body, phase="world", job_store=jobs, pin=data.pinned_target(jobs), page_class="run-observer", stylesheets=('run-workspace.css','genre-workspace.css'), scripts=('genre-workspace.js',))
    handler._send_html(doc)


def new(handler):
    from viewer.story_genre_pages import show
    show(handler)


def detail(handler, ident):
    store = LibraryStore(handler.server.job_store.configs.repo)
    snap = editor.snapshot(store, ident)
    genre = next(g for g in store.genres() if g['id'] == ident)
    worlds = store.worlds()
    initial = E(json.dumps({'snapshot':snap,'sections':SECTIONS,'defaults':editor.DEFAULTS},ensure_ascii=False))
    nav = ''.join(f'<button type="button" data-section="{key}">{label}<span data-dirty-section="{key}"></span></button>' for key,label,_ in SECTIONS)
    used = ''.join(f'<li><a href="/worlds/{quote(w["id"])}">{E(w["name"] or w["id"])}</a></li>' for w in worlds if w['id'] in genre['used_by'])
    options = '<option value="">世界を選んでください</option>'+''.join(f'<option value="{E(w["id"])}">{E(w["name"] or w["id"])}</option>' for w in worlds)
    body = f'''<div class="ge" data-genre-editor data-initial="{initial}"><header class="ge-heading"><div><h1>基本ルールを編集 <span data-genre-name>{E(genre['name'])}</span> <small class="ge-badge" data-dirty-badge>保存済み</small></h1><p>物語の展開と、人物の振る舞いを整えます。</p></div></header>
    <div class="ge-layout"><nav class="ge-nav" aria-label="編集する内容"><h2>編集する内容</h2>{nav}<a href="/worlds">← 世界一覧へ</a></nav>
    <section class="ge-edit ge-scroll" aria-label="ジャンルの編集項目"><header class="ge-section-heading"><div><h2 data-title>基本情報</h2><p data-help></p></div><button type="button" data-add hidden>＋ 追加</button></header><div class="ge-role" data-role hidden><button type="button" data-variant="0">主人公側</button><button type="button" data-variant="1">敵役側</button></div><div data-editor></div></section>
    <aside class="ge-inspector ge-scroll"><h2>この変更を確認</h2><section><h3>使っている世界</h3>{'<ul>'+used+'</ul>' if used else '<p>まだ使っている世界はありません。</p>'}<p class="ge-hint">新しく保存する実行設定に反映されます。保存済みの設定・結果は変わりません。</p></section><section><h3>世界と組み合わせて確認</h3><label class="ge-field">確認する世界<select data-world>{options}</select></label><button type="button" data-validate>変更内容を確認</button><p data-validation role="status">未確認</p><p class="ge-hint">下書きも確認します。シミュレーションは実行しません。</p></section><section><h3>変更した項目</h3><ul data-changes><li>変更はありません</li></ul></section></aside></div>
    <footer class="ge-footer"><p role="status" data-status>保存済みの設定を表示しています。</p><button type="button" data-reset>この項目の変更を戻す</button><button type="button" class="ge-primary is-confirm" data-save>この項目を保存</button></footer></div>'''
    document(handler, '基本ルールを編集', body)


def action(handler, ident, operation, body):
    store = LibraryStore(handler.server.job_store.configs.repo)
    if not isinstance(body, dict): raise ConfigError('request','入力を確認してください',code='bad_request')
    required = {'save':{'path','content','revision'},'parse':{'path','content'},'check':{'world_id','files','revision'}}[operation]
    if set(body) != required: raise ConfigError('request','入力項目を確認してください',code='bad_request')
    if operation == 'save': result = editor.save(store, ident, **body)
    elif operation == 'parse':
        editor._base(store, ident)
        value = editor.parse(body['path'],body['content'])
        result = {'value':value if editor._json_tree(value) else None,'form':editor._json_tree(value)}
    else: result = editor.validate_draft(store, ident, **body)
    handler._send_json(HTTPStatus.OK,result)
