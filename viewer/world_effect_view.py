"""段階4「拡張あり／なしの比較」(WB-WORLDGROW-001 段階4a)。

対は保存しない -- 両実験の凍結入力（config.json の project_id/template_id/
evolution、input-manifest.json の world.yaml エントリの source_sha256 と
world_patches）から毎回導出する。読み取り専用（一切書き込まない）。
"""
from __future__ import annotations

from urllib.parse import urlsplit

from viewer import data, pages, world_demand_view, world_graph, world_usage_badge

_escape = pages._escape
_url = pages._url_segment
_SAFE_ERRORS = (data.MissingResource, OSError, ValueError, KeyError, TypeError, AttributeError)


def _read_json_safe(repository, experiment, name):
    try:
        path = repository.safe_path(experiment, name)
    except _SAFE_ERRORS:
        return None
    if not path.is_file():
        return None
    try:
        return data._read_json(path)
    except (OSError, ValueError):
        return None


def _world_entry(config, manifest):
    """input-manifest.json内の world.yaml の記録（source_sha256/world_patches）。"""
    project_id = config.get("project_id") if isinstance(config, dict) else None
    if not project_id or not isinstance(manifest, dict):
        return None
    key = f"projects/{project_id}/world.yaml"
    for entry in manifest.get("files") or []:
        if isinstance(entry, dict) and entry.get("path") == key:
            return entry
    return None


# 探索そのものを左右するキーだけ（Opus review R2/R3）。world_expansion は対の
# 判定そのもの、kappa は別枠の警告なので、どちらもここには含めない。
# processes（並列度）・keep／record_explanations（保存方針）は探索結果を
# 変えないので、条件不一致の警告対象に含めると無害な差でも警告が出てしまう。
_SEARCH_CONDITION_KEYS = frozenset({
    "seed_base", "ga_seed", "generations", "population", "seeds",
    "target_ending", "meta_evolution", "coevolve",
    # WB-ROUTE-001 S4 §2: unlike kappa (a post-hoc judge veto), route_rho
    # weights the search itself -- a differing rho is a differing search
    # condition, so it belongs in this set rather than kappa's own separate
    # "shared table accumulation" warning below.
    "route_rho",
})


def _condition(evolution):
    if not isinstance(evolution, dict):
        return {}
    return {k: v for k, v in evolution.items() if k in _SEARCH_CONDITION_KEYS}


def _patch_ids(entry):
    # Opus review R4: 対の判定はここ（input-manifest.json の world_patches）を
    # 正とする。「何が足されたか」の表示（expansion_line）は別ソース（凍結
    # world.yaml の expansion.patches, data.world_expansion_state 経由）を使う
    # -- execution/configs.py._capture_inputs が両方を同時に書くので通常は
    # 一致するが、片方だけ欠けた壊れた実験では役割表示と一致しないことがある。
    return tuple(p.get("id") for p in (entry or {}).get("world_patches") or [] if isinstance(p, dict))


def find_partners(repository, run_name):
    """(role, config, partners) を返す。

    role は "expand"（この実験は拡張ずみ）／"base"（拡張なし）／None（凍結入力に
    世界の記録が無く比較できない）。partners は対になりうる実験のリストで、
    それぞれ {"run_name", "warnings"}（warnings は空なら条件も完全一致）。
    """
    experiment = world_demand_view.resolve_root(repository, run_name)
    if experiment is None:
        return None, None, []
    config = _read_json_safe(repository, experiment, "config.json")
    manifest = _read_json_safe(repository, experiment, "input-manifest.json")
    entry = _world_entry(config, manifest)
    if not entry or not entry.get("source_sha256"):
        return None, config, []
    source_sha = entry["source_sha256"]
    my_patches = _patch_ids(entry)
    role = "expand" if my_patches else "base"
    project_id, template_id = config.get("project_id"), config.get("template_id")
    condition = _condition(config.get("evolution"))
    kappa = (config.get("evolution") or {}).get("kappa")

    # ponytail: 実験ディレクトリを毎回全走査する O(n) 探索。実験数が増えて
    # 遅くなったら、対の鍵（project_id, source_sha256）で絞った索引に変える。
    partners = []
    for other_name, other_root in repository.experiments():
        if other_root == experiment:
            continue
        other_config = _read_json_safe(repository, other_root, "config.json")
        if not isinstance(other_config, dict):
            continue
        if other_config.get("project_id") != project_id or other_config.get("template_id") != template_id:
            continue
        other_manifest = _read_json_safe(repository, other_root, "input-manifest.json")
        other_entry = _world_entry(other_config, other_manifest)
        if not other_entry or other_entry.get("source_sha256") != source_sha:
            continue
        other_patches = _patch_ids(other_entry)
        if bool(other_patches) == bool(my_patches):
            continue  # どちらも拡張あり／どちらも拡張なし、では対にならない
        warnings = []
        if _condition(other_config.get("evolution")) != condition:
            warnings.append("探索条件（世代数・個体数・seedなど）が異なります")
        if kappa is not None or (other_config.get("evolution") or {}).get("kappa") is not None:
            warnings.append("合理性(κ)ありの実験は共有テーブルの蓄積により厳密には比較できません")
        base_root = experiment if not my_patches else other_root
        if data.world_demand(repository, base_root) is None:
            warnings.append("ベース側が「検知のみ」で回っていないため、きっかけの空振り率は比較できません")
        partners.append({"run_name": other_name, "warnings": warnings})
    partners.sort(key=lambda p: bool(p["warnings"]))
    return role, config, partners


def _trigger_maps(report):
    """(whiff, ignorance, blocked) キー付き辞書。キーは whiff=(zone, verb)、
    ignorance=zone、blocked=requirement。route の無い実験では ignorance/
    blocked は常に空（world_demand.collect() が route_counts/blocked_counts
    を空で返すのと同じ理由）。"""
    whiff, ignorance, blocked = {}, {}, {}
    for t in data._as_list((report or {}).get("triggers")):
        if not isinstance(t, dict):
            continue
        kind = t.get("kind", "whiff")
        if kind == "whiff" and t.get("zone") and t.get("verb"):
            whiff[(t["zone"], t["verb"])] = t
        elif kind == "ignorance" and t.get("zone"):
            ignorance[t["zone"]] = t
        elif kind == "blocked" and t.get("requirement"):
            blocked[t["requirement"]] = t
    return whiff, ignorance, blocked


def _whiff_side(trigger):
    if not isinstance(trigger, dict):
        return "—"
    count, whiffs = data._number(trigger.get("count")), data._number(trigger.get("whiffs"))
    if count <= 0:
        return "0回"
    return f"{count:.0f}回中{whiffs:.0f}回（{whiffs / count * 100:.1f}%）"


def _whiff_table_html(base_report, expand_report):
    before_map, after_map = _trigger_maps(base_report)[0], _trigger_maps(expand_report)[0]
    keys = sorted(set(before_map) | set(after_map))
    if not keys:
        return "<p>比較できるきっかけの記録がありません。</p>"
    rows = "".join(
        f"<tr><td>{_escape(zone)}</td><td>{_escape(verb)}</td>"
        f"<td>{_whiff_side(before_map.get((zone, verb)))}</td>"
        f"<td>{_whiff_side(after_map.get((zone, verb)))}</td></tr>"
        for zone, verb in keys
    )
    return ('<table class="wb-table"><thead><tr><th>場所</th><th>行動</th>'
            f"<th>ベース</th><th>拡張後</th></tr></thead><tbody>{rows}</tbody></table>")


def _route_totals(report):
    """(lost_total, route_total_all) from report["route_counts"] -- raw and
    unfiltered by world_demand.py's own trigger thresholds (R3, 段階3 review
    1)."""
    counts = (report or {}).get("route_counts") or {}
    lost_total = sum(z.get("lost", 0) for z in counts.values() if isinstance(z, dict))
    route_total_all = sum(
        sum(v for v in z.values() if isinstance(v, (int, float)))
        for z in counts.values() if isinstance(z, dict)
    )
    return lost_total, route_total_all


def _ignorance_from_report(report, zone):
    """R3 (段階3 review 1): a zone whose ignorance count is under
    world_demand.py's own threshold (never became -- or stopped being -- a
    trigger) still has real numbers in report["route_counts"]. Read them
    directly so "0回" and "しきい値未満で表に出ない" don't both render as
    the same "—"."""
    counts = ((report or {}).get("route_counts") or {}).get(zone)
    if not isinstance(counts, dict):
        return {"count": 0, "total": 0, "share": None}
    total = sum(v for v in counts.values() if isinstance(v, (int, float)))
    count = counts.get("detour:ignorance", 0)
    return {"count": count, "total": total, "share": round(count / total, 4) if total else None}


def _blocked_from_report(report, requirement):
    """R3 の blocked 版。blocked_counts は runs/count のみ持つので、
    share/lost_share は route_counts から world_demand.py と同じ式で
    再計算する(collect() 自体は変えない、表示側の純関数)。"""
    entry = ((report or {}).get("blocked_counts") or {}).get(requirement)
    count = entry.get("count", 0) if isinstance(entry, dict) else 0
    runs = entry.get("runs") if isinstance(entry, dict) else None
    lost_total, route_total_all = _route_totals(report)
    return {
        "count": count, "runs": runs,
        "share": round(count / lost_total, 4) if lost_total else None,
        "lost_share": round(count / route_total_all, 4) if route_total_all else None,
    }


def _ignorance_side(trigger):
    if not isinstance(trigger, dict):
        return "—"
    count = data._number(trigger.get("count"))
    if count <= 0:
        return "0回"
    share = trigger.get("share")
    if isinstance(share, (int, float)):
        return f"{count:.0f}回（道筋付き決定の{share * 100:.1f}%）"
    return f"{count:.0f}回"


def _ignorance_table_html(base_report, expand_report):
    # WB-WORLDGROW-002 stage 3: route の無い実験では常に空 map 同士なので
    # 何も出力しない（きっかけの表の出力を変えない）。
    before_map, after_map = _trigger_maps(base_report)[1], _trigger_maps(expand_report)[1]
    zones = sorted(set(before_map) | set(after_map))
    if not zones:
        return ""
    rows = "".join(
        f"<tr><td>{_escape(zone)}</td>"
        f"<td>{_ignorance_side(before_map.get(zone) or _ignorance_from_report(base_report, zone))}</td>"
        f"<td>{_ignorance_side(after_map.get(zone) or _ignorance_from_report(expand_report, zone))}</td></tr>"
        for zone in zones
    )
    return ('<h3>手探りは減ったか</h3><table class="wb-table"><thead><tr><th>場所</th>'
            f"<th>ベース</th><th>拡張後</th></tr></thead><tbody>{rows}</tbody></table>")


def _blocked_side(trigger):
    if not isinstance(trigger, dict):
        return "—"
    count = data._number(trigger.get("count"))
    if count <= 0:
        return "0回"
    # Required 4 (段階3 review 1): world_demand.py の需要トリガーの
    # share=count/lost_total（「見通しなし全体」の中でのこの要件の割合）と
    # lost_share=count/道筋付き決定の総数（M4）は意味が違う -- 前のコードは
    # lost_share の値を「見通しなし全体の」というラベルで出していて、実データ
    # (share≈1.0・lost_share≈0.11)では実態の逆に読めた。両方を正しい
    # ラベルで出す。
    runs, share, lost_share = trigger.get("runs"), trigger.get("share"), trigger.get("lost_share")
    extra = []
    if isinstance(runs, (int, float)):
        extra.append(f"{runs:.0f}本のラン")
    if isinstance(share, (int, float)):
        extra.append(f"見通しなし全体の{share * 100:.1f}%")
    if isinstance(lost_share, (int, float)):
        extra.append(f"道筋付き決定の{lost_share * 100:.1f}%")
    detail = "、".join(extra)
    return f"{count:.0f}回（{detail}）" if detail else f"{count:.0f}回"


def _blocked_table_html(base_report, expand_report):
    before_map, after_map = _trigger_maps(base_report)[2], _trigger_maps(expand_report)[2]
    requirements = sorted(set(before_map) | set(after_map))
    if not requirements:
        return ""
    rows = "".join(
        # Required 3 (段階4 review 1): 生の "has_item:縄" ではなく
        # world_demand_view._requirement_phrase の読みやすい句を出す。
        f"<tr><td>{world_demand_view._requirement_phrase(requirement)}</td>"
        f"<td>{_blocked_side(before_map.get(requirement) or _blocked_from_report(base_report, requirement))}</td>"
        f"<td>{_blocked_side(after_map.get(requirement) or _blocked_from_report(expand_report, requirement))}</td></tr>"
        for requirement in requirements
    )
    return ('<h3>計画が立たない状態は減ったか</h3><table class="wb-table"><thead><tr><th>要件</th>'
            f"<th>ベース</th><th>拡張後</th></tr></thead><tbody>{rows}</tbody></table>")


def _last_generation(summary):
    rows = [g for g in data._as_list((summary or {}).get("generations")) if isinstance(g, dict)]
    return rows[-1] if rows else {}


def _trend_html(base_summary, expand_summary):
    b, e = _last_generation(base_summary), _last_generation(expand_summary)

    def rate(row):
        value = data._number(row.get("reach_rate"), None)
        return f"{value * 100:.1f}%" if value is not None else "—"

    return (
        '<table class="wb-table"><thead><tr><th></th><th>結末到達率</th><th>物語の種類数</th></tr></thead><tbody>'
        f'<tr><td>ベース</td><td>{rate(b)}</td><td>{_escape(b.get("occupied_cells", "—"))}</td></tr>'
        f'<tr><td>拡張後</td><td>{rate(e)}</td><td>{_escape(e.get("occupied_cells", "—"))}</td></tr>'
        "</tbody></table>"
    )


def _map_diff_html(repository, base_root, expand_root, expand_name, expand_state, protagonist):
    try:
        base_cells = set((repository.archive(base_root) or {}).get("cells") or {})
        expand_archive = repository.archive(expand_root) or {}
        expand_cells_map = expand_archive.get("cells") or {}
        expand_cells = set(expand_cells_map)
    except _SAFE_ERRORS:
        return "<p>地図の記録を読み込めませんでした。</p>"
    only_expand = sorted(expand_cells - base_cells)
    only_base = sorted(base_cells - expand_cells)
    both = base_cells & expand_cells
    # 品質(quality)は出さない -- ゾーン追加で最大ホップ数が変わり、正規化の
    # 基準が実験ごとに異なるため、占有の有無だけを比べる（段階4設計メモ G7）。
    parts = [f"<p>両方に出た型: {len(both)}種類／拡張後だけに出た型: {len(only_expand)}種類／"
             f"ベースだけに出た型: {len(only_base)}種類</p>"]
    if only_expand:
        # 段階4b: 新しく出た型が本当に拡張要素で生まれたかを、代表個体の
        # ログから読み取ったバッジで添える（保存しない、都度導出）。
        patches = expand_state.get("patches") if isinstance(expand_state, dict) else None
        links = "".join(
            f'<li><a href="/exp/{_url(expand_name)}/cell/{_url(cell)}">{_escape(cell.replace("|", " × "))}</a>'
            f' {world_usage_badge.cell_badge_html(repository, expand_root, patches, expand_cells_map.get(cell), protagonist)}'
            "</li>"
            for cell in only_expand
        )
        parts.append(f"<details><summary>拡張後だけに出た型（{len(only_expand)}）</summary><ul>{links}</ul></details>")
    return "".join(parts)


def _no_partner_html(config, role):
    project_id = (config or {}).get("project_id")
    hint = ("実行設定の「世界の拡張」を「検知のみ」にして、同じ条件で回してください。" if role == "expand"
            else "実行設定の「世界の拡張」で「承認済みの拡張を適用」を選んで、同じ条件で回してください。")
    href = f"/configs/new?project={_url(project_id)}" if project_id else "/configs/new"
    return (f'<p class="rw-empty">この実験と対になる実験がまだありません。'
            f'<a href="{_escape(href)}">同じ世界で新しく実験を回す →</a> {_escape(hint)}</p>')


def effect_html(handler, view, query):
    """観測画面「拡張の効果」タブの中身。query は run_workspace.render() が
    すでに parse_qs 済みの dict（"with" で対の実験を選べる）。"""
    run_name = (view or {}).get("run_name")
    if not run_name:
        return '<p class="rw-empty">実験がまだ保存されていません。</p>'
    repository = handler.repository
    role, config, partners = find_partners(repository, run_name)
    if role is None:
        return '<p class="rw-empty">凍結入力に世界の記録が無いため、比較できません（旧形式の実験など）。</p>'
    if not partners:
        return _no_partner_html(config, role)

    names = [p["run_name"] for p in partners]
    chosen = query.get("with", [None])[0]
    if chosen not in names:
        chosen = names[0]
    partner = next(p for p in partners if p["run_name"] == chosen)
    base_name, expand_name = (chosen, run_name) if role == "expand" else (run_name, chosen)
    base_root = world_demand_view.resolve_root(repository, base_name)
    expand_root = world_demand_view.resolve_root(repository, expand_name)
    if base_root is None or expand_root is None:
        return '<p class="rw-empty">対の実験を読み込めませんでした。</p>'

    switcher = ""
    if len(partners) > 1:
        # サーバー描画のパネルなので、JSの無い<select>ではなく直接
        # ?with=<name> へ飛ぶリンクの一覧にする（Opus review M1: <select>の
        # change をrun-workspace.jsのどのハンドラも拾っておらず無反応だった）。
        current_path = urlsplit(handler.path).path
        items = "".join(
            f'<li>{"<strong>" if p["run_name"] == chosen else ""}'
            f'<a href="{_escape(current_path)}?tab=effect&with={_url(p["run_name"])}">{_escape(p["run_name"])}</a>'
            f'{"</strong>" if p["run_name"] == chosen else ""}'
            f'{"（条件に注意）" if p["warnings"] else ""}</li>'
            for p in partners
        )
        switcher = f'<p>対の実験を選ぶ:</p><ul class="rw-effect-partners">{items}</ul>'
    other_label = "ベース" if role == "expand" else "拡張後"
    warnings_html = "".join(f'<p class="muted">⚠ {_escape(w)}</p>' for w in partner["warnings"])
    header = (f'<p>対の実験: <strong>{_escape(chosen)}</strong>（{other_label}）'
              f' <a href="/exp/{_url(chosen)}/monitor">実験を開く ↗</a></p>{switcher}{warnings_html}')

    base_report = data.world_demand(repository, base_root)
    expand_report = data.world_demand(repository, expand_root)
    base_summary = _read_json_safe(repository, base_root, "summary.json")
    expand_summary = _read_json_safe(repository, expand_root, "summary.json")
    expand_state = data.world_expansion_state(repository, expand_root)
    expand_config = config if role == "expand" else _read_json_safe(repository, expand_root, "config.json")
    protagonist = ((expand_config or {}).get("preview") or {}).get("protagonist")
    # Opus review R1: expansion_line() は「この実験の世界」という一人称の
    # 文言なので、role=="base"（開いているのは相手側）のときは誤読を招く。
    # 拡張側の実験名を明示する。
    added_heading = "何が足されたか" if role == "expand" else f"何が足されたか（{_escape(expand_name)}側）"

    return (
        '<section class="card"><h2>拡張の効果</h2>'
        '<p class="muted">同じseedでも候補集合が変わるため物語は一致しません。分布としての比較です。</p>'
        + header
        + "<h3>きっかけは解消したか</h3>" + _whiff_table_html(base_report, expand_report)
        + _ignorance_table_html(base_report, expand_report)
        + _blocked_table_html(base_report, expand_report)
        + "<h3>物語はどう変わったか（最終世代）</h3>" + _trend_html(base_summary, expand_summary)
        + "<h3>地図の差分</h3>" + _map_diff_html(repository, base_root, expand_root, expand_name, expand_state, protagonist)
        + f"<h3>{added_heading}</h3>" + world_demand_view.expansion_line(expand_state)
        + _frozen_world_map_html(expand_config)
        + "</section>"
    )


def _frozen_world_map_html(expand_config):
    """段階4c-最小: 凍結された（拡張後の）世界を地図として見る。世界画面
    本体は変えない（v1制約=常にベースworld.yamlを正として表示、を維持）。
    追加I/Oなし -- expand_config["preview"]["world"] は expand 実験では
    execution/configs.py._capture_inputs が既に拡張後の世界を焼き込んでいる。"""
    world_yaml = ((expand_config or {}).get("preview") or {}).get("world")
    if not isinstance(world_yaml, dict):
        return ""
    zones = world_yaml.get("zones") or []
    routes = world_yaml.get("routes") or {}
    return ("<details><summary>回った世界を地図で見る（拡張後）</summary>"
            + world_graph.zone_list_html(zones) + world_graph.zone_svg(zones, routes)
            + "</details>")
