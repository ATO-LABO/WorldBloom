"""Read-only screening model; identity and receipt hashes stay authoritative."""
import math
from datetime import datetime, timezone
from execution.output_store import OutputStore, verified
from execution.provenance import ConfigError, contained, read_json
from execution.worker import TERMINAL

ERRORS = (ConfigError, OSError, ValueError, KeyError, TypeError, AttributeError)
LABELS = {"ok": "本文あり", "pending": "未開始", "running": "生成中", "prompt_only": "プロンプトのみ",
          "error": "失敗", "unknown": "結果不明", "damaged": "読み取り不可", "skipped_limit": "上限で未開始",
          "skipped_cancelled": "停止で未開始", "skipped_interrupted": "中断で未開始"}


def body_text(store, oid, entry):
    if entry.get("status") != "ok" or not entry.get("text_ref"):
        return None
    try:
        return verified(contained(store.folder(oid), entry["text_ref"]), entry["text_sha256"]).decode("utf-8")
    except ERRORS:
        return None


def created_at(store, output, jobs):
    value = (jobs.get(output["request"].get("job_id")) or {}).get("created_at")
    if value is None:
        try:
            value = read_json(store.folder(output["output_id"]) / "quota.json").get("created_at")
        except ERRORS:
            value = None
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 < value < 253402300799 else None


def date_label(value):
    return datetime.fromtimestamp(value, timezone.utc).astimezone().strftime("%Y/%m/%d %H:%M") if value else "日時の記録なし"


def build(job_store, outputs):
    store = OutputStore(job_store.configs.control)
    try:
        jobs = {j["job_id"]: j for j in job_store.list()}
    except ERRORS:
        jobs = {}
    works = {}
    for output in outputs:
        req = output.get("request") or {}
        if req.get("kind") != "narrate":
            continue
        oid, rid = output["output_id"], req["run_id"]
        stamp = created_at(store, output, jobs)
        entries = {e["candidate_id"]: e for e in output.get("entries", [])}
        for cid in req["candidate_ids"]:
            entry = entries.get(cid, {"candidate_id": cid, "status": "pending"})
            text = body_text(store, oid, entry)
            status = entry.get("status", "pending")
            if status == "ok" and text is None:
                status = "damaged"
            draft = {"output_id": oid, "request": req, "entry": entry, "status": status,
                     "text": text, "created_at": stamp, "date": date_label(stamp),
                     "active": output.get("job_state") is not None and output["job_state"] not in TERMINAL}
            work = works.setdefault((rid, cid), {"run_id": rid, "candidate_id": cid, "title": cid[:17] + "…", "drafts": []})
            work["drafts"].append(draft)
    for work in works.values():
        # An ID is only a deterministic tie-breaker, never evidence of recency.
        drafts = sorted(work["drafts"], key=lambda d: (d["created_at"] or 0, d["output_id"]))
        work["drafts"] = drafts
        stamps = [d["created_at"] for d in drafts]
        ordered = None not in stamps and len(set(stamps)) == len(stamps)
        for n, draft in enumerate(drafts, 1):
            draft["label"] = (f"第{n}稿" if ordered else "稿 " + draft["output_id"][:14])
            if ordered and n == len(drafts): draft["label"] += "（最新）"
            draft["label"] += " · " + LABELS.get(draft["status"], draft["status"])
        work["preferred"] = next((d for d in reversed(drafts) if d["status"] == "ok"), drafts[-1])
        work["created_at"] = max(stamps, key=lambda v: v or 0)
    return list(works.values())


def synopsis(store, draft, cid):
    req = draft["request"]
    ref = (req.get("synopsis_refs") or {}).get(cid)
    if not ref:
        return "この稿には入力あらすじの参照がありません。"
    try:
        prior = store.request(ref["output_id"])
        item = store.sink(ref["output_id"], cid).current()
        if (prior["kind"] != "synopsize" or prior["run_id"] != req["run_id"]
            or prior["input_manifest_sha256"] != req["input_manifest_sha256"]
            or cid not in prior["candidate_ids"] or item is None or item["status"] != "ok"
            or item["attempt_id"] != ref["attempt_id"] or item["text_sha256"] != ref["text_sha256"]):
            return "この稿の入力あらすじを照合できません。生成記録を確認してください。"
        text = body_text(store, ref["output_id"], item)
        return text if text is not None else "入力あらすじを読み込めません。"
    except ERRORS:
        return "入力あらすじを読み込めません。"
