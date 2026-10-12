"""Read-only Sifting actions bound to a fixed candidate and publication."""
from urllib.parse import urlencode
from execution.provenance import ConfigError, contained
from execution.output_store import verified
from viewer import data, raw_view as model, raw_pages


def result_text(row):
    # effective records whether the action had an effect, not whether the story won.
    if row.get("effective") is True:
        return "効果あり"
    if row.get("effective") is False:
        return "効果なし"
    return "未記録"


def overview(source):
    records = [r for r in source["records"] if r.kind == "decision" and model.scalar(r.row.get("verb"))]
    header = next((r.row for r in source["records"] if r.kind == "header"), {})
    return {"protagonist": model.scalar(header.get("protagonist")),
            "actors": sorted({model.scalar(r.row.get("subject")) for r in records}),
            "invalid_count": sum(bool(r.error and r.kind != "blank") for r in source["records"]),
            "rows": [{"line": r.line, "day": model.scalar(r.row.get("day")),
                      "slot": model.scalar(r.row.get("slot")), "turn": model.scalar(r.row.get("turn")),
                      "actor": model.scalar(r.row.get("subject")),
                      "action": model.title({**r.row, "subject": ""}), "result": result_text(r.row)} for r in records]}


def detail(record, raw_href, source_hash):
    row = record.row
    details = model.mapping(row.get("details"))
    route = model.mapping(model.mapping(row.get("policy")).get("route"))
    items = [("日・スロット", (model.scalar(row.get("day")) + "日目" if model.scalar(row.get("day")) else "日：未記録") + " · " + (model.scalar(row.get("slot")) or "スロット：未記録")),
             ("人物", row.get("subject", "未記録")), ("結果", result_text(row))]
    for label, key in (("相手", "target"), ("品物", "item"), ("場所", "zone"), ("話題", "topic")):
        value = details.get(key, row.get(key, model.mapping(row.get("explanation")).get(key)))
        if value is not None:
            items.append((label, value))
    if row.get("args"):
        items.append(("対象・引数", " / ".join(raw_pages.value_text(v) for v in model.sequence(row["args"]))))
    # Use only saved text; do not infer prose reasons from missing annotations.
    reason = model.scalar(route.get("text")) or "未記録"
    html = raw_pages.fields(items) + '<h3>記録された理由</h3><p>' + raw_pages.E(reason) + '</p>'
    html += state_changes(row) + raw_pages.changes(row) + raw_pages.selection(row)
    if details or "result" in row:
        html += '<details><summary>行動の結果の記録を詳しく見る</summary>' + raw_pages.fields([("結果コード", row.get("result", "未記録"))]) + raw_pages.all_fields(details) + '</details>'
    return {"line": record.line, "title": model.action_label(row), "html": html,
            "raw_href": raw_href + "?" + urlencode({"line": record.line, "source": source_hash})}


def state_changes(row):
    """Actor/target deltas hold new values; only relation deltas are differences."""
    delta = model.mapping(row.get("delta"))
    labels = {"stamina":"体力", "stress":"ストレス", "zone":"場所", "inventory":"所持品",
              "resources":"資源", "assets":"資産", "bonds":"絆", "reputation":"評判",
              "knowledge":"知識", "alive":"生存", "phase":"段階", "stance":"立場",
              "ability":"能力", "base":"基礎値", "modifiers":"補正", "pending":"予定される効果",
              "value":"値", "active":"有効", "visible":"見える状態"}
    values = []
    def leaves(value, path):
        if isinstance(value, dict):
            for key, child in value.items():
                leaves(child, path + [labels.get(key,key)])
        elif isinstance(value, list):
            descriptions = [model.scalar(model.mapping(v).get("description")) or model.scalar(model.mapping(v).get("label")) for v in value]
            text = f"{len(value)}件" + (" · " + " / ".join(v for v in descriptions[:3] if v) if any(descriptions[:3]) else "（詳細は変化の記録へ）")
            values.append((" / ".join(path), text))
        else:
            values.append((" / ".join(path), value))
    leaves(model.mapping(delta.get("actor")), [model.scalar(row.get("subject")) or "人物：未記録"])
    for person, value in model.mapping(delta.get("targets")).items():
        leaves(value, [str(person)])
    if not values:
        return ""
    return '<h3>変化した状態（変更後の値）</h3>' + raw_pages.fields(values[:12])


def load(catalog, run_id, candidate_id, query):
    allowed = {"publication", "source", "line"}
    if set(query) - allowed or any(len(v) != 1 for v in query.values()):
        raise ConfigError("request", "行動ログの要求項目が不正です", code="bad_request")
    publication = query.get("publication", [None])[0]
    digest = query.get("source", [None])[0]
    if not publication or not digest:
        raise ConfigError("request", "公開版と原記録を指定してください", code="bad_request")
    snapshot = catalog.snapshot(run_id)
    if str(snapshot["revision"]) != publication:
        raise ConfigError("publication", "公開版が更新されています。候補一覧を再読み込みしてください。", code="conflict")
    candidate = next((c for c in snapshot["candidates"]["candidates"] if c["candidate_id"] == candidate_id), None)
    if candidate is None:
        raise ConfigError("candidate_id", "候補がありません", code="not_found")
    if candidate.get("source_log_sha256") != digest:
        raise ConfigError("source", "候補の原記録が更新されています。再読み込みしてください。", code="conflict")
    log = candidate["log"]
    if log.get("availability") != "present" or not log.get("relative_path"):
        raise ConfigError("source", "原記録を確認できません。候補一覧を再読み込みしてください。", code="not_found")
    root, _legacy = catalog.resolve(run_id)
    try:
        source = model.source_bytes(verified(contained(root, log["relative_path"]), digest),
                                   relative=log["relative_path"], experiment=snapshot["experiment_name"],
                                   cell=candidate.get("cell_key") or "未分類")
    except data.BadRequest as error:
        raise ConfigError("source", str(error), code="bad_request") from error
    bound = {"candidate_id": candidate_id, "publication": snapshot["revision"], "source": digest}
    if "line" not in query:
        return {**bound, **overview(source)}
    value = query["line"][0]
    if len(value) > 9 or not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= len(source["records"]):
        raise ConfigError("line", "行番号が原記録の範囲外です", code="bad_request")
    record = source["records"][int(value)-1]
    if record.kind != "decision" or not model.scalar(record.row.get("verb")):
        raise ConfigError("line", "行動の行を選択してください", code="bad_request")
    raw_href = f'/runs/{raw_pages.U(run_id)}/candidates/{raw_pages.U(candidate_id)}/raw'
    return {**bound, **detail(record, raw_href, digest)}
