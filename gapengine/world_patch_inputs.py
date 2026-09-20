"""Resolved, sealed inputs and content identities shared by patch consumers."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import yaml

from execution.provenance import code_snapshot, contained, verify_files
from gapengine.world_patch import PatchError

ROOT = Path(__file__).resolve().parents[1]


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def read_subjects(folder) -> dict:
    return {p.name: yaml.safe_load(p.read_text(encoding="utf-8"))
            for p in sorted(Path(folder).glob("*.yaml"))}


def template_data(folder) -> dict:
    folder = Path(folder)
    return {p.relative_to(folder).as_posix(): yaml.safe_load(p.read_text(encoding="utf-8"))
            for p in sorted(folder.rglob("*")) if p.is_file() and p.suffix in (".yaml", ".yml")}


def resolve_references(world, project, repo_root=ROOT) -> dict:
    references = {}
    for role in ("action_graph", "effects"):
        value = (world.get("gapengine") or {}).get(role)
        if value is None:
            continue
        if not isinstance(value, str) or not value:
            raise PatchError(f"参照が不正です: {role}")
        path = Path(value)
        choices = [path] if path.is_absolute() else [Path(project) / path, Path(repo_root) / path]
        resolved = next((p.resolve() for p in choices if p.is_file()), None)
        if resolved is None:
            raise PatchError(f"参照先がありません: {role}")
        references[role] = resolved
    return references


def inputs_digest(world, subjects, template_dir, references) -> str:
    normalized = copy.deepcopy(world)
    refs = {}
    for role, path in sorted(references.items()):
        refs[role] = digest(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
        normalized.setdefault("gapengine", {})[role] = f"ref:{role}"
    for role in ("action_graph", "effects"):
        if (normalized.get("gapengine") or {}).get(role) is not None and role not in refs:
            raise PatchError(f"未解決の参照です: {role}")
    return digest({"world": normalized, "subjects": subjects,
                   "template": template_data(template_dir), "references": refs})


def runtime_digest(repo_root=ROOT) -> str:
    _, manifest = code_snapshot(Path(repo_root))
    # Git status / commit and absolute locations are provenance, not behavior.
    return digest({"files": [{k: record[k] for k in ("path", "sha256", "bytes")}
                             for record in manifest["files"]],
                   "python": manifest["python"], "pyyaml": manifest["pyyaml"]})


def verify_frozen(experiment) -> dict:
    """ConfigStore.verify_run's sealed-file checks without constructing a store."""
    root = Path(experiment)
    def raw(name):
        return contained(root, name).read_bytes()
    def read(name):
        return json.loads(raw(name))
    try:
        manifest = read("manifest.json")
        if hashlib.sha256(raw("manifest.json")).hexdigest() != read("complete.json")["manifest_sha256"]:
            raise ValueError("manifest seal")
        if hashlib.sha256(raw("config.json")).hexdigest() != manifest["config_sha256"]:
            raise ValueError("config seal")
        for name, folder in (("input", "inputs"), ("runtime", "runtime")):
            filename = f"{name}-manifest.json"
            if hashlib.sha256(raw(filename)).hexdigest() != manifest[f"{name}_manifest_sha256"]:
                raise ValueError(f"{name} manifest seal")
            verify_files(root / folder, read(filename)["files"])
        return read("config.json")
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise PatchError(f"凍結入力の封印を検証できません: {error}") from error


def resolve_experiment_inputs(experiment_dir, *, template_dir=None) -> dict:
    experiment = Path(experiment_dir)
    if (experiment / "manifest.json").exists():
        config = verify_frozen(experiment)
        project = contained(experiment, f"inputs/projects/{config['project_id']}")
        template = contained(experiment, f"inputs/templates/{config['template_id']}")
        source = "frozen_inputs"
        repo = experiment / "inputs"
    elif (experiment / "expanded-project/world.yaml").is_file():
        project = experiment / "expanded-project"
        source_path = project / "source.json"
        record = json.loads(source_path.read_text(encoding="utf-8")) if source_path.is_file() else {}
        template = Path(template_dir) if template_dir else Path(record.get("template", ""))
        if not template.is_dir() or (not template_dir and (
                not record or digest(template_data(template)) != record.get("template_digest"))):
            raise PatchError("拡張実験のtemplateを確認できません。--template を指定してください")
        source, repo = "expanded_project", ROOT
    else:
        # Keep legacy genre lookup identical to lineage's former fallback.
        from viewer import data
        repository = data.RunRepository(experiment.parent)
        archive = json.loads(repository.safe_path(experiment, "archive.json").read_text(encoding="utf-8"))
        header = data._first_header(repository, experiment, archive.get("cells") or {})
        resolved = data.resolve_genre(str(header.get("world", "")))
        if resolved is None:
            raise PatchError(f"cannot resolve project/template for world {header.get('world')!r}")
        _, project, template = resolved
        if template_dir:
            template = Path(template_dir)
        source, repo = "repository", ROOT
    world_path = project / "world.yaml"
    world = yaml.safe_load(world_path.read_text(encoding="utf-8"))
    refs = resolve_references(world, project, repo)
    if source == "frozen_inputs" and any(not p.is_relative_to(repo.resolve()) for p in refs.values()):
        raise PatchError("凍結入力の参照が封印された入力集合の外にあります")
    return {"world_path": world_path, "subjects_dir": project / "subjects", "template_dir": template,
            "source": source, "references": refs}
