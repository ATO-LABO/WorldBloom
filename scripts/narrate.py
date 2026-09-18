"""Narrate only the archive elites listed in a human selection file."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from execution.provenance import atomic_json
from gapengine.gpu_guard import GpuBusy, local_gpu_session
from gapengine.qd import Archive, read_rows
from gapengine.scenes import extract_scenes
from gapengine.synopsis import (
    BACKENDS,
    GenerationError,
    _output_section,
    build_narration_prompt,
    generate_text,
    load_settings,
    load_world_meta,
)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_cell_name(cell: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", cell)
    return value.strip("-") or "cell"


def _synopsis_by_cell(path: Path | None) -> dict[str, str]:
    if path is None or not path.is_file():
        return {}
    raw = _load_json(path)
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, str] = {}
    for entry in raw.get("entries", []) or []:
        if not isinstance(entry, Mapping):
            continue
        cell = entry.get("cell")
        synopsis = entry.get("synopsis")
        if isinstance(cell, str) and isinstance(synopsis, str):
            result[cell] = synopsis
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Narrate human-selected archive elites.",
    )
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--runs",
        type=Path,
        help="Run root; defaults to the archive directory.",
    )
    parser.add_argument(
        "--synopses",
        type=Path,
        help="Optional synopses.json used as selection context.",
    )
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
    parser.add_argument("--timeout", type=int, default=600)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.timeout < 1:
        raise ValueError("--timeout must be positive")

    archive_path = args.archive.resolve()
    selection_path = args.selection.resolve()
    output_dir = args.out.resolve()
    runs_root = (
        args.runs.resolve()
        if args.runs is not None
        else archive_path.parent
    )
    prompts_dir = output_dir.parent / "prompts"
    synopses_path = (
        args.synopses.resolve()
        if args.synopses is not None
        else archive_path.parent / "synopses.json"
    )

    selection = _load_json(selection_path)
    if not isinstance(selection, Mapping):
        raise ValueError("selection root must be a JSON object")
    raw_selected = selection.get("selected")
    if not isinstance(raw_selected, list) or not all(
        isinstance(value, str) for value in raw_selected
    ):
        raise ValueError("selection.selected must be a list of strings")

    selected = sorted(dict.fromkeys(raw_selected))
    archive = Archive.load(archive_path)
    world_meta = load_world_meta(
        args.project.resolve(),
        args.template.resolve(),
    )
    synopses = _synopsis_by_cell(synopses_path)
    entries: list[dict[str, Any]] = []
    index_path = output_dir / "index.json"
    payload = {"archive": archive_path.as_posix(), "backend": args.backend,
               "entries": entries, "selected": selected, "world": world_meta["name"]}
    reported_warnings: set[str] = set()

    settings, _settings_warning = load_settings(args.settings)
    output_settings = _output_section(settings)
    try:
        with local_gpu_session(args.backend, output_settings, owner="narrate", wait_seconds=args.timeout):
            for cell_key in selected:
                parts = cell_key.split("|")
                entry: dict[str, Any] = {
                    "cell": cell_key,
                    "status": "error",
                    "story_path": None,
                }
                if len(parts) != 2 or (parts[0], parts[1]) not in archive.cells:
                    entry["error"] = "selected cell is not present in archive"
                    entries.append(entry)
                    atomic_json(index_path, payload)
                    continue

                elite = archive.cells[(parts[0], parts[1])]
                elite_payload = elite.to_dict()
                elite_payload["cell"] = cell_key
                stem = _safe_cell_name(cell_key)
                prompt_path = prompts_dir / f"narration-{stem}.txt"
                story_path = output_dir / f"{stem}.md"
                entry["prompt_path"] = prompt_path.relative_to(
                    output_dir.parent
                ).as_posix()

                try:
                    rows = read_rows(
                        runs_root / str(elite.exemplar["layers_path"])
                    )
                    scenes = extract_scenes(rows, world_meta)
                    prompt = build_narration_prompt(
                        elite_payload,
                        scenes,
                        world_meta,
                        synopsis=synopses.get(cell_key),
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
                    if result.text is not None:
                        story_path.parent.mkdir(parents=True, exist_ok=True)
                        story_path.write_text(
                            result.text.rstrip() + "\n",
                            encoding="utf-8",
                            newline="\n",
                        )
                        entry["story_path"] = story_path.relative_to(
                            output_dir
                        ).as_posix()
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
                atomic_json(index_path, payload)
    except (GpuBusy, RuntimeError) as error:
        print(f"warning: {error}", file=sys.stderr)
        return 1

    atomic_json(index_path, payload)
    print(f"stories={output_dir} selected={len(selected)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())