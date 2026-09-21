"""Server-rendered lineage river for one GA experiment (WB-GAVIZ-002)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from gapengine import lineage
from gapengine.evolve import _best_reached
from viewer import data, pages
from viewer.ga_replay import _parent_display


COLUMN_WIDTH = 56
LEFT_MARGIN = 96
POINT_PITCH = 8
POINT_RADIUS = 2.5
SURVIVOR_RADIUS = 3.5
BAR_LIMIT = 60
EDGE_LIMIT = 6000


def _cell_key(cell: Any) -> str | None:
    if not isinstance(cell, Sequence) or isinstance(cell, (str, bytes)) or len(cell) != 2:
        return None
    return f"{cell[0]}|{cell[1]}"


def build_index(generations: Sequence[Sequence[Mapping[str, Any]]]) -> dict:
    """Build a stable (generation, individual index) lookup."""

    index = {}
    for generation, results in enumerate(generations):
        for result in results:
            individual_index = int(result["index"])
            best = _best_reached(result)
            index[(generation, individual_index)] = {
                "generation": generation,
                "index": individual_index,
                "cell_key": _cell_key(result.get("cell")),
                "quality": float(best["quality"]) if best is not None else None,
                "parent_refs": [str(value) for value in result.get("parents", [])],
                "status": result.get("classification_status"),
            }
    return index


def _archive_running_best(index: Mapping, generations_count: int) -> list[dict[str, tuple[int, int]]]:
    current: dict[str, tuple[int, int]] = {}
    snapshots: list[dict[str, tuple[int, int]]] = []
    for generation in range(generations_count):
        nodes = sorted(
            (
                (key, node)
                for key, node in index.items()
                if key[0] == generation
            ),
            key=lambda item: item[0][1],
        )
        for key, node in nodes:
            cell_key = node.get("cell_key")
            quality = node.get("quality")
            if cell_key is None or quality is None:
                continue
            existing_key = current.get(cell_key)
            candidate_order = (float(quality), -key[0], -key[1])
            existing_order = (
                (
                    float(index[existing_key]["quality"]),
                    -existing_key[0],
                    -existing_key[1],
                )
                if existing_key is not None
                else None
            )
            if existing_order is None or candidate_order > existing_order:
                current[cell_key] = key
        snapshots.append(dict(current))
    return snapshots


def _resolve_parent_ref(
    ref: str,
    index: Mapping,
    archive_best: Sequence[Mapping[str, tuple[int, int]]],
) -> tuple[int, int] | None:
    individual_match = lineage._IND_REF.match(ref)
    if individual_match:
        key = (int(individual_match.group(1)), int(individual_match.group(2)))
        return key if key in index else None

    archive_match = lineage._ARCHIVE_REF.match(ref)
    if not archive_match:
        return None
    generation_limit = int(archive_match.group(1))
    category, separator, volatility_bin = archive_match.group(2).rpartition("-")
    if not separator or not category or not 0 <= generation_limit < len(archive_best):
        return None
    return archive_best[generation_limit].get(f"{category}|{volatility_bin}")


def resolve_parents(index: Mapping, generations_count: int) -> tuple[list[dict], int]:
    """Resolve all parent refs in one pass without rereading results files."""

    archive_best = _archive_running_best(index, generations_count)
    edges: list[dict] = []
    unresolved_count = 0
    for child_key, child in sorted(index.items()):
        resolved_for_child: dict[tuple[int, int], list[str]] = defaultdict(list)
        for ref in child.get("parent_refs", []):
            parent_key = _resolve_parent_ref(ref, index, archive_best)
            if parent_key is None:
                unresolved_count += 1
                continue
            resolved_for_child[parent_key].append(ref)
        for parent_key, refs in sorted(resolved_for_child.items()):
            edges.append({
                "parent": parent_key,
                "child": child_key,
                "twice": len(refs) > 1,
                "refs": tuple(refs),
            })
    return edges, unresolved_count


def survivors(index: Mapping, edges: Sequence[Mapping], elite_nodes: Mapping[str, tuple[int, int]]) -> dict:
    """Return every node on each final elite's transitive ancestry."""

    parents_by_child: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    for edge in edges:
        parents_by_child[edge["child"]].append(edge["parent"])

    survivor_map: dict[tuple[int, int], set[str]] = defaultdict(set)
    for cell_key, elite_key in sorted(elite_nodes.items()):
        if elite_key not in index:
            continue
        pending = [elite_key]
        visited: set[tuple[int, int]] = set()
        while pending:
            node_key = pending.pop()
            if node_key in visited:
                continue
            visited.add(node_key)
            survivor_map[node_key].add(cell_key)
            pending.extend(parents_by_child.get(node_key, ()))
    return dict(survivor_map)


def layout(index: Mapping, edges: Sequence[Mapping], survivor_map: Mapping, band_order: Sequence[str]) -> dict:
    """Calculate deterministic SVG positions for nodes, bands, and bars."""

    parent_nodes = {edge["parent"] for edge in edges}
    observed_cells = {node["cell_key"] for node in index.values() if node.get("cell_key")}
    ordered_cells = list(dict.fromkeys(str(value) for value in band_order))
    ordered_cells.extend(sorted(observed_cells - set(ordered_cells)))

    generations_count = max((key[0] for key in index), default=-1) + 1
    visible_by_band: dict[str, dict[int, list[tuple[int, int]]]] = defaultdict(lambda: defaultdict(list))
    omitted_by_generation = {generation: 0 for generation in range(generations_count)}
    for node_key, node in sorted(index.items()):
        if node.get("cell_key"):
            visible_by_band[node["cell_key"]][node_key[0]].append(node_key)
        elif node_key in parent_nodes:
            visible_by_band["__offmap_parent__"][node_key[0]].append(node_key)
        else:
            omitted_by_generation[node_key[0]] += 1

    positions = {}
    bands = []
    current_y = 36.0
    for band_key in ordered_cells + ["__offmap_parent__"]:
        generation_nodes = visible_by_band.get(band_key, {})
        max_stack = max((len(values) for values in generation_nodes.values()), default=0)
        band_height = float(max(1, max_stack) * POINT_PITCH)
        band_center = current_y + band_height / 2.0
        bands.append({
            "key": band_key,
            "label": (
                "地図に載らなかった親"
                if band_key == "__offmap_parent__"
                else band_key.replace("|", " × ")
            ),
            "top": current_y,
            "height": band_height,
            "center": band_center,
        })
        for generation, node_keys in sorted(generation_nodes.items()):
            for stack_index, node_key in enumerate(sorted(node_keys, key=lambda key: key[1])):
                positions[node_key] = {
                    "x": float(LEFT_MARGIN + generation * COLUMN_WIDTH),
                    "y": current_y + POINT_PITCH / 2.0 + stack_index * POINT_PITCH,
                    "band": band_key,
                }
        current_y += band_height + 8.0

    bar_top = current_y + 8.0
    bars = []
    for generation in range(generations_count):
        count = omitted_by_generation[generation]
        height = min(count, BAR_LIMIT)
        bars.append({
            "generation": generation,
            "count": count,
            "height": height,
            "x": float(LEFT_MARGIN + generation * COLUMN_WIDTH),
            "y": float(bar_top + BAR_LIMIT - height),
        })

    return {
        "width": LEFT_MARGIN + max(1, generations_count - 1) * COLUMN_WIDTH + 220,
        "height": bar_top + BAR_LIMIT + 30,
        "positions": positions,
        "bands": bands,
        "bars": bars,
        "bar_top": bar_top,
        "generations": generations_count,
        "point_radius": POINT_RADIUS,
        "survivor_radius": SURVIVOR_RADIUS,
    }


def _generation_results(repository: data.RunRepository, experiment, generation: int) -> list[Mapping[str, Any]]:
    path = repository.safe_path(experiment, f"g{generation}/results.json")
    if not path.is_file():
        return []
    return lineage._generation_results(repository, experiment, generation)


def river_model(
    repository: data.RunRepository,
    experiment_name: str,
    selected_cell: str | None = None,
    *,
    max_generation: int | None = None,
    snapshot_archive: Mapping | None = None,
    snapshot_axes=None,
    snapshot_root=None,
) -> dict:
    # Native callers already resolved and verified their publication root.
    # They need no mutable top-level archive.json to observe that snapshot.
    experiment = snapshot_root if snapshot_root is not None else repository.experiment(experiment_name)
    generation_numbers = sorted(
        int(path.name[1:])
        for path in experiment.iterdir()
        if path.is_dir() and path.name.startswith("g") and path.name[1:].isdigit()
    )
    generations_count = (generation_numbers[-1] + 1) if generation_numbers else 0
    if max_generation is not None:
        generations_count = min(generations_count, max_generation + 1)
    generations = [
        _generation_results(repository, experiment, generation)
        for generation in range(generations_count)
    ]
    if not any(generations):
        raise data.MissingResource(f"generation results not found: {experiment_name}")

    index = build_index(generations)
    edges, unresolved_count = resolve_parents(index, generations_count)
    archive = snapshot_archive if snapshot_archive is not None else repository.archive(experiment)
    cells = data._as_mapping(archive.get("cells"))
    elite_nodes = {}
    elite_quality = {}
    for cell_key, elite in sorted(cells.items()):
        if not isinstance(elite, Mapping):
            continue
        exemplar = data._as_mapping(elite.get("exemplar"))
        match = lineage._EXEMPLAR_PATH.match(str(exemplar.get("layers_path", "")))
        if not match:
            continue
        node_key = (int(match.group(1)), int(match.group(2)))
        if node_key in index:
            elite_nodes[str(cell_key)] = node_key
            elite_quality[str(cell_key)] = float(elite.get("quality", 0.0))

    if selected_cell is not None and selected_cell not in elite_nodes:
        raise data.MissingResource(f"cell not found in archive: {selected_cell}")

    survivor_map = survivors(index, edges, elite_nodes)
    try:
        meta = ({"categories": snapshot_axes[0], "bins": snapshot_axes[1]} if snapshot_axes is not None
                else data.experiment_meta(repository, experiment))
        band_order = [
            f"{category}|{volatility_bin}"
            for category in meta["categories"]
            for volatility_bin in meta["bins"]
        ]
    except (OSError, ValueError, KeyError, TypeError):
        band_order = sorted(
            node["cell_key"] for node in index.values() if node.get("cell_key")
        )
    diagram = layout(index, edges, survivor_map, band_order)
    parentless = sum(not node.get("parent_refs") for node in index.values())
    offmap = sum(node.get("cell_key") is None for node in index.values())
    return {
        "experiment": experiment_name,
        "selected_cell": selected_cell,
        "index": index,
        "edges": edges,
        "survivor_map": survivor_map,
        "elite_nodes": elite_nodes,
        "elite_quality": elite_quality,
        "layout": diagram,
        "counts": {
            "total": len(index),
            "generations": generations_count,
            "population": max((len(results) for results in generations), default=0),
            "survivors": len(survivor_map),
            "parentless": parentless,
            "offmap": offmap,
            "unresolved": unresolved_count,
            "elites": len(elite_nodes),
        },
    }


def _edge_classes(edge: Mapping, survivor_map: Mapping, selected_cell: str | None) -> tuple[str, set[str]]:
    shared_cells = set(survivor_map.get(edge["parent"], ())) & set(
        survivor_map.get(edge["child"], ())
    )
    classes = ["river-edge"]
    if shared_cells:
        classes.append("is-alive")
    if selected_cell is not None and selected_cell in shared_cells:
        classes.append("is-selected")
    return " ".join(classes), shared_cells


def _node_title(node: Mapping) -> str:
    cell_text = (node.get("cell_key") or "地図外").replace("|", " × ")
    quality = "q なし" if node.get("quality") is None else f"q {float(node['quality']):.3f}"
    parent_text = " × ".join(_parent_display(ref) for ref in node.get("parent_refs", ()))
    suffix = f"・親: {parent_text}" if parent_text else "・親なし"
    return (
        f"第 {int(node['generation']) + 1} 世代の個体 #{int(node['index'])}・"
        f"{cell_text}・{quality}{suffix}"
    )


def river_parts(model: Mapping, base_url: str) -> tuple[str, str, str]:
    """(前置き(summary段落+controls), 図('<div class="grid-wrap river-wrap">'+svg+'</div>'), 凡例)."""
    index = model["index"]
    edges = model["edges"]
    survivor_map = model["survivor_map"]
    elite_nodes = model["elite_nodes"]
    elite_by_node = {node_key: cell_key for cell_key, node_key in elite_nodes.items()}
    diagram = model["layout"]
    positions = diagram["positions"]
    selected_cell = model.get("selected_cell")
    counts = model["counts"]

    percent = 0.0 if not counts["total"] else counts["survivors"] * 100.0 / counts["total"]
    summary = (
        f"{counts['generations']} 世代 × {counts['population']} 体 = {counts['total']} 体。"
        f"地図に残った {counts['elites']} つの物語の血筋は "
        f"{counts['survivors']} 体（{percent:.0f}%）。"
        f"親なしの新顔 {counts['parentless']} 体。"
        f"地図に載らなかった個体 {counts['offmap']} 体。"
    )
    if counts["unresolved"]:
        summary += f"親を特定できなかった参照 {counts['unresolved']} 本。"

    edge_rows = []
    for edge in edges:
        edge_class, shared_cells = _edge_classes(edge, survivor_map, selected_cell)
        edge_rows.append((bool(shared_cells), edge, edge_class))
    dead_edges = [row for row in edge_rows if not row[0]]
    alive_edges = [row for row in edge_rows if row[0]]
    omitted_dead = 0
    # A ponytail-sized guard: dense 20x100 runs remain readable and responsive.
    if len(edges) > EDGE_LIMIT:
        omitted_dead = len(dead_edges)
        dead_edges = []
        summary += f"途絶えた親子の線は多すぎるため省略（{omitted_dead} 本）。"

    edge_markup = []
    for _, edge, edge_class in dead_edges + alive_edges:
        parent_position = positions.get(edge["parent"])
        child_position = positions.get(edge["child"])
        if parent_position is None or child_position is None:
            continue
        title = "<title>同じ親が 2 回選ばれた</title>" if edge.get("twice") else ""
        edge_markup.append(
            f'<line class="{edge_class}" x1="{parent_position["x"]:.1f}" '
            f'y1="{parent_position["y"]:.1f}" x2="{child_position["x"]:.1f}" '
            f'y2="{child_position["y"]:.1f}">{title}</line>'
        )

    generation_markup = []
    for generation in range(diagram["generations"]):
        x_value = LEFT_MARGIN + generation * COLUMN_WIDTH
        generation_markup.append(
            f'<text class="river-generation" x="{x_value}" y="18" text-anchor="middle">'
            f'<title>g{generation}</title>第 {generation + 1} 世代</text>'
        )

    band_markup = []
    for band in diagram["bands"]:
        band_markup.append(
            f'<line class="river-band-line" x1="{LEFT_MARGIN - 8}" y1="{band["top"]:.1f}" '
            f'x2="{diagram["width"] - 18}" y2="{band["top"]:.1f}"/>'
            f'<text class="river-band-label" x="{LEFT_MARGIN - 12}" y="{band["center"] + 3:.1f}" '
            f'text-anchor="end">{pages._escape(band["label"])}</text>'
        )

    bar_markup = [
        f'<text class="river-band-label" x="{LEFT_MARGIN - 12}" '
        f'y="{diagram["bar_top"] + BAR_LIMIT / 2 + 3:.1f}" text-anchor="end">載らず・子なし</text>'
    ]
    for bar in diagram["bars"]:
        if bar["height"]:
            bar_markup.append(
                f'<rect class="river-bar" x="{bar["x"] - 8:.1f}" y="{bar["y"]:.1f}" '
                f'width="16" height="{bar["height"]}"/>'
            )
        bar_markup.append(
            f'<text class="river-bar-count" x="{bar["x"]:.1f}" '
            f'y="{diagram["bar_top"] + BAR_LIMIT + 14:.1f}" text-anchor="middle">'
            f'{bar["count"]}</text>'
        )

    node_markup = []
    label_markup = []
    for node_key, position in sorted(positions.items()):
        node = index[node_key]
        lineage_cells = set(survivor_map.get(node_key, ()))
        classes = ["river-node"]
        if lineage_cells:
            classes.append("is-alive")
        if selected_cell is not None and selected_cell in lineage_cells:
            classes.append("is-selected")
        if node_key in elite_by_node:
            classes.append("is-elite")
        quality = node.get("quality")
        opacity = 0.25 if quality is None else 0.25 + 0.75 * max(0.0, min(1.0, float(quality)))
        radius = SURVIVOR_RADIUS if lineage_cells else POINT_RADIUS
        node_markup.append(
            f'<circle class="{" ".join(classes)}" cx="{position["x"]:.1f}" '
            f'cy="{position["y"]:.1f}" r="{radius}" fill-opacity="{opacity:.3f}">'
            f'<title>{pages._escape(_node_title(node))}</title></circle>'
        )
        elite_cell = elite_by_node.get(node_key)
        if elite_cell is None:
            continue
        quality_text = model["elite_quality"].get(elite_cell, 0.0)
        target = base_url if selected_cell == elite_cell else f"{base_url}?cell={pages._url_segment(elite_cell)}"
        label_markup.append(
            f'<a class="river-elite-link" href="{pages._escape(target)}">'
            f'<text x="{position["x"] + 8:.1f}" y="{position["y"] + 3:.1f}">'
            f'{pages._escape(elite_cell.replace("|", " × "))} q {float(quality_text):.3f}'
            f'</text></a>'
        )

    svg_classes = "river-svg has-selection" if selected_cell else "river-svg"
    svg = (
        f'<svg class="{svg_classes}" viewBox="0 0 {diagram["width"]} {diagram["height"]}" '
        f'width="{diagram["width"]}" height="{diagram["height"]}" '
        'role="img" aria-labelledby="river-title river-desc">'
        '<title id="river-title">世代をまたぐ個体の系譜</title>'
        '<desc id="river-desc">太い線は最終的に地図へ残った物語の祖先です。</desc>'
        + "".join(generation_markup)
        + "".join(band_markup)
        + "".join(bar_markup)
        + "".join(edge_markup)
        + "".join(node_markup)
        + "".join(label_markup)
        + "</svg>"
    )

    legend = (
        '<ul class="river-legend">'
        '<li><span class="river-legend-line is-alive"></span>太い実線＝残った血筋</li>'
        '<li><span class="river-legend-line"></span>細い破線＝途絶えた親子</li>'
        '<li><span class="river-legend-dot"></span>点の濃さ＝品質</li>'
        '<li><span class="river-legend-bar"></span>下段のバー＝載らなかった数</li>'
        '</ul>'
    )
    if selected_cell:
        lineage_href = (
            f"{base_url.rsplit('/river', 1)[0]}/cell/"
            f"{pages._url_segment(selected_cell)}/lineage"
        )
        controls = (
            '<p class="river-selection">'
            f'{pages._escape(selected_cell.replace("|", " × "))} の血筋を強調中 ・ '
            f'<a href="{pages._escape(lineage_href)}">転機を見る</a> ・ '
            f'<a href="{pages._escape(base_url)}">強調を解除</a></p>'
        )
    else:
        elite_links = [
            f'<a href="{pages._escape(base_url)}?cell={pages._url_segment(cell_key)}">'
            f'{pages._escape(cell_key.replace("|", " × "))}</a>'
            for cell_key in sorted(elite_nodes)
        ]
        controls = '<p class="river-selection">血筋を強調: ' + " ・ ".join(elite_links) + "</p>"

    preface = f'<p class="river-summary">{pages._escape(summary)}</p>' + controls
    diagram_html = '<div class="grid-wrap river-wrap">' + svg + '</div>'
    return preface, diagram_html, legend


def render_river(model: Mapping, base_url: str) -> str:
    return "".join(river_parts(model, base_url))


def river_page(
    repository: data.RunRepository,
    experiment_name: str,
    *,
    selected_cell: str | None = None,
    job_store: Any = None,
) -> str:
    from viewer import review_pages
    return review_pages.river(repository, experiment_name, selected_cell=selected_cell, job_store=job_store)
