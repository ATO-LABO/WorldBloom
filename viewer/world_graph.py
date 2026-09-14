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
