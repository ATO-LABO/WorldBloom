"""Chronological, editable story plans with immutable material references."""
from copy import deepcopy
from execution.provenance import ConfigError, canonical, sha256
from gapengine.story_materials import verify_material
from gapengine.story_spine import pivotal_event_ids, narrative_threads

IMPORTANT = {"give_item", "buy", "craft", "fight", "downed", "revived", "dead", "disguise",
             "exposure", "identity_revealed", "neutralize", "sabotage", "mislead", "donate",
             "sacrifice", "pledge", "grand_gesture", "trial"}

def chosen_events(material):
    hero, foe = material["world"].get("protagonist"), material["world"].get("antagonist")
    important = []
    for e in material["events"]:
        actor, d, verb = e["actor_id"], e["details"], e["verb"]
        participates = actor in (hero, foe) or d.get("target") == hero or d.get("ally") == hero
        if e["type"] == "ending" or (verb == "payoff" and (actor == hero or any(hero in f.get("arguments", []) for f in e["facts"]))):
            important.append(e)
        elif participates and (verb in IMPORTANT or verb == "ally_gained") and e["effective"] is not False:
            important.append(e)
        elif actor == hero and verb in ("observe", "learn_fact") and (d.get("identity_seen") or d.get("learned") or d.get("revealed")):
            important.append(e)
        elif actor == hero and verb == "move" and d.get("dest") in ("鬼ヶ島", "村"):
            important.append(e)
    # Give the first successful protagonist move a place even in other worlds.
    start = next((e for e in material["events"] if e["actor_id"] == hero and e["verb"] == "move" and e["effective"] is not False), None)
    if start and start not in important:
        important.append(start)
    return sorted(important, key=lambda e: e["order"])

def create_plan(material, *, focus="関係と行動の変化をたどる", length=None):
    verify_material(material)
    selected = chosen_events(material)
    chosen = {e["event_id"] for e in selected}
    pivotal = pivotal_event_ids(material, selected)
    # Beat grouping is lossless: every original event reference remains.
    beats = []
    for e in selected:
        if beats and beats[-1]["turn"] == e["turn"]:
            beats[-1]["event_ids"].append(e["event_id"])
        else:
            beats.append({"beat_id": "beat-" + str(len(beats) + 1), "turn": e["turn"],
                "event_ids": [e["event_id"]], "purpose": "記録された転機を時系列で示す",
                "compression": "同じ時点の出来事をまとめる", "interpretation_notes": []})
    # Initial pass keeps the selected goal/relationship chain mandatory.
    # A user may choose a smaller required set only with explicit omission rationale.
    doc = {"schema_version": 1, "plan_id": "", "revision": 1,
        "material_ref": {"material_id": material["material_id"], "content_sha256": material["content_sha256"],
                         "source_binding_sha256": sha256(canonical(material["source_binding"]))},
        "focus": focus, "viewpoint": "第三者。設定上の真実と人物の認識を区別する",
        "tone": "読みやすい物語", "length": None,
        "required_event_ids": pivotal, "narrative_threads": narrative_threads(material, selected, pivotal), "beats": beats,
        "omissions": [{"event_ids": [e["event_id"]], "reason": "構成の主軸から省略。実際の出来事か未発動条件かは素材のtypeで区別する"}
                     for e in material["events"] if e["event_id"] not in chosen],
        "open_threads": [{"registered_event_id": e["registered_event_id"], "handling": "未発動。場面として書かない"}
                         for e in material["unresolved"]],
        "constraints": ["元ログの行順を保持", "主目標達成を全員の和解にしない", "downedを死亡にしない",
                        "companionsを仲間成立とみなさない", "未記録の動機・救助・知識の取得を断定しない"],
        "status": "draft"}
    return revise_identity(doc)

def revise_identity(plan):
    plan = deepcopy(plan)
    plan["plan_id"] = "plan-" + sha256(canonical({k:v for k,v in plan.items() if k != "plan_id"}))[:24]
    return plan

def validate_plan(material, plan):
    verify_material(material)
    if plan.get("schema_version") != 1:
        raise ConfigError("plan", "未対応の構成案です")
    ref = plan.get("material_ref")
    expected = {"material_id": material["material_id"], "content_sha256": material["content_sha256"],
                "source_binding_sha256": sha256(canonical(material["source_binding"]))}
    if ref != expected:
        raise ConfigError("plan", "構成案と素材が一致しません", code="snapshot_changed")
    if type(plan.get("revision")) is not int or plan["revision"] < 1 or plan.get("status") not in ("draft", "confirmed"):
        raise ConfigError("plan", "構成案の版・状態が不正です")
    for key in ("focus", "viewpoint", "tone"):
        if not isinstance(plan.get(key), str) or not plan[key].strip() or len(plan[key]) > 2000:
            raise ConfigError(key, "空でない短い文字列が必要です")
    # Historical plans retain their field for provenance, but length is not a quality gate.
    length = plan.get("length")
    if length is not None and (not isinstance(length, dict) or set(length) != {"min_chars", "max_chars"}
            or any(type(v) is not int for v in length.values())):
        raise ConfigError("length", "旧構成案の長さ記録が不正です")
    ids = {e["event_id"]: e for e in material["events"]}
    ordered = []
    for beat in plan.get("beats", []):
        if not isinstance(beat, dict) or not isinstance(beat.get("event_ids"), list) or not beat["event_ids"]:
            raise ConfigError("beats", "根拠を持つ場面が必要です")
        ordered.extend(beat["event_ids"])
    if not ordered or any(i not in ids for i in ordered) or len(set(ordered)) != len(ordered):
        raise ConfigError("beats", "不明・重複・空の出来事があります")
    if ordered != sorted(ordered, key=lambda i: ids[i]["order"]):
        raise ConfigError("beats", "出来事の順序を保持してください")
    required = plan.get("required_event_ids", [])
    if not isinstance(required, list) or not set(required) <= set(ordered):
        raise ConfigError("required_event_ids", "必須の出来事が構成にありません")
    threads = plan.get("narrative_threads", [])
    if not isinstance(threads, list) or any(not isinstance(t, dict) or not isinstance(t.get("question"), str)
            or not t["question"].strip() or len(t["question"]) > 2000
            or not isinstance(t.get("event_ids"), list) or not t["event_ids"]
            or not set(t["event_ids"]) <= set(required) for t in threads):
        raise ConfigError("narrative_threads", "主題と必須出来事の参照が不正です")
    ending_ids = {e["event_id"] for e in material["events"] if e["type"] == "ending"}
    if not ending_ids or not ending_ids <= set(required):
        raise ConfigError("ending", "実際の結末を必須の出来事として残してください")
    omitted=[]
    for omission in plan.get("omissions", []):
        if not isinstance(omission.get("reason"), str) or not omission["reason"].strip():
            raise ConfigError("omissions", "省略理由が必要です")
        omitted.extend(omission.get("event_ids", []))
        if any(i not in ids or i in ordered for i in omission.get("event_ids", [])):
            raise ConfigError("omissions", "省略する出来事の参照が不正です")
    if len(set(omitted)) != len(omitted) or set(ordered) | set(omitted) != set(ids):
        raise ConfigError("omissions", "構成から外した出来事には省略理由を残してください")
    for link in material["causal_links"]:
        if link["kind"] == "gift_to_ally" and link["to"] in ordered and link["from"] not in ordered:
            raise ConfigError("beats", "仲間成立に対応する贈与を残してください")
    return plan
