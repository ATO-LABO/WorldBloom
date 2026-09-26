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

from gapengine.world_patch import (_valued_facts, addition_caps, innocent_tokens, lottery_facts,
                                    patch_id_for, trigger_is_proposable)

MAX_PROMPT_CHARS = 12000

_INTRO = """あなたは物語シミュレーションの世界設定を拡張する設計者です。
この世界では、主人公がある場所で同じ行動を何度も試みているのに、世界の側に応えるものがありません。
その場所を少しだけ豊かにする「拡張パッチ」を1つ提案してください。"""

_RULES_TEMPLATE = """# 拡張のルール
- 足せるのは add.zones / add.items / add.facts だけです。既存のものは変更も削除もできません。1つの仕組みに絞ってください。
- zones（最大1）: {{"name","parent","note"}}。parent は既存の場所で、足した場所は parent と同じ入場条件を持つ、その場所の一部になります。note は世界の中の描写だけを書いてください。「枝」「親」「パッチ」などの設計上の言葉や、この世界に無い場所・物の名前を書いてはいけません。facts の label も同じです。
- items（最大2）: {{"name","sources":[{{"type":"investigate","zone":場所,"count":1,"max":1〜3}}],"give":{{"receiver_affinity":0〜0.5,"giver_affinity":0〜0.5}}}}。sources.max は、1人がその場所で何個まで手に入れられるかです。max:1 は1回取ったら二度と出ず、その後は元どおりの空振りに戻ります。{count_phrase}、1つしか無いことに意味がある品でなければ max は2以上にしてください。{give_rule}
- items に足せる任意の項目（渡すだけの品にしないための選択肢です。要るものだけ使ってください）:
  "keepsake": true … 手放さない品（渡せず、取引にも差し出さない）。give とは併記できません。
  "lootable": true … 持ち主が倒れたとき、相手に奪われうる品。
  "modifier": {{"id":"item:アイテム名","value":0〜10,"kind":"item","visible":trueかfalse}} … 持っていると対決での強さに value が足される。visible が true なら相手から見える。value の合計は1つの提案で10まで。
  "made_from": {{素材名:1〜3}} … 同じ提案で足す別のアイテムを素材にして作る品（sources の代わりに書ける）。"craft_zone": 場所 で作れる場所を限定できる。give とは併記できません。
  "requires": {{"knowledge": 事実id}} … その事実を知っている人物だけが作れる品（made_from と一緒に使う）。
- facts（最大2）: {{"id","label"(60文字以内),"secrecy":0〜1,"share_min_affinity":-1〜1,"sources":[{{"type":"investigate","zone":場所,"count":1}}]}}。事実は一度知ると二度と得られません（countは1固定、maxはありません）。secrecy は必ず明示してください。0 は誰にでも話す噂、0.2〜0.4 は相手を選んで話すこと、0.8 以上はほぼ口外しない秘密です。省略すると0になり、最も広まりやすい事実になります。share_min_affinity は、この好感度以上の相手にしか話さないという下限です（省略時0）。{implies_rule}
- {budget_rule}
- 名前とidは30文字以内。新しい名前は、この世界の説明文（上の一覧を含む world.yaml 全体）のどこかに含まれる文字列であってはいけません（既存の語をそのまま名前にしない）。アイテムの name と事実の id にも、互いに違う名前を付けてください。
- 結末、目的の品、乗り物、道の通行条件には触れられません。
{coverage_rule}
- rationale はちょうど3文で書いてください。1文目「新たに何を選べるか」、2文目「何を失う可能性があるか」、3文目「既存のどの関係へ作用するか」。2文目と3文目には、上の一覧にある人物名・事実id・アイテム名のいずれかを必ず名指しで入れてください。提案に入れていない効果を書いてはいけません。"""

_OUTPUT = """# 出力
次の形のJSONだけを、読みやすく複数行で出力してください。説明文やコードフェンスは要りません。
{
  "title": "20文字程度の題",
  "rationale": "3文、300文字以内",
  "add": { "zones": [...], "items": [...], "facts": [...] }
}

例（この世界とは無関係な架空の設定です。この例に出てくる名前「灯台守の記録」「色あせた航海日誌」「岬」「灯台守の失踪」「灯を消した者」「船主」「見習い」は真似しないでください。形も真似せず、この世界に合う項目を選んでください）:
{
  "title": "灯台守の記録",
  "rationale": "岬で航海日誌を拾い、見習いに渡すか手元に置くかを選べる。日誌を渡すと、灯台守の失踪を先に知った見習いが船主を疑い、主人公の言葉を聞かなくなるおそれがある。灯を消した者についての確信が弱い向きに一つ増え、見習いと船主の関係に作用する。",
  "add": {
    "zones": [],
    "items": [
      {"name": "色あせた航海日誌", "give": {"receiver_affinity": 0.2, "giver_affinity": 0.05}, "sources": [{"type": "investigate", "zone": "岬", "count": 1, "max": 2}]}
    ],
    "facts": [
      {"id": "灯台守の失踪", "label": "先代の灯台守が三年前に姿を消したらしい", "secrecy": 0.25,
       "sources": [{"type": "investigate", "zone": "岬", "count": 1}],
       "implies": {"fact": "灯を消した者", "value": "船主", "confidence": 0.2}}
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


def _demand_section_whiff(trigger: dict, zone_verbs: list) -> str:
    zone_verb_text = "、".join(f"{item[0]}×{item[1]}" for item in zone_verbs) if zone_verbs else "記録なし"
    return (
        "# 足りていない場所\n"
        f"「{trigger['zone']}」で主人公は「調べる」を{trigger.get('count')}回行い、"
        f"そのうち{trigger.get('whiffs')}回は何も得られませんでした。\n"
        f"この場所で主人公がよくしている行動: {zone_verb_text}"
    )


def _pair_text(pairs) -> str:
    return "、".join(f"{name}×{count}" for name, count in pairs) if pairs else "不明"


# WB-WORLDGROW-002 S2: judgment call -- world_demand.py's collect() (S1)
# tracks ignorance only as a per-zone count/share (no per-milestone
# breakdown), so unlike the original stage-2 plan text there's no "top 3
# milestones the protagonist needed" to show. Falls back to the same
# zone/count/share framing whiff's own demand section uses.
def _demand_section_ignorance(trigger: dict, zone_verbs: list) -> str:
    zone_verb_text = "、".join(f"{item[0]}×{item[1]}" for item in zone_verbs) if zone_verbs else "記録なし"
    share_pct = (trigger.get("share") or 0.0) * 100
    return (
        "# 足りていない場所\n"
        f"「{trigger.get('zone')}」で主人公が『手探り』（何をすべきか分からず調べ回る寄り道）を"
        f"{trigger.get('count')}回（道筋付き決定の{share_pct:.0f}%）繰り返しています。\n"
        f"その場所で調べると得られる手がかり（factとitemのinvestigateのsource）を足してください。\n"
        f"この場所で主人公がよくしている行動: {zone_verb_text}"
    )


def _blocked_target(requirement: str) -> tuple[str, str]:
    """(readable target, requirement kind) for a blocked trigger's
    `requirement` string ("has_item:<品>"/"knows:<事実>"/"reach:<区域>", per
    gapengine/route.py's `_blocked_on`)."""
    for prefix, kind in (("has_item:", "item"), ("knows:", "fact"), ("reach:", "reach")):
        if requirement.startswith(prefix):
            return requirement[len(prefix):], kind
    return requirement, "other"


def _demand_section_blocked(trigger: dict) -> str:
    requirement = trigger.get("requirement") or ""
    target, kind = _blocked_target(requirement)
    stuck_text = _pair_text(trigger.get("stuck_zones"))
    count, runs = trigger.get("count"), trigger.get("runs")
    if kind == "reach":
        body = (
            f"「{target}」に行く手段が主人公にありません。"
            f"主に{stuck_text}で、結末への計画が立たなくなっています（{runs}本のランで{count}回）。\n"
            f"経路（routes）を新設するのではなく、「{target}」が果たしていた役割を代わりに果たす"
            "入手手段を、主人公が到達できる場所に足してください。"
        )
    else:
        source_text = _pair_text(trigger.get("source_zones"))
        held_text = _pair_text(trigger.get("held_by")) if trigger.get("held_by") else "誰も持っていません"
        body = (
            f"「{target}」を手に入れる手段が、主人公が行ける場所にありません"
            f"（入手できる場所: {source_text}＝今は行けない／持っているのは{held_text}）。"
            f"主に{stuck_text}で、結末への計画が立たなくなっています（{runs}本のランで{count}回）。\n"
            "主人公が到達できる場所に、別の入手手段を足してください。"
        )
    return "# 足りていない場所\n" + body


def _demand_section(trigger: dict, zone_verbs: list) -> str:
    kind = trigger.get("kind", "whiff")
    if kind == "ignorance":
        return _demand_section_ignorance(trigger, zone_verbs)
    if kind == "blocked":
        return _demand_section_blocked(trigger)
    return _demand_section_whiff(trigger, zone_verbs)


def _budget_rule(world: dict) -> str:
    left = {key: max(cap - used, 0) for key, (cap, used) in addition_caps(world).items()}
    return (f"この世界に今回足せる数は、場所{min(left['zones'], 1)}・アイテム{min(left['items'], 2)}・"
            f"事実{min(left['facts'], 2)}までです（これを超えると不採用になります）。パッチ総数8以下。")


def _give_rule(give_available: bool) -> str:
    if not give_available:
        return "この世界には品を渡す行動がありません。give は書かないでください。"
    return ("give は渡したときの好感度の変化で、両方の値を必ず明示してください。省略時は受け手0.2・渡し手0.05として数えます。"
            "（受け手＋渡し手）×その品の sources の max の合計、を全追加アイテムで足して1パッチ0.6・累積1.2以下"
            "（sources を2つ書けば max も2つ分数えます）。"
            "たくさん拾える品（max が2以上）ほど give の値を小さくしてください"
            "（例: 受け手0.1・渡し手0 で max 3 なら 0.3）。keepsake と made_from の品は渡せないので数えません。")


def _implies_rule(world: dict) -> str:
    valued = _valued_facts(world)
    if not valued:
        return "この世界には implies を付けられる事実がありません。implies は書かないでください。"
    lottery = lottery_facts(world)
    fixed = {fact: values for fact, values in valued.items() if fact not in lottery}

    # The schema line is unconditional: a world with only lottery facts
    # (detective) would otherwise never be told the keys or the 0.3 cap.
    sentences = ['事実には implies: {"fact":既存id,"value":値,"confidence":0より大きく0.3以下} を付けられます。'
                 "少なくとも1つの事実に付けてください。implies の無い事実は、得ても誰の考えも変わりません。"]
    if fixed:
        targets = "、".join(f"{fact}（{'／'.join(sorted(values))}）" for fact, values in sorted(fixed.items()))
        sentences.append(f"value を名前で書く相手は {targets} です。")
    if lottery:
        lottery_targets = "、".join(
            f"{fact}（{'／'.join(sorted(values))}）" for fact, values in sorted(lottery.items()))
        tokens = "、".join(
            f"{fact}なら" + "・".join(f'"{t}"' for t in innocent_tokens(values))
            for fact, values in sorted(lottery.items()) if innocent_tokens(values))
        candidate_names = "、".join(sorted({c for values in lottery.values() for c in values}))
        innocent = f"か、無実の候補を指す当てにならない手がかり（{tokens}）" if tokens else ""
        example = next(iter(sorted(lottery)))
        sentences.append(
            f"次の事実は真値が毎回くじで決まります: {lottery_targets}。"
            f'これらを相手にする implies の value は名前ではなく "$truth"（本物を指す手がかり）{innocent}で、'
            "この綴りのまま半角で書いてください"
            f'（例: "implies": {{"fact":"{example}","value":"$truth","confidence":0.25}}。下の出力例のように名前では書けません）。'
            f"その事実の id と label には候補の名前（{candidate_names}）を書かないでください（誰が本物かは回ごとに変わります）。")
    sentences.append("implies の fact に書けるのは、ここに挙げた事実だけです（それ以外の事実や証拠の id は書けません）。対象ごとの累積は0.6以下。")
    return "".join(sentences)


_WHIFF_COVERAGE_RULE = (
    "- 必ず「{zone}」そのものをsourcesのzoneにしたitemsかfactsを1つ以上入れてください。足した場所に置くだけでは、"
    "「{zone}」で調べたときの空振りは1回も減りません。\n"
    "- 場所を足さずに「{zone}」の中身を増やすのが基本です。zonesを足すのは、その場所でしか成り立たない中身があ"
    "るときだけにしてください。足した場合は、その場所をsourcesのzoneにしたitemsかfactsを必ず1つ以上そこに置い"
    "てください（中身の無い場所は、行っても必ず空振りする場所が増えるだけです）。"
)

_BLOCKED_COVERAGE_RULE = (
    "- 必ず、主人公が到達できる場所（{stuck_zones}、またはそこから道でつながる場所）のいずれかをsourcesのzoneに"
    "したitemsかfactsを1つ以上入れ、「{target}」の入手手段にしてください。\n"
    "- 場所を足さずに既存の場所の中身を増やすのが基本です。zonesを足すのは、その場所でしか成り立たない中身があ"
    "るときだけにしてください。足した場合は、その場所をsourcesのzoneにしたitemsかfactsを必ず1つ以上そこに置い"
    "てください（中身の無い場所は、行っても必ず空振りする場所が増えるだけです）。"
)


def _coverage_rule(trigger: dict) -> str:
    if trigger.get("kind") == "blocked":
        target, _rkind = _blocked_target(trigger.get("requirement") or "")
        return _BLOCKED_COVERAGE_RULE.format(stuck_zones=_pair_text(trigger.get("stuck_zones")), target=target)
    # whiff and ignorance both key off a single trigger zone -- byte-identical
    # to the original (pre-S2, whiff-only) rule text.
    return _WHIFF_COVERAGE_RULE.format(zone=trigger.get("zone"))


def _count_phrase(trigger: dict) -> str:
    if trigger.get("kind", "whiff") == "whiff":
        # Byte-identical to the pre-S2 (whiff-only) wording.
        return f"今回の空振りは{trigger.get('whiffs')}回なので"
    return f"今回の件数は{trigger.get('count')}回なので"


def _assemble(brief: str, trigger: dict, zone_verbs: list, world: dict, *, give_available: bool = True) -> str:
    rules = _RULES_TEMPLATE.format(count_phrase=_count_phrase(trigger),
                                   budget_rule=_budget_rule(world), implies_rule=_implies_rule(world),
                                   give_rule=_give_rule(give_available), coverage_rule=_coverage_rule(trigger))
    return "\n\n".join([_INTRO, brief, _demand_section(trigger, zone_verbs), rules, _OUTPUT])


def build_prompt(world: dict, subject_ids: list[str], trigger: dict, zone_verbs: list, *,
                  give_available: bool = True) -> str:
    """The rules/output sections a reader depends on must never be cut off, so
    the fixed parts (intro, demand section, rules, output) get their budget
    first and only the "# 世界" brief shrinks to make room: first each note/
    label is capped to 40 chars, then -- if that's still not enough -- facts,
    then items, then zones are dropped from the tail one at a time (each
    shedding round adds a "（ほかN件）" marker instead of silently vanishing).
    `give_available` should be scripts/world_patch.py's `_give_available()`
    result for this world's subjects -- give never fires with no give_item
    verb, so the prompt must not ask for it."""
    if not trigger_is_proposable(trigger):
        raise ValueError("v1 は investigate の需要だけに対応しています")

    fixed_chars = len(_assemble("", trigger, zone_verbs, world, give_available=give_available))
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

    prompt = _assemble(brief, trigger, zone_verbs, world, give_available=give_available)
    if len(prompt) > MAX_PROMPT_CHARS:
        # Last-resort safety net -- the loops above already fit `brief` to
        # `budget`, so this only bites if the fixed parts themselves (e.g. an
        # enormous zone_verbs list) already overran MAX_PROMPT_CHARS.
        brief = brief[:max(len(brief) - (len(prompt) - MAX_PROMPT_CHARS), 0)]
        prompt = _assemble(brief, trigger, zone_verbs, world, give_available=give_available)
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


# WB-WORLDGROW-002 S2: patch["trigger"] (kept on the patch itself, so a later
# `check` can re-run check_trigger_coverage without the original
# world_demand.json report) -- keyed by kind, same reasoning as
# gapengine/world_patch.py's EXPANSION_TRIGGER_KEYS but a separate, richer
# slimming (this one needs enough fields for coverage to be re-checkable,
# e.g. blocked's stuck_zones). whiff keeps its exact pre-S2 field set.
PATCH_TRIGGER_KEYS = {
    "whiff": ("experiment", "zone", "verb", "count", "whiffs"),
    "ignorance": ("experiment", "kind", "zone", "count"),
    "blocked": ("experiment", "kind", "requirement", "count", "stuck_zones"),
}


def make_patch(proposal: dict, *, trigger: dict, parent_digest: str, author: dict) -> dict:
    add = proposal["add"]
    kind = trigger.get("kind", "whiff") if isinstance(trigger, dict) else "whiff"
    keys = PATCH_TRIGGER_KEYS.get(kind, PATCH_TRIGGER_KEYS["whiff"])
    trigger_slim = {k: trigger[k] for k in keys if k in trigger}
    return {
        "id": patch_id_for(add),
        "title": proposal["title"],
        "rationale": proposal["rationale"],
        "parent_digest": parent_digest,
        "trigger": trigger_slim,
        "author": author,
        "add": add,
    }


def _sourced_zones(add: dict) -> set:
    found: set = set()
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
                if isinstance(source, dict) and isinstance(source.get("zone"), str):
                    found.add(source["zone"])
    return found


def _zone_children(world: dict | None, add: dict, zone: str) -> set:
    """Existing zones (from `world`) plus this patch's own new zones whose
    `parent` is `zone` -- WB-WORLDGROW-002 S2's "ignorance" coverage allows a
    source in the trigger zone's immediate children too, not only the zone
    itself (§2's "そのゾーンの子ゾーン")."""
    children = set()
    if isinstance(world, dict):
        children |= {z.get("name") for z in (world.get("zones") or [])
                     if isinstance(z, dict) and z.get("parent") == zone and isinstance(z.get("name"), str)}
    zones_raw = add.get("zones") if isinstance(add, dict) else None
    if isinstance(zones_raw, list):
        children |= {z.get("name") for z in zones_raw
                     if isinstance(z, dict) and z.get("parent") == zone and isinstance(z.get("name"), str)}
    return children


def _check_blocked_coverage(add: dict, trigger: dict, reachable_zones: set | None) -> list[str]:
    requirement = trigger.get("requirement")
    if not isinstance(requirement, str) or not requirement:
        return ["きっかけの要件が不明です"]
    target, kind = _blocked_target(requirement)
    if reachable_zones is None:
        # No engine/world available to compute real reachability (e.g. a
        # unit test exercising this function directly) -- fall back to just
        # the trigger's own stuck zones, never treating an unrelated zone as
        # reachable by default.
        reachable_zones = {z for z, _n in (trigger.get("stuck_zones") or []) if isinstance(z, str)}

    def _sourced_from_reachable(entries, key, name) -> bool:
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict) or entry.get(key) != name:
                continue
            for source in entry.get("sources") or []:
                if isinstance(source, dict) and source.get("zone") in reachable_zones:
                    return True
        return False

    if kind == "item":
        ok = _sourced_from_reachable(add.get("items"), "name", target)
    elif kind == "fact":
        ok = _sourced_from_reachable(add.get("facts"), "id", target)
    else:
        # "reach:<区域>"/unrecognized: can't mechanically verify the added
        # source "fills the same role" as the unreachable zone would have --
        # judgment call (WB-WORLDGROW-002 S2): require at least one added
        # source placed somewhere the protagonist can actually reach.
        ok = bool(_sourced_zones(add) & reachable_zones)
    return [] if ok else [f"「{target}」を主人公が到達できる場所に足す入手手段がありません"]


def check_trigger_coverage(add: dict, trigger: dict, *, world: dict | None = None,
                            reachable_zones: set | None = None) -> list[str]:
    """Extra gate (not part of validate_patch's generic schema check).
    Universal (every kind): A2 -- every zone add.zones adds must itself have
    at least one added item/fact sourcing from it (an empty added zone is
    just a new place to whiff/get-lost in).
    Kind-specific (A1): whiff/ignorance need a source in the trigger zone
    itself (ignorance also accepts one of its immediate children, via
    `world`); blocked needs a source for its `requirement` placed somewhere
    the protagonist can reach (`reachable_zones`, expected to be computed by
    a caller with engine/world access -- see gapengine/world_patch_contract.
    reachable_zones_from; falls back to the trigger's own stuck zones when
    not given, e.g. a plain unit test).
    Never raises: `add`/`trigger` may be whatever shape an LLM (or a caller
    re-running check on a hand-edited proposal) handed us."""
    if not isinstance(add, dict):
        return ["きっかけの場所そのものに調べて得られるものが足されていません"]
    if not isinstance(trigger, dict):
        trigger = {}
    covered = _sourced_zones(add)
    violations: list[str] = []
    kind = trigger.get("kind", "whiff")
    if kind == "blocked":
        violations += _check_blocked_coverage(add, trigger, reachable_zones)
    else:
        zone = trigger.get("zone")
        allowed = {zone} if isinstance(zone, str) else set()
        if kind == "ignorance" and isinstance(zone, str):
            allowed |= _zone_children(world, add, zone)
        if not covered & allowed:
            violations.append("きっかけの場所そのものに調べて得られるものが足されていません")
    zones_raw = add.get("zones")
    for entry in zones_raw if isinstance(zones_raw, list) else []:
        name = entry.get("name") if isinstance(entry, dict) else None
        if isinstance(name, str) and name not in covered:
            violations.append(f"足した場所に調べて得られるものがありません: '{name}'")
    return violations


def check_proposal_rules(add: dict, *, give_available: bool = True) -> list[str]:
    """Extra proposal-time-only gate (not part of validate_patch's generic
    schema check):
    - every added fact must set secrecy explicitly -- an omitted secrecy
      silently defaults to 0, the most shareable value, so the model must
      choose it rather than fall into it.
    - a keepsake item (never handed over -- see engine/actions.py's
      _give_candidates) cannot also carry give.
    - a made_from item (a crafted recipe -- engine/world.py's `recipes`,
      also excluded from _give_candidates) cannot also carry give.
    - when this world has no give_item verb on any subject, give can never
      fire, so a proposal must not spend its give budget on it either.
    Never raises: `add` may be whatever shape an LLM handed us."""
    violations: list[str] = []
    if not isinstance(add, dict):
        return violations
    facts = add.get("facts")
    if isinstance(facts, list):
        for fact in facts:
            if isinstance(fact, dict) and fact.get("secrecy") is None:
                violations.append(f"secrecy を明示してください: '{fact.get('id')}'")
    items = add.get("items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            name, has_give = item.get("name"), "give" in item
            made_from = item.get("made_from")
            has_made_from = isinstance(made_from, dict) and bool(made_from)
            if item.get("keepsake") and has_give:
                violations.append(f"keepsake の品に give は書けません（手放さない品は渡せません）: '{name}'")
            if has_made_from and has_give:
                violations.append(f"作る品（made_from）に give は書けません（作った品は渡せません）: '{name}'")
            if not give_available and has_give:
                violations.append(f"この世界には品を渡す行動が無いので give は書けません: '{name}'")
    return violations
