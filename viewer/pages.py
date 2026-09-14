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

from viewer import data


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
        return f"/jobs/{_url_segment(str(job_id))}" if job_id else "/jobs"
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


def _header_pickers(
    world: Mapping[str, Any] | None,
    run: str | None,
) -> str:
    pickers = []
    if world is not None:
        pickers.append(
            '<span class="picker"><span class="picker-label">世界</span>'
            f'<span class="picker-value">{_escape(world.get("name"))}</span></span>'
        )
    if run is not None:
        pickers.append(
            '<span class="picker"><span class="picker-label">実験</span>'
            f'<span class="picker-value">{_escape(run)}</span></span>'
        )
    return (
        '<div class="header-pickers">'
        '<a class="home-cell" href="/">⌂ ホーム</a>'
        + "".join(pickers)
        + '<span class="header-links">'
        '<a href="/configs">設定</a><a href="/jobs">実行履歴</a>'
        "</span>"
        '<a class="brand" href="/">WorldBloom</a>'
        "</div>"
    )


def document(
    title: str,
    body: str,
    *,
    crumbs: list[tuple[str, str]] = (),
    world: Mapping[str, Any] | None = None,
    run: str | None = None,
    output_run: str | None = None,
    phase: str | None = None,
    phases: Mapping[str, Any] | None = None,
    lead: str | None = None,
    next_action: tuple[str, str] | None = None,
) -> str:
    # Callers that already looked up phase_status() (experiment/cell/compare/
    # raw pages) get the stage link's catalog-id distinction for free; other
    # callers pass output_run explicitly when they know it (see workbench_
    # pages.py / output_pages.py), or leave it unset when there is none.
    if output_run is None and phases:
        output_run = phases.get("catalog_run_id")
    crumb_items = [
        '<a href="/">ホーム</a>'
    ]
    for label, href in crumbs:
        crumb_items.append(
            f'<a href="{html.escape(href, quote=True)}">'
            f"{_escape(label)}</a>"
        )
    breadcrumb = " <span aria-hidden=\"true\">›</span> ".join(
        crumb_items
    )
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
    return (
        "<!doctype html>"
        '<html lang="ja"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_escape(title)} | WorldBloom</title>"
        '<link rel="stylesheet" href="/static/app.css">'
        '<script src="/static/app.js" defer></script>'
        '<script src="/static/workbench.js" defer></script>'
        "</head><body>"
        '<div class="app-shell">'
        '<header class="site-header">'
        + _header_pickers(world, run)
        + _phase_band(phase=phase, phases=phases, world=world, run=run, output_run=output_run)
        + f'<nav class="crumbs" aria-label="パンくず">{breadcrumb}</nav>'
        "</header>"
        f'<main class="page-shell"><h1>{_escape(title)}</h1>{lead_html}{body}</main>'
        '<div id="toast" role="status" aria-live="polite"></div>'
        "</div>"
        "</body></html>"
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
        "選定状態: Sifting での判断。採用（adopted）＝生成に進める、"
        "保留（held）、除外（rejected）、未分類（unclassified）。"
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
        "（例: ollama / qwen3.5:9b）。画面を開いただけでは生成しない。"
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


def _experiment_card(meta: Mapping[str, Any]) -> str:
    name = str(meta["name"])
    href = f"/exp/{_url_segment(name)}"
    date = datetime.fromtimestamp(
        float(meta["archive_mtime"])
    ).strftime("%Y-%m-%d %H:%M")
    seeds = meta.get("seeds")
    seed_count = len(seeds) if isinstance(seeds, list) else None
    truths = data._display(meta.get("truths")) if meta.get("truths") else "—"
    ending = data._display(meta.get("target_ending"))
    reach_values = meta.get("reach_series", [])
    quality_values = meta.get("quality_series", [])
    quality_text = (
        f"{quality_values[-1]:.2f}"
        if quality_values
        else "—"
    )
    return (
        f'<article class="card run-card" data-experiment="{_escape(name)}">'
        '<div class="card-heading">'
        f'<h3><a href="{href}">{_escape(name)}</a></h3>'
        f'<span class="date">{_escape(date)}</span>'
        "</div>"
        f'<p><span class="genre">{_escape(meta["genre"])} '
        f'{_escape(meta["world"])}</span></p>'
        '<p class="facts">'
        f'{_escape(meta["generations"])}世代'
        f' · {_escape(meta.get("population"))}個体'
        f' · {_escape(seed_count)}シード'
        f' · 結末 {_escape(ending)}'
        f' · 真相 {_escape(truths)}'
        "</p>"
        '<div class="run-stats">'
        f'<span class="cells">{_tip("cells", "占有")} '
        f'<b class="{_grade(_ratio(meta["cells"], meta["grid_size"]))}">'
        f'{_escape(meta["cells"])}/{_escape(meta["grid_size"])}</b></span>'
        f'{sparkline(meta.get("occupied_series") or [])}'
        f'<span>{_tip("dissimilarity", "相異度")} '
        f'<b class="{_grade(_last(meta.get("dissimilarity_series") or []))}">'
        f'{_escape(_series_text(meta.get("dissimilarity_series") or []))}</b></span>'
        f'<span>{_tip("quality", "q̄")} '
        f'<b class="{_grade(_last(meta.get("quality_series") or []))}">'
        f'{_escape(quality_text)}</b></span>'
        f'<span class="reach">{_tip("reach", "到達率")} '
        f'{_escape(_gate_text(reach_values))}</span>'
        "</div>"
        '<p class="outputs">'
        f'あらすじ {_escape(meta["synopsis_ok"])}/{_escape(meta["cells"])}'
        f' · 選定 {_escape(meta["selected"])}'
        f' · 本文 {_escape(meta["story_ok"])}'
        f' · engine {_escape(str(meta["engine_hash"])[:12] or "—")}'
        "</p>"
        f'<p class="open"><a class="button" href="{href}">開く</a></p>'
        "</article>"
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


def _world_section(
    world_name: str,
    info: Mapping[str, str] | None,
    majors: Sequence[Mapping[str, Any]],
    minors: Sequence[Mapping[str, Any]],
    repository: data.RunRepository,
    job_store: Any,
    history: Sequence[Mapping[str, Any]] | None,
    outputs: Sequence[Mapping[str, Any]] | None,
) -> str:
    # info = {"id": projects/<id> directory name, "genre": templates/<genre>}
    # (from _project_info). A world created via /worlds/new can have an id
    # that differs from its genre, so links to the world use the id and only
    # the ?template= preset uses the genre.
    info = info or {}
    project_id = info.get("id") or None
    genre = info.get("genre") or None
    world_id = _url_segment(project_id or world_name)
    if project_id:
        new_run_href = f"/configs/new?project={_url_segment(project_id)}"
        if genre:
            new_run_href += f"&template={_url_segment(genre)}"
        edit_link = f'<a class="button" href="/worlds/{_url_segment(project_id)}">世界を編集</a>'
    else:
        new_run_href = "/configs/new"
        edit_link = ""
    resolved = genre
    heading = (
        f'<section class="world-group" id="world-{world_id}">'
        '<div class="section-heading">'
        f'<div><span class="eyebrow">ジャンル: {_escape(resolved or "不明")}</span>'
        f"<h2>{_escape(world_name)}</h2></div>"
        f'{edit_link}'
        f'<a class="button primary" href="{_escape(new_run_href)}">'
        "この世界で新しい実験を回す</a>"
        "</div>"
    )
    major_rows = "".join(
        _progress_row(meta, repository, job_store, history, outputs) for meta in majors
    )
    body = (
        f'<div class="progress-dashboard">{major_rows}</div>'
        if major_rows
        else '<p class="muted">まだ実験がありません。</p>'
    )
    minor_block = ""
    if minors:
        minor_rows = "".join(
            _progress_row(meta, repository, job_store, history, outputs) for meta in minors
        )
        minor_block = (
            '<details class="minor-runs">'
            f"<summary>その他の短いラン ({len(minors)})</summary>"
            f'<div class="progress-dashboard">{minor_rows}</div></details>'
        )
    return heading + body + minor_block + "</section>"


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


_HOME_ACTIONS = (
    '<p class="actions">'
    '<a class="button primary" href="/worlds/new">新しい世界を作る</a>'
    '<a class="button" href="/worlds">世界とジャンルの一覧</a>'
    "</p>"
)


def index_page(repository: data.RunRepository, *, job_store: Any = None) -> str:
    groups, minor = data.grouped_experiments(repository)
    by_world: dict[str, list[Mapping[str, Any]]] = dict(groups)
    minor_by_world: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for meta in minor:
        minor_by_world[str(meta["world"])].append(meta)

    # Fetched once for the whole dashboard: data.phase_status() otherwise
    # calls catalog.history()/job_store.outputs() again for every row, which
    # made the home page take several seconds with a couple dozen runs.
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

    project_info = _project_info(job_store)
    world_order: list[str] = []
    seen: set[str] = set()
    for name in project_info:
        world_order.append(name)
        seen.add(name)
    for name in sorted(set(by_world) | set(minor_by_world)):
        if name not in seen:
            world_order.append(name)
            seen.add(name)

    if not world_order:
        return document(
            "世界を選ぶ",
            _HOME_ACTIONS
            + '<section class="card"><p>表示できる世界も実験もありません。</p>'
            '<p class="muted">各実験ディレクトリに '
            "<code>archive.json</code> が必要です。</p></section>",
            phase="world",
        )

    sections = [
        _world_section(
            world_name,
            project_info.get(world_name),
            by_world.get(world_name, []),
            minor_by_world.get(world_name, []),
            repository,
            job_store,
            history,
            outputs,
        )
        for world_name in world_order
    ]
    body = (
        _HOME_ACTIONS
        + '<p class="lead">世界を選び、実験を回し、Sifting で候補を選んで'
        "上映します。</p>"
        + "".join(sections)
    )
    return document("世界を選ぶ", body, phase="world")


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
    experiment = repository.experiment(experiment_name)
    archive = repository.archive(experiment)
    cells = data._as_mapping(archive.get("cells"))
    meta = data.experiment_meta(repository, experiment)
    categories, bins = data._ordered_axes(
        meta["categories"],
        meta["bins"],
        cells,
    )
    selected = repository.selection(experiment)

    headings = "".join(
        f"<th scope=\"col\">{_escape(bin_name)}</th>"
        for bin_name in bins
    )
    body_rows = []
    for category in categories:
        columns = []
        for bin_name in bins:
            cell_key = f"{category}|{bin_name}"
            elite = cells.get(cell_key)
            if isinstance(elite, Mapping):
                columns.append(
                    _cell_markup(
                        repository,
                        experiment,
                        experiment_name,
                        cell_key,
                        elite,
                        meta["world_meta"],
                        meta["categories"],
                        selected,
                        len(meta.get("seeds") or []),
                    )
                )
            else:
                columns.append('<td class="empty">空</td>')
        body_rows.append(
            f'<tr><th scope="row">{_escape(category)}</th>'
            + "".join(columns)
            + "</tr>"
        )

    reach = meta["reach_series"]
    occupied = meta["occupied_series"]
    quality = meta["quality_series"]
    dissimilarity = meta.get("dissimilarity_series") or []
    strip = (
        '<section class="card metric-strip">'
        f'<div><strong>{_tip("cells", "占有マス")}</strong>'
        f'{sparkline(occupied)}'
        f'<span class="{_grade(_ratio(meta["cells"], meta["grid_size"]))}">'
        f'{_escape(meta["cells"])}/{_escape(meta["grid_size"])}</span></div>'
        f'<div><strong>{_tip("dissimilarity", "相異度")}</strong>'
        f'{sparkline(dissimilarity)}'
        f'<span class="{_grade(_last(dissimilarity))}">'
        f'{_escape(_series_text(dissimilarity))}</span>'
        f'{_dissimilarity_note(dissimilarity)}</div>'
        f'<div><strong>{_tip("quality", "q̄")}</strong>'
        f'{sparkline(quality)}'
        f'<span class="{_grade(_last(quality))}">'
        f'{_escape(_series_text(quality))}</span></div>'
        f'<div class="gate"><strong>{_tip("reach", "到達率")}</strong>'
        f'<span>{_escape(_gate_text(reach))}</span>'
        '<span class="gate-note">アーカイブへの入場ゲート。'
        '上げる対象ではない</span></div>'
        '<p class="strip-detail">'
        f'volatility 閾値 {_escape(_threshold_text(meta["thresholds"]))}'
        f' · 結末 {_escape(meta.get("target_ending"))}'
        f' · keep={_escape(meta.get("keep"))}'
        "</p></section>"
    )

    order = [
        f"{category}|{bin_name}"
        for category in categories
        for bin_name in bins
    ]
    tray_links = [
        (
            f'<a href="/exp/{_url_segment(experiment_name)}'
            f'/cell/{_url_segment(cell)}">★ {_escape(cell)}</a>'
        )
        for cell in order
        if cell in selected
    ]
    tray = (
        '<aside class="tray"><strong>Sifting トレイ:</strong> '
        + (" ".join(tray_links) if tray_links else "選定なし")
        + "</aside>"
    )

    workbench_links = ""
    if repository.catalog is not None:
        run_id = repository.catalog.run_id(experiment_name)
        if run_id.startswith("legacy-"):
            run_id = repository.catalog.register_legacy(experiment_name)
        links = [
            f'<a href="/runs/{_url_segment(run_id)}/candidates">候補一覧（世代・seed別）</a>',
            '<a href="/selected">横断 Sifting トレイ</a>',
        ]
        manifest_path = repository.safe_path(experiment, "manifest.json")
        if manifest_path.is_file():
            manifest = data._read_json(manifest_path)
            config_id = manifest.get("config_id") if isinstance(manifest, dict) else None
            if config_id:
                links.append(f'<a href="/configs/{_url_segment(config_id)}">実行設定</a>')
        workbench_links = f'<p class="workbench-links">{" ".join(links)}</p>'

    body = (
        f'<form id="compare-cells" method="get" action="/exp/{_url_segment(experiment_name)}/compare"><p>格子から2〜4候補を選択して <button type="submit">四項目で比較</button></p></form>'
        f"{workbench_links}"
        '<p class="experiment-meta">'
        f'<span class="genre">{_escape(meta["genre"])}</span> '
        f'{_escape(meta["world"])} · '
        f'{datetime.fromtimestamp(float(meta["archive_mtime"])).strftime("%Y-%m-%d %H:%M")}'
        "</p>"
        f"{strip}"
        '<section class="card grid-wrap">'
        '<table class="archive-grid">'
        f'<thead><tr><th scope="col">カテゴリ</th>{headings}</tr></thead>'
        f'<tbody>{"".join(body_rows)}</tbody>'
        "</table></section>"
        f"{_command_block(repository, meta)}"
        f"{tray}"
    )
    world = _experiment_world(meta, job_store)
    phases = data.phase_status(repository, experiment_name, job_store=job_store)
    next_label, next_href = next_action_for(experiment_name, phases)
    return document(
        f"実験: {experiment_name}",
        body,
        crumbs=[(experiment_name, f"/exp/{_url_segment(experiment_name)}")],
        world=world,
        run=experiment_name,
        phase="sifting",
        phases=phases,
        lead="格子で候補を吟味し、★で選定します。",
        next_action=(next_label, next_href),
    )


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


def _output_panel(
    title: str,
    entry: Mapping[str, Any] | None,
    text: str | None,
    backend: str | None = None,
) -> str:
    if entry is None:
        return (
            f'<section class="card output-panel"><h2>{_escape(title)}</h2>'
            '<p class="muted">該当項目がありません。</p></section>'
        )
    status = str(entry.get("status", "—"))
    backend_text = f" · {backend}" if backend else ""
    error = entry.get("error")
    return (
        f'<section class="card output-panel"><h2>{_escape(title)}</h2>'
        f'<p><span class="badge">{_escape(status + backend_text)}</span></p>'
        + (
            f"<pre>{_escape(text)}</pre>"
            if text
            else '<p class="muted">まだ生成されていません。</p>'
        )
        + (
            f'<p class="error">{_escape(error)}</p>'
            if error
            else ""
        )
        + "</section>"
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
    endpoint = f"{experiment_url}/selection"
    checked = " checked" if model["selected"] else ""

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

    parents = " × ".join(
        str(parent)
        for parent in model["parents"]
    ) or "—"
    synopsis_entry = model["synopsis"]
    synopsis_text = (
        str(synopsis_entry.get("synopsis"))
        if isinstance(synopsis_entry, Mapping)
        and synopsis_entry.get("synopsis")
        else None
    )

    body = (
        '<div class="cell-navigation">'
        f'<a href="{experiment_url}">← 格子</a>'
        f'<span class="neighbors">{" ".join(nav_links)}</span></div>'
        '<section class="card elite-summary">'
        '<label class="selection large">'
        f'<input type="checkbox" class="selection-toggle" '
        f'data-endpoint="{endpoint}" data-cell="{_escape(cell_key)}"{checked}>'
        "<span>本文候補として選定する</span></label>"
        '<dl class="metric">'
        f'<dt>{_tip("quality", "q")}</dt>'
        f'<dd>{model["quality"]:.4f}</dd>'
        f'<dt>{_tip("elite_reach", "到達")}</dt><dd>'
        f'{_reached_seeds(model["reach_rate"], len(model.get("seeds") or []))}'
        "</dd>"
        f'<dt>{_tip("generation", "世代")}</dt>'
        f'<dd>g{model["generation"]}</dd>'
        f'<dt>{_tip("seed", "seed")}</dt>'
        f'<dd>{_escape(model["seed"])}</dd>'
        f'<dt>{_tip("parents", "親")}</dt>'
        f'<dd>{_escape(parents)}</dd>'
        f'<dt>{_tip("layers", "layers")}</dt>'
        f'<dd>{_escape(model["layers_path"])}</dd>'
        "</dl></section>"
        + ('' if model["explanation"].get("reader_summary") else
           '<section class="card"><h2>選択から後続へのつながり</h2>'
           + explanation_ui.panel(model["explanation"]) + '</section>')
        + f'{_genome_panel(model["genome"], model["categories"])}'
        '<section class="card chart-card"><h2>7層の推移</h2>'
        '<p class="muted">x = 日。▼ downed、▲ revived、● ending。</p>'
        f'{layers_svg(model["layer_points"], model["markers"])}</section>'
        '<section class="card story-section">'
        '<div class="section-heading"><h2>物語</h2>'
        f'<nav class="view-modes">{" ".join(mode_links)}</nav></div>'
        f'{_timeline(model)}</section>'
        + '<section class="card"><h2>場面ごとの四項目</h2>'
        + "".join(f'<details><summary>T{_escape(item["turn"])} {_escape(explanation_ui.action_text(item))}</summary>' + explanation_ui.panel(model["explanation"], item, details=(item["line"] == (model["explanation"]["representative"] or {}).get("line") or item["turning"].get("confirmation") == "confirmed")) + "</details>" for item in model["explanation"]["decisions"] if view == "all" or item["subject"] == model["protagonist"])
        + "</section>"
        +
        '<details class="raw">'
        '<summary>模範ランのターン列（生ログ）</summary>'
        f'{_raw_table(model["turn_rows"])}</details>'
        '<div class="outputs-grid">'
        f'{_output_panel("あらすじ", synopsis_entry, synopsis_text, model["synopsis_backend"])}'
        f'{_output_panel("本文", model["story"], model["story_text"])}'
        "</div>"
    )
    if model["explanation"].get("reader_summary"):
        body = '<section class="card reader-primary">' + reader_ui.panel(model["explanation"]) + "</section>" + body
    phases = data.phase_status(repository, experiment_name, job_store=job_store)
    return document(
        f"{experiment_name} / {cell_key}",
        body,
        crumbs=[
            (experiment_name, experiment_url),
            (cell_key, cell_base),
        ],
        run=experiment_name,
        phase="sifting",
        phases=phases,
        lead="この候補の経緯を四項目で確かめます。",
        next_action=("格子に戻る →", experiment_url),
    )


def compare_page(repository, experiment_name, cells, *, job_store=None):
    experiment = repository.experiment(experiment_name)
    phases = data.phase_status(repository, experiment_name, job_store=job_store)
    grid_href = f"/exp/{_url_segment(experiment_name)}"
    lead = "候補を同じ四項目で並べて比べます。"
    next_action = ("格子に戻る →", grid_href)
    if not 2 <= len(cells) <= 4 or len(set(cells)) != len(cells):
        return document("四項目で比較",
                        '<p role="alert">比較する異なる候補を2〜4件選んでください。</p>'
                        + f'<p><a href="{grid_href}">← 格子で候補を選ぶ</a></p>',
                        run=experiment_name, phase="sifting", phases=phases,
                        lead=lead, next_action=next_action)
    explanations = [data.cell_explanation(repository, experiment, cell) for cell in cells]
    same = len({x["trajectory_signature"] for x in explanations}) == 1
    body = f'<p><a href="{grid_href}">← 格子で候補を選ぶ</a></p>'
    body += '<p>それぞれの候補で、何が起きたかを読み比べられます。</p>'
    has_reader = any(x.get("reader_summary") for x in explanations)
    if has_reader:
        body += '<details><summary>記録上の比較</summary>'
    body += '<p>主人公の行動・対象・結果の並びは同じ筋です。</p>' if same else '<p>主人公の行動・対象・結果の並びに差があります。物語品質の優劣は判定していません。</p>'
    if has_reader:
        body += "</details>"
    body += '<div class="explanation-comparison">'
    for cell, explanation in zip(cells, explanations):
        body += f'<section class="card"><h2><a href="{explanation_ui.base_url(explanation)}">{_escape(cell)}</a></h2>'
        body += reader_ui.panel(explanation) + '</section>'
    return document("四項目で比較", body + '</div>', run=experiment_name, phase="sifting", phases=phases,
                     lead=lead, next_action=next_action)


def raw_page(repository, experiment_name, cell_key, line=None, *, job_store=None):
    experiment = repository.experiment(experiment_name)
    explanation = data.cell_explanation(repository, experiment, cell_key)
    # The source path is obtained only through the repository containment check.
    from pathlib import Path
    raw = Path(explanation["source"]["layers_path"]).read_text(encoding="utf-8-sig")
    body = f'<p><a href="{explanation_ui.base_url(explanation)}">← 四項目</a></p>'
    body += f'<p>SHA-256: {_escape(explanation["source"]["sha256"])}</p><div class="raw-lines">'
    lines = raw.splitlines()
    if line is not None:
        try:
            line = int(line)
        except (TypeError, ValueError) as error:
            raise data.BadRequest("line must be a 1-based integer") from error
        if not 1 <= line <= len(lines):
            raise data.BadRequest("line is outside the source log")
        first, last = max(1, line - 3), min(len(lines), line + 3)
        body += f'<p>原ログ全{len(lines)}行のうちL{first}〜L{last}。<a href="{explanation_ui.base_url(explanation)}/raw">全文</a></p>'
    else:
        first, last = 1, len(lines)
    for number in range(first, last + 1):
        value = lines[number - 1]
        body += f'<pre id="L{number}"><a href="?line={number}#L{number}">L{number}</a> {_escape(value)}</pre>'
    phases = data.phase_status(repository, experiment_name, job_store=job_store)
    return document(
        f"{cell_key} 原ログ", body + '</div>',
        run=experiment_name, phase="sifting", phases=phases,
    )
