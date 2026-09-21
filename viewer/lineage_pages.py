"""Full-width primary-lineage workspace; extraction stays in gapengine.lineage."""
import math
from urllib.parse import urlencode
from gapengine.lineage import LineageError
from viewer import data, pages

E, U = pages._escape, pages._url_segment
TABS = (("choices", "選択の違い"), ("traits", "行動傾向の変化"), ("records", "記録・再現状況"))


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _value(value):
    return f"{value:.3f}".rstrip("0").rstrip(".") if _number(value) else "不明"


def _key(c):
    return (str(c.get("verb", "")), tuple(str(a) for a in c.get("args", [])))


def _probability(c):
    p = (c or {}).get("probability")
    return p if _number(p) and 0 <= p <= 1 else None


def _candidate_table(keys, candidates, stats):
    recorded = {_key(c): c for c in candidates}
    complete = (stats or {}).get("truncated") is False and type((stats or {}).get("total_candidates")) is int and (stats or {}).get("recorded_candidates") == (stats or {}).get("total_candidates")
    rows = []
    for key in keys:
        c = recorded.get(key)
        label = pages._lineage_action_text({"verb": key[0], "args": key[1]})
        p = _probability(c)
        text = (f"{p:.0%}" if p is not None else "不明") if c else ("候補なし" if complete else "未記録")
        bar = f'<span class="lw-probability"><i style="width:{p * 100:.4f}%"></i></span>' if p is not None else '<span></span>'
        rows.append(f'<tr><th scope="row">{E(label)}' + (' <span class="lw-check" aria-label="実際の選択">✓</span>' if c and c.get("selected") else '') + f'</th><td>{bar}</td><td>{text}</td></tr>')
    return '<table class="candidate-table"><tbody>' + "".join(rows) + '</tbody></table>'


def _choice_panels(turning):
    parent, child = turning.get("parent_candidates", []), turning.get("child_candidates", [])
    pstats, cstats = turning.get("parent_candidate_stats"), turning.get("child_candidate_stats")
    combined = {}
    for c in parent + child:
        key = _key(c)
        combined[key] = max(combined.get(key, 0), _probability(c) or 0)
    keys = sorted(combined, key=lambda key: (-combined[key], key))
    selected = {_key(c) for c in parent + child if c.get("selected")}
    brief = [key for key in keys if key in selected or key in keys[:3]]

    def columns(shown):
        blocks = []
        for label, candidates, stats in (("親の選択肢", parent, pstats), ("子の選択肢", child, cstats)):
            note = (f'記録 {stats.get("recorded_candidates", "不明")} / 全 {stats.get("total_candidates", "不明")}件 · 省略 {"あり" if stats.get("truncated") else "なし"}'
                    if stats and stats.get("total_candidates") is not None else "選択肢の記録範囲は不明")
            table = _candidate_table(shown, candidates, stats) if candidates else '<p class="lw-empty">候補の記録がありません。未記録は確率0を意味しません。</p>'
            blocks.append(f'<section><h3>{label}</h3><p class="lw-stats-note">{E(note)}</p>{table}</section>')
        return '<div class="turning-columns">' + "".join(blocks) + '</div>'

    body = '<div class="lw-actions">'
    for label, generation, action in (("親候補", turning["parent_generation"], turning.get("parent_action")), ("子候補", turning["child_generation"], turning.get("child_action"))):
        body += f'<section><span>{label} · 第{E(generation)}世代</span><h3>{E(pages._lineage_action_text(action))}</h3></section>'
    body += '</div>' + columns(brief)
    if len(brief) < len(keys):
        body += f'<details class="lw-more"><summary>選択肢の記録をすべて見る（{len(keys)}種類）</summary>{columns(keys)}</details>'
    return body + '<p class="lw-hint">記録された選択確率です。行動傾向の変化との因果関係は未確認です。</p>'


def _traits(node, turning):
    shifts = turning.get("gene_shift") if turning else node.get("gene_shift")
    if not shifts:
        return '<p class="lw-empty">比べる前の候補、または行動傾向の記録がありません。</p>'
    body = '<p class="lw-hint">親候補から変化した値です。物語の優劣を表す点数ではありません。</p><table class="lw-traits"><thead><tr><th>行動傾向</th><th>親候補</th><th>子候補</th><th>変化</th></tr></thead><tbody>'
    for shift in shifts:
        delta = shift.get("delta")
        body += f'<tr><th scope="row">{E(shift.get("label") or shift.get("key"))}</th><td>{_value(shift.get("before"))}</td><td>{_value(shift.get("after"))}</td><td>{f"{delta:+.3f}" if _number(delta) else "不明"}</td></tr>'
    body += '</tbody></table>'
    series = (turning or {}).get("trait_series")
    if series and series.get("values") and all(_number(v) for v in series["values"]):
        body += f'<section class="lw-trend"><h3>{E(series["label"])}の推移</h3>{pages.sparkline(series["values"])}<p class="lw-hint">主系の出発点から現在まで、同じ項目を追っています。横軸は主系の順序です。</p></section>'
    return body


def _records(model, node, turning, legacy):
    body = '<dl class="lw-records">'
    values = [("対象候補", node["ref"]), ("世代", node["generation"]), ("共通seed", model.get("seed"))]
    if turning:
        values += [("親候補", turning["parent_ref"]), ("子候補", turning["child_ref"]),
                   ("親側の時点", f'T{turning.get("parent_turn", "不明")}'), ("子側の時点", f'T{turning.get("turn", "不明")}')]
    values += [("入力の扱い", "現在の世界・ジャンル設定を参照する旧形式" if legacy else "実行に保存された世界・ジャンル設定"),
               ("再現結果", "再現できませんでした" if node.get("rerun_error") else "再現記録あり" if node.get("outcome") is not None else "記録不足で不明")]
    for label, value in values:
        body += f'<dt>{label}</dt><dd>{E(value if value is not None else "不明")}</dd>'
    body += '</dl><p class="lw-hint">主系は、二親がある場合に遺伝子が近い親をたどった列です。すべての分岐を含む家系図ではありません。</p>'
    body += '<p class="lw-hint">親子を共通seedで再現した記録を比較します。保存済みの再現記録があれば利用します。実行当時と完全に一致することを保証する表示ではありません。</p>'
    body += '<p class="lw-hint">初到達は、この主系を共通seedで再現した範囲で初めて結末に到達した地点です。</p>'
    if node.get("rerun_error"):
        body += '<details open><summary>再現できなかった理由</summary><pre>' + E(node["rerun_error"]) + '</pre></details>'
    missing = [n for n in model["ancestry"] if n.get("rerun_error")]
    if missing:
        body += '<details><summary>再現できなかった祖先</summary><ul>' + "".join(f'<li>{E(n["ref"])}：{E(n["rerun_error"])}</li>' for n in missing) + '</ul></details>'
    return body


def render_model(model, experiment_name, cell_key, *, turning_index=None, point=None, tab="choices", view="key", expected_ref=None, job_store=None, phases=None, legacy=True, error=None):
    base = f'/exp/{U(experiment_name)}/cell/{U(cell_key)}'
    title = "この候補は、どう生まれたか"
    tab = tab if tab in dict(TABS) else "choices"
    view = view if view in ("key", "all", "fit") else "key"
    ancestry = (model or {}).get("ancestry", [])
    turns = (model or {}).get("turnings", [])
    if expected_ref and model and expected_ref != model.get("elite_ref"):
        error, ancestry = "代表候補が更新されています。候補画面に戻り、対象を確認してください。", []
    header = '<header class="lw-heading"><div><p>Sifting / 候補の詳細 / 系譜・転機</p><h1>' + title + '</h1><p>' + E(cell_key) + (f' · 第{E(ancestry[-1]["generation"])}世代の候補' if ancestry else '') + '</p></div><a href="' + base + '">← 候補に戻る</a></header>'
    body = '<div class="rw-shell lw-shell" data-lineage>' + header
    if not ancestry:
        body += '<div class="lw-empty lw-unavailable"><h2>系譜を表示できません</h2><p role="status">' + E(error or "比べられる祖先の記録がありません。") + '</p><p>候補のあらすじや原記録は候補画面で確認できます。</p></div>'
        footer_text = "系譜の記録を確認してください"
    else:
        default = f'turning-{turning_index if type(turning_index) is int and 0 <= turning_index < len(turns) else 0}' if turns else "current"
        points = {f"node-{i}": (i, None) for i in range(len(ancestry))}
        points.update({f"turning-{i}": (t["child_index"], i) for i, t in enumerate(turns)})
        points.update({"origin": (0, None), "current": (len(ancestry)-1, None)})
        if model.get("first_reach_index") is not None:
            points["reach"] = (model["first_reach_index"], None)
        point = point if point in points else default
        # Timeline and old node links resolve to the same named milestone.
        if point.startswith("node-"):
            for name in ("origin", "current", "reach"):
                if name in points and points[name] == points[point]:
                    point = name
                    break
        node_index, selected_turn = points[point]
        node = ancestry[node_index]
        turning = turns[selected_turn] if selected_turn is not None else None
        reach = model.get("first_reach_index")
        events = [(0, "origin", "出発点", "")]
        events += [(t["child_index"], f"turning-{i}", f"転機 {i+1}", pages._lineage_action_text(t.get("parent_action")) + " → " + pages._lineage_action_text(t.get("child_action"))) for i, t in enumerate(turns)]
        if reach is not None:
            events.append((reach, "reach", "初到達", "共通seedで確認"))
        events.append((len(ancestry)-1, "current", "現在の候補", ""))
        events.sort(key=lambda e: e[0])
        key_nodes = {e[0] for e in events} | {node_index}
        fit_nodes = {0, len(ancestry)-1, node_index}
        if reach is not None: fit_nodes.add(reach)
        fit_nodes |= {round(i * (len(ancestry)-1) / 5) for i in range(6)}
        def href(p):
            q = {"point":p, "tab":tab, "view":view, "elite":model["elite_ref"]}
            if p.startswith("turning-"): q["turning"] = p.split("-")[1]
            return base + "/lineage?" + E(urlencode(q))
        def visible(i):
            return view == "all" or i in (fit_nodes if view == "fit" else key_nodes)
        body += '<section class="lw-overview"><header><div><h2>この候補につながる系譜</h2><small>近い親をたどる主系</small></div><div class="lw-view-controls">'
        for key, label in (("key","主な地点"),("all","すべての世代"),("fit","全体表示")):
            body += f'<button type="button" data-lw-view="{key}" aria-pressed="{str(view==key).lower()}">{label}</button>'
        body += '</div></header><div class="lw-track-scroll" tabindex="0" aria-label="系譜の世代"><ol class="lineage-band" data-view="' + view + '">'
        turn_by_node = {t["child_index"]: i for i,t in enumerate(turns)}
        for i, n in enumerate(ancestry):
            target = f"turning-{turn_by_node[i]}" if i in turn_by_node else f"node-{i}"
            labels = []
            if i == 0: labels.append("出発点")
            if i in turn_by_node: labels.append(f"転機 {turn_by_node[i]+1}")
            if i == reach: labels.append("初到達")
            if i == len(ancestry)-1: labels.append("現在の候補")
            if n.get("rerun_error"): labels.append("再現不可")
            body += f'<li class="lineage-node" data-lw-node="{i}" data-key="{int(i in key_nodes)}" data-fit="{int(i in fit_nodes)}"' + ('' if visible(i) else ' hidden') + '>'
            body += f'<a data-lw-point href="{href(target)}"' + (' aria-current="step"' if i == node_index else '') + f'><span class="lw-dot{" is-broken" if n.get("rerun_error") else ""}"></span><strong>第{E(n["generation"])}世代</strong><span>{E(" / ".join(labels))}</span></a></li>'
        body += '</ol></div><p class="lw-track-note" data-lw-track-note>地点を選ぶと、下の詳細が切り替わります。</p></section>'
        broken = [n for n in ancestry if n.get("rerun_error")]
        if broken:
            body += f'<p class="lw-warning">一部の祖先は再現できませんでした（{len(broken)}件）。この区間の転機は不明です。初到達も確認できた範囲での表示です。</p>'
        if not turns:
            body += '<p class="lw-warning">転機が見つかりませんでした。' + ("再現できなかった区間を含みます。" if broken else "決定が一致したか、比べられる祖先がありません。") + '</p>'
        body += '<div class="lw-workspace"><nav class="lw-events" aria-label="系譜の地点"><header><h2>主な地点</h2><span>' + str(len(events)) + '件</span></header><div class="lw-event-list">'
        for i, target, label, note in events:
            n = ancestry[i]
            body += f'<a data-lw-point href="{href(target)}"' + (' aria-current="page"' if point==target else '') + f'><strong>{E(label)}</strong><small>第{E(n["generation"])}世代</small><span>{E(note)}</span></a>'
        body += '</div></nav><section class="lw-detail" aria-label="選んだ地点の詳細"><header class="lw-detail-head">'
        if turning:
            subtitle = f'第{turning["parent_generation"]}世代の親候補 → 第{turning["child_generation"]}世代の子候補'
            headline = f'転機 {selected_turn+1}　' + pages._lineage_action_text(turning.get("parent_action")) + " → " + pages._lineage_action_text(turning.get("child_action"))
        else:
            subtitle = f'第{node["generation"]}世代 · {node["ref"]}'
            headline = next((e[2] for e in events if e[1] == point), f'第{node["generation"]}世代')
        body += f'<div><h2>{E(headline)}</h2><p>{E(subtitle)}</p></div><div class="lw-step-controls">'
        for offset, label, symbol in ((-1,"前の地点","‹"),(1,"次の地点","›")):
            other = node_index + offset
            body += (f'<a data-lw-point aria-label="{label}" href="{href(f"turning-{turn_by_node[other]}" if other in turn_by_node else f"node-{other}")}">{symbol}</a>' if 0 <= other < len(ancestry) else f'<button disabled aria-label="{label}">{symbol}</button>')
        body += '</div></header><div class="lw-tabs" role="tablist" aria-label="系譜の詳細">'
        for key,label in TABS:
            body += f'<button type="button" id="lw-tab-{key}" role="tab" aria-controls="lw-panel-{key}" aria-selected="{str(key==tab).lower()}" tabindex="{0 if key==tab else -1}" data-lw-tab="{key}">{label}</button>'
        body += '</div><div class="lw-detail-scroll">'
        choices = ""
        if turning:
            context = f'子側 T{turning.get("turn", "不明")} / 親側 T{turning.get("parent_turn", "不明")}'
            if turning.get("present"): context += " · 同席 " + "・".join(map(str, turning["present"]))
            choices = '<div class="lw-context"><span>親子の行動が最初に分かれた地点です。</span><span>' + E(context) + '</span></div>' + _choice_panels(turning)
        else:
            choices = '<div class="lw-node-summary"><h3>この地点の結果</h3><p>' + E(pages._lineage_outcome_text(node.get("outcome"))) + '</p>'
            choices += '<p class="lw-hint">' + ('再現できないため、この地点を含む転機は判定できません。' if node.get("rerun_error") else '左右の地点をたどるか、左の転機を選ぶと親子の行動を比較できます。') + '</p></div>'
        for key, content in (("choices", choices), ("traits", _traits(node, turning)), ("records", _records(model, node, turning, legacy))):
            body += f'<section id="lw-panel-{key}" role="tabpanel" aria-labelledby="lw-tab-{key}" data-lw-panel="{key}"' + ('' if key==tab else ' hidden') + '>' + content + '</section>'
        body += '</div></section></div>'
        footer_text = f'第{node["generation"]}世代の' + ("転機" if turning else "地点") + "を表示中"
    body += '<footer class="lw-footer"><div><strong>' + E(footer_text) + '</strong><span>採用・メモは候補画面で行えます</span></div><a class="lw-primary" href="' + base + '">この候補に戻る →</a></footer><noscript>地点のリンクはそのまま利用できます。表示切り替えにはJavaScriptが必要です。</noscript></div>'
    doc = pages.document(title, body, phase="sifting", run=experiment_name, output_run=(phases or {}).get("catalog_run_id"), phases=phases, job_store=job_store, page_class="run-observer")
    return doc.replace("</head>", '<link rel="stylesheet" href="/static/run-workspace.css"><link rel="stylesheet" href="/static/lineage-workspace.css"><script src="/static/lineage-workspace.js" defer></script></head>')


def render(repository, experiment_name, cell_key, *, job_store=None, **options):
    experiment = repository.experiment(experiment_name)
    error, model = None, None
    try:
        model = data.lineage_view(repository, experiment, cell_key)
    except LineageError as exc:
        error = "主系の記録を確認できません：" + str(exc)
    phases = data.phase_status(repository, experiment_name, job_store=job_store)
    return render_model(model, experiment_name, cell_key, job_store=job_store, phases=phases,
                        legacy=not repository.safe_path(experiment, "manifest.json").is_file(), error=error, **options)
