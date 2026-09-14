"""Generate deterministic prompts and optional synopses for every elite."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from execution.provenance import atomic_json
from gapengine.qd import Archive, read_rows
from gapengine.scenes import extract_scenes
from gapengine.synopsis import (
    BACKENDS,
    GenerationError,
    build_synopsis_prompt,
    generate_text,
    load_world_meta,
)


def _safe_cell_name(cell: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", cell)
    return value.strip("-") or "cell"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate synopses for all archive elites.",
    )
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--backend",
        choices=BACKENDS,
        default="none",
    )
    parser.add_argument(
        "--project",
        type=Path,
        default=ROOT / "projects" / "momotaro",
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=ROOT / "templates" / "momotaro",
    )
    parser.add_argument(
        "--settings",
        type=Path,
        default=ROOT / "settings.json",
    )
    parser.add_argument("--cells", nargs="+", help="Only update these archive cell keys; preserve other entries.")
    parser.add_argument("--timeout", type=int, default=600)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.timeout < 1:
        raise ValueError("--timeout must be positive")

    archive_path = args.archive.resolve()
    runs_root = args.runs.resolve()
    output_path = args.out.resolve()
    prompts_dir = output_path.parent / "prompts"
    world_meta = load_world_meta(
        args.project.resolve(),
        args.template.resolve(),
    )
    archive = Archive.load(archive_path)

    selected = None if args.cells is None else set(args.cells)
    available = {"|".join(cell) for cell in archive.cells}
    if selected is not None and selected - available:
        raise ValueError("requested cells are not present in archive")
    entries: list[dict[str, Any]] = []
    if selected is not None and output_path.exists():
        previous = json.loads(output_path.read_text(encoding="utf-8"))
        if not isinstance(previous, dict) or not isinstance(previous.get("entries"), list):
            raise ValueError("existing synopses are invalid")
        if previous.get("archive") != archive_path.as_posix():
            raise ValueError("existing synopses belong to another archive")
        entries = [entry for entry in previous["entries"] if entry.get("cell") not in selected]
    payload = {"archive": archive_path.as_posix(), "backend": args.backend,
               "entries": entries, "world": world_meta["name"]}
    reported_warnings: set[str] = set()

    for cell in sorted(archive.cells):
        cell_key = "|".join(cell)
        if selected is not None and cell_key not in selected:
            continue
        elite = archive.cells[cell]
        elite_payload = elite.to_dict()
        elite_payload["cell"] = cell_key
        layers_path = runs_root / str(
            elite.exemplar["layers_path"]
        )
        prompt_path = (
            prompts_dir
            / f"synopsis-{_safe_cell_name(cell_key)}.txt"
        )

        entry: dict[str, Any] = {
            "cell": cell_key,
            "descriptor": elite.descriptor.to_dict(),
            "generation": elite.generation,
            "layers_path": elite.exemplar["layers_path"],
            "prompt_path": prompt_path.relative_to(
                output_path.parent
            ).as_posix(),
            "quality": elite.quality,
            "reach_rate": elite.reach_rate,
            "seed": elite.exemplar.get("seed"),
            "status": "error",
            "synopsis": None,
        }

        try:
            rows = read_rows(layers_path)
            scenes = extract_scenes(rows, world_meta)
            prompt = build_synopsis_prompt(
                elite_payload,
                scenes,
                world_meta,
            )
            prompt_path.parent.mkdir(parents=True, exist_ok=True)
            prompt_path.write_text(
                prompt,
                encoding="utf-8",
                newline="\n",
            )
            result = generate_text(
                args.backend,
                prompt,
                settings_path=args.settings,
                timeout=args.timeout,
            )
            entry["status"] = result.status
            entry["synopsis"] = result.text
            if result.warning and result.warning not in reported_warnings:
                print(f"warning: {result.warning}", file=sys.stderr)
                reported_warnings.add(result.warning)
        except (OSError, ValueError, GenerationError) as error:
            entry["error"] = str(error)
            print(
                f"warning: {cell_key}: {error}",
                file=sys.stderr,
            )

        entries.append(entry)
        entries.sort(key=lambda item: item["cell"])
        atomic_json(output_path, payload)

    payload = {
        "archive": archive_path.as_posix(),
        "backend": args.backend,
        "entries": entries,
        "world": world_meta["name"],
    }
    atomic_json(output_path, payload)
    print(f"synopses={output_path} entries={len(entries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())