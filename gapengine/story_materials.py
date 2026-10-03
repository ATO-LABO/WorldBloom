"""Evidence-bound story material. Reads logs; never executes a simulation."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import yaml

from execution.provenance import ConfigError, canonical, sha256

VERSION = "story-materials-1"
KINDS = {
    "planted": "effect_registered", "payoff": "effect_applied",
    "ally_gained": "relation_transition", "learn_fact": "knowledge_transition",
    "exposure": "knowledge_transition", "identity_revealed": "knowledge_transition",
    "downed": "state_transition", "revived": "state_transition",
    "dead": "state_transition", "ending": "ending",
}
VERBS = {
    "move": "移動", "give_item": "贈与", "fight": "戦闘", "buy": "購入",
    "labor": "労働", "craft": "製作", "observe": "観察", "investigate": "調査",
    "neutralize": "無力化", "sabotage": "妨害", "disguise": "変装",
    "mislead": "誤情報を伝える", "share_knowledge": "情報を伝える",
    "rest": "休息", "rescue": "救助", "withdraw": "退却", "train": "訓練",
    "persuade": "説得", "pledge": "誓約", "negotiate": "交渉",
    "donate": "寄付", "sacrifice": "犠牲", "confront": "問いただす",
}

def material_digest(doc):
    return sha256(canonical({k: v for k, v in doc.items() if k != "content_sha256"}))

def verify_material(doc):
    if doc.get("schema_version") != 1 or doc.get("extractor_version") != VERSION:
        raise ConfigError("materials", "未対応の素材版です")
    if doc.get("content_sha256") != material_digest(doc):
        raise ConfigError("materials", "素材のSHAが一致しません", code="snapshot_changed")
    ids = [e["event_id"] for e in doc["events"]]
    if len(ids) != len(set(ids)):
        raise ConfigError("materials", "根拠IDが重複しています")
    return doc

def read_inputs(project, template):
    """Returns exact input bytes. No settings or credentials are captured."""
    project, template = Path(project), Path(template)
    files = {"project/world.yaml": (project / "world.yaml").read_bytes()}
    for p in sorted((project / "subjects").glob("*.yaml")):
        files["project/subjects/" + p.name] = p.read_bytes()
    for p in sorted(template.glob("*.yaml")):
        files["template/" + p.name] = p.read_bytes()
    return files

def merge_post(state, patch):
    """Actor/target deltas contain post-values; relation increments do not."""
    for key, value in patch.items():
        if value is None:
            state.pop(key, None)
        elif isinstance(value, dict):
            if not isinstance(state.get(key), dict):
                state[key] = {}
            merge_post(state[key], value)
        else:
            state[key] = deepcopy(value)

def _flat(value, prefix=""):
    for key, v in sorted(value.items()):
        path = prefix + "/" + str(key).replace("~", "~0").replace("/", "~1")
        if isinstance(v, dict):
            yield from _flat(v, path)
        else:
            yield path, v

def _label(row, display):
    verb, actor, d = row.get("verb"), display(row.get("subject")), row.get("details") or {}
    args = "、".join(str(x) for x in row.get("args", []))
    if verb == "planted":
        return f"{actor}：条件を登録（{d.get('library_id', d.get('effect_id', '不明'))}）。場面の発生・効果の発動ではない"
    if verb == "payoff":
        change = d.get("applied", {})
        return f"{actor}：効果が発動（{d.get('library_id', d.get('effect_id', '不明'))}）、作用={json.dumps(change, ensure_ascii=False)}"
    if verb == "ally_gained":
        return f"{actor}が{display(d.get('ally'))}に仲間として関わるようになった（この方向の関係）"
    if verb == "fight":
        return f"{actor}の戦闘：勝者={display(d.get('winner'))}、敗者={display(d.get('loser'))}、戦利品={json.dumps(d.get('loot', {}), ensure_ascii=False)}"
    if verb == "move":
        return f"{actor}：{d.get('origin', '不明')}から{d.get('dest', args)}へ移動、結果={row.get('result')}"
    if verb == "ending":
        return f"主目標達成：{d.get('label', row.get('id', '不明'))}、届け先={json.dumps(d.get('delivered', {}), ensure_ascii=False)}"
    if verb in ("downed", "revived", "dead"):
        return f"{actor}：{dict(downed='戦闘不能', revived='復帰', dead='死亡')[verb]}、記録={json.dumps(d, ensure_ascii=False)}"
    return f"{actor}：{VERBS.get(verb, verb)}（{args}）、結果={row.get('result', '不明')}、記録={json.dumps(d, ensure_ascii=False)}"

def extract_materials(raw, inputs, binding):
    rows = [json.loads(line) if line.strip() else None for line in raw.decode("utf-8").splitlines()]
    digest = sha256(raw)
    if binding.get("source_log_sha256", digest) != digest:
        raise ConfigError("source", "候補と元ログが一致しません", code="snapshot_changed")
    world = yaml.safe_load(inputs["project/world.yaml"]) or {}
    subjects = [(p, yaml.safe_load(b) or {}) for p, b in sorted(inputs.items()) if p.startswith("project/subjects/")]
    entities, initial = [], []
    def source_ref(path, pointer):
        return {"source": path, "sha256": sha256(inputs[path]), "pointer": pointer}
    for path, sub in subjects:
        sid = sub["id"]
        entities.append({"id": sid, "displayed": sub.get("identity", {}).get("displayed", sid),
            "true_identity": sub.get("identity", {}).get("true"), "evidence_refs": [source_ref(path, "/identity")]})
        for key in ("inventory", "relations", "knowledge", "beliefs_about", "goal", "companions", "range"):
            if key in sub:
                initial.append({"subject": sid, "predicate": key, "value": deepcopy(sub[key]),
                    "certainty": "observed_config", "evidence_refs": [source_ref(path, "/" + key)]})
    names = {e["id"]: e["displayed"] for e in entities}
    def display(sid):
        return names.get(sid, sid or "不明")
    effects_doc = yaml.safe_load(inputs.get("template/effects.yaml", b"[]")) or []
    effect_library = {e["id"]: e for e in effects_doc} if isinstance(effects_doc, list) else {}
    events, effects, links, state, relation_state, objective = [], [], [], {}, {}, {}
    last_snapshot = None
    active_effects, latest_gifts = {}, {}
    diagnostics = [{"kind": "partial_initial_state", "severity": "info",
                    "message": "設定の初期事実は保持する。実行時の全状態は記録されたsnapshot・差分の範囲でのみ扱う。"}]
    for n, row in enumerate(rows, 1):
        if row is None:
            continue
        kind, verb, actor = row.get("kind"), row.get("verb"), row.get("subject")
        if kind == "header":
            continue
        if kind == "snapshot":
            if actor:
                state[actor] = deepcopy(row.get("layers", {}))
            for r in row.get("relations", []):
                relation_state[(r["observer"], r["target"])] = {k: r[k] for k in ("affinity", "awareness") if k in r}
            last_snapshot = n
            continue
        if kind not in ("decision", "event"):
            diagnostics.append({"kind": "unsupported_row", "row": n, "severity": "warning"})
            continue
        event_id = f"log:{digest}:row:{n}"
        ref = {"source": "layers.jsonl", "sha256": digest, "row": n}
        delta, d = row.get("delta") or {}, deepcopy(row.get("details") or {})
        actor_post = delta.get("actor") or {}
        if actor is not None:
            merge_post(state.setdefault(actor, {}), actor_post)
        elif actor_post:
            diagnostics.append({"kind": "unknown_actor", "row": n, "severity": "warning"})
        for target, patch in (delta.get("targets") or {}).items():
            merge_post(state.setdefault(target, {}), patch)
        facts = []
        for who, patch in [(actor, actor_post), *list((delta.get("targets") or {}).items())]:
            for pointer, value in _flat(patch):
                # Pending descriptions are labels of conditions, not events.
                if pointer.startswith("/pending"):
                    continue
                facts.append({"predicate": "post_value", "arguments": [who, pointer],
                    "value": value, "certainty": "observed", "evidence_refs": [ref]})
        for r in delta.get("relations") or []:
            key = (r["observer"], r["target"])
            current = relation_state.get(key)
            changes = {k: r[k] for k in ("affinity", "awareness") if k in r}
            value = None
            if current is not None and all(k in current for k in changes):
                for k, v in changes.items():
                    current[k] = round(current[k] + v, 4)
                value = deepcopy(current)
            facts.append({"predicate": "relation_increment", "arguments": list(key), "value": changes,
                          "post_value": value, "certainty": "observed", "evidence_refs": [ref]})
        if delta.get("objective"):
            merge_post(objective, delta["objective"])
            facts.append({"predicate": "objective_post", "arguments": [], "value": delta["objective"],
                          "certainty": "observed", "evidence_refs": [ref]})
        if verb == "ally_gained":
            facts.append({"predicate": "ally_gained", "arguments": [actor, d.get("ally")],
                          "value": True, "certainty": "observed", "evidence_refs": [ref]})
        if verb in ("learn_fact", "exposure", "identity_revealed") or (verb == "observe" and d.get("identity_seen")):
            facts.append({"predicate": "knowledge_record", "arguments": [actor], "value": d,
                          "certainty": "observed", "evidence_refs": [ref]})
        event = {"event_id": event_id, "order": n, "turn": row.get("turn"), "day": row.get("day"),
            "slot": row.get("slot"), "type": KINDS.get(verb, "action" if kind == "decision" else "other"),
            "actor_id": actor, "target_ids": [d["target"]] if d.get("target") else [],
            "verb": verb, "result": row.get("result"), "effective": row.get("effective"),
            "args": deepcopy(row.get("args", [])), "details": d, "facts": facts,
            "label": _label(row, display), "evidence_refs": [ref],
            "state_as_of": {"row": n, "snapshot_row": last_snapshot, "precision": "recorded_fields_only"},
            "diagnostics": ["動機は記録がある場合だけ解釈する"] if kind == "decision" else []}
        events.append(event)
        if verb == "give_item" and row.get("result") == "given":
            latest_gifts[(actor, d.get("target"))] = event
        if verb == "ally_gained":
            gift = latest_gifts.get((d.get("ally"), actor))
            if gift and gift["turn"] == event["turn"] and gift["args"][-1:] == [d.get("item")]:
                links.append({"kind": "gift_to_ally", "from": gift["event_id"], "to": event_id,
                              "rule": "same_turn_giver_receiver_item"})
        eid = d.get("effect_id")
        if verb == "planted" and eid:
            lib = effect_library.get(d.get("library_id"), {}).get("payoff", {})
            effect = {"effect_instance_id": eid, "library_id": d.get("library_id"), "planter_id": actor,
                "target_id": d.get("target"), "registered_event_id": event_id, "applied_event_ids": [],
                "status": "registered", "condition": lib.get("condition"), "label": lib.get("description"),
                "actual_changes": [], "diagnostics": ["説明は将来の効果のラベル。場面や知識の実在を証明しない。"]}
            effects.append(effect)
            active_effects[(actor, eid)] = effect
        elif verb == "payoff" and eid:
            effect = active_effects.get((actor, eid))
            if effect is None:
                effect = {"effect_instance_id": eid, "library_id": d.get("library_id"), "planter_id": actor,
                    "target_id": None, "registered_event_id": None, "applied_event_ids": [],
                    "status": "unknown", "condition": None, "label": d.get("description"),
                    "actual_changes": [], "diagnostics": ["登録行が未確認"]}
                effects.append(effect)
            effect["status"] = "applied"
            effect["applied_event_ids"].append(event_id)
            effect["actual_changes"].append(d.get("applied", {}))
            if effect["registered_event_id"]:
                links.append({"kind": "effect_registration_to_application", "from": effect["registered_event_id"],
                              "to": event_id, "rule": "same_planter_effect_instance"})
    source = {**deepcopy(binding), "source_log_sha256": digest,
              "input_files": [{"path": p, "sha256": sha256(b)} for p, b in sorted(inputs.items())]}
    doc = {"schema_version": 1, "extractor_version": VERSION, "material_id": "mat-" + sha256(canonical(source))[:24],
        "source_binding": source, "world": {"name": world.get("name"), "protagonist": world.get("protagonist"),
        "antagonist": world.get("antagonist"), "ally_threshold": world.get("companionship", {}).get("threshold")},
        "entities": entities, "initial_facts": initial, "events": events, "pending_effects": effects,
        "causal_links": links, "unresolved": [e for e in effects if e["status"] != "applied"],
        "diagnostics": diagnostics, "final_recorded_state": {"subjects": state,
        "relations": [{"observer": a, "target": b, **v} for (a, b), v in sorted(relation_state.items())],
        "objective": objective, "precision": "recorded_fields_only"}}
    doc["content_sha256"] = material_digest(doc)
    return verify_material(doc)
