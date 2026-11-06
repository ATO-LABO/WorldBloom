"""CI-side packaging from immutable Git blobs, with no network activity.

The caller supplies metadata already verified against GitHub's API. This module
does not turn environment variables into trusted merge evidence. A caller can
fabricate a dict or an EXE receipt; provenance therefore depends on the reviewed
CI wrapper calling build_studio and immediately passing its receipt to pack_bundle.
Neither function uploads, triggers CI, or changes the original checkout.
"""
from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import sys

from .contract import (
    REPOSITORY, BRANCH, WORKFLOW_PATH, PLATFORM, DATA_ADAPTER, MANIFEST_NAME,
    RUNTIME_DIRS, MAX_FILE, MAX_EXPANDED, MAX_FILES, MAX_MANIFEST,
    canonical, digest, is_link, managed_path, positive, safe_child,
    valid_sha, validate_manifest,
)
from .errors import DeliveryError


@dataclass(frozen=True)
class VerifiedSource:
    checkout: Path
    source_sha: str
    workflow_id: int
    run_id: int
    run_attempt: int
    merged_pr: int


def _git(root, *args):
    try:
        result = subprocess.run(
            ["git", "--no-replace-objects", "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(root), *args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            timeout=30, shell=False,
            env={**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeliveryError("git_unavailable") from exc
    if result.returncode:
        raise DeliveryError("git_read_failed")
    return result.stdout


def _plain_directory(path):
    p = Path(path).absolute()
    for part in (p, *p.parents):
        if is_link(part):
            raise DeliveryError("linked_source_or_output")
    if not p.is_dir():
        raise DeliveryError("missing_source_directory")
    return p.resolve()


def _metadata(context):
    if not isinstance(context, dict):
        raise DeliveryError("build_context")
    if context.get("repository") != REPOSITORY or context.get("event") != "push" or context.get("ref") != "refs/heads/" + BRANCH:
        raise DeliveryError("not_merged_develop")
    sha = context.get("source_sha")
    if not valid_sha(sha) or context.get("workflow_path") != WORKFLOW_PATH:
        raise DeliveryError("build_context")
    if context.get("ci_policy") != "success" or not all(positive(context.get(k)) for k in ("workflow_id", "run_id", "run_attempt")):
        raise DeliveryError("ci_not_verified")
    pr = context.get("merged_pr")
    if not isinstance(pr, dict) or pr.get("merged") is not True or pr.get("merge_commit_sha") != sha or not positive(pr.get("number")):
        raise DeliveryError("merge_not_verified")
    base = pr.get("base")
    if not isinstance(base, dict) or base.get("ref") != BRANCH or not isinstance(base.get("repo"), dict) or base["repo"].get("full_name") != REPOSITORY:
        raise DeliveryError("merge_not_verified")
    return sha, pr["number"]


def verify_source(checkout, context):
    """Require a verified push/merge context and a completely clean exact SHA.

    Ignored untracked files also cause rejection. Generated build output must be
    outside this checkout. Commit blobs, rather than working-tree bytes, are used
    below so an edit-and-revert race cannot enter a bundle or build snapshot.
    """
    sha, pr = _metadata(context)
    root = _plain_directory(checkout)
    try:
        top = Path(_git(root, "rev-parse", "--show-toplevel").decode("utf8").strip()).resolve()
        head = _git(root, "rev-parse", "HEAD").decode("ascii").strip()
    except (UnicodeError, ValueError) as exc:
        raise DeliveryError("git_metadata") from exc
    if os.path.normcase(str(top)) != os.path.normcase(str(root)) or head != sha:
        raise DeliveryError("source_sha_mismatch")
    if _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all") or _git(root, "ls-files", "--others", "--ignored", "--exclude-standard", "-z"):
        raise DeliveryError("dirty_source")
    return VerifiedSource(root, sha, context["workflow_id"], context["run_id"], context["run_attempt"], pr)


def _source_files(source):
    rows = _git(source.checkout, "ls-tree", "-r", "-z", "--full-tree", source.source_sha)
    result = []
    sensitive = {"settings.json", "credentials.json", "secrets.json", "token.json", "id_rsa", "id_ed25519", ".env"}
    for row in rows.split(b"\0"):
        if not row:
            continue
        try:
            header, raw_path = row.split(b"\t", 1)
            mode, kind, oid = header.decode("ascii").split()
            rel = raw_path.decode("utf8")
        except (ValueError, UnicodeError) as exc:
            raise DeliveryError("source_tree") from exc
        parts = rel.split("/")
        selected = rel == "requirements.txt" or (len(parts) >= 2 and parts[0] in RUNTIME_DIRS)
        if not selected:
            continue
        if "__pycache__" in parts or rel.endswith((".pyc", ".pyo")):
            continue
        if any(p.casefold() in sensitive | {"credentials", "secrets", ".ssh", ".aws", ".git"} or p.casefold().startswith(".env.") for p in parts) or Path(rel).suffix.casefold() in {".pem", ".pfx", ".p12", ".key"}:
            raise DeliveryError("sensitive_source_path")
        if mode not in ("100644", "100755") or kind != "blob" or not valid_sha(oid):
            raise DeliveryError("linked_or_submodule_source")
        managed_path("app/" + rel)
        try:
            p = safe_child(source.checkout, rel, managed=False)
        except OSError as exc:
            raise DeliveryError("source_path") from exc
        if not p.is_file():
            raise DeliveryError("source_path")
        result.append((rel, oid))
    if not result or len(result) + 3 > MAX_FILES:
        raise DeliveryError("source_file_count")
    return sorted(result)


def _read_blob(source, oid):
    try:
        size = int(_git(source.checkout, "cat-file", "-s", oid).decode("ascii").strip())
    except (ValueError, UnicodeError) as exc:
        raise DeliveryError("source_blob_size") from exc
    if not 0 <= size <= MAX_FILE:
        raise DeliveryError("source_file_too_large")
    blob = _git(source.checkout, "cat-file", "blob", oid)
    if len(blob) != size:
        raise DeliveryError("source_blob_size")
    return blob


def _new_output(checkout, output):
    output = Path(output).absolute()
    for part in (output, *output.parents):
        if is_link(part):
            raise DeliveryError("linked_source_or_output")
    if output.exists() or output.resolve().is_relative_to(checkout):
        raise DeliveryError("unsafe_or_existing_output")
    if not output.parent.is_dir():
        raise DeliveryError("missing_output_parent")
    output.mkdir(exist_ok=False)
    return output


def _write_new(root, rel, blob):
    path = safe_child(root, rel, managed=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(blob)
        stream.flush()
        os.fsync(stream.fileno())


def _exe_bytes(exe):
    exe = Path(exe).absolute()
    for part in (exe, *exe.parents):
        if is_link(part):
            raise DeliveryError("linked_executable")
    try:
        if exe.stat().st_size > MAX_FILE:
            raise DeliveryError("executable_too_large")
        blob = exe.read_bytes()
    except OSError as exc:
        raise DeliveryError("missing_executable") from exc
    if not blob.startswith(b"MZ"):
        raise DeliveryError("invalid_executable_header")
    return blob


def build_studio(checkout, output, context, *, run=subprocess.run):
    """Build only from an independent committed snapshot; not invoked on import.

    Real CI must install its reviewed hash-pinned build lock first. Tests inject
    run; they never invoke PyInstaller or run the resulting executable.
    """
    source = verify_source(checkout, context)
    files = _source_files(source)
    out = _new_output(source.checkout, output)
    snapshot = out / "source"
    snapshot.mkdir()
    for rel, oid in files:
        _write_new(snapshot, rel, _read_blob(source, oid))
    script = snapshot / "viewer/app_desktop.py"
    icon = snapshot / "viewer/worldbloom.ico"
    if not script.is_file() or not icon.is_file():
        raise DeliveryError("missing_build_input")
    args = [sys.executable, "-I", "-B", "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed", "--name", "WorldBloom-Studio", "--paths", str(snapshot), "--collect-all", "webview", "--collect-all", "yaml", "--icon", str(icon), "--distpath", str(out / "dist"), "--workpath", str(out / "work"), "--specpath", str(out / "spec"), str(script)]
    environment = {k: v for k, v in os.environ.items() if k.upper() not in
                   {"PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "GH_TOKEN", "GITHUB_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"}}
    environment["PYINSTALLER_CONFIG_DIR"] = str(out / "cache")
    try:
        with (out / "build.log").open("wb") as log:
            result = run(args, cwd=snapshot, stdout=log, stderr=subprocess.STDOUT,
                         shell=False, timeout=1800, env=environment)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeliveryError("build_failed") from exc
    if result.returncode:
        raise DeliveryError("build_failed")
    exe = out / "dist/WorldBloom-Studio.exe"
    blob = _exe_bytes(exe)
    verify_source(source.checkout, context)
    receipt = {"builder": "wb-delivery-build-v1", "source_sha": source.source_sha,
               "head_sha": source.source_sha, "exe_sha256": digest(blob), "exe_size": len(blob),
               "command": args}
    _write_new(out, "build-receipt.json", canonical(receipt))
    return {"exe": str(exe), "receipt": receipt}


def pack_bundle(checkout, exe, output_dir, context):
    source = verify_source(checkout, context)
    files = _source_files(source)
    if Path(exe).resolve().is_relative_to(source.checkout):
        raise DeliveryError("executable_inside_checkout")
    executable = _exe_bytes(exe)
    receipt = context.get("build_receipt")
    if not isinstance(receipt, dict) or receipt.get("builder") != "wb-delivery-build-v1" or receipt.get("source_sha") != source.source_sha or receipt.get("head_sha") != source.source_sha or receipt.get("exe_sha256") != digest(executable) or type(receipt.get("exe_size")) is not int or receipt["exe_size"] != len(executable):
        raise DeliveryError("build_receipt_mismatch")
    out = _new_output(source.checkout, output_dir)
    records, total = [], 0

    def add(rel, blob):
        nonlocal total
        managed_path(rel)
        total += len(blob)
        if total > MAX_EXPANDED:
            raise DeliveryError("bundle_too_large")
        _write_new(out, rel, blob)
        records.append({"path": rel, "size": len(blob), "sha256": digest(blob)})

    for rel, oid in files:
        add("app/" + rel, _read_blob(source, oid))
    add("WorldBloom-Studio.exe", executable)
    commit_date = _git(source.checkout, "show", "-s", "--format=%cs", source.source_sha).decode("ascii").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", commit_date):
        raise DeliveryError("commit_date")
    add("app/VERSION.txt", (source.source_sha + " " + commit_date + "\n").encode("ascii"))
    package = {"base_commit": source.source_sha, "revision": "develop CI portable",
               "source_sha256": {f["path"][4:]: f["sha256"] for f in records if f["path"].startswith("app/")}}
    add("app/PACKAGE-MANIFEST.json", canonical(package))
    manifest = {"schema_version": 1, "repository": REPOSITORY, "branch": BRANCH,
                "source_sha": source.source_sha, "platform": PLATFORM,
                "build": {"workflow_id": source.workflow_id, "workflow_path": WORKFLOW_PATH,
                          "run_id": source.run_id, "run_attempt": source.run_attempt,
                          "merged_pr": source.merged_pr},
                "data": {"adapter": DATA_ADAPTER, "requires_migration": False},
                "files": sorted(records, key=lambda f: f["path"])}
    validate_manifest(manifest)
    encoded = canonical(manifest)
    if len(encoded) > MAX_MANIFEST:
        raise DeliveryError("manifest_too_large")
    verify_source(source.checkout, context)
    # Published last. Failed packaging leaves a scratch directory without it.
    _write_new(out, MANIFEST_NAME, encoded)
    return manifest
