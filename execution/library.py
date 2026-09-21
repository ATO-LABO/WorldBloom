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
import tempfile
from pathlib import Path

import yaml

from engine.yaml_cache import load_yaml
from execution.provenance import ConfigError, contained, identifier, directory_lock

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
        return load_yaml(path)
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
            from execution.genre_editor import metadata
            info = metadata(entry)
            result.append({"id": entry.name, "name": info.get("name") or entry.name, "description": info.get("description") or "", "files": files, "used_by": used_by})
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
        if kind == "genre":
            base = self._base(kind, owner_id)
            if not base.is_dir():
                raise ConfigError("genre_id", "対象がありません", code="not_found")
            with directory_lock(base):
                return self._write_file(kind, owner_id, rel, text)
        return self._write_file(kind, owner_id, rel, text)

    def _write_file(self, kind, owner_id, rel, text):
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

    @staticmethod
    def _text(value, field, *, required=False, maximum=8000):
        if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
            raise ConfigError(field, f"{maximum}文字以内で入力してください" if value else "入力してください", code="bad_request")
        return value.strip()

    def create_original_world(self, new_id, *, name, overview=""):
        identifier(new_id, "world_id")
        name = self._text(name, "name", required=True, maximum=120)
        overview = self._text(overview, "overview")
        if not (self.repo / "templates/basic").is_dir():
            raise ConfigError("template_id", "共通の基本ルールが見つかりません", code="unavailable")
        world = {
            "name": name, "overview": overview, "initial_story": "",
            "time": {"days": 7, "slots": ["朝", "昼", "夕方", "夜"]},
            "protagonist": "", "antagonist": "", "zones": [], "routes": {},
            "ending": [], "target_ending": [],
            "gapengine": {"action_graph": "templates/basic/action_graph.yaml",
                          "effects": "templates/basic/effects.yaml"},
        }
        return self._publish_world(new_id, world=world)

    def create_world(self, new_id, *, from_id, genre_id, name):
        identifier(new_id, "world_id")
        identifier(from_id, "from_world_id")
        identifier(genre_id, "template_id")
        name = self._text(name, "name", required=True, maximum=120)
        source = contained(self.repo / "projects", from_id)
        if not source.is_dir():
            raise ConfigError("from_world_id", "複製元の世界がありません", code="not_found")
        if not (self.repo / "templates" / genre_id).is_dir():
            raise ConfigError("template_id", "ジャンルがありません", code="not_found")
        _reject_symlinks(source)
        return self._publish_world(new_id, source=source, name=name, genre_id=genre_id)

    def _publish_world(self, new_id, *, world=None, source=None, name=None, genre_id=None):
        # Stage outside projects: listings never expose a half-created world.
        with directory_lock(self.repo / "projects"):
            dest = contained(self.repo / "projects", new_id)
            if dest.exists():
                raise ConfigError("world_id", "このIDは使用されています。別のIDを指定してください", code="conflict")
            with tempfile.TemporaryDirectory(prefix=".world-create-", dir=self.repo) as temporary:
                staged = Path(temporary) / "world"
                if source is not None:
                    shutil.copytree(source, staged)
                    try:
                        world = yaml.safe_load((staged / "world.yaml").read_text(encoding="utf-8"))
                    except (OSError, yaml.YAMLError) as error:
                        raise ConfigError("from_world_id", "複製元の世界を読めません", code="bad_request") from error
                    if not isinstance(world, dict):
                        raise ConfigError("from_world_id", "複製元の世界の形式が不正です", code="bad_request")
                    world["name"] = name
                    graph = dict(world.get("gapengine") or {})
                    graph.update(action_graph=f"templates/{genre_id}/action_graph.yaml", effects=f"templates/{genre_id}/effects.yaml")
                    world["gapengine"] = graph
                else:
                    (staged / "subjects").mkdir(parents=True)
                (staged / "world.yaml").write_text(yaml.safe_dump(world, allow_unicode=True, sort_keys=False), encoding="utf-8")
                staged.rename(dest)
        return new_id

    def update_world_basics(self, world_id, changes):
        if not isinstance(changes, dict) or not changes or set(changes) - {"name", "overview", "initial_story"}:
            raise ConfigError("request", "名前・概要・初期物語を指定してください", code="bad_request")
        cleaned = {key: self._text(value, key, required=key == "name", maximum=120 if key == "name" else 8000)
                   for key, value in changes.items()}
        base = self._base("world", world_id)
        if not base.is_dir():
            raise ConfigError("world_id", "世界がありません", code="not_found")
        with directory_lock(base):
            try:
                world = yaml.safe_load(self.read("world", world_id, "world.yaml"))
            except yaml.YAMLError as error:
                raise ConfigError("content", "世界の設定を読めません", code="bad_request") from error
            if not isinstance(world, dict):
                raise ConfigError("content", "世界の設定形式が不正です", code="bad_request")
            world.update(cleaned)
            self.write("world", world_id, "world.yaml", yaml.safe_dump(world, allow_unicode=True, sort_keys=False))
        return cleaned

    def create_genre(self, new_id, *, from_id):
        from execution.genre_editor import create
        return create(self, new_id, name=new_id, from_id=from_id)

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
