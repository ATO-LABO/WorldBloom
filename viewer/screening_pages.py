"""Full-width screening workspace. GET navigation never starts generation."""
import json
from urllib.parse import urlencode
from execution.output_store import OutputStore
from viewer import pages, workbench_pages as wb, screening_view as view, world_usage_badge
E, U = pages._escape, pages._url_segment


def href(query=None, **changes):
    values = {k: v[0] for k, v in (query or {}).items() if k in ("run", "q", "sort", "state", "candidate", "work_run", "output") and v}
    values.update(changes)
    return "/outputs?" + urlencode({k: v for k, v in values.items() if v is not None and v != ""})


def link(url, label, extra=""):
    return f'<a href="{E(url)}" {extra}>{E(label)}</a>'


def shell(handler, title, content, *, query, history, active="reader", world=None, polling=None):
    rid = query.get("run", [None])[0]
    run = next((r for r in history if r["run_id"] == rid), {})
    if world is None and run.get("config_id"):
        try:
            world = wb._config_world(wb._job_store(handler).configs.get(run["config_id"]))
        except view.ERRORS:
            pass
    navigation = '<nav class="rw-sidebar sc-nav" aria-label="上映メニュー">'
    for key, label in (("reader", "作品を読む"), ("history", "生成履歴")):
        navigation += link(href(run=rid, view=key if key == "history" else None), label, 'aria-current="page"' if key == active else '')
    navigation += link(f'/runs/{U(rid)}/candidates' if rid else '/selected', '← Siftingへ戻る', 'class="sc-back"') + '</nav>'
    options = '<option value="/outputs' + ('?view=history' if active == 'history' else '') + '">すべての実行</option>'
    for item in history:
        url = href(run=item["run_id"], view='history' if active == 'history' else None)
        options += f'<option value="{E(url)}"' + (' selected' if item['run_id'] == rid else '') + f'>{E(item.get("label") or item["experiment_name"])}</option>'
    data = E(json.dumps(polling or [], ensure_ascii=False))
    body = (f'<div class="rw-shell sc-shell" data-screening data-poll="{data}">' + navigation + '<div class="sc-main">'
            f'<header class="sc-heading"><h1>{E(title)}</h1><label>対象の実行<select data-sc-navigate aria-label="対象の実行">{options}</select></label></header>'
            '<div class="sc-update" role="status" data-sc-update hidden><span></span><button type="button" data-sc-refresh>表示を更新</button></div>'
            + content + '</div></div>')
    doc = pages.document(title, body, phase="stage", world=world, run=run.get("experiment_name"), output_run=rid,
                         job_store=wb._job_store(handler), page_class="run-observer")
    handler._send_html(doc.replace('</head>', '<link rel="stylesheet" href="/static/run-workspace.css"><link rel="stylesheet" href="/static/screening-workspace.css"><script src="/static/screening-workspace.js" defer></script></head>'))


def render(handler, output_id=None):
    from viewer import output_pages
    query = wb._query(handler)
    job_store = wb._job_store(handler)
    if job_store is None:
        handler._send_html(wb._guidance_page(phase="stage"))
        return
    if output_id:
        chosen_output = job_store.output(output_id)
        query["run"] = [chosen_output["request"]["run_id"]]
        query["output"] = [output_id]
    rid = query.get("run", [None])[0]
    try:
        history = handler.repository.catalog.history()
    except view.ERRORS:
        history = []
    try:
        outputs = job_store.outputs()
    except view.ERRORS:
        shell(handler, "作品を読む", '<div class="sc-empty">作品一覧を読み込めません。保存記録を確認してから再読み込みしてください。</div>', query=query, history=history)
        return
    if rid:
        outputs = [o for o in outputs if not o.get("request") or o["request"].get("run_id") == rid]
    if query.get("view") == ["history"]:
        names = {r["run_id"]: r["experiment_name"] for r in history}
        try:
            jobs = {j["job_id"]: j for j in job_store.list()}
        except view.ERRORS:
            jobs = {}
        store = OutputStore(job_store.configs.control)
        dates = {o["output_id"]: view.created_at(store, o, jobs) for o in outputs if o.get("request")}
        records = output_pages.render_outputs_list(outputs, names, dates).replace("<th>output_id</th>", "<th>生成日時 / 記録</th>")
        # Keep history focused on attempts; the explicit record retains recovery controls.
        for output in outputs:
            oid = output["output_id"]
            records = records.replace(f'/outputs/{U(oid)}"', f'/outputs/{U(oid)}?view=record"')
        shell(handler, "生成履歴", '<div class="sc-history">' + records + '</div>', query=query, history=history, active="history")
        return
    works = view.build(job_store, outputs)
    names = {r["run_id"]: r.get("label") or r["experiment_name"] for r in history}
    total = len(works)
    ready = sum(w["preferred"]["status"] == "ok" for w in works)
    text_query = query.get("q", [""])[0].strip()
    if text_query:
        works = [w for w in works if text_query.casefold() in (w["title"] + " " + (w["preferred"]["text"] or "") + " " + names.get(w["run_id"], w["run_id"])).casefold()]
    if query.get("state") == ["ready"]:
        works = [w for w in works if w["preferred"]["status"] == "ok"]
    order = query.get("sort", ["new"])[0]
    works.sort(key=lambda w: (w["created_at"] is None, -(w["created_at"] or 0) if order != "old" else (w["created_at"] or 0), w["run_id"], w["candidate_id"]))
    selected = next((w for w in works if w["candidate_id"] == query.get("candidate", [None])[0] and w["run_id"] == query.get("work_run", [rid])[0]), None)
    if selected is None and query.get("output"):
        selected = next((w for w in works if any(d["output_id"] == query["output"][0] for d in w["drafts"])), None)
    selected = selected or (works[0] if works else None)
    form = '<form class="sc-filter" method="get" action="/outputs">'
    if rid: form += f'<input type="hidden" name="run" value="{E(rid)}">'
    form += f'<label class="sc-search">作品を探す<input type="search" name="q" value="{E(text_query)}" placeholder="候補ID・本文で検索"></label>'
    form += '<div><label>表示<select name="state"><option value="">すべて ' + str(total) + '</option><option value="ready"' + (' selected' if query.get("state") == ["ready"] else '') + '>本文あり ' + str(ready) + '</option></select></label>'
    form += '<label>並び順<select name="sort"><option value="new">新しい順</option><option value="old"' + (' selected' if order == 'old' else '') + '>古い順</option></select></label><button>適用</button></div></form>'
    rows = ''
    for work in works:
        d = work["preferred"]
        url = href(query, candidate=work["candidate_id"], work_run=work["run_id"], output=None)
        row = f'<strong>{E(work["title"])}</strong><span class="sc-badge">{E(view.LABELS.get(d["status"], d["status"]))}</span>'
        if any(draft["active"] for draft in work["drafts"]):
            row += '<span class="sc-badge">別稿を生成中</span>'
        row += f'<p>{E((d["text"] or "本文はまだありません。生成情報で状態を確認できます。")[:95])}</p><small>{E(names.get(work["run_id"], work["run_id"]))} · {len(work["drafts"])}稿</small>'
        rows += f'<a class="sc-work" href="{E(url)}"' + (' aria-current="true"' if work is selected else '') + '>' + row + '</a>'
    if not rows:
        rows = '<div class="sc-empty">' + ('条件に一致する作品がありません。' if total else '本文を生成した作品がここに並びます。') + '<p>' + link(f'/runs/{U(rid)}/candidates' if rid else '/selected', 'Siftingで採用候補を確認する →') + '</p></div>'
    broken = sum(not o.get("request") for o in outputs)
    if broken: rows += f'<p class="sc-empty">読み取れない生成記録が{broken}件あります。' + link(href(run=rid, view='history'), '生成履歴を確認') + '</p>'
    content = '<div class="sc-workspace"><aside class="sc-list" aria-label="作品一覧">' + form + f'<div class="sc-list-count">{len(works)}作品</div><div class="sc-rows">' + rows + '</div></aside>'
    polling = []
    if selected:
        chosen = next((d for d in selected["drafts"] if d["output_id"] == query.get("output", [None])[0]), selected["preferred"])
        # 段階4b: 拡張要素の使用バッジ（1候補だけなので毎回軽い）。
        usage_html = world_usage_badge.badge_for_run_candidate(
            handler.repository, selected["run_id"], selected["candidate_id"])
        content += reader(selected, chosen, works, query, job_store, names, usage_html)
        for d in selected["drafts"]:
            if d["active"]:
                polling.append({"output_id": d["output_id"], "candidate_id": selected["candidate_id"], "status": d["entry"].get("status")})
    else:
        content += '<section class="sc-reader sc-empty"><h2>作品を読む</h2><p>作品を選ぶと、この欄に本文が表示されます。</p></section>'
    content += '</div>'
    shell(handler, "作品を読む", content, query=query, history=history, polling=polling)


def reader(work, draft, works, query, job_store, names, usage_html=""):
    cid, rid, oid = work["candidate_id"], work["run_id"], draft["output_id"]
    record = f'/outputs/{U(oid)}?view=record#entry-{U(cid)}'
    choices = ''
    for d in reversed(work["drafts"]):
        url = href(query, candidate=cid, work_run=rid, output=d["output_id"])
        choices += f'<option value="{E(url)}"' + (' selected' if d is draft else '') + f'>{E(d["label"])} · {E(d["date"])}</option>'
    body = '<section class="sc-reader" aria-label="本文を読む"><header class="sc-reader-head"><button type="button" class="sc-mobile-back" data-sc-back>← 作品一覧</button>'
    body += f'<p class="sc-context">{E(names.get(rid, rid))}</p><div class="sc-title"><h2>{E(work["title"])}</h2><details class="sc-menu"><summary aria-label="作品の操作">…</summary><div>' + link(record, '生成記録・再生成を確認')
    if draft['status'] == 'ok': body += link(f'/outputs/{U(oid)}/entries/{U(cid)}/text', '本文を保存', 'download="story.txt"')
    body += '</div></details></div><div class="sc-draft"><label>表示する稿<select data-sc-navigate aria-label="表示する稿">' + choices + '</select></label>'
    body += f'<span>{E(draft["date"])}' + (f' · {len(draft["text"]):,}文字' if draft['text'] is not None else '') + '</span></div>'
    if draft is work['preferred'] and draft is not work['drafts'][-1] and draft['status'] == 'ok':
        body += '<p class="sc-hint">完成済みの稿を表示しています。ほかの稿の状態は上の選択欄で確認できます。</p>'
    body += '</header><div class="sc-tools"><div role="tablist" aria-label="作品の内容">'
    for key, label in (("body", "本文"), ("synopsis", "あらすじ"), ("info", "生成情報")):
        body += f'<button type="button" role="tab" id="sc-tab-{key}" aria-controls="sc-panel-{key}" aria-selected="{str(key == "body").lower()}" tabindex="{0 if key == "body" else -1}" data-sc-tab="{key}">{label}</button>'
    body += '</div><div class="sc-reading-tools"><button type="button" data-sc-size="-1" aria-label="文字を小さく">A−</button><button type="button" data-sc-size="1" aria-label="文字を大きく">A＋</button><button type="button" data-sc-focus aria-pressed="false">集中して読む</button></div></div>'
    body += '<div class="sc-reading" data-sc-reading><section role="tabpanel" id="sc-panel-body" aria-labelledby="sc-tab-body" data-sc-panel="body">'
    if draft['text'] is not None:
        body += '<div class="sc-prose">' + E(draft['text']) + '</div>'
    else:
        body += '<div class="sc-empty"><h3>' + E(view.LABELS.get(draft['status'], draft['status'])) + '</h3><p>この稿には読める本文がありません。</p>' + link(record, '生成記録を確認する →') + '</div>'
    summary = view.synopsis(OutputStore(job_store.configs.control), draft, cid)
    body += '</section><section role="tabpanel" id="sc-panel-synopsis" aria-labelledby="sc-tab-synopsis" data-sc-panel="synopsis" hidden><h3>この稿に使ったあらすじ</h3><div class="sc-prose">' + E(summary) + '</div></section>'
    body += '<section role="tabpanel" id="sc-panel-info" aria-labelledby="sc-tab-info" data-sc-panel="info" hidden><h3>生成情報</h3><dl class="sc-info">'
    for name, value in (("状態", view.LABELS.get(draft['status'], draft['status'])), ("作成日時", draft['date']), ("候補", cid), ("生成版", oid), ("試行", draft['entry'].get('attempt_id') or '未開始'), ("モデル", draft['request'].get('model') or '指定なし')):
        body += f'<dt>{E(name)}</dt><dd>{E(value)}</dd>'
    if usage_html:  # 段階4b: 拡張実験の候補にだけ出る
        body += f'<dt>世界の拡張</dt><dd>{usage_html}</dd>'
    body += '</dl><p>' + link(record, '生成記録・再生成・復旧を確認 →') + '</p><p>' + link(f'/runs/{U(rid)}/candidates?candidate={U(cid)}', 'Siftingでこの候補を確認 →') + '</p></section></div>'
    index = works.index(work)
    body += '<footer class="sc-reader-footer">'
    for offset, label in ((-1, '← 前の作品'), (1, '次の作品 →')):
        other = index + offset
        if 0 <= other < len(works):
            item = works[other]
            body += link(href(query, candidate=item['candidate_id'], work_run=item['run_id'], output=None), label)
        else: body += f'<span aria-disabled="true">{label}</span>'
        if offset == -1: body += '<span data-sc-progress aria-live="off">読書位置 0%</span>'
    return body + '</footer></section>'
