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
            f'stroke-opacity="{opacity:.2f}"><title>{html.escape(title)}</title></line>'
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
            f'<g class="{classes}">'
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
    for subject in subjects:
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
            "<tr>"
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


_VITALITY_LABELS = {"alive": "生存中", "downed": "戦闘不能", "dead": "死亡", "revived": "立ち直った"}
_STANCE_LABELS = {"hostile": "敵対関係", "neutral": "中立関係", "friendly": "友好関係"}
_ROLE_LABELS = {"hostile": "敵対相手", "neutral": "第三者", "ally": "味方", "self": "自分自身"}
_OBJECTIVE_LABELS = {"self": "自分自身", "hostile": "敵対相手", "ally": "味方", "other": "味方でも敵でもない相手・場所"}


def _ctx_text(ctx: Mapping[str, Any]) -> str:
    """Render a canon/precedent ContextKey (gapengine/precedent.py).

    Field set and values (phase, hostile_present, objective in
    {none,self,ally,hostile,other}, vitality, stance in
    {hostile,friendly,neutral}, disguised) mirror precedent.py's
    `ctx_key`/`_parse_ctx` exactly, not just what today's three
    genre templates happen to use -- an unrecognised value still shows (as
    its raw token) rather than being silently dropped.
    """

    parts = []
    phase = [str(step) for step in (ctx.get("phase") or []) if step]
    if phase:
        parts.append("段階: " + "・".join(phase))
    if ctx.get("hostile_present"):
        parts.append("敵対相手が同席")
    objective = ctx.get("objective")
    if objective and objective != "none":
        parts.append(f"目的物: {_OBJECTIVE_LABELS.get(str(objective), str(objective))}")
    vitality = ctx.get("vitality")
    if vitality:
        parts.append(_VITALITY_LABELS.get(str(vitality), str(vitality)))
    stance = ctx.get("stance")
    if stance:
        parts.append(_STANCE_LABELS.get(str(stance), str(stance)))
    if ctx.get("disguised"):
        parts.append("変装中")
    return "・".join(parts) if parts else "（条件なし）"


def _act_text(act: Mapping[str, Any]) -> str:
    verb = act.get("verb")
    label = VERB_LABELS.get(str(verb), str(verb)) if verb else "（行動なし）"
    detail = []
    category = act.get("category")
    if category:
        detail.append(f"カテゴリ{category}")
    role = act.get("role")
    if role and role != "none":
        detail.append(f"対象: {_ROLE_LABELS.get(str(role), str(role))}")
    return f"{label}（{'・'.join(detail)}）" if detail else label


def canon_table_html(canon_yaml: Mapping[str, Any]) -> str:
    """Render a genre's canon.yaml as a 状況→行動→件数 table.

    This is the GA's precedent for generation 0 (WB-EXPLAIN-canon): a prior
    over "typical" actions per situation, not a plot -- individuals are
    rewarded for straying from it (novelty_drive), never for following it.
    """

    entries = [entry for entry in (canon_yaml.get("entries") or []) if _mapping(entry)]
    if not entries:
        return '<p class="muted">正典データがありません。</p>'
    rows = []
    for entry in entries:
        entry = _mapping(entry)
        ctx_text = _ctx_text(_mapping(entry.get("ctx")))
        act_text = _act_text(_mapping(entry.get("act")))
        rows.append(
            "<tr>"
            f"<td>{html.escape(ctx_text)}</td>"
            f"<td>{html.escape(act_text)}</td>"
            f"<td>{_esc(entry.get('n'))}</td>"
            "</tr>"
        )
    return (
        '<div class="grid-wrap"><table class="wb-table canon-table"><thead><tr>'
        "<th>状況</th><th>典型的な行動</th><th>擬似観測件数</th>"
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    )


def character_readout_html(
    world_yaml: Mapping[str, Any],
    subjects: Sequence[Mapping[str, Any]],
    effects: Sequence[Any],
) -> str:
    """Render, per subject, what its numbers already mean: base strength
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

    blocks = []
    for subject in subjects:
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

        body = strength_html + secret_html + payoff_html
        if not body:
            continue
        blocks.append(
            f'<details class="readout"><summary>{html.escape(subject_id)}</summary>{body}</details>'
        )

    if not blocks:
        return '<p class="muted">読み下せる設定がありません。</p>'
    return '<div class="character-readout">' + "".join(blocks) + "</div>"
