"""Anchored Story v2 prompts and safe, deterministic assembly.

The model may add prose to a slot, but it never owns the log-derived facts:
those are inserted here after parsing, in source order.
"""
import json
import re

from execution.provenance import canonical, sha256
from gapengine.story_materials import verify_material
from gapengine.story_plan import validate_plan


VERSION = "story-v2-1"
_KINDS = {"synopsize": "synopsis", "narrate": "story", "synopsis": "synopsis", "story": "story"}


def _kind(kind):
    try:
        return _KINDS[kind]
    except KeyError:
        raise ValueError("kind must be synopsize or narrate") from None


def _clean_json(raw_text):
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned).strip()
    return json.loads(cleaned)


def _names(values):
    values = [str(value) for value in values if value not in (None, "")]
    return "、".join(values)


def _fact_sentence(event, recovered=()):
    """Return only a statement directly supported by one normalized event."""
    actor = event.get("actor_id")
    details = event.get("details") or {}
    verb = event.get("verb")
    if verb == "fight":
        winner, loser = details.get("winner"), details.get("loser")
        if winner and loser:
            loot = details.get("loot")
            text = f"{winner}は{loser}との戦いに勝った。"
            if isinstance(loot, dict) and loot:
                gained = _names(item for item in loot if item not in recovered)
                text += f"{winner}は{loser}から{gained}を得た。" if gained else ""
                returned = _names(item for item in loot if item in recovered)
                text += f"{winner}は{loser}に奪われた{returned}を取り戻した。" if returned else ""
            return text
    if not actor:
        return None
    if verb == "downed":
        return f"{actor}は倒れた。"
    elif verb == "revived":
        helper = details.get("by") or details.get("rescuer")
        return f"{helper}の救助で{actor}は持ち直した。" if helper else f"{actor}は持ち直した。"
    elif verb == "dead":
        killer = details.get("killer") or details.get("by")
        return f"{actor}は{killer}に殺された。" if killer else f"{actor}は息を引き取った。"
    elif verb in ("rescue", "rescued"):
        target = details.get("target") or (event.get("target_ids") or [None])[0]
        if target:
            return f"{actor}は{target}を救助した。"
    elif verb == "exposure":
        actual, displayed = details.get("actual"), details.get("displayed")
        target = details.get("target") or (event.get("target_ids") or [None])[0]
        if actual and displayed:
            return f"{actor}は{displayed}の正体が{actual}だと知った。"
        if actual and target:
            return f"{actor}は{target}の正体が{actual}だと知った。"
    elif verb == "mislead":
        target = details.get("target") or (event.get("target_ids") or [None])[0]
        adopted = details.get("outcome") == "adopted" or event.get("result") == "adopted"
        if target and adopted and details.get("accurate") is False:
            value = details.get("value")
            if details.get("about") == "oni_weakness" and isinstance(value, str):
                return f"{actor}は{target}に、弱点は{value}だと偽り、{target}はそれを信じた。"
            return f"{actor}が{target}に誤った情報を伝えると、{target}はそれを信じた。"
        if target and details.get("accurate") is False:
            return f"{actor}は{target}に誤った情報を伝えた。"
    elif verb == "neutralize":
        neutralized = details.get("neutralized") if isinstance(details.get("neutralized"), dict) else {}
        target = details.get("target") or neutralized.get("target") or (event.get("target_ids") or [None])[0]
        source = details.get("source") or neutralized.get("source")
        if event.get("result") == "neutralized" and target and source:
            return f"{actor}は{target}の{source}の力を封じた。"
    elif verb == "craft":
        if event.get("result") == "crafted" and details.get("item"):
            return f"{actor}は{details['item']}を作った。"
    elif verb == "move":
        dest = details.get("dest")
        if dest:
            return f"{actor}は{dest}へ移動した。"
    elif verb == "disguise":
        displayed = details.get("displayed") or details.get("as")
        if displayed:
            return f"{actor}は{displayed}に変装した。"
    elif verb == "give_item":
        target, item = details.get("target"), details.get("item")
        count = details.get("count")
        if target and item:
            suffix = f"を{count}個" if isinstance(count, int) else "を"
            return f"{actor}は{target}に{item}{suffix}渡した。"
    elif verb == "ally_gained":
        ally = details.get("ally")
        if ally:
            return f"{actor}は{ally}の仲間になった。"
    elif verb == "observe" and details.get("identity_seen") and details.get("target"):
        return f"{actor}は{details['target']}の正体を見抜いた。"
    if event.get("type") == "ending":
        delivered = details.get("delivered")
        if isinstance(delivered, dict) and delivered:
            destinations = set(delivered.values())
            if len(destinations) == 1:
                return f"{actor}は{_names(delivered.keys())}を{next(iter(destinations))}に届けた。"
    return None


def _packet(material, plan):
    validate_plan(material, plan)
    required = set(plan["required_event_ids"])
    hero = material["world"].get("protagonist")
    foe = material["world"].get("antagonist")
    central = {name for name in (hero, foe) if name}
    lost_to = set()
    slots = []
    events = material["events"]
    for index, event in enumerate(events):
        event_id, verb = event["event_id"], event["verb"]
        details, actor = event["details"], event["actor_id"]
        winner, loser, loot = details.get("winner"), details.get("loser"), details.get("loot")
        recovered = []
        if verb == "fight" and winner and loser and isinstance(loot, dict):
            recovered = [item for item in loot if (winner, loser, item) in lost_to]
            lost_to.update((loser, winner, item) for item in loot)
        relevant = (verb == "dead" and (actor in central or details.get("killer") in central)
                    or verb == "revived" and details.get("by") in central
                    or verb == "rescue" and (actor in central or details.get("target") in central)
                    or verb == "neutralize" and actor == hero and event.get("result") == "neutralized")
        if event_id not in required and not relevant:
            continue
        # A rescue and its immediate revival are two rows for one reported result.
        if verb == "rescue" and event_id not in required and any(
            next_event["verb"] == "revived" and next_event["turn"] == event["turn"]
            and next_event["actor_id"] == details.get("target") and next_event["details"].get("by") == actor
            for next_event in events[index + 1:index + 3]
        ):
            continue
        anchor = _fact_sentence(event, recovered)
        slots.append({
            "slot_id": f"scene-{len(slots) + 1:03d}", "event_id": event_id,
            "order": event["order"], "required": event_id in required, "render": True,
            "anchor": anchor,
            "review_reason": None if anchor else "固定文を安全に組み立てられません。原ログを確認してください。",
        })
    return {"schema_version": 1, "packet_version": VERSION,
            "world": material["world"], "slots": slots}


def build_story_v2_prompt(material, plan, kind):
    """Build a kind-specific prompt over the identical deterministic packet."""
    kind = _kind(kind)
    verify_material(material)
    packet = _packet(material, plan)
    task = "あらすじ" if kind == "synopsis" else "本文"
    compact = "補足は必要な場面だけ短くしてください。" if kind == "synopsis" else "補足は場面を自然につなぐために必要な分だけ書いてください。"
    return (
        f"あなたは記録に基づく日本語の{task}を書く補助です。文字数の上限・下限はありません。\n"
        "アプリ側が各 slot_id に anchor の文章を差し込みます。返す text には anchor を書き写したり、同じ事実を言い換えたりしないでください。"
        + compact +
        "直前から自然につなぐための文や、展開を変えない情景だけを補ってください。補足が不要なら slots に含めなくて構いません。"
        "記録にない動機、原因、感情、結果を断定しないでください。要確認の場面は補わないでください。\n"
        "返答は次の JSON のみです：{\"slots\":[{\"slot_id\":\"scene-001\",\"text\":\"任意の補足文\"}]}。"
        "slot_id の順番は任意ですが、知らない slot_id は含めないでください。\n"
        "固定場面パケット：\n" + json.dumps(packet, ensure_ascii=False, sort_keys=True)
    )


def _model_slots(raw_text, slot_ids, findings):
    try:
        document = _clean_json(raw_text)
        values = document.get("slots") if isinstance(document, dict) else None
        if not isinstance(values, list):
            raise ValueError("slots 配列が必要です")
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        findings.append({"severity": "error", "kind": "invalid_model_json", "message": str(error)})
        return {}
    prose = {}
    for value in values:
        if not isinstance(value, dict) or not isinstance(value.get("slot_id"), str) or not isinstance(value.get("text"), str):
            findings.append({"severity": "warning", "kind": "invalid_slot", "message": "不正な slot を無視しました"})
            continue
        slot_id, text = value["slot_id"], value["text"].strip()
        if slot_id not in slot_ids:
            findings.append({"severity": "warning", "kind": "unknown_slot", "slot_id": slot_id, "message": "未知の slot を無視しました"})
        elif slot_id in prose:
            findings.append({"severity": "warning", "kind": "duplicate_slot", "slot_id": slot_id, "message": "重複 slot の先頭だけを使いました"})
        elif text:
            prose[slot_id] = text
    return prose


def _without_fixed_sentences(text, fixed):
    pieces = re.split(r"(?<=[。！？])|\n+", text)
    def echo_key(part):
        return re.sub(r"^(?:その後|その結果|そして|最後に|やがて|ついに)[、，,]?\s*", "", part.strip())

    return "".join(part.strip() for part in pieces if echo_key(part) not in fixed).strip()


def inspect_story_v2(raw_text, material, plan, kind):
    """Assemble optional model prose with mandatory source-order fact anchors."""
    _kind(kind)
    verify_material(material)
    packet = _packet(material, plan)
    findings = []
    slot_ids = {slot["slot_id"] for slot in packet["slots"]}
    prose = _model_slots(raw_text, slot_ids, findings)
    fixed = {part for slot in packet["slots"] if slot["anchor"]
             for part in re.split(r"(?<=[。！？])", slot["anchor"]) if part}
    paragraphs, sources, coverage = [], [], []
    for slot in packet["slots"]:
        if not slot["render"]:
            continue
        parts = []
        if slot["anchor"]:
            supplemental = _without_fixed_sentences(prose.get(slot["slot_id"], ""), fixed)
            if supplemental:
                parts.append(supplemental)
            parts.append(slot["anchor"])
        else:
            findings.append({"severity": "warning", "kind": "unanchored_event", "event_id": slot["event_id"],
                             "message": slot["review_reason"]})
            continue
        paragraphs.append("\n".join(parts))
        sources.append({"paragraph": len(sources) + 1, "event_ids": [slot["event_id"]], "embellishments": []})
        coverage.append(slot["event_id"])
    text = "\n\n".join(paragraphs) or None
    unrendered_required = [slot["event_id"] for slot in packet["slots"]
                           if slot["required"] and slot["event_id"] not in coverage]
    if unrendered_required:
        findings.append({"severity": "error", "kind": "missing_required_anchor", "event_ids": unrendered_required,
                         "message": "必須イベントに安全な固定文を作成できません。原ログを確認してください。"})
    record = {"schema_version": 1, "validator_version": VERSION,
              "raw_text_sha256": sha256(raw_text.encode("utf-8")),
              "reference_encoding": "story_v2_slots", "text_sha256": sha256(text.encode("utf-8")) if text is not None else None,
              "material_sha256": material["content_sha256"], "plan_sha256": sha256(canonical(plan)),
              "structural_status": "failed" if any(f["severity"] == "error" for f in findings) else "passed",
              "findings": findings, "required_event_coverage": sorted(set(plan["required_event_ids"]) & set(coverage)),
              "semantic_review_status": "not_reviewed", "human_adoption": "pending",
              "limitation": "固定文は記録の転記であり、モデル補足の意味的正確さは人が確認します。"}
    return {"text": text, "paragraph_sources": sources, "validation": record}
