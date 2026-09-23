"""Execution observer: live job state and immutable generation observations.

No computation is started here. The replay/river algorithms belong to the
existing GAVIZ modules; this module only bounds them to a published snapshot.
"""
from __future__ import annotations

import json
from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

import yaml

from execution.provenance import ConfigError
from execution.worker import TERMINAL
from viewer import data, pages, ga_replay, lineage_river, world_demand_view, world_effect_view, world_expansion_view


TABS = (("overview", "概要"), ("replay", "進化のリプレイ"),
        ("river", "系譜の川"), ("trends", "世代の推移"), ("demand", "世界の需要と拡張"),
        ("effect", "拡張の効果"))
READ_ERRORS = (ConfigError, OSError, ValueError, KeyError, TypeError, data.MissingResource, yaml.YAMLError)
E = pages._escape
U = pages._url_segment


def _integer(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def river_payload(model):
    """Retain all nodes/edges; only the presentation decides what to emphasize."""
    key = lambda pair: f"{pair[0]}:{pair[1]}"
    diagram = model["layout"]
    elite_ids = {key(node): cell for cell, node in model["elite_nodes"].items()}
    nodes = []
    for pair, node in model["index"].items():
        position = diagram["positions"].get(pair)
        nodes.append({
            "id": key(pair), "generation": pair[0], "index": pair[1],
            "cell": node.get("cell_key"), "quality": node.get("quality"),
            "parents": node.get("parent_refs", []), "position": position,
            "survives": sorted(model["survivor_map"].get(pair, [])),
            "elite": elite_ids.get(key(pair)),
        })
    return {
        "nodes": nodes,
        "edges": [{"parent": key(edge["parent"]), "child": key(edge["child"]),
                   "twice": bool(edge.get("twice"))} for edge in model["edges"]],
        "bands": diagram["bands"], "bars": diagram["bars"],
        "counts": model["counts"], "elites": {cell: key(n) for cell, n in model["elite_nodes"].items()},
    }


def observation(handler, view, generation=None):
    """Capture one publication, never scan beyond its committed generation."""
    job = view.get("job") or {}
    run_name = view.get("run_name")
    categories, bins = view["axes"]
    result = {"generation": None, "latest": None, "revision": job.get("publication_revision"),
              "generations": [], "cells": {}, "categories": list(categories), "bins": list(bins),
              "replay": None, "river": None, "candidate_count": 0,
              "historical": False, "notice": "", "run_name": run_name}
    snapshot = None
    snapshot_root = None
    catalog = handler.repository.catalog
    if catalog is not None and job.get("run_id") and job.get("publication_revision"):
        try:
            root, legacy = catalog.resolve(job["run_id"])
            revision = int(job["publication_revision"])
            if not legacy:
                snapshot_root = root
                run_name = result["run_name"] = root.name
                latest = revision - 1
                selected = latest if generation is None else min(max(0, generation), latest)
                snapshot = ga_replay._snapshot(catalog, job["run_id"], root, selected + 1)
                result.update(generation=selected, latest=latest, historical=True)
                # Trend data always includes the most recent committed summary.
                latest_snapshot = snapshot if selected == latest else ga_replay._snapshot(
                    catalog, job["run_id"], root, revision)
                result["generations"] = [dict(row, generation=row.get("generation", i))
                    for i, row in enumerate(latest_snapshot.get("summary", {}).get("generations") or [])
                    if isinstance(row, dict) and type(row.get("generation", i)) is int
                    and 0 <= row.get("generation", i) <= latest]
                result["candidate_count"] = len((latest_snapshot.get("candidates") or {}).get("candidates") or [])
                # Axes captured with the run, not the mutable template in the repo.
                template_id = (view.get("config") or {}).get("template_id")
                if template_id:
                    captured_template = handler.repository.safe_path(root, f"inputs/templates/{template_id}")
                    if (captured_template / "qd.yaml").is_file():
                        captured_axes = data.qd_axes(captured_template)
                        result["categories"], result["bins"] = map(list, captured_axes)
                result["replay"] = ga_replay.replay_model(
                    handler, job, (result["categories"], result["bins"]), selected)
                result["replay_html"] = ga_replay.render_panel(result["replay"], urlsplit(handler.path).path)
        except READ_ERRORS as error:
            import sys
            print(f"run_workspace: {type(error).__name__}: {error}", file=sys.stderr)
            result["notice"] = "保存済みの世代記録を読み込めません。進捗の確認は続けています。"
            return result
    if snapshot is None and job.get("job_id"):
        # A running generation with no publication is not a readable record.
        result["notice"] = ("最初の世代が保存されると表示されます。" if job.get("state") not in TERMINAL
                            else "この実行には保存済みの世代記録がありません。")
        return result
    if snapshot is None and run_name:
        try:
            root = handler.repository.experiment(run_name)
            meta = data.experiment_meta(handler.repository, root)
            summary_path = handler.repository.safe_path(root, "summary.json")
            summary = data._read_json(summary_path) if summary_path.is_file() else {}
            snapshot = {"archive": handler.repository.archive(root), "summary": summary}
            result["generations"] = summary.get("generations") or []
            result["categories"], result["bins"] = list(meta["categories"]), list(meta["bins"])
            numbers = [g.get("generation") for g in result["generations"] if type(g.get("generation")) is int]
            result["generation"] = result["latest"] = max(numbers, default=None)
            result["notice"] = "旧実行の保存結果です。世代を指定したリプレイには対応していません。"
        except READ_ERRORS:
            result["notice"] = "この実行には読み取れる世代記録がありません。"
            return result
    if snapshot is None:
        return result
    archive = snapshot.get("archive") or {}
    result["cells"] = {cell: {"quality": value.get("quality"), "generation": value.get("generation")}
                       for cell, value in (archive.get("cells") or {}).items() if isinstance(value, dict)}
    if run_name:
        try:
            result["river"] = river_payload(lineage_river.river_model(
                handler.repository, run_name, max_generation=result["generation"], snapshot_archive=archive,
                snapshot_axes=(result["categories"], result["bins"]), snapshot_root=snapshot_root))
        except READ_ERRORS:
            pass
    return result


def _world_state(handler, view):
    """(experiment dir, world_expansion_state) read once per page render; either
    may be None when the run isn't saved yet or can't be read."""
    experiment = world_demand_view.resolve_root(handler.repository, view.get("run_name"))
    if experiment is None:
        return None, None
    try:
        return experiment, data.world_expansion_state(handler.repository, experiment)
    except READ_ERRORS:
        return experiment, None


def _world_row_html(handler, state, setting):
    """「世界の拡張」の1行: 選んだ設定と、実際に回った世界（ベース/拡張/不明）。
    拡張ありなら5つ目のタブへリンクする。"""
    from viewer import workbench_pages as wb
    parts = [E(f"設定: {wb._world_expansion_label(setting or 'off')}")]
    if state is not None:
        parts.append(E(f"回った世界: {world_demand_view.summary_text(state)}"))
        if state.get("state") == "expanded":
            href = urlsplit(handler.path).path + "?tab=demand"
            parts.append(f'<a href="{E(href)}">世界の需要と拡張を見る →</a>')
    return "<br>".join(parts)


def _expansion_project(handler, view):
    """project_dir for the world this run's config names, or None for a
    legacy run with no config (nothing to resolve a world from) --
    WB-WORLDGROW-001 段階3b-1. Only project_dir is returned: the sole caller
    never needed template_dir (R5, Opus review)."""
    config = view.get("config") or {}
    project_id, template_id = config.get("project_id"), config.get("template_id")
    if not project_id or not template_id:
        return None
    job_store = getattr(handler.server, "job_store", None)
    repo = job_store.configs.repo if job_store is not None else data.ROOT
    project_dir = repo / "projects" / project_id
    if not project_dir.is_dir():
        return None
    return project_dir


def _proposals_html(handler, view, run_name):
    """「この実験から生まれた提案」節: この実験がトリガーとなった提案だけを
    proposal_card で並べ、他の提案・承認済みは件数だけ世界の画面へ逃がす。

    節の先頭に world-patch ジョブの進行表示の器（data-patch-job）を置く --
    中身は viewer/static/world-expansion.js が GET /api/jobs で埋める
    （段階3b-3）。project_dir が解決できない実験には出さない。"""
    project_dir = _expansion_project(handler, view)
    if project_dir is None:
        return ""
    world_id = (view.get("config") or {}).get("project_id")
    job_panel = (
        f'<div class="card" data-patch-job data-run="{E(run_name)}" hidden>'
        '<p role="status"></p>'
        '<button type="button" data-patch-job-cancel>停止</button></div>'
    )
    try:
        state = world_expansion_view.load(project_dir)
        world_yaml = yaml.safe_load((project_dir / "world.yaml").read_text(encoding="utf-8"))
    except READ_ERRORS:
        return job_panel + '<p class="rw-empty">世界の拡張を読み込めませんでした。</p>'
    if not isinstance(world_yaml, dict):
        world_yaml = {}
    can_write = getattr(handler.server, "job_store", None) is not None
    mine = [p for p in state["proposed"]
            if isinstance(p.get("patch"), dict) and (p["patch"].get("trigger") or {}).get("experiment") == run_name]
    approved_mine = [a for a in state["approved"] if a.get("experiment") == run_name]
    other_count = len(state["proposed"]) - len(mine) + (len(state["approved"]) - len(approved_mine))

    parts = [job_panel, "<h3>この実験から生まれた提案</h3>"]
    if state.get("error"):
        parts.append(f'<p class="rw-empty">拡張の記録を読み込めませんでした: {E(state["error"])}</p>')
        return "".join(parts)
    if not mine:
        parts.append("<p>この実験から生まれた提案はまだありません。</p>")
    else:
        parts.extend(world_expansion_view.proposal_card(p, world_yaml, world_id=world_id, can_write=can_write,
                                                         run_id=run_name)
                     for p in mine)
    if approved_mine:
        titles = "、".join(f'『{E(a["patch"].get("title"))}』' for a in approved_mine)
        parts.append(f"<p>承認済み: {titles}</p>"
                     f'<p><a href="/configs/new?project={U(world_id)}">この拡張を適用して次の実験を回す →</a> '
                     '実行設定の「世界の拡張」で「承認済みの拡張を適用」を選んでください。</p>')
    if other_count:
        parts.append(f'<p>この世界には他に {other_count} 件の提案・承認済みの拡張があります。'
                     f'<a href="/worlds/{U(world_id)}">世界の画面で見る →</a></p>')
    return "".join(parts)


def _usage_html(handler, view, state, experiment):
    """「この実験での拡張の使われ方」節（WB-WORLDGROW-001 段階5a）: この実験が
    expand で回っていれば、適用パッチごとに代表個体の使用状況と枯れ候補
    バッジを出し、can_write なら「枯らす」フォームを添える。

    対象は凍結世界の expansion.patches（この実験が実際に回った拡張、名前
    だけ）と現在の承認スタックの適用中パッチの積集合 -- すでに淘汰済み、
    またはこの実験のあとに承認された（この実験では回っていない）パッチは
    出さない。"""
    if state is None or state.get("state") != "expanded" or experiment is None:
        return ""
    project_dir = _expansion_project(handler, view)
    if project_dir is None:
        return ""
    config = view.get("config") or {}
    protagonist = (config.get("preview") or {}).get("protagonist")
    if not protagonist:
        return ""
    frozen_ids = {p.get("id") for p in state.get("patches") or [] if isinstance(p, dict)}
    if not frozen_ids:
        return ""
    try:
        expansion_state = world_expansion_view.load(project_dir)
    except READ_ERRORS:
        return ""
    if expansion_state.get("error"):
        return ""
    active = [a["patch"] for a in expansion_state.get("approved") or []
              if isinstance(a.get("patch"), dict) and a["patch"].get("id") in frozen_ids]
    if not active:
        return ""
    from gapengine.world_patch_usage import patch_usage, wither_candidates
    try:
        # M1 (Opus review): a ConfigStore-prepared experiment (the only kind
        # the screen can ever expand-run) has no top-level archive.json --
        # only published/<revision>/archive.json. handler.repository.archive()
        # already resolves that (viewer.data.RunRepository.archive(), the
        # same catalog-verified path every other candidate/archive read on
        # this screen uses) instead of gapengine.world_patch_usage's own
        # unverified published/ fallback (which exists for the CLI, which
        # has no RunRepository to hand).
        usage = patch_usage(experiment, protagonist, active, archive=handler.repository.archive(experiment))
    except READ_ERRORS:
        return ""
    candidates = set(wither_candidates(usage))
    world_id = config.get("project_id")
    can_write = getattr(handler.server, "job_store", None) is not None
    parts = ['<section class="card we-usage-panel"><h3>この実験での拡張の使われ方</h3><ul>']
    for patch in active:
        pid = patch.get("id")
        counts = usage.get(pid) or {"elites_total": 0, "elites_strong": 0, "elites_weak": 0}
        badge = ' <span class="we-status we-wither">枯れ候補</span>' if pid in candidates else ""
        parts.append(
            f'<li data-patch-card="{E(pid)}"><strong>{E(patch.get("title"))}</strong>{badge}'
            f'<p class="muted">代表個体{E(counts["elites_total"])}体中 '
            f'強い使用{E(counts["elites_strong"])}体・弱い使用{E(counts["elites_weak"])}体</p>'
        )
        if can_write:
            parts.append(
                '<div class="we-actions">'
                '<textarea data-retire-reason placeholder="枯らす理由（10文字以上）" minlength="10"></textarea>'
                f'<button type="button" data-patch-action="retire" data-world="{E(world_id)}" '
                f'data-patch="{E(pid)}" data-experiment="{E(experiment.name)}" '
                f'data-head="{E(expansion_state.get("head"))}">枯らす</button>'
                '<p class="we-message" data-patch-message role="alert"></p></div>'
            )
        parts.append("</li>")
    parts.append("</ul></section>")
    return "".join(parts)


def _propose_run_and_reason(handler, view):
    """(propose_run, reason) for the demand tab's "拡張を提案させる" button --
    WB-WORLDGROW-001 段階3b-3. propose_run is only set when a proposal has a
    plausible chance of being accepted (the server's POST .../world-patch
    still has the final say and can 409/422/503); otherwise reason explains
    why not, in the same two cases run_catalog.py's admit() rejects first."""
    if view is None:
        return None, None
    can_write = getattr(handler.server, "job_store", None) is not None
    if not can_write:
        return None, "閲覧モードでは提案できません"
    if not view.get("config"):
        return None, "凍結入力の無い実験からは提案できません"
    if _expansion_project(handler, view) is None:
        return None, "この実験の世界が見つからないため、提案できません"
    return view.get("run_name"), None


def _demand_html(handler, experiment, state, view=None):
    if experiment is None:
        return '<p class="rw-empty">実験がまだ保存されていません。</p>'
    propose_run, reason = _propose_run_and_reason(handler, view)
    try:
        block = world_demand_view.demand_block(handler.repository, experiment, state, propose_run=propose_run)
    except READ_ERRORS:
        return '<p class="rw-empty">世界の需要を読み込めませんでした。</p>'
    if reason:
        block += f'<p class="muted">{E(reason)}</p>'
    if view is None:
        return block
    return block + _usage_html(handler, view, state, experiment) + _proposals_html(handler, view, view.get("run_name"))


def _effect_html(handler, view, query):
    try:
        return world_effect_view.effect_html(handler, view, query)
    except READ_ERRORS:
        return '<p class="rw-empty">拡張の効果を読み込めませんでした。</p>'


def _condition_html(handler, view, control=None, state=None):
    config = view.get("config") or {}
    preview = config.get("preview") or {}
    ev = config.get("evolution") or {}
    name = preview.get("world_name") or view["world"]["name"]
    endings = preview.get("target_endings") or ev.get("target_ending") or []
    labels = {e["id"]: e.get("label", e["id"]) for e in (preview.get("world") or {}).get("ending", [])}
    ending = "、".join(labels.get(e, e) for e in ([endings] if isinstance(endings, str) else endings)) or "記録なし"
    size = (f"{ev['generations']}世代 × {ev['population']}個体 × 各{ev['seeds']}回" if ev else "記録なし")
    rows = [("世界", name), ("目指す結末", ending), ("探索規模", size),
            ("結果の保存", {"all": "すべて", "reached": "結末に到達した結果", "none": "ログを保存しない"}.get(ev.get("keep"), "実行時の条件を参照"))]
    if config and ev.get("kappa") is not None and control is not None:
        # WB-JEV-002: only shown when the saved config actually has a kappa
        # value (i.e. its genre has rationality.yaml) -- reuses the config
        # detail page's own frozen-snapshot summary so this can never
        # disagree with what the run actually did.
        from viewer import workbench_pages as wb
        rows.append(("合理性 κ", wb._rationality_summary(wb._frozen_template_dir(control, config), ev)))
    cid = config.get("config_id")
    dl_items = "".join(f"<dt>{E(k)}</dt><dd>{E(v)}</dd>" for k, v in rows)
    dl_items += f"<dt>世界の拡張</dt><dd>{_world_row_html(handler, state, ev.get('world_expansion'))}</dd>"
    return ('<aside class="rw-conditions" id="rw-conditions"><h2>今回の条件</h2><dl>'
            + dl_items + "</dl>"
            + (f'<a href="/configs/{U(cid)}">実行時の条件を見る ↗</a>' if cid else "")
            + '<p class="muted">開始時点の条件です。</p></aside>')


def render(handler, view):
    from viewer import workbench_pages as wb
    query = parse_qs(urlsplit(handler.path).query)
    generation = _integer(query.get("gen", [None])[0])
    observed = observation(handler, view, generation)
    job = view.get("job") or {"state": "legacy", "progress": {}}
    actions = {key for row in observed["generations"] for key in (row.get("action_share") or {})}
    # WB-JEV-002: reuse view["live"] (from _live_map, reading the catalog's
    # on-disk published/current.json directly) rather than the replay-scoped
    # `observed["generations"]` (bounded by job["publication_revision"], the
    # job record's own copy of the same revision). Both only ever hold
    # committed/published generations -- evolution_worker.py writes the disk
    # pointer and then the job record's publication_revision back-to-back in
    # the same checkpoint, so `live` can be at most one generation ahead,
    # never unbounded. (observation()'s legacy fallback path, ~106-119,
    # leaves view["live"] as None since it has no job; harmless here because
    # legacy runs predate the rationality_* summary fields anyway.)
    rationality_totals = wb._rationality_totals((view.get("live") or {}).get("generations") or [])
    rationality_html = wb._rationality_progress_line(rationality_totals) if rationality_totals is not None else ""
    payload = {"job": job, "observation": observed, "state_labels": wb.STATE_LABELS,
               "phase_labels": wb.PHASE_LABELS, "terminal_states": sorted(TERMINAL),
               "error_messages": wb.ERROR_MESSAGES,
               "action_labels": {key: wb._action_share_label(key) if "/" in key else key for key in actions},
               "world": view["world"], "config_id": (view.get("config") or {}).get("config_id"),
               "rationality_html": rationality_html}
    if query.get("view-data") == ["1"]:
        handler._send_json(HTTPStatus.OK, payload)
        return
    active = query.get("tab", ["overview"])[0]
    if active not in dict(TABS):
        active = "overview"
    title = "実行状況" if job.get("state") not in TERMINAL and job.get("job_id") else "実行結果"
    initial = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    world = view["world"]
    config = view.get("config") or {}
    config_href = f'/configs/{U(config["config_id"])}' if config else f'/jobs?world={U(world["id"])}'
    tabs = ''.join(f'<button type="button" role="tab" id="rw-tab-{key}" aria-controls="rw-{key}" '
                   f'aria-selected="{str(key == active).lower()}" tabindex="{0 if key == active else -1}" '
                   f'data-tab="{key}">{label}</button>' for key, label in TABS)
    empty = '<p class="rw-empty">保存済みの記録を読み込んでいます。</p>'
    initial_replay = observed.get("replay_html", empty).replace('class="ga-replay"', 'class="ga-replay" data-rw-managed="true" data-active="false"')
    terminal_message = wb._run_terminal_message(job) if job.get("state") in TERMINAL else ""
    experiment, world_state = _world_state(handler, view)
    panels = (
        '<section id="rw-overview" role="tabpanel" aria-labelledby="rw-tab-overview">'
        '<div class="rw-overview"><div><h2>探索の進み具合</h2><div data-overview-progress></div>'
        '<div data-rationality>' + rationality_html + '</div>'
        '<h2>見つかっている物語</h2><div data-overview-metrics></div><div data-map></div>'
        '<div data-recent-saves></div><details><summary>処理の詳細・ログ</summary><div data-run-log></div></details></div>'
        # N1 (Opus review): view["control"] is job_store.configs.control
        # (set in _run_view -- same value _run_plan() reads at
        # workbench_pages.py:1052/1060), so this avoids re-deriving it via a
        # second _job_store(handler) call.
        + _condition_html(handler, view, view.get("control"), world_state) + '</div></section>'
        '<section id="rw-replay" role="tabpanel" aria-labelledby="rw-tab-replay" hidden>'
        '<div data-generation-controls="replay"></div><p>保存済みの世代を再生しています。計算の進捗は上部で確認できます。</p>'
        f'<div data-replay-host>{initial_replay}</div></section>'
        '<section id="rw-river" role="tabpanel" aria-labelledby="rw-tab-river" hidden>'
        '<h2>残った物語は、どこから生まれたか</h2><div data-generation-controls="river"></div>'
        '<div class="rw-river-tools"><label>表示範囲 <select data-range><option value="all">全体</option>'
        '<option value="window">範囲を絞る</option></select></label><button data-range-prev>前の範囲</button>'
        '<button data-range-next>次の範囲</button><button data-clear-node>選択を解除</button></div>'
        f'<div data-river-host>{empty}</div></section>'
        '<section id="rw-trends" role="tabpanel" aria-labelledby="rw-tab-trends" hidden>'
        '<h2>世代を重ねて、何が変わったか</h2><label>指標 <select data-metric>'
        '<option value="occupied_cells">物語の種類数</option><option value="reach_rate">結末到達率</option>'
        '<option value="average_archive_quality">地図上の平均品質</option></select></label>'
        '<p data-metric-description></p><div data-trend-graph></div><div data-trend-detail></div>'
        '<details class="rw-trend-table"><summary>表で見る</summary><div data-trend-table></div></details></section>'
        '<section id="rw-demand" role="tabpanel" aria-labelledby="rw-tab-demand" hidden>'
        + _demand_html(handler, experiment, world_state, view) + '</section>'
        '<section id="rw-effect" role="tabpanel" aria-labelledby="rw-tab-effect" hidden>'
        + _effect_html(handler, view, query) + '</section>'
    )
    from viewer.run_browse import navigation
    body = (
        f'<div class="rw-shell" data-run-workspace data-initial="{E(initial)}">'
        + navigation("status", world=world, config=config, status_href=urlsplit(handler.path).path)
        +
        '<div class="rw-main"><header class="rw-heading"><div><h1 data-run-title>' + title + '</h1>'
        '<span class="state-badge" data-status>' + E(wb.STATE_LABELS.get(job.get("state"), "記録")) + '</span></div>'
        f'<p>{E(config.get("label") or world["name"])}</p></header>'
        '<div class="rw-live"><span data-phase></span><strong data-progress></strong>'
        '<progress aria-label="完了世代"></progress><span data-elapsed></span><small data-last-update></small></div>'
        '<p class="rw-connection" data-connection role="status" hidden></p>'
        f'<div class="rw-tabs" role="tablist" aria-label="実行状況の表示">{tabs}</div>'
        '<div class="rw-notices"><div data-terminal-message>' + terminal_message + '</div>'
        '<p class="rw-notice" data-new-generation hidden></p></div><div class="rw-content">' + panels + '</div>'
        '<footer class="rw-footer"><div><button data-stop>実行を停止</button>'
        f'<a data-restart href="{E(config_href)}" hidden>この条件で新しく実行</a>'
        '<span data-stop-status role="status">保存済みの結果は残ります。</span></div>'
        '<a class="rw-primary" data-candidates>保存済みの候補を見る →</a></footer></div></div>'
        '<noscript><p>各タブの操作にはJavaScriptが必要です。保存記録は既存の候補画面で閲覧できます。</p></noscript>'
    )
    doc = pages.document(title, body, phase="run", world=world, run=view.get("run_name"),
                         output_run=job.get("run_id"), job_store=getattr(handler.server, "job_store", None),
                         page_class="run-observer")
    doc = doc.replace('</head>', '<link rel="stylesheet" href="/static/run-workspace.css">'
                      '<script src="/static/ga_replay.js" defer></script>'
                      '<script src="/static/run-workspace.js" defer></script>'
                      '<script src="/static/world-expansion.js" defer></script></head>')
    handler._send_html(doc)


def experiment_page(handler, name):
    """Read-only records can be observed without a configured control store."""
    # Prefer the tracked job (and its publication boundary) when it exists.
    store = getattr(handler.server, "job_store", None)
    if store is not None:
        for job in store.list():
            if job.get("run_id") == name and job.get("kind", "evolve") == "evolve":
                from viewer.workbench_pages import _run_view
                return render(handler, _run_view(handler, job=job))
    root = handler.repository.experiment(name)
    meta = data.experiment_meta(handler.repository, root)
    render(handler, {"world": {"id": "", "name": meta.get("world") or name},
                     "job": None, "config": None, "run_name": name,
                     "axes": (meta["categories"], meta["bins"])})
