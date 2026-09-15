"""Deterministic person-relation rendering for a world's subjects (WB-UI-016).

Pure and dependency-light on purpose: no import of viewer.pages or
execution.library, so it can be called from either without a circular
import. The relation SVG is built as a plain string (stdlib html + math),
matching pages.layers_svg's approach -- no client-side layout, no inline
<script>.
"""
from __future__ import annotations

import html
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from gapengine.scenes import VERB_LABELS

_PROTAGONIST_FILL = "#2e6b4f"
_ANTAGONIST_FILL = "#8c3030"
_DEFAULT_FILL = "#fffdf8"
_DEFAULT_STROKE = "#2e6b4f"

_POSITIVE = "#2e6b4f"
_NEGATIVE = "#8c3030"
_NEUTRAL = "#9aa39e"


def _esc(value: Any) -> str:
    if value is None or value == "":
        return "—"
    return html.escape(str(value))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def load_subjects(base: Path) -> list[dict[str, Any]]:
    """Load projects/<id>/subjects/*.yaml in filename order.

    Files that fail to parse, or that do not parse to a mapping, are
    skipped -- never raised -- so a broken subject file degrades the graph
    instead of breaking the whole world page.
    """

    subjects_dir = base / "subjects"
    if not subjects_dir.is_dir():
        return []
    result: list[dict[str, Any]] = []
    for path in sorted(subjects_dir.glob("*.yaml")):
        try:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        if isinstance(value, dict):
            result.append(value)
    return result


def relation_svg(
    subjects: Sequence[Mapping[str, Any]],
    *,
    protagonist: str | None,
    antagonist: str | None,
) -> str:
    """Render a circular-layout relation graph as a self-contained SVG string.

    Deterministic: node order follows `subjects` (caller passes filename
    order), edge order follows the sorted node-id pairs, and all numbers are
    formatted to a fixed number of decimals -- the same (world, subjects)
    input always yields the same byte string.
    """

    ids = list(dict.fromkeys(str(subject["id"]) for subject in subjects if subject.get("id")))
    if not ids:
        return '<p class="muted">人物がいません。</p>'

    by_id = {str(subject["id"]): subject for subject in subjects if subject.get("id")}
    id_set = set(ids)
    # WB-UI-021: same "sheet-<subjects index>" ids as character_table /
    # character_readout_html, so a node opens the same <dialog> as its row
    # and app.js can light the edges that touch a hovered node.
    sheet_of: dict[str, str] = {}
    for index, subject in enumerate(subjects):
        sheet_of.setdefault(str(subject.get("id") or ""), f"sheet-{index}")

    center_x, center_y, radius = 320.0, 200.0, 150.0
    positions: dict[str, tuple[float, float]] = {}
    count = len(ids)
    for index, subject_id in enumerate(ids):
        if count == 1:
            positions[subject_id] = (center_x, center_y)
            continue
        angle = math.radians(-90.0 + 360.0 * index / count)
        positions[subject_id] = (
            center_x + radius * math.cos(angle),
            center_y + radius * math.sin(angle),
        )

    # Collect one entry per direction, keyed by the unordered node pair, but
    # only for relations whose target is itself a subject on this graph --
    # a disguise alias (e.g. a subject's "as" name) is not a node, so any
    # relation naming it is silently dropped rather than drawn as an edge.
    edges: dict[tuple[str, str], list[tuple[str, str, float, float]]] = {}
    for subject_id in ids:
        relations = _mapping(by_id[subject_id].get("relations"))
        for target, relation in relations.items():
            target_id = str(target)
            if target_id == subject_id or target_id not in id_set:
                continue
            relation = _mapping(relation)
            affinity = _number(relation.get("affinity"))
            awareness = _clamp01(_number(relation.get("awareness"), default=0.5))
            pair = tuple(sorted((subject_id, target_id)))
            edges.setdefault(pair, []).append((subject_id, target_id, affinity, awareness))

    edge_markup = []
    for pair in sorted(edges):
        entries = edges[pair]
        mean_affinity = sum(entry[2] for entry in entries) / len(entries)
        max_awareness = max(entry[3] for entry in entries)
        if mean_affinity > 0.05:
            color = _POSITIVE
        elif mean_affinity < -0.05:
            color = _NEGATIVE
        else:
            color = _NEUTRAL
        width = 1.0 + 3.0 * abs(mean_affinity)
        opacity = 0.35 + 0.65 * max_awareness
        (x1, y1), (x2, y2) = positions[pair[0]], positions[pair[1]]
        title = " / ".join(
            f"{observer}→{target} 好感 {affinity:.2f} 認知 {awareness:.2f}"
            for observer, target, affinity, awareness in entries
        )
        edge_markup.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{color}" stroke-width="{width:.1f}" '
            f'stroke-opacity="{opacity:.2f}" data-a="{sheet_of[pair[0]]}" data-b="{sheet_of[pair[1]]}">'
            f"<title>{html.escape(title)}</title></line>"
        )

    node_markup = []
    for subject_id in ids:
        x, y = positions[subject_id]
        # The label sits below the circle on the page background, so it
        # keeps the default (dark) text colour for every node.
        if subject_id == protagonist:
            classes, fill = "node is-protagonist", _PROTAGONIST_FILL
            stroke_attr = ""
        elif subject_id == antagonist:
            classes, fill = "node is-antagonist", _ANTAGONIST_FILL
            stroke_attr = ""
        else:
            classes, fill = "node", _DEFAULT_FILL
            stroke_attr = f' stroke="{_DEFAULT_STROKE}"'
        node_markup.append(
            f'<g class="{classes}" data-sheet="{sheet_of[subject_id]}" tabindex="0" role="button" '
            f'aria-label="{html.escape(subject_id)}のパラメータを開く">'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="22" fill="{fill}"{stroke_attr}/>'
            f'<text x="{x:.1f}" y="{y + 36:.1f}" text-anchor="middle">'
            f"{html.escape(subject_id)}</text></g>"
        )

    return (
        '<svg class="relation-graph" viewBox="0 0 640 400" '
        'role="img" aria-label="人物相関図">'
        + "".join(edge_markup)
        + "".join(node_markup)
        + "</svg>"
    )


def character_table(
    subjects: Sequence[Mapping[str, Any]],
    *,
    protagonist: str | None,
    antagonist: str | None,
) -> str:
    """Render the one-row-per-subject table shown beside the relation SVG."""

    rows = []
    for index, subject in enumerate(subjects):
        subject_id = str(subject.get("id") or "")
        if not subject_id:
            continue
        if subject_id == protagonist:
            role = "主人公"
        elif subject_id == antagonist:
            role = "敵役"
        else:
            role = "—"
        identity = _mapping(subject.get("identity"))
        true_identity = identity.get("true")
        displayed = identity.get("displayed")
        identity_text = _esc(true_identity)
        # identity.true is a description, so compare the shown name with the
        # subject id instead: only a real alias earns the "表向き" note.
        if displayed is not None and str(displayed) != subject_id:
            identity_text += f"（表向き: {html.escape(str(displayed))}）"
        goal = _mapping(subject.get("goal"))
        companions = [
            str(name)
            for name in subject.get("companions") or []
            if str(name) != subject_id
        ]
        rows.append(
            # data-sheet pairs the row with the <dialog> character_readout_html
            # emits for the same subjects index (app.js opens it on click).
            f'<tr data-sheet="sheet-{index}" tabindex="0" title="クリックでパラメータを開く">'
            f"<td>{html.escape(subject_id)}</td>"
            f"<td>{html.escape(role)}</td>"
            f"<td>{identity_text}</td>"
            f"<td>{_esc(goal.get('target'))}</td>"
            f"<td>{_esc(_mapping(subject.get('range')).get('entry'))}</td>"
            f"<td>{html.escape('、'.join(companions)) if companions else '—'}</td>"
            "</tr>"
        )
    if not rows:
        return '<p class="muted">人物がいません。</p>'
    return (
        '<div class="grid-wrap"><table class="wb-table character-table"><thead><tr>'
        "<th>名前</th><th>役割</th><th>正体</th><th>目的</th><th>開始地点</th><th>仲間</th>"
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    )


def _goal_text(subject: Mapping[str, Any] | None) -> str:
    if subject is None:
        return "—"
    goal = _mapping(subject.get("goal"))
    target = goal.get("target")
    if not target:
        return "—"
    parts = [str(target)]
    deliver_to = goal.get("deliver_to")
    if deliver_to:
        parts.append(f"→ {deliver_to}へ")
    obstacles = [str(value) for value in goal.get("obstacles") or [] if value]
    if obstacles:
        parts.append("障害: " + "、".join(obstacles))
    return " · ".join(parts)


def world_summary(
    subjects: Sequence[Mapping[str, Any]],
    *,
    protagonist: str | None,
    antagonist: str | None,
) -> str:
    """Render the <dl> of protagonist/antagonist goals above the graph."""

    by_id = {str(subject.get("id")): subject for subject in subjects if subject.get("id")}
    protagonist_goal = _goal_text(by_id.get(protagonist) if protagonist else None)
    antagonist_goal = _goal_text(by_id.get(antagonist) if antagonist else None)

    return (
        '<dl class="world-summary">'
        f"<dt>主人公の目的</dt><dd>{html.escape(protagonist_goal)}</dd>"
        f"<dt>敵役の目的</dt><dd>{html.escape(antagonist_goal)}</dd>"
        "</dl>"
    )


def zone_list_html(zones: Sequence[Mapping[str, Any]]) -> str:
    """Render the plain name/note list of a world's zones."""

    rows = []
    for zone in zones or []:
        zone = _mapping(zone)
        name = zone.get("name")
        if not name:
            continue
        note = zone.get("note")
        rows.append(
            f"<dt>{html.escape(str(name))}</dt><dd>{_esc(note)}</dd>"
        )
    if not rows:
        return '<p class="muted">場所がありません。</p>'
    return '<dl class="zone-list">' + "".join(rows) + "</dl>"


def zone_svg(zones: Sequence[Mapping[str, Any]], routes: Mapping[str, Any]) -> str:
    """Render the zones and their `routes` adjacency as a relation-style graph.

    Same circular layout and edge-dedup approach as relation_svg, but edges
    carry no affinity -- a route either exists or it doesn't -- so every
    edge is drawn the same way, with the hop's cost/requires_item (if any)
    surfaced only in the hover title.
    """

    names = list(dict.fromkeys(
        str(zone["name"]) for zone in zones if _mapping(zone).get("name")
    ))
    if not names:
        return '<p class="muted">場所がありません。</p>'
    name_set = set(names)

    center_x, center_y, radius = 320.0, 200.0, 150.0
    positions: dict[str, tuple[float, float]] = {}
    count = len(names)
    for index, name in enumerate(names):
        if count == 1:
            positions[name] = (center_x, center_y)
            continue
        angle = math.radians(-90.0 + 360.0 * index / count)
        positions[name] = (
            center_x + radius * math.cos(angle),
            center_y + radius * math.sin(angle),
        )

    edges: dict[tuple[str, str], list[str]] = {}
    for origin, hops in _mapping(routes).items():
        origin = str(origin)
        if origin not in name_set:
            continue
        for hop in hops or []:
            hop = _mapping(hop)
            target = str(hop.get("to"))
            if target == origin or target not in name_set:
                continue
            detail = []
            cost = hop.get("cost")
            if cost is not None:
                detail.append(f"移動コスト {cost}")
            requires = hop.get("requires_item")
            if requires:
                detail.append(f"要: {requires}")
            suffix = f"（{'・'.join(detail)}）" if detail else ""
            pair = tuple(sorted((origin, target)))
            edges.setdefault(pair, []).append(f"{origin}→{target}{suffix}")

    edge_markup = []
    for pair in sorted(edges):
        (x1, y1), (x2, y2) = positions[pair[0]], positions[pair[1]]
        title = " / ".join(edges[pair])
        edge_markup.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{_NEUTRAL}" stroke-width="2.0">'
            f'<title>{html.escape(title)}</title></line>'
        )

    node_markup = []
    for name in names:
        x, y = positions[name]
        node_markup.append(
            '<g class="node">'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="22" fill="{_DEFAULT_FILL}" '
            f'stroke="{_DEFAULT_STROKE}"/>'
            f'<text x="{x:.1f}" y="{y + 36:.1f}" text-anchor="middle">'
            f"{html.escape(name)}</text></g>"
        )

    return (
        '<svg class="relation-graph zone-graph" viewBox="0 0 640 400" '
        'role="img" aria-label="場所のつながり">'
        + "".join(edge_markup)
        + "".join(node_markup)
        + "</svg>"
    )


_SLOT_PALETTE = ("#c9a24a", "#2e6b4f", "#a86f1c", "#1d4a36", "#8c3030", "#66716d")


def day_cycle_svg(slots: Sequence[Any]) -> str:
    """Render one day's time slots as a ring, one wedge per slot in order.

    Built with stroke-dasharray/-dashoffset on stacked concentric circles
    (the standard zero-path-math donut-chart trick) instead of manual arc
    paths, so it stays correct for any slot count without large-arc-flag
    edge cases.
    """

    names = [str(slot) for slot in slots if str(slot)]
    if not names:
        return '<p class="muted">時間帯がありません。</p>'

    center, radius = 120.0, 86.0
    circumference = 2 * math.pi * radius
    share = circumference / len(names)

    segments = []
    labels = []
    for index, name in enumerate(names):
        color = _SLOT_PALETTE[index % len(_SLOT_PALETTE)]
        offset = share * index
        segments.append(
            f'<circle cx="{center}" cy="{center}" r="{radius}" fill="none" '
            f'stroke="{color}" stroke-width="34" '
            f'stroke-dasharray="{share:.2f} {circumference - share:.2f}" '
            f'stroke-dashoffset="{-offset:.2f}"><title>{html.escape(name)}</title></circle>'
        )
        angle = math.radians(-90.0 + 360.0 * (index + 0.5) / len(names))
        label_x = center + (radius + 34) * math.cos(angle)
        label_y = center + (radius + 34) * math.sin(angle)
        labels.append(
            f'<text x="{label_x:.1f}" y="{label_y:.1f}" text-anchor="middle" '
            f'dominant-baseline="middle">{html.escape(name)}</text>'
        )

    return (
        '<svg class="day-cycle" viewBox="0 0 240 240" role="img" '
        'aria-label="1日の時間帯の分け方">'
        f'<g transform="rotate(-90 {center} {center})">' + "".join(segments) + "</g>"
        + "".join(labels)
        + "</svg>"
    )


_MAX_CALENDAR_DAYS = 366


def calendar_grid_html(days: Any) -> str:
    """Render a calendar-style grid of day cells, "1".."days" in order.

    world.yaml's `time.days` is user-editable through this app's own file
    editor, so a typo (e.g. an extra zero) must not blow up the DOM -- past
    _MAX_CALENDAR_DAYS this falls back to plain text instead of one cell per
    day.
    """

    count = int(_number(days))
    if count <= 0:
        return '<p class="muted">期間がありません。</p>'
    if count > _MAX_CALENDAR_DAYS:
        return f'<p class="muted">{count}日間（グリッド表示は{_MAX_CALENDAR_DAYS}日までです）。</p>'
    cells = "".join(
        f'<div class="calendar-day"><span>{day}</span></div>'
        for day in range(1, count + 1)
    )
    return f'<div class="calendar-grid" role="img" aria-label="{count}日間の日程">{cells}</div>'


_ROLE_LABELS = {"hostile": "敵対相手", "neutral": "第三者", "ally": "味方", "self": "自分自身"}


# WB-UI-020: canon.yaml rendered for a reader. Each ctx field is phrased as
# a clause; fields that never vary across the table are pulled out into one
# footnote (or dropped when they only hold precedent.py's default), so a row
# reads as a sentence instead of a dump of the context key.
_CTX_DEFAULTS = {
    "phase": (), "hostile_present": False, "objective": "none",
    "vitality": "alive", "stance": "neutral", "disguised": False,
}


def _ctx_value(ctx: Mapping[str, Any], field: str) -> Any:
    if field == "phase":
        return tuple(str(step) for step in (ctx.get("phase") or []) if step)
    if field in ("hostile_present", "disguised"):
        return bool(ctx.get(field))
    return str(ctx.get(field) or _CTX_DEFAULTS[field])


def _ctx_clause(field: str, value: Any, objective: str) -> str:
    if field == "phase":
        return f"{'・'.join(value)}のあと" if value else "まだ何も起きていないうち"
    if field == "hostile_present":
        return "敵が目の前にいる" if value else "敵がいない"
    if field == "objective":
        holder = {
            "none": "誰の手にもない", "self": "自分が持っている", "hostile": "敵が持っている",
            "ally": "味方が持っている", "other": "第三者が持っている",
        }.get(value, value)
        return f"{objective}を{holder}" if value != "none" else f"{objective}が{holder}"
    if field == "vitality":
        return {"alive": "無事", "downed": "倒れている", "dead": "死んでいる", "revived": "立ち直った直後"}.get(value, value)
    if field == "stance":
        return {"hostile": "敵と敵対している", "friendly": "敵と友好的", "neutral": "敵と中立"}.get(value, value)
    if field == "disguised":
        return "変装中" if value else "素顔"
    return str(value)


def canon_table_html(canon_yaml: Mapping[str, Any], *, objective: str = "目的の品") -> str:
    """Render a genre's canon.yaml as 状況（一文）→定石の行動→定石の強さ（バー）.

    This is the GA's precedent for generation 0 (WB-EXPLAIN-canon): a prior
    over "typical" actions per situation, not a plot -- novelty_drive makes
    the protagonist less likely to pick these, never more.
    """

    entries = [_mapping(entry) for entry in (canon_yaml.get("entries") or []) if _mapping(entry)]
    if not entries:
        return '<p class="muted">正典データがありません。</p>'
    contexts = [_mapping(entry.get("ctx")) for entry in entries]
    varying, shared = [], []
    for field, default in _CTX_DEFAULTS.items():
        values = {_ctx_value(ctx, field) for ctx in contexts}
        if len(values) > 1:
            varying.append(field)
        elif values and next(iter(values)) != default:
            shared.append(_ctx_clause(field, next(iter(values)), objective))
    max_n = max([_number(entry.get("n"), 1.0) for entry in entries] + [0.0])
    rows = []
    for entry, ctx in zip(entries, contexts):
        clauses = [_ctx_clause(field, _ctx_value(ctx, field), objective) for field in varying]
        act = _mapping(entry.get("act"))
        verb = act.get("verb")
        act_text = VERB_LABELS.get(str(verb), str(verb)) if verb else "（行動なし）"
        role = act.get("role")
        if role and role != "none":
            act_text += f"（相手: {_ROLE_LABELS.get(str(role), str(role))}）"
        n = _number(entry.get("n"), 1.0)
        ratio = _clamp01(n / max_n) if max_n > 0 else 0.0
        rows.append(
            "<tr>"
            f"<td>{html.escape('、'.join(clauses) if clauses else 'どんな状況でも')}</td>"
            f"<td>{html.escape(act_text)}</td>"
            f'<td><span class="gene-track"><span class="gene-fill" style="width:{ratio * 100:.1f}%"></span></span>'
            f' <span class="muted">{n:g}</span></td>'
            "</tr>"
        )
    shared_html = (
        f'<p class="muted">すべての行に共通: 主人公は{html.escape("、".join(shared))}。</p>' if shared else ""
    )
    return (
        '<div class="grid-wrap"><table class="wb-table canon-table"><thead><tr>'
        "<th>状況</th><th>定石の行動</th><th>定石の強さ</th>"
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
        + shared_html
    )


# WB-UI-018: the nine initial parameters of a subject, drawn as game-style
# bars. Scales are fixed where the engine defines one (traits and reputation
# live in 0..1, base is normalised /100 in the QD volatility vector); stamina
# and ally_value have no ceiling, so they are drawn relative to the largest
# value among this world's subjects -- "a lot" means "a lot for this world".
_TRAIT_LABELS = (
    ("social", "社交性"), ("stubbornness", "頑固さ"), ("curiosity", "好奇心"),
    ("diligence", "勤勉さ"), ("temper", "気性の荒さ"),
)


_SHEET_NOTE = (
    '<p class="muted sheet-note">気質と評判は 0〜1、基礎の強さは 100、'
    "体力と仲間への加勢はこの世界での最大値を上限にバーを描いています。"
    "右側は数値がすでに意味しているもの（見えない修正・秘密・伏線）で、生成された説明ではなく "
    "world.yaml/subjects/effects.yaml の値とエンジンの計算式どおりです。</p>"
)


def _stat_bar(label: str, value: float, scale: float, text: str) -> str:
    ratio = _clamp01(value / scale) if scale > 0 else 0.0
    return (
        f'<div class="stat"><span class="stat-label">{html.escape(label)}</span>'
        f'<span class="gene-track"><span class="gene-fill" style="width:{ratio * 100:.1f}%"></span></span>'
        f'<span class="stat-value">{html.escape(text)}</span></div>'
    )


def _stat_bars_html(
    subject: Mapping[str, Any], *, base_scale: float, stamina_scale: float, ally_scale: float,
) -> str:
    traits = _mapping(subject.get("traits"))
    stamina = _mapping(subject.get("stamina"))
    base = _number(subject.get("base"))
    stamina_max = _number(stamina.get("max"))
    recover = _number(stamina.get("recover_per_slot"))
    reputation = _number(subject.get("reputation"))
    ally = _number(subject.get("ally_value"))
    bars = [
        _stat_bar(label, _number(traits.get(key)), 1.0, f"{_number(traits.get(key)):.2f}")
        for key, label in _TRAIT_LABELS
    ]
    bars += [
        _stat_bar("基礎の強さ", base, base_scale, f"{base:.0f}"),
        _stat_bar("体力", stamina_max, stamina_scale, f"{stamina_max:.0f}（回復 {recover:g}/時間帯）"),
        _stat_bar("評判", reputation, 1.0, f"{reputation:.2f}"),
        _stat_bar("仲間への加勢", ally, ally_scale, f"{ally:.0f}"),
    ]
    return '<div class="stat-bars">' + "".join(bars) + "</div>"


def character_readout_html(
    world_yaml: Mapping[str, Any],
    subjects: Sequence[Mapping[str, Any]],
    effects: Sequence[Any],
    *,
    protagonist: str | None = None,
    antagonist: str | None = None,
) -> str:
    """Render one stat sheet per subject: its nine initial parameters as
    bars (WB-UI-018) next to what its numbers already mean -- base strength
    plus item modifiers (flagging ones hidden from other characters),
    personal secrets (`facts.secret_of`), and foreshadowing tied to it
    (effects.yaml plant/payoff pairs whose plant reveals this subject).

    Nothing here is generated -- every line restates a value the engine
    already tracks (WB-EXPLAIN-canon), so it stays exactly as trustworthy
    as world.yaml/subjects/*.yaml/effects.yaml themselves.
    """

    item_defs = {
        str(item["name"]): _mapping(item)
        for item in world_yaml.get("items") or []
        if _mapping(item).get("name")
    }
    facts = [_mapping(fact) for fact in world_yaml.get("facts") or [] if _mapping(fact)]
    # engine/world.py defaults contest.epsilon to 5.0 when the world.yaml
    # omits it -- match that instead of silently treating it as 0.
    epsilon = _number(_mapping(world_yaml.get("contest")).get("epsilon"), 5.0)

    beliefs_by_target: dict[str, list[tuple[str, float, set[str]]]] = {}
    for subject in subjects:
        observer_id = str(subject.get("id") or "")
        for target_id, belief in _mapping(subject.get("beliefs_about")).items():
            belief = _mapping(belief)
            estimate = belief.get("base_estimate")
            if estimate is None:
                continue
            known = {str(name) for name in belief.get("known_modifiers") or []}
            beliefs_by_target.setdefault(str(target_id), []).append(
                (observer_id, _number(estimate), known)
            )

    scales = dict(
        base_scale=max([100.0] + [_number(s.get("base")) for s in subjects]),
        stamina_scale=max([0.0] + [_number(_mapping(s.get("stamina")).get("max")) for s in subjects]),
        ally_scale=max([0.0] + [_number(s.get("ally_value")) for s in subjects]),
    )
    blocks = []
    for index, subject in enumerate(subjects):
        subject_id = str(subject.get("id") or "")
        if not subject_id:
            continue

        base = subject.get("base")
        # Mirrors engine/subject.py Subject.from_yaml + engine/world.py's
        # item_modifier(): both default an omitted `visible`/`active` to
        # True, so a modifier missing the key is NOT hidden -- getting this
        # backwards would fabricate a "hidden power" that isn't one.
        modifiers: list[tuple[str, float, bool]] = []
        for raw_modifier in subject.get("modifiers") or []:
            raw_modifier = _mapping(raw_modifier)
            if not raw_modifier.get("active", True):
                continue
            source = raw_modifier.get("source")
            if not source:
                continue
            modifiers.append((str(source), _number(raw_modifier.get("value")), bool(raw_modifier.get("visible", True))))
        for item_name, count in _mapping(subject.get("inventory")).items():
            if _number(count) <= 0:
                continue
            modifier = _mapping(_mapping(item_defs.get(str(item_name))).get("modifier"))
            if not modifier or not modifier.get("active", True):
                continue
            modifiers.append((str(item_name), _number(modifier.get("value")), bool(modifier.get("visible", True))))

        strength_html = ""
        if base is not None:
            base_value = _number(base)
            traits = _mapping(subject.get("traits"))
            temperament = (
                0.65 * _number(traits.get("stubbornness"))
                + 0.20 * _number(traits.get("social"))
                + 0.15 * _number(traits.get("curiosity"))
            )
            temperament_bonus = epsilon * temperament
            total = base_value + sum(value for _, value, _ in modifiers) + temperament_bonus
            if modifiers:
                mod_items = "".join(
                    f"<li>{html.escape(source)}: {value:+.0f}"
                    f"（{'他の登場人物にも見えている' if visible else '他の登場人物には見えていない隠れた強化'}）</li>"
                    for source, value, visible in modifiers
                )
                strength_html = (
                    f"<p>基礎の強さ {base_value:.0f} ＋ 所持品などによる修正"
                    f" ＋ 性格による小さな補正 {temperament_bonus:+.1f}"
                    f" = 実際の強さ {total:.0f}"
                    '（同席する仲間からの加算は含みません）</p>'
                    f"<ul>{mod_items}</ul>"
                )
            else:
                strength_html = (
                    f"<p>基礎の強さ {base_value:.0f} ＋ 性格による小さな補正 {temperament_bonus:+.1f}"
                    f" = 実際の強さ {total:.0f}（所持品などによる修正なし。"
                    "同席する仲間からの加算は含みません）</p>"
                )

            observer_lines = []
            for observer_id, estimate, known in beliefs_by_target.get(subject_id, []):
                believed = estimate + sum(
                    value for source, value, visible in modifiers
                    if visible or source in known
                )
                if abs(believed - total) > 0.001:
                    observer_lines.append(
                        f"<li>{html.escape(observer_id)}の当初の見積もり: {believed:.0f}"
                        f"（実際は{total:.0f}）</li>"
                    )
            if observer_lines:
                strength_html += (
                    '<p class="muted">他の人物からの見え方:</p>'
                    f"<ul>{''.join(observer_lines)}</ul>"
                )

        secret_lines = [
            f"<li>{html.escape(str(fact.get('label') or fact.get('id') or ''))}</li>"
            for fact in facts
            if str(fact.get("secret_of")) == subject_id
        ]
        secret_html = (
            f'<p class="muted">この人物にまつわる秘密:</p><ul>{"".join(secret_lines)}</ul>'
            if secret_lines else ""
        )

        payoff_lines = []
        for effect in effects:
            effect = _mapping(effect)
            reveals = _mapping(_mapping(effect.get("plant")).get("reveals"))
            if str(reveals.get("target")) != subject_id:
                continue
            description = _mapping(effect.get("payoff")).get("description")
            if description:
                payoff_lines.append(f"<li>{html.escape(str(description))}</li>")
        payoff_html = (
            f'<p class="muted">関連する伏線:</p><ul>{"".join(payoff_lines)}</ul>'
            if payoff_lines else ""
        )

        body = (strength_html + secret_html + payoff_html
                or '<p class="muted">秘密・伏線・隠された修正はありません。</p>')
        role = "主人公" if subject_id == protagonist else "敵役" if subject_id == antagonist else ""
        role_html = f' <span class="muted">{role}</span>' if role else ""
        blocks.append(
            f'<dialog class="sheet-dialog" id="sheet-{index}" aria-label="{html.escape(subject_id)}のパラメータ">'
            '<section class="character-sheet">'
            f'<div class="sheet-head"><h4>{html.escape(subject_id)}{role_html}</h4>'
            '<button type="button" class="button" data-close-dialog>閉じる</button></div>'
            '<div class="sheet-body">'
            + _stat_bars_html(subject, **scales)
            + f'<div class="sheet-readout">{body}</div></div>'
            + _SHEET_NOTE
            + "</section></dialog>"
        )

    if not blocks:
        return '<p class="muted">人物がいません。</p>'
    return '<div class="character-readout">' + "".join(blocks) + "</div>"
