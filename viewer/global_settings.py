"""Full-width settings workspace. Secrets stay behind the existing settings API."""
import json
import re
from urllib.parse import parse_qs, urlsplit
from datetime import datetime
from execution.evolution_settings import read_evolution_settings
from execution.library import LibraryStore
from execution.output_settings import read_output_settings
from execution.provenance import ConfigError
from execution.rationality_settings import read_jev_settings
from viewer import pages, ui, workbench_pages as wb
E, U = pages._escape, pages._url_segment
BACKENDS = {"claude-cli":"Claude Code CLI", "codex-cli":"Codex CLI", "anthropic":"Anthropic API", "openai":"OpenAI API", "ollama":"ローカル Ollama", "llama-server":"ローカルLLM（llama-server）", "none":"生成しない（プロンプト保存のみ）"}
LIMITS = (("max_calls","AI呼出しの上限","件"),("call_timeout_seconds","1件の待ち時間","秒"),("wall_seconds","全体の待ち時間","秒"),("max_saved_response_bytes","応答の保存上限","bytes"))


def link(url, label, cls=""):
    return ui.button(label, href=url, kind='advance' if 'gs-primary' in cls else 'secondary', class_name=cls) if cls else f'<a href="{E(url)}">{E(label)}</a>'


def field(name, label, value, *, unit="", minimum=1, maximum=None):
    tag = ' type="number" step="1" min="'+str(minimum)+'"'+('' if maximum is None else ' max="'+str(maximum)+'"')
    return f'<label class="gs-field ui-field" for="gs-{E(name)}"><span>{E(label)}</span><span class="gs-input ui-input-group"><input id="gs-{E(name)}" data-field="{E(name)}"{tag} value="{E(value)}"><span>{E(unit)}</span></span><small data-error-for="{E(name)}"></small></label>'


def settings_footer(*, compute=False):
    key = 'compute-' if compute else ''
    status_key = 'compute-status' if compute else 'save-status'
    return ui.footer(f'<p data-gs-{status_key} role="status">保存済みの設定を表示しています</p>'
        + ui.button('変更を戻す', attrs={f'data-gs-{key}reset':True})
        + ui.button('設定を保存', kind='confirm', class_name='gs-primary is-confirm',
                    attrs={'type':'submit', f'data-gs-{key}save':True}), class_name='gs-footer')


def gemma_setup():
    return (
        '<section class="gs-gemma" data-gemma-setup aria-labelledby="gs-gemma-title">'
        '<div class="ui-section-head"><h2 id="gs-gemma-title">Gemma 4</h2></div>'
        '<div class="gs-test"><button class="ui-button" type="button" data-gs-gemma-profile>'
        'Gemma 4を選ぶ</button>'
        '<button class="ui-button" type="button" data-gemma-refresh>導入状況を再確認</button></div>'
        '<p data-gemma-state role="status">導入状況を確認中…</p>'
        '<p data-gemma-link role="status">保存済みの生成先を確認中…</p>'
        '<p class="ui-hint" data-gs-profile-note>Gemma 4を選んで「設定を保存」を押すと、生成先が切り替わります。'
        '導入状況の確認では文章生成は行いません。</p>'
        '<details class="ui-settings-help" data-gemma-guide>'
        '<summary>Gemma 4の導入手順</summary>'
        '<div data-gemma-unreachable hidden><p>Ollamaの起動状況と接続先を確認してください。</p>'
        '<a href="https://ollama.com/download" target="_blank" rel="noopener noreferrer">'
        'Ollama公式のインストール案内</a></div>'
        '<p>Ollamaを実行しているPCで、次のコマンドを実行してください。'
        '別のPCのOllamaを使っている場合は、そのPCで実行します。</p>'
        '<p><code>ollama pull gemma4:e4b-it-qat</code></p>'
        '<p>完了したら「導入状況を再確認」を押してください。</p></details></section>'
    )


def settings_tabs(group, items, active, label, *, hook=None):
    return ui.tabs(''.join(ui.button(text, attrs={
        'id': f'{group}-tab-{key}', 'role': 'tab',
        'aria-selected': str(key == active).lower(), 'aria-controls': f'{group}-panel-{key}',
        'tabindex': '0' if key == active else '-1', 'disabled': disabled,
        **({hook: key} if hook else {}),
    }) for key, text, disabled in items), label=label, attrs={'role': 'tablist'})


def tab_panel(group, key, active, content, *, class_name=""):
    return (f'<section id="{group}-panel-{key}" class="{class_name}" role="tabpanel" '
            f'aria-labelledby="{group}-tab-{key}"'+('' if key == active else ' hidden')+f'>{content}</section>')


def output_panel(view, error, jev=None):
    heading = ui.heading('AIの設定', 'すべての世界に共通する文章生成と合理性の判定器を設定します。', class_name='gs-heading')
    start = '<section class="gs-panel" id="output" data-gs-panel="output">'
    if error:
        return start + heading + '<div class="gs-empty" role="alert">settings.json を読めません。設定ファイルを確認してから再読み込みしてください。</div></section>'

    items = [('ai', '使用するAI', False), ('limits', '生成の上限', False)]
    if jev is not None:
        items.append(('jev', '合理性の判定器（Jev）', False))
    tabs = settings_tabs('gs', items, 'ai', 'AIの設定の切り替え', hook='data-gs-output-tab')
    jev_tab = tab_panel('gs', 'jev', 'ai', jev_panel(jev)) if jev is not None else ''
    limits = ''.join(field('limits.'+k, label, view['limits'][k], unit=unit,
                           minimum=0 if k=='max_calls' and view['backend']=='none' else 1)
                     for k, label, unit in LIMITS)

    return (start + heading
        + '<form id="gs-output-form" data-wb="output-settings" data-gs-output novalidate>'
        + tabs + '<div class="gs-form-scroll ui-content"><div class="ui-error" data-form-error role="alert"></div>'
        '<fieldset data-gs-fields><legend class="gs-sr-only">文章生成の設定項目</legend>'
        '<section id="gs-panel-ai" role="tabpanel" aria-labelledby="gs-tab-ai" data-gs-output-panel="ai">'
        '<h2>保存済みの生成先</h2><dl data-gs-saved></dl>'
        + gemma_setup() + '</section>'
        '<section id="gs-panel-limits" role="tabpanel" aria-labelledby="gs-tab-limits" data-gs-output-panel="limits" hidden>'
        '<h2>保存済みの上限</h2><dl data-gs-saved-limits></dl>'
        '<h2>上限を変更</h2><p class="ui-hint">生成方式ごとに、1回の生成操作で使う上限を設定します。'
        '編集中の生成方式は <strong data-gs-limits-backend></strong> です。</p>'
        '<div class="gs-limit-fields">'+limits+'</div></section></fieldset>'
        + jev_tab + '<div data-gs-output-details><div class="gs-diff" data-gs-diff></div>'
        '<details class="ui-settings-help gs-scope"><summary>この設定を使う画面</summary>'
        '<p>① 概要からの設定作成</p><p>③ Sifting のあらすじ生成</p><p>④ 上映の本文生成</p>'
        '<p class="ui-hint">保存後に開始する生成から適用されます。</p></details></div></div>'
        + settings_footer(compute=False) + '</form></section>')


def jev_panel(jev):
    """Render the Jev key editor and its independent save action."""
    status = (f"有効（{E(jev['model'])}、{E(display_date(jev['verified_at']))} 確認）"
              if jev['available'] else "未設定")
    return ('<section class="gs-jev"><h2>合理性の判定器（Jev）</h2>'
        '<p>κ&gt;0 の実験がどの判定器を使うかは、実行設定の画面で選びます。ここでは TypeSafe（api.typesafe.ai）の APIキーを登録します。</p>'
        '<label class="gs-field ui-field" for="gs-jev-key"><span>APIキー</span><input id="gs-jev-key" type="password" autocomplete="off" data-gs-jev-key-input placeholder="変更する場合のみ入力"></label>'
        '<div class="gs-test"><button class="ui-button" type="button" data-gs-jev-key-save>キーを保存</button>'
        f'<span data-gs-jev-key-status role="status">{status}</span></div>'
        '<p class="gs-hint ui-hint">κ&gt;0 の実験で Jev を選ぶと、主人公の状況テキストが TypeSafe (api.typesafe.ai) に送られます。保存済みのキーは表示しません。</p>'
        '</section>')


def compute_panel(evolution, error):
    start = '<section class="gs-panel" id="compute" data-gs-panel="compute">'
    if error:
        return start + ui.heading('計算の設定', class_name='gs-heading') + '<div class="gs-empty" role="alert">settings.json を読めません。設定ファイルを確認してから再読み込みしてください。</div></section>'

    tabs = [('parallel', 'GA の並列数', False), ('gpu', 'GPU ガード', False)]
    group = 'gs-compute'
    parallel = (
        '<h2>GA の並列数</h2><p>保存済みの並列数 <strong data-gs-compute-saved-processes>'
        f'{evolution["processes"]}</strong></p>'
        + field('processes', 'GA の並列数', evolution['processes'], minimum=1,
                maximum=evolution['cpu_count']).replace('<input ', '<input required ', 1)
        + f'<p class="ui-hint">GA の個体評価を同時に何本走らせるか。結果は変わらず、速さだけが変わります。開発PC（20コア）の実測では 8 前後で頭打ちでした。このPCのコア数は {evolution["cpu_count"]}、既定は {evolution["default"]} です。</p>'
        '<p class="ui-hint">保存後に開始するGA実験から適用されます。</p>'
    )
    gpu = (
        '<h2>GPU ガード（温度）</h2><p>保存済みの状態 <strong data-gs-compute-saved-thermal></strong></p>'
        '<label class="gs-check" for="gs-thermal-enabled"><input id="gs-thermal-enabled" type="checkbox" '
        'data-field="thermal_enabled"'+(' checked' if evolution['thermal_enabled'] else '')+'>GPU ガードを使う</label>'
        + field('pause_at', '一時停止する温度', evolution['pause_at'], unit='℃',
                minimum=evolution['pause_min'], maximum=evolution['pause_max']).replace('<input ', '<input required ', 1)
        + f'<p class="ui-hint">ローカルLLMで文章を生成する前に GPU 温度を確かめ、この温度以上なら 8℃ 下がるまで待ちます（最長10分）。既定はオン・{evolution["pause_default"]}℃です。</p>'
        '<p class="ui-hint">保存後に開始する生成から適用されます。</p>'
    )
    panels = tab_panel(group, 'parallel', 'parallel', parallel) + tab_panel(group, 'gpu', 'parallel', gpu)
    return (start + ui.heading('計算の設定', 'GAの並列数とGPUの温度を設定します。',
        class_name='gs-heading', title_extra=' <span class="gs-badge" data-gs-compute-dirty>保存済み</span>')
        + '<form id="gs-compute-form" data-wb="compute-settings" data-gs-compute novalidate>'
        + settings_tabs(group, tabs, 'parallel', '計算の設定の切り替え')
        + '<div class="gs-form-scroll ui-content"><div class="ui-error" data-compute-form-error role="alert"></div>'
        '<fieldset data-gs-compute-fields><legend class="gs-sr-only">計算の設定項目</legend>'
        + panels + '</fieldset></div>' + settings_footer(compute=True) + '</form></section>')


def display_date(value):
    if not value: return "—"
    try: return datetime.fromisoformat(str(value)).astimezone().strftime("%Y/%m/%d %H:%M")
    except ValueError: return str(value)


def collection_panel(items, worlds, selected, *, show_detail=False):
    group = 'gs-configs'
    active = 'detail' if items and show_detail else 'list'
    tabs = settings_tabs(group, (('list', '保存版一覧', False), ('detail', '保存版の内容', not items)),
                         active, '実行設定の保存版の切り替え')
    rows, details = [], []
    for item in items:
        ident = item['config_id']
        label = item['label']
        selected_attr = ' aria-current="true"' if ident == selected else ''
        ev = item['evolution']
        world = worlds.get(item['project_id'], item['project_id'])
        scale = f'{ev["generations"]}世代 × {ev["population"]}個体 × {ev["seeds"]}回'
        meta = f'{world} · {scale}'
        pairs = [('世界', world), ('基本ルール', item['template_id']), ('実行規模', scale),
                 ('作成日時', display_date(item.get('created_at')))]
        body = '<dl>'+''.join(f'<dt>{E(k)}</dt><dd>{E(v)}</dd>' for k,v in pairs)+'</dl>'
        body += '<p class="ui-hint">保存版は編集不可です。条件を変える場合は複製してください。</p>'
        body += link('/configs/'+U(ident), '実行条件を詳しく確認 ↗')
        body += '<details><summary>保存版の記録</summary><p>'+E(ident)+'</p><p>複製元：'+E(item.get('parent_config_id') or 'なし')+'</p></details>'
        actions = ui.button('複製して調整', href='/configs/new?from='+U(ident))
        actions += link('/configs/'+U(ident)+'/start', 'この設定で実行画面へ →', 'gs-primary')
        rows.append(f'<a class="gs-item" data-gs-item="{E(ident)}" data-search="{E(label+" "+meta+" "+ident)}" '
                    f'href="/configs?tab=configs&amp;item={U(ident)}"{selected_attr}><strong>{E(label)}</strong><span>{E(meta)}</span></a>')
        details.append(f'<article class="gs-item-detail" data-gs-detail="{E(ident)}"'
                       + ('' if ident == selected else ' hidden')
                       + f'><div class="gs-detail-scroll ui-content"><h2 tabindex="-1">{E(label)}</h2>{body}</div>'
                       + ui.footer(actions) + '</article>')

    listing = ('<div class="gs-list-tools"><label class="ui-field">保存版を探す'
               '<input type="search" data-gs-search placeholder="名前で検索"></label>'
               f'<p class="ui-hint" data-gs-count role="status">{len(items)}件</p></div>'
               '<div class="gs-list-scroll ui-content">'+''.join(rows)
               + '<p data-gs-no-match hidden>一致する項目がありません。</p>'
               + ('' if items else '<p>まだ保存版がありません。「新しい実行設定」から作成できます。</p>')+'</div>')
    heading = ui.heading('実行設定の保存版', '保存した条件を確認し、複製や実行に使います。',
                         actions=link('/configs/new', '＋ 新しい実行設定', 'gs-add'), class_name='gs-heading')
    return ('<section class="gs-panel" id="configs" data-gs-panel="configs">' + heading + tabs
            + tab_panel(group, 'list', active, listing, class_name='gs-config-view')
            + tab_panel(group, 'detail', active, ''.join(details), class_name='gs-config-view') + '</section>')


def render(handler):
    jobs=wb._job_store(handler)
    if jobs is None:
        handler._send_html(wb._guidance_page(phase="world")); return
    query=parse_qs(urlsplit(handler.path).query); active=query.get('tab',['output'])[0]
    if active not in ('output','compute','configs'): active='output'
    library=LibraryStore(jobs.configs.repo)
    worlds={w['id']:w.get('name') or w['id'] for w in library.worlds()}
    configs=jobs.configs.list()
    requested=query.get('item',[None])[0]
    selected_config=next((c['config_id'] for c in configs if c['config_id']==requested),configs[0]['config_id'] if configs else None)
    view=None;error=False
    try: view=read_output_settings(getattr(handler.server,'settings_path',None))
    except ConfigError: error=True
    evolution=None;compute_error=False
    try: evolution=read_evolution_settings(getattr(handler.server,'settings_path',None))
    except ConfigError: compute_error=True
    jev=None
    try: jev=read_jev_settings(getattr(handler.server,'settings_path',None))
    except ConfigError: jev=None
    initial=E(json.dumps({'view':view,'labels':BACKENDS,'tab':active,'compute':evolution},ensure_ascii=False))
    nav='<h2>全体設定</h2>'+''.join(f'<a href="/configs?tab={key}" data-gs-tab="{key}"'+(' aria-current="page"' if key==active else '')+f'>{label}</a>' for key,label in (('output','AIの設定'),('compute','計算'),('configs','実行設定の保存版')))+'<div class="gs-nav-back">'+link('/version','バージョン情報')+'<br>'+link('/','← 世界一覧へ戻る')+'</div><small>すべての世界に共通</small>'
    workspace = query.get('creation_workspace', [''])[0]
    if re.fullmatch(r'[a-f0-9]{32}', workspace):
        target = '/worlds/new?mode=create&screen=workflow&step=1&workspace=' + workspace
        nav += ui.button('← 保存した下書きの①へ戻る', href=target)
    nav=ui.navigation(nav, label='全体設定', class_name='gs-nav')
    panels=[output_panel(view,error,jev),compute_panel(evolution,compute_error),collection_panel(configs,worlds,selected_config,show_detail=bool(requested))]
    for i,key in enumerate(('output','compute','configs')):
        if key != active: panels[i]=panels[i].replace('data-gs-panel="'+key+'"','data-gs-panel="'+key+'" hidden',1)
    content=f'<div class="gs-shell ui-workspace ui-workspace--nav" data-global-settings data-initial="{initial}">{nav}<div class="gs-main">'+''.join(panels)+'</div></div>'
    doc=pages.document('全体設定',content,phase=None,job_store=jobs,pin=wb.data.pinned_target(jobs,configs=configs),page_class='settings-workspace', stylesheets=('global-settings.css',), scripts=('ui-tabs.js','global-settings.js','gemma-setup.js'))
    handler._send_html(doc)
