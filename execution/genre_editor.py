"""Genre workspace storage and isolated draft validation. No simulation is run."""
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import uuid
import yaml
from execution.library import GENRE_FILES, MAX_BYTES, LibraryStore, _reject_symlinks
from execution.provenance import ConfigError, canonical, contained, directory_lock, identifier, materialize, sha256

META = "genre.json"
DEFAULTS = {"action_graph.yaml": {"nodes": [], "edges": []}, "canon.yaml": {"entries": []},
            "effects.yaml": [], "rules.yaml": [], "qd.yaml": {"categories": ["I", "II", "III", "IV", "V", "VI"], "volatility_bins": ["low", "mid", "high"]}}


def metadata(base):
    try:
        obj = json.loads(contained(base, META).read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def _json_tree(value, depth=0):
    if depth > 40: return False
    if value is None or isinstance(value, (str, bool, int)): return True
    if isinstance(value, float): return math.isfinite(value)
    if isinstance(value, list): return all(_json_tree(v, depth+1) for v in value)
    if isinstance(value, dict): return all(isinstance(k, str) and _json_tree(v, depth+1) for k, v in value.items())
    return False


def parse(path, content):
    if path not in (*GENRE_FILES, META):
        raise ConfigError("path", "対象外の項目です", code="bad_request")
    if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_BYTES:
        raise ConfigError("content", "256KB以内で入力してください", code="bad_request")
    try:
        value = json.loads(content) if path == META else yaml.safe_load(content)
    except (ValueError, yaml.YAMLError) as error:
        raise ConfigError("content", "設定の書式を確認してください", code="bad_request") from error
    if path == META:
        if not isinstance(value, dict): raise ConfigError("content", "基本情報の形式が不正です", code="bad_request")
        value["name"] = LibraryStore._text(value.get("name"), "name", required=True, maximum=120)
        value["description"] = LibraryStore._text(value.get("description", ""), "description")
    elif value is None and path in ("rules.yaml", "effects.yaml"):
        pass
    elif not isinstance(value, (dict, list)):
        raise ConfigError("content", "設定は項目の一覧または対応表で入力してください", code="bad_request")
    return value


def _base(store, ident):
    base = store._base("genre", ident)
    if not base.is_dir(): raise ConfigError("genre_id", "ジャンルがありません", code="not_found")
    return base


def snapshot(store, ident):
    base = _base(store, ident)
    files = {}
    for path in (*GENRE_FILES, META):
        target = contained(base, path)
        raw = target.read_bytes() if target.is_file() else None
        content = raw.decode("utf-8") if raw is not None else None
        value = None
        error = None
        if content is not None:
            try: value = parse(path, content)
            except ConfigError as exc: error = str(exc)
        elif path == META:
            value = {"name": ident, "description": ""}
        editable = error is None and _json_tree(value)
        files[path] = {"content": content, "value": value if editable else None, "form": editable,
                       "error": error, "revision": sha256(raw) if raw is not None else "missing"}
    return {"id": ident, "files": files, "revision": sha256(canonical({p: v["revision"] for p,v in files.items()}))}


def create(store, ident, *, name, description="", from_id=None):
    identifier(ident, "template_id")
    info = parse(META, json.dumps({"name": name, "description": description}, ensure_ascii=False))
    source = None
    if from_id is not None:
        source = _base(store, from_id)
        _reject_symlinks(source)
    with directory_lock(store.repo / "templates"):
        dest = contained(store.repo / "templates", ident)
        if dest.exists(): raise ConfigError("template_id", "このIDは使用されています", code="conflict")
        with tempfile.TemporaryDirectory(prefix=".genre-create-", dir=store.repo) as temporary:
            staged = Path(temporary) / "genre"
            if source is not None:
                shutil.copytree(source, staged, ignore=shutil.ignore_patterns(".write.lock"))
                # Preserve unrecognized metadata when copying a genre.
                info = {**metadata(staged), **info}
            else:
                staged.mkdir()
                for path, value in DEFAULTS.items():
                    (staged / path).write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")
            (staged / META).write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
            staged.rename(dest)
    return ident


def save(store, ident, path, content, revision):
    value = parse(path, content)
    base = _base(store, ident)
    with directory_lock(base):
        current = snapshot(store, ident)
        if revision != current["files"][path]["revision"]:
            raise ConfigError("revision", "別の画面で変更されています。入力を控えてから再読み込みしてください", code="conflict")
        target = contained(base, path)
        # One selected item per atomic save; other drafts stay in the browser.
        text = json.dumps(value, ensure_ascii=False, indent=2) if path == META else content
        temporary = target.with_name("." + target.name + "-" + uuid.uuid4().hex)
        try:
            temporary.write_text(text, encoding="utf-8")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return snapshot(store, ident)


def validate_draft(store, ident, *, world_id, files, revision):
    from execution.configs import ConfigStore, _capture_inputs, normalize
    identifier(world_id, "world_id")
    if not isinstance(files, dict) or set(files) - set((*GENRE_FILES, META)):
        raise ConfigError("files", "確認する項目が不正です", code="bad_request")
    for path, content in files.items(): parse(path, content)
    base = _base(store, ident)
    with directory_lock(base):
        current = snapshot(store, ident)
        if revision != current["revision"]:
            raise ConfigError("revision", "保存済み設定が変わりました。再読み込みして確認してください", code="conflict")
        spec = normalize({"label": "編集中のジャンルを確認", "project_id": world_id, "template_id": ident})
        try: blobs, _ = _capture_inputs(store.repo, spec)
        except FileNotFoundError as error:
            raise ConfigError("inputs", "世界の設定ファイルが不足しています", code="bad_request") from error
        for path, content in files.items():
            if path != META: blobs[f"templates/{ident}/{path}"] = content.encode("utf-8")
    # Check the chosen genre, including its graph/effects, regardless of the
    # world's currently assigned genre. Only the temporary world is rebased.
    world_key = f"projects/{world_id}/world.yaml"
    world = yaml.safe_load(blobs[world_key])
    graph = dict(world.get("gapengine") or {})
    graph.update(action_graph=f"../../templates/{ident}/action_graph.yaml", effects=f"../../templates/{ident}/effects.yaml")
    world["gapengine"] = graph
    blobs[world_key] = yaml.safe_dump(world, allow_unicode=True, sort_keys=False).encode("utf-8")
    with tempfile.TemporaryDirectory(prefix="wb-genre-check-") as temporary:
        root = Path(temporary)
        materialize(root / "repo", blobs)
        result = ConfigStore(root / "repo", root / "control", root / "runs").preview(spec)["preview"]
    return {"world_name": result["world_name"], "subjects": len(result["subjects"]),
            "target_endings": result["target_endings"], "fallbacks": result["fallbacks"], "revision": revision}
