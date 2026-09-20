"""LLM-authored world-expansion proposals (WB-WORLDGROW-001, stage 3b).

v1 only handles an `investigate` trigger: the zone has content missing from
its investigate table, which a data-only patch (item/fact `sources`) can fix
directly. A verb like `observe` whiffs for reasons a patch can't touch (it
depends on who's present, not on what's investigable), so build_prompt
refuses anything else.
"""
from __future__ import annotations

import json
from typing import Any

from gapengine.world_patch import patch_id_for

MAX_PROMPT_CHARS = 12000

_INTRO = """あなたは物語シミュレーションの世界設定を拡張する設計者です。
この世界では、主人公がある場所で同じ行動を何度も試みているのに、世界の側に応えるものがありません。
その場所を少しだけ豊かにする「拡張パッチ」を1つ提案してください。"""

_RULES_TEMPLATE = """# 拡張のルール
- 足せるのは add.zones / add.items / add.facts / add.daily_events だけです。既存のものは変更も削除もできません。
- zones（最大2）: {{"name","parent","note"}}。parent は既存の場所。新しい場所は parent からしか行けない行き止まりになります。
- items（最大4）: {{"name","sources":[{{"type":"investigate","zone":場所,"count":1,"max":1〜3}}],"lootable":真偽,"keepsake":真偽,"give":{{"receiver_affinity":0〜0.5,"giver_affinity":0〜0.5}},"modifier":{{"id":"item:名前","value":0〜10,"kind":"item","visible":真偽}}}}。name と sources 以外は省略できます。
- facts（最大4）: {{"id","label"(60文字以内),"secrecy":0〜1,"share_min_affinity":-1〜1,"sources":[{{"type":"investigate","zone":場所,"count":1}}]}}。値付きの事実に対する手がかりにしたいときだけ "implies" か "refutes": {{"fact":既存の値付き事実のid,"value":その値のどれか,"confidence":0.5以下}} を足せます。
- daily_events（最大3）: {{"id","label","weight":0より大きく3以下,"stress_delta":-1〜1}}。
- 名前とidは30文字以内。既存の場所・アイテム・事実・人物の名前や、上の世界の説明に出てくる語を含む名前は使えません。
- 結末、目的の品、乗り物、道の通行条件には触れられません。
- 必ず「{zone}」またはその枝の場所をsourcesのzoneにしたitemsかfactsを1つ以上入れてください。
- 物語として意味のあるものにしてください。なぜそれがそこにあるのか、主人公や仲間の選択をどう変えうるかをrationaleに書いてください。"""

_OUTPUT = """# 出力
次の形のJSONだけを、読みやすく複数行で出力してください。説明文やコードフェンスは要りません。
{
  "title": "20文字程度の題",
  "rationale": "200文字以内",
  "add": { "zones": [...], "items": [...], "facts": [...], "daily_events": [...] }
}"""


def _ending_labels(world: dict) -> list[str]:
    target = world.get("target_ending")
    if not isinstance(target, list):
        target = [target] if target else []
    endings = {e.get("id"): e.get("label") for e in (world.get("ending") or []) if isinstance(e, dict)}
    return [str(endings.get(eid, eid)) for eid in target]


def _routes_lines(world: dict) -> list[str]:
    lines = []
    routes = world.get("routes") or {}
    for origin in sorted(routes):
        for route in routes.get(origin) or []:
            if not isinstance(route, dict) or route.get("to") is None:
                continue
            requires = route.get("requires_item")
            suffix = f"（{requires}が必要）" if requires else ""
            lines.append(f"{origin} → {route['to']}{suffix}")
    return lines


def _item_line(item: Any) -> str | None:
    if not isinstance(item, dict) or not item.get("name"):
        return None
    notes = []
    sources = item.get("sources")
    if isinstance(sources, list):
        zones = sorted({s.get("zone") for s in sources if isinstance(s, dict) and s.get("zone")})
        if zones:
            notes.append("、".join(zones) + "で調べると手に入る")
    made_from = item.get("made_from")
    if isinstance(made_from, dict) and made_from:
        notes.append("、".join(str(m) for m in made_from) + "から作る")
    suffix = f"（{'／'.join(notes)}）" if notes else ""
    return f"{item['name']}{suffix}"


def _fact_line(fact: Any) -> str | None:
    if not isinstance(fact, dict) or not fact.get("id"):
        return None
    values = fact.get("values")
    if isinstance(values, list) and values:
        return f"{fact['id']} = " + "／".join(str(v) for v in values) + "のどれか"
    label = fact.get("label")
    return f"{fact['id']}: {label}" if label else str(fact["id"])


def world_brief(world: dict, subject_ids: list[str], *, limit: int | None = None) -> str:
    """A short natural-language digest of `world`, for the prompt's "# 世界"
    section. `limit` caps the zones/items/facts lists (build_prompt's
    fallback when the full brief would blow the character budget)."""
    zones = [z for z in (world.get("zones") or []) if isinstance(z, dict)]
    items = [line for line in (_item_line(i) for i in (world.get("items") or [])) if line]
    facts = [line for line in (_fact_line(f) for f in (world.get("facts") or [])) if line]
    zone_lines = [f"{z.get('name')}: {z.get('note', '')}" for z in zones]
    routes = _routes_lines(world)
    if limit is not None:
        zone_lines, routes, items, facts = zone_lines[:limit], routes[:limit], items[:limit], facts[:limit]

    expansion = world.get("expansion")
    applied = [p.get("title") for p in (expansion.get("patches") or [])] if isinstance(expansion, dict) else []

    lines = [
        "# 世界",
        f"名前: {world.get('name', '')} / 主人公: {world.get('protagonist', '')} / 敵役: {world.get('antagonist', '')}",
        "結末: " + "、".join(_ending_labels(world)),
        "場所: " + "／".join(zone_lines),
        "道: " + "／".join(routes),
        "アイテム: " + "／".join(items),
        "事実: " + "／".join(facts),
        "人物: " + "、".join(subject_ids),
        "既に適用済みの拡張: " + ("、".join(str(t) for t in applied) if applied else "なし"),
    ]
    return "\n".join(lines)


def _demand_section(trigger: dict, zone_verbs: list) -> str:
    zone_verb_text = "、".join(f"{item[0]}×{item[1]}" for item in zone_verbs) if zone_verbs else "記録なし"
    return (
        "# 足りていない場所\n"
        f"「{trigger['zone']}」で主人公は「調べる」を{trigger.get('count')}回行い、"
        f"そのうち{trigger.get('whiffs')}回は何も得られませんでした。\n"
        f"この場所で主人公がよくしている行動: {zone_verb_text}"
    )


def _compose(world: dict, subject_ids: list[str], trigger: dict, zone_verbs: list, *, limit: int | None) -> str:
    parts = [
        _INTRO,
        world_brief(world, subject_ids, limit=limit),
        _demand_section(trigger, zone_verbs),
        _RULES_TEMPLATE.format(zone=trigger["zone"]),
        _OUTPUT,
    ]
    return "\n\n".join(parts)


def build_prompt(world: dict, subject_ids: list[str], trigger: dict, zone_verbs: list) -> str:
    if trigger.get("verb") != "investigate":
        raise ValueError("v1 は investigate の需要だけに対応しています")
    prompt = _compose(world, subject_ids, trigger, zone_verbs, limit=None)
    if len(prompt) > MAX_PROMPT_CHARS:
        prompt = _compose(world, subject_ids, trigger, zone_verbs, limit=20)
    if len(prompt) > MAX_PROMPT_CHARS:
        prompt = prompt[:MAX_PROMPT_CHARS]
    return prompt


def parse_proposal(text: str) -> dict:
    if not isinstance(text, str):
        raise ValueError("応答が文字列ではありません")
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("応答からJSONを取り出せません")
    try:
        value = json.loads(text[start:end + 1])
    except json.JSONDecodeError as error:
        raise ValueError(f"応答がJSONとして解析できません: {error}") from error
    if not isinstance(value, dict) or set(value) != {"title", "rationale", "add"}:
        raise ValueError("応答のキーが title/rationale/add ちょうどではありません")
    if not (isinstance(value["title"], str) and 1 <= len(value["title"]) <= 40):
        raise ValueError("title は1〜40文字で指定してください")
    if not (isinstance(value["rationale"], str) and 1 <= len(value["rationale"]) <= 300):
        raise ValueError("rationale は1〜300文字で指定してください")
    if not isinstance(value["add"], dict):
        raise ValueError("add はオブジェクトで指定してください")
    return value


def make_patch(proposal: dict, *, trigger: dict, parent_rev: list, author: dict) -> dict:
    add = proposal["add"]
    trigger_slim = {k: trigger[k] for k in ("experiment", "zone", "verb", "count", "whiffs") if k in trigger}
    return {
        "id": patch_id_for(add),
        "title": proposal["title"],
        "rationale": proposal["rationale"],
        "parent_rev": list(parent_rev),
        "trigger": trigger_slim,
        "author": author,
        "add": add,
    }


def check_trigger_coverage(add: dict, trigger: dict) -> list[str]:
    """Extra gate (not part of validate_patch's generic schema check): at
    least one added item/fact must source from the trigger zone or a new
    zone branching directly off it -- otherwise the patch doesn't actually
    address the whiff it was proposed for."""
    zone = trigger.get("zone")
    branch_zones = {
        z.get("name") for z in (add.get("zones") or [])
        if isinstance(z, dict) and z.get("parent") == zone
    }
    valid_zones = {zone} | branch_zones
    for bucket in ("items", "facts"):
        for entry in (add.get(bucket) or []):
            if not isinstance(entry, dict):
                continue
            for source in (entry.get("sources") or []):
                if isinstance(source, dict) and source.get("zone") in valid_zones:
                    return []
    return ["きっかけの場所に調べて得られるものが足されていません"]
