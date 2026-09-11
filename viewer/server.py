"""Dependency-free HTTP viewer for WorldBloom experiment runs."""

from __future__ import annotations

import argparse
import html
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit


DEFAULT_CATEGORIES = ("I", "II", "III", "IV", "V", "VI")
DEFAULT_VOLATILITY_BINS = ("low", "mid", "high")
MAX_POST_BYTES = 64 * 1024

LAYER_SERIES = (
    ("能力層", "#55d6be"),
    ("認識層", "#5aa9e6"),
    ("資源層", "#f7b267"),
    ("フェーズ層", "#c77dff"),
    ("身分層", "#ff6b6b"),
    ("対象層", "#ffe66d"),
    ("遅延効果層", "#8ac926"),
)


class ForbiddenPath(ValueError):
    """Raised when a request attempts to leave an allowed directory."""


class MissingResource(LookupError):
    """Raised when an experiment or cell does not exist."""


class BadRequest(ValueError):
    """Raised when a request body is invalid."""


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _json_text(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _display(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _safe_cell_stem(cell_key: str) -> str:
    return cell_key.replace("|", "-")


class RunRepository:
    """Read experiment artifacts while enforcing the configured run root."""

    def __init__(self, runs_root: Path) -> None:
        self.runs_root = runs_root.expanduser().resolve()
        if not self.runs_root.is_dir():
            raise ValueError(
                f"--runs must be an existing directory: {self.runs_root}"
            )

    @staticmethod
    def validate_segment(value: str) -> str:
        if (
            not value
            or value in {".", ".."}
            or "/" in value
            or "\\" in value
            or "\x00" in value
        ):
            raise ForbiddenPath("invalid path segment")
        return value

    def safe_path(
        self,
        base: Path,
        relative: str | Path,
        *,
        require_experiment_scope: bool = True,
    ) -> Path:
        relative_path = Path(relative)
        if relative_path.is_absolute():
            raise ForbiddenPath("absolute artifact path")

        resolved_base = base.resolve()
        candidate = (resolved_base / relative_path).resolve()

        if not _inside(candidate, self.runs_root):
            raise ForbiddenPath("artifact leaves --runs")
        if require_experiment_scope and not _inside(candidate, resolved_base):
            raise ForbiddenPath("artifact leaves experiment directory")
        return candidate

    def experiment(self, name: str) -> Path:
        safe_name = self.validate_segment(name)
        candidate = (self.runs_root / safe_name).resolve()
        if not _inside(candidate, self.runs_root):
            raise ForbiddenPath("experiment leaves --runs")
        if not candidate.is_dir():
            raise MissingResource(f"experiment not found: {safe_name}")
        if not self.safe_path(candidate, "archive.json").is_file():
            raise MissingResource(f"archive not found: {safe_name}")
        return candidate

    def experiments(self) -> list[tuple[str, Path]]:
        experiments: list[tuple[str, Path]] = []
        for candidate in sorted(
            self.runs_root.iterdir(),
            key=lambda path: path.name,
        ):
            try:
                resolved = candidate.resolve()
                if (
                    not candidate.is_dir()
                    or not _inside(resolved, self.runs_root)
                    or not self.safe_path(
                        resolved,
                        "archive.json",
                    ).is_file()
                ):
                    continue
            except (OSError, ForbiddenPath):
                continue
            experiments.append((candidate.name, resolved))
        return experiments

    def archive(self, experiment: Path) -> dict[str, Any]:
        raw = _read_json(self.safe_path(experiment, "archive.json"))
        if not isinstance(raw, dict):
            raise ValueError("archive root must be a JSON object")
        cells = raw.get("cells")
        if not isinstance(cells, dict):
            raise ValueError("archive.cells must be a JSON object")
        return raw

    def selection(self, experiment: Path) -> set[str]:
        path = self.safe_path(experiment, "selection.json")
        if not path.is_file():
            return set()

        raw = _read_json(path)
        if not isinstance(raw, Mapping):
            raise ValueError("selection root must be a JSON object")
        selected = raw.get("selected")
        if not isinstance(selected, list) or not all(
            isinstance(value, str) for value in selected
        ):
            raise ValueError(
                "selection.selected must be a list of strings"
            )
        return set(selected)

    def write_selection(
        self,
        experiment: Path,
        selected: set[str],
    ) -> Path:
        destination = self.safe_path(experiment, "selection.json")
        payload = (
            json.dumps(
                {"selected": sorted(selected)},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix=".selection-",
                suffix=".tmp",
                dir=experiment,
                delete=False,
            ) as handle:
                handle.write(payload)
                temporary_path = Path(handle.name)

            resolved_temporary = temporary_path.resolve()
            if not _inside(resolved_temporary, experiment.resolve()):
                raise ForbiddenPath("temporary file leaves experiment")
            os.replace(temporary_path, destination)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

        return destination


def _document(title: str, body: str) -> str:
    escaped_title = html.escape(title)
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escaped_title} | WorldBloom</title>
<style>
:root {{
  color-scheme: dark;
  --bg: #09111f;
  --panel: #111d30;
  --panel-2: #17263d;
  --line: #2b3d58;
  --text: #ecf3ff;
  --muted: #9fb0c9;
  --accent: #55d6be;
  --warning: #f7b267;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background:
    radial-gradient(circle at top right, #17345a 0, transparent 38rem),
    var(--bg);
  color: var(--text);
  font: 15px/1.65 system-ui, -apple-system, "Segoe UI", sans-serif;
}}
header, main {{
  width: min(1180px, calc(100% - 32px));
  margin-inline: auto;
}}
header {{
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 24px;
  padding: 28px 0 16px;
}}
header a, a {{ color: var(--accent); }}
h1 {{ margin: 0; font-size: clamp(1.5rem, 4vw, 2.4rem); }}
h2 {{ margin-top: 2rem; }}
.card {{
  background: color-mix(in srgb, var(--panel) 94%, transparent);
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 18px;
  margin: 14px 0;
  box-shadow: 0 10px 30px rgb(0 0 0 / 18%);
}}
.muted {{ color: var(--muted); }}
.error {{ color: #ff9b9b; }}
.badge {{
  display: inline-block;
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 2px 9px;
  color: var(--muted);
}}
.grid-wrap {{ overflow-x: auto; }}
table {{
  width: 100%;
  border-collapse: collapse;
  background: var(--panel);
}}
th, td {{
  border: 1px solid var(--line);
  padding: 10px;
  vertical-align: top;
  text-align: left;
}}
th {{ background: var(--panel-2); }}
.archive-grid td {{ min-width: 170px; }}
.archive-grid td.empty {{ color: #657791; }}
.metric {{
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 2px 10px;
}}
.metric dt {{ color: var(--muted); }}
.metric dd {{ margin: 0; }}
pre {{
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  background: #08101d;
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 16px;
}}
svg {{
  display: block;
  width: 100%;
  height: auto;
  background: #08101d;
  border: 1px solid var(--line);
  border-radius: 10px;
}}
.selection {{
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 1.05rem;
}}
.selection input {{ width: 20px; height: 20px; }}
#selection-status {{ color: var(--muted); }}
@media (max-width: 680px) {{
  header {{ align-items: flex-start; flex-direction: column; gap: 4px; }}
  th, td {{ padding: 8px; }}
}}
</style>
</head>
<body>
<header>
  <h1>{escaped_title}</h1>
  <nav><a href="/">実験一覧</a></nav>
</header>
<main>{body}</main>
</body>
</html>
"""


def _experiment_summary(
    repository: RunRepository,
    experiment: Path,
) -> tuple[int, str]:
    try:
        archive = repository.archive(experiment)
        cells = archive["cells"]
        thresholds = archive.get("volatility_thresholds")
        threshold_text = (
            _json_text(thresholds)
            if thresholds is not None
            else "未設定"
        )
        return len(cells), threshold_text
    except (OSError, ValueError, json.JSONDecodeError):
        return 0, "読み取りエラー"


def _index_page(repository: RunRepository) -> str:
    experiments = repository.experiments()
    if not experiments:
        return _document(
            "WorldBloom 実験一覧",
            (
                '<section class="card">'
                "<p>表示できる実験がありません。</p>"
                '<p class="muted">各実験ディレクトリに '
                "<code>archive.json</code> が必要です。</p>"
                "</section>"
            ),
        )

    cards: list[str] = []
    for name, experiment in experiments:
        count, thresholds = _experiment_summary(
            repository,
            experiment,
        )
        href = f"/exp/{quote(name, safe='')}"
        cards.append(
            '<article class="card">'
            f'<h2><a href="{href}">{html.escape(name)}</a></h2>'
            f"<p>占有マス: <strong>{count}</strong></p>"
            f'<p class="muted">volatility 閾値: '
            f"{html.escape(thresholds)}</p>"
            "</article>"
        )

    return _document("WorldBloom 実験一覧", "".join(cards))


def _ordered_categories(cells: Mapping[str, Any]) -> list[str]:
    present = {
        key.split("|", 1)[0]
        for key in cells
        if isinstance(key, str) and "|" in key
    }
    return [
        *DEFAULT_CATEGORIES,
        *sorted(present.difference(DEFAULT_CATEGORIES)),
    ]


def _ordered_bins(cells: Mapping[str, Any]) -> list[str]:
    present = {
        key.split("|", 1)[1]
        for key in cells
        if isinstance(key, str) and "|" in key
    }
    return [
        *DEFAULT_VOLATILITY_BINS,
        *sorted(present.difference(DEFAULT_VOLATILITY_BINS)),
    ]


def _cell_metrics(
    experiment_name: str,
    cell_key: str,
    elite: Mapping[str, Any],
) -> str:
    quality = _number(elite.get("quality"))
    reach_rate = _number(elite.get("reach_rate"))
    generation = int(_number(elite.get("generation")))
    href = (
        f"/exp/{quote(experiment_name, safe='')}"
        f"/cell/{quote(cell_key, safe='')}"
    )
    return (
        f'<a href="{href}"><strong>{html.escape(cell_key)}</strong></a>'
        '<dl class="metric">'
        "<dt>q</dt>"
        f"<dd>{quality:.4f}</dd>"
        "<dt>reach</dt>"
        f"<dd>{reach_rate:.1%}</dd>"
        "<dt>generation</dt>"
        f"<dd>{generation}</dd>"
        "</dl>"
    )


def _experiment_page(
    repository: RunRepository,
    experiment_name: str,
) -> str:
    experiment = repository.experiment(experiment_name)
    archive = repository.archive(experiment)
    cells = archive["cells"]
    categories = _ordered_categories(cells)
    bins = _ordered_bins(cells)

    headings = "".join(
        f"<th>{html.escape(bin_name)}</th>" for bin_name in bins
    )
    rows: list[str] = []
    for category in categories:
        columns = []
        for bin_name in bins:
            cell_key = f"{category}|{bin_name}"
            elite = cells.get(cell_key)
            if isinstance(elite, Mapping):
                columns.append(
                    "<td>"
                    + _cell_metrics(
                        experiment_name,
                        cell_key,
                        elite,
                    )
                    + "</td>"
                )
            else:
                columns.append('<td class="empty">空</td>')
        rows.append(
            f"<tr><th>{html.escape(category)}</th>"
            + "".join(columns)
            + "</tr>"
        )

    thresholds = archive.get("volatility_thresholds")
    threshold_text = (
        _json_text(thresholds)
        if thresholds is not None
        else "未設定"
    )
    body = (
        '<section class="card">'
        f"<p>占有マス: <strong>{len(cells)}</strong></p>"
        f'<p class="muted">volatility 閾値: '
        f"{html.escape(threshold_text)}</p>"
        "</section>"
        '<section class="card grid-wrap">'
        '<table class="archive-grid">'
        f"<thead><tr><th>カテゴリ</th>{headings}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</section>"
    )
    return _document(f"実験: {experiment_name}", body)


def _synopsis_entry(
    repository: RunRepository,
    experiment: Path,
    cell_key: str,
) -> Mapping[str, Any] | None:
    path = repository.safe_path(experiment, "synopses.json")
    if not path.is_file():
        return None
    raw = _read_json(path)
    if not isinstance(raw, Mapping):
        return None
    entries = raw.get("entries")
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if (
            isinstance(entry, Mapping)
            and entry.get("cell") == cell_key
        ):
            return entry
    return None


def _story_entry(
    repository: RunRepository,
    experiment: Path,
    cell_key: str,
) -> tuple[Mapping[str, Any] | None, str | None]:
    stories_dir = repository.safe_path(experiment, "stories")
    index_path = repository.safe_path(stories_dir, "index.json")
    if not index_path.is_file():
        return None, None

    raw = _read_json(index_path)
    if not isinstance(raw, Mapping):
        return None, None
    entries = raw.get("entries")
    if not isinstance(entries, list):
        return None, None

    for entry in entries:
        if (
            not isinstance(entry, Mapping)
            or entry.get("cell") != cell_key
        ):
            continue
        story_path = entry.get("story_path")
        if not isinstance(story_path, str):
            return entry, None
        resolved = repository.safe_path(stories_dir, story_path)
        if not resolved.is_file():
            return entry, None
        return entry, resolved.read_text(encoding="utf-8")
    return None, None


def _vector_value(vector: Sequence[Any], index: int) -> float:
    if index >= len(vector):
        return 0.0
    return max(0.0, min(1.0, _number(vector[index])))


def _layer_values(
    snapshots: Sequence[Mapping[str, Any]],
) -> list[tuple[int, list[float]]]:
    pending_counts: list[int] = []
    for snapshot in snapshots:
        layers = snapshot.get("layers")
        pending = (
            layers.get("pending")
            if isinstance(layers, Mapping)
            else None
        )
        pending_counts.append(len(pending) if isinstance(pending, list) else 0)

    pending_max = max(pending_counts, default=0)
    result: list[tuple[int, list[float]]] = []

    for snapshot, pending_count in zip(
        snapshots,
        pending_counts,
        strict=True,
    ):
        vector = snapshot.get("vector")
        if not isinstance(vector, list):
            continue

        values = [
            (
                _vector_value(vector, 0)
                + _vector_value(vector, 1)
            )
            / 2.0,
            _vector_value(vector, 7),
            (
                _vector_value(vector, 2)
                + _vector_value(vector, 3)
                + _vector_value(vector, 4)
                + _vector_value(vector, 5)
            )
            / 4.0,
            _vector_value(vector, 6),
            _vector_value(vector, 8),
            _vector_value(vector, 9),
            (
                pending_count / pending_max
                if pending_max > 0
                else 0.0
            ),
        ]
        result.append(
            (
                int(_number(snapshot.get("turn"))),
                values,
            )
        )
    return result


def _layers_svg(rows: Sequence[Mapping[str, Any]]) -> str:
    snapshots = [
        row
        for row in rows
        if row.get("kind") == "snapshot"
        and isinstance(row.get("vector"), list)
    ]
    points = _layer_values(snapshots)
    if not points:
        return '<p class="muted">snapshot vector がありません。</p>'

    width = 920
    height = 390
    left = 58
    right = 24
    top = 28
    bottom = 92
    plot_width = width - left - right
    plot_height = height - top - bottom
    count = len(points)

    def x_position(index: int) -> float:
        if count == 1:
            return left + plot_width / 2
        return left + plot_width * index / (count - 1)

    def y_position(value: float) -> float:
        clipped = max(0.0, min(1.0, value))
        return top + (1.0 - clipped) * plot_height

    grid: list[str] = []
    for step in range(5):
        value = step / 4
        y = y_position(value)
        grid.append(
            f'<line x1="{left}" y1="{y:.2f}" '
            f'x2="{width - right}" y2="{y:.2f}" '
            'stroke="#263852" stroke-width="1"/>'
        )
        grid.append(
            f'<text x="{left - 10}" y="{y + 4:.2f}" '
            'text-anchor="end" fill="#9fb0c9" font-size="11">'
            f"{value:.2f}</text>"
        )

    turn_labels: list[str] = []
    label_step = max(1, count // 10)
    for index, (turn, _) in enumerate(points):
        if index % label_step != 0 and index != count - 1:
            continue
        x = x_position(index)
        turn_labels.append(
            f'<text x="{x:.2f}" y="{top + plot_height + 22}" '
            'text-anchor="middle" fill="#9fb0c9" font-size="11">'
            f"{turn}</text>"
        )

    lines: list[str] = []
    legends: list[str] = []
    legend_width = plot_width / len(LAYER_SERIES)
    for series_index, (label, color) in enumerate(LAYER_SERIES):
        coordinates = " ".join(
            f"{x_position(index):.2f},"
            f"{y_position(values[series_index]):.2f}"
            for index, (_, values) in enumerate(points)
        )
        lines.append(
            f'<polyline points="{coordinates}" fill="none" '
            f'stroke="{color}" stroke-width="2.3" '
            'stroke-linejoin="round" stroke-linecap="round"/>'
        )
        legend_x = left + legend_width * series_index
        legend_y = height - 32
        legends.append(
            f'<line x1="{legend_x:.2f}" y1="{legend_y}" '
            f'x2="{legend_x + 18:.2f}" y2="{legend_y}" '
            f'stroke="{color}" stroke-width="3"/>'
            f'<text x="{legend_x + 23:.2f}" y="{legend_y + 4}" '
            'fill="#ecf3ff" font-size="11">'
            f"{html.escape(label)}</text>"
        )

    return (
        f'<svg viewBox="0 0 {width} {height}" '
        'role="img" aria-labelledby="layer-chart-title layer-chart-desc">'
        '<title id="layer-chart-title">7層の推移</title>'
        '<desc id="layer-chart-desc">'
        "snapshot の正規化 vector を7つの論理層へ集約した折れ線グラフ"
        "</desc>"
        + "".join(grid)
        + f'<line x1="{left}" y1="{top}" x2="{left}" '
        f'y2="{top + plot_height}" stroke="#71839d"/>'
        + f'<line x1="{left}" y1="{top + plot_height}" '
        f'x2="{width - right}" y2="{top + plot_height}" '
        'stroke="#71839d"/>'
        + "".join(lines)
        + "".join(turn_labels)
        + f'<text x="{width / 2:.2f}" '
        f'y="{top + plot_height + 46}" text-anchor="middle" '
        'fill="#9fb0c9" font-size="12">turn</text>'
        + "".join(legends)
        + "</svg>"
    )


def _turn_table(rows: Sequence[Mapping[str, Any]]) -> str:
    turn_rows = [
        row
        for row in rows
        if row.get("kind") not in {"header", "snapshot"}
        and "turn" in row
    ]
    if not turn_rows:
        return '<p class="muted">表示できるターン行がありません。</p>'

    rendered: list[str] = []
    for row in turn_rows:
        detail = {
            key: row[key]
            for key in ("args", "details", "delta", "classification")
            if key in row and row[key] not in (None, {}, [])
        }
        rendered.append(
            "<tr>"
            f"<td>{html.escape(_display(row.get('turn')))}</td>"
            f"<td>{html.escape(_display(row.get('day')))}</td>"
            f"<td>{html.escape(_display(row.get('slot')))}</td>"
            f"<td>{html.escape(_display(row.get('kind')))}</td>"
            f"<td>{html.escape(_display(row.get('subject')))}</td>"
            f"<td>{html.escape(_display(row.get('verb')))}</td>"
            f"<td>{html.escape(_display(row.get('result')))}</td>"
            f"<td>{html.escape(_display(row.get('effective')))}</td>"
            f"<td><code>{html.escape(_json_text(detail))}</code></td>"
            "</tr>"
        )

    return (
        '<div class="grid-wrap"><table>'
        "<thead><tr>"
        "<th>turn</th><th>day</th><th>slot</th><th>kind</th>"
        "<th>subject</th><th>verb</th><th>result</th>"
        "<th>effective</th><th>details</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rendered)}</tbody>"
        "</table></div>"
    )


def _synopsis_panel(entry: Mapping[str, Any] | None) -> str:
    if entry is None:
        return (
            '<section class="card"><h2>あらすじ</h2>'
            '<p class="muted">synopses.json に該当項目がありません。</p>'
            "</section>"
        )

    synopsis = entry.get("synopsis")
    status = html.escape(_display(entry.get("status")))
    error = entry.get("error")
    content = (
        f"<pre>{html.escape(synopsis)}</pre>"
        if isinstance(synopsis, str) and synopsis
        else '<p class="muted">本文はまだ生成されていません。</p>'
    )
    error_text = (
        f'<p class="error">{html.escape(str(error))}</p>'
        if error
        else ""
    )
    return (
        '<section class="card"><h2>あらすじ</h2>'
        f'<p><span class="badge">{status}</span></p>'
        f"{content}{error_text}</section>"
    )


def _story_panel(
    entry: Mapping[str, Any] | None,
    story: str | None,
) -> str:
    if entry is None:
        return (
            '<section class="card"><h2>本文</h2>'
            '<p class="muted">stories/index.json に該当項目がありません。</p>'
            "</section>"
        )

    status = html.escape(_display(entry.get("status")))
    error = entry.get("error")
    content = (
        f"<pre>{html.escape(story)}</pre>"
        if story is not None
        else '<p class="muted">本文はまだ生成されていません。</p>'
    )
    error_text = (
        f'<p class="error">{html.escape(str(error))}</p>'
        if error
        else ""
    )
    return (
        '<section class="card"><h2>本文</h2>'
        f'<p><span class="badge">{status}</span></p>'
        f"{content}{error_text}</section>"
    )


def _cell_page(
    repository: RunRepository,
    experiment_name: str,
    cell_key: str,
) -> str:
    repository.validate_segment(cell_key)
    experiment = repository.experiment(experiment_name)
    archive = repository.archive(experiment)
    cells = archive["cells"]
    elite = cells.get(cell_key)
    if not isinstance(elite, Mapping):
        raise MissingResource(f"cell not found: {cell_key}")

    exemplar = elite.get("exemplar")
    if not isinstance(exemplar, Mapping):
        raise ValueError("elite.exemplar must be a JSON object")
    layers_path = exemplar.get("layers_path")
    if not isinstance(layers_path, str):
        raise ValueError("elite exemplar has no layers_path")

    resolved_layers = repository.safe_path(experiment, layers_path)
    if not resolved_layers.is_file():
        raise MissingResource("exemplar layers.jsonl not found")
    rows = _read_jsonl(resolved_layers)

    selected = cell_key in repository.selection(experiment)
    synopsis = _synopsis_entry(repository, experiment, cell_key)
    story_entry, story = _story_entry(
        repository,
        experiment,
        cell_key,
    )

    quality = _number(elite.get("quality"))
    reach_rate = _number(elite.get("reach_rate"))
    generation = int(_number(elite.get("generation")))
    selection_endpoint = (
        f"/exp/{quote(experiment_name, safe='')}/selection"
    )
    checked = " checked" if selected else ""

    selection_script = f"""
<script>
(() => {{
  const checkbox = document.getElementById("selected");
  const status = document.getElementById("selection-status");
  checkbox.addEventListener("change", async () => {{
    checkbox.disabled = true;
    status.textContent = "保存中…";
    try {{
      const response = await fetch(
        {json.dumps(selection_endpoint)},
        {{
          method: "POST",
          headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify({{
            cell: {json.dumps(cell_key, ensure_ascii=False)},
            selected: checkbox.checked
          }})
        }}
      );
      if (!response.ok) {{
        throw new Error(`HTTP ${{response.status}}`);
      }}
      const result = await response.json();
      checkbox.checked = result.selected;
      status.textContent = "selection.json に保存しました";
    }} catch (error) {{
      checkbox.checked = !checkbox.checked;
      status.textContent = `保存に失敗しました: ${{error.message}}`;
    }} finally {{
      checkbox.disabled = false;
    }}
  }});
}})();
</script>
"""

    back_href = f"/exp/{quote(experiment_name, safe='')}"
    body = (
        '<section class="card">'
        f'<p><a href="{back_href}">← アーカイブ格子へ戻る</a></p>'
        '<dl class="metric">'
        f"<dt>cell</dt><dd>{html.escape(cell_key)}</dd>"
        f"<dt>q</dt><dd>{quality:.4f}</dd>"
        f"<dt>reach rate</dt><dd>{reach_rate:.1%}</dd>"
        f"<dt>generation</dt><dd>{generation}</dd>"
        f"<dt>seed</dt><dd>{html.escape(_display(exemplar.get('seed')))}</dd>"
        f"<dt>layers</dt><dd>{html.escape(layers_path)}</dd>"
        "</dl>"
        "</section>"
        '<section class="card">'
        '<label class="selection">'
        f'<input id="selected" type="checkbox"{checked}>'
        "<span>本文候補として選定する</span>"
        "</label>"
        '<p id="selection-status" aria-live="polite"></p>'
        "</section>"
        '<section class="card"><h2>7層の推移</h2>'
        '<p class="muted">'
        "ログの11成分 vector を設計上の7層へ集約。"
        "遅延効果層は snapshot の pending 件数をラン内最大値で正規化。"
        "vitality はターン列のイベント／delta で確認できます。"
        "</p>"
        f"{_layers_svg(rows)}</section>"
        '<section class="card"><h2>模範ランのターン列</h2>'
        f"{_turn_table(rows)}</section>"
        f"{_synopsis_panel(synopsis)}"
        f"{_story_panel(story_entry, story)}"
        f"{selection_script}"
    )
    return _document(
        f"{experiment_name} / {cell_key}",
        body,
    )


class ViewerServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class ViewerHandler(BaseHTTPRequestHandler):
    server_version = "WorldBloomViewer/1.0"

    @property
    def repository(self) -> RunRepository:
        repository = getattr(self.server, "repository", None)
        if not isinstance(repository, RunRepository):
            raise RuntimeError("viewer repository is not configured")
        return repository

    def _parts(self) -> list[str]:
        raw_path = urlsplit(self.path).path
        decoded = [unquote(part) for part in raw_path.split("/")]
        for part in decoded:
            if (
                part in {".", ".."}
                or "/" in part
                or "\\" in part
                or "\x00" in part
            ):
                raise ForbiddenPath("path traversal")
        return [part for part in decoded if part]

    def _send_bytes(
        self,
        status: HTTPStatus,
        content_type: str,
        payload: bytes,
    ) -> None:
        self.send_response(status.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "style-src 'unsafe-inline'; "
            "script-src 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "object-src 'none'; "
            "base-uri 'none'; "
            "frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(payload)

    def _send_html(self, document: str) -> None:
        self._send_bytes(
            HTTPStatus.OK,
            "text/html; charset=utf-8",
            document.encode("utf-8"),
        )

    def _send_json(
        self,
        status: HTTPStatus,
        value: Mapping[str, Any],
    ) -> None:
        payload = (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        self._send_bytes(
            status,
            "application/json; charset=utf-8",
            payload,
        )

    def _dispatch_get(self) -> None:
        parts = self._parts()
        if not parts:
            self._send_html(_index_page(self.repository))
            return
        if len(parts) == 2 and parts[0] == "exp":
            self._send_html(
                _experiment_page(self.repository, parts[1])
            )
            return
        if (
            len(parts) == 4
            and parts[0] == "exp"
            and parts[2] == "cell"
        ):
            self._send_html(
                _cell_page(
                    self.repository,
                    parts[1],
                    parts[3],
                )
            )
            return
        raise MissingResource("route not found")

    def do_GET(self) -> None:
        try:
            self._dispatch_get()
        except ForbiddenPath:
            self.send_error(HTTPStatus.FORBIDDEN.value)
        except MissingResource:
            self.send_error(HTTPStatus.NOT_FOUND.value)
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
        ):
            self.send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR.value,
                "Could not read experiment artifacts",
            )

    def _request_json(self) -> Mapping[str, Any]:
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or "")
        except ValueError as error:
            raise BadRequest("invalid Content-Length") from error
        if length < 1:
            raise BadRequest("empty request body")
        if length > MAX_POST_BYTES:
            raise BadRequest("request body is too large")

        try:
            value = json.loads(
                self.rfile.read(length).decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BadRequest("invalid JSON") from error
        if not isinstance(value, Mapping):
            raise BadRequest("JSON root must be an object")
        return value

    def _update_selection(
        self,
        experiment_name: str,
    ) -> None:
        experiment = self.repository.experiment(experiment_name)
        archive = self.repository.archive(experiment)
        valid_cells = set(archive["cells"])

        body = self._request_json()
        cell = body.get("cell")
        selected_value = body.get("selected")
        if not isinstance(cell, str):
            raise BadRequest("cell must be a string")
        self.repository.validate_segment(cell)
        if cell not in valid_cells:
            raise BadRequest("cell is not present in archive")
        if not isinstance(selected_value, bool):
            raise BadRequest("selected must be a boolean")

        selected = {
            value
            for value in self.repository.selection(experiment)
            if value in valid_cells
        }
        if selected_value:
            selected.add(cell)
        else:
            selected.discard(cell)

        self.repository.write_selection(experiment, selected)
        self._send_json(
            HTTPStatus.OK,
            {
                "cell": cell,
                "selected": selected_value,
                "selected_cells": sorted(selected),
            },
        )

    def do_POST(self) -> None:
        try:
            parts = self._parts()
            if (
                len(parts) == 3
                and parts[0] == "exp"
                and parts[2] == "selection"
            ):
                self._update_selection(parts[1])
                return
            raise MissingResource("route not found")
        except ForbiddenPath:
            self.send_error(HTTPStatus.FORBIDDEN.value)
        except MissingResource:
            self.send_error(HTTPStatus.NOT_FOUND.value)
        except BadRequest as error:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": str(error)},
            )
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
        ):
            self.send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR.value,
                "Could not update selection",
            )

    def log_message(
        self,
        format: str,
        *args: Any,
    ) -> None:
        print(
            f"{self.address_string()} "
            f"[{self.log_date_time_string()}] "
            f"{format % args}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="View WorldBloom experiment archives.",
    )
    parser.add_argument(
        "--runs",
        type=Path,
        required=True,
        help="Root containing WorldBloom experiment directories.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Listen address; defaults to loopback only.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5401,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 0 <= args.port <= 65535:
        raise ValueError("--port must be between 0 and 65535")

    repository = RunRepository(args.runs)
    server = ViewerServer(
        (args.host, args.port),
        ViewerHandler,
    )
    server.repository = repository

    host, port = server.server_address[:2]
    print(
        f"WorldBloom viewer: http://{host}:{port}/ "
        f"(runs={repository.runs_root})",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())