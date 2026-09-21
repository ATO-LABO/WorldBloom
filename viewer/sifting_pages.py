"""Full-width Sifting: browse, compare and review before explicit generation."""
import json
from urllib.parse import parse_qs, urlsplit, urlencode
from execution.provenance import ConfigError
from execution.output_store import OutputStore
from execution.output_requests import eligible
from viewer import pages, workbench_pages as wb, output_pages, sifting_view
from viewer import generation_pages
E, U = pages._escape, pages._url_segment
LABELS = {"unclassified":"未分類", "held":"保留", "adopted":"採用", "rejected":"除外"}


def json_attr(value):
    return E(json.dumps(value, ensure_ascii=False))


def link(path, text, cls=""):
    return f'<a class="{cls}" href="{E(path)}">{text}</a>'


def badge(state):
    return f'<span class="sf-badge sf-{E(state)}">{LABELS.get(state, E(state))}</span>'


def picker(v, active):
    choices = '<option value="/selected">すべての実行</option>' if active == "tray" else ''
    for run in v["history"]:
        if run.get("publication_revision") is None: continue
        rid = run["run_id"]
        href = f'/selected?run={U(rid)}' if active == "tray" else (f'/exp/{U(run["experiment_name"])}' if active == "grid" else f'/runs/{U(rid)}/candidates')
        choices += f'<option value="{E(href)}"' + (' selected' if rid == v["run_id"] else '') + f'>{E(run.get("label") or (v["label"] if rid == v["run_id"] else run["experiment_name"]))}</option>'
    return '<label class="sf-picker">対象の実行<select data-sf-run>' + choices + '</select></label>'


def shell(handler, v, active, title, body, footer, *, step=1, initial=None):
    rid = v["run_id"]
    links = [("list", "候補一覧", f'/runs/{U(rid)}/candidates' if rid else '/selected'),
             ("grid", "格子で見る", f'/exp/{U(v["name"])}' if rid else '/selected'),
             ("tray", "Siftingトレイ", f'/selected?run={U(rid)}' if rid else '/selected')]
    nav = '<nav class="rw-sidebar" aria-label="Siftingメニュー">' + ''.join(
        f'<a href="{E(href)}"' + (' aria-current="page"' if key == active else '') + f'>{label}</a>' for key,label,href in links) + '</nav>'
    steps = '<ol class="sf-steps" aria-label="Siftingの進行">' + ''.join(f'<li' + (' aria-current="step"' if n == step else '') + f'><span>{n}</span>{label}</li>' for n,label in enumerate(("候補を選ぶ", "採用候補を確認", "本文を生成"),1)) + '</ol>'
    content = '<div class="rw-shell sf-shell" data-sifting data-initial="' + json_attr(initial or {}) + '">' + nav
    content += '<main class="sf-main"><header class="sf-heading"><h1>' + title + '</h1>' + steps + picker(v, active) + '</header>'
    content += '<div class="sf-notice" role="status" data-sf-notice hidden></div>' + body + '<footer class="sf-footer">' + footer + '</footer></main></div>'
    if "data-generation" not in content:
        content += generation_pages.surface(modal=True)
    doc = pages.document(title, content, phase="sifting", world=v.get("world"), run=v.get("name") or None, output_run=rid or None,
                         job_store=wb._job_store(handler), page_class="run-observer")
    handler._send_html(doc.replace('</head>', generation_pages.ASSETS + '<link rel="stylesheet" href="/static/run-workspace.css"><link rel="stylesheet" href="/static/sifting-workspace.css"><script src="/static/sifting-workspace.js" defer></script></head>'))


def inspector():
    tabs = ''.join(f'<button type="button" role="tab" id="sf-tab-{key}" aria-controls="sf-panel-{key}" aria-selected="{str(key=="story").lower()}" data-sf-tab="{key}">{label}</button>' for key,label in (("story","物語"),("note","選択とメモ"),("data","実験データ")))
    return ('<aside class="sf-inspector" aria-label="候補の内容"><header><button type="button" class="sf-mobile-back" data-sf-back>← 一覧に戻る</button>'
            '<div class="sf-inspector-heading"><h2 data-sf-title>候補を選択</h2><div><button type="button" data-sf-prev aria-label="前の候補">‹</button><button type="button" data-sf-next aria-label="次の候補">›</button></div></div>'
            '<p data-sf-meta></p></header><div class="sf-tabs" role="tablist" aria-label="候補情報">' + tabs + '</div>'
            '<div class="sf-reading"><section role="tabpanel" id="sf-panel-story" aria-labelledby="sf-tab-story" data-sf-panel="story"><h3>あらすじ</h3><p class="sf-story" data-sf-story>左から候補を選んでください。</p>'
            '<a data-sf-synopsis hidden>この候補のあらすじを作る →</a><p><a data-sf-detail hidden>物語と根拠を詳しく読む ↗</a></p><p><a data-sf-output hidden>生成状況・作品を確認 ↗</a></p><div class="sf-ending"><h3>結末</h3><p data-sf-ending></p></div></section>'
            '<section role="tabpanel" id="sf-panel-note" aria-labelledby="sf-tab-note" data-sf-panel="note" hidden><h3>選んだ理由</h3><label for="sf-note">この候補についてのメモ</label><textarea id="sf-note" data-sf-note rows="6" placeholder="気になった点・選んだ理由を残す"></textarea><p>メモは自動保存されます。</p></section>'
            '<section role="tabpanel" id="sf-panel-data" aria-labelledby="sf-tab-data" data-sf-panel="data" hidden><h3>実験データ</h3><dl data-sf-data></dl><a data-sf-raw hidden>原記録を読む ↗</a></section></div>'
            '<div class="sf-verdict" data-sf-verdict><fieldset><legend>この候補の判定</legend>' + ''.join(f'<label><input type="radio" name="sf-verdict" value="{key}"><span>{label}</span></label>' for key,label in LABELS.items()) + '</fieldset><p data-sf-reason></p><span data-sf-save role="status">判定・メモは自動保存。あとから変更できます。</span><button type="button" data-sf-retry hidden>再試行</button></div></aside>')


def candidate_row(c, *, grid=False):
    cid = c["candidate_id"]
    shade = max(0, min(1, c.get("quality") or 0))
    attr = f' style="--sf-quality:{shade}"' if grid else ''
    return (f'<article class="sf-candidate" data-candidate-id="{E(cid)}"{attr}>'
            f'<label class="sf-choice" hidden><input type="checkbox" data-sf-choice="{E(cid)}"><span>対象にする</span></label>'
            f'<button type="button" data-sf-open="{E(cid)}"><span class="sf-row-title"><strong>{E(c["label"])}</strong><span data-row-state>{badge(c["state"])}</span></span>'
            + ('' if grid else f'<span class="sf-snippet">{E(c["synopsis"][:120] or c["synopsis_state"])}</span>')
            + f'<small>品質 {E(c["quality_text"])} · {"到達" if c.get("reached") else "未到達"} · 第{E(c.get("generation"))}世代</small><small>{E(c.get("cell_key"))}</small></button></article>')


def candidates(handler, run_id, grid=False):
    if handler.repository.catalog is None:
        handler._send_html(wb._guidance_page(phase="sifting"))
        return
    query = parse_qs(urlsplit(handler.path).query, keep_blank_values=True)
    v = sifting_view.load(handler, run_id, query=query)
    items = v["visible"]
    base = f'/runs/{U(run_id)}/candidates'
    if grid: items = [c for c in v["items"] if c["representative"]]
    toolbar = '<div class="sf-list-toolbar">'
    if grid:
        toolbar += '<strong>各区画の代表候補</strong><button type="button" data-sf-mode="synopsis">あらすじの準備…</button><button type="button" data-sf-mode="compare">比較する…</button><button type="button" data-sf-fit>格子を全体表示</button>'
    else:
        state = query.get("state", [""])[0]
        for key,label in (("","すべて"),("adopted","採用"),("held","保留")):
            href = base + ('?' + urlencode({**{k:val[0] for k,val in query.items() if k not in ("state","candidate")}, "state":key}) if key or query else '')
            count = v["counts"].get(key,0) if key else len(v["items"])
            toolbar += link(href, f'{label} {count}', 'sf-pill' + (' active' if state == key else ''))
        toolbar += '<details class="sf-tools"><summary>絞り込み・並び替え</summary>' + wb._candidates_filter_form(run_id, query)
        toolbar += '<form method="get"><label>並び替え<select name="sort"><option value="quality">品質</option><option value="generation">世代</option><option value="seed">seed</option></select></label><label>順序<select name="dir"><option value="desc">高い・新しい順</option><option value="asc">低い・古い順</option></select></label><button>適用</button></form></details>'
        order = query.get("sort", ["quality"])[0]
        if order not in ("quality", "generation", "seed"): order = "quality"
        direction = "asc" if query.get("dir") == ["asc"] else "desc"
        toolbar = toolbar.replace(f'<option value="{order}">', f'<option value="{order}" selected>').replace(f'<option value="{direction}">', f'<option value="{direction}" selected>')
        preserved = ''.join(f'<input type="hidden" name="{E(k)}" value="{E(vals[0])}">' for k,vals in query.items() if k not in ("sort", "dir", "candidate") and vals)
        toolbar = toolbar.replace('<form method="get"><label>並び替え', '<form method="get">' + preserved + '<label>並び替え')
        toolbar += '<button type="button" data-sf-mode="synopsis">あらすじの準備…</button>'
    toolbar += '</div><div class="sf-mode-note" data-sf-mode-note hidden><span></span><button type="button" data-sf-mode="browse">候補選びに戻る</button><button type="button" data-sf-clear>対象を解除</button></div>'
    if grid:
        by_id = {c["candidate_id"]:c for c in items}
        listing = '<div class="sf-grid-scroll"><table class="sf-grid"><caption>変動の大きさ · 色の濃さは品質</caption><thead><tr><th>主導カテゴリ</th>' + ''.join(f'<th>{E({"low":"低", "mid":"中", "high":"高"}.get(b,b))}</th>' for b in v["bins"]) + '</tr></thead><tbody>'
        for category in v["categories"]:
            listing += f'<tr><th scope="row">{E(category)}</th>'
            for bin_name in v["bins"]:
                c = by_id.get(v["reps"].get(f'{category}|{bin_name}'))
                listing += '<td>' + (candidate_row(c,grid=True) if c else '<span class="sf-empty-cell">代表候補なし</span>') + '</td>'
            listing += '</tr>'
        listing += '</tbody></table></div><p class="sf-hint">各区画の代表候補です。全候補は「候補一覧」で確認できます。</p>'
    else:
        listing = '<div class="sf-list-scroll" data-sf-list>' + ''.join(candidate_row(c) for c in items) + ('<p class="sf-empty">条件に一致する候補はありません。絞り込みを解除してください。</p>' if not items else '') + '</div>'
    notice = '<p class="sf-warning">実行中のため選定は保存できません。' + (link(f'/jobs/{U(v["busy"][0]["job_id"])}','実行状況を見る →') if v["busy"] else '') + '</p>' if v["running"] else ''
    if v["output_error"]: notice += '<p class="sf-warning">稿の記録を読み取れないため件数を表示できません。生成状況を確認してください。</p>'
    if v["rep_error"]: notice += '<p class="sf-warning">代表候補の対応を確認できません。格子からの詳細・判定は利用できません。</p>'
    body = notice + '<div class="sf-workspace' + (' sf-grid-layout' if grid else '') + '"><section class="sf-list" aria-label="候補一覧">' + toolbar + listing + '</section>' + inspector().replace('type="radio"', 'type="radio" disabled').replace('data-sf-note rows', 'data-sf-note disabled rows') + '</div>'
    n = v["counts"].get("adopted",0)
    footer = f'<strong>採用済み <span data-sf-adopted>{n}</span>件</strong><span data-sf-footer-note>次の画面で対象を確認します</span><button type="button" class="sf-primary" data-sf-proceed{ " disabled" if not n else "" }>採用候補を確認（{n}件） →</button>'
    initial = {k:v[k] for k in ("run_id","revision","publication","running","items","reps","query")}
    initial.update(view="grid" if grid else "list", visible=[c["candidate_id"] for c in items])
    shell(handler,v,"grid" if grid else "list","格子で候補を探す" if grid else "候補を読む・選ぶ",body,footer,initial=initial)


def tray(handler, run_id=None, confirmation=None):
    if handler.repository.catalog is None:
        handler._send_html(wb._guidance_page(phase="sifting"))
        return
    query = parse_qs(urlsplit(handler.path).query, keep_blank_values=True)
    run_id = run_id or query.get("run", [None])[0]
    store = wb._job_store(handler)
    if not run_id:
        rows = handler.repository.selections.tray()
        history = handler.repository.catalog.history()
        v = dict(run_id="", name="", label="", history=history)
        content = '<div class="sf-cross"><h2>実行を選んで候補を確認</h2><p>すべての実行の採用・保留候補です。本文生成は実行を選んで行います。</p>'
        for record in history:
            entries = [e for e in rows if e["run_id"] == record["run_id"]]
            if not entries: continue
            n = sum(e["state"] == "adopted" for e in entries)
            content += '<article><h3>' + link(f'/selected?run={U(record["run_id"])}',E(record["experiment_name"])) + f'</h3><p>採用 {n}件 · 保留 {len(entries)-n}件</p>'
            for entry in entries:
                content += '<p>' + badge(entry['state']) + ' ' + link(f'/runs/{U(record["run_id"])}/candidates?candidate={U(entry["candidate_id"])}', E(wb._short_id(entry['candidate_id']))) + ' ' + E(entry.get('note', '')) + '</p>'
            content += '</article>'
        content += '</div>'
        return shell(handler,v,"tray","Siftingトレイ",content,'<span>対象の実行を選択してください</span>',step=2)
    state = query.get("state", ["adopted"])[0]
    if state not in ("adopted", "held"): state = "adopted"
    v = sifting_view.load(handler,run_id)
    plan = confirmation
    if state == "adopted" and plan is None:
        plan = output_pages.build_confirmation(handler,run_id,{**{k:vals for k,vals in query.items() if k in ("config",)},"kind":["narrate"]})
    if plan is None and state == "adopted": return
    entries = [c for c in v["items"] if c["state"] == state]
    toolbar = '<div class="sf-list-toolbar">' + ''.join(link(f'/selected?run={U(run_id)}&state={key}',f'{label} {v["counts"].get(key,0)}','sf-pill' + (' active' if state == key else '')) for key,label in (("adopted","採用"),("held","保留"))) + '<span class="sf-hint">保留は生成対象に含まれません</span></div>'
    rows = ''
    for c in entries:
        status = c["status"].get("narrate")
        rows += f'<article class="sf-tray-card"><header><strong>{E(c["label"])}</strong>{badge(c["state"])}</header><small>品質 {E(c["quality_text"])} · 第{E(c.get("generation"))}世代</small><p>{E(c["synopsis"][:300] or c["synopsis_state"])}</p><p class="sf-note-preview"><b>選んだ理由</b> {E(c["note"] or "メモはありません")}</p><p>' + link(f'/runs/{U(run_id)}/candidates?candidate={U(c["candidate_id"])}','内容を読む ↗')
        rows += f' <button type="button" data-sf-tray-state="{E(c["candidate_id"])}" data-state="{"held" if state=="adopted" else "adopted"}"' + (' disabled' if v["running"] or (state=="held" and not c.get("screenable")) else '') + f'>{"保留に戻す" if state=="adopted" else "採用する"}</button></p>'
        if status: rows += '<p>' + E(output_pages.ENTRY_STATUS_LABELS.get(status,status)) + ' ' + link(c["output_href"], '生成状況・作品を確認 ↗') + '</p>'
        if not c.get("screenable"): rows += '<p class="sf-warning">採用・生成できません：' + E(c["availability"]) + '</p>'
        rows += '</article>'
    if not rows: rows = '<p class="sf-empty">この判定の候補はありません。</p>'
    request = None
    if state == "adopted":
        aside, action, request = generation_summary(handler,v,plan)
    else:
        aside = '<aside class="rs-summary"><h2>保留候補の見直し</h2><p>内容を確認し、本文にしたい候補を採用してください。</p></aside>'
        action = link(f'/selected?run={U(run_id)}','採用候補を確認 →','sf-primary')
    body = '<div class="sf-tray-layout"><section class="sf-tray-list">' + toolbar + rows + '</section>' + aside + '</div>'
    footer = link(f'/runs/{U(run_id)}/candidates','← 候補選びに戻る') + '<span data-sf-save role="status"></span><button type="button" data-sf-retry hidden>再試行</button>' + action
    initial = dict(view="tray",run_id=run_id,revision=v["revision"],request=request,items=v["items"],running=v["running"])
    shell(handler,v,"tray","採用候補を確認する" if state=="adopted" else "保留候補を見直す",body,footer,step=2,initial=initial)


def generation_summary(handler,v,plan):
    errors = list(plan["errors"])
    request = plan["request"]
    accepted = []
    if request:
        try: accepted = eligible(OutputStore(wb._job_store(handler).configs.control),request)
        except (ConfigError,OSError,ValueError) as e: errors.append(str(e))
    if v["output_error"]:
        errors.append("生成記録を確認できません。復旧してから対象を確認してください。")
    if v["running"]:
        errors.append("この実行には処理中のジョブがあります。完了後に確認してください。")
    generation = plan.get("generation") or {}
    limit = generation.get("limits",{}).get("max_calls",0)
    prompt_only = generation.get("backend") == "none"
    if request and not prompt_only and limit == 0: errors.append("呼出し上限が0件です。生成設定を変更してください。")
    if errors or not accepted:
        request = None
    else:
        # Freeze exactly the reviewed subset; later output changes cannot add targets.
        request = {**request, "candidate_ids": list(accepted),
                   "synopsis_refs": {cid:ref for cid,ref in request["synopsis_refs"].items() if cid in accepted}}
    m = len(accepted)
    text = "プロンプト保存" if prompt_only else "本文生成"
    aside = f'<aside class="rs-summary sf-generation"><h2>今回の{text}</h2><strong class="sf-total">{m} 件</strong><p>採用 {v["counts"].get("adopted",0)}件のうち、今回の対象</p>'
    if plan["mode"] == "regenerate": aside += '<p>再生成：別の稿として保存します。</p>'
    aside += '<dl><dt>生成するもの</dt><dd>' + ('プロンプトのみ（本文は生成されません）' if prompt_only else '物語の本文') + '</dd><dt>生成設定</dt><dd>' + E(str(generation.get("backend") or "未設定")) + ' / ' + E(str(generation.get("model") or "—")) + '</dd></dl>'
    aside += '<p>' + link('/configs#output','設定を確認・変更 ↗') + '</p>'
    if plan.get("check") and not plan["check"].get("available"):
        errors.append(wb.availability_label(plan["check"]))
    if errors: aside += '<div class="sf-warning">' + ''.join(f'<p>{E(e)}</p>' for e in errors) + '</div>'
    if plan["legacy"]:
        options = ''.join(f'<option value="{E(c["config_id"])}"' + (' selected' if c['config_id'] == plan['config_id'] else '') + f'>{E(c["label"])}</option>' for c in wb._job_store(handler).configs.list())
        fixed = {"kind": ["narrate"], "mode": [plan["mode"]], "candidate": plan["candidate_ids"]}
        current_query = parse_qs(urlsplit(handler.path).query)
        for key in ("ack", "from_output"):
            if key in current_query: fixed[key] = current_query[key]
        hidden = ''.join(f'<input type="hidden" name="{E(k)}" value="{E(val)}">' for k,vals in fixed.items() for val in vals)
        aside += f'<form action="/runs/{U(v["run_id"])}/generate">{hidden}<label>旧実験の文章化用設定<select name="config">{options}</select></label><button>この設定で確認</button></form>'
    aside += '<details><summary>詳細設定を確認</summary><p>選定版 ' + str(plan["selection_revision"]) + '</p>' + ''.join(f'<p>{E(k)}：{E(val)}</p>' for k,val in generation.get("limits",{}).items()) + '<p>今回の生成対象</p><ul>' + ''.join(f'<li>{E(wb._short_id(cid))}</li>' for cid in accepted) + '</ul></details>'
    aside += '<h3>生成後の流れ</h3><ol><li>生成状況を確認</li><li>④ 上映で作品を読む</li></ol><p>開始するまで文章生成は行われません。</p></aside>'
    if request:
        label = f'{m}件のプロンプトを保存' if prompt_only else (f'上限{limit}件で本文生成を開始' if limit<m else f'{m}件の本文を生成')
        if plan["attempt_ids"]:
            label = '確認して再生成を開始'
            action = '<label class="sf-ack"><input type="checkbox" data-sf-ack>結果不明の試行を再生成します。二重生成の可能性を確認しました。</label>'
        else: action = ''
        action += f'<button type="button" class="sf-primary" data-sf-generate>{label}</button>'
    else:
        adopted = [c for c in v['items'] if c['state'] == 'adopted']
        complete = bool(adopted) and all(c['status'].get('narrate') == 'ok' for c in adopted)
        label = '生成済みの作品を見る →' if complete else '生成状況・作品を確認 →'
        action = link(f'/outputs?run={U(v["run_id"])}',label,'sf-primary') if adopted else link(f'/runs/{U(v["run_id"])}/candidates','候補選びに戻る →','sf-primary')
    return aside, action, request


def synopsis_confirm(handler,run_id,view):
    v = sifting_view.load(handler,run_id)
    # The existing confirmation retains retry acknowledgements and immutable request details.
    args = {k:val for k,val in view.items() if k != "experiment_name"}
    body = '<div class="sf-confirm-scroll">' + generation_pages.surface({"plan_url":handler.path}) + '<noscript>' + output_pages._render_generate_body(**args) + '</noscript></div>'
    shell(handler,v,"list","あらすじの生成内容を確認",body,link(f'/runs/{U(run_id)}/candidates','← 候補選びに戻る'),step=1)
