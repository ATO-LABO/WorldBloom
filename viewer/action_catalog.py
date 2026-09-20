"""行動図鑑タブ（WB-UI-025）: 世界ごとの GapEngine 行動一覧。

`engine/action_catalog.yaml`（ジャンル非依存・コード同梱）に持つ前提／対象／
効果の説明文に、実際の `World`（この世界の action_graph.yaml と world.yaml）
から読んだ値を埋め込んで表示する。半静的方式（詳細は Notion の設計計画を参照）。

有効性は4段階で機械判定する:
  A. 未実装      -- VerbEngine にメソッドが無い
  B. ジャンルで枝刈り -- World.genre_allows(verb) が False
  C. この世界では未使用 -- どの人物の verbs にも無い
  D. 有効        -- 上記以外
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml

from engine.verbs import HANDLED_VERBS
from engine.world import World
from viewer import pages, world_graph

_escape = pages._escape

_ROLE_LABELS = {"hostile": "敵", "neutral": "中立", "ally": "味方"}

# VerbEngine.execute() only role-gates a verb when action.args[0] resolves to
# an actual subject id (engine/verbs.py's dispatch, around the
# action_graph_enabled check); these verbs' first arg is never a subject
# (an item, an effect id, a display name, ...), so no role/permission ever
# applies to them and the permission line would be a meaningless "allow" x3.
_TARGETLESS_VERBS = frozenset({
    "train", "rest", "withdraw", "craft", "guard", "rethink",
    "plant", "payoff", "disguise", "donate",
})


def _engine_verb_methods() -> frozenset[str]:
    return HANDLED_VERBS


_CATALOG_PATH = Path(__file__).resolve().parent.parent / "engine" / "action_catalog.yaml"


@functools.lru_cache(maxsize=1)
def _load_catalog() -> dict[str, Any]:
    # Code-shipped, genre-agnostic: lives beside engine/ (not per-world
    # data), so it resolves from this file's own location -- never from
    # store.repo, which in tests and the read-only viewer exe holds only
    # a copy of projects/templates, not engine/.
    raw = yaml.safe_load(_CATALOG_PATH.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _build_world(repo: Path, world: dict) -> tuple[World | None, str | None]:
    world_yaml_path = repo / "projects" / world["id"] / "world.yaml"
    genre = world.get("genre")
    action_graph_path = (
        repo / "templates" / genre / "action_graph.yaml"
        if genre and (repo / "templates" / genre / "action_graph.yaml").exists()
        else None
    )
    try:
        return World.from_yaml(world_yaml_path, action_graph_path=action_graph_path), None
    except (OSError, ValueError, yaml.YAMLError, KeyError, TypeError) as error:
        # World.__init__ indexes required keys directly (definition["name"],
        # ["protagonist"], item["name"], ...) and raises KeyError/TypeError
        # on a mid-edit world.yaml -- every other panel on this page already
        # degrades instead of raising, this tab must too.
        return None, str(error)


def world_facts(world: World) -> dict[str, str]:
    """世界固有の値。カタログ本文の {name} プレースホルダを埋める。"""

    def fmt(value: float) -> str:
        text = f"{value:g}"
        return text

    # Only values actually referenced by a {placeholder} in
    # engine/action_catalog.yaml belong here -- test_action_catalog.py
    # checks the reverse direction (every placeholder resolves) but an
    # unused key here is dead weight nothing catches.
    return {
        "companionship_threshold": fmt(world.companionship["threshold"]),
        "sacrifice_asset_base": fmt(world.sacrifice_rewards["asset_base"]),
        "sacrifice_bond_stress": fmt(world.sacrifice_rewards["bond_stress"]),
        "sacrifice_bond_phase": str(world.sacrifice_rewards["bond_phase"]),
        "negotiate_threshold": fmt(world.negotiate_threshold),
        "rethink_stagnation_slots": str(world.rethink_stagnation_slots),
    }


class _MissingPlaceholder(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _fill(text: str, facts: dict[str, str]) -> str:
    return text.format_map(_MissingPlaceholder(facts))


def _node_matches_genre(node: dict, world: World) -> bool:
    raw_genres = node.get("genres")
    if raw_genres is None or not world.genres:
        return True
    node_genres = {raw_genres} if isinstance(raw_genres, str) else set(raw_genres)
    return bool(world.genres.intersection(node_genres))


def _node_for(world: World, verb: str, node_when: str | None) -> dict | None:
    nodes = world.action_graph.get("nodes", []) or []
    for node in nodes:
        if not isinstance(node, dict) or node.get("verb") != verb:
            continue
        if node.get("when") != node_when:
            continue
        if _node_matches_genre(node, world):
            return node
    return None


def _subject_verb_index(repo: Path, world_id: str) -> tuple[set[str], dict[str, list[str]]]:
    subjects = world_graph.load_subjects(repo / "projects" / world_id)
    union: set[str] = set()
    by_verb: dict[str, list[str]] = {}
    for subject in subjects:
        subject_id = str(subject.get("id") or "")
        for verb in subject.get("verbs") or []:
            verb = str(verb)
            union.add(verb)
            by_verb.setdefault(verb, []).append(subject_id)
    return union, by_verb


def _verb_status(verb: str, world: World, subject_union: set[str]) -> str:
    if verb not in _engine_verb_methods():
        return "unimplemented"
    if not world.genre_allows(verb):
        return "pruned"
    if verb not in subject_union:
        return "unused"
    return "active"


_STATUS_BADGE = {
    "unimplemented": "未実装（構想のみ）",
    "pruned": "このジャンルでは無効（genres タグで枝刈り）",
    "unused": "この世界では未使用（どの人物の verbs にも無い）",
}


def _permission_line(world: World, verb: str) -> str:
    if not world.action_graph_enabled:
        return ""
    parts = []
    for role in ("hostile", "neutral", "ally"):
        level = world.action_permissions.get(verb, {}).get(role, "allow")
        if level == "restricted":
            level_text = f"restricted(×{world.permission_restricted_weight:g})"
        else:
            level_text = level
        parts.append(f"{_ROLE_LABELS[role]}={level_text}")
    return "permission: " + " ".join(parts)


def _kind_row(kind: dict, facts: dict[str, str], node: dict | None) -> str:
    label = _escape(kind.get("label") or kind.get("id") or "")
    if node is None:
        classification = '<span class="muted">このジャンルには対応ノードなし</span>'
    else:
        category = node.get("category")
        classification = _escape(f"{category if category is not None else '分類なし'} / {node.get('subtype', '')}")
    precondition = _escape(_fill(str(kind.get("precondition", "")), facts))
    target = _escape(_fill(str(kind.get("target", "")), facts))
    effect = _escape(_fill(str(kind.get("effect", "")), facts))
    return (
        "<tr>"
        f"<td><code>{label}</code></td>"
        f"<td>{classification}</td>"
        f"<td>{precondition}</td>"
        f"<td>{target}</td>"
        f"<td>{effect}</td>"
        "</tr>"
    )


def _verb_block(
    verb: str,
    entry: dict,
    world: World,
    facts: dict[str, str],
    subject_union: set[str],
    subject_by_verb: dict[str, list[str]],
) -> tuple[str, str]:
    """Returns (category_key_for_grouping, html)."""

    status = _verb_status(verb, world, subject_union)
    design = entry.get("design") or {}
    kinds = entry.get("kinds") or []

    resolved_category = None
    kind_rows = []
    for kind in kinds:
        node = None
        if status != "unimplemented":
            node = _node_for(world, verb, kind.get("node_when"))
        if node is not None and resolved_category is None:
            resolved_category = str(node.get("category")) if node.get("category") is not None else "none"
        kind_rows.append(_kind_row(kind, facts, node))

    group_key = resolved_category or str(design.get("category", "none"))

    summary_bits = [f"<strong>{_escape(verb)}</strong>"]
    if resolved_category is None:
        # No action_graph node matched anywhere for this verb in this genre
        # (unimplemented, or implemented but this genre never wires it up)
        # -- the design.slot fallback is the only classification we have.
        slot = _escape(str(design.get("slot", "")))
        if slot:
            summary_bits.append(f'<span class="muted">{slot}</span>')
    if status == "active":
        users = subject_by_verb.get(verb, [])
        summary_bits.append(f"使える: {_escape('・'.join(users)) if users else '（該当人物なし）'}")
        if verb not in _TARGETLESS_VERBS:
            permission_line = _permission_line(world, verb)
            if permission_line:
                summary_bits.append(_escape(permission_line))
    else:
        summary_bits.append(f'<span class="badge-unimplemented">{_STATUS_BADGE[status]}</span>')

    table = (
        '<table class="wb-table catalog-kinds"><thead><tr>'
        "<th>分岐</th><th>分類</th><th>前提</th><th>対象</th><th>効果</th>"
        f"</tr></thead><tbody>{''.join(kind_rows)}</tbody></table>"
        if kind_rows else '<p class="muted">分岐なし。</p>'
    )
    open_attr = " open" if status == "active" else ""
    css_class = "catalog-verb" if status == "active" else "catalog-verb is-off"
    html = (
        f'<details class="{css_class}"{open_attr}>'
        f"<summary>{' ｜ '.join(summary_bits)}</summary>"
        f"{table}</details>"
    )
    return group_key, html


def catalog_panel_html(world: dict, repo: Path) -> str:
    catalog = _load_catalog()
    engine_world, error = _build_world(repo, world)
    if engine_world is None:
        return f'<p class="muted">世界設定が読み込めません: {_escape(error or "")}</p>'

    facts = world_facts(engine_world)
    subject_union, subject_by_verb = _subject_verb_index(repo, world["id"])

    grouped: dict[str, list[str]] = {}
    counts = {"active": 0, "pruned": 0, "unused": 0, "unimplemented": 0}
    for verb, entry in sorted(catalog.get("verbs", {}).items()):
        status = _verb_status(verb, engine_world, subject_union)
        counts[status] += 1
        group_key, html = _verb_block(
            verb, entry, engine_world, facts, subject_union, subject_by_verb,
        )
        if status == "unimplemented":
            grouped.setdefault("__unimplemented__", []).append(html)
        else:
            grouped.setdefault(group_key, []).append(html)

    genre = world.get("genre") or "（未設定）"
    intro = (
        '<p class="muted">この世界で主体が選びうる行動の一覧。'
        f"使える人物・分類・数値はジャンル「{_escape(str(genre))}」の action_graph.yaml と"
        "この世界の設定から取っています。見出し（I〜VI）は代表的な分類、"
        "各行の「分類」列がこの世界での実際の判定です（同じ verb でも分岐ごとに変わることがあります）。"
        "前提／対象／効果はエンジンの挙動そのもの（ジャンル共通）で、{ } はこの世界固有の値です。</p>"
    )
    summary = (
        '<dl class="world-summary">'
        f"<dt>有効</dt><dd>{counts['active']}</dd>"
        f"<dt>枝刈り</dt><dd>{counts['pruned']}</dd>"
        f"<dt>未使用</dt><dd>{counts['unused']}</dd>"
        f"<dt>未実装</dt><dd>{counts['unimplemented']}</dd>"
        "</dl>"
    )

    categories = catalog.get("categories", {})
    order = sorted(
        (key for key in grouped if key != "__unimplemented__"),
        key=lambda key: categories.get(key, {}).get("order", 999),
    )
    sections = []
    for key in order:
        meta = categories.get(key, {})
        label = meta.get("label", key)
        desc = meta.get("desc", "")
        heading = f"<h3>{_escape(str(label))}</h3>"
        if desc:
            heading += f'<p class="muted">{_escape(str(desc))}</p>'
        sections.append(heading + "".join(grouped[key]))

    if "__unimplemented__" in grouped:
        sections.append(
            "<h3>未実装（構想のみ）</h3>" + "".join(grouped["__unimplemented__"])
        )

    return intro + summary + "".join(sections)
