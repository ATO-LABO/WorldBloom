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
- 名前とidは30文字以内。新しい名前は、この世界の説明文（上の一覧を含む world.yaml 全体）のどこかに含まれる文字列であってはいけません（既存の語をそのまま名前にしない）。
- modifier.value の合計は1つの提案で10までです。
- 結末、目的の品、乗り物、道の通行条件には触れられません。
- 必ず「{zone}」またはその枝の場所をsourcesのzoneにしたitemsかfactsを1つ以上入れてください。
- 物語として意味のあるものにしてください。なぜそれがそこにあるのか、主人公や仲間の選択をどう変えうるかをrationaleに書いてください。"""

_OUTPUT = """# 出力
次の形のJSONだけを、読みやすく複数行で出力してください。説明文やコードフェンスは要りません。
{
  "title": "20文字程度の題",
  "rationale": "200文字以内",
  "add": { "zones": [...], "items": [...], "facts": [...], "daily_events": [...] }
}

例（この世界とは無関係な架空の設定です。この例に出てくる名前「灯台守の記録」「色あせた航海日誌」「岬」「灯台守の失踪」は真似しないでください）:
{
  "title": "灯台守の記録",
  "rationale": "岬に眠る記録が、主人公の判断に新しい手がかりを与える。",
  "add": {
    "zones": [],
    "items": [
      {"name": "色あせた航海日誌", "sources": [{"type": "investigate", "zone": "岬", "count": 1, "max": 1}]}
    ],
    "facts": [
      {"id": "灯台守の失踪", "label": "先代の灯台守が三年前に姿を消したらしい",
       "sources": [{"type": "investigate", "zone": "岬", "count": 1}]}
    ]
  }
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


def _capped(text: str, cap: int | None) -> str:
    text = str(text)
    if cap is None or len(text) <= cap:
        return text
    return text[:cap] + "…"


def _item_line(item: Any, *, cap: int | None = None) -> str | None:
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
    note_text = _capped("／".join(notes), cap) if notes else ""
    suffix = f"（{note_text}）" if note_text else ""
    return f"{item['name']}{suffix}"


def _fact_line(fact: Any, *, cap: int | None = None) -> str | None:
    if not isinstance(fact, dict) or not fact.get("id"):
        return None
    values = fact.get("values")
    if isinstance(values, list) and values:
        return f"{fact['id']} = " + _capped("／".join(str(v) for v in values) + "のどれか", cap)
    label = fact.get("label")
    return f"{fact['id']}: {_capped(label, cap)}" if label else str(fact["id"])


def world_brief(world: dict, subject_ids: list[str], *, cap: int | None = None,
                 n_zones: int | None = None, n_items: int | None = None,
                 n_facts: int | None = None) -> str:
    """A short natural-language digest of `world`, for the prompt's "# 世界"
    section. `cap` truncates each zone note / item note / fact label to that
    many characters; `n_zones`/`n_items`/`n_facts` keep only the first that
    many zones/items/facts, appending "（ほかN件）" for the rest. build_prompt
    uses these to fit the world into MAX_PROMPT_CHARS without cutting off the
    rules/output sections that come after this brief."""
    all_zones = [z for z in (world.get("zones") or []) if isinstance(z, dict)]
    all_items = [line for line in (_item_line(i, cap=cap) for i in (world.get("items") or [])) if line]
    all_facts = [line for line in (_fact_line(f, cap=cap) for f in (world.get("facts") or [])) if line]

    zones = all_zones if n_zones is None else all_zones[:n_zones]
    items = all_items if n_items is None else all_items[:n_items]
    facts = all_facts if n_facts is None else all_facts[:n_facts]

    zone_lines = [f"{z.get('name')}: {_capped(z.get('note', ''), cap)}" for z in zones]
    if n_zones is not None and len(all_zones) > n_zones:
        zone_lines.append(f"（ほか{len(all_zones) - n_zones}件）")
    if n_items is not None and len(all_items) > n_items:
        items = items + [f"（ほか{len(all_items) - n_items}件）"]
    if n_facts is not None and len(all_facts) > n_facts:
        facts = facts + [f"（ほか{len(all_facts) - n_facts}件）"]

    routes = _routes_lines(world)

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


def _assemble(brief: str, trigger: dict, zone_verbs: list) -> str:
    return "\n\n".join([_INTRO, brief, _demand_section(trigger, zone_verbs),
                        _RULES_TEMPLATE.format(zone=trigger["zone"]), _OUTPUT])


def build_prompt(world: dict, subject_ids: list[str], trigger: dict, zone_verbs: list) -> str:
    """The rules/output sections a reader depends on must never be cut off, so
    the fixed parts (intro, demand section, rules, output) get their budget
    first and only the "# 世界" brief shrinks to make room: first each note/
    label is capped to 40 chars, then -- if that's still not enough -- facts,
    then items, then zones are dropped from the tail one at a time (each
    shedding round adds a "（ほかN件）" marker instead of silently vanishing)."""
    if trigger.get("verb") != "investigate":
        raise ValueError("v1 は investigate の需要だけに対応しています")

    fixed_chars = len(_assemble("", trigger, zone_verbs))
    budget = max(MAX_PROMPT_CHARS - fixed_chars, 0)

    brief = world_brief(world, subject_ids)
    if len(brief) > budget:
        brief = world_brief(world, subject_ids, cap=40)

    # ponytail: rebuilds the whole brief on every step it needs to shed an
    # element; real worlds have a handful of zones/items/facts so this stays
    # fast, revisit with a smarter budget split if a world ever has hundreds.
    n_facts = len([f for f in (world.get("facts") or []) if isinstance(f, dict) and f.get("id")])
    while len(brief) > budget and n_facts > 0:
        n_facts -= 1
        brief = world_brief(world, subject_ids, cap=40, n_facts=n_facts)

    n_items = len([i for i in (world.get("items") or []) if isinstance(i, dict) and i.get("name")])
    while len(brief) > budget and n_items > 0:
        n_items -= 1
        brief = world_brief(world, subject_ids, cap=40, n_facts=n_facts, n_items=n_items)

    n_zones = len([z for z in (world.get("zones") or []) if isinstance(z, dict)])
    while len(brief) > budget and n_zones > 0:
        n_zones -= 1
        brief = world_brief(world, subject_ids, cap=40, n_facts=n_facts, n_items=n_items, n_zones=n_zones)

    prompt = _assemble(brief, trigger, zone_verbs)
    if len(prompt) > MAX_PROMPT_CHARS:
        # Last-resort safety net -- the loops above already fit `brief` to
        # `budget`, so this only bites if the fixed parts themselves (e.g. an
        # enormous zone_verbs list) already overran MAX_PROMPT_CHARS.
        brief = brief[:max(len(brief) - (len(prompt) - MAX_PROMPT_CHARS), 0)]
        prompt = _assemble(brief, trigger, zone_verbs)
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
    address the whiff it was proposed for. Never raises: `add`/`trigger` may
    be whatever shape an LLM (or a caller re-running check on a hand-edited
    proposal) handed us."""
    no_coverage = ["きっかけの場所に調べて得られるものが足されていません"]
    if not isinstance(add, dict):
        return no_coverage
    zone = trigger.get("zone")
    zones_raw = add.get("zones")
    branch_zones = {
        z.get("name") for z in (zones_raw if isinstance(zones_raw, list) else [])
        if isinstance(z, dict) and isinstance(z.get("name"), str) and z.get("parent") == zone
    }
    valid_zones = ({zone} if isinstance(zone, str) else set()) | branch_zones
    for bucket in ("items", "facts"):
        entries = add.get(bucket)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            sources = entry.get("sources")
            if not isinstance(sources, list):
                continue
            for source in sources:
                if (isinstance(source, dict) and isinstance(source.get("zone"), str)
                        and source["zone"] in valid_zones):
                    return []
    return no_coverage
