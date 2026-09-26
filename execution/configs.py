"""Validated, immutable settings and run-input preparation for UI-002.

This module exposes Python services; HTTP routes and worker lifecycle belong to
UI-003/007. Creating or previewing a configuration never starts GA or an LLM.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import inspect
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid

import yaml

from engine.sim import Simulation
from engine.subject import Subject
from engine.world import World
from gapengine.evolve import _load_yaml, _rationality_backend_cfg, _rule_ids
from gapengine.genome import Genome
from gapengine.ollama import DEFAULT_MODEL as RATIONALITY_DEFAULT_MODEL
from gapengine.policy import _compile_rules
from gapengine.precedent import load_canon
from gapengine.seed_genomes import from_archive as seed_genomes_from_archive
from gapengine.synopsis import _backend_config
from gapengine.world_patch import LIBRARY_DIR, PatchError, apply_patches, approved_patches, template_identifiers
from gapengine.world_patch_usage import load_archive as load_seed_archive
from scripts.evolve import build_parser
from execution.provenance import (
    ConfigError, atomic_json, canonical, code_snapshot, contained, directory_lock,
    identifier, materialize, publish_directory, python_executable, read_json, sha256, verify_files,
)


def _safe_path_token(value):
    """Collapse anything but [A-Za-z0-9._-] to "_" -- used only to turn a
    rationality.yaml model name into one filename component (WB-JEV-002).
    template_id is already identifier()-safe; this covers the model/method
    names, which come from repo content rather than the API, so a stray "/"
    or ".." in a hand-edited rationality.yaml still can't leave the token.
    Truncated to 80 chars (Opus review) -- a pathologically long model name
    could otherwise approach Windows' MAX_PATH once joined under control."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(value))[:80] or "_"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _object(value, field):
    if not isinstance(value, dict):
        raise ConfigError(field, "オブジェクトを指定してください")
    return value


def _keys(value, allowed, field):
    _object(value, field)
    if set(value) - set(allowed):
        raise ConfigError(field, "未対応の設定項目があります")


def _integer(value, field, minimum=None):
    if type(value) is not int or (minimum is not None and value < minimum):
        raise ConfigError(field, "指定範囲の整数を入力してください")
    return value


def _model(value):
    if value is not None and (not isinstance(value, str) or not
                             re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", value)):
        raise ConfigError("generation.model", "モデル名を明示してください")
    return value


def evolution_defaults():
    parser = build_parser()
    # "resume"/"resume_allow_code_change" (WB-GA-RESUME) are CLI-only,
    # run-mode flags with no bearing on what a saved config *means* --
    # excluding their dests here (same treatment as project/template/out)
    # keeps them out of normalize()'s allowed evolution.* keys,
    # prepare_run()'s generated argv, and legacy_settings(), so existing
    # configs/argv/tests are byte-for-byte unaffected.
    return {a.dest: deepcopy(a.default) for a in parser._actions
            if a.dest not in {"help", "project", "template", "out",
                               "resume", "resume_allow_code_change"}}


def quick_label(world_name, genre):
    """Default 設定名 for a freshly opened config form (WB-UI-022).

    Naive local time is intentional here (a human-facing label, unlike this
    module's UTC created_at) -- don't "fix" it to UTC. The quick-start button
    itself doesn't call this: it builds the same label client-side at click
    time (workbench.js's quickLabel), since this render-time value would
    otherwise go stale if the page sits open before the button is clicked.
    """
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"{stamp} {genre} - {world_name}"


def _growth(spec_growth):
    """None (mode="off"/absent) means "no growth key at all" -- the caller
    must not add "growth" to the saved document in that case, so an
    unspecified/off config stays byte-identical to one saved before
    WB-WORLDGROW-001 段階5c ever existed. growth never enters `evolution`
    (its keys must match scripts/evolve.py's parser dests one-to-one --
    see evolution_defaults()) and is never handed to prepare_run()'s argv
    loop, which only ever iterates config["evolution"]."""
    if spec_growth is None:
        return None
    _keys(spec_growth, {"mode", "epochs", "auto_retire"}, "growth")
    mode = spec_growth.get("mode", "off")
    if mode not in ("off", "auto", "manual"):
        raise ConfigError("growth.mode", "育成モードの指定が不正です")
    if mode == "off":
        return None
    epochs = spec_growth.get("epochs", 3)
    _integer(epochs, "growth.epochs", 1)
    if epochs > 10:
        raise ConfigError("growth.epochs", "エポック数は1〜10で指定してください")
    auto_retire = spec_growth.get("auto_retire", mode == "auto")
    if type(auto_retire) is not bool:
        raise ConfigError("growth.auto_retire", "真偽値を指定してください")
    return {"mode": mode, "epochs": epochs, "auto_retire": auto_retire}


def normalize(spec):
    _keys(spec, {"label", "project_id", "template_id", "evolution",
                 "execution_limits", "generation", "growth"}, "config")
    if "generation" in spec:
        raise ConfigError("generation", "文章生成の設定は ⚙ 設定で行います（実行設定には含めません）")
    growth = _growth(spec.get("growth"))
    result = {"label": spec.get("label", "")}
    if not isinstance(result["label"], str) or not result["label"].strip() or len(result["label"]) > 200:
        raise ConfigError("label", "設定名を1〜200文字で入力してください")
    for name in ("project_id", "template_id"):
        result[name] = identifier(spec.get(name), name)
    defaults = evolution_defaults()
    changes = spec.get("evolution", {})
    _keys(changes, defaults, "evolution")
    values = {**defaults, **changes}
    for name in ("generations", "population", "seeds", "processes"):
        _integer(values[name], "evolution." + name, 1)
    _integer(values["seed_base"], "evolution.seed_base", 0)
    _integer(values["ga_seed"], "evolution.ga_seed")
    for name in ("coevolve", "meta_evolution", "record_explanations"):
        if type(values[name]) is not bool:
            raise ConfigError("evolution." + name, "真偽値を指定してください")
    if values["keep"] not in ("all", "reached", "exemplar"):
        raise ConfigError("evolution.keep", "保存方針が不正です")
    if values["world_expansion"] not in ("off", "detect", "expand"):
        raise ConfigError("evolution.world_expansion", "世界の拡張の指定が不正です")
    if growth is not None:
        # WB-WORLDGROW-001 段階5c §9: growth!=off always runs the chain's
        # own expand-run experiments, regardless of what the form's "世界の
        # 拡張" radio was set to.
        values["world_expansion"] = "expand"
    # WB-WORLDGROW-001 段階5b: "" (フォームの未選択) は off と同じ None に。
    seed_genomes = values["seed_genomes"]
    if seed_genomes == "":
        values["seed_genomes"] = None
    elif seed_genomes is not None:
        values["seed_genomes"] = identifier(seed_genomes, "evolution.seed_genomes")
    endings = values["target_ending"]
    if endings is not None and (not isinstance(endings, list) or not endings
            or any(not isinstance(x, str) or not x for x in endings)
            or len(set(endings)) != len(endings)):
        raise ConfigError("evolution.target_ending", "結末IDの重複しない配列を指定してください")
    # WB-JEV-002: kappa is the only rationality field the UI/API may set.
    # rationality_table is never accepted here -- it would let a caller point
    # GA at an arbitrary write path -- prepare_run() alone decides it, always
    # under this config's own control root (see below).
    kappa = values["kappa"]
    if kappa is not None:
        if (type(kappa) is bool or not isinstance(kappa, (int, float))
                or not (0 <= kappa <= 1)):
            raise ConfigError("evolution.kappa", "0〜1の数値を指定してください")
        # 0 means "off", same as never having set it -- normalizing it away
        # here keeps a kappa=0 config byte-identical to a pre-WB-JEV-002 one,
        # so prepare_run()'s CLI argv (and the frozen-runtime tests that
        # compare it against a hand-typed invocation) never see --kappa 0.
        values["kappa"] = None if kappa == 0 else float(kappa)
    # WB-ROUTE-001 S4 §2: same 0-means-off normalization as kappa above, so
    # a route_rho=0 config stays byte-identical to one that never set it
    # (prepare_run()'s argv never sees --route-rho 0).
    route_rho = values["route_rho"]
    if route_rho is not None:
        if (type(route_rho) is bool or not isinstance(route_rho, (int, float))
                or not (0 <= route_rho <= 1)):
            raise ConfigError("evolution.route_rho", "0〜1の数値を指定してください")
        values["route_rho"] = None if route_rho == 0 else float(route_rho)
    if values["rationality_backend"] not in (None, "ollama", "none"):
        raise ConfigError("evolution.rationality_backend", "backendの指定が不正です")
    if values["rationality_method"] not in (None, "noul", "choice"):
        raise ConfigError("evolution.rationality_method", "methodの指定が不正です")
    if values["rationality_max_calls"] is not None:
        _integer(values["rationality_max_calls"], "evolution.rationality_max_calls", 1)
    if values["rationality_table"] is not None:
        raise ConfigError("evolution.rationality_table", "表の保存先はサーバーが決めます")
    # WB-JEV-003: same model-name shape as generation.model (_model()'s
    # regex, inlined rather than reusing that helper's hardcoded field name).
    if values["rationality_model"] is not None and (
        not isinstance(values["rationality_model"], str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", values["rationality_model"])
    ):
        raise ConfigError("evolution.rationality_model", "モデル名を明示してください")
    if values["rationality_num_ctx"] is not None:
        if (type(values["rationality_num_ctx"]) is not int
                or not (256 <= values["rationality_num_ctx"] <= 131072)):
            raise ConfigError("evolution.rationality_num_ctx", "256〜131072の整数を指定してください")
    result["evolution"] = deepcopy(values)
    limits = spec.get("execution_limits", {})
    _keys(limits, {"wall_seconds"}, "execution_limits")
    result["execution_limits"] = {"wall_seconds": _integer(
        limits.get("wall_seconds", 3600), "execution_limits.wall_seconds", 1)}
    if growth is not None:
        result["growth"] = growth
    return result


def _auto_launchable(settings, config, model):
    """True when the GPU guard is on and llama-server's launch command can start `model`."""
    from gapengine.synopsis import _output_section
    if not isinstance(_output_section(settings).get("gpu_guard"), dict):
        return False
    launch = config.get("launch")
    if not isinstance(launch, list) or not launch or not all(isinstance(x, str) for x in launch):
        return False
    if shutil.which(launch[0]) is None and not Path(launch[0]).is_file():
        return False
    # A launch command that names a different --alias would serve another model name.
    if "--alias" in launch[:-1] and launch[launch.index("--alias") + 1] != model:
        return False
    return True


def generation_availability(generation, settings_path=None):
    """Return only allowlisted facts. Never return credentials or raw errors."""
    backend = generation["backend"]
    model = generation.get("model")
    base = {"backend": backend, "model": model, "available": False,
            "authentication": "unverified", "reason": None,
            "limits": deepcopy(generation["limits"])}
    if backend == "none":
        return {**base, "model": None, "available": True,
                "authentication": "not_required", "completion_kind": "prompt_only"}
    settings = {}
    if settings_path is not None:
        try:
            settings = read_json(settings_path)
            if not isinstance(settings, dict):
                raise ValueError()
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            return {**base, "reason": "settings_unreadable"}
    config = _backend_config(settings, backend)
    if model is None:
        try:
            model = _model(config.get("model"))
        except ConfigError:
            return {**base, "reason": "invalid_model"}
    base["model"] = model
    if model is None:
        return {**base, "reason": "model_required"}
    if backend.endswith("-cli"):
        command = config.get("command", "codex" if backend == "codex-cli" else "claude")
        if not isinstance(command, str) or not command:
            return {**base, "reason": "executable_missing"}
        available = shutil.which(command) is not None
        return {**base, "available": available,
                "reason": None if available else "executable_missing"}
    if backend == "ollama":
        from gapengine import ollama
        probe = ollama.availability({**config, "model": model})
        # Allowlisted facts only: the probe's raw error string is dropped.
        return {**base, "available": bool(probe["available"]),
                "authentication": "not_required", "reason": probe["reason"]}
    if backend == "llama-server":
        from gapengine import llama_server
        probe = llama_server.availability({**config, "model": model})
        if probe["reason"] == "server_unreachable" and _auto_launchable(settings, config, model):
            # The GPU guard starts the server itself when generation runs, so a
            # stopped server is not a reason to refuse the request -- the same
            # way a CLI backend is available when its executable exists.
            return {**base, "available": True, "authentication": "not_required",
                    "reason": None, "startup": "auto_launch"}
        # Allowlisted facts only: the probe's raw error string is dropped.
        return {**base, "available": bool(probe["available"]),
                "authentication": "not_required", "reason": probe["reason"]}
    key = config.get("api_key")
    available = isinstance(key, str) and bool(key.strip())
    return {**base, "available": available,
            "reason": None if available else "credentials_missing"}


def _capture_inputs(repo, spec, runs=None):
    """Read a closed YAML set once; rebase only World's external graph reference."""
    blobs, records = {}, {}
    def add(path):
        relative = path.relative_to(repo).as_posix()
        path = contained(repo, relative)
        if not any(path.is_relative_to(repo / area) for area in ("projects", "templates")):
            raise ConfigError("inputs", "入力参照はprojects/templates内に限定します")
        if path.suffix not in (".yaml", ".yml"):
            raise ConfigError("inputs", "入力参照はYAMLファイルに限定します")
        data = path.read_bytes()
        blobs[relative] = data
        records[relative] = {"path": relative, "source_path": str(path),
                             "source_sha256": sha256(data), "source_bytes": len(data)}
        return relative
    project = contained(repo, "projects/" + spec["project_id"])
    template = contained(repo, "templates/" + spec["template_id"])
    if not project.is_dir() or not template.is_dir():
        raise ConfigError("inputs", "世界またはテンプレートがありません")
    world_key = add(project / "world.yaml")
    subjects = sorted((project / "subjects").glob("*.yaml"))
    if not subjects:
        raise ConfigError("inputs.subjects", "登場人物の入力がありません")
    for p in subjects:
        add(p)
    for p in sorted(template.rglob("*")):
        contained(repo, p.relative_to(repo).as_posix())
        if LIBRARY_DIR in p.relative_to(template).parts:
            continue
        if p.is_file() and p.suffix in (".yaml", ".yml"):
            add(p)
    try:
        world = yaml.safe_load(blobs[world_key])
        _object(world, "inputs.world")
        for field in ("action_graph", "effects"):
            graph = (world.get("gapengine") or {}).get(field)
            if graph is None:
                continue
            if not isinstance(graph, str) or not graph:
                raise ConfigError("inputs.action_graph", "行動グラフの参照が不正です")
            raw = Path(graph)
            candidates = [raw] if raw.is_absolute() else [project / raw, repo / raw]
            resolved = None
            for candidate in candidates:
                target = candidate.absolute()
                # A source reference may contain ..; resolve it, then enforce the root.
                for parent in (target, *target.parents):
                    if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
                        raise ConfigError("inputs.action_graph", "リンク参照は使用できません")
                target = target.resolve()
                if not target.is_relative_to(repo):
                    raise ConfigError("inputs.action_graph", "入力参照が範囲外です")
                if target.is_file():
                    resolved = target
                    break
            if resolved is None:
                raise ConfigError("inputs.action_graph", "参照された行動グラフがありません")
            key = add(resolved)
            relative = os.path.relpath(repo / key, project).replace(os.sep, "/")
            if graph != relative:
                world["gapengine"][field] = relative
                blobs[world_key] = yaml.safe_dump(world, allow_unicode=True,
                                                  sort_keys=False).encode("utf-8")
                records[world_key]["transformation"] = "rebase_world_references_to_frozen_relative_paths"
    except (yaml.YAMLError, AttributeError, TypeError) as error:
        raise ConfigError("inputs.world", "世界の入力形式が不正です") from error
    if spec["evolution"]["world_expansion"] == "expand":
        from execution.world_patches import expanded_snapshot
        try:
            world, expanded_people, verified = expanded_snapshot(project, template, repo_root=repo)
        except PatchError as error:
            raise ConfigError("inputs.world_patches", str(error)) from error
        if verified:
            # Preserve the already rebased references captured above.
            rebased = yaml.safe_load(blobs[world_key]).get("gapengine")
            if rebased is not None:
                world["gapengine"] = rebased
            for p in subjects:
                key = p.relative_to(repo).as_posix()
                if yaml.safe_load(blobs[key]) != expanded_people[p.name]:
                    blobs[key] = yaml.safe_dump(expanded_people[p.name], allow_unicode=True,
                                               sort_keys=False).encode("utf-8")
                    records[key]["transformation"] = "world_patches_extend_range"
            blobs[world_key] = yaml.safe_dump(world, allow_unicode=True,
                                              sort_keys=False).encode("utf-8")
            patch_records = [{"id": patch["id"], "sha256": sha256(raw)} for patch, raw in verified]
            records[world_key]["world_patches"] = patch_records
    rid = spec["evolution"].get("seed_genomes")
    if rid is not None:
        if runs is None:
            raise ConfigError("evolution.seed_genomes", "引き継ぎ元の実行を確認できません")
        run_dir = contained(runs, rid)
        if not run_dir.is_dir():
            raise ConfigError("evolution.seed_genomes", "引き継ぎ元の実行が見つかりません", code="not_found")
        try:
            run_config = read_json(contained(run_dir, "config.json"))
        except (OSError, ValueError) as error:
            raise ConfigError("evolution.seed_genomes", "引き継ぎ元の実行の設定を読み取れません", code="not_found") from error
        if (run_config.get("project_id") != spec["project_id"]
                or run_config.get("template_id") != spec["template_id"]):
            raise ConfigError("evolution.seed_genomes", "引き継ぎ元は同じ世界・同じジャンルの実験を選んでください")
        # Opus review R2: a ConfigStore-managed run always has BOTH a
        # top-level archive.json (a mutable, display-only copy that every
        # generation simply overwrites -- see gapengine/evolve.py) and, once
        # at least one generation is published, an immutable, sha-verified
        # published/<rev>/archive.json. load_seed_archive() (gapengine.
        # world_patch_usage.load_archive) prefers the top-level file when
        # present, which here would let a tampered/stale top-level file pass
        # through unverified and would freeze a still-running run's
        # not-yet-sealed state. Prefer published/current.json's revision
        # whenever it exists; fall back to load_seed_archive() (top-level,
        # or a legacy run with no published/ at all) only when it doesn't.
        published_pointer = contained(run_dir, "published/current.json")
        if published_pointer.is_file():
            try:
                pointer = read_json(published_pointer)
                revision = pointer["revision"]
                raw_archive = contained(run_dir, f"published/{revision}/archive.json").read_bytes()
                publication = read_json(contained(run_dir, f"published/{revision}/manifest.json"))
                expected_sha256 = publication["files"]["archive"]["sha256"]
            except (OSError, ValueError, KeyError, TypeError) as error:
                raise ConfigError("evolution.seed_genomes", "引き継ぎ元の公開情報を読み取れません", code="not_found") from error
            if sha256(raw_archive) != expected_sha256:
                raise ConfigError("evolution.seed_genomes", "引き継ぎ元のアーカイブが変更されています", code="snapshot_changed")
            try:
                archive = json.loads(raw_archive)
            except ValueError as error:
                raise ConfigError("evolution.seed_genomes", "引き継ぎ元のアーカイブが不正です") from error
            source = f"published/{revision}"
        else:
            try:
                archive, raw_archive, source = load_seed_archive(run_dir)
            except (OSError, ValueError) as error:
                raise ConfigError("evolution.seed_genomes", "引き継ぎ元のアーカイブが見つかりません", code="not_found") from error
            revision = None
        try:
            seed_entries = seed_genomes_from_archive(archive)
        except ValueError as error:
            raise ConfigError("evolution.seed_genomes", "引き継ぎ元のアーカイブが不正です") from error
        if not seed_entries:
            raise ConfigError("evolution.seed_genomes", "引き継ぎ元のアーカイブにデータがありません", code="empty_seed_archive")
        for seed_entry in seed_entries:
            try:
                Genome.from_dict(seed_entry["genome"])
            except (ValueError, TypeError, KeyError) as error:
                raise ConfigError("evolution.seed_genomes", "引き継ぎ元の遺伝子が不正です") from error
        seed_doc = {"schema_version": 1,
                     "source": {"run_id": rid, "config_id": run_config.get("config_id"),
                                "project_id": run_config["project_id"], "template_id": run_config["template_id"],
                                "archive_path": source, "archive_sha256": sha256(raw_archive),
                                "revision": revision, "captured_at": _now()},
                     "genomes": seed_entries}
        key = "seed_genomes.json"
        data = canonical(seed_doc)
        blobs[key] = data
        records[key] = {"path": key, "source_path": str(run_dir), "source_sha256": sha256(raw_archive),
                        "source_bytes": len(raw_archive), "transformation": "seed_genomes_from_archive"}
    entries = [{**records[p], "sha256": sha256(b), "bytes": len(b)}
               for p, b in sorted(blobs.items())]
    return blobs, {"schema_version": 1, "files": entries}


def _describe(root, spec):
    project = root / "projects" / spec["project_id"]
    template = root / "templates" / spec["template_id"]
    graph = template / "action_graph.yaml"
    world = World.from_yaml(project / "world.yaml",
                            action_graph_path=graph if graph.is_file() else None)
    if spec["evolution"]["target_ending"] is not None:
        world.set_target_ending(spec["evolution"]["target_ending"])
    people = [Subject.from_yaml(p) for p in sorted((project / "subjects").glob("*.yaml"))]
    if len({p.id for p in people}) != len(people):
        raise ConfigError("inputs.subjects", "人物IDが重複しています")
    subjects = {p.id: p for p in people}
    if world.protagonist not in subjects or world.antagonist not in subjects:
        raise ConfigError("inputs.subjects", "主人公または敵役の人物入力がありません")
    Simulation(spec["evolution"]["seed_base"], world, subjects, root / ".unused")
    defaults = {"action_graph.yaml": {"nodes": [], "edges": []},
                "qd.yaml": {"categories": ["I", "II", "III", "IV", "V", "VI"],
                            "volatility_bins": ["low", "mid", "high"]},
                "rules.yaml": [], "effects.yaml": []}
    effective, fallback = {}, {}
    for name, default in defaults.items():
        path = template / name
        raw = yaml.safe_load(path.read_bytes()) if path.is_file() else None
        value = deepcopy(default) if raw is None else raw
        if not isinstance(value, type(default)):
            raise ConfigError("inputs." + name, "テンプレートの形式が不正です")
        effective[name] = value
        if raw is None:
            fallback[name] = {"reason": "missing" if not path.exists() else "empty",
                              "effective": deepcopy(default)}
    try:
        _rule_ids(effective["rules.yaml"])
        _compile_rules(effective["rules.yaml"])
    except (ValueError, KeyError, TypeError) as error:
        raise ConfigError("inputs.rules.yaml", "ルールのID・対象範囲・条件式・調整値を確認してください") from error
    load_canon(template / "canon.yaml")
    for name in ("canon.yaml", "action_graph.antagonist.yaml", "canon.antagonist.yaml"):
        if not (template / name).is_file():
            fallback[name] = {"reason": "missing", "effective":
                              effective["action_graph.yaml"] if name.startswith("action") else {}}
        elif name.startswith("canon"):
            load_canon(template / name)
        elif not isinstance(_load_yaml(template / name, effective["action_graph.yaml"]), dict):
            raise ConfigError("inputs." + name, "テンプレートの形式が不正です")
    qd = effective["qd.yaml"]
    for key in ("categories", "volatility_bins"):
        if key in qd and (not isinstance(qd[key], list) or not qd[key]
                or any(not isinstance(x, str) or not x for x in qd[key])
                or len(set(qd[key])) != len(qd[key])):
            raise ConfigError("inputs.qd", "QD軸が不正です")
    ev = spec["evolution"]
    return {"world_name": world.name, "protagonist": world.protagonist,
            "antagonist": world.antagonist,
            "subjects": [yaml.safe_load(p.read_bytes()) for p in sorted((project / "subjects").glob("*.yaml"))],
            "world": yaml.safe_load((project / "world.yaml").read_bytes()),
            "target_endings": [e["id"] for e in world.target_endings()],
            "max_turns": world.days * len(world.slots), "qd": qd,
            "fallbacks": fallback,
            "planned_individual_evaluations": ev["generations"] * ev["population"] * (2 if ev["coevolve"] else 1),
            "planned_seed_evaluations": ev["generations"] * ev["population"] * ev["seeds"] * (2 if ev["coevolve"] else 1),
            "seed_range": {"first": ev["seed_base"], "count": ev["seeds"]},
            "fixed_parameters": {
                "source": "gapengine.genome.Genome.mutate",
                "mutation_probability": inspect.signature(Genome.mutate).parameters["p"].default,
                "rule_mutation_probability": inspect.signature(Genome.mutate).parameters["rule_p"].default,
                "editable": False}}


class ConfigStore:
    def __init__(self, repo_root, control_root, runs_root):
        self.repo = Path(repo_root).absolute()
        self.control = Path(control_root).absolute()
        self.runs = Path(runs_root).absolute()
        for root in (self.repo, self.control, self.runs):
            contained(root, ".boundary")
        for target in (self.control, self.runs):
            if target == self.repo or target.is_relative_to(self.repo):
                raise ConfigError("storage", "実行データはリポジトリ外へ保存してください")
        if self.control == self.runs or self.control.is_relative_to(self.runs) or self.runs.is_relative_to(self.control):
            raise ConfigError("storage", "管理領域と実行領域を分離してください")

    def _prepare(self, spec, parent=None):
        spec = normalize(spec)
        if parent is None:
            rid = spec["evolution"].get("seed_genomes")
            if rid is not None:
                try:
                    self.verify_run(rid)
                except FileNotFoundError as error:
                    raise ConfigError("evolution.seed_genomes", "引き継ぎ元の実行が見つかりません", code="not_found") from error
                except ConfigError:
                    raise
                except (ValueError, KeyError) as error:
                    # verify_run() reads complete.json/manifest.json straight
                    # off disk (json.JSONDecodeError is a ValueError; a
                    # truncated/empty seal or manifest KeyErrors on its own
                    # required fields) -- a malformed run must surface as a
                    # normal field error here, not an uncaught 500.
                    raise ConfigError("evolution.seed_genomes", "引き継ぎ元の実行の記録が壊れています", code="not_found") from error
            try:
                blobs, manifest = _capture_inputs(self.repo, spec, runs=self.runs)
            except FileNotFoundError as error:
                raise ConfigError("inputs", "必要な入力ファイルがありません") from error
        else:
            prior, manifest, root = self._bundle(parent)
            if any(prior[k] != spec[k] for k in ("project_id", "template_id")):
                raise ConfigError("parent_config_id", "複製元と異なる入力は新規設定として保存してください")
            prior_expand = prior["evolution"].get("world_expansion", "off") == "expand"
            spec_expand = spec["evolution"]["world_expansion"] == "expand"
            if prior_expand != spec_expand:
                raise ConfigError("parent_config_id", "複製元と異なる入力は新規設定として保存してください")
            if prior["evolution"].get("seed_genomes") != spec["evolution"].get("seed_genomes"):
                raise ConfigError("parent_config_id", "複製元と異なる入力は新規設定として保存してください")
            blobs = {r["path"]: contained(root / "inputs", r["path"]).read_bytes()
                     for r in manifest["files"]}
        # Opus review: a genre switch (client-side, before submit) must not
        # silently carry another genre's kappa along -- if this template has
        # no rationality.yaml at all, kappa can never mean anything for it,
        # so force it off here rather than trust the client to have cleared
        # the field. Checked against the just-captured blobs (this save's own
        # frozen snapshot), not a fresh disk read, so it can't race a
        # concurrent repo edit.
        if spec["evolution"]["kappa"] is not None:
            key = f"templates/{spec['template_id']}/rationality.yaml"
            if key not in blobs:
                spec["evolution"]["kappa"] = None
        # WB-ROUTE-001 S4 §2: same guard for route_rho -- a genre switch
        # (client-side, before submit) to a template with no route.yaml must
        # not carry another genre's rho along (gapengine.evolve._route_cfg
        # raises on rho>0 with no route.yaml, which would fail the whole GA
        # run rather than just being ignored).
        if spec["evolution"]["route_rho"] is not None:
            key = f"templates/{spec['template_id']}/route.yaml"
            if key not in blobs:
                spec["evolution"]["route_rho"] = None
        with tempfile.TemporaryDirectory(prefix="wb-config-preview-") as temp:
            materialize(Path(temp), blobs)
            try:
                description = _describe(Path(temp), spec)
            except ConfigError:
                raise
            except (ValueError, KeyError, TypeError, OSError, yaml.YAMLError) as error:
                raise ConfigError("inputs", "世界・人物・テンプレートまたは結末の検証に失敗しました") from error
        return spec, blobs, manifest, description

    def preview(self, spec):
        spec, _, manifest, description = self._prepare(spec)
        return {"schema_version": 1, **spec, "preview": description,
                "input_manifest_sha256": sha256(canonical(manifest))}

    def save(self, spec, *, config_id=None, parent_config_id=None):
        cid = identifier(("cfg-" + uuid.uuid4().hex) if config_id is None else config_id, "config_id")
        configs = self.control / "configs"
        contained(configs, cid)
        spec, blobs, manifest, preview = self._prepare(spec, parent_config_id)
        document = {"schema_version": 1, "config_id": cid,
                    "parent_config_id": parent_config_id, "created_at": _now(),
                    **spec, "preview": preview,
                    "input_manifest_sha256": sha256(canonical(manifest))}
        with directory_lock(configs):
            final = contained(configs, cid)
            if final.exists():
                raise ConfigError("config_id", "設定版が既にあります", code="conflict")
            staging = contained(configs, ".pending-" + uuid.uuid4().hex)
            materialize(staging / "inputs", blobs)
            atomic_json(staging / "input-manifest.json", manifest)
            atomic_json(staging / "config.json", document)
            atomic_json(staging / "complete.json", {"schema_version": 1,
                        "config_sha256": sha256(canonical(document)),
                        "input_manifest_sha256": sha256(canonical(manifest))})
            publish_directory(staging, final)
        return deepcopy(document)

    def _bundle(self, config_id):
        root = contained(self.control / "configs", identifier(config_id, "config_id"))
        seal = read_json(contained(root, "complete.json"))
        config_raw = (contained(root, "config.json")).read_bytes()
        manifest_raw = (contained(root, "input-manifest.json")).read_bytes()
        if (sha256(config_raw) != seal["config_sha256"] or
                sha256(manifest_raw) != seal["input_manifest_sha256"]):
            raise ConfigError("snapshot", "設定版の整合性が失われています", code="snapshot_changed")
        config, manifest = read_json(contained(root, "config.json")), read_json(contained(root, "input-manifest.json"))
        verify_files(root / "inputs", manifest["files"])
        actual = {p.relative_to(root / "inputs").as_posix()
                  for p in (root / "inputs").rglob("*") if p.is_file()}
        if actual != {r["path"] for r in manifest["files"]}:
            raise ConfigError("snapshot", "入力ファイル集合が変更されています", code="snapshot_changed")
        return config, manifest, root

    def get(self, config_id):
        return deepcopy(self._bundle(config_id)[0])

    def list(self):
        folder = self.control / "configs"
        if not folder.exists():
            return []
        return [self.get(p.name) for p in sorted(folder.iterdir())
                if p.is_dir() and not p.name.startswith(".")]

    def duplicate(self, config_id, changes=None, *, new_id=None):
        original = self.get(config_id)
        spec = {k: original[k] for k in ("label", "project_id", "template_id",
                "evolution", "execution_limits")}
        # R5 (Opus review, WB-WORLDGROW-001 段階5c): a growth-enabled config
        # used to lose its growth on duplicate (spec never carried it over)
        # -- carry it forward like every other field; `changes` may still
        # replace or clear it below.
        if "growth" in original:
            spec["growth"] = original["growth"]
        changes = changes or {}
        for key, value in changes.items():
            if (key in ("evolution", "execution_limits", "growth")
                    and isinstance(value, dict) and isinstance(spec.get(key), dict)):
                spec[key] = {**spec[key], **value}
            else:
                spec[key] = value
        # Unlike project_id/template_id (hidden, fixed fields on the "duplicate to
        # edit" form), evolution.world_expansion is an editable radio there. Toggling
        # to/from "expand" makes the frozen inputs incompatible with the parent
        # config (_prepare's parent_config_id check) -- save as a fresh config
        # instead of dead-ending the save, the same way changing project_id would
        # require a fresh (non-duplicate) config if it were reachable from the UI.
        prior_expand = original["evolution"].get("world_expansion") == "expand"
        # R5: growth.mode != off forces world_expansion to "expand" at
        # normalize() time regardless of what spec["evolution"] itself says
        # -- judge the parent-compatibility check by that *effective* value,
        # not the pre-normalize one, or turning growth on (or leaving it on
        # while flipping world_expansion off in the same call) would wrongly
        # keep a parent whose frozen inputs are about to change out from
        # under it (normalize() would then dead-end the save instead).
        spec_growth_mode = (spec.get("growth") or {}).get("mode", "off")
        spec_expand = (spec.get("evolution") or {}).get("world_expansion") == "expand" or spec_growth_mode != "off"
        # WB-WORLDGROW-001 段階5b: same treatment for seed_genomes -- changing
        # which run's archive seeds generation 0 makes the frozen inputs
        # incompatible with the parent config's own frozen seed_genomes.json.
        prior_seed = original["evolution"].get("seed_genomes")
        spec_seed = spec["evolution"].get("seed_genomes")
        parent = config_id if prior_expand == spec_expand and prior_seed == spec_seed else None
        return self.save(spec, config_id=new_id, parent_config_id=parent)

    def prepare_run(self, config_id, *, run_id=None, job_id, processes=None):
        rid = identifier(("run-" + uuid.uuid4().hex) if run_id is None else run_id, "run_id")
        identifier(job_id, "job_id")
        config, inputs, source = self._bundle(config_id)
        final = contained(self.runs, rid)
        with directory_lock(self.runs):
            if final.exists():
                raise ConfigError("run_id", "実行先が既にあります", code="conflict")
            code, runtime = code_snapshot(self.repo)
            staging = contained(self.runs, ".pending-" + uuid.uuid4().hex)
            blobs = {r["path"]: contained(source / "inputs", r["path"]).read_bytes()
                     for r in inputs["files"]}
            materialize(staging / "inputs", blobs)
            verify_files(staging / "inputs", inputs["files"])
            materialize(staging / "runtime", code)
            verify_files(staging / "runtime", runtime["files"])
            argv = [python_executable(), "-I", "-B", str(final / "runtime/scripts/evolve.py"),
                    "--project", str(final / "inputs/projects" / config["project_id"]),
                    "--template", str(final / "inputs/templates" / config["template_id"]),
                    "--out", str(final)]
            for key, value in config["evolution"].items():
                flag = "--" + key.replace("_", "-")
                if key == "record_explanations":
                    argv.append(flag if value else "--no-record-explanations")
                elif key == "seed_genomes":
                    # WB-WORLDGROW-001 段階5b: config["evolution"]["seed_genomes"]
                    # is the *source run's* run_id (normalize()'s identifier
                    # check); the frozen inputs/seed_genomes.json this run's own
                    # snapshot carries is what scripts/evolve.py actually reads.
                    if value is not None:
                        argv.append(flag)
                        argv.append(str(final / "inputs/seed_genomes.json"))
                elif type(value) is bool:
                    if value:
                        argv.append(flag)
                elif value is not None:
                    argv.append(flag)
                    argv.extend(value if isinstance(value, list) else [str(value)])
            if processes is not None:
                # PC-side operational setting (WB-COMPUTE-001), not part of what the
                # config means: overrides the frozen evolution.processes value in the
                # argv only -- config["evolution"]/config_sha256 stay untouched.
                argv[argv.index("--processes") + 1] = str(processes)
            # .get(), not [...]: a config.json saved before WB-JEV-002 added
            # these keys to evolution_defaults() has none of them at all, and
            # this manifest-derived "evolution" dict is read straight off
            # disk here (not renormalized), unlike normalize()'s own inputs.
            kappa = config["evolution"].get("kappa")
            rationality_table = None
            if kappa is not None:
                # WB-JEV-002: the shared table lives under this store's own
                # control root, one file per (template, model, method) so
                # unrelated genres/backends never collide or overwrite each
                # other's accumulated judgments. rationality_table itself is
                # never accepted from the API (normalize(), above) -- this is
                # the only place a --rationality-table flag is ever built.
                # Read from the frozen inputs just materialized above (not
                # self.repo): a live repo edit after this config was saved
                # must never change which table a prepared run points at,
                # same guarantee as --project/--template/--out above.
                rationality_yaml, rationality_yaml_backend, rationality_override = (
                    _rationality_backend_cfg(
                        {"rationality": {
                            "method": config["evolution"].get("rationality_method"),
                            "model": config["evolution"].get("rationality_model"),
                        }},
                        staging / "inputs/templates" / config["template_id"]))
                method = rationality_override.get("method") or rationality_yaml.get("method", "noul")
                model = (rationality_override.get("model")
                         or rationality_yaml_backend.get("model", RATIONALITY_DEFAULT_MODEL))
                table_path = contained(self.control, "rationality/{}.{}.{}.json".format(
                    config["template_id"], _safe_path_token(model), _safe_path_token(method)))
                table_path.parent.mkdir(parents=True, exist_ok=True)
                argv.extend(["--rationality-table", str(table_path)])
                rationality_table = str(table_path)
            manifest = {"schema_version": 1, "run_id": rid, "job_id": job_id,
                        "config_id": config_id, "created_at": _now(),
                        "input_manifest_sha256": config["input_manifest_sha256"],
                        "config_sha256": sha256(canonical(config)),
                        "runtime_manifest_sha256": sha256(canonical(runtime)),
                        "argv": argv, "evolution": config["evolution"],
                        "processes": processes if processes is not None else config["evolution"]["processes"],
                        "target_endings": config["preview"]["target_endings"],
                        "execution_limits": config["execution_limits"],
                        # WB-ROUTE-001 S4 follow-up: --rationality-table above is
                        # the only place this run's shared judgment-table path is
                        # computed, but it used to live only in argv, which the
                        # frozen adapter (execution/evolution_worker.py) never
                        # reads (it rebuilds cfg from this manifest's "evolution"
                        # dict instead of parsing argv). Recording it here too
                        # lets both the CLI (legacy_evolve_cli) and adapter
                        # (evolution_worker) launch paths land on the same table
                        # file. None (kappa<=0, or a manifest from before this
                        # field existed) means "no persisted table" -- worker
                        # falls back to evolve()'s own <out>/rationality.json
                        # default, same as the CLI path always has.
                        "rationality_table": rationality_table,
                        "status": "prepared"}
            atomic_json(staging / "config.json", config)
            atomic_json(staging / "input-manifest.json", inputs)
            atomic_json(staging / "runtime-manifest.json", runtime)
            atomic_json(staging / "manifest.json", manifest)
            atomic_json(staging / "complete.json", {"schema_version": 1,
                        "manifest_sha256": sha256(canonical(manifest))})
            publish_directory(staging, final)
        return deepcopy(manifest)

    def verify_run(self, run_id):
        root = contained(self.runs, identifier(run_id, "run_id"))
        seal = read_json(contained(root, "complete.json"))
        manifest_raw = (contained(root, "manifest.json")).read_bytes()
        if sha256(manifest_raw) != seal["manifest_sha256"]:
            raise ConfigError("snapshot", "実行manifestが変更されています", code="snapshot_changed")
        manifest = read_json(contained(root, "manifest.json"))
        if sha256(contained(root, "config.json").read_bytes()) != manifest["config_sha256"]:
            raise ConfigError("snapshot", "実行設定が変更されています", code="snapshot_changed")
        for name in ("input", "runtime"):
            raw = (contained(root, name + "-manifest.json")).read_bytes()
            if sha256(raw) != manifest[name + "_manifest_sha256"]:
                raise ConfigError("snapshot", "来歴manifestが変更されています", code="snapshot_changed")
            records = read_json(contained(root, name + "-manifest.json"))["files"]
            verify_files(root / ("inputs" if name == "input" else "runtime"), records)
        return manifest

    @staticmethod
    def legacy_settings(recorded_summary):
        # Never backfill absent historic facts from current project defaults.
        return {"schema_version": 1, "provenance": "legacy_recorded_only",
                "evolution": {k: deepcopy(recorded_summary.get(k))
                              for k in evolution_defaults()},
                "input_manifest_sha256": None}
