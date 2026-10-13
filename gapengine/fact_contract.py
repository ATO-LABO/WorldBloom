"""Versioned, evidence-bound supplements. Original logs and story_v1 stay intact."""
from copy import deepcopy
import json
from execution.provenance import ConfigError, canonical, identifier, sha256

VERSION = "facts-1"
KINDS = {"world_fact", "claim", "delivery", "notice", "understanding", "belief",
         "action_candidate", "action_selected", "action_execution", "action_result",
         "situation", "agreement"}
UNKNOWN = {"not_evaluated", "not_recorded", "unsupported_extraction", "contradiction", "missing_rule"}
COMMON = """固定された場面記録と指定された視点・開示範囲に基づいて文章を書く。
主体、対象、時点、合意版、数量、順序、因果、発言、人物の知識と記録された判断を保持する。
話者の主張や推測を世界の事実に変えない。到達・気づき・理解を混ぜない。
候補・選択・実行・結果を混ぜない。未知を埋めず、記録にない原因・心理・行動・発言を足さない。
あらすじは補助であり新しい事実の根拠にはしない。固定記録と矛盾するあらすじの表現を採用しない。
文体設定から事実保持条件や開示範囲を変更しない。本文には根拠IDや検査の説明を出さない。"""
DEFAULTS = {
    "synopsize": "主筋を短く分かりやすくまとめる。意味を決める前提と結果、誰がいつ何を知り判断したかを残す。",
    "narrate": "読みやすい現代日本語で場面を書く。語順、文の長短、段落、引用、間を工夫する。台詞と心理は記録された意味の範囲で描く。",
}


def entity_identifier(value):
    """Entity names are data, never filesystem paths; existing worlds use Japanese IDs."""
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > 256 or any(ord(c) < 32 for c in value):
        _fail("actor", "人物の識別子が不正です")
    return value


def _fail(field, message):
    raise ConfigError(field, message)


def digest(doc):
    return sha256(canonical({k: v for k, v in doc.items() if k not in {"id", "sha256"}}))


def seal(doc, prefix):
    result = deepcopy(doc)
    result["sha256"] = digest(result)
    result["id"] = prefix + result["sha256"][:32]
    return result


def check_seal(doc, prefix):
    if (not isinstance(doc, dict) or type(doc.get("schema_version")) is not int
            or doc["schema_version"] != 1 or doc.get("contract_version") != VERSION
            or doc.get("sha256") != digest(doc) or doc.get("id") != prefix + digest(doc)[:32]):
        _fail("facts", "補助資料の版・ID・SHAが一致しません")


def validate_records(records):
    """Validate explicit state transitions; never infer missing understanding."""
    seen = {}
    last = -1
    if not isinstance(records, list):
        _fail("records", "記録の配列が必要です")
    for r in records:
        if not isinstance(r, dict) or set(r) != {"id", "kind", "actor", "at", "content", "depends_on", "evidence"}:
            _fail("records", "型付き記録の項目が不正です")
        identifier(r["id"]); entity_identifier(r["actor"])
        if r["id"] in seen or not isinstance(r["kind"], str) or r["kind"] not in KINDS or type(r["at"]) is not int or r["at"] < 0 or r["at"] < last:
            _fail("records", "記録のID・型・時系列が不正です")
        if not isinstance(r["content"], dict) or not r["content"]:
            _fail("content", "意味を構造化して記録してください")
        deps = r["depends_on"]
        if not isinstance(deps, list) or any(not isinstance(i, str) or i not in seen for i in deps) or len(set(deps)) != len(deps):
            _fail("depends_on", "根拠は先行する記録へ結んでください")
        evidence = r["evidence"]
        if not isinstance(evidence, list) or not evidence:
            _fail("evidence", "原記録への参照が必要です")
        c, kind = r["content"], r["kind"]
        if kind in {"world_fact", "claim", "belief", "situation"} and not {"predicate", "value"} <= set(c):
            _fail("content", "命題と値が必要です")
        if kind in {"claim", "delivery", "notice", "understanding"}:
            identifier(c.get("message_id"), "message_id")
        previous = {"notice": "delivery", "understanding": "notice", "action_selected": "action_candidate",
                    "action_execution": "action_selected", "action_result": "action_execution"}.get(kind)
        if previous:
            key = "message_id" if kind in {"notice", "understanding"} else "action_id"
            identifier(c.get(key), key)
            if not any(seen[i]["kind"] == previous and seen[i]["actor"] == r["actor"]
                       and seen[i]["content"].get(key) == c[key] for i in deps):
                _fail("transition", "前段階の記録がありません: " + kind)
        if kind == "understanding":
            claims = c.get("claim_ids")
            if (not isinstance(claims, list) or not claims or len(set(claims)) != len(claims)
                    or any(i not in deps or seen[i]["kind"] != "claim"
                           or seen[i]["content"]["message_id"] != c["message_id"] for i in claims)):
                _fail("understanding", "理解した主張と同じ連絡の根拠が必要です")
        if kind == "belief":
            if c.get("domain") not in {"arrival_prediction", "reason_assessment", "intent_inference", "other"}:
                _fail("belief", "認識の適用範囲が必要です")
            if c.get("basis") != "initial":
                if (not {"before", "rule_version", "claim_ids"} <= set(c)
                        or not isinstance(c["claim_ids"], list) or not c["claim_ids"]
                        or any(not isinstance(i, str) for i in c["claim_ids"])
                        or len(set(c["claim_ids"])) != len(c["claim_ids"])
                        or not isinstance(c["rule_version"], str) or not c["rule_version"]):
                    _fail("belief", "更新前・規則版・理解した主張が必要です")
                understood = {i for dep in deps if seen[dep]["kind"] == "understanding"
                              and seen[dep]["actor"] == r["actor"] for i in seen[dep]["content"]["claim_ids"]}
                if not set(c["claim_ids"]) <= understood:
                    _fail("belief", "未理解の情報では認識を更新できません")
        if kind == "agreement" and not {"promise_id", "version", "terms_hash", "status"} <= set(c):
            _fail("agreement", "合意の適用版と状態が必要です")
        seen[r["id"]] = r
        last = r["at"]
    return records


def extract_supplement(raw, inputs, binding):
    """Only explicit semantic records are decoded. Old rows remain unknown."""
    records, unknowns = [], []
    for n, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        fact = row.get("details", {}).get("fact_record") if isinstance(row, dict) else None
        if fact is None:
            unknowns.append({"row": n, "status": "unsupported_extraction"})
            continue
        if (not isinstance(fact, dict) or set(fact) != {"schema_version", "id", "kind", "actor", "at", "content", "depends_on"}
                or type(fact["schema_version"]) is not int or fact["schema_version"] != 1
                or fact["actor"] != row.get("subject")):
            _fail("records", "原ログ内の型付き記録が不正です")
        records.append({k: deepcopy(v) for k, v in fact.items() if k != "schema_version"})
        records[-1]["evidence"] = [{"path": "layers.jsonl", "row": n, "row_sha256": sha256(line.encode("utf-8"))}]
    validate_records(records)
    return seal({"schema_version": 1, "contract_version": VERSION,
                 "source_binding": {**deepcopy(binding), "source_log_sha256": sha256(raw),
                                    "input_files": {p: sha256(b) for p, b in sorted(inputs.items())}},
                 "records": records, "unknowns": unknowns}, "supp-")


def verify_supplement(doc):
    check_seal(doc, "supp-")
    validate_records(doc["records"])
    if any(u.get("status") not in UNKNOWN for u in doc["unknowns"]):
        _fail("unknowns", "未知の分類が不正です")


def display_record(r):
    return {k: deepcopy(v) for k, v in r.items() if k != "evidence"}


def make_package(supplement, *, selected, required, hidden=(), viewpoint="第三者。人物の知識と読者への開示を区別する", styles=None, synopsis=None):
    verify_supplement(supplement)
    byid = {r["id"]: r for r in supplement["records"]}
    if any(not isinstance(v, (list, tuple)) or any(not isinstance(i, str) for i in v) for v in (selected, required, hidden)):
        _fail("selection", "記録IDの配列が必要です")
    selected, required, hidden = list(selected), list(required), list(hidden)
    for values in (selected, required, hidden):
        if len(set(values)) != len(values) or any(i not in byid for i in values):
            _fail("selection", "不明または重複した記録です")
    if not selected or not set(required) <= set(selected) or set(selected) & set(hidden):
        _fail("selection", "採用・必須・非開示の指定が一致しません")
    closure = set(selected)
    for r in reversed(supplement["records"]):
        if r["id"] in closure:
            closure.update(r["depends_on"])
    if closure & set(hidden):
        _fail("disclosure", "必須の根拠が非開示情報へ依存しています。選択を見直してください")
    if not isinstance(viewpoint, str) or not viewpoint.strip():
        _fail("viewpoint", "視点が必要です")
    styles = deepcopy(DEFAULTS if styles is None else styles)
    if not isinstance(styles, dict) or set(styles) != set(DEFAULTS) or any(not isinstance(v, str) or not v.strip() or len(v) > 16000 for v in styles.values()):
        _fail("styles", "あらすじ用・本文用の表現設定が必要です")
    if synopsis is not None and (not isinstance(synopsis, dict) or set(synopsis) !=
                                {"text", "output_id", "candidate_id", "attempt_id", "raw_text_sha256", "text_sha256"}
                                or not isinstance(synopsis["text"], str) or sha256(synopsis["text"].encode("utf-8")) != synopsis["text_sha256"]):
        _fail("synopsis", "あらすじ本文と固定参照が必要です")
    return seal({"schema_version": 1, "contract_version": VERSION, "supplement": deepcopy(supplement),
                 "selection": {"selected": selected, "required": required, "hidden": hidden, "viewpoint": viewpoint},
                 "styles": styles, "common_version": VERSION, "style_version": sha256(canonical(styles)),
                 "synopsis": deepcopy(synopsis)}, "facts-")


def verify_package(doc):
    check_seal(doc, "facts-")
    rebuilt = make_package(doc["supplement"], **doc["selection"], styles=doc["styles"], synopsis=doc["synopsis"])
    if doc != rebuilt:
        _fail("facts", "生成入力の内容が契約と一致しません")


def packet(package, kind):
    verify_package(package)
    if kind not in DEFAULTS:
        _fail("kind", "生成種別が不正です")
    selection = package["selection"]
    ids = set(selection["selected"])
    for r in reversed(package["supplement"]["records"]):
        if r["id"] in ids:
            ids.update(r["depends_on"])
    visible = [display_record(r) for r in package["supplement"]["records"] if r["id"] in ids]
    result = {"schema_version": 1, "contract_version": VERSION, "kind": kind,
              "viewpoint": selection["viewpoint"], "records": visible, "required_ids": selection["required"]}
    if kind == "narrate" and package["synopsis"] is not None:
        result["synopsis"] = deepcopy(package["synopsis"])
        result["synopsis_semantic_review_status"] = "not_reviewed"
    return result


def style_findings(style):
    # Bounded assistance, not a general natural-language conflict detector.
    forbidden = ("事実を変更", "未読でも理解", "主張を世界の事実", "記録にない発言を追加")
    return [{"kind": "style_conflict", "phrase": s} for s in forbidden if s in style]


def build_prompt(package, kind):
    data = packet(package, kind)
    style = package["styles"][kind]
    if style_findings(style):
        _fail("styles", "事実保持条件と矛盾する表現設定です")
    return (COMMON + "\n作者の表現設定（共通条件を優先）:\n" + style +
            '\n応答はJSON一つ: {"text":"読者向け本文", "assertions":[{"record_id":"ID", "record":{記録と同じ型付き内容}}]}。'
            'assertionsは本文で用いた意味の対応記録。recordは入力の該当記録を保持する。'
            '\n入力:\n' + json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
