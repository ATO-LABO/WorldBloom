"""Pack a minimal, viewer-readable slice of experiment runs into samples/.

For each experiment, copies archive.json / archive_antagonist.json / summary.json /
synopses.json / selection.json / g0/population.json (whichever exist), all of
stories/, and — for every archive cell's exemplar — only its
layers.jsonl, kept at the same relative path. No other generation or
individual is copied. Raw prompts require --include-prompts and a content review.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.publication_guard import PublicationError, check_name, ensure_output_safe, validate_files

TOP_LEVEL_FILES = (
    "archive.json",
    "archive_antagonist.json",
    "summary.json",
    "synopses.json",
    "selection.json",
    "world_demand.json",
    "g0/population.json",
)
TOP_LEVEL_DIRS = ("stories",)


def _exemplar_layers_paths(archive_path: Path) -> list[str]:
    if not archive_path.is_file():
        return []
    archive = json.loads(archive_path.read_text(encoding="utf-8"))
    cells = archive.get("cells")
    if not isinstance(cells, dict):
        return []
    paths = []
    for cell in cells.values():
        if not isinstance(cell, dict):
            continue
        exemplar = cell.get("exemplar")
        layers_path = exemplar.get("layers_path") if isinstance(exemplar, dict) else None
        if isinstance(layers_path, str) and layers_path:
            paths.append(layers_path)
    return paths


def _copy(source: Path, run_dir: Path, out_dir: Path) -> int:
    if not source.is_file():
        return 0
    destination = out_dir / source.relative_to(run_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return source.stat().st_size


def pack_experiment(run_dir: Path, out_dir: Path, *, include_prompts: bool = False) -> tuple[int, int]:
    """Copy one experiment's minimal sample set. Returns (file_count, total_bytes)."""

    ensure_output_safe(out_dir, [run_dir])
    sources = [run_dir / relative for relative in TOP_LEVEL_FILES if (run_dir / relative).exists()]
    directories = TOP_LEVEL_DIRS + (("prompts",) if include_prompts else ())
    for dir_name in directories:
        source_dir = run_dir / dir_name
        if not source_dir.exists():
            continue
        validate_files([source_dir], run_dir)
        for source in sorted(source_dir.rglob("*")):
            validate_files([source], run_dir)
            if source.is_file():
                if source.suffix.lower() not in {'.txt', '.md', '.json'}:
                    raise PublicationError('Publication blocked: unexpected-story-file-type')
                sources.append(source)
    for relative in ("archive.json", "archive_antagonist.json"):
        for layers_path in _exemplar_layers_paths(run_dir / relative):
            source = run_dir / layers_path
            validate_files([source], run_dir)
            if source.is_file():
                sources.append(source)

    sources = list(dict.fromkeys(sources))
    validate_files(sources, run_dir)
    # Reject unsafe material before replacing a previously reviewed output.
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    total = sum(_copy(source, run_dir, out_dir) for source in sources)
    files = len(sources)

    return files, total


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Copy minimal viewer-readable samples out of experiment runs."
    )
    parser.add_argument("--runs", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--experiments", required=True, nargs="+")
    parser.add_argument("--include-prompts", action="store_true",
                        help="Include raw prompts only after reviewing their publication content.")
    args = parser.parse_args()

    report = {}
    for name in args.experiments:
        # Experiment names must not escape the selected roots.
        check_name(name)
        files, total = pack_experiment(args.runs / name, args.out / name,
                                      include_prompts=args.include_prompts)
        report[name] = {"files": files, "bytes": total}

    print(
        json.dumps(
            {
                "experiments": report,
                "total_files": sum(v["files"] for v in report.values()),
                "total_bytes": sum(v["bytes"] for v in report.values()),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
