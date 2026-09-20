"""Readable execution conditions and history. Existing start APIs stay unchanged."""
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

from viewer import data, pages

E, U = pages._escape, pages._url_segment


def _date(value):
    if not value:
        return "日時未記録"
    try:
        stamp = datetime.fromtimestamp(value).astimezone() if isinstance(value, (int, float)) else datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
        return stamp.strftime("%Y/%m/%d %H:%M")
    except (ValueError, TypeError, OSError):
        return str(value)


def _document(handler, title, active, body, *, world=None, config=None, footer=""):
    from viewer import workbench_pages as wb
    store = wb._job_store(handler)
    world_id = (world or {}).get("id")
    conditions = f'/jobs?world={U(world_id)}' if world_id else '/jobs'
    if config:
        conditions += f'&config={U(config["config_id"])}'
    jobs = store.list() if store else []
    current = next((j for j in jobs if j.get("state") in wb.RUNNING_STATES and j.get("kind", "evolve") == "evolve"), None)
    if current is None:
        current = next((j for j in sorted(jobs, key=lambda j: str(j.get("created_at") or ""), reverse=True)
                        if j.get("kind", "evolve") == "evolve" and (not config or j.get("config_id") == config["config_id"])), None)
    status_href = f'/jobs/{U(current["job_id"])}' if current else conditions
    nav = ''.join(f'<a href="{E(href)}"' + (' aria-current="page"' if key == active else '') + f'>{label}</a>'
                  for key, label, href in (("conditions", "実行条件", conditions), ("status", "実行状況", status_href), ("history", "実行履歴", "/history")))
    content = ('<div class="rw-shell rb-shell"><nav class="rw-sidebar" aria-label="実行メニュー">' + nav + '</nav>'
               '<div class="rb-main"><header class="rw-heading"><h1>' + E(title) + '</h1>'
               '<p>' + ("条件を確認して、物語の探索を始めます。" if active == "conditions" else "実行した探索を振り返り、続きの作業へ進めます。") + '</p></header>'
               '<div class="rb-content">' + body + '</div>'
               + ('<footer class="rw-footer rb-footer">' + footer + '</footer>' if footer else '') + '</div></div>')
    doc = pages.document(title, content, phase="run", world=world, job_store=store,
                         pin=data.pinned_target(store), page_class="run-observer")
    handler._send_html(doc.replace('</head>', '<link rel="stylesheet" href="/static/run-workspace.css"></head>'))


def conditions(handler, view=None, *, config=None):
    from viewer import workbench_pages as wb
    preparing = view is not None
    config = view.get("config") if preparing else config
    world = view["world"] if preparing else wb._config_world(config)
    if not config:
        body = '<div class="rb-empty"><h2>この世界の実行設定がまだありません。</h2><p>探索する規模と保存する結果を決めましょう。</p>'
        body += f'<a class="rw-primary" href="{E(wb._new_config_href(world))}">新しく作る →</a></div>'
        return _document(handler, "実行条件", "conditions", body, world=world)
    ev, preview = config["evolution"], config["preview"]
    cid = U(config["config_id"])
    labels = {v["id"]: v.get("label", v["id"]) for v in preview.get("world", {}).get("ending", [])}
    endings = "、".join(labels.get(v, v) for v in preview.get("target_endings", [])) or "世界の既定"
    keep = {"all": "すべての結果", "reached": "結末に到達した結果", "none": "ログを保存しない"}.get(ev["keep"], ev["keep"])
    picker = wb._run_config_picker(world, view["configs"], config) if preparing else f'<h2>{E(config["label"])}</h2>'
    stats = ''.join(f'<div><span>{label}</span><strong>{E(ev[key])}<small>{unit}</small></strong></div>' for label, key, unit in
                    (("世代を重ねる回数", "generations", "世代"), ("各世代の物語", "population", "個体"), ("各個体の評価", "seeds", "回")))
    body = ('<div class="rb-config-picker">' + picker + f'<a href="/configs/new?from={cid}">複製して調整 ↗</a>'
            f'<a href="{E(wb._new_config_href(world))}">＋ 新しい条件</a></div>'
            '<div class="rb-condition-grid"><div><section class="rb-section"><span class="rb-eyebrow">01 · 世界と結末</span>'
            f'<h2>{E(preview["world_name"])}</h2><p class="rb-ending">{E(endings)}</p><dl class="rb-facts">'
            f'<dt>主人公</dt><dd>{E(preview["protagonist"])}</dd><dt>敵役</dt><dd>{E(preview["antagonist"])}</dd>'
            f'<dt>物語の長さの上限</dt><dd>{E(preview["max_turns"])} ターン</dd></dl></section>'
            '<section class="rb-section"><span class="rb-eyebrow">02 · 探索の規模</span><h2>どのくらい探索するか</h2>'
            '<div class="rw-stats rb-scale">' + stats + '</div><p class="muted">同じ個体を異なる乱数条件で評価します。</p></section>'
            '<section class="rb-section"><span class="rb-eyebrow">03 · 保存と制限</span><h2>残す結果と実行上限</h2>'
            f'<dl class="rb-facts"><dt>結果の保存</dt><dd>{E(keep)}</dd><dt>実行時間の上限</dt><dd>{E(config["execution_limits"]["wall_seconds"])} 秒</dd>'
            f'<dt>説明の記録</dt><dd>{"残す" if ev["record_explanations"] else "残さない"}</dd>'
            f'<dt>共進化</dt><dd>{"あり" if ev["coevolve"] else "なし"}</dd><dt>メタ進化</dt><dd>{"あり" if ev["meta_evolution"] else "なし"}</dd></dl></section>'
            '</div><aside class="rb-summary"><h2>今回の探索</h2><dl>'
            f'<dt>評価する個体</dt><dd>{E(preview["planned_individual_evaluations"])} 個体</dd>'
            f'<dt>シミュレーションの評価回数</dt><dd>{E(preview["planned_seed_evaluations"])} 回</dd>'
            f'<dt>所要時間の目安</dt><dd>{view["estimate"] if preparing else "実行前の確認画面で表示"}</dd></dl>'
            '<p>GA は LLM を呼び出しません。文章の生成は、候補を選んだあとの工程です。</p>'
            f'<p class="muted">設定作成：{E(_date(config["created_at"]))}</p></aside></div>'
            '<details class="rb-advanced"><summary>詳細な設定・固定値・来歴を確認</summary>'
            + wb.render_config_detail(config).rsplit('<p class="actions">', 1)[0] + '</details>')
    back = f'<a href="/worlds/{U(world["id"])}">世界設定へ戻る</a>'
    if preparing:
        action = wb._blocking_notice(view["blocking_job"]) if view.get("blocking_job") else wb._start_cta(config, view["request_id"])
    else:
        action = f'<a class="rw-primary" href="/configs/{cid}/start">この条件で実行へ →</a>'
    _document(handler, "実行条件", "conditions", body, world=world, config=config, footer=back + action)


def history(handler, records, generation_jobs):
    from viewer import workbench_pages as wb, output_pages
    store = wb._job_store(handler)
    configs = {c["config_id"]: c for c in store.configs.list()} if store else {}
    jobs = {j["job_id"]: j for j in store.list()} if store else {}
    query = parse_qs(urlsplit(handler.path).query)
    term = query.get("q", [""])[0].strip()
    state = query.get("state", [""])[0]
    world = query.get("world", [""])[0]
    worlds = {c["project_id"]: c["preview"]["world_name"] for c in configs.values()}
    filtered = []
    for r in records:
        c = configs.get(r.get("config_id"), {})
        words = ' '.join(str(v) for v in (r["experiment_name"], r["run_id"], c.get("label", ""), c.get("preview", {}).get("world_name", "")))
        if term.casefold() not in words.casefold() or (state and r["state"] != state) or (world and c.get("project_id") != world):
            continue
        filtered.append(r)
    def options(items, selected):
        return ''.join(f'<option value="{E(k)}"' + (' selected' if k == selected else '') + f'>{E(v)}</option>' for k, v in items)
    form = ('<form class="rb-history-filter" method="get" action="/history"><label>実行を探す'
            f'<input type="search" name="q" value="{E(term)}" placeholder="実行名・世界名で検索"></label>'
            '<label>世界<select name="world"><option value="">すべての世界</option>' + options(sorted(worlds.items()), world) + '</select></label>'
            '<label>状態<select name="state"><option value="">すべての状態</option>' + options(wb.STATE_LABELS.items(), state) + '</select></label>'
            '<button type="submit">絞り込む</button><a href="/history">クリア</a></form>')
    def newest(record):
        j = jobs.get(record.get("job_id"), {})
        value = j.get("started_at") or j.get("created_at")
        try:
            return float(value) if isinstance(value, (int, float)) else datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except (AttributeError, ValueError, TypeError, OSError):
            return 0
    filtered.sort(key=newest, reverse=True)
    running = [r for r in filtered if r["state"] in wb.RUNNING_STATES]
    completed = [r for r in filtered if r["state"] not in wb.RUNNING_STATES]
    def cards(rows, empty):
        if not rows:
            return f'<p class="rb-empty">{empty}</p>'
        content = []
        for r in rows:
            c = configs.get(r.get("config_id"), {})
            j = jobs.get(r.get("job_id"), {})
            ev = c.get("evolution", {})
            name = c.get("label") or r["experiment_name"]
            world_name = c.get("preview", {}).get("world_name", "世界の記録なし")
            href = f'/jobs/{U(r["job_id"])}' if r.get("job_id") else f'/exp/{U(r["experiment_name"])}/monitor'
            scale = f'{ev["generations"]}世代 × {ev["population"]}個体 × 各{ev["seeds"]}回' if ev else '探索規模の記録なし'
            at = j.get("started_at") or j.get("created_at") or r.get("created_at")
            stamp = _date(at)
            revision = r.get("publication_revision")
            saved = f'{revision}世代を保存' if revision is not None else '保存世代の記録なし'
            result = f'<a href="/runs/{U(r["run_id"])}/candidates">候補を見る →</a>' if r["state"] == "legacy" or revision is not None else ''
            config_link = f'<a href="/configs/{U(c["config_id"])}">実行時の条件</a>' if c else ''
            content.append('<article class="rb-history-row"><div class="rb-history-identity">'
                           f'<span class="rb-eyebrow">{E(world_name)}</span><h3><a href="{E(href)}">{E(name)}</a></h3>'
                           f'<p class="muted">{E(scale)}</p><details><summary>実行ID</summary><code>{E(r["run_id"])}</code></details></div>'
                           f'<div class="rb-history-state">{wb.state_badge(r["state"])}<p>{E(saved)}</p></div>'
                           f'<div class="rb-history-time"><time>{E(stamp)}</time><p class="muted">開始日時</p></div>'
                           f'<div class="rb-history-actions"><a class="rb-detail-link" href="{E(href)}">詳細を見る →</a>{result}{config_link}</div></article>')
        return ''.join(content)
    body = (form + f'<p class="rb-result-count">{len(filtered)} 件の探索</p>'
            '<section class="rb-history-section"><h2>進行中</h2>' + cards(running, "進行中の実行はありません") + '</section>'
            '<section class="rb-history-section"><h2>履歴</h2>' + cards(completed, "条件に一致する実行履歴はありません") + '</section>'
            '<div class="rb-output-history">' + output_pages.render_generation_jobs_section(generation_jobs) + '</div>')
    if not records:
        body += '<p><a class="rw-primary" href="/jobs">実行条件を確認 →</a></p>'
    _document(handler, "実行履歴", "history", body)
