"""World and genre library: file I/O and validation (WB-UI-010 Stage 2).

Every path is checked against a fixed whitelist before touching disk. YAML
*syntax* is validated here; the actual semantic validation (people, rules,
QD axes, canon, ...) is never reimplemented -- it is delegated entirely to
the already-tested execution.configs.ConfigStore.preview.
"""
from __future__ import annotations

import os
import re
import shutil
import uuid
from pathlib import Path

import yaml

from execution.provenance import ConfigError, contained, identifier

GENRE_FILES = ("action_graph.yaml", "canon.yaml", "effects.yaml", "qd.yaml", "rules.yaml",
               "action_graph.antagonist.yaml", "canon.antagonist.yaml")

_SUBJECT_REL = re.compile(r"subjects/([A-Za-z0-9_-]{1,64})\.yaml")
_GENRE_PATH = re.compile(r"^templates/([A-Za-z0-9][A-Za-z0-9_-]{0,95})/")

MAX_BYTES = 256 * 1024
# rules.yaml/effects.yaml may be empty (execution/configs.py's _describe
# already documents a missing/empty file as a fallback default); every other
# library file must parse to a non-empty mapping or list.
_ALLOW_EMPTY = {"rules.yaml", "effects.yaml"}


def _load_yaml_or_none(path):
    if not path.is_file():
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return None


def _reject_symlinks(root):
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ConfigError("path", "リンクを含む複製元は使用できません", code="bad_request")


def _validate_rel(kind, rel):
    if kind == "world":
        if rel == "world.yaml" or _SUBJECT_REL.fullmatch(rel):
            return
    elif kind == "genre":
        if rel in GENRE_FILES:
            return
    else:
        raise ConfigError("kind", "対象外の種別です", code="bad_request")
    raise ConfigError("path", "対象外のファイルです", code="bad_request")


class LibraryStore:
    def __init__(self, repo):
        self.repo = Path(repo).absolute()

    # -- listing ------------------------------------------------------

    def worlds(self):
        root = self.repo / "projects"
        if not root.is_dir():
            return []
        result = []
        for entry in sorted(p for p in root.iterdir() if p.is_dir()):
            world = _load_yaml_or_none(entry / "world.yaml")
            world = world if isinstance(world, dict) else {}
            subjects_dir = entry / "subjects"
            subjects = sorted(subjects_dir.glob("*.yaml")) if subjects_dir.is_dir() else []
            result.append({
                "id": entry.name,
                "name": world.get("name"),
                "genre": self._genre_of(entry.name, world),
                "subjects": len(subjects),
                "protagonist": world.get("protagonist"),
                "antagonist": world.get("antagonist"),
            })
        return result

    def _genre_of(self, world_id, world):
        graph = (world.get("gapengine") or {}).get("action_graph") if isinstance(world, dict) else None
        if isinstance(graph, str):
            match = _GENRE_PATH.match(graph.replace(os.sep, "/"))
            if match:
                return match.group(1)
        if (self.repo / "templates" / world_id).is_dir():
            return world_id
        return None

    def genres(self):
        root = self.repo / "templates"
        if not root.is_dir():
            return []
        worlds = self.worlds()
        result = []
        for entry in sorted(p for p in root.iterdir() if p.is_dir()):
            files = [name for name in GENRE_FILES if (entry / name).is_file()]
            used_by = sorted(w["id"] for w in worlds if w["genre"] == entry.name)
            result.append({"id": entry.name, "files": files, "used_by": used_by})
        return result

    def world_files(self, world_id):
        base = contained(self.repo / "projects", identifier(world_id, "world_id"))
        if not base.is_dir():
            raise ConfigError("world_id", "世界がありません", code="not_found")
        files = ["world.yaml"] if (base / "world.yaml").is_file() else []
        subjects_dir = base / "subjects"
        if subjects_dir.is_dir():
            files += sorted("subjects/" + p.name for p in subjects_dir.glob("*.yaml"))
        return files

    # -- file I/O -------------------------------------------------------

    def _base(self, kind, owner_id):
        if kind == "world":
            return contained(self.repo / "projects", identifier(owner_id, "world_id"))
        if kind == "genre":
            return contained(self.repo / "templates", identifier(owner_id, "genre_id"))
        raise ConfigError("kind", "対象外の種別です", code="bad_request")

    def read(self, kind, owner_id, rel):
        _validate_rel(kind, rel)
        path = contained(self._base(kind, owner_id), rel)
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise ConfigError("path", "ファイルがありません", code="not_found") from None

    def write(self, kind, owner_id, rel, text):
        _validate_rel(kind, rel)
        if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_BYTES:
            raise ConfigError("content", "256KB以内のテキストを指定してください", code="bad_request")
        try:
            loaded = yaml.safe_load(text)
        except yaml.YAMLError as error:
            raise ConfigError("content", "YAMLとして読めません", code="bad_request") from error
        name = rel.rsplit("/", 1)[-1]
        if loaded is None and name not in _ALLOW_EMPTY:
            raise ConfigError("content", "空にできないファイルです", code="bad_request")
        if loaded is not None and not isinstance(loaded, (dict, list)):
            raise ConfigError("content", "YAMLの形式が不正です", code="bad_request")
        base = self._base(kind, owner_id)
        if not base.is_dir():
            raise ConfigError(kind + "_id", "対象がありません", code="not_found")
        path = contained(base, rel)
        if rel.startswith("subjects/"):
            path.parent.mkdir(parents=True, exist_ok=True)
        data = text.encode("utf-8")
        temporary = path.with_name("." + path.name + "-" + uuid.uuid4().hex)
        temporary.write_bytes(data)
        os.replace(temporary, path)
        return len(data)

    # -- creation ---------------------------------------------------------

    def create_world(self, new_id, *, from_id, genre_id, name):
        identifier(new_id, "world_id")
        identifier(from_id, "from_world_id")
        identifier(genre_id, "template_id")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError("name", "表示名を入力してください", code="bad_request")
        dest = contained(self.repo / "projects", new_id)
        if dest.exists():
            raise ConfigError("world_id", "既に存在します", code="conflict")
        source = contained(self.repo / "projects", from_id)
        if not source.is_dir():
            raise ConfigError("from_world_id", "複製元の世界がありません", code="not_found")
        if not (self.repo / "templates" / genre_id).is_dir():
            raise ConfigError("template_id", "ジャンルがありません", code="not_found")
        _reject_symlinks(source)
        shutil.copytree(source, dest)
        try:
            world_path = dest / "world.yaml"
            world = yaml.safe_load(world_path.read_text(encoding="utf-8"))
            if not isinstance(world, dict):
                raise ConfigError("world_id", "複製元のworld.yamlが不正です", code="bad_request")
            world["name"] = name
            gapengine = dict(world.get("gapengine") or {})
            gapengine["action_graph"] = f"templates/{genre_id}/action_graph.yaml"
            gapengine["effects"] = f"templates/{genre_id}/effects.yaml"
            world["gapengine"] = gapengine
            world_path.write_text(
                yaml.safe_dump(world, allow_unicode=True, sort_keys=False), encoding="utf-8")
        except (OSError, ValueError, TypeError, yaml.YAMLError, ConfigError):
            shutil.rmtree(dest, ignore_errors=True)
            raise
        return new_id

    def create_genre(self, new_id, *, from_id):
        identifier(new_id, "template_id")
        identifier(from_id, "from_template_id")
        dest = contained(self.repo / "templates", new_id)
        if dest.exists():
            raise ConfigError("template_id", "既に存在します", code="conflict")
        source = contained(self.repo / "templates", from_id)
        if not source.is_dir():
            raise ConfigError("from_template_id", "複製元のジャンルがありません", code="not_found")
        _reject_symlinks(source)
        try:
            shutil.copytree(source, dest)
        except OSError:
            shutil.rmtree(dest, ignore_errors=True)
            raise
        return new_id

    # -- validation ---------------------------------------------------------

    def validate(self, config_store, *, world_id, genre_id):
        preview = config_store.preview({
            "label": "検証", "project_id": world_id, "template_id": genre_id,
        })["preview"]
        return {
            "world_name": preview["world_name"], "protagonist": preview["protagonist"],
            "antagonist": preview["antagonist"], "subjects": len(preview["subjects"]),
            "target_endings": preview["target_endings"], "qd": preview["qd"],
            "fallbacks": preview["fallbacks"],
        }
