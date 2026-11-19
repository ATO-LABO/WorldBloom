"""Read-only screening model; identity and receipt hashes stay authoritative."""
import json
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
        if store.request(oid).get("pipeline") in ("story_v1", "story_v2"):
            from execution.story_service import read_story_output
            result = read_story_output(store,oid,entry["candidate_id"])
            return result["text"] if result else None
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


def review_state(store, oid, entry):
    """Keep transport state separate from a Story v2 draft's review state."""
    if store.request(oid).get("pipeline") != "story_v2":
        return "acceptable"
    try:
        from execution.story_service import read_story_output
        result = read_story_output(store, oid, entry["candidate_id"])
        validation = (result or {}).get("validation") or {}
        folder = store.sink(oid, entry["candidate_id"]).folder
        pointer = read_json(folder / "story-review-pointer.json")
        saved = json.loads(verified(contained(folder, pointer["path"]), pointer["sha256"]))
        if (saved.get("adoption") == "accepted"
                and saved.get("text_sha256") == validation.get("text_sha256")
                and saved.get("plan_sha256") == validation.get("plan_sha256")):
            return "accepted"
    except ERRORS:
        pass
    return "pending"


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
            draft["review_state"] = review_state(store, oid, entry) if status == "ok" else "pending"
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
            if draft["status"] == "ok" and draft["review_state"] == "pending":
                draft["label"] += " · 要確認"
        # A new Story v2 draft is readable, but must not hide a prior legacy
        # or explicitly accepted version until a person has reviewed it.
        work["preferred"] = next((d for d in reversed(drafts)
                                  if d["status"] == "ok" and d["review_state"] in ("acceptable", "accepted")),
                                 next((d for d in reversed(drafts) if d["status"] == "ok"), drafts[-1]))
        work["created_at"] = max(stamps, key=lambda v: v or 0)
    return list(works.values())


def synopsis(store, draft, cid):
    req = draft["request"]
    if req.get("pipeline") == "story_v2":
        target = (req.get("story_refs") or {}).get(cid)
        if not target:
            return "この稿に対応する骨格のあらすじはありません。"
        try:
            candidates = []
            for folder in store.root.iterdir():
                if not folder.is_dir() or folder.name.startswith("."):
                    continue
                prior = store.request(folder.name)
                prior_ref = (prior.get("story_refs") or {}).get(cid)
                same_skeleton = (isinstance(prior_ref, dict) and isinstance(target, dict)
                                 and all(prior_ref.get(key) == target.get(key)
                                         for key in ("material_id", "material_sha256", "plan_id", "plan_sha256")))
                if (prior.get("pipeline") == "story_v2" and prior.get("kind") == "synopsize"
                        and prior.get("run_id") == req.get("run_id") and cid in prior.get("candidate_ids", [])
                        and same_skeleton):
                    item = store.sink(folder.name, cid).current()
                    if item and item.get("status") == "ok":
                        candidates.append((folder.name, item))
            if not candidates:
                return "同じ骨格から作ったあらすじはまだありません。"
            oid, item = sorted(candidates)[-1]
            text = body_text(store, oid, item)
            return text if text is not None else "同じ骨格から作ったあらすじを読み込めません。"
        except ERRORS:
            return "同じ骨格から作ったあらすじを読み込めません。"
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
