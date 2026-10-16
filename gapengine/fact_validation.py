"""Structural and typed-meaning checks are separate from prose review."""
import json
from execution.provenance import sha256, canonical
from gapengine.fact_contract import packet


def inspect_output(raw_text, package, kind):
    visible = packet(package, kind)
    byid = {r["id"]: r for r in visible["records"]}
    findings, semantic, refs = [], [], set()
    text = None
    try:
        doc = json.loads(raw_text)
        if isinstance(doc, dict) and isinstance(doc.get("text"), str):
            text = doc["text"]
        if (not isinstance(doc, dict) or set(doc) != {"text", "assertions"}
                or not isinstance(text, str) or not text.strip() or not isinstance(doc["assertions"], list)):
            raise ValueError("本文と意味の対応記録が必要です")
        for item in doc["assertions"]:
            if (not isinstance(item, dict) or set(item) != {"record_id", "record"}
                    or not isinstance(item["record_id"], str) or not isinstance(item["record"], dict)):
                raise ValueError("意味の対応記録の形式が不正です")
            rid = item["record_id"]
            if rid not in byid or rid in refs:
                raise ValueError("非開示・不明・重複した根拠IDです")
            refs.add(rid)
            if canonical(item["record"]) != canonical(byid[rid]):
                semantic.append({"kind": "typed_meaning_changed", "record_id": rid,
                                 "message": "主体・時点・型・内容・依存関係のいずれかが原記録と異なります"})
        missing = sorted(set(visible["required_ids"]) - refs)
        if missing:
            findings.append({"kind": "missing_required_refs", "record_ids": missing})
    except (ValueError, TypeError, KeyError) as error:
        findings.append({"kind": "invalid_structure", "message": str(error)})
    return {"text": text, "validation": {
        "schema_version": 1, "validator_version": "facts-validation-1",
        "package_sha256": package["sha256"], "raw_text_sha256": sha256(raw_text.encode("utf-8")),
        "structural_status": "failed" if findings else "passed", "structural_findings": findings,
        "typed_meaning_status": "failed" if semantic else ("not_checked" if findings else "passed"),
        "semantic_findings": semantic, "semantic_review_status": "not_reviewed", "human_adoption": "pending",
        "unverified": ["自由文と対応記録の意味の一致", "自由文の事実忠実性と非開示情報の漏れ", "文章の読みやすさと作品の魅力"],
        "limitation": "型付き対応記録の一致は、自由文の意味が正しい証明ではありません"}}
