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
    world_yaml: Mapping[str, Any],
    subjects: Sequence[Mapping[str, Any]],
    *,
    protagonist: str | None,
    antagonist: str | None,
) -> str:
    """Render the <dl> of read-only setting highlights above the graph."""

    time_info = _mapping(world_yaml.get("time"))
    days = time_info.get("days")
    slots = time_info.get("slots")
    if days and isinstance(slots, list) and slots:
        period = f"{days}日 × {'/'.join(str(slot) for slot in slots)}"
    else:
        period = "—"

    zone_text = []
    for zone in world_yaml.get("zones") or []:
        zone = _mapping(zone)
        name = zone.get("name")
        if not name:
            continue
        note = zone.get("note")
        zone_text.append(f"{name}（{note}）" if note else str(name))
    places = "、".join(zone_text) if zone_text else "—"

    by_id = {str(subject.get("id")): subject for subject in subjects if subject.get("id")}
    protagonist_goal = _goal_text(by_id.get(protagonist) if protagonist else None)
    antagonist_goal = _goal_text(by_id.get(antagonist) if antagonist else None)

    return (
        '<dl class="world-summary">'
        f"<dt>期間</dt><dd>{html.escape(period)}</dd>"
        f"<dt>場所</dt><dd>{html.escape(places)}</dd>"
        f"<dt>主人公の目的</dt><dd>{html.escape(protagonist_goal)}</dd>"
        f"<dt>敵役の目的</dt><dd>{html.escape(antagonist_goal)}</dd>"
        "</dl>"
    )
