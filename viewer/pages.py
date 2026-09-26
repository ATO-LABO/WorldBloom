"""Deterministic HTML and SVG rendering for the WorldBloom viewer."""

from __future__ import annotations

from viewer import explanation_ui
from viewer import reader_ui

import html
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from urllib.parse import quote

from viewer import data, version


def _escape(value: Any) -> str:
    return html.escape(data._display(value), quote=True)


def _url_segment(value: str) -> str:
    return quote(value, safe="")


_PHASE_ITEMS = (
    ("world", "1", "世界"),
    ("run", "2", "実行"),
    ("sifting", "3", "Sifting"),
    ("stage", "4", "上映"),
)


def _phase_href(
    key: str,
    *,
    world: Mapping[str, Any] | None,
    run: str | None,
    output_run: str | None,
    phases: Mapping[str, Any] | None,
) -> str:
    if key == "world":
        if world and world.get("id"):
            return f"/worlds/{_url_segment(str(world['id']))}"
        return "/"
    if key == "run":
        job_id = phases.get("job_id") if phases else None
        if job_id:
            return f"/jobs/{_url_segment(str(job_id))}"
        # Carry the world you're viewing along, so /jobs shows *its* target
        # instead of falling back to whichever world's config is newest.
        if world and world.get("id"):
            return f"/jobs?world={_url_segment(str(world['id']))}"
        return "/jobs"
    if key == "sifting":
        return f"/exp/{_url_segment(run)}" if run else "/selected"
    if key == "stage":
        # /outputs?run= always keys off the *catalog* run_id (legacy runs'
        # catalog id differs from the experiment folder name in `run`), so
        # this must not fall back to `run` itself.
        return f"/outputs?run={_url_segment(output_run)}" if output_run else "/outputs"
    raise AssertionError(key)  # pragma: no cover - exhaustive _PHASE_ITEMS


def _phase_band(
    *,
    phase: str | None,
    phases: Mapping[str, Any] | None,
    world: Mapping[str, Any] | None,
    run: str | None,
    output_run: str | None,
) -> str:
    segments = []
    for key, number, label in _PHASE_ITEMS:
        classes = ["phase-seg"]
        if phases and phases.get(key):
            classes.append("is-done")
        if phase == key:
            classes.append("is-active")
        href = html.escape(
            _phase_href(key, world=world, run=run, output_run=output_run, phases=phases),
            quote=True,
        )
        current = ' aria-current="step"' if phase == key else ""
        title_attr = f' title="{_escape(TERM_HELP["sifting"])}"' if key == "sifting" else ""
        segments.append(
            f'<a class="{" ".join(classes)}"{current}{title_attr} href="{href}">'
            f'<span class="seg-num">{number}</span>'
            f'<span class="seg-title">{_escape(label)}</span></a>'
        )
    return (
        '<nav class="phase-band" aria-label="工程">'
        + "".join(segments)
        + "</nav>"
    )


def _library_worlds(job_store: Any) -> list[Mapping[str, Any]]:
    from execution.library import LibraryStore  # deferred: mirrors index_page

    try:
        repo = job_store.configs.repo if job_store is not None else data.ROOT
        return LibraryStore(repo).worlds()
    except (ValueError, OSError, KeyError, TypeError, AttributeError):
        return []


def _world_picker(
    world: Mapping[str, Any] | None,
    worlds: Sequence[Mapping[str, Any]],
) -> str:
    current = str(world["id"]) if world and world.get("id") else ""
    known = {str(w["id"]) for w in worlds}
    placeholder_selected = "" if current in known else " selected"
    options = [f'<option value=""{placeholder_selected}>世界を選ぶ…</option>']
    for w in worlds:
        wid = str(w["id"])
        selected = " selected" if wid == current else ""
        options.append(
            f'<option value="{_escape(wid)}"{selected}>{_escape(w.get("name") or wid)}</option>'
        )
    return (
        '<label class="picker"><span class="picker-label">世界</span>'
        f'<select class="picker-select" data-wb="world-picker">{"".join(options)}</select></label>'
    )


def _header_pickers(
    world: Mapping[str, Any] | None,
    run: str | None,
    worlds: Sequence[Mapping[str, Any]],
    *,
    is_home: bool,
) -> str:
    # Home already shows the full world/genre hub in the body, so the header
    # picker (world + run) would just be a redundant, pinned-elsewhere guess.
    pickers = []
    if not is_home:
        pickers.append(_world_picker(world, worlds))
        if run is not None:
            pickers.append(
                '<span class="picker"><span class="picker-label">実験</span>'
                f'<span class="picker-value">{_escape(run)}</span></span>'
            )
    # Every page leads with the same wordmark; off the home page it links back home.
    leftmost = (
        '<a class="brand" href="/">WorldBloom</a>'
        if is_home
        else '<a class="home-cell" href="/" aria-label="WorldBloom ホーム">WorldBloom</a>'
    )
    return (
        '<div class="header-pickers">'
        + leftmost
        + "".join(pickers)
        + '<span class="header-links">'
        f'<a href="/version" class="header-version" title="バージョン情報・最新版の確認">{_escape(version.label())}</a>'
        '<a href="#" data-sheet="local-status-dialog" title="GPUとローカルAIの動作状況" aria-label="GPUとローカルAIの動作状況">'
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true" focusable="false"><rect x="7" y="7" width="10" height="10" rx="1.5"/><path d="M9 7V4M12 7V4M15 7V4M9 20v-3M12 20v-3M15 20v-3M7 9H4M7 12H4M7 15H4M20 9h-3M20 12h-3M20 15h-3" stroke-linecap="round"/></svg><span>動作状況</span></a>'
        '<a href="/history" title="実行履歴" aria-label="実行履歴">'
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2" stroke-linecap="round" stroke-linejoin="round"/></svg><span>実行履歴</span></a>'
        '<a href="/configs" title="設定" aria-label="設定">'
        '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" focusable="false"><path fill-rule="evenodd" d="M10 2h4l.6 3 2 .9 2.6-1.5 2 3.5-2.2 2v2.2l2.2 2-2 3.5-2.6-1.5-2 .9-.6 3h-4l-.6-3-2-.9-2.6 1.5-2-3.5 2.2-2V10L2.8 8l2-3.5 2.6 1.5 2-.9L10 2zm2 6a4 4 0 1 0 0 8 4 4 0 0 0 0-8z"/></svg><span>設定</span></a>'
        "</span>"
        '<dialog class="sheet-dialog local-status-dialog" id="local-status-dialog" '
        'data-fetch="/api/status/local" aria-label="GPU・ローカルAIの動作状況">'
        '<section class="local-status">'
        '<div class="sheet-head"><h4>GPU・ローカルAIの動作状況</h4>'
        '<button type="button" class="button" data-local-status-refresh>更新</button>'
        '<button type="button" class="button" data-close-dialog>閉じる</button></div>'
        '<p class="local-status-backend" data-local-status-backend>確認中…</p>'
        '<dl class="local-status-rows" data-local-status-rows></dl>'
        '<p class="local-status-time" data-local-status-time></p>'
        "</section></dialog>"
        "</div>"
    )


def document(
    title: str,
    body: str,
    *,
    crumbs: list[tuple[str, str]] = (),  # unused: breadcrumb nav was removed, kept for caller compatibility
    world: Mapping[str, Any] | None = None,
    run: str | None = None,
    output_run: str | None = None,
    phase: str | None = None,
    phases: Mapping[str, Any] | None = None,
    lead: str | None = None,
    next_action: tuple[str, str] | None = None,
    job_store: Any = None,
    pin: Mapping[str, Any] | None = None,
    show_phase_band: bool = True,
    is_home: bool = False,
    page_class: str | None = None,
) -> str:
    # pin (data.pinned_target()) fills in world/run/output_run for callers
    # that don't already know their own (Home, /configs, /jobs): the run is
    # only borrowed when the header world *is* the pinned world, so a page
    # for a different world never gets ③/④ tabs pointing at someone else's
    # run -- it keeps the honest cross-world /selected and /outputs fallback.
    if pin is not None:
        if world is None:
            world = pin["world"]
        if run is None and output_run is None and world.get("id") == pin["world"]["id"]:
            run, output_run = pin["run"], pin["output_run"]
    # Callers that already looked up phase_status() (experiment/cell/compare/
    # raw pages) get the stage link's catalog-id distinction for free; other
    # callers pass output_run explicitly when they know it (see workbench_
    # pages.py / output_pages.py), or leave it unset when there is none.
    if output_run is None and phases:
        output_run = phases.get("catalog_run_id")
    lead_html = ""
    if lead is not None or next_action is not None:
        lead_text = f"{_escape(lead)} " if lead is not None else ""
        cta = ""
        if next_action is not None:
            next_label, next_href = next_action
            # next_label already ends in "→" (the _progress_row/
            # next_action_for convention this reuses) -- no second arrow here.
            cta = (
                f'<a class="next-cta" href="{html.escape(next_href, quote=True)}">'
                f"次: {_escape(next_label)}</a>"
            )
        lead_html = f'<p class="page-lead">{lead_text}{cta}</p>'
    safe_page_class = _escape(page_class or "standard-workspace")
    body_class = f' class="layout-{safe_page_class}"'
    main_html = (
        f'<main class="page-shell {safe_page_class}">'
        f'<header class="page-heading"><h1>{_escape(title)}</h1>{lead_html}</header>'
        f'<div class="page-content">{body}</div></main>'
    )
    return (
        "<!doctype html>"
        '<html lang="ja"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_escape(title)} | WorldBloom</title>"
        '<link rel="stylesheet" href="/static/app.css">'
        '<script src="/static/app.js" defer></script>'
        '<script src="/static/workbench.js" defer></script>'
        f"</head><body{body_class}>"
        '<div class="app-shell">'
        '<header class="site-header">'
        + _header_pickers(
            world, run, () if is_home else _library_worlds(job_store), is_home=is_home,
        )
        + (_phase_band(phase=phase, phases=phases, world=world, run=run, output_run=output_run)
           if show_phase_band else "")
        + "</header>"
        + main_html
        + '<div id="toast" role="status" aria-live="polite"></div>'
        "</div>"
        "</body></html>"
    )


def tabs(name: str, panels: Sequence[tuple[str, str]]) -> str:
    """Render a zero-JS tab strip: a radio-button group plus labelled panels.

    Pure CSS via sibling selectors (app.css hand-writes one rule per panel
    index for `name`) -- no client script, and every panel's markup stays in
    the document (just hidden), so page-content assertions in tests don't
    need to know which tab is open. The first panel is selected by default.
    """

    inputs, tab_list, panel_markup = [], [], []
    for index, (label, content) in enumerate(panels):
        input_id = f"tab-{_escape(name)}-{index}"
        checked = " checked" if index == 0 else ""
        inputs.append(
            f'<input type="radio" name="tabs-{_escape(name)}" id="{input_id}" '
            f'class="tab-input"{checked}>'
        )
        tab_list.append(f'<label class="tab-label" for="{input_id}">{_escape(label)}</label>')
        panel_markup.append(f'<section class="tab-panel" aria-label="{_escape(label)}">{content}</section>')
    return (
        f'<div class="tabs tabs-{_escape(name)}">'
        + "".join(inputs)
        + '<div class="tab-list">' + "".join(tab_list) + "</div>"
        + '<div class="tab-panels">' + "".join(panel_markup) + "</div>"
        "</div>"
    )


def sparkline(
    values: Sequence[float],
    *,
    width: int = 120,
    height: int = 28,
) -> str:
    clean = [float(value) for value in values]
    if not clean:
        return '<span class="muted">—</span>'
    low = min(clean)
    high = max(clean)
    span = high - low
    points = []
    for index, value in enumerate(clean):
        x = width / 2 if len(clean) == 1 else (
            index * width / (len(clean) - 1)
        )
        y = (
            height / 2
            if span == 0
            else height - 2 - (value - low) * (height - 4) / span
        )
        points.append(f"{x:.2f},{y:.2f}")
    return (
        f'<svg class="spark" viewBox="0 0 {width} {height}" '
        'role="img" aria-label="推移">'
        f'<polyline points="{" ".join(points)}"/></svg>'
    )


def _series_text(
    values: Sequence[float],
    *,
    percent: bool = False,
) -> str:
    if not values:
        return "—"
    if percent:
        return f"{values[0]:.0%} → {values[-1]:.0%}"
    return f"{values[0]:.2f} → {values[-1]:.2f}"


def _grade(value: float | None) -> str:
    """Colour class for a 0-1 metric where higher genuinely is better.

    占有マス (n/N), 相異度 and q are all bounded at 1 by construction, so the
    same ramp reads the same way across them. 到達率 is deliberately excluded:
    it is the archive's entry gate, not a score, and a run of 100% would mean
    the world is too easy rather than that the search did well.
    """

    if value is None:
        return "grade-none"
    if value >= 0.85:
        return "grade-strong"
    if value >= 0.65:
        return "grade-good"
    if value >= 0.45:
        return "grade-fair"
    if value >= 0.25:
        return "grade-weak"
    return "grade-poor"


def _ratio(numerator: Any, denominator: Any) -> float | None:
    try:
        total = float(denominator)
        if total <= 0:
            return None
        return float(numerator) / total
    except (TypeError, ValueError):
        return None


def _last(values: Sequence[float]) -> float | None:
    return values[-1] if values else None


TERM_HELP = {
    "cells": (
        "占有マス: アーカイブが埋まった区画の数。"
        "区画は「主導カテゴリ × 起伏の大きさ」で決まり、"
        "1 区画につき最良のラン 1 本だけが残る。"
        "多いほど、違う種類の物語が見つかっている。"
    ),
    "dissimilarity": (
        "相異度: アーカイブに残ったラン同士が、"
        "どれだけ違う経路を通ったかの平均。1 に近いほど多様。"
        "到達率だけを追うと経路が似通って下がりやすい。"
    ),
    "quality": (
        "q: 結末に届いたランの質。7 つの指標の平均で、"
        "目的物の移動・関係の振れ幅・復帰・力関係の逆転・"
        "新事実の獲得・信念の反転・前提を揃えた行動の連鎖を数え、"
        "空振りの多さを減点する。q̄ はアーカイブ全体の平均。"
        "絶対尺度なので、世界の作りによって出る水準が違う"
        "（未回収の伏線が多い世界は構造的に低く出る）。"
    ),
    "reach": (
        "到達率: その世代の全ラン（個体数 × シード数）のうち、"
        "あらかじめ固定した結末に届いた割合。"
        "アーカイブに入るためのゲートであり、"
        "探索が最大化する対象ではない。"
    ),
    "tendency": (
        "傾向: この個体の遺伝子がいちばん重みを置く行動カテゴリ。"
        "このジャンルが使う軸の中から選ぶ。"
        "格子の行（主導カテゴリ）は、そのランで実際に多く選ばれた"
        "行動から決まる別の値なので、両者は一致しないことがある。"
    ),
    "generation": (
        "世代: このマスの記録が最後に更新された世代。"
        "アーカイブは質が上回ったときだけ入れ替わるので、"
        "以後の世代ではこれを超える個体が現れていない。"
    ),
    "seed": (
        "seed: この模範ランを生成した乱数の種。"
        "同じ遺伝子でも種が違えば世界の初期条件が変わる"
        "（探偵なら真犯人が変わる）。"
    ),
    "parents": (
        "親: この個体を生んだ 2 つの個体。"
        "アーカイブのマスから選ばれた場合は「マス名」で表される。"
    ),
    "layers": (
        "layers: この模範ランの全ログの場所。"
        "実験ディレクトリからの相対パス。"
    ),
    "elite_reach": (
        "到達: このエリートが評価されたシードのうち、"
        "結末に届いた回数。世代ごとの到達率とは分母が違う。"
    ),
    # WB-UI-013: general-purpose vocabulary for column headings, glossaries
    # and lead text. Design-role-confirmed wording (see
    # docs/2026-09-11_gapengine-detailed-design.md's companion plan, §1.2) --
    # do not paraphrase. "generation"/"seed" above are deliberately left
    # untouched (elite-specific wording); these new keys never collide with
    # them.
    "run": (
        "実験（run）: 1 回の GA 実行。設定版 1 つから生まれ、"
        "runs/<名前> に記録が残る。画面では実験名（フォルダ名）で表す。"
    ),
    "individual": (
        "個体: 1 つの遺伝子（行動の重み付け）。"
        "世代ごとに個体数ぶん評価される。"
    ),
    "candidate_generation": (
        "世代: GA の反復回数。世代ごとに個体を評価し、格子（アーカイブ）を更新する。"
    ),
    "candidate_seed": (
        "seed: 乱数の種。同じ遺伝子でも seed が違えば世界の初期条件が変わり、"
        "別の経緯になる（探偵なら真犯人が変わる）。"
    ),
    "candidate_id": (
        "候補ID: 実験・世代・個体・seed の組を一意に指す ID。"
        "選定と生成はこの ID 単位で記録される。"
    ),
    "role": (
        "役割: 候補が主人公側の遺伝子か敵役側か"
        "（共進化のとき両方が生まれる）。旧実験は不明。"
    ),
    "cell": (
        "セル: 格子の区画。行は主導カテゴリ"
        "（そのランで実際に多く選ばれた行動の種類）、列は起伏（low/mid/high）。"
        "区画ごとに最良の 1 本だけが残る。"
    ),
    "reached": (
        "到達: あらかじめ固定した結末に届いたか。"
        "届いたランだけがアーカイブと本文生成の対象になる。"
    ),
    "log": (
        "原記録: GA が書いた全ログ（layers.jsonl）。"
        "「あり」なら本文生成と原ログ閲覧ができる。"
        "「剪定済み」は保存方針で削られた、「不在」は見つからない、"
        "「不一致」はハッシュが合わない。"
    ),
    "screenable": (
        "採用可: 本文生成に使える条件（到達済み かつ 原記録あり）を満たすか。"
    ),
    "state": (
        "選定状態: Sifting での判断。✔ 採用＝生成に進める、⏸ 保留＝あとで決める、"
        "✖ 除外＝使わない、○ 未分類＝まだ判断していない。"
    ),
    "note": (
        "メモ: 選定の理由や気づきを残す自由記述。選定版に含まれる。"
    ),
    "draft": (
        "稿: この候補から生成済みの本文の数"
        "（あらすじ／上映それぞれの「本文あり」件数）。"
    ),
    "publication_revision": (
        "公開版: 実験の記録（格子と候補）が確定した回数。"
        "世代が確定するたびに増え、候補一覧はこの版から作られる。"
    ),
    "selection_revision": (
        "選定版: 選定状態とメモを保存した回数。"
        "他のタブで先に保存されていると版が進み、保存が拒否される（上書き防止）。"
    ),
    "sifting": (
        "Sifting: 実験が残した候補をふるいにかけ、読む価値のあるものを採用する工程。"
        "格子で吟味し、四項目で比較し、選定状態を付ける。"
    ),
    "config_id": (
        "config_id: 実行設定の版 ID。設定は保存すると不変になり、"
        "実験はどの版から生まれたかを記録する。"
    ),
    "output_id": (
        "output_id: 生成した作品（あらすじ／上映の束）の ID。"
        "1 回の生成ジョブに 1 つ。"
    ),
    "backend_model": (
        "backend/model: 本文を生成した方式とモデル名"
        "（例: ollama / qwen3.6:35b）。画面を開いただけでは生成しない。"
    ),
    "kind": (
        "種別: あらすじ生成（synopsize）か上映生成（narrate、本文）か。"
    ),
    "phase": (
        "段階: 実行中のジョブが今どこにいるか"
        "（準備中／評価中／世代確定中／生成中）。"
    ),
    "job_state": (
        "状態: ジョブの状態（待機中／実行中／停止処理中／完了／"
        "一部完了／失敗／停止済み／中断）。"
    ),
    "counts": (
        "内訳: 作品の中の候補ごとの結果の集計（本文あり／失敗／未開始 など）。"
    ),
    "targets": (
        "対象数: その生成ジョブが本文を作ろうとした候補の数。"
    ),
    "genre": (
        "ジャンル: 世界が使う文法の組（行動グラフ・正典・効果表・ルール・QD 軸）。"
        "templates/<ジャンル> に置かれ、世界の設定ファイルから参照される。"
    ),
    "world": (
        "世界: 地名・経路・日数と登場人物の初期状態の組。projects/<世界> に置かれる。"
    ),
    # WB-LINEAGE-002
    "lineage": (
        "主系: このエリートから親を一本道でたどった列。"
        "二親のうち遺伝子が近い方だけを採用し、免れた側は表示しない。"
    ),
    "turning": (
        "転機: 主系の隣り合う親子を同じ seed で再実行し、"
        "主人公の決定が最初に分かれた地点。"
        "分かれても何も変えない選択は読み飛ばす。"
    ),
}

METRIC_HELP = TERM_HELP  # backward-compat alias; do not add new entries here


def term(key: str, label: str | None = None) -> str:
    """Label with its hover explanation, sourced from TERM_HELP.

    ``label`` defaults to the term's own heading word (the text before the
    first ": " in its TERM_HELP entry).
    """

    text = TERM_HELP[key]
    if label is None:
        label = text.split(": ", 1)[0]
    return f'<span class="tip" title="{_escape(text)}">{label}</span>'


def _tip(key: str, label: str) -> str:
    """Label with its hover explanation (thin wrapper around term())."""

    return term(key, label)


def glossary(keys: Sequence[str]) -> str:
    """A <details> block defining just the terms this screen actually uses."""

    parts = []
    for key in keys:
        heading, _, definition = TERM_HELP[key].partition(": ")
        parts.append(f"<dt>{_escape(heading)}</dt><dd>{_escape(definition)}</dd>")
    return (
        '<details class="glossary"><summary>用語</summary><dl>'
        + "".join(parts)
        + "</dl></details>"
    )


def _gate_text(values: Sequence[float]) -> str:
    """The gate figure, without the arrow that reads as progress.

    到達率 is the share of runs that reached the fixed ending. It admits a run
    to the archive; it is not the thing the search maximises, so it is shown
    as a plain current value rather than "start → end".
    """

    if not values:
        return "—"
    return f"{values[-1]:.0%}"


def _dissimilarity_note(values: Sequence[float]) -> str:
    if len(values) < 2 or values[-1] >= values[0]:
        return ""
    return (
        '<span class="warn-note">初期より低下'
        f"（{values[0]:.2f} から）</span>"
    )


def _command_block(
    repository: data.RunRepository,
    meta: Mapping[str, Any],
) -> str:
    genre = meta.get("genre")
    if genre == "不明":
        return (
            '<section class="card commands"><h2>生成コマンド</h2>'
            '<p class="warning">ジャンル不明のため生成コマンドを'
            "確定できません。</p></section>"
        )

    experiment = repository.runs_root / str(meta["name"])
    archive = experiment / "archive.json"
    synopsis = experiment / "synopses.json"
    selection = experiment / "selection.json"
    stories = experiment / "stories"
    settings_warning = ""
    if not (data.ROOT / "settings.json").is_file():
        settings_warning = (
            '<p class="warning">settings.json が無いため backend は '
            "prompt_only になります。例: "
            '<code>{&quot;output&quot;:{&quot;codex-cli&quot;:'
            '{&quot;model&quot;:&quot;gpt-5.6-sol&quot;}}}</code></p>'
        )
    synopsize = (
        "python scripts\\synopsize.py"
        f" --archive {archive}"
        f" --runs {experiment}"
        f" --out {synopsis}"
        " --backend codex-cli"
        f" --project projects\\{genre}"
        f" --template templates\\{genre}"
    )
    narrate = (
        "python scripts\\narrate.py"
        f" --archive {archive}"
        f" --selection {selection}"
        f" --out {stories}"
        " --backend codex-cli"
        f" --project projects\\{genre}"
        f" --template templates\\{genre}"
    )
    return (
        '<section class="card commands"><h2>生成コマンド</h2>'
        f"{settings_warning}"
        f'<pre class="cmd">{_escape(synopsize)}\n'
        f"{_escape(narrate)}</pre></section>"
    )


_NEXT_LABELS = {
    "sifting": "Sifting で候補を選ぶ →",
    "stage": "上映を生成する →",
}


def next_action_for(
    run_name: str,
    phases: Mapping[str, Any],
    *,
    catalog_run_id: str | None = None,
) -> tuple[str, str]:
    """The next-step label/href for a run, from phase_status()'s "next" field.

    Shared by the home dashboard and every other screen that knows which run
    it's showing (WB-UI-012 §2.1), so "next:" always agrees with the home
    page for the same experiment. This is the only place that reads
    phases["next"]; no new judgement is added here.
    """

    # Outputs are stored under the catalog run_id (legacy runs' catalog id
    # differs from the experiment folder name), so /outputs?run= must use it
    # when known; fall back to the folder name only when there is no catalog.
    output_run = str(catalog_run_id or phases.get("catalog_run_id") or run_name)
    next_phase = phases.get("next")
    if next_phase is None:
        return "上映を読む →", f"/outputs?run={_url_segment(output_run)}"
    if next_phase == "run":
        job_id = phases.get("job_id")
        if job_id:
            return "実行の進捗を見る →", f"/jobs/{_url_segment(str(job_id))}"
        return "実行する →", "/jobs"
    if next_phase == "sifting":
        return _NEXT_LABELS["sifting"], f"/exp/{_url_segment(run_name)}"
    return _NEXT_LABELS["stage"], f"/outputs?run={_url_segment(output_run)}"


def _next_command(meta: Mapping[str, Any], phases: Mapping[str, Any]) -> tuple[str, str]:
    return next_action_for(str(meta["name"]), phases)


_CIRCLED_DIGITS = ("①", "②", "③", "④")


def _progress_badges(phases: Mapping[str, Any]) -> str:
    parts = []
    for circled, (key, _number, _label) in zip(_CIRCLED_DIGITS, _PHASE_ITEMS):
        done = bool(phases.get(key))
        cls = "progress-badge-done" if done else "progress-badge-next"
        mark = "●" if done else "○"
        parts.append(f'<span class="progress-badge {cls}">{circled}{mark}</span>')
    return '<div class="progress-badges">' + "".join(parts) + "</div>"


def _progress_row(
    meta: Mapping[str, Any],
    repository: data.RunRepository,
    job_store: Any,
    history: Sequence[Mapping[str, Any]] | None,
    outputs: Sequence[Mapping[str, Any]] | None,
) -> str:
    run_name = str(meta["name"])
    selected_count = meta.get("selected")
    phases = data.phase_status(
        repository, run_name, job_store=job_store, history=history, outputs=outputs,
        selected=(int(selected_count) > 0) if isinstance(selected_count, int) else None,
    )
    href = f"/exp/{_url_segment(run_name)}"
    date = datetime.fromtimestamp(
        float(meta["archive_mtime"])
    ).strftime("%Y-%m-%d %H:%M")
    seeds = meta.get("seeds")
    seed_count = len(seeds) if isinstance(seeds, list) else None
    quality_values = meta.get("quality_series") or []
    quality_text = f"{quality_values[-1]:.2f}" if quality_values else "—"
    reach_values = meta.get("reach_series") or []
    next_label, next_href = _next_command(meta, phases)
    delete_button = (
        f'<button type="button" class="run-delete" data-run="{_escape(run_name)}" '
        f'data-endpoint="/exp/{_url_segment(run_name)}/delete">削除</button>'
        if job_store is not None else ""
    )
    return (
        '<div class="progress-row">'
        '<div class="progress-label">'
        f'<a href="{href}"><strong>{_escape(run_name)}</strong></a>'
        f'<span class="muted">{_escape(date)}'
        f' · {_escape(meta["generations"])}世代'
        f' · {_escape(meta.get("population"))}個体'
        f' · {_escape(seed_count)}シード</span>'
        "</div>"
        f"{_progress_badges(phases)}"
        '<div class="progress-metrics muted">'
        f'占有 {_escape(meta["cells"])}/{_escape(meta["grid_size"])}'
        f' · q̄ {_escape(quality_text)}'
        f' · 到達率 {_escape(_gate_text(reach_values))}'
        "</div>"
        f'<a class="progress-command" href="{next_href}">次: {_escape(next_label)}</a>'
        f"{delete_button}"
        "</div>"
    )


def _world_project_info() -> dict[str, str]:
    """Map a world's display name to its resolvable genre slug.

    Mirrors data.resolve_genre's own requirement (project.world.yaml name +
    a matching templates/<slug> directory) so the "?project=&template="
    preset only ever points at real directories.
    """

    projects_dir = data.ROOT / "projects"
    if not projects_dir.is_dir():
        return {}
    info: dict[str, str] = {}
    for project_dir in sorted(
        (p for p in projects_dir.iterdir() if p.is_dir()),
        key=lambda path: path.name,
    ):
        world = data._yaml_mapping(project_dir / "world.yaml")
        name = str(world.get("name") or "")
        template_dir = data.ROOT / "templates" / project_dir.name
        if name and name not in info and template_dir.is_dir():
            info[name] = project_dir.name
    return info


def quick_start_actions(world_id, genre, world_name, run_href, *, css_class="", text="この世界で新しい実験を回す",
                         repo=None):
    """WB-UI-022: one click starts a run with an auto-generated 設定名 and the
    current defaults, skipping the config form. The 設定名 is built client-side
    at click time (see workbench.js's quickLabel/initQuickStart) so it carries
    the actual click time, not this page's render time. Falls back to a plain
    link to that form when there is no world/genre to start from
    (data-quick-start's JS handler also falls back to run_href on failure).

    S4 (Opus review): `repo` lets a caller with a job_store pass its own
    `job_store.configs.repo` instead of reading the live data.ROOT working
    tree directly; callers with no job_store (or that don't care) keep the
    old data.ROOT default.
    """
    repo = repo if repo is not None else data.ROOT
    cls = f' class="{_escape(css_class)}"' if css_class else ""
    if not world_id or not genre:
        return f'<a{cls} href="{_escape(run_href)}">{_escape(text)}</a>'
    # WB-ROUTE-001 S4 §2: ρ carries no extra compute cost (unlike κ), so the
    # quick-start button passes route_rho=1.0 outright when the genre has a
    # route.yaml, rather than deferring to the config form.
    has_route = (repo / "templates" / genre / "route.yaml").is_file()
    route_attr = ' data-route-rho="1.0"' if has_route else ""
    quick = (
        f'<a{cls} href="{_escape(run_href)}" data-quick-start '
        f'data-project="{_escape(world_id)}" data-template="{_escape(genre)}" '
        f'data-world-name="{_escape(world_name)}"{route_attr}>{_escape(text)}</a>'
    )
    result = quick + f' <a href="{_escape(run_href)}">設定を変更して実行</a>'
    if (repo / "templates" / genre / "rationality.yaml").is_file():
        # WB-JEV-002: this button always saves kappa=None (rationality off,
        # same as leaving the field untouched) -- the default scale
        # (20*100*3=6,000 runs) would take ~150h at kappa=0.6.
        # Opus review: a <span>, not a <p> -- render_world_detail() (in
        # library_pages.py) wraps this whole result in its own <p
        # class="actions">, and a nested <p> would break there. "muted" (not
        # just "hint", which app.css only styles under .cfg-form) so this
        # also looks right on the world page, outside any config form.
        result += ' <span class="hint muted">合理性（κ）は実行設定で指定します。</span>'
    if has_route:
        result += ' <span class="hint muted">道筋 ρ=1.0 で実行</span>'
    return result


def _project_info(job_store: Any) -> dict[str, dict[str, str]]:
    """Map a world's display name to {"id": projects/<id>, "genre": templates/<genre>}.

    With --control (job_store set), delegate to execution.library.LibraryStore
    so a world's genre reflects its current world.yaml gapengine.* references
    (kept in sync by the /worlds editor) instead of the name-matching guess
    below. Without --control, fall back to _world_project_info() unchanged.
    """

    if job_store is None:
        return {name: {"id": slug, "genre": slug} for name, slug in _world_project_info().items()}
    from execution.library import LibraryStore

    info: dict[str, dict[str, str]] = {}
    try:
        worlds = LibraryStore(job_store.configs.repo).worlds()
    except (ValueError, OSError, KeyError, TypeError, AttributeError):
        return {name: {"id": slug, "genre": slug} for name, slug in _world_project_info().items()}
    for world in worlds:
        name = world.get("name")
        project_id = world.get("id")
        if name and project_id and name not in info:
            info[name] = {"id": str(project_id), "genre": str(world.get("genre") or "")}
    return info


def _experiment_world(meta: Mapping[str, Any], job_store: Any) -> dict[str, str] | None:
    """Header/phase-band world context for a run: resolve the world's
    projects/<id> from its display name (never from the genre slug, which
    can differ for worlds created via /worlds/new)."""
    name = meta.get("world")
    if not name:
        return None
    info = _project_info(job_store).get(str(name))
    if not info or not info.get("id"):
        return None
    return {"id": info["id"], "name": str(name)}


def _home_actions(can_create: bool) -> str:
    if not can_create:
        return ""
    return (
        '<p class="actions">'
        '<a class="button primary" href="/worlds/new">新しい世界を作る</a>'
        "</p>"
    )


def _dashboard_sources(
    repository: data.RunRepository,
    job_store: Any,
) -> tuple[list[Mapping[str, Any]] | None, list[Mapping[str, Any]] | None]:
    """Fetch history/outputs once for a page rendering many progress rows.

    data.phase_status() otherwise calls catalog.history()/job_store.outputs()
    again for every row, which made the home page take several seconds with
    a couple dozen runs.
    """

    history: list[Mapping[str, Any]] | None = None
    if repository.catalog is not None:
        try:
            history = repository.catalog.history()
        except (ValueError, OSError, KeyError, TypeError, AttributeError):
            history = []
    outputs: list[Mapping[str, Any]] | None = None
    if job_store is not None:
        try:
            outputs = job_store.outputs()
        except (ValueError, OSError, KeyError, TypeError, AttributeError):
            outputs = []
    return history, outputs


def world_runs_block(
    repository: data.RunRepository,
    world_name: str,
    job_store: Any,
) -> tuple[str, int]:
    """The progress-dashboard body for one world's experiments (WB-UI-016),
    plus its run count (WB-UI: 実行履歴 tab).

    Used by the world detail page's 実行履歴 tab; shows the same major-run
    rows plus a closed "その他の短いラン" details as the home dashboard
    previously did per world, just for a single world at a time.
    """

    groups, minor = data.grouped_experiments(repository)
    majors = dict(groups).get(world_name, [])
    minors = [meta for meta in minor if str(meta["world"]) == world_name]
    history, outputs = _dashboard_sources(repository, job_store)
    count = len(majors) + len(minors)

    major_rows = "".join(
        _progress_row(meta, repository, job_store, history, outputs) for meta in majors
    )
    body = (
        f'<div class="progress-dashboard">{major_rows}</div>'
        if major_rows
        else '<p class="muted">まだ実験がありません。</p>'
    )
    if not minors:
        return body, count
    minor_rows = "".join(
        _progress_row(meta, repository, job_store, history, outputs) for meta in minors
    )
    return body + (
        '<details class="minor-runs">'
        f"<summary>その他の短いラン ({len(minors)})</summary>"
        f'<div class="progress-dashboard">{minor_rows}</div></details>'
    ), count


def index_page(repository: data.RunRepository, *, job_store: Any = None) -> str:
    from viewer import home_pages
    return home_pages.render(repository, job_store=job_store)


def _threshold_text(thresholds: Mapping[str, Any]) -> str:
    low = thresholds.get("low_max")
    mid = thresholds.get("mid_max")
    if low is None or mid is None:
        return "未設定"
    return (
        f"low ≤ {data._number(low):.3f} < "
        f"mid ≤ {data._number(mid):.3f} < high"
    )


def _lead_category(
    genome: Mapping[str, Any],
    categories: Sequence[str],
) -> str:
    """Return the strongest category that is active in this template."""

    weights = data._as_mapping(genome.get("category_weight"))
    active = [
        category
        for category in categories
        if category in weights
    ]
    if not active:
        return "—"
    return str(
        max(
            sorted(active),
            key=lambda category: data._number(weights[category]),
        )
    )


def _reached_seeds(
    reach_rate: float,
    seed_count: int,
) -> str:
    """Elite reach as "n/N シード".

    The generation-level figure keeps the name 到達率 (share of all runs in a
    generation). An elite is only ever evaluated on the K seeds of its own
    job, so showing it as a percentage invited confusion with that share.
    """

    if seed_count <= 0:
        return f"{reach_rate:.1%}"
    return f"{round(reach_rate * seed_count)}/{seed_count} シード"


def _cell_markup(
    repository: data.RunRepository,
    experiment: Any,
    experiment_name: str,
    cell_key: str,
    elite: Mapping[str, Any],
    world_meta: Mapping[str, Any],
    categories: Sequence[str],
    selected: set[str],
    seed_count: int,
) -> str:
    synopsis, _ = data.synopsis_entry(
        repository,
        experiment,
        cell_key,
    )
    hook = data.cell_hook(
        repository,
        experiment,
        cell_key,
        elite,
        world_meta,
        synopsis,
    )
    href = (
        f"/exp/{_url_segment(experiment_name)}"
        f"/cell/{_url_segment(cell_key)}"
    )
    endpoint = (
        f"/exp/{_url_segment(experiment_name)}/selection"
    )
    is_selected = cell_key in selected
    star = "★" if is_selected else "☆"
    pressed = "true" if is_selected else "false"
    genome = data._as_mapping(elite.get("genome"))
    try:
        explanation_short = reader_ui.short(data.cell_explanation(repository, experiment, cell_key))
    except (data.MissingResource, FileNotFoundError):
        explanation_short = '<p class="muted">原ログが見つからないため四項目を表示できません。</p>'
    return (
        '<td class="occupied">'
        '<div class="cell-head">'
        f'<a href="{href}"><strong>{_escape(cell_key)}</strong></a>'
        f'<button type="button" class="selection-toggle" '
        f'data-endpoint="{endpoint}" data-cell="{_escape(cell_key)}" '
        f'aria-pressed="{pressed}" title="選定を切り替える">{star}</button>'
        "</div>"
        '<dl class="cell-metrics">'
        f'<dt>{_tip("quality", "q")}</dt>'
        f'<dd class="{_grade(data._number(elite.get("quality")))}">'
        f'{data._number(elite.get("quality")):.4f}</dd>'
        f'<dt>{_tip("elite_reach", "到達")}</dt><dd>'
        f'{_reached_seeds(data._number(elite.get("reach_rate")), seed_count)}'
        "</dd>"
        f'<dt>{_tip("generation", "世代")}</dt>'
        f'<dd>g{int(data._number(elite.get("generation")))}</dd>'
        f'<dt>{_tip("tendency", "傾向")}</dt>'
        f'<dd>{_escape(_lead_category(genome, categories))}</dd>'
        "</dl>"
        f'<p class="hook">{_escape(hook) if hook else "場面情報なし"}</p>'
        + explanation_short
        + f'<label><input type="checkbox" name="cell" value="{_escape(cell_key)}" form="compare-cells"> 四項目で比較</label>'
        +
        "</td>"
    )


def experiment_page(
    repository: data.RunRepository,
    experiment_name: str,
    *,
    job_store: Any = None,
) -> str:
    from viewer import review_pages
    return review_pages.grid(repository, experiment_name, job_store=job_store)



def layers_svg(
    points: Sequence[Mapping[str, Any]],
    markers: Sequence[Mapping[str, Any]],
) -> str:
    if not points:
        return '<p class="muted">snapshot vector がありません。</p>'

    width = 920
    height = 360
    left = 58
    right = 24
    top = 26
    bottom = 88
    plot_width = width - left - right
    plot_height = height - top - bottom

    point_days = [
        int(data._number(point.get("day")))
        for point in points
    ]
    marker_days = [
        int(data._number(marker.get("day")))
        for marker in markers
    ]
    domain_days = [*point_days, *marker_days]
    minimum_day = min(domain_days)
    maximum_day = max(domain_days)

    def x_for_day(day: int) -> float:
        if maximum_day == minimum_day:
            return left + plot_width / 2
        return left + (
            plot_width * (day - minimum_day)
            / (maximum_day - minimum_day)
        )

    def y_for_value(value: float) -> float:
        clipped = max(0.0, min(1.0, value))
        return top + (1.0 - clipped) * plot_height

    grid = []
    for step in range(5):
        value = step / 4
        y = y_for_value(value)
        grid.append(
            f'<line class="chart-grid" x1="{left}" y1="{y:.2f}" '
            f'x2="{width-right}" y2="{y:.2f}"/>'
            f'<text class="axis-label" x="{left-9}" y="{y+4:.2f}" '
            f'text-anchor="end">{value:.2f}</text>'
        )

    polylines = []
    legends = []
    legend_width = plot_width / len(data.LAYER_SERIES)
    for index, (label, color) in enumerate(data.LAYER_SERIES):
        coordinates = " ".join(
            f'{x_for_day(int(data._number(point.get("day")))):.2f},'
            f'{y_for_value(float(point["values"][index])):.2f}'
            for point in points
        )
        polylines.append(
            f'<polyline data-series="{index}" points="{coordinates}" '
            f'fill="none" stroke="{color}" stroke-width="2.3"/>'
        )
        x = left + legend_width * index
        y = height - 30
        legends.append(
            f'<g class="legend-item" data-series="{index}" tabindex="0" '
            f'role="button" aria-label="{_escape(label)}の表示切替">'
            f'<line x1="{x:.2f}" y1="{y}" x2="{x+18:.2f}" y2="{y}" '
            f'stroke="{color}" stroke-width="3"/>'
            f'<text x="{x+23:.2f}" y="{y+4}">{_escape(label)}</text></g>'
        )

    day_marks = []
    for day in sorted(set(domain_days)):
        x = x_for_day(day)
        day_marks.append(
            f'<g class="day-mark" data-day="{day}">'
            f'<rect x="{x-12:.2f}" y="{top}" width="24" '
            f'height="{plot_height+26}" fill="transparent"/>'
            f'<line x1="{x:.2f}" y1="{top+plot_height}" '
            f'x2="{x:.2f}" y2="{top+plot_height+5}"/>'
            f'<text x="{x:.2f}" y="{top+plot_height+20}" '
            f'text-anchor="middle">{day}</text></g>'
        )

    marker_symbols = {
        "downed": "▼",
        "revived": "▲",
        "ending": "●",
    }
    markers_by_day: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for marker in markers:
        markers_by_day[
            int(data._number(marker.get("day")))
        ].append(marker)

    marker_markup = []
    for day in sorted(markers_by_day):
        day_markers = markers_by_day[day]
        count = len(day_markers)
        spacing = min(
            8.0,
            16.0 / max(1, count - 1),
        )
        for index, marker in enumerate(day_markers):
            kind = str(marker.get("kind"))
            turn = int(data._number(marker.get("turn")))
            offset = (index - (count - 1) / 2.0) * spacing
            x = x_for_day(day) + offset
            marker_markup.append(
                f'<text class="event-marker {kind}" '
                f'data-marker="true" data-day="{day}" data-turn="{turn}" '
                f'x="{x:.2f}" y="{top+13}" text-anchor="middle">'
                f'<title>{_escape(kind)} T{turn}</title>'
                f'{marker_symbols.get(kind, "●")}</text>'
            )

    return (
        f'<svg class="layer-chart" viewBox="0 0 {width} {height}" '
        'role="img" aria-labelledby="layer-chart-title layer-chart-desc">'
        '<title id="layer-chart-title">7層の推移</title>'
        '<desc id="layer-chart-desc">日ごとの7層推移と転機</desc>'
        + "".join(grid)
        + f'<line class="chart-axis" x1="{left}" y1="{top}" '
        f'x2="{left}" y2="{top+plot_height}"/>'
        + f'<line class="chart-axis" x1="{left}" y1="{top+plot_height}" '
        f'x2="{width-right}" y2="{top+plot_height}"/>'
        + "".join(polylines)
        + "".join(marker_markup)
        + "".join(day_marks)
        + f'<text class="axis-title" x="{width/2:.2f}" '
        f'y="{top+plot_height+43}" text-anchor="middle">日</text>'
        + "".join(legends)
        + "</svg>"
    )


def _genome_panel(
    genome: Mapping[str, Any],
    categories: Sequence[str],
) -> str:
    weights = data._as_mapping(genome.get("category_weight"))
    active_categories = set(categories)
    lead = _lead_category(genome, categories)
    bars = []
    for category in data.DEFAULT_CATEGORIES:
        value = data._number(weights.get(category))
        classes = ["gene"]
        suffix = ""
        style = ""
        if category == lead:
            classes.append("lead")
        if category not in active_categories:
            classes.append("unused")
            suffix = "（未使用）"
            style = ' style="opacity:0.45"'
        bars.append(
            f'<div class="{" ".join(classes)}"{style}>'
            f'<span>{_escape(category)}'
            f'<small>{_escape(suffix)}</small></span>'
            '<span class="gene-track">'
            f'<span class="gene-fill" style="width:{max(0.0, min(1.0, value))*100:.2f}%"></span>'
            "</span>"
            f'<strong>{value:.2f}</strong></div>'
        )
    return (
        '<section class="card genome"><h2>遺伝子</h2>'
        f'<div class="gene-bars">{"".join(bars)}</div>'
        '<p class="gene-scalars">'
        f'risk {data._number(genome.get("risk_tolerance")):+.2f} · '
        f'stance {data._number(genome.get("stance_shift_bias")):+.2f} · '
        f'novelty {data._number(genome.get("novelty_drive")):.2f}'
        "</p></section>"
    )


def _state_chips(
    state: Mapping[str, Any],
    world_meta: Mapping[str, Any],
    protagonist: str,
    antagonist: str,
) -> str:
    chips = []
    if state.get("zone") is not None:
        chips.append(str(state["zone"]))
    if state.get("vitality") is not None:
        chips.append(str(state["vitality"]))
    if state.get("strength") is not None:
        chips.append(f'強さ {data._number(state["strength"]):.0f}')

    names = data._as_mapping(world_meta.get("display_names"))
    for fact, belief in sorted(
        data._as_mapping(state.get("beliefs")).items()
    ):
        mapping = data._as_mapping(belief)
        value = mapping.get("value")
        confidence = mapping.get("confidence")
        fact_name = names.get(str(fact), fact)
        value_name = names.get(str(value), value)
        text = f"{fact_name}: {value_name}"
        if confidence is not None:
            text += f" ({data._number(confidence):.2f})"
        chips.append(text)

    for item, holder in sorted(
        data._as_mapping(state.get("holders")).items()
    ):
        chips.append(f"{item}: {holder}")
    phases = state.get("phase")
    if isinstance(phases, list) and phases:
        chips.append("節目: " + " / ".join(str(value) for value in phases))

    stance_out = state.get("stance_out")
    stance_in = state.get("stance_in")
    if stance_out is not None:
        chips.append(
            f"{protagonist}→{antagonist} {data._number(stance_out):+.2f}"
        )
    if stance_in is not None:
        chips.append(
            f"{antagonist}→{protagonist} {data._number(stance_in):+.2f}"
        )
    return "".join(
        f'<span class="chip">{_escape(chip)}</span>'
        for chip in chips
    )




def _motive_line(motive: Mapping[str, Any]) -> str:
    # WB-ROUTE-001 S4 §1.2: one line per protagonist decision the scene
    # carries a route for -- "[ラベル] 出来事 — 理由文". Scenes with no
    # "motives" key (rho=0 runs, or a run predating the route layer) never
    # call this at all, so their output is unchanged.
    label, css_class = explanation_ui.route_badge(motive)
    why = motive.get("why") or "はっきりした理由は記録されていない"
    return (
        f'<p class="detail motive-line">'
        f'<span class="tag {css_class}">{_escape(label)}</span> '
        f'{_escape(motive.get("event"))} — {_escape(why)}</p>'
    )


def _timeline(view_model: Mapping[str, Any]) -> str:
    grouped: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for scene in view_model["scenes"]:
        grouped[int(scene.get("day", 0))].append(scene)

    sections = []
    for day in sorted(grouped):
        state = data._as_mapping(view_model["day_states"].get(day))
        articles = []
        for scene in grouped[day]:
            turn = int(scene["turn"])
            events = "".join(
                f"<p>{_escape(event)}</p>"
                for event in scene.get("events", [])
            )
            details = "".join(
                f'<p class="detail">{_escape(detail)}</p>'
                for detail in scene.get("details", [])
            )
            tags = "".join(
                f'<span class="tag">{_escape(reason)}</span>'
                for reason in scene.get("reasons", [])
            )
            if scene.get("turning"):
                tags += '<span class="tag">転機候補</span>'
            motive_lines = "".join(
                _motive_line(motive) for motive in scene.get("motives") or []
            )
            delta = (
                f'<span class="delta">Δ {data._number(scene.get("delta_l1")):.2f}</span>'
                if data._number(scene.get("delta_l1")) > 0
                else ""
            )
            foreshadowing = "".join(
                f'<span class="tag foreshadow">{_escape(value)}</span>'
                for value in scene.get("foreshadowing", [])
            )
            npc = ""
            npc_rows = view_model["npc_by_turn"].get(turn, [])
            if npc_rows:
                npc = (
                    '<details class="npc">'
                    f"<summary>NPC の行動 ({len(npc_rows)})</summary>"
                    + "".join(
                        f"<p>{_escape(value)}</p>"
                        for value in npc_rows
                    )
                    + "</details>"
                )
            articles.append(
                f'<article class="scene" data-turn="{turn}" data-day="{day}">'
                f'<span class="turn">T{turn} {_escape(scene.get("slot"))}</span>'
                f"{events}{details}"
                f'<div class="scene-tags">{tags}{foreshadowing}{delta}</div>'
                f"{motive_lines}"
                f"{npc}</article>"
            )
        sections.append(
            f'<section class="day" data-day="{day}">'
            f'<h3>第{day}日 '
            '<span class="muted">日終わり時点</span>'
            f'{_state_chips(state, view_model["world_meta"], view_model["protagonist"], view_model["antagonist"])}</h3>'
            + "".join(articles)
            + "</section>"
        )

    omitted = int(
        data._number(view_model.get("turning_omitted"))
    )
    omitted_note = (
        '<p class="warning">'
        f"ほか {omitted} 件の転機候補は「主人公の全決定」で"
        "</p>"
        if omitted > 0
        else ""
    )
    return (
        f'<section class="timeline" data-view="{_escape(view_model["view"])}">'
        f"{omitted_note}"
        + "".join(sections)
        + "</section>"
    )


def _raw_table(rows: Sequence[Mapping[str, Any]]) -> str:
    rendered = []
    for row in rows:
        detail = {
            key: row[key]
            for key in ("args", "details", "delta", "classification")
            if key in row and row[key] not in (None, {}, [])
        }
        rendered.append(
            "<tr>"
            f"<td>{_escape(row.get('turn'))}</td>"
            f"<td>{_escape(row.get('day'))}</td>"
            f"<td>{_escape(row.get('slot'))}</td>"
            f"<td>{_escape(row.get('kind'))}</td>"
            f"<td>{_escape(row.get('subject'))}</td>"
            f"<td>{_escape(row.get('verb'))}</td>"
            f"<td>{_escape(row.get('result'))}</td>"
            f"<td>{_escape(row.get('effective'))}</td>"
            f"<td><code>{_escape(data._json_text(detail))}</code></td>"
            "</tr>"
        )
    if not rendered:
        return '<p class="muted">表示できるターン行がありません。</p>'
    return (
        '<div class="grid-wrap"><table class="raw-table">'
        "<thead><tr><th>turn</th><th>day</th><th>slot</th>"
        "<th>kind</th><th>subject</th><th>verb</th>"
        "<th>result</th><th>effective</th><th>details</th></tr></thead>"
        f'<tbody>{"".join(rendered)}</tbody></table></div>'
    )


def _route_breakdown(
    decisions: Sequence[Mapping[str, Any]], protagonist: str
) -> str | None:
    """WB-ROUTE-001 S4 §1.2: one summary line of the protagonist's decision
    kinds for this run, or None when no decision carries a policy.route
    (rho=0 runs, or a run predating the route layer -- unchanged output)."""
    counts = {"advance": 0, "prepare": 0, "detour": 0, "detour_none": 0, "lost": 0}
    seen = False
    for item in decisions:
        if item.get("subject") != protagonist:
            continue
        route = ((item.get("system") or {}).get("policy") or {}).get("route")
        if not route:
            continue
        seen = True
        kind = route.get("kind")
        if kind in ("advance", "prepare", "lost"):
            counts[kind] += 1
        elif kind == "detour":
            counts["detour"] += 1
            if route.get("cause") == "none":
                counts["detour_none"] += 1
    if not seen:
        return None
    return (
        f'前進 {counts["advance"]}／準備 {counts["prepare"]}／'
        f'寄り道 {counts["detour"]}〈うち理由なし {counts["detour_none"]}〉／'
        f'見通しなし {counts["lost"]}'
    )


def cell_page(
    repository: data.RunRepository,
    experiment_name: str,
    cell_key: str,
    *,
    view: str,
    job_store: Any = None,
) -> str:
    experiment = repository.experiment(experiment_name)
    model = data.cell_view(
        repository,
        experiment,
        cell_key,
        view=view,
    )
    experiment_url = f"/exp/{_url_segment(experiment_name)}"
    cell_base = (
        f"{experiment_url}/cell/{_url_segment(cell_key)}"
    )

    nav_links = []
    if model["prev_cell"]:
        previous = str(model["prev_cell"])
        nav_links.append(
            f'<a href="{experiment_url}/cell/{_url_segment(previous)}">'
            f"◀ {_escape(previous)}</a>"
        )
    if model["next_cell"]:
        following = str(model["next_cell"])
        nav_links.append(
            f'<a href="{experiment_url}/cell/{_url_segment(following)}">'
            f"{_escape(following)} ▶</a>"
        )

    mode_links = []
    labels = {
        "digest": "注目 12 場面",
        "decisions": "主人公の全決定",
        "all": "NPC の行動も",
    }
    for mode, label in labels.items():
        current = ' aria-current="page"' if view == mode else ""
        mode_links.append(
            f'<a href="{cell_base}?view={mode}"{current}>'
            f"{_escape(label)}</a>"
        )
    mode_links.append(f'<a href="{cell_base}/lineage">{_tip("lineage", "系譜")}</a>')

    parents = " × ".join(
        str(parent)
        for parent in model["parents"]
    ) or "—"
    route_breakdown = _route_breakdown(
        model["explanation"]["decisions"], model["protagonist"]
    )
    route_breakdown_html = (
        f'<p class="muted route-breakdown">{_escape(route_breakdown)}</p>'
        if route_breakdown
        else ""
    )
    story_panel = (
        '<section class="card story-section"><div class="section-heading"><h2>物語の流れ</h2>'
        f'<nav class="view-modes">{" ".join(mode_links)}</nav></div>'
        f'{route_breakdown_html}{_timeline(model)}</section>'
    )
    if model["explanation"].get("reader_summary"):
        reader_panel = '<section class="card reader-primary">' + reader_ui.panel(model["explanation"]) + '</section>'
    else:
        reader_panel = (
            '<section class="card"><h2>選択から後続へのつながり</h2>'
            + (reader_ui.generate_button(experiment_name, cell_key, url_segment=_url_segment)
               if _reader_generation_backend(job_store) and data.is_summarizable(model["explanation"])
               else '')
            + explanation_ui.panel(model["explanation"]) + '</section>'
        )
    decisions = ''.join(
        f'<details><summary>T{_escape(item["turn"])} {_escape(explanation_ui.action_text(item))}</summary>'
        + explanation_ui.panel(
            model["explanation"], item,
            details=(item["line"] == (model["explanation"]["representative"] or {}).get("line")
                     or item["turning"].get("confirmation") == "confirmed"),
        ) + '</details>'
        for item in model["explanation"]["decisions"]
        if view == "all" or item["subject"] == model["protagonist"]
    )
    reason_panel = reader_panel + '<section class="card decision-list"><h2>場面ごとの四項目</h2>' + decisions + '</section>'
    parents = " × ".join(str(parent) for parent in model["parents"]) or "—"
    data_panel = (
        '<section class="card elite-summary"><dl class="metric">'
        f'<dt>{_tip("quality", "q")}</dt><dd>{model["quality"]:.4f}</dd>'
        f'<dt>{_tip("elite_reach", "到達")}</dt><dd>{_reached_seeds(model["reach_rate"], len(model.get("seeds") or []))}</dd>'
        f'<dt>{_tip("generation", "世代")}</dt><dd>g{model["generation"]}</dd>'
        f'<dt>{_tip("seed", "seed")}</dt><dd>{_escape(model["seed"])}</dd>'
        f'<dt>{_tip("parents", "親")}</dt><dd>{_escape(parents)}</dd>'
        f'<dt>{_tip("layers", "layers")}</dt><dd>{_escape(model["layers_path"])}</dd>'
        '</dl></section>'
        + _genome_panel(model["genome"], model["categories"])
        + '<section class="card chart-card"><h2>7層の推移</h2>'
        '<p class="muted">x = 日。▼ downed、▲ revived、● ending。</p>'
        f'{layers_svg(model["layer_points"], model["markers"])}</section>'
        '<details class="raw"><summary>模範ランのターン列（生ログ）</summary>'
        f'{_raw_table(model["turn_rows"])}</details>'
    )
    from viewer import review_pages
    return review_pages.candidate(repository, experiment_name, cell_key, model,
                                  story_panel, reason_panel, data_panel, nav_links, job_store=job_store)



def _gene_shift_text(gene_shift: Sequence[Mapping[str, Any]] | None) -> str:
    """The "動いた性格" one-liner: the biggest-|delta| scalar, plus a second
    one only when the rest moved by more than ±0.05 (design doc §(d))."""

    if not gene_shift:
        return "（起点。比べる前がありません）"
    leading = gene_shift[0]
    lines = [f'{leading["label"]} {leading["before"]:.2f}→{leading["after"]:.2f}']
    rest = gene_shift[1:]
    if rest and any(abs(item["delta"]) > 0.05 for item in rest):
        second = rest[0]
        lines.append(f'{second["label"]} {second["before"]:.2f}→{second["after"]:.2f}')
    elif rest:
        lines.append("他は±0.05以内")
    return "／".join(lines)


def _lineage_action_text(action: Mapping[str, Any] | None) -> str:
    if not action:
        return "—"
    verb = explanation_ui.verb_label(action.get("verb"))
    args = "・".join(str(value) for value in action.get("args") or [])
    return f"{verb}（{args}）" if args else verb


def _lineage_outcome_text(outcome: Mapping[str, Any] | None) -> str:
    if outcome is None:
        return "再現できず不明"
    parts = []
    if outcome.get("ending"):
        parts.append(f"結末: {outcome['ending']}")
    elif outcome.get("downed"):
        parts.append("道中で倒れた")
    reached = outcome.get("reached")
    parts.append("到達" if reached else "未到達" if reached is False else "到達: 不明")
    parts.append(f'仲間 {outcome.get("allies_final", 0)} 人')
    win_probability = outcome.get("win_probability")
    if win_probability is not None:
        parts.append(f"決戦勝率 {win_probability:.0%}")
    return " ／ ".join(parts)


def _candidate_table(
    candidates: Sequence[Mapping[str, Any]],
    stats: Mapping[str, Any] | None = None,
) -> str:
    if not candidates:
        return '<p class="muted">候補の記録がありません（record_explanations 対象外）。</p>'
    note = ""
    if stats is not None and stats.get("total_candidates") is not None:
        note = (
            f'<p class="muted">記録 {stats.get("recorded_candidates")} / '
            f'全 {stats.get("total_candidates")} 件。省略: '
            f'{"あり" if stats.get("truncated") else "なし"}。</p>'
        )
    rows = []
    for candidate in sorted(
        candidates,
        key=lambda item: -(data._number(item.get("probability"), 0.0)),
    ):
        verb = explanation_ui.verb_label(candidate.get("verb"))
        args = "・".join(str(value) for value in candidate.get("args") or [])
        probability = candidate.get("probability")
        width = round(data._number(probability, 0.0) * 100)
        mark = " ✓" if candidate.get("selected") else ""
        rows.append(
            "<tr>"
            f'<td>{_escape(verb)}{(" " + _escape(args)) if args else ""}{mark}</td>'
            f'<td><div class="candidate-bar"><div class="candidate-bar-fill" '
            f'style="width:{width}%"></div></div></td>'
            f'<td>{f"{probability:.0%}" if probability is not None else "—"}</td>'
            "</tr>"
        )
    return (
        note
        + '<table class="candidate-table"><thead><tr>'
        "<th>行動</th><th></th><th>確率</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


def _lineage_band(
    ancestry: Sequence[Mapping[str, Any]],
    turnings: Sequence[Mapping[str, Any]],
    first_reach_index: int | None,
) -> str:
    turning_indices = {turning["child_index"] for turning in turnings}
    last = len(ancestry) - 1
    items = []
    for index, node in enumerate(ancestry):
        classes = ["lineage-node"]
        labels = []
        if index == 0:
            labels.append(f"g{node['generation']}")
        if index == first_reach_index:
            classes.append("reach")
            labels.append("初到達")
        if index in turning_indices:
            classes.append("turning")
            labels.append("転機")
        if index == last:
            classes.append("final")
            labels.append(f"g{node['generation']}(最終)")
        if node.get("rerun_error"):
            classes.append("broken")
        label_html = (
            f'<span class="label">{_escape(" / ".join(labels))}</span>'
            if labels
            else ""
        )
        items.append(
            f'<li class="{" ".join(classes)}" title="世代 g{node["generation"]}">'
            f'<span class="dot"></span>{label_html}</li>'
        )
    return f'<ol class="lineage-band">{"".join(items)}</ol>'


def _turning_card(
    title: str,
    *,
    generation: int,
    body_lines: Sequence[str],
    href: str | None = None,
    current: bool = False,
) -> str:
    inner = (
        f"<h3>{_escape(title)}</h3><p class=\"muted\">g{generation}</p>"
        + "".join(f"<p>{line}</p>" for line in body_lines)
    )
    if href is None:
        return f'<div class="card turning-card">{inner}</div>'
    current_attr = ' aria-current="page"' if current else ""
    return f'<a class="card turning-card" href="{href}"{current_attr}>{inner}</a>'


def _turning_detail(turning: Mapping[str, Any]) -> str:
    present = turning.get("present")
    header = f'<p>T{_escape(turning["turn"])}（親側 T{_escape(turning["parent_turn"])}）'
    if present:
        header += f'　同席: {_escape("、".join(present))}'
    header += "</p>"
    trait_series = turning.get("trait_series")
    trait_html = (
        f'<p class="muted">{_escape(trait_series["label"])}の推移</p>'
        f'{sparkline(trait_series["values"])}'
        if trait_series
        else ""
    )
    body = (
        header
        + f'<p>{_gene_shift_text(turning["gene_shift"])}</p>'
        + trait_html
        + '<div class="turning-columns">'
        + "<div><h3>親の選択肢</h3>"
        + _candidate_table(turning["parent_candidates"], turning.get("parent_candidate_stats"))
        + f'<p>実際の選択: {_lineage_action_text(turning["parent_action"])}</p></div>'
        + "<div><h3>子の選択肢</h3>"
        + _candidate_table(turning["child_candidates"], turning.get("child_candidate_stats"))
        + f'<p>実際の選択: {_lineage_action_text(turning["child_action"])}</p></div>'
        + "</div>"
        + f'<p>{_lineage_outcome_text(turning["outcome"])}</p>'
    )
    return body


def lineage_page(
    repository: data.RunRepository,
    experiment_name: str,
    cell_key: str,
    *,
    turning_index: int | None = None,
    job_store: Any = None,
    point: str | None = None,
    tab: str = "choices",
    view: str = "key",
    expected_ref: str | None = None,
) -> str:
    from viewer import lineage_pages
    return lineage_pages.render(
        repository, experiment_name, cell_key, turning_index=turning_index,
        job_store=job_store, point=point, tab=tab, view=view, expected_ref=expected_ref,
    )


def _reader_generation_backend(job_store):
    """WB-EXPLAIN-009: the configured 文章生成 backend, or None when there is
    no control root (read-only exe) or the backend is "none". A pure
    settings read -- never probes a remote backend at render time."""
    if job_store is None:
        return None
    from execution.output_settings import resolve_generation
    settings_path = job_store.configs.repo / "settings.json"
    backend = resolve_generation(settings_path)["backend"]
    return backend if backend != "none" else None


def compare_page(repository, experiment_name, cells, *, job_store=None):
    from viewer import review_pages
    return review_pages.compare(repository, experiment_name, cells, job_store=job_store)



def raw_page(repository, experiment_name, cell_key, line=None, *, job_store=None, **options):
    from viewer import raw_pages
    return raw_pages.render(repository, experiment_name, cell_key, line, job_store=job_store, **options)
