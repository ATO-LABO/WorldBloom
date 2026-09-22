"""Candidate comparison bound to a verified publication and candidate IDs."""
import json
from hashlib import sha256
from viewer import data, pages, explanation_ui as ex, reader_ui, sifting_view, workbench_pages as wb, generation_pages

E, U = pages._escape, pages._url_segment
VERDICTS = (("unclassified", "未分類"), ("adopted", "採用"), ("held", "保留"), ("rejected", "除外"))
TABS = (("story", "あらすじ"), ("evidence", "選択・根拠・代償・転機"), ("data", "実験データ"))


def _link(href, label):
    return f'<a href="{E(href)}">{E(label)}</a>'


def _evidence(explanation, raw_href):
    item = (explanation or {}).get("representative")
    cells = []
    for key, label in reader_ui._ITEM_LABELS:
        text, status, links = "記録不足で不明", "不明", ""
        if item:
            value = item[key]
            text = (f"T{item['turn']} {ex.action_text(item)}／場所: {value.get('zone') or '不明'}" if key == "choice"
                    else ex.knowledge_text(value) if key == "grounds"
                    else ex.turning_text(value) if key == "turning" else value["text"])
            status = ({**ex.LABELS, "candidate": "候補"}.get(value.get("confirmation"), "不明")
                      if key == "turning" else ex.LABELS.get(value.get("status"), "不明"))
            sources = ([item["source"]] if key == "choice" else value.get("sources", []) if key == "grounds"
                       else [x["before_source"] for x in value.get("items", []) if x.get("before_source")] + [item["source"]] if key == "cost"
                       else value.get("search", {}).get("candidate_sources", []))
            links = " · ".join(_link(f'{raw_href}?line={int(s["line"])}#L{int(s["line"])}', f'原ログ L{int(s["line"])}') for s in sources)
        extra = ""
        if key == "grounds":
            extra = "記録された知識の範囲です。心理的な動機とは区別します。"
        elif key == "cost":
            extra = "遅延した代償は未確認です。"
            if item and item[key].get("status") == "confirmed" and not item[key].get("complete"):
                extra += "ほかの即時の代償に不明な部分があります。"
        elif key == "turning" and explanation:
            details = ex.panel(explanation).replace(ex.base_url(explanation) + "/raw", raw_href)
            extra = '<details><summary>抽選条件・根拠の詳細</summary>' + details + "</details>"
        cells.append(f'<section class="cp-evidence-cell"><h3>{label} <small>{E(status)}</small></h3>'
                     f'<p>{E(text)}</p><div class="cp-sources">{links}</div>'
                     f'<div class="cp-hint">{extra if key == "turning" else E(extra)}</div></section>')
    return "".join(cells)


def render(handler, experiment_name, query):
    repo = handler.repository
    rid = repo.catalog.register_legacy(experiment_name)
    v = sifting_view.load(handler, rid)
    cells, ids = query.get("cell", []), query.get("candidate", [])
    if (not 2 <= len(cells) <= 4 or len(set(cells)) != len(cells)
            or v["rep_error"] or any(cell not in v["reps"] for cell in cells)):
        raise data.BadRequest("比較する異なる区画の代表候補を2〜4件選んでください。")
    bound = [v["reps"][cell] for cell in cells]
    if len(set(bound)) != len(bound):
        raise data.BadRequest("比較する異なる候補を2〜4件選んでください。")
    if ("candidate" in query or "publication" in query) and (
            query.get("publication") != [str(v["publication"])] or ids != bound):
        raise data.BadRequest("比較対象が更新されています。格子で対象を選び直してください")
    items = {c["candidate_id"]: dict(c) for c in v["items"]}
    experiment = repo.experiment(experiment_name)
    explanations = {}
    for cid in bound:
        c = items[cid]
        try:
            explanation = (data.cell_explanation(repo, experiment, c["cell_key"])
                           if c.get("log", {}).get("availability") == "present" else None)
            if explanation and explanation["source"]["sha256"] != c.get("source_log_sha256"):
                raise data.BadRequest("候補の原記録が更新されています。比較対象を選び直してください。")
        except (data.MissingResource, FileNotFoundError):
            explanation = None
        explanations[cid] = explanation
        reader = (explanation or {}).get("reader_summary")
        c["reader_label"] = "AI生成・未照合"
        if reader:
            c["label"] = c.get("title") or reader["summary"]["title"]["text"]
            if not c["synopsis"]:
                c["synopsis"] = "\n\n".join(p["text"] for p in reader["summary"]["synopsis"])
                c["reader_label"] = reader_ui._short_label(reader)
        if c["synopsis"]:
            c["can_synopsis"] = False
    # The cell-based extractor must still belong to the publication we just bound.
    latest = repo.catalog.snapshot(rid)
    legacy_changed = v["legacy"] and sha256(repo.safe_path(experiment, "archive.json").read_bytes()).hexdigest() != latest["manifest"].get("source_archive_sha256")
    if latest["revision"] != v["publication"] or legacy_changed:
        raise data.BadRequest("公開版が更新されました。比較対象を選び直してください。")
    base = f'/exp/{U(experiment_name)}/compare'
    known = [x for x in explanations.values() if x and x.get("representative")]
    trajectory = ("主人公の行動・対象・結果の並びは同じ筋です。" if len({x["trajectory_signature"] for x in known}) == 1 else "主人公の行動・対象・結果の並びに差があります。") if len(known) == len(bound) else "記録不足の候補があるため、筋の一致は未確認です。"
    initial = {"trajectory_message": trajectory, "run_id": rid, "revision": v["revision"], "publication": v["publication"],
               "bound": bound, "base": base, "running": v["running"],
               "items": [{k: c.get(k) for k in ("candidate_id", "state", "note")} for c in v["items"]]}
    body = '<div class="rw-shell cp-shell" data-comparison data-initial="' + E(json.dumps(initial, ensure_ascii=False)) + '">'
    body += '<header class="cp-heading"><div><p class="cp-breadcrumb">Sifting / 候補比較</p><h1>候補を比べる</h1><p>あらすじを読み比べて、採用する候補を選びます。</p></div><div class="cp-heading-actions"><button type="button" data-cp-picker>比較する候補を変更</button>' + _link(f'/runs/{U(rid)}/candidates', "一覧に戻る") + '</div></header>'
    body += '<div class="cp-tabs" role="tablist" aria-label="比較する情報">' + "".join(
        f'<button type="button" role="tab" id="cp-tab-{key}" aria-controls="cp-panel" aria-selected="{str(key == "story").lower()}" tabindex="{0 if key == "story" else -1}" data-cp-tab="{key}">{label}</button>' for key, label in TABS) + '</div>'
    body += '<div class="cp-caption" data-cp-caption>あらすじは保存された生成文です。各候補の照合状態と原記録を確認できます。</div>'
    if v["running"]:
        body += '<p class="cp-notice">実行中のため判定・メモは保存できません。</p>'
    body += '<div class="cp-notice" role="status" data-cp-notice hidden></div>'
    body += f'<div class="cp-scroll" id="cp-panel" role="tabpanel" aria-labelledby="cp-tab-story" tabindex="0"><div class="cp-matrix" data-view="story" style="--cp-count:{len(bound)}">'
    for i, cid in enumerate(bound):
        c = items[cid]
        body += f'<article class="cp-card" data-cp-card="{E(cid)}" aria-label="候補 {chr(65+i)}" style="grid-column:{i+1}">'
        body += f'<header class="cp-card-head"><span class="cp-letter">{chr(65+i)}</span><div><h2>{E(c["label"])}</h2><small>第{E(c.get("generation"))}世代 · {"到達" if c.get("reached") else "未到達"}</small></div><button type="button" data-cp-remove="{E(cid)}" aria-label="候補 {chr(65+i)} を比較から外す"' + (' disabled' if len(bound) == 2 else '') + '>×</button></header>'
        body += '<section class="cp-story cp-body" data-cp-view="story"><h3>あらすじ</h3>'
        if c["synopsis"]:
            body += f'<p class="cp-prose" data-cp-story>{E(c["synopsis"])}</p><small data-cp-reader>{E(c["reader_label"])}</small>'
        else:
            body += f'<p class="cp-prose" data-cp-story>{E(c["synopsis_state"])}。原記録から内容を確認できます。</p><small data-cp-reader></small>'
            if c["can_synopsis"]:
                body += f'<p><a data-cp-generate href="/runs/{U(rid)}/generate?kind=synopsize&amp;candidate={U(cid)}">この候補のあらすじを準備 →</a></p>'
            elif c["output_href"]:
                body += '<p>' + _link(c["output_href"], "生成状況を確認 →") + '</p>'
        body += '<p>' + _link(c["raw_href"] or c["detail_href"], "根拠となる記録を見る →") + '</p></section>'
        body += '<div class="cp-evidence" data-cp-view="evidence" hidden>' + _evidence(explanations[cid], c["raw_href"]) + '</div>'
        body += '<section class="cp-body cp-data" data-cp-view="data" hidden><h3>実験データ</h3><dl>'
        for label, value in (("候補ID", cid), ("区画", c["cell_key"]), ("世代", c.get("generation")), ("seed", c.get("seed")), ("品質", c["quality_text"]), ("結末", c["ending_text"]), ("原記録", c["availability"])):
            body += f'<dt>{label}</dt><dd>{E(value if value is not None else "—")}</dd>'
        body += '</dl><p class="cp-hint">品質の値だけで物語の優劣は判定しません。</p>'
        if c["raw_href"]: body += _link(c["raw_href"], "原記録を読む →")
        body += '</section><section class="cp-decision"><fieldset><legend>この候補の判定</legend><div class="cp-verdict">'
        for state, label in VERDICTS:
            disabled = v["running"] or (state == "adopted" and not c["screenable"])
            body += f'<label><input type="radio" name="verdict-{i}" value="{state}" data-cp-state' + (' checked' if c["state"] == state else '') + (' disabled' if disabled else '') + f'><span>{label}</span></label>'
        body += '</div></fieldset>'
        if not c["screenable"]:
            body += f'<p class="cp-hint">採用できません：{E(c["availability"] if c.get("reached") else "結末に未到達")}</p>'
        body += f'<label class="cp-note-label" for="cp-note-{i}">選ぶ理由・メモ</label><textarea id="cp-note-{i}" data-cp-note rows="2" placeholder="気になった点を残す…"' + (' disabled' if v["running"] else '') + f'>{E(c["note"])}</textarea></section></article>'
    body += '</div></div><footer class="cp-footer"><div><span role="status" data-cp-save>判定・メモは自動保存</span><button type="button" data-cp-check hidden>保存状況を確認</button><button type="button" data-cp-retry hidden>自分の入力を保存</button><button type="button" data-cp-discard hidden>最新の判定を使う</button></div><div><span data-cp-count></span><button class="cp-primary" type="button" data-cp-proceed disabled>採用候補を確認 →</button></div></footer>'
    body += '<dialog class="cp-picker" aria-labelledby="cp-picker-title"><form method="dialog"><header><div><h2 id="cp-picker-title">比較する候補を変更</h2><p>各区画の代表候補を2〜4件選びます。採用とは別の選択です。</p></div><button aria-label="閉じる">×</button></header></form><label class="cp-search">候補を探す<input type="search" data-cp-search placeholder="候補名・区画・あらすじ"></label><div class="cp-picker-list">'
    for c in items.values():
        if not c["representative"]: continue
        body += f'<label data-cp-option><input type="checkbox" data-cp-choice value="{E(c["candidate_id"])}" data-cell="{E(c["cell_key"])}"><span><strong>{E(c["label"])}</strong><small>{E(c["cell_key"])} · 第{E(c.get("generation"))}世代</small><span>{E(c["synopsis"][:100] or c["synopsis_state"])}</span></span></label>'
    body += '</div><footer><span role="status" data-cp-picked></span><button type="button" class="cp-primary" data-cp-apply>選んだ候補を比較</button></footer></dialog><noscript>比較の切り替え・判定の保存にはJavaScriptが必要です。</noscript></div>'
    body += generation_pages.surface(modal=True)
    doc = pages.document("候補を比べる", body, phase="sifting", world=v["world"], run=v["name"], output_run=rid,
                         job_store=wb._job_store(handler), page_class="run-observer")
    assets = generation_pages.ASSETS + '<link rel="stylesheet" href="/static/run-workspace.css"><link rel="stylesheet" href="/static/comparison.css"><script src="/static/comparison.js" defer></script>'
    handler._send_html(doc.replace("</head>", assets + "</head>"))
