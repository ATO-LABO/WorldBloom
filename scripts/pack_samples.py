"""Pack a minimal, viewer-readable slice of experiment runs into samples/.

For each experiment, copies archive.json / archive_antagonist.json / summary.json /
synopses.json / selection.json / g0/population.json (whichever exist), all of
prompts/ and stories/, and — for every archive cell's exemplar — only its
layers.jsonl, kept at the same relative path. No other generation or
individual is copied.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

TOP_LEVEL_FILES = (
    "archive.json",
    "archive_antagonist.json",
    "summary.json",
    "synopses.json",
    "selection.json",
    "g0/population.json",
)
TOP_LEVEL_DIRS = ("prompts", "stories")


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


def pack_experiment(run_dir: Path, out_dir: Path) -> tuple[int, int]:
    """Copy one experiment's minimal sample set. Returns (file_count, total_bytes)."""

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    files = 0
    total = 0

    for relative in TOP_LEVEL_FILES:
        size = _copy(run_dir / relative, run_dir, out_dir)
        if size or (run_dir / relative).is_file():
            files += 1
            total += size

    for dir_name in TOP_LEVEL_DIRS:
        source_dir = run_dir / dir_name
        if not source_dir.is_dir():
            continue
        for source in sorted(source_dir.rglob("*")):
            if not source.is_file():
                continue
            total += _copy(source, run_dir, out_dir)
            files += 1

    for relative in ("archive.json", "archive_antagonist.json"):
        for layers_path in _exemplar_layers_paths(run_dir / relative):
            size = _copy(run_dir / layers_path, run_dir, out_dir)
            if size:
                files += 1
                total += size

    return files, total


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Copy minimal viewer-readable samples out of experiment runs."
    )
    parser.add_argument("--runs", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--experiments", required=True, nargs="+")
    args = parser.parse_args()

    report = {}
    for name in args.experiments:
        files, total = pack_experiment(args.runs / name, args.out / name)
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
