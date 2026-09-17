"""Static HTML export of experiment results for public (read-only) disclosure.

Reads run artifacts under --runs (archive.json/summary.json/synopses.json/
selection.json/stories/) and writes a self-contained static site under --out:
one index.html with a MAP-Elites grid per experiment, plus one HTML page per
selected story. No JS, no external assets.
"""
from __future__ import annotations

import argparse
import html
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CATEGORY_LABELS = {
    "I": "力", "II": "情報", "III": "社交",
    "IV": "身分", "V": "移動と停滞", "VI": "外部と還元",
}
DEFAULT_BINS = ["low", "mid", "high"]
DESCRIPTION_LINES = [
    "設定と結末を先に固定し、そのあいだの道のりを進化で探す。",
    "出来事の生成に LLM は関与しない。",
    "人が格子のあらすじを読んで選び、選ばれた道のりだけを AI が言葉にする。",
]

CSS = """
:root {
  color-scheme: light;
  --background: #f6f3ea;
  --surface: #fffdf8;
  --text: #24302a;
  --muted: #66716d;
  --line: #ddd8cc;
  --accent: #2e6b4f;
  --accent-dark: #1d4a36;
  --gold: #c9a24a;
  --warm-soft: #fbecd0;
  --danger: #8c3030;
  --font-ui: "Yu Gothic UI", "Yu Gothic", "Hiragino Kaku Gothic ProN", Meiryo, sans-serif;
  --font-serif: "Yu Mincho", "Hiragino Mincho ProN", serif;
}
body { font-family: var(--font-ui); margin: 0;
       background: var(--background); color: var(--text); line-height: 1.7; }
.site-header { background: var(--accent-dark); border-bottom: 3px solid var(--gold);
               color: #f8f4ec; padding: 14px max(16px, calc((100vw - 960px) / 2)); }
.site-header .wordmark { font-size: 1.1rem; font-weight: 750; text-decoration: none; color: #f8f4ec; }
main { max-width: 960px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 1.8rem; }
h2 { font-size: 1.3rem; border-bottom: 2px solid var(--line); padding-bottom: 4px; margin-top: 2.5rem; }
.lead p { margin: 0.3em 0; color: var(--muted); }
.meta { color: var(--muted); font-size: 0.92rem; }
.table-wrap { overflow-x: auto; margin: 1em 0; }
table { border-collapse: collapse; width: 100%; min-width: 480px; }
th, td { border: 1px solid var(--line); padding: 8px; text-align: left; vertical-align: top; font-size: 0.9rem; }
thead th { background: var(--surface); text-align: center; }
tbody th { background: var(--surface); white-space: nowrap; }
td.empty { color: var(--muted); text-align: center; }
td.selected { background: var(--warm-soft); border: 2px solid var(--gold); }
.stat { font-weight: 600; }
.synopsis-head { margin: 0 0 4px; }
.read-link { display: inline-block; margin-top: 6px; font-weight: 600; color: var(--accent); }
.missing { color: var(--danger); }
details summary { cursor: pointer; color: var(--text); }
.story-body { font-family: var(--font-serif); font-size: 1.05rem; line-height: 2.05; }
.story-body p { margin: 0.8em 0; }
.back { display: inline-block; margin-bottom: 1.5em; color: var(--accent); }
footer.credit { margin-top: 2em; font-size: 0.85rem; color: var(--muted); }
"""

HEADER = '<header class="site-header"><span class="wordmark">WorldBloom</span></header>'


def read_json(path: Path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def genre_of(experiment: str) -> str:
    return experiment.split("-", 1)[1] if "-" in experiment else experiment


def load_categories(root: Path, genre: str, archive: dict | None) -> list[str]:
    qd_path = root / "templates" / genre / "qd.yaml"
    if qd_path.exists():
        import yaml
        data = yaml.safe_load(qd_path.read_text(encoding="utf-8")) or {}
        cats = data.get("categories")
        if cats:
            return list(cats)
    if archive:
        return sorted({key.split("|", 1)[0] for key in archive.get("cells", {})})
    return []


def population_size(exp_dir: Path):
    pop = read_json(exp_dir / "g0" / "population.json")
    return len(pop) if isinstance(pop, list) else None


def model_label(root: Path, backend: str | None) -> str:
    if not backend:
        return "不明"
    settings = read_json(root / "settings.json") or {}
    model = settings.get("output", {}).get(backend, {}).get("model")
    return f"{backend} {model}" if model else backend


class Experiment:
    def __init__(self, root: Path, runs_dir: Path, name: str):
        self.name = name
        self.genre = genre_of(name)
        exp_dir = runs_dir / name
        self.exp_dir = exp_dir
        self.archive = read_json(exp_dir / "archive.json")
        self.summary = read_json(exp_dir / "summary.json")
        self.synopses = read_json(exp_dir / "synopses.json")
        self.selection = read_json(exp_dir / "selection.json")
        self.stories_index = read_json(exp_dir / "stories" / "index.json")
        self.categories = load_categories(root, self.genre, self.archive)
        self.population = population_size(exp_dir)
        self.world = (self.synopses or {}).get("world") or self.genre
        self.backend = (self.stories_index or {}).get("backend") or (self.synopses or {}).get("backend")

    @property
    def available(self) -> bool:
        return bool(self.archive and self.summary and self.synopses)

    def synopsis_for(self, cell: str):
        for entry in (self.synopses or {}).get("entries", []):
            if entry.get("cell") == cell:
                return entry
        return None

    def story_for(self, cell: str):
        for entry in (self.stories_index or {}).get("entries", []):
            if entry.get("cell") == cell:
                return entry
        return None

    def selected_cells(self) -> set:
        return set((self.selection or {}).get("selected", []))


def esc(text) -> str:
    return html.escape(str(text), quote=True)


def fmt_pct(value) -> str:
    return f"{value * 100:.1f}%" if isinstance(value, (int, float)) else "-"


def fmt_num(value, digits=3) -> str:
    return f"{value:.{digits}f}" if isinstance(value, (int, float)) else "-"


def story_filename(genre: str, cell: str) -> str:
    return f"{genre}-{cell.replace('|', '-')}.html"


def render_stats_line(exp: Experiment) -> str:
    gens = exp.summary.get("generations", [])
    n_gen = len(gens)
    reach_first = gens[0].get("reach_rate") if gens else None
    reach_last = gens[-1].get("reach_rate") if gens else None
    avg_quality = gens[-1].get("average_archive_quality") if gens else None
    dissimilarity = exp.summary.get("final_archive_dissimilarity")
    if dissimilarity is None and gens:
        dissimilarity = gens[-1].get("archive_dissimilarity")
    occupied = len((exp.archive or {}).get("cells", {}))
    total_cells = len(exp.categories) * len(DEFAULT_BINS)
    seeds = exp.summary.get("seeds", [])
    pop = exp.population if exp.population is not None else "-"
    parts = [
        f"世代数: {n_gen}",
        f"個体数: {pop}",
        f"シード: {', '.join(str(s) for s in seeds) if seeds else '-'}",
        f"到達率: {fmt_pct(reach_first)}→{fmt_pct(reach_last)}",
        f"占有マス: {occupied}/{total_cells}",
        f"平均品質: {fmt_num(avg_quality)}",
        f"相異度: {fmt_num(dissimilarity)}",
    ]
    return " ・ ".join(esc(p) for p in parts)


def render_grid(exp: Experiment) -> str:
    cells = (exp.archive or {}).get("cells", {})
    selected = exp.selected_cells()
    header = "<tr><th></th>" + "".join(f"<th>{esc(b)}</th>" for b in DEFAULT_BINS) + "</tr>"
    rows = []
    for cat in exp.categories:
        label = CATEGORY_LABELS.get(cat, cat)
        cols = [f"<th>{esc(cat)} {esc(label)}</th>"]
        for b in DEFAULT_BINS:
            cell_key = f"{cat}|{b}"
            cell = cells.get(cell_key)
            if not cell:
                cols.append('<td class="empty">未到達</td>')
                continue
            syn = exp.synopsis_for(cell_key)
            css_class = "selected" if cell_key in selected else ""
            body = [f'<p class="stat">q={fmt_num(cell.get("quality"))} reach={fmt_num(cell.get("reach_rate"))}</p>']
            if syn and syn.get("status") == "ok" and syn.get("synopsis"):
                text = syn["synopsis"]
                preview = text[:40]
                body.append(f'<p class="synopsis-head">{esc(preview)}…</p>')
                body.append(
                    f"<details><summary>あらすじ全文</summary><p>{esc(text)}</p></details>"
                )
            else:
                body.append('<p class="missing">あらすじ未生成</p>')
            if cell_key in selected:
                story = exp.story_for(cell_key)
                if story and story.get("status") == "ok":
                    href = f"stories/{story_filename(exp.genre, cell_key)}"
                    body.append(f'<a class="read-link" href="{esc(href)}">本文を読む</a>')
            cols.append(f'<td class="{css_class}">' + "".join(body) + "</td>")
        rows.append("<tr>" + "".join(cols) + "</tr>")
    return (
        '<div class="table-wrap"><table><thead>' + header + "</thead><tbody>"
        + "".join(rows) + "</tbody></table></div>"
    )


def render_experiment_section(exp: Experiment) -> str:
    heading = f"<h2>{esc(exp.world)}（{esc(exp.name)}）</h2>"
    if not exp.available:
        return heading + '<p class="missing">未生成</p>'
    return heading + f'<p class="meta">{render_stats_line(exp)}</p>' + render_grid(exp)


def render_index(experiments: list[Experiment]) -> str:
    lead = "".join(f"<p>{esc(line)}</p>" for line in DESCRIPTION_LINES)
    sections = "".join(render_experiment_section(exp) for exp in experiments)
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WorldBloom ── 遺伝的アルゴリズム×LLM による結末固定型の物語生成エンジン</title>
<style>{CSS}</style>
</head>
<body>
{HEADER}
<main>
<h1>WorldBloom</h1>
<p class="subtitle">遺伝的アルゴリズム×LLM による結末固定型の物語生成エンジン</p>
<div class="lead">{lead}</div>
{sections}
</main>
</body>
</html>
"""


def render_story_page(root: Path, exp: Experiment, cell: str) -> str | None:
    story = exp.story_for(cell)
    if not story or story.get("status") != "ok":
        return None
    story_path = exp.exp_dir / "stories" / story["story_path"]
    if not story_path.exists():
        return None
    body_text = story_path.read_text(encoding="utf-8")
    paragraphs = "".join(
        f"<p>{esc(line.strip())}</p>" for line in body_text.splitlines() if line.strip()
    )
    syn = exp.synopsis_for(cell)
    synopsis_html = f"<p>{esc(syn['synopsis'])}</p>" if syn and syn.get("synopsis") else ""
    layers_path = syn.get("layers_path") if syn else None
    backend = exp.backend
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(exp.world)} {esc(cell)}</title>
<style>{CSS}</style>
</head>
<body>
{HEADER}
<main>
<a class="back" href="../index.html">&larr; 一覧に戻る</a>
<h1>{esc(exp.world)}・{esc(cell)}</h1>
<section>{synopsis_html}</section>
<section class="story-body">{paragraphs}</section>
<footer class="credit">
<p>生成: {esc(model_label(root, backend))}</p>
<p>元データ: {esc(layers_path) if layers_path else '-'}</p>
</footer>
</main>
</body>
</html>
"""


def export(root: Path, runs_dir: Path, out_dir: Path, experiment_names: list[str]) -> dict:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    stories_dir = out_dir / "stories"
    stories_dir.mkdir(parents=True)

    experiments = [Experiment(root, runs_dir, name) for name in experiment_names]

    written_stories = []
    for exp in experiments:
        if not exp.available:
            continue
        for cell in exp.selected_cells():
            page = render_story_page(root, exp, cell)
            if page is None:
                continue
            path = stories_dir / story_filename(exp.genre, cell)
            path.write_text(page, encoding="utf-8")
            written_stories.append(path)

    index_path = out_dir / "index.html"
    index_path.write_text(render_index(experiments), encoding="utf-8")

    return {"index": index_path, "stories": written_stories}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--experiments", nargs="+", required=True)
    args = parser.parse_args(argv)
    result = export(ROOT, args.runs, args.out, args.experiments)
    print(json.dumps(
        {"index": str(result["index"]), "stories": [str(p) for p in result["stories"]]},
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
