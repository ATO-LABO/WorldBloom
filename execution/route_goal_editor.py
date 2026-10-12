"""Revision-checked, atomic world-specific survey goal editing. No GA jobs."""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import uuid

import yaml

from execution.library import LibraryStore, MAX_BYTES
from execution.provenance import ConfigError, canonical, contained, directory_lock, identifier, sha256
from gapengine.route_goals import resolve_goal
from gapengine.route import load_route_config
from gapengine.route_paths import _load_world_and_subjects
from gapengine.route_search import UnsupportedModel, survey_goal


def _read_yaml(path):
    if not path.exists():
        return {}
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_BYTES:
            raise ValueError("256KB以内のYAMLを指定してください")
        value = yaml.safe_load(raw)
        if not isinstance(value, dict):
            raise ValueError("YAMLは空ではない項目と値の形式で指定してください")
        return value
    except (ValueError, yaml.YAMLError, UnicodeError) as error:
        raise ConfigError("inputs", str(error)) from error


def _paths(store, world_id):
    identifier(world_id, "world_id")
    project = store._base("world", world_id)
    if not project.is_dir():
        raise ConfigError("world_id", "世界がありません", code="not_found")
    world = next(w for w in store.worlds() if w["id"] == world_id)
    genre = world.get("genre")
    if not genre:
        raise ConfigError("template_id", "先に世界のジャンルを選んでください")
    template = contained(store.repo, "templates/" + identifier(genre, "template_id"))
    if not template.is_dir():
        raise ConfigError("template_id", "ジャンルがありません", code="not_found")
    return project, template


def _revision(project, template):
    records = {}
    for label, root in (("world", project), ("template", template)):
        # Include absence and the selected genre; first creation also conflicts.
        records[label + ":root"] = root.name
        records[label + ":route.yaml"] = None
        for path in sorted(root.rglob("*")):
            safe = contained(root, path.relative_to(root).as_posix())
            if safe.is_file() and safe.suffix in (".yaml", ".yml"):
                records[label + ":" + path.relative_to(root).as_posix()] = sha256(safe.read_bytes())
    return sha256(canonical(records))


def _load(store, world_id):
    project, template = _paths(store, world_id)
    revision = _revision(project, template)
    source = "world" if contained(project, "route.yaml").exists() else "template"
    config = _read_yaml(contained(project if source == "world" else template, "route.yaml"))
    try:
        load_route_config(project if source == "world" else template)
        ids = [_read_yaml(contained(project, path.relative_to(project).as_posix())).get("id")
               for path in sorted((project / "subjects").glob("*.yaml"))]
        if any(not isinstance(person, str) or not person for person in ids) or len(set(ids)) != len(ids):
            raise ValueError("人物IDは空ではない一意の名前にしてください")
        world, subjects = _load_world_and_subjects(project, template)
    except (ValueError, TypeError, KeyError, yaml.YAMLError) as error:
        raise ConfigError("inputs", str(error)) from error
    if revision != _revision(project, template):
        raise ConfigError("revision", "設定が更新されました。再読み込みしてください", code="conflict")
    return project, template, revision, config, source, world, subjects


def snapshot(store: LibraryStore, world_id):
    _, _, revision, config, source, world, subjects = _load(store, world_id)
    raw_goal = config.get("goal")
    error = None
    try:
        parsed = resolve_goal(raw_goal, world, subjects)
        goal = parsed.to_input_dict() if raw_goal is not None else None
    except (ValueError, TypeError, KeyError) as failure:
        goal, error = deepcopy(raw_goal), str(failure)
    return {"world_id": world_id, "revision": revision, "source": source,
            "goal": goal, "error": error, "protagonist": world.protagonist,
            "people": sorted(subjects), "zones": sorted(world.zones),
            "items": sorted(world.items), "facts": sorted(world.facts),
            "days": world.days, "slots": list(world.slots)}


def _validate(loaded, body):
    _, _, revision, config, _, world, subjects = loaded
    if not isinstance(body, dict) or set(body) != {"revision", "goal"}:
        raise ConfigError("request", "revision と goal を指定してください", code="bad_request")
    if body["revision"] != revision:
        raise ConfigError("revision", "別の画面で更新されています。再読み込みしてください", code="conflict")
    raw = body["goal"]
    if raw is None:
        return None, {"status": "legacy", "message": "従来の配送目標を使います。人物の目標は変更しません。"}
    try:
        if not isinstance(raw, dict) or not isinstance(raw.get("all"), list) or len(raw["all"]) > 100:
            raise ValueError("目標は1〜100条件のANDで指定してください")
        goal = resolve_goal(raw, world, subjects)
        result = survey_goal(world, subjects, goal, config.get("search", {}))
        return goal.to_input_dict(), result
    except UnsupportedModel as error:
        return None, {"status": "unsupported", "message": str(error)}
    except (ValueError, TypeError, KeyError) as error:
        return None, {"status": "invalid_config", "message": str(error)}


def validate(store, world_id, body):
    _, result = _validate(_load(store, world_id), body)
    return result


def save(store, world_id, body):
    project, template = _paths(store, world_id)
    # Same locks as the existing world and genre editors. Fixed lock order.
    with directory_lock(template), directory_lock(project):
        loaded = _load(store, world_id)
        if loaded[1] != template:
            raise ConfigError("revision", "ジャンルが更新されました。再読み込みしてください", code="conflict")
        goal, result = _validate(loaded, body)
        if result["status"] in ("unsupported", "invalid_config"):
            raise ConfigError("goal", result["message"], code=result["status"])
        config = deepcopy(loaded[3])
        if goal is None:
            # No explicit goal: a cancel/unchanged legacy save is a no-op,
            # rather than creating a world override of the genre settings.
            if config.get("goal") is None:
                return snapshot(store, world_id)
            config.pop("goal", None)
        else:
            config["goal"] = goal
        raw = yaml.safe_dump(config, allow_unicode=True, sort_keys=False).encode("utf-8")
        if len(raw) > MAX_BYTES:
            raise ConfigError("goal", "設定は256KB以内にしてください")
        if loaded[2] != _revision(project, template):
            raise ConfigError("revision", "設定が更新されました。再読み込みしてください", code="conflict")
        path = contained(project, "route.yaml")
        temporary = contained(project, ".route.yaml-" + uuid.uuid4().hex)
        try:
            with temporary.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return snapshot(store, world_id)
