"""Full-width, read-only source record inspector."""
import json
from urllib.parse import urlencode
from viewer import data, pages, raw_view as model

E, U = pages._escape, pages._url_segment


def value_text(value):
    if value is None:
        return "null"
    if isinstance(value, (dict, list, bool)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def fields(items):
    return '<dl class="rv-fields">' + "".join(
        f'<dt>{E(label)}</dt><dd>{E(value_text(value))}</dd>' for label, value in items
    ) + '</dl>'


def all_fields(value):
    if not isinstance(value, dict):
        return '<pre class="rv-value">' + E(value_text(value)) + '</pre>'
    items = []
    for key, item in value.items():
        if isinstance(item, (dict, list)):
            contents = '<details><summary>' + E(key) + f'（{len(item)}項目）</summary><pre class="rv-value">' + E(value_text(item)) + '</pre></details>'
        else:
            contents = fields([(key, item)])
        items.append(contents)
    return '<div class="rv-all-fields">' + "".join(items) + '</div>'


def changes(row):
    delta = model.mapping(row.get("delta"))
    rows = []
    labels = {"affinity": "親しさ", "awareness": "認識", "trust": "信頼"}
    for relation in model.sequence(delta.get("relations")):
        relation = model.mapping(relation)
        target = f'{model.scalar(relation.get("observer")) or "不明"} → {model.scalar(relation.get("target")) or "不明"}'
        for key, value in relation.items():
            if key in ("observer", "target") or not model.number(value):
                continue
            rows.append(f'<tr><th scope="row">{E(target)}</th><td>{E(labels.get(key,key))}</td><td>{value:+.4g}</td></tr>')
    body = '<h3>記録された変化</h3>'
    if rows:
        body += '<table class="rv-changes"><thead><tr><th>対象</th><th>項目</th><th>変化</th></tr></thead><tbody>' + "".join(rows) + '</tbody></table>'
    elif delta:
        body += '<p class="rv-note">この記録には、項目別の変化が保存されています。</p>'
    else:
        body += '<p class="rv-note">この行には変化の記録がありません。</p>'
    if delta:
        body += '<details><summary>変化の記録をすべて見る</summary>' + all_fields(delta) + '</details>'
    return body


def selection(row):
    explanation = model.mapping(row.get("explanation"))
    saved = model.mapping(explanation.get("selection"))
    candidates = model.sequence(saved.get("candidates"))
    body = '<details class="rv-selection"><summary>選択肢と選ばれ方</summary>'
    probability = row.get("choice_prob")
    if model.number(probability) and 0 <= probability <= 1:
        body += f'<p>実際の選択の確率：{probability:.2%}</p>'
    if not candidates:
        body += '<p class="rv-note">選択肢の記録はありません。確率0を意味しません。</p>'
    else:
        recorded = saved.get("recorded_candidates")
        total = saved.get("total_candidates")
        body += f'<p class="rv-note">記録 {E(recorded if recorded is not None else "不明")} / 全 {E(total if total is not None else "不明")}件</p>'
        body += '<table><thead><tr><th>行動</th><th>確率</th><th>選択</th></tr></thead><tbody>'
        for candidate in candidates:
            candidate = model.mapping(candidate)
            probability = candidate.get("probability")
            shown = f"{probability:.2%}" if model.number(probability) and 0 <= probability <= 1 else "不明"
            name = model.action_label(candidate)
            args = model.sequence(candidate.get("args"))
            if args:
                name += "（" + "・".join(value_text(a) for a in args) + "）"
            body += f'<tr><th scope="row">{E(name)}</th><td>{shown}</td><td>' + ("✓" if candidate.get("selected") is True else "—") + '</td></tr>'
        body += '</tbody></table>'
    return body + '</details>'


def readable(record):
    if record.error:
        return '<div class="rv-empty"><h3>原文を確認してください</h3><p>' + E(record.error) + '</p><p>「生ログ（JSON）」からこの行をそのまま読めます。</p></div>'
    row = record.row
    intro = '<p class="rv-note">保存された記録を、項目ごとに整理して表示しています。</p>'
    if row.get("verb"):
        items = [("行動", model.action_label(row) + " / " + model.scalar(row.get("verb"))),
                 ("行動した人物", row.get("subject", "未記録"))]
        detail = model.mapping(row.get("details"))
        if "target" in detail:
            items.append(("相手", detail["target"]))
        if "item" in detail:
            items.append(("品物", detail["item"]))
        args = model.sequence(row.get("args"))
        # Avoid repeating the same recipient/item in the reading view.
        # Every original field, including result and args, stays in the full record.
        if args and not (row.get("verb") == "give_item" and args == [detail.get("target"), detail.get("item")]):
            items.append(("対象・引数", " / ".join(value_text(v) for v in args)))
        body = intro + '<h3>行動の内容</h3>' + fields(items) + changes(row) + selection(row)
    else:
        preferred = ("world", "seed", "protagonist", "antagonist", "day", "slot", "turn", "ending", "reached")
        names = {"world":"世界","seed":"seed","protagonist":"主人公","antagonist":"対立する人物",
                 "day":"日","slot":"時間帯","turn":"時点","ending":"結末","reached":"到達（記録値）"}
        items = [(names[k], row[k]) for k in preferred if k in row]
        body = intro + '<h3>' + E(record.kind_label) + '</h3>'
        body += fields(items) if items else '<p class="rv-note">保存された項目は下から確認できます。</p>'
    return body + '<details class="rv-everything"><summary>記録のすべての項目</summary>' + all_fields(record.value) + '</details>'


def render_source(source, *, line=None, q="", kind="", person="", page=None,
                  mode="readable", expected_source=None, phases=None, job_store=None,
                  back_href=None, raw_href=None, output_run=None):
    view = model.select(source, line=line, q=q, kind=kind, person=person,
                        page=page, expected_source=expected_source)
    base = f'/exp/{U(source["experiment"])}/cell/{U(source["cell"])}'
    raw_url = raw_href or base + "/raw"
    base = back_href or base
    mode = mode if mode in ("readable", "json") else "readable"
    chosen = view.get("selected")
    def href(**overrides):
        params = {"q":q, "kind":kind, "person":person, "mode":mode, "source":source["sha256"]}
        if chosen:
            params["line"] = chosen.line
        params.update(overrides)
        return raw_url + "?" + E(urlencode({k:v for k,v in params.items() if v is not None and v != ""}))
    def hidden(name, value):
        return f'<input type="hidden" name="{name}" value="{E(value)}">'
    def options(values, active, label):
        # Keep an unknown filter explicit rather than silently claiming "all".
        if active and active not in values:
            values = {**values, active: active}
        return '<option value="">' + label + '</option>' + "".join(
            f'<option value="{E(key)}"' + (' selected' if key == active else '') + f'>{E(text)}</option>'
            for key, text in sorted(values.items()))
    meta = " · ".join(x for x in (source.get("world"), source["cell"],
           f'第{source["generation"]}世代' if source.get("generation") is not None else "",
           f'seed {source["seed"]}' if source.get("seed") is not None else "") if x)
    body = f'<div class="rw-shell rv-shell" data-record-viewer data-line="{chosen.line if chosen else ""}" data-source="{source["sha256"]}"><header class="rv-heading"><div><p>Sifting / 候補の詳細 / 原記録</p><h1>原記録を確かめる</h1><p>' + E(meta) + f'</p></div><a href="{base}">← 候補に戻る</a></header>'
    if view["changed"]:
        body += '<div class="rv-empty rv-unavailable"><h2>原記録が更新されています</h2><p>リンク先の記録と現在のファイルが一致しません。候補に戻って対象を確認してください。</p></div>'
    else:
        records = source["records"]
        kinds = {r.kind:r.kind_label for r in records}
        people = {p:p for r in records for p in model.people(r.value)}
        body += f'<div class="rv-toolbar"><form action="{raw_url}" method="get" class="rv-filters" role="search">'
        body += hidden("source",source["sha256"]) + hidden("mode",mode)
        body += f'<input type="search" name="q" value="{E(q)}" aria-label="記録を検索" placeholder="記録を検索…">'
        body += '<select name="kind" aria-label="記録の種類">' + options(kinds,kind,"すべての種類") + '</select>'
        body += '<select name="person" aria-label="関与した人物">' + options(people,person,"すべての人物") + '</select>'
        body += '<button type="submit">絞り込む</button>'
        if q or kind or person:
            body += f'<a data-rv-link href="{href(q=None,kind=None,person=None,line=None)}">解除</a>'
        body += f'</form><form action="{raw_url}" method="get" class="rv-jump">'
        body += hidden("source",source["sha256"]) + hidden("mode",mode)
        body += f'<label for="rv-jump">行番号へ</label><input id="rv-jump" type="number" name="line" min="1" max="{len(records)}" required value="{chosen.line if chosen else ""}" aria-label="移動する行番号"><button type="submit"' + ('' if records else ' disabled') + '>移動</button></form></div>'
        body += '<div class="rv-workspace"><aside class="rv-list"><header><h2>記録一覧</h2><span>' + (f'{view["matches"]} / ' if q or kind or person else '') + f'{len(records)}行</span></header><nav class="rv-records" aria-label="原記録の一覧">'
        group = None
        for entry in view["entries"]:
            if entry.group != group:
                group = entry.group
                body += '<h3 class="rv-group">' + E(group) + '</h3>'
            body += f'<a data-rv-link id="L{entry.line}" href="{href(line=entry.line)}#L{entry.line}"' + (' aria-current="true"' if chosen and entry.line == chosen.line else '') + f'><span class="rv-line">L{entry.line}</span><span class="rv-list-text"><strong>{E(entry.title)}</strong><small>{E(entry.kind_label)}</small></span></a>'
        if not view["entries"]:
            body += '<p class="rv-empty">一致する記録がありません。</p>' if records else '<p class="rv-empty">原記録は空です。</p>'
        body += '</nav><footer class="rv-pager">'
        if view["page"] > 1:
            body += f'<a data-rv-link href="{href(page=view["page"]-1)}" aria-label="前の一覧ページ">‹</a>'
        body += f'<span>{view["first"]}–{view["last"]}件 · {view["page"]}/{view["pages"]}ページ</span>'
        if view["page"] < view["pages"]:
            body += f'<a data-rv-link href="{href(page=view["page"]+1)}" aria-label="次の一覧ページ">›</a>'
        body += '</footer></aside><section class="rv-detail" aria-label="選んだ記録">'
        if chosen:
            turn = model.scalar(chosen.row.get("turn"))
            place = f'L{chosen.line} · {chosen.group}' + (f' · T{turn}' if turn else '')
            body += '<header class="rv-detail-head"><div class="rv-location"><span>' + E(place) + '</span><nav aria-label="前後の記録">'
            for key,label,symbol in (("previous","前の記録","‹"),("next","次の記録","›")):
                other = view[key]
                body += f'<a data-rv-link href="{href(line=other)}#L{other}" aria-label="{label}">{symbol}</a>' if other else f'<button disabled aria-label="{label}">{symbol}</button>'
            body += '</nav></div><div class="rv-title"><div><h2>' + E(chosen.title) + '</h2>'
            if chosen.row.get("effective") is True:
                body += '<span class="rv-badge">有効な行動</span>'
            elif chosen.row.get("effective") is False:
                body += '<span class="rv-badge">効果なし</span>'
            body += '</div><div class="rv-copy"><button type="button" data-rv-copy="record">この記録をコピー</button><button type="button" data-rv-copy="link">リンクをコピー</button></div></div></header>'
            if view["outside"]:
                body += '<p class="rv-warning">選んだ行は絞り込みの対象外です。<a data-rv-link href="' + href(q=None,kind=None,person=None) + '">この行を一覧に表示</a></p>'
            body += '<div class="rv-tabs" role="tablist" aria-label="記録の表示形式">'
            for key,label in (("readable","読みやすく表示"),("json","生ログ（JSON）")):
                body += f'<a role="tab" id="rv-tab-{key}" data-rv-tab="{key}" href="{href(mode=key)}" aria-controls="rv-panel-{key}" aria-selected="{str(mode==key).lower()}" tabindex="{0 if mode==key else -1}">{label}</a>'
            body += '</div><div class="rv-detail-scroll">'
            body += '<section id="rv-panel-readable" role="tabpanel" aria-labelledby="rv-tab-readable"' + ('' if mode=="readable" else ' hidden') + '>' + readable(chosen) + '</section>'
            body += '<section id="rv-panel-json" role="tabpanel" aria-labelledby="rv-tab-json"' + ('' if mode=="json" else ' hidden') + '><div class="rv-json-head"><span>原文 L' + str(chosen.line) + '</span><label><input type="checkbox" data-rv-wrap checked>長い行を折り返す</label></div><p class="rv-note">選んだ1行の原文です。表示上の折り返しはコピーする内容を変えません。</p><pre class="rv-json is-wrapped"><code data-rv-original>' + E(chosen.raw) + '</code></pre></section></div>'
        else:
            body += '<div class="rv-empty"><h2>記録を選んでください</h2><p>検索条件を変えるか、一覧から確認したい行を選んでください。</p></div>'
        if chosen:
            # A JSON string preserves control characters that HTML normalizes.
            original_text = json.dumps(chosen.raw, ensure_ascii=True).replace("<", "\\u003c")
            body += '<script type="application/json" data-rv-copy-source>' + original_text + '</script>'
        body += '</section></div>'
    info = f'<details class="rv-file"><summary>ファイル情報</summary><div>{fields([("ファイル",source["relative"]),("サイズ",str(source["size"])+" bytes"),("SHA-256",source["sha256"])])}<p class="rv-note">ハッシュは読み取ったファイル全体のバイト列に対応します。</p></div></details>'
    body += '<footer class="rv-footer"><div><span>' + (f'原記録 L{chosen.line} / {len(source["records"])}行' if chosen else "原記録") + '</span>' + info + f'</div><a class="rv-primary" href="{base}">候補に戻る →</a></footer><p class="rv-status" data-rv-status role="status" aria-live="polite"></p></div>'
    html = pages.document("原記録を確かめる", body, run=source["experiment"], phase="sifting",
                          phases=phases, output_run=output_run, job_store=job_store, page_class="run-observer")
    return html.replace("</head>", '<link rel="stylesheet" href="/static/run-workspace.css"><link rel="stylesheet" href="/static/raw-workspace.css"><script src="/static/raw-workspace.js" defer></script></head>')


def render(repository, experiment_name, cell_key, line=None, *, job_store=None, **options):
    source = model.read_source(repository, experiment_name, cell_key)
    phases = data.phase_status(repository, experiment_name, job_store=job_store)
    return render_source(source, line=line, phases=phases, job_store=job_store, **options)
