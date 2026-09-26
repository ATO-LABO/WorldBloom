"""Validate world-expansion patch job requests (propose/check) and freeze
their argv for the worker (execution/worker.py). Mirrors
execution/output_requests.py's normalize/admit/prepare split (WB-WORLDGROW-001
stage 3b-3): admission runs under the job-table lock; the actual propose/check
work runs later, in the job's owned child process, as scripts/world_patch.py.
"""
from __future__ import annotations

import json

import yaml

from execution.configs import generation_availability
from execution.output_settings import resolve_generation
from execution.provenance import (
    ConfigError, canonical, code_snapshot, contained, identifier, python_executable, sha256)
from execution.world_patches import _check_parent_rev
from gapengine.world_patch import ID_RE, trigger_is_proposable

PROPOSE_FIELDS = {"schema_version", "request_id", "kind", "config_id", "run_id", "action", "trigger"}
CHECK_FIELDS = {"schema_version", "request_id", "kind", "config_id", "run_id", "action", "patch_id"}

# ponytail: empty means "use scripts/world_patch.py's own --max-runs/
# --seeds-per-run defaults (5x8)"; a test's cloned repo overrides this tuple
# (via its own copy of this module) to shrink the trial so tests stay fast.
TRIAL_ARGS: tuple[str, ...] = ()

# The output limits in settings.json are tuned for one synopsis (240s for the
# hosted backends). A proposal is up to three generations of up to 900s each
# (scripts/world_patch.py's timeout and --retries) plus two trials, and the
# deadline counts from submission, so never run with less than this.
MIN_WALL_SECONDS = 4500


def _triggers(report_path):
    """world_demand.json's triggers, or a ConfigError. The file sits outside
    the frozen inputs, so it can be hand-edited or half-written; anything but
    ConfigError here would drop the HTTP connection without a response."""
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ConfigError("trigger", "世界の需要を読み取れません") from error
    triggers = report.get("triggers") if isinstance(report, dict) else None
    if not isinstance(triggers, list):
        raise ConfigError("trigger", "世界の需要を読み取れません")
    return triggers


def normalize(request):
    if not isinstance(request, dict):
        raise ConfigError("request", "オブジェクトを指定してください")
    action = request.get("action")
    if action not in ("propose", "check"):
        raise ConfigError("action", "処理種別が不正です")
    expected = PROPOSE_FIELDS if action == "propose" else CHECK_FIELDS
    if set(request) != expected:
        raise ConfigError("request", "未対応の要求項目があります")
    if request.get("schema_version", 1) != 1 or type(request.get("schema_version", 1)) is not int:
        raise ConfigError("schema_version", "未対応の版です")
    if request.get("kind") != "world_patch":
        raise ConfigError("kind", "処理種別が不正です")
    doc = {"schema_version": 1, "kind": "world_patch", "action": action}
    for key in ("request_id", "config_id", "run_id"):
        doc[key] = identifier(request.get(key), key)
    if action == "propose":
        trigger = request.get("trigger")
        if type(trigger) is not int or trigger < 0:
            raise ConfigError("trigger", "きっかけの番号を指定してください")
        doc["trigger"] = trigger
    else:
        patch_id = request.get("patch_id")
        if not isinstance(patch_id, str) or not ID_RE.fullmatch(patch_id):
            raise ConfigError("patch_id", "パッチIDの形式が不正です")
        doc["patch_id"] = patch_id
    return doc


def admit(jobs, request, *, settings_path=None):
    """Everything checked here must still hold when prepare() re-derives the
    same experiment/project/argv later in the owned child (execution/worker.py's
    prepare()) -- admit only rejects early with a clear reason."""
    from viewer.run_catalog import RunCatalog

    configs = jobs.configs
    catalog = RunCatalog(configs.runs, configs.control)
    root, legacy = catalog.resolve(request["run_id"])
    if legacy:
        raise ConfigError("run_id", "凍結入力の無い実験からは提案できません")

    manifest = configs.verify_run(request["run_id"])
    if manifest["config_id"] != request["config_id"]:
        raise ConfigError("config_id", "実行時の設定と一致しません")
    config = configs.get(request["config_id"])

    project = contained(configs.repo, "projects/" + config["project_id"])
    if not project.is_dir():
        raise ConfigError("project_id", "世界がありません", code="not_found")

    if settings_path is None:
        raise ConfigError("backend", "文章生成のバックエンドが未設定です", code="unavailable")
    current = resolve_generation(settings_path)
    if current["backend"] == "none":
        raise ConfigError("backend", "文章生成のバックエンドが未設定です")
    if not generation_availability(current, settings_path)["available"]:
        raise ConfigError("backend", "明示モデル・実行環境・資格情報を確認してください", code="unavailable")

    if request["action"] == "propose":
        report_path = contained(root, "world_demand.json")
        if not report_path.is_file():
            raise ConfigError("trigger", "世界の需要が集計されていません")
        triggers = _triggers(report_path)
        trigger = request["trigger"]
        if not (0 <= trigger < len(triggers)) or not trigger_is_proposable(triggers[trigger]):
            raise ConfigError("trigger", "対応できる需要がありません")
    else:
        patch_path = contained(project, f"patches/_proposed/{request['patch_id']}.yaml")
        if not patch_path.is_file():
            raise ConfigError("patch_id", "提案がありません", code="not_found")

    world_path = contained(root, f"inputs/projects/{config['project_id']}/world.yaml")
    base_world = yaml.safe_load(world_path.read_text(encoding="utf-8"))
    mismatch = _check_parent_rev(base_world, project)
    if mismatch:
        raise ConfigError("run_id", mismatch, code="conflict")

    return {"wall_seconds": max(current["limits"]["wall_seconds"], MIN_WALL_SECONDS)}


def prepare(configs, job, request):
    """Freeze the exact argv scripts/world_patch.py will be launched with."""
    from viewer.run_catalog import RunCatalog

    config = configs.get(request["config_id"])
    catalog = RunCatalog(configs.runs, configs.control)
    root, legacy = catalog.resolve(request["run_id"])
    if legacy:
        raise ConfigError("run_id", "凍結入力の無い実験からは提案できません")
    project = contained(configs.repo, "projects/" + config["project_id"])
    script = configs.repo / "scripts" / "world_patch.py"
    _, runtime = code_snapshot(configs.repo)
    common = ["--repo", str(configs.repo), *TRIAL_ARGS,
              "--control", str(configs.control), "--job", job["job_id"]]

    if request["action"] == "propose":
        triggers = _triggers(contained(root, "world_demand.json"))
        raw = request["trigger"]
        if not (0 <= raw < len(triggers)) or not trigger_is_proposable(triggers[raw]):
            raise ConfigError("trigger", "対応できる需要がありません")
        # WB-WORLDGROW-002 S2: `raw` is forwarded as-is -- scripts/world_patch.py's
        # `propose --trigger` now takes the same raw index into
        # world_demand.json's triggers that this request and the UI's
        # data-trigger already use (no more investigate-only re-numbering).
        argv = [python_executable(), "-I", "-B", str(script),
                "propose", "--experiment", str(root), "--project", str(project),
                "--trigger", str(raw), "--settings", job["settings_path"],
                "--then-holdout", *common]
        phase = "proposing"
    else:
        argv = [python_executable(), "-I", "-B", str(script),
                "check", "--experiment", str(root), "--project", str(project),
                "--patch", request["patch_id"], "--seed-set", "holdout", *common]
        phase = "checking"

    return {"schema_version": 1, "phase": phase,
            "runtime_manifest_sha256": sha256(canonical(runtime)), "argv": argv}
