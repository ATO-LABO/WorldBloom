"""Deterministic data access and view models for the WorldBloom viewer."""

from __future__ import annotations

import copy
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gapengine.explanations import extract_explanation, is_turning_candidate
from gapengine.reader_summary import load_reviewed
from gapengine.qd import read_rows
from gapengine.scenes import describe_row, extract_scenes
from gapengine.synopsis import load_world_meta


@lru_cache(maxsize=None)
def _cached_world_meta(
    project_dir: Path,
    template_dir: Path,
) -> dict[str, Any]:
    """Load each resolved world's descriptive metadata only once."""

    return load_world_meta(project_dir, template_dir)


DEFAULT_CATEGORIES = ("I", "II", "III", "IV", "V", "VI")
DEFAULT_VOLATILITY_BINS = ("low", "mid", "high")
MINOR_GENERATIONS = 8

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
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return " / ".join(str(item) for item in value)
    return str(value)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


class RunRepository:
    """Read experiment artifacts while enforcing the configured run root."""

    def __init__(self, runs_root: Path, *, control_root=None, jobs=None) -> None:
        self.runs_root = runs_root.expanduser().resolve()
        if not self.runs_root.is_dir():
            raise ValueError(
                f"--runs must be an existing directory: {self.runs_root}"
            )

        self.catalog = None
        self.selections = None
        if control_root is not None:
            from viewer.run_catalog import RunCatalog
            from execution.selections import SelectionStore
            self.catalog = RunCatalog(self.runs_root, control_root, jobs)
            self.selections = SelectionStore(self.catalog)

    def _catalog_id(self, experiment):
        self.safe_path(experiment, "archive.json")
        return self.catalog.register_legacy(experiment.name)

    def history(self):
        if self.catalog is None:
            raise BadRequest("history requires a control root")
        return self.catalog.history()

    def toggle_selection(self, experiment, cell, selected):
        if self.selections is not None:
            return self.selections.toggle(self._catalog_id(experiment), cell, selected)
        from execution.provenance import directory_lock
        with directory_lock(experiment):
            valid = set(self.archive(experiment)["cells"])
            if cell not in valid or type(selected) is not bool:
                raise BadRequest("invalid cell or selection")
            values = self.selection(experiment) & valid
            values.add(cell) if selected else values.discard(cell)
            self._write_selection_legacy(experiment, values)
            return values

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
        if self.catalog is not None and (candidate / "manifest.json").is_file() and not (candidate / "published/current.json").is_file():
            raise MissingResource(f"published archive not found: {safe_name}")
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
            if self.catalog is not None and (resolved / "manifest.json").is_file() and not (resolved / "published/current.json").is_file():
                continue
            experiments.append((candidate.name, resolved))
        return experiments

    def archive(self, experiment: Path) -> dict[str, Any]:
        if self.catalog is not None and (experiment / "manifest.json").is_file():
            return self.catalog.snapshot(self.catalog.run_id(experiment.name), observe=False)["archive"]
        raw = _read_json(self.safe_path(experiment, "archive.json"))
        if not isinstance(raw, dict):
            raise ValueError("archive root must be a JSON object")
        cells = raw.get("cells")
        if not isinstance(cells, dict):
            raise ValueError("archive.cells must be a JSON object")
        return raw

    def selection(self, experiment: Path) -> set[str]:
        if self.selections is not None:
            return self.selections.projected(self._catalog_id(experiment))
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

    def write_selection(self, experiment, selected):
        if self.selections is not None:
            # Bulk compatibility callers also preserve all non-representative entries.
            rid = self._catalog_id(experiment)
            with self.selections._guard(rid):
                snapshot = self.catalog.snapshot(rid)
                previous = self.selections._read(rid) or self.selections._initial(rid, snapshot)
                reps = self.catalog.representatives(snapshot)
                if set(selected) - set(reps):
                    raise BadRequest("invalid selected cells")
                current = self.selections._projection(snapshot, previous)
                changes = [{"candidate_id": cid, "state": "adopted" if cell in selected else "unclassified"}
                           for cell, cid in reps.items() if (cell in selected) != (cell in current)]
                if changes:
                    self.selections._save(rid, changes, previous["revision"])
            return self.safe_path(experiment, "selection.json")
        from execution.provenance import directory_lock
        with directory_lock(experiment):
            return self._write_selection_legacy(experiment, selected)

    def _write_selection_legacy(
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


@lru_cache(maxsize=None)
def _yaml_mapping(path: Path) -> Mapping[str, Any]:
    """Read each immutable project/template YAML file only once."""

    if not path.is_file():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    return value if isinstance(value, Mapping) else {}


@lru_cache(maxsize=None)
def resolve_genre(
    world_name: str,
) -> tuple[str, Path, Path] | None:
    """Resolve a displayed world name to its project and template."""

    projects = ROOT / "projects"
    if not projects.is_dir():
        return None
    for project_dir in sorted(
        projects.iterdir(),
        key=lambda path: path.name,
    ):
        if not project_dir.is_dir():
            continue
        world = _yaml_mapping(project_dir / "world.yaml")
        if str(world.get("name", "")) != world_name:
            continue
        template_dir = ROOT / "templates" / project_dir.name
        if template_dir.is_dir():
            return project_dir.name, project_dir, template_dir
    return None


def qd_axes(
    template_dir: Path | None,
) -> tuple[list[str], list[str]]:
    """Return declared QD categories and volatility bins."""

    raw = (
        _yaml_mapping(template_dir / "qd.yaml")
        if template_dir is not None
        else {}
    )
    categories = [
        str(value)
        for value in _as_list(raw.get("categories"))
        if str(value)
    ]
    bins = [
        str(value)
        for value in _as_list(raw.get("volatility_bins"))
        if str(value)
    ]
    return (
        categories or list(DEFAULT_CATEGORIES),
        bins or list(DEFAULT_VOLATILITY_BINS),
    )


def world_meta_for(
    header: Mapping[str, Any],
) -> tuple[str | None, dict[str, Any]]:
    """Resolve a header to output metadata, with a safe fallback."""

    world_name = str(header.get("world", ""))
    resolved = resolve_genre(world_name)
    if resolved is not None:
        genre, project_dir, template_dir = resolved
        return (
            genre,
            dict(_cached_world_meta(project_dir, template_dir)),
        )
    return (
        None,
        {
            "name": world_name,
            "protagonist": str(header.get("protagonist", "")),
            "antagonist": str(header.get("antagonist", "")),
            "display_names": {},
            "ending_labels": {},
            "effect_descriptions": {},
        },
    )


def _elite_header(
    repository: RunRepository,
    experiment: Path,
    elite: Mapping[str, Any],
) -> dict[str, Any]:
    """Read only as far as the first header row of an exemplar log."""

    exemplar = _as_mapping(elite.get("exemplar"))
    layers_path = exemplar.get("layers_path")
    if not isinstance(layers_path, str):
        return {}
    path = repository.safe_path(experiment, layers_path)
    if not path.is_file():
        return {}

    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            value = json.loads(line)
            if (
                isinstance(value, Mapping)
                and value.get("kind") == "header"
            ):
                return dict(value)
    return {}


def _first_header(
    repository: RunRepository,
    experiment: Path,
    cells: Mapping[str, Any],
) -> dict[str, Any]:
    for cell_key in sorted(cells):
        elite = cells.get(cell_key)
        if isinstance(elite, Mapping):
            header = _elite_header(repository, experiment, elite)
            if header:
                return header
    synopsis_path = repository.safe_path(experiment, "synopses.json")
    if synopsis_path.is_file():
        raw = _read_json(synopsis_path)
        if isinstance(raw, Mapping) and raw.get("world"):
            return {"world": str(raw["world"])}
    return {}


def _entries(
    repository: RunRepository,
    experiment: Path,
    relative: str,
) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]]:
    path = repository.safe_path(experiment, relative)
    if not path.is_file():
        return {}, []
    raw = _read_json(path)
    if not isinstance(raw, Mapping):
        return {}, []
    entries = [
        value
        for value in _as_list(raw.get("entries"))
        if isinstance(value, Mapping)
    ]
    return raw, entries


def _generation_count(experiment: Path) -> int:
    return sum(
        1
        for path in experiment.iterdir()
        if path.is_dir() and re.fullmatch(r"g\d+", path.name)
    )


def _series(
    generations: Sequence[Any],
    key: str,
) -> list[float]:
    return [
        _number(item.get(key))
        for item in generations
        if isinstance(item, Mapping) and item.get(key) is not None
    ]


def experiment_meta(
    repository: RunRepository,
    experiment: Path,
) -> dict[str, Any]:
    """Collect stable metadata used by the index and experiment pages."""

    archive = repository.archive(experiment)
    cells = _as_mapping(archive.get("cells"))
    header = _first_header(repository, experiment, cells)
    genre, world_meta = world_meta_for(header)
    world = str(
        header.get("world")
        or world_meta.get("name")
        or "不明"
    )

    summary_path = repository.safe_path(experiment, "summary.json")
    summary: Mapping[str, Any] = {}
    if repository.catalog is not None and (experiment / "manifest.json").is_file():
        summary = repository.catalog.snapshot(repository.catalog.run_id(experiment.name), observe=False)["summary"]
    elif summary_path.is_file():
        value = _read_json(summary_path)
        if isinstance(value, Mapping):
            summary = value
    generations_raw = [
        value
        for value in _as_list(summary.get("generations"))
        if isinstance(value, Mapping)
    ]

    population: int | None = None
    population_path = repository.safe_path(
        experiment,
        "g0/population.json",
    )
    if population_path.is_file():
        population_raw = _read_json(population_path)
        if isinstance(population_raw, list):
            population = len(population_raw)

    synopsis_root, synopsis_entries = _entries(
        repository,
        experiment,
        "synopses.json",
    )
    _, story_entries = _entries(
        repository,
        experiment,
        "stories/index.json",
    )

    selected = repository.selection(experiment)
    truths: set[str] = set()
    engine_hash = str(header.get("engine_hash", ""))
    for cell_key in sorted(cells):
        elite = cells.get(cell_key)
        if not isinstance(elite, Mapping):
            continue
        exemplar = _as_mapping(elite.get("exemplar"))
        if not engine_hash:
            engine_hash = str(exemplar.get("engine_hash", ""))
        candidate_header = _elite_header(
            repository,
            experiment,
            elite,
        )
        truth = _as_mapping(candidate_header.get("truth"))
        culprit = truth.get("culprit")
        if culprit:
            truths.add(str(culprit))

    resolved = resolve_genre(world)
    template_dir = resolved[2] if resolved is not None else None
    categories, bins = qd_axes(template_dir)
    stat = repository.safe_path(experiment, "archive.json").stat()

    return {
        "name": experiment.name,
        "path": experiment,
        "world": world,
        "genre": genre or "不明",
        "protagonist": str(header.get("protagonist", "")),
        "antagonist": str(header.get("antagonist", "")),
        "truths": sorted(truths),
        "engine_hash": engine_hash,
        "archive_mtime": stat.st_mtime,
        "generations": len(generations_raw),
        "population": population,
        "seeds": (
            list(summary["seeds"])
            if isinstance(summary.get("seeds"), list)
            else None
        ),
        "target_ending": (
            summary.get("target_ending")
            or world_meta.get("target_ending_label")
        ),
        "keep": summary.get("keep"),
        "reach_series": _series(generations_raw, "reach_rate"),
        "occupied_series": _series(
            generations_raw,
            "occupied_cells",
        ),
        "quality_series": _series(
            generations_raw,
            "average_archive_quality",
        ),
        "dissimilarity_series": _series(
            generations_raw,
            "archive_dissimilarity",
        ),
        "dissimilarity": summary.get(
            "final_archive_dissimilarity"
        ),
        "cells": len(cells),
        "grid_size": len(categories) * len(bins),
        "thresholds": dict(
            _as_mapping(archive.get("volatility_thresholds"))
        ),
        "synopsis_ok": sum(
            entry.get("status") == "ok"
            for entry in synopsis_entries
        ),
        "synopsis_total": len(synopsis_entries),
        "selected": len(selected),
        "story_ok": sum(
            entry.get("status") == "ok"
            for entry in story_entries
        ),
        "synopsis_backend": synopsis_root.get("backend"),
        "categories": categories,
        "bins": bins,
        "world_meta": world_meta,
        "template_dir": template_dir,
    }


def is_minor(meta: Mapping[str, Any]) -> bool:
    generations = meta.get("generations")
    return (
        generations is None
        or int(_number(generations)) < MINOR_GENERATIONS
    )


def grouped_experiments(
    repository: RunRepository,
) -> tuple[list[tuple[str, list[dict[str, Any]]]], list[dict[str, Any]]]:
    """Group main experiments by world and collect short runs."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    minor: list[dict[str, Any]] = []
    for _, experiment in repository.experiments():
        meta = experiment_meta(repository, experiment)
        if is_minor(meta):
            minor.append(meta)
        else:
            grouped.setdefault(str(meta["world"]), []).append(meta)

    for values in grouped.values():
        values.sort(
            key=lambda value: (
                -float(value["archive_mtime"]),
                str(value["name"]),
            )
        )
    minor.sort(
        key=lambda value: (
            -float(value["archive_mtime"]),
            str(value["name"]),
        )
    )
    return sorted(grouped.items()), minor


def synopsis_entry(
    repository: RunRepository,
    experiment: Path,
    cell_key: str,
) -> tuple[Mapping[str, Any] | None, str | None]:
    root, entries = _entries(
        repository,
        experiment,
        "synopses.json",
    )
    for entry in entries:
        if entry.get("cell") == cell_key:
            return entry, (
                str(root.get("backend"))
                if root.get("backend") is not None
                else None
            )
    return None, (
        str(root.get("backend"))
        if root.get("backend") is not None
        else None
    )


def story_entry(
    repository: RunRepository,
    experiment: Path,
    cell_key: str,
) -> tuple[Mapping[str, Any] | None, str | None]:
    stories_dir = repository.safe_path(experiment, "stories")
    _, entries = _entries(
        repository,
        experiment,
        "stories/index.json",
    )
    for entry in entries:
        if entry.get("cell") != cell_key:
            continue
        story_path = entry.get("story_path")
        if not isinstance(story_path, str):
            return entry, None
        resolved = repository.safe_path(stories_dir, story_path)
        if not resolved.is_file():
            return entry, None
        return entry, resolved.read_text(encoding="utf-8")
    return None, None


def cell_hook(
    repository: RunRepository,
    experiment: Path,
    cell_key: str,
    elite: Mapping[str, Any],
    world_meta: Mapping[str, Any],
    synopsis: Mapping[str, Any] | None,
) -> str:
    """Build the compact story hook shown inside a grid cell."""

    if (
        synopsis is not None
        and synopsis.get("status") == "ok"
        and isinstance(synopsis.get("synopsis"), str)
    ):
        text = " ".join(str(synopsis["synopsis"]).split())
        return text[:50] + ("…" if len(text) > 50 else "")

    exemplar = _as_mapping(elite.get("exemplar"))
    relative = exemplar.get("layers_path")
    if not isinstance(relative, str):
        return ""
    path = repository.safe_path(experiment, relative)
    if not path.is_file():
        return ""

    scenes = extract_scenes(
        read_rows(path),
        world_meta,
        limit=12,
    )
    candidates = sorted(
        (
            scene
            for scene in scenes
            if int(_number(scene.get("priority"))) < 100
            and _as_list(scene.get("events"))
        ),
        key=lambda scene: (
            -int(_number(scene.get("priority"))),
            -float(_number(scene.get("delta_l1"))),
            int(_number(scene.get("turn"))),
        ),
    )
    return " → ".join(
        str(_as_list(scene.get("events"))[0])
        for scene in candidates[:2]
    )


def _belief_text(
    value: Any,
    world_meta: Mapping[str, Any],
) -> str:
    if value is None:
        return "なし"
    mapping = _as_mapping(value)
    raw_value = mapping.get("value")
    confidence = mapping.get("confidence")
    names = _as_mapping(world_meta.get("display_names"))
    displayed = str(names.get(str(raw_value), raw_value))
    if confidence is None:
        return displayed
    return f"{displayed}({_number(confidence):.2f})"


def detail_line(
    row: Mapping[str, Any],
    world_meta: Mapping[str, Any],
) -> str | None:
    """Render viewer-only details for actual belief changes."""

    verb = str(row.get("verb", ""))
    details = _as_mapping(row.get("details"))
    display_names = _as_mapping(world_meta.get("display_names"))

    if verb == "rethink":
        before = _as_mapping(details.get("before"))
        after = _as_mapping(details.get("after"))
        rendered: list[str] = []
        for fact in sorted(set(before) | set(after)):
            old = before.get(fact)
            new = after.get(fact)
            if old == new:
                continue
            fact_name = str(display_names.get(str(fact), fact))
            new_text = (
                _belief_text(new, world_meta)
                if fact in after
                else "保留"
            )
            rendered.append(
                f"{fact_name}: "
                f"{_belief_text(old, world_meta)} → {new_text}"
            )
        if not rendered:
            return None
        evidence = [
            str(display_names.get(str(value), value))
            for value in _as_list(details.get("evidence"))
        ]
        suffix = (
            f"。証拠: {', '.join(evidence)}"
            if evidence
            else ""
        )
        return "考え直した — " + "／".join(rendered) + suffix

    if verb == "confront":
        result = "正解" if bool(details.get("correct")) else "不正解"
        confidence = details.get("confidence")
        suffix = (
            f"（確信 {_number(confidence):.2f}）"
            if confidence is not None
            else ""
        )
        return f"問い詰めた — 指摘は{result}{suffix}"

    if verb == "learn_fact":
        raw_beliefs = _as_list(details.get("beliefs"))
        singular = details.get("belief")
        if isinstance(singular, Mapping):
            raw_beliefs = [*raw_beliefs, singular]

        rendered: list[str] = []
        for belief in raw_beliefs:
            if not isinstance(belief, Mapping):
                continue
            before = belief.get("before")
            after = belief.get("after")
            if before == after:
                continue
            fact = str(belief.get("fact", details.get("fact", "")))
            fact_name = str(display_names.get(fact, fact))
            outcome = str(belief.get("outcome", ""))
            rendered.append(
                f"{fact_name}: "
                f"{_belief_text(before, world_meta)}"
                f" → {_belief_text(after, world_meta)}"
                f"（{outcome}）"
            )
        return "／".join(rendered) or None

    return None


def _vector_value(vector: Sequence[Any], index: int) -> float:
    if index >= len(vector):
        return 0.0
    return max(0.0, min(1.0, _number(vector[index])))


def layer_points(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Aggregate snapshot vectors into the seven viewer series."""

    snapshots = [
        row
        for row in rows
        if row.get("kind") == "snapshot"
        and isinstance(row.get("vector"), list)
    ]
    pending_counts = []
    for snapshot in snapshots:
        layers = _as_mapping(snapshot.get("layers"))
        pending_counts.append(len(_as_list(layers.get("pending"))))
    pending_max = max(pending_counts, default=0)

    result: list[dict[str, Any]] = []
    for snapshot, pending_count in zip(
        snapshots,
        pending_counts,
        strict=True,
    ):
        vector = _as_list(snapshot.get("vector"))
        values = [
            (
                _vector_value(vector, 0)
                + _vector_value(vector, 1)
            )
            / 2.0,
            _vector_value(vector, 7),
            sum(_vector_value(vector, index) for index in range(2, 6))
            / 4.0,
            _vector_value(vector, 6),
            _vector_value(vector, 8),
            _vector_value(vector, 9),
            pending_count / pending_max if pending_max else 0.0,
        ]
        result.append(
            {
                "day": int(_number(snapshot.get("day"))),
                "turn": int(_number(snapshot.get("turn"))),
                "values": values,
            }
        )
    return result


def vitality_markers(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return downed, revived, and ending markers in log order."""

    return [
        {
            "day": int(_number(row.get("day"))),
            "turn": int(_number(row.get("turn"))),
            "kind": str(row.get("verb")),
        }
        for row in rows
        if str(row.get("verb")) in {"downed", "revived", "ending"}
    ]


def _relation_affinity(
    relations: Any,
    observer: str,
    target: str,
) -> float | None:
    for relation in _as_list(relations):
        if (
            isinstance(relation, Mapping)
            and relation.get("observer") == observer
            and relation.get("target") == target
        ):
            return _number(relation.get("affinity"))
    return None


def _snapshot_state(
    snapshot: Mapping[str, Any],
    protagonist: str,
    antagonist: str,
) -> dict[str, Any]:
    layers = _as_mapping(snapshot.get("layers"))
    ability = _as_mapping(layers.get("ability"))
    strength = _number(ability.get("base"))
    for modifier in _as_list(ability.get("modifiers")):
        if isinstance(modifier, Mapping) and bool(
            modifier.get("active", False)
        ):
            strength += _number(modifier.get("value"))

    relations = snapshot.get("relations")
    if not isinstance(relations, list):
        relations = layers.get("relations")

    return {
        "zone": layers.get("zone"),
        "vitality": layers.get("vitality"),
        "strength": strength,
        "beliefs": dict(
            _as_mapping(layers.get("valued_beliefs"))
        ),
        "holders": dict(_as_mapping(layers.get("objective"))),
        "phase": list(_as_list(layers.get("phase"))),
        "pending": len(_as_list(layers.get("pending"))),
        "stance_out": _relation_affinity(
            relations,
            protagonist,
            antagonist,
        ),
        "stance_in": _relation_affinity(
            relations,
            antagonist,
            protagonist,
        ),
    }


def _ordered_axes(
    categories: Sequence[str],
    bins: Sequence[str],
    cells: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    present_categories: set[str] = set()
    present_bins: set[str] = set()
    for cell_key in cells:
        if not isinstance(cell_key, str) or "|" not in cell_key:
            continue
        category, bin_name = cell_key.split("|", 1)
        present_categories.add(category)
        present_bins.add(bin_name)
    return (
        [
            *categories,
            *sorted(present_categories.difference(categories)),
        ],
        [
            *bins,
            *sorted(present_bins.difference(bins)),
        ],
    )


def cell_view(
    repository: RunRepository,
    experiment: Path,
    cell_key: str,
    *,
    view: str,
) -> dict[str, Any]:
    """Build all data needed by one elite page."""

    if view not in {"digest", "decisions", "all"}:
        raise BadRequest("view must be digest, decisions, or all")
    repository.validate_segment(cell_key)
    if (
        cell_key.count("|") != 1
        or not all(cell_key.split("|", 1))
    ):
        raise BadRequest(
            "cell must have the form category|volatility_bin"
        )

    archive = repository.archive(experiment)
    cells = _as_mapping(archive.get("cells"))
    elite = cells.get(cell_key)
    if not isinstance(elite, Mapping):
        raise MissingResource(f"cell not found: {cell_key}")

    exemplar = _as_mapping(elite.get("exemplar"))
    layers_path = exemplar.get("layers_path")
    if not isinstance(layers_path, str):
        raise ValueError("elite exemplar has no layers_path")
    resolved_layers = repository.safe_path(experiment, layers_path)
    if not resolved_layers.is_file():
        raise MissingResource("exemplar layers.jsonl not found")
    rows = read_rows(resolved_layers)

    header = next(
        (
            dict(row)
            for row in rows
            if row.get("kind") == "header"
        ),
        {},
    )
    genre, world_meta = world_meta_for(header)
    protagonist = str(
        world_meta.get("protagonist")
        or header.get("protagonist")
        or ""
    )
    antagonist = str(
        world_meta.get("antagonist")
        or header.get("antagonist")
        or ""
    )

    rows_by_turn: dict[int, list[Mapping[str, Any]]] = {}
    for row in rows:
        if "turn" in row:
            rows_by_turn.setdefault(
                int(_number(row.get("turn"))),
                [],
            ).append(row)

    turning_turns = {
        turn
        for turn, turn_rows in rows_by_turn.items()
        if any(is_turning_candidate(row, protagonist) for row in turn_rows)
    }

    all_scenes = extract_scenes(
        rows,
        world_meta,
        limit=max(1, len(rows)),
    )
    for scene in all_scenes:
        turn = int(scene["turn"])
        turn_rows = rows_by_turn.get(turn, [])
        scene["turning"] = turn in turning_turns
        scene["verbs"] = list(
            dict.fromkeys(
                str(row.get("verb", ""))
                for row in turn_rows
                if row.get("subject") == protagonist
                and row.get("verb")
            )
        )

    turning_scenes = [
        scene
        for scene in all_scenes
        if bool(scene.get("turning"))
    ]
    turning_omitted = max(0, len(turning_scenes) - 12)

    if view == "digest":
        selected_scenes = turning_scenes[:12]
        selected_turns = {
            int(scene["turn"])
            for scene in selected_scenes
        }
        remaining = sorted(
            (
                scene
                for scene in all_scenes
                if int(scene["turn"]) not in selected_turns
                and not bool(scene.get("turning"))
            ),
            key=lambda scene: (
                -int(_number(scene.get("priority"))),
                -float(_number(scene.get("delta_l1"))),
                int(_number(scene.get("turn"))),
            ),
        )
        selected_scenes.extend(
            remaining[: max(0, 12 - len(selected_scenes))]
        )
        scenes = sorted(
            selected_scenes,
            key=lambda scene: int(_number(scene.get("turn"))),
        )
    else:
        scenes = all_scenes

    for scene in scenes:
        details: list[str] = []
        for row in rows_by_turn.get(int(scene["turn"]), []):
            if row.get("subject") != protagonist:
                continue
            line = detail_line(row, world_meta)
            if line and line not in details:
                details.append(line)
        scene["details"] = details

    npc_by_turn: dict[int, list[str]] = {}
    if view == "all":
        for row in rows:
            if (
                row.get("kind") not in {"decision", "event"}
                or row.get("subject") == protagonist
                or "turn" not in row
            ):
                continue
            turn = int(_number(row.get("turn")))
            npc_by_turn.setdefault(turn, []).append(
                describe_row(row, world_meta)
            )

    snapshots_by_day: dict[int, Mapping[str, Any]] = {}
    for row in rows:
        if row.get("kind") == "snapshot":
            snapshots_by_day[int(_number(row.get("day")))] = row

    relevant_days = sorted(
        {
            int(scene.get("day", 0))
            for scene in scenes
        }
        | set(snapshots_by_day)
    )
    day_states: dict[int, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    for day in relevant_days:
        snapshot = snapshots_by_day.get(day)
        if snapshot is not None:
            current = _snapshot_state(
                snapshot,
                protagonist,
                antagonist,
            )
        if current is not None:
            day_states[day] = dict(current)

    meta = experiment_meta(repository, experiment)
    categories, bins = _ordered_axes(
        meta["categories"],
        meta["bins"],
        cells,
    )
    occupied = [
        f"{category}|{bin_name}"
        for category in categories
        for bin_name in bins
        if f"{category}|{bin_name}" in cells
    ]
    if cell_key not in occupied:
        raise BadRequest(
            "cell is not addressable on the archive axes"
        )
    position = occupied.index(cell_key)

    synopsis, synopsis_backend = synopsis_entry(
        repository,
        experiment,
        cell_key,
    )
    story, story_text = story_entry(
        repository,
        experiment,
        cell_key,
    )

    return {
        "explanation": _with_reader(repository, experiment, extract_explanation(resolved_layers, experiment=experiment.name, cell=cell_key)),
        "experiment": experiment.name,
        "cell": cell_key,
        "quality": _number(elite.get("quality")),
        "reach_rate": _number(elite.get("reach_rate")),
        "generation": int(_number(elite.get("generation"))),
        "seed": exemplar.get("seed"),
        "parents": list(_as_list(elite.get("parents"))),
        "genome": dict(_as_mapping(elite.get("genome"))),
        "categories": list(meta["categories"]),
        "seeds": list(meta.get("seeds") or []),
        "prev_cell": occupied[position - 1] if position else None,
        "next_cell": (
            occupied[position + 1]
            if position + 1 < len(occupied)
            else None
        ),
        "selected": cell_key in repository.selection(experiment),
        "view": view,
        "genre": genre,
        "world_meta": world_meta,
        "protagonist": protagonist,
        "antagonist": antagonist,
        "scenes": scenes,
        "turning_omitted": (
            turning_omitted if view == "digest" else 0
        ),
        "npc_by_turn": npc_by_turn,
        "day_states": day_states,
        "layer_points": layer_points(rows),
        "markers": vitality_markers(rows),
        "turn_rows": [
            row
            for row in rows
            if row.get("kind") not in {"header", "snapshot"}
            and "turn" in row
        ],
        "layers_path": layers_path,
        "synopsis": synopsis,
        "synopsis_backend": synopsis_backend,
        "story": story,
        "story_text": story_text,
    }


@lru_cache(maxsize=128)
def _cached_explanation(path, mtime_ns, size, experiment_name, cell_key):
    # Only extraction is cached; editorial approval is checked on every request.
    return extract_explanation(path, experiment=experiment_name, cell=cell_key)


def cell_explanation(repository, experiment, cell_key):
    repository.validate_segment(cell_key)
    elite = _as_mapping(repository.archive(experiment).get("cells")).get(cell_key)
    if not isinstance(elite, Mapping):
        raise MissingResource(f"cell not found: {cell_key}")
    relative = _as_mapping(elite.get("exemplar")).get("layers_path")
    if not isinstance(relative, str):
        raise MissingResource("exemplar log not recorded")
    path = repository.safe_path(experiment, relative)
    if not path.is_file():
        raise MissingResource("exemplar layers.jsonl not found")
    stat = path.stat()
    explanation = copy.deepcopy(_cached_explanation(
        path, stat.st_mtime_ns, stat.st_size, experiment.name, cell_key))
    return _with_reader(repository, experiment, explanation)


def _with_reader(repository, experiment, explanation):
    reader = load_reviewed(repository, experiment, explanation)
    if reader is not None:
        explanation["reader_summary"] = reader
    return explanation
