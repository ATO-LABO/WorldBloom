"""Deterministic HTML and SVG rendering for the WorldBloom viewer."""

from __future__ import annotations

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


def document(
    title: str,
    body: str,
    *,
    crumbs: list[tuple[str, str]] = (),
) -> str:
    crumb_items = [
        '<a href="/">実験一覧</a>'
    ]
    for label, href in crumbs:
        crumb_items.append(
            f'<a href="{html.escape(href, quote=True)}">'
            f"{_escape(label)}</a>"
        )
    breadcrumb = " <span aria-hidden=\"true\">›</span> ".join(
        crumb_items
    )
    return (
        "<!doctype html>"
        '<html lang="ja"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_escape(title)} | WorldBloom</title>"
        '<link rel="stylesheet" href="/static/app.css">'
        '<script src="/static/app.js" defer></script>'
        "</head><body>"
        '<header class="site-header">'
        '<a class="brand" href="/">WorldBloom</a>'
        f'<nav class="crumbs" aria-label="パンくず">{breadcrumb}</nav>'
        "</header>"
        f'<main><h1>{_escape(title)}</h1>{body}</main>'
        '<div id="toast" role="status" aria-live="polite"></div>'
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
    reach_text = _series_text(reach_values, percent=True)
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
        f'<span class="reach">到達率 {_escape(reach_text)}</span>'
        f'{sparkline(reach_values)}'
        f'<span>占有 {_escape(meta["cells"])}/{_escape(meta["grid_size"])}</span>'
        f'<span>q̄ {_escape(quality_text)}</span>'
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


def index_page(repository: data.RunRepository) -> str:
    groups, minor = data.grouped_experiments(repository)
    if not groups and not minor:
        return document(
            "WorldBloom 実験一覧",
            '<section class="card"><p>表示できる実験がありません。</p>'
            '<p class="muted">各実験ディレクトリに '
            "<code>archive.json</code> が必要です。</p></section>",
        )

    sections = []
    for world, metas in groups:
        genre = metas[0]["genre"] if metas else "不明"
        sections.append(
            '<section class="run-group">'
            f"<h2>{_escape(genre)} {_escape(world)}</h2>"
            f'<div class="run-list">{"".join(_experiment_card(meta) for meta in metas)}</div>'
            "</section>"
        )
    if minor:
        sections.append(
            '<details class="minor-runs">'
            f"<summary>その他の短いラン（{len(minor)}件）</summary>"
            f'<div class="run-list">{"".join(_experiment_card(meta) for meta in minor)}</div>'
            "</details>"
        )
    return document("WorldBloom 実験一覧", "".join(sections))


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
    return (
        '<td class="occupied">'
        '<div class="cell-head">'
        f'<a href="{href}"><strong>{_escape(cell_key)}</strong></a>'
        f'<button type="button" class="selection-toggle" '
        f'data-endpoint="{endpoint}" data-cell="{_escape(cell_key)}" '
        f'aria-pressed="{pressed}" title="選定を切り替える">{star}</button>'
        "</div>"
        '<dl class="cell-metrics">'
        f'<dt>q</dt><dd>{data._number(elite.get("quality")):.4f}</dd>'
        f'<dt>到達</dt><dd>'
        f'{_reached_seeds(data._number(elite.get("reach_rate")), seed_count)}'
        "</dd>"
        f'<dt>世代</dt><dd>g{int(data._number(elite.get("generation")))}</dd>'
        f'<dt>主導</dt><dd>{_escape(_lead_category(genome, categories))}</dd>'
        "</dl>"
        f'<p class="hook">{_escape(hook) if hook else "場面情報なし"}</p>'
        "</td>"
    )


def experiment_page(
    repository: data.RunRepository,
    experiment_name: str,
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
    strip = (
        '<section class="card metric-strip">'
        '<div><strong>到達率</strong>'
        f'{sparkline(reach)}<span>{_escape(_series_text(reach, percent=True))}</span></div>'
        '<div><strong>占有</strong>'
        f'{sparkline(occupied)}<span>{_escape(meta["cells"])}/{_escape(meta["grid_size"])}</span></div>'
        '<div><strong>q̄</strong>'
        f'{sparkline(quality)}<span>{_escape(_series_text(quality))}</span></div>'
        f'<div><strong>相異度</strong><span>{_escape(meta.get("dissimilarity"))}</span></div>'
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
        '<aside class="tray"><strong>選定トレイ:</strong> '
        + (" ".join(tray_links) if tray_links else "選定なし")
        + "</aside>"
    )

    body = (
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
    return document(
        f"実験: {experiment_name}",
        body,
        crumbs=[(experiment_name, f"/exp/{_url_segment(experiment_name)}")],
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
                tags += '<span class="tag">転機</span>'
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
        f"ほか {omitted} 件の転機は「主人公の全決定」で"
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
        f'<dt>q</dt><dd>{model["quality"]:.4f}</dd>'
        f'<dt>到達</dt><dd>'
        f'{_reached_seeds(model["reach_rate"], len(model.get("seeds") or []))}'
        "</dd>"
        f'<dt>世代</dt><dd>g{model["generation"]}</dd>'
        f'<dt>seed</dt><dd>{_escape(model["seed"])}</dd>'
        f'<dt>親</dt><dd>{_escape(parents)}</dd>'
        f'<dt>layers</dt><dd>{_escape(model["layers_path"])}</dd>'
        "</dl></section>"
        f'{_genome_panel(model["genome"], model["categories"])}'
        '<section class="card chart-card"><h2>7層の推移</h2>'
        '<p class="muted">x = 日。▼ downed、▲ revived、● ending。</p>'
        f'{layers_svg(model["layer_points"], model["markers"])}</section>'
        '<section class="card story-section">'
        '<div class="section-heading"><h2>物語</h2>'
        f'<nav class="view-modes">{" ".join(mode_links)}</nav></div>'
        f'{_timeline(model)}</section>'
        '<details class="raw">'
        '<summary>模範ランのターン列（生ログ）</summary>'
        f'{_raw_table(model["turn_rows"])}</details>'
        '<div class="outputs-grid">'
        f'{_output_panel("あらすじ", synopsis_entry, synopsis_text, model["synopsis_backend"])}'
        f'{_output_panel("本文", model["story"], model["story_text"])}'
        "</div>"
    )
    return document(
        f"{experiment_name} / {cell_key}",
        body,
        crumbs=[
            (experiment_name, experiment_url),
            (cell_key, cell_base),
        ],
    )
