"""Strict wire contract shared by CI packaging and the local updater."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import stat
import unicodedata

from .errors import DeliveryError

REPOSITORY = "ATO-LABO/WorldBloom"
BRANCH = "develop"
WORKFLOW_PATH = ".github/workflows/tests.yml"
CODEQL_PATH = ".github/workflows/codeql.yml"
PLATFORM = "windows-x64"
DATA_ADAPTER = "world-editor-snapshot-v1"
MANIFEST_NAME = "manifest.json"
MAX_ARCHIVE = 256 * 1024 * 1024
MAX_EXPANDED = 512 * 1024 * 1024
MAX_FILE = 128 * 1024 * 1024
MAX_FILES = 10000
MAX_MANIFEST = 4 * 1024 * 1024
RUNTIME_DIRS = ("viewer", "execution", "engine", "gapengine", "scripts", "wb_delivery")
ROOT_FILES = {"WorldBloom-Studio.exe", "app/VERSION.txt", "app/PACKAGE-MANIFEST.json", "app/requirements.txt"}
REQUIRED_FILES = {"WorldBloom-Studio.exe", "app/viewer/app_desktop.py", "app/viewer/server.py", "app/execution/world_editor.py", "app/engine/world.py", "app/gapengine/__init__.py", "app/VERSION.txt"}
REQUIRED_FILES |= {"app/wb_delivery/" + name for name in ("__init__.py", "runtime_guard.py", "lease.py", "errors.py", "contract.py")}
PROTECTED = ("runs", "control", "app/settings.json", "app/projects", "app/templates", "app/samples")
SHA40 = re.compile(r"[a-f0-9]{40}\Z")
SHA64 = re.compile(r"[a-f0-9]{64}\Z")
RESERVED = {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def valid_sha(value, length=40):
    return isinstance(value, str) and bool((SHA40 if length == 40 else SHA64).fullmatch(value))


def positive(value):
    return type(value) is int and value > 0


def artifact_name(source_sha, run_id, run_attempt):
    if not valid_sha(source_sha) or not positive(run_id) or not positive(run_attempt):
        raise DeliveryError("invalid_artifact_identity")
    return f"WorldBloom-Studio-win-x64-{source_sha}-r{run_id}-a{run_attempt}"


def strict_json(blob):
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise DeliveryError("duplicate_json_key")
            result[k] = v
        return result
    try:
        return json.loads(blob, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(DeliveryError("invalid_json_number")))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise DeliveryError("invalid_json") from exc


def canonical(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf8")


def normalized_path(rel):
    if not isinstance(rel, str) or not rel or rel != unicodedata.normalize("NFC", rel):
        raise DeliveryError("unsafe_path")
    if "\\" in rel or rel.startswith("/") or any(ord(c) < 32 or ord(c) == 127 for c in rel):
        raise DeliveryError("unsafe_path")
    parts = rel.split("/")
    for part in parts:
        if not part or part in (".", "..") or part.endswith((" ", ".")) or any(c in part for c in '<>:"|?*'):
            raise DeliveryError("unsafe_path")
        if part.split(".")[0].upper() in RESERVED:
            raise DeliveryError("unsafe_path")
    return rel


def managed_path(rel):
    normalized_path(rel)
    parts = rel.split("/")
    if any(p.casefold() in {"__pycache__", ".git", ".env"} for p in parts) or rel.endswith((".pyc", ".pyo")):
        raise DeliveryError("unmanaged_path")
    if rel in ROOT_FILES or (len(parts) >= 3 and parts[0] == "app" and parts[1] in RUNTIME_DIRS):
        return rel
    raise DeliveryError("protected_or_unmanaged_path")


def is_link(path):
    try:
        info = path.lstat()
        return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)
    except FileNotFoundError:
        return False


def safe_child(root, rel, *, managed=True):
    (managed_path if managed else normalized_path)(rel)
    root = Path(root).absolute()
    if any(is_link(p) for p in [root, *root.parents]):
        raise DeliveryError("linked_root")
    child = root.joinpath(*rel.split("/"))
    cursor = root
    for part in rel.split("/"):
        if cursor.exists():
            for existing in cursor.iterdir():
                if existing.name.casefold() == part.casefold() and existing.name != part:
                    raise DeliveryError("case_alias")
        cursor = cursor / part
        if is_link(cursor):
            raise DeliveryError("linked_path")
    if not child.resolve().is_relative_to(root.resolve()):
        raise DeliveryError("path_escape")
    return child


@dataclass(frozen=True)
class ReleaseEvidence:
    source_sha: str
    run_id: int
    run_attempt: int
    workflow_id: int
    merged_pr: int
    artifact_id: int
    artifact_digest: str
    artifact_size: int
    repository_id: int
    codeql_verified: bool = True

    def __post_init__(self):
        if not valid_sha(self.source_sha) or not valid_sha(self.artifact_digest, 64):
            raise DeliveryError("invalid_evidence_sha")
        if not all(positive(getattr(self, k)) for k in ("run_id", "run_attempt", "workflow_id", "merged_pr", "artifact_id", "repository_id", "artifact_size")):
            raise DeliveryError("invalid_evidence_id")
        if self.artifact_size > MAX_ARCHIVE or self.codeql_verified is not True:
            raise DeliveryError("unverified_evidence")


def validate_manifest(value, evidence=None):
    if not isinstance(value, dict) or set(value) != {"schema_version", "repository", "branch", "source_sha", "platform", "build", "data", "files"}:
        raise DeliveryError("manifest_shape")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise DeliveryError("manifest_version")
    if value["repository"] != REPOSITORY or value["branch"] != BRANCH or value["platform"] != PLATFORM or not valid_sha(value["source_sha"]):
        raise DeliveryError("manifest_source")
    build = value["build"]
    if not isinstance(build, dict) or set(build) != {"workflow_id", "workflow_path", "run_id", "run_attempt", "merged_pr"} or build["workflow_path"] != WORKFLOW_PATH:
        raise DeliveryError("manifest_build")
    if not all(positive(build[k]) for k in ("workflow_id", "run_id", "run_attempt", "merged_pr")):
        raise DeliveryError("manifest_build")
    if value["data"] != {"adapter": DATA_ADAPTER, "requires_migration": False} or type(value["data"].get("requires_migration")) is not bool:
        raise DeliveryError("data_migration_required")
    files = value["files"]
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise DeliveryError("manifest_files")
    seen, size = set(), 0
    for f in files:
        if not isinstance(f, dict) or set(f) != {"path", "size", "sha256"}:
            raise DeliveryError("manifest_file")
        rel = managed_path(f["path"])
        if rel.casefold() in seen or type(f["size"]) is not int or not 0 <= f["size"] <= MAX_FILE or not valid_sha(f["sha256"], 64):
            raise DeliveryError("manifest_file")
        seen.add(rel.casefold())
        size += f["size"]
    if size > MAX_EXPANDED or not REQUIRED_FILES.issubset({f["path"] for f in files}):
        raise DeliveryError("incomplete_bundle")
    if evidence is not None:
        if value["source_sha"] != evidence.source_sha or any(build[k] != getattr(evidence, k) for k in ("workflow_id", "run_id", "run_attempt", "merged_pr")):
            raise DeliveryError("manifest_evidence_mismatch")
    return value
