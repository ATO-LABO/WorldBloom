"""Deterministic extraction and natural-language rendering of notable turns."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


SPECIAL_PRIORITY = {
    "ending": 100,
    "betrayal": 90,
    "downed": 85,
    "revived": 84,
    "exposure": 80,
    "payoff": 75,
    "planted": 70,
    "ally_gained": 65,
    "concede": 60,
    "threshold_crossed": 55,
}

VERB_LABELS = {
    "ally_gained": "仲間になった",
    "betrayal": "誓いを破った",
    "concede": "譲歩した",
    "confront": "問い詰めた",
    "craft": "作った",
    "daily_event": "日々の出来事が起きた",
    "disguise": "変装した",
    "donate": "寄付した",
    "downed": "倒れた",
    "ending": "結末に到達した",
    "exposure": "正体や秘密が露見した",
    "fight": "戦った",
    "give_item": "品物を渡した",
    "grand_gesture": "大きな代償を伴う行動に出た",
    "guard": "守った",
    "investigate": "調べた",
    "mislead": "誤った情報へ誘導した",
    "move": "移動した",
    "negotiate": "交渉した",
    "neutralize": "相手の力を無効化した",
    "observe": "観察した",
    "payoff": "伏線が回収された",
    "persuade": "説得した",
    "plant": "伏線となる行動を選んだ",
    "planted": "伏線が設置された",
    "pledge": "誓いを交わした",
    "rescue": "救助した",
    "rest": "休んだ",
    "revived": "立ち直った",
    "sabotage": "妨害した",
    "sacrifice": "何かを犠牲にした",
    "scheduled_event": "予定された出来事が起きた",
    "share_knowledge": "知識を伝えた",
    "threshold_crossed": "物語の節目を越えた",
    "train": "鍛えた",
    "trial": "試練に挑んだ",
    "withdraw": "退いた",
}

RESULT_LABELS = {
    "applied": "出来事が生じた",
    "crafted": "作成に成功した",
    "expired": "制限時間を迎えた",
    "failed": "失敗した",
    "ignored": "効果は生じなかった",
    "invalid": "実行条件を満たさなかった",
    "lost": "敗れた",
    "moved": "移動した",
    "neutralized": "無効化に成功した",
    "observed": "観察に成功した",
    "planted": "伏線を設置した",
    "revived": "復帰した",
    "shared": "伝達に成功した",
    "won": "勝った",
}


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _l1(first: Sequence[Any], second: Sequence[Any]) -> float:
    return round(
        sum(
            abs(float(left) - float(right))
            for left, right in zip(first, second, strict=True)
        ),
        12,
    )


def _turn_magnitudes(
    rows: Sequence[Mapping[str, Any]],
) -> dict[int, float]:
    snapshots = [
        row
        for row in rows
        if row.get("kind") == "snapshot"
        and isinstance(row.get("vector"), list)
    ]
    result: dict[int, float] = {}
    previous: Sequence[Any] | None = None
    for snapshot in snapshots:
        vector = snapshot["vector"]
        turn = int(snapshot.get("turn", 0))
        result[turn] = 0.0 if previous is None else _l1(previous, vector)
        previous = vector
    return result


def _snapshot_for_turn(
    snapshots: Sequence[Mapping[str, Any]],
    turn: int,
) -> Mapping[str, Any] | None:
    candidates = [
        row
        for row in snapshots
        if int(row.get("turn", -1)) <= turn
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda row: int(row.get("turn", -1)),
    )


def _active_modifier_total(layers: Mapping[str, Any]) -> float:
    ability = _as_mapping(layers.get("ability"))
    modifiers = ability.get("modifiers")
    if not isinstance(modifiers, list):
        return 0.0
    return sum(
        float(modifier.get("value", 0.0))
        for modifier in modifiers
        if isinstance(modifier, Mapping)
        and bool(modifier.get("active", False))
    )


def _protagonist_strength(
    layers: Mapping[str, Any],
    world_meta: Mapping[str, Any],
) -> float:
    ability = _as_mapping(layers.get("ability"))
    base = float(ability.get("base", 0.0))
    traits = _as_mapping(world_meta.get("protagonist_traits"))
    temperament = (
        0.65 * float(traits.get("stubbornness", 0.0))
        + 0.20 * float(traits.get("social", 0.0))
        + 0.15 * float(traits.get("curiosity", 0.0))
    )
    epsilon = float(world_meta.get("contest_epsilon", 0.0))
    return round(
        base + _active_modifier_total(layers) + epsilon * temperament,
        6,
    )


def _believed_strength(
    layers: Mapping[str, Any],
    world_meta: Mapping[str, Any],
    turn_rows: Sequence[Mapping[str, Any]],
    protagonist_strength: float,
) -> float | None:
    for row in reversed(turn_rows):
        if (
            row.get("kind") == "decision"
            and row.get("subject") == world_meta.get("protagonist")
        ):
            details = _as_mapping(row.get("details"))
            if "believed_diff" in details:
                return round(
                    protagonist_strength
                    - float(details["believed_diff"]),
                    6,
                )

    antagonist = str(world_meta.get("antagonist", ""))
    belief = _as_mapping(layers.get("belief"))
    antagonist_belief = _as_mapping(belief.get(antagonist))
    if not antagonist_belief:
        return None

    value = float(
        antagonist_belief.get(
            "base_estimate",
            world_meta.get("default_strength_prior", 0.0),
        )
    )
    known = {
        str(source)
        for source in antagonist_belief.get("known_modifiers", [])
    }
    modifiers = _as_mapping(
        world_meta.get("initial_modifiers")
    ).get(antagonist, [])
    if isinstance(modifiers, list):
        for modifier in modifiers:
            if not isinstance(modifier, Mapping):
                continue
            if not bool(modifier.get("active", True)):
                continue
            source = str(modifier.get("source", ""))
            if bool(modifier.get("visible", True)) or source in known:
                value += float(modifier.get("value", 0.0))
    return round(value, 6)


def _stance(
    snapshot: Mapping[str, Any],
    observer: str,
    target: str,
) -> float | None:
    relations = snapshot.get("relations")
    if not isinstance(relations, list):
        return None
    for relation in relations:
        if (
            isinstance(relation, Mapping)
            and relation.get("observer") == observer
            and relation.get("target") == target
        ):
            return round(float(relation.get("affinity", 0.0)), 4)
    return None


def _state_summary(
    snapshot: Mapping[str, Any] | None,
    turn_rows: Sequence[Mapping[str, Any]],
    world_meta: Mapping[str, Any],
) -> dict[str, Any]:
    if snapshot is None:
        return {}

    layers = _as_mapping(snapshot.get("layers"))
    protagonist = str(world_meta.get("protagonist", ""))
    antagonist = str(world_meta.get("antagonist", ""))
    actual_strength = _protagonist_strength(layers, world_meta)
    objective = _as_mapping(layers.get("objective"))

    return {
        "strength": actual_strength,
        "believed_strength": _believed_strength(
            layers,
            world_meta,
            turn_rows,
            actual_strength,
        ),
        "stance": _stance(
            snapshot,
            protagonist,
            antagonist,
        ),
        "holder": {
            str(item): (
                str(holder) if holder is not None else None
            )
            for item, holder in sorted(objective.items())
        },
        "phase": [
            str(value)
            for value in layers.get("phase", [])
        ],
        "vitality": str(layers.get("vitality", "不明")),
        "zone": str(layers.get("zone", "不明")),
    }


def _objective_changes(
    row: Mapping[str, Any],
) -> dict[str, str | None]:
    delta = _as_mapping(row.get("delta"))
    objective = delta.get("objective")
    if not isinstance(objective, Mapping):
        return {}
    return {
        str(item): (
            str(holder) if holder is not None else None
        )
        for item, holder in sorted(objective.items())
    }


def _display_name(
    value: Any,
    world_meta: Mapping[str, Any],
) -> str:
    text = "世界" if value is None else str(value)
    names = _as_mapping(world_meta.get("display_names"))
    return str(names.get(text, text))


def _argument_text(
    args: Any,
    world_meta: Mapping[str, Any],
) -> str:
    if not isinstance(args, list) or not args:
        return ""
    values = [
        _display_name(value, world_meta)
        for value in args
    ]
    return "（" + "、".join(values) + "）"


def _effect_description(
    row: Mapping[str, Any],
    world_meta: Mapping[str, Any],
) -> str | None:
    details = _as_mapping(row.get("details"))
    effect_id = details.get("library_id")
    if effect_id is None:
        compound = str(details.get("effect_id", ""))
        effect_id = compound.split(":", 1)[0] if compound else None
    descriptions = _as_mapping(
        world_meta.get("effect_descriptions")
    )
    if effect_id is None:
        return None
    description = descriptions.get(str(effect_id))
    return str(description) if description else None


def describe_row(
    row: Mapping[str, Any],
    world_meta: Mapping[str, Any],
) -> str:
    subject = _display_name(row.get("subject"), world_meta)
    verb = str(row.get("verb", ""))
    details = _as_mapping(row.get("details"))

    label = details.get("label")
    if isinstance(label, str) and label:
        text = f"{subject}に「{label}」"
    elif verb == "ending":
        ending_labels = _as_mapping(world_meta.get("ending_labels"))
        ending_label = ending_labels.get(str(row.get("id", "")))
        text = (
            f"{subject}が「{ending_label}」という結末に到達した"
            if ending_label
            else f"{subject}が結末に到達した"
        )
    elif verb == "threshold_crossed":
        threshold = details.get("threshold")
        text = f"{subject}が「{threshold}」という節目を越えた"
    elif verb == "ally_gained":
        ally = _display_name(details.get("ally"), world_meta)
        text = f"{subject}が{ally}の仲間になった"
    elif verb == "betrayal":
        target = _display_name(details.get("target"), world_meta)
        text = f"{subject}が{target}との誓いを破った"
    elif verb == "exposure":
        target = _display_name(details.get("target"), world_meta)
        displayed = details.get("displayed")
        text = f"{subject}の前で{target}の秘密が露見した"
        if displayed:
            text += f"（それまでの姿は{displayed}）"
    elif verb in {"planted", "payoff"}:
        description = _effect_description(row, world_meta)
        action = VERB_LABELS[verb]
        text = f"{subject}について{action}"
        if description:
            text += f"。「{description}」"
    else:
        action = VERB_LABELS.get(verb, f"{verb}という行動を取った")
        text = (
            f"{subject}が"
            f"{_argument_text(row.get('args'), world_meta)}"
            f"{action}"
        )

    result = str(row.get("result", ""))
    result_label = RESULT_LABELS.get(result)
    if result_label and result not in {"applied", "moved"}:
        text += f"。結果は{result_label}"

    changes = _objective_changes(row)
    if changes:
        rendered = "、".join(
            f"{item}の新しい所持先は"
            f"{_display_name(holder, world_meta) if holder else 'なし'}"
            for item, holder in changes.items()
        )
        text += f"。{rendered}"

    return text


def _route_reason(route: Mapping[str, Any]) -> str | None:
    """WB-ROUTE-001 S3: turn ``policy.route`` into a prose reason, or None
    when the route layer explicitly recorded "no reason". ``lost`` gets a
    fixed text (route.text is not written for that kind); every other kind/
    cause (advance, prepare, detour with cause in motive/belief/body/
    ignorance) reuses route.text as-is -- it is already natural Japanese,
    S0-S2. detour/none is the one case with no reason to report."""

    kind = route.get("kind")
    if kind == "lost":
        return "先の見通しが立たないまま動いた"
    if kind == "detour" and route.get("cause") == "none":
        return None
    text = route.get("text")
    return str(text) if text is not None else None


def _scene_motives(
    narrative_rows: Sequence[Mapping[str, Any]],
    world_meta: Mapping[str, Any],
    protagonist: str,
) -> list[dict[str, Any]]:
    """One entry per protagonist decision row in this scene that carries a
    ``policy.route`` (WB-ROUTE-001 S1+, only at rho>0). Rows without a route
    (rho=0, or a run predating the route layer) are skipped entirely, so a
    scene with no route-bearing decisions ends up with no motives here --
    ``extract_scenes`` leaves the "motives" key off the scene dict in that
    case, keeping old runs byte-identical."""

    motives: list[dict[str, Any]] = []
    for row in narrative_rows:
        if row.get("kind") != "decision" or row.get("subject") != protagonist:
            continue
        route = _as_mapping(_as_mapping(row.get("policy")).get("route"))
        if not route:
            continue
        motives.append(
            {
                "event": describe_row(row, world_meta),
                "why": _route_reason(route),
                "kind": route.get("kind"),
                "cause": route.get("cause"),
            }
        )
    return motives


def _scene_rows(
    rows: Sequence[Mapping[str, Any]],
    protagonist: str,
) -> list[Mapping[str, Any]]:
    selected: list[Mapping[str, Any]] = []
    for row in rows:
        verb = str(row.get("verb", ""))
        if verb in SPECIAL_PRIORITY:
            selected.append(row)
        elif _objective_changes(row):
            selected.append(row)
        elif (
            row.get("kind") == "decision"
            and row.get("subject") == protagonist
            and bool(row.get("effective", False))
        ):
            selected.append(row)
    return selected


def extract_scenes(
    rows: Sequence[Mapping[str, Any]],
    world_meta: Mapping[str, Any],
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Extract at most ``limit`` notable turns without consuming randomness."""

    if limit < 1:
        raise ValueError("limit must be positive")

    header = next(
        (row for row in rows if row.get("kind") == "header"),
        {},
    )
    protagonist = str(
        world_meta.get("protagonist")
        or header.get("protagonist")
        or ""
    )
    snapshots = [
        row for row in rows if row.get("kind") == "snapshot"
    ]
    magnitudes = _turn_magnitudes(rows)

    by_turn: dict[int, list[Mapping[str, Any]]] = {}
    for row in rows:
        if row.get("kind") == "header" or "turn" not in row:
            continue
        turn = int(row["turn"])
        by_turn.setdefault(turn, []).append(row)

    candidates: list[dict[str, Any]] = []
    for turn in sorted(by_turn):
        turn_rows = by_turn[turn]
        narrative_rows = _scene_rows(turn_rows, protagonist)
        if not narrative_rows:
            continue

        priorities = [
            SPECIAL_PRIORITY.get(str(row.get("verb", "")), 10)
            for row in narrative_rows
        ]
        if any(_objective_changes(row) for row in narrative_rows):
            priorities.append(70)

        reasons: list[str] = []
        if any(
            row.get("kind") == "decision"
            and row.get("subject") == protagonist
            and bool(row.get("effective", False))
            for row in narrative_rows
        ):
            reasons.append("主人公の有効な決定")
        if any(
            str(row.get("verb", "")) in SPECIAL_PRIORITY
            for row in narrative_rows
        ):
            reasons.append("重要な派生イベント")
        if any(_objective_changes(row) for row in narrative_rows):
            reasons.append("目的物の所持者交代")

        snapshot = _snapshot_for_turn(snapshots, turn)
        foreshadowing = [
            description
            for description in (
                _effect_description(row, world_meta)
                for row in narrative_rows
                if str(row.get("verb", "")) in {"planted", "payoff"}
            )
            if description
        ]
        first = narrative_rows[0]
        scene = {
            "turn": turn,
            "day": int(first.get("day", 0)),
            "slot": first.get("slot"),
            "delta_l1": magnitudes.get(turn, 0.0),
            "priority": max(priorities),
            "reasons": reasons,
            "events": [
                describe_row(row, world_meta)
                for row in narrative_rows
            ],
            "state": _state_summary(
                snapshot,
                turn_rows,
                world_meta,
            ),
            "foreshadowing": list(dict.fromkeys(foreshadowing)),
        }
        motives = _scene_motives(narrative_rows, world_meta, protagonist)
        if motives:
            scene["motives"] = motives
        candidates.append(scene)

    kept = sorted(
        candidates,
        key=lambda scene: (
            -int(scene["priority"]),
            -float(scene["delta_l1"]),
            int(scene["turn"]),
        ),
    )[:limit]
    return sorted(kept, key=lambda scene: int(scene["turn"]))