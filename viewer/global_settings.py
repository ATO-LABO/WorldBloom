"""Full-width settings workspace. Secrets stay behind the existing settings API."""
import json
from urllib.parse import parse_qs, urlsplit
from datetime import datetime
from execution.library import LibraryStore
from execution.output_settings import read_output_settings
from execution.provenance import ConfigError
from viewer import pages, workbench_pages as wb
E, U = pages._escape, pages._url_segment
BACKENDS = {"claude-cli":"Claude Code CLI", "codex-cli":"Codex CLI", "anthropic":"Anthropic API", "openai":"OpenAI API", "ollama":"ローカル Ollama", "llama-server":"ローカルLLM（llama-server）", "none":"生成しない（プロンプト保存のみ）"}
LIMITS = (("max_calls","候補数の上限","件"),("call_timeout_seconds","1件の待ち時間","秒"),("wall_seconds","全体の待ち時間","秒"),("max_saved_response_bytes","応答の保存上限","bytes"))


def link(url, label, cls=""):
    return f'<a class="{cls}" href="{E(url)}">{E(label)}</a>'


def field(name, label, value, *, number=False, unit="", minimum=1):
    tag = ' type="number" step="1" min="'+str(minimum)+'"' if number else ' type="text" list="output-model-list" autocomplete="off"'
    return f'<label class="gs-field" for="gs-{E(name)}">{E(label)}<span class="gs-input"><input id="gs-{E(name)}" data-field="{E(name)}"{tag} value="{E(value)}"><span>{E(unit)}</span></span><small data-error-for="{E(name)}"></small></label>'


def output_panel(view, error):
    if error:
        return '<section class="gs-panel" id="output" data-gs-panel="output"><header class="gs-heading"><h1>文章生成の設定</h1></header><div class="gs-empty" role="alert">settings.json を読めません。設定ファイルを確認してから再読み込みしてください。</div></section>'
    options = ''.join(f'<option value="{E(k)}"'+(' selected' if k==view['backend'] else '')+f'>{E(v)}</option>' for k,v in BACKENDS.items())
    limits = ''.join(field('limits.'+k,label,view['limits'][k],number=True,unit=unit,minimum=0 if k=='max_calls' and view['backend']=='none' else 1) for k,label,unit in LIMITS[:3])
    advanced = field('limits.max_saved_response_bytes',LIMITS[3][1],view['limits']['max_saved_response_bytes'],number=True,unit='bytes')
    return ('<section class="gs-panel" id="output" data-gs-panel="output"><header class="gs-heading"><div><h1>文章生成の設定 <span class="gs-badge" data-gs-dirty>保存済み</span></h1><p>あらすじと本文の生成に使う設定です。</p></div></header>'
        '<form id="gs-output-form" data-wb="output-settings" data-gs-output><div class="gs-output-layout"><div class="gs-editor"><div class="gs-error" data-form-error role="alert"></div><fieldset data-gs-fields><legend class="gs-sr-only">文章生成の設定項目</legend><h2>生成先とモデル</h2>'
        '<div class="gs-source-fields"><label class="gs-field" for="f-backend">生成方式<select id="f-backend" name="backend" data-field="backend">'+options+'</select></label>'
        + field('model','モデル',view['model'] or '') + '</div><datalist id="output-model-list"></datalist><p class="gs-hint" data-gs-model-hint></p>'
        '<div class="gs-key" data-gs-key hidden><label class="gs-field" for="gs-key">APIキー<input id="gs-key" type="password" autocomplete="off" data-gs-key-input placeholder="変更する場合のみ入力"></label><div class="gs-test"><button type="button" data-gs-key-save>キーを保存</button><span data-gs-key-status role="status"></span></div><p class="gs-hint">キーはこのボタンで個別に保存します。保存済みの値は表示しません。</p></div>'
        '<div class="gs-test"><button type="button" data-gs-test>接続を確認</button><span data-gs-connection role="status">未確認</span></div><p class="gs-hint">接続先・実行環境を確認します。文章生成は行いません。</p>'
        '<section class="gs-limits"><h2>生成の上限</h2><div class="gs-limit-fields">'+limits+'</div><p class="gs-hint">1回の生成操作で使う上限です。</p></section>'
        '<details class="gs-advanced"><summary>詳細設定 <small>応答の保存上限</small></summary>'+advanced+'</details></fieldset></div>'
        '<aside class="gs-summary" aria-label="現在使っている設定"><h2>現在使っている設定</h2><span class="gs-saved">● 保存済み</span><dl data-gs-saved></dl><div class="gs-diff" data-gs-diff></div><div class="gs-scope"><h2>この変更が適用される範囲</h2><p>③ Sifting のあらすじ生成</p><p>④ 上映の本文生成</p><p class="gs-hint">保存後に開始する生成から適用されます。</p></div></aside></div>'
        '<footer class="gs-footer"><p data-gs-save-status role="status">保存済みの設定を表示しています</p><button type="button" data-gs-reset>変更を戻す</button><button type="submit" class="gs-primary is-confirm" data-gs-save>設定を保存</button></footer></form></section>')


def display_date(value):
    if not value: return "—"
    try: return datetime.fromisoformat(str(value)).astimezone().strftime("%Y/%m/%d %H:%M")
    except ValueError: return str(value)


def collection_panel(kind, items, worlds, selected):
    configs = kind == 'configs'
    title = '実行設定の保存版' if configs else 'ジャンル'
    description = '保存した条件を確認し、複製や実行に使います。' if configs else '物語の展開ルールと、それを使う世界を確認します。'
    create = link('/configs/new' if configs else '/genres/new','＋ 新しい実行設定' if configs else '＋ 新しいジャンル','gs-add')
    rows, details = [], []
    for item in items:
        ident = item['config_id'] if configs else item['id']
        label = item['label'] if configs else item.get('name') or item['id']
        selected_attr = ' aria-current="true"' if ident == selected else ''
        if configs:
            ev=item['evolution']; world=worlds.get(item['project_id'],item['project_id'])
            meta=f'{world} · {ev["generations"]}世代 × {ev["population"]}個体 × {ev["seeds"]}回'
            pairs=[('世界',world),('ジャンル',item['template_id']),('実行規模',f'{ev["generations"]}世代 × {ev["population"]}個体 × {ev["seeds"]}回'),('作成日時',display_date(item.get('created_at')))]
            body='<dl>'+''.join(f'<dt>{E(k)}</dt><dd>{E(v)}</dd>' for k,v in pairs)+'</dl><p class="gs-hint">保存版は編集不可です。条件を変える場合は複製してください。</p>'
            body+=link('/configs/'+U(ident),'実行条件を詳しく確認 ↗')
            body+='<details><summary>保存版の記録</summary><p>'+E(ident)+'</p><p>複製元：'+E(item.get('parent_config_id') or 'なし')+'</p></details>'
            footer=link('/configs/new?from='+U(ident),'複製して調整')+link('/configs/'+U(ident)+'/start','この設定で実行画面へ →','gs-primary')
        else:
            used=item['used_by'];meta=f'利用中の世界 {len(used)}件'
            body='<h3>使っている世界</h3>'+('<ul>'+''.join('<li>'+link('/worlds/'+U(w),worlds.get(w,w))+'</li>' for w in used)+'</ul>' if used else '<p>まだ利用している世界はありません。</p>')
            names={'actions.yaml':'行動のつながり','action_graph.yaml':'行動のつながり','canon.yaml':'状況ごとの定石','effects.yaml':'行動の効果','rules.yaml':'展開のルール','qd.yaml':'候補の分類軸'}
            body+='<h3>含まれる設定</h3><ul>'+''.join(f'<li>{E(names.get(f,f))}</li>' for f in item['files'])+'</ul><p class="gs-hint">ジャンルは複数の世界で共有される、物語の展開ルールです。</p>'
            footer=link('/genres/'+U(ident),'ジャンルを編集 →','gs-primary')
        search=label+' '+meta+' '+ident
        rows.append(f'<a class="gs-item" data-gs-item="{E(ident)}" data-search="{E(search)}" href="/configs?tab={kind}&amp;item={U(ident)}"{selected_attr}><strong>{E(label)}</strong><span>{E(meta)}</span></a>')
        details.append(f'<article class="gs-item-detail" data-gs-detail="{E(ident)}"'+('' if ident==selected else ' hidden')+f'><div class="gs-detail-scroll"><h2>{E(label)}</h2>{body}</div><footer class="gs-footer">{footer}</footer></article>')
    return (f'<section class="gs-panel" id="{kind}" data-gs-panel="{kind}"><header class="gs-heading"><div><h1>{title}</h1><p>{description}</p></div>{create}</header><div class="gs-collection"><div class="gs-list"><label class="gs-search">{title}を探す<input type="search" data-gs-search placeholder="名前で検索"></label><p class="gs-hint" data-gs-count>{len(items)}件</p><div class="gs-list-scroll">'+''.join(rows)+'<p data-gs-no-match hidden>一致する項目がありません。</p>'+('' if items else '<p>まだ登録されていません。</p>')+'</div></div><div class="gs-details">'+''.join(details)+('' if items else '<div class="gs-empty">左上のボタンから作成できます。</div>')+'</div></div></section>')


def render(handler):
    jobs=wb._job_store(handler)
    if jobs is None:
        handler._send_html(wb._guidance_page(phase="world")); return
    query=parse_qs(urlsplit(handler.path).query); active=query.get('tab',['output'])[0]
    if active not in ('output','configs','genres'): active='output'
    library=LibraryStore(jobs.configs.repo)
    worlds={w['id']:w.get('name') or w['id'] for w in library.worlds()}
    configs=jobs.configs.list(); genres=library.genres()
    requested=query.get('item',[None])[0]
    selected_config=next((c['config_id'] for c in configs if c['config_id']==requested),configs[0]['config_id'] if configs else None)
    selected_genre=next((g['id'] for g in genres if g['id']==requested),genres[0]['id'] if genres else None)
    view=None;error=False
    try: view=read_output_settings(getattr(handler.server,'settings_path',None))
    except ConfigError: error=True
    initial=E(json.dumps({'view':view,'labels':BACKENDS,'tab':active},ensure_ascii=False))
    nav='<nav class="gs-nav" aria-label="全体設定"><h2>全体設定</h2>'+''.join(f'<a href="/configs?tab={key}" data-gs-tab="{key}"'+(' aria-current="page"' if key==active else '')+f'>{label}</a>' for key,label in (('output','文章生成'),('configs','実行設定の保存版'),('genres','ジャンル')))+'<div class="gs-nav-back">'+link('/','← 世界一覧へ戻る')+'</div><small>すべての世界に共通</small></nav>'
    panels=[output_panel(view,error),collection_panel('configs',configs,worlds,selected_config),collection_panel('genres',genres,worlds,selected_genre)]
    for i,key in enumerate(('output','configs','genres')):
        if key != active: panels[i]=panels[i].replace('data-gs-panel="'+key+'"','data-gs-panel="'+key+'" hidden',1)
    content=f'<div class="gs-shell" data-global-settings data-initial="{initial}">{nav}<div class="gs-main">'+''.join(panels)+'</div></div>'
    doc=pages.document('全体設定',content,phase=None,job_store=jobs,pin=wb.data.pinned_target(jobs,configs=configs),page_class='run-observer')
    handler._send_html(doc.replace('</head>','<link rel="stylesheet" href="/static/run-workspace.css"><link rel="stylesheet" href="/static/global-settings.css"><script src="/static/global-settings.js" defer></script></head>'))
