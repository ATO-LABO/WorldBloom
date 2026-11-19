"""Read-only GitHub evidence through the existing gh CLI; no auth setup or writes.

Construction never starts a process. Tests inject runner/popen fixtures. Only
fixed-repository GET endpoints are built here. A develop ref lookup is pinned
to its commit SHA before evidence checks; mutable branch names and local source
trees are never used to build the distribution.
"""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
from queue import Empty, Full, Queue
import re
import subprocess
from threading import Event, Thread
import time
from types import MappingProxyType
from collections.abc import Mapping
import unicodedata
from urllib.parse import urlencode

from .contract import (BRANCH, CODEQL_PATH, MAX_ARCHIVE, REPOSITORY,
                       WORKFLOW_PATH, ReleaseEvidence, artifact_name, positive, strict_json,
                       valid_sha)
from .errors import DeliveryError

_PAGE_SIZE = 100
_MAX_JSON = 8 * 1024 * 1024
_API_TIMEOUT = 45
_DOWNLOAD_TIMEOUT = 120
_CHUNK = 64 * 1024
_PREFIX = "/repos/" + REPOSITORY
_DIGEST = re.compile(r"sha256:([a-f0-9]{64})\Z")


def _need(condition, code):
    if not condition:
        raise DeliveryError(code)


def _id(value):
    return positive(value) and value <= (1 << 63) - 1


@dataclass(frozen=True)
class _Config:
    repository_id: int
    workflow_id: int
    codeql_workflow_id: int
    workflow_path: str
    codeql_workflow_path: str
    ci_policy_job: str
    portable_job: str
    gh_path: str


@dataclass(frozen=True)
class ResolvedContext:
    evidence: ReleaseEvidence
    merged_pr_detail: Mapping
    codeql_run_id: int
    codeql_run_attempt: int


class GitHubReader:
    def __init__(self, config, *, runner=None, popen=None, max_pages=5):
        _need(isinstance(config, Mapping), "github_config")
        keys = ("repository_id", "workflow_id", "codeql_workflow_id",
                "workflow_path", "codeql_workflow_path")
        _need(all(key in config for key in keys), "github_config")
        _need(all(_id(config[key]) for key in keys[:3]), "github_config")
        _need(config["workflow_id"] != config["codeql_workflow_id"], "github_config")
        _need(config.get("repository", REPOSITORY) == REPOSITORY
              and config["workflow_path"] == WORKFLOW_PATH
              and config["codeql_workflow_path"] == CODEQL_PATH,
              "github_config")
        _need(config.get("ci_policy_job", "ci-policy") == "ci-policy"
              and config.get("portable_job", "portable") == "portable",
              "github_config")
        _need(type(max_pages) is int and 1 <= max_pages <= 20, "github_config")
        executable = config.get("gh_path", "gh")
        _need(isinstance(executable, str) and executable
              and not any(unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in executable),
              "github_cli_path")
        windows, posix = PureWindowsPath(executable), PurePosixPath(executable)
        _need(executable in ("gh", "gh.exe")
              or ((windows.is_absolute() and windows.name.casefold() in ("gh", "gh.exe"))
                  or (os.name != "nt" and posix.is_absolute() and posix.name in ("gh", "gh.exe"))),
              "github_cli_path")
        self._config = _Config(*(config[key] for key in keys), "ci-policy", "portable", executable)
        self._runner = runner if runner is not None else subprocess.run
        self._popen = popen if popen is not None else subprocess.Popen
        self._max_pages = max_pages

    def _command(self, endpoint):
        _need(isinstance(endpoint, str) and
              (endpoint == _PREFIX or endpoint.startswith(_PREFIX + "/"))
              and "://" not in endpoint and "\n" not in endpoint,
              "github_endpoint")
        return [self._config.gh_path, "api", "--hostname", "github.com", "--method", "GET",
                "--header", "Accept: application/vnd.github+json",
                "--header", "X-GitHub-Api-Version: 2022-11-28", endpoint]

    def _get(self, path, params=None):
        endpoint = _PREFIX + path
        if params:
            endpoint += "?" + urlencode(params)
        try:
            result = self._runner(self._command(endpoint), shell=False,
                                  stdin=subprocess.DEVNULL, capture_output=True,
                                  timeout=_API_TIMEOUT, check=False,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as exc:
            raise DeliveryError("github_timeout") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise DeliveryError("github_unavailable") from exc
        _need(result.returncode == 0, "github_read_failed")
        raw = result.stdout
        if isinstance(raw, str):
            raw = raw.encode("utf8")
        _need(isinstance(raw, bytes) and len(raw) <= _MAX_JSON, "github_response_size")
        return strict_json(raw)

    def _pages(self, path, key=None, params=None, *, identity="id"):
        rows, seen, total = [], set(), None
        for page in range(1, self._max_pages + 1):
            query = dict(params or {})
            query.update(per_page=_PAGE_SIZE, page=page)
            value = self._get(path, query)
            if key is None:
                values = value
            else:
                _need(isinstance(value, dict) and key in value, "github_pagination")
                count = value.get("total_count")
                _need(type(count) is int and count >= 0, "github_pagination")
                _need(count <= _PAGE_SIZE * self._max_pages, "github_pagination_limit")
                if total is not None:
                    _need(total == count, "github_pagination_changed")
                total, values = count, value[key]
            _need(isinstance(values, list) and len(values) <= _PAGE_SIZE,
                  "github_pagination")
            for row in values:
                _need(isinstance(row, dict) and _id(row.get(identity)), "github_pagination")
                ident = row[identity]
                _need(ident not in seen, "github_ambiguous_page")
                seen.add(ident)
                rows.append(row)
            if key is not None:
                _need(len(rows) <= total, "github_pagination")
                if len(rows) == total:
                    return rows
                _need(bool(values), "github_pagination_incomplete")
            elif len(values) < _PAGE_SIZE:
                return rows
        raise DeliveryError("github_pagination_limit")

    def _repository(self):
        value = self._get("")
        self._same_repo(value)
        return value

    def _same_repo(self, value):
        _need(isinstance(value, dict) and _id(value.get("id"))
              and value["id"] == self._config.repository_id
              and value.get("full_name") == REPOSITORY, "github_repository")

    def _workflow(self, workflow_id, path):
        value = self._get(f"/actions/workflows/{workflow_id}")
        _need(isinstance(value, dict) and _id(value.get("id"))
              and value["id"] == workflow_id and value.get("path") == path
              and value.get("state") == "active", "github_workflow")

    def _develop_sha(self):
        value = self._get("/git/ref/heads/" + BRANCH)
        _need(isinstance(value, dict) and value.get("ref") == "refs/heads/" + BRANCH
              and isinstance(value.get("object"), dict)
              and value["object"].get("type") == "commit"
              and valid_sha(value["object"].get("sha")), "github_develop_ref")
        return value["object"]["sha"]

    def _run_identity(self, value, workflow_id, path, sha=None):
        _need(isinstance(value, dict), "github_run")
        self._same_repo(value.get("repository"))
        self._same_repo(value.get("head_repository"))
        _need(_id(value.get("id")) and _id(value.get("run_attempt"))
              and _id(value.get("workflow_id")) and value["workflow_id"] == workflow_id
              and value.get("path") == path and value.get("event") == "push"
              and value.get("head_branch") == BRANCH and valid_sha(value.get("head_sha"))
              and (sha is None or value["head_sha"] == sha),
              "github_run")
        return value

    def _run(self, value, workflow_id, path, sha=None):
        value = self._run_identity(value, workflow_id, path, sha)
        _need(value.get("status") == "completed" and value.get("conclusion") == "success",
              "github_run")
        return value

    def _select_run(self, workflow_id, path, sha):
        _need(valid_sha(sha), "github_source_sha")
        query = {"branch": BRANCH, "event": "push", "head_sha": sha}
        runs = self._pages(f"/actions/workflows/{workflow_id}/runs", "workflow_runs",
                           query)
        # Never turn a failed or incomplete run for this SHA into an older success.
        candidates = [run for run in runs if run.get("head_sha") == sha]
        _need(bool(candidates), "github_no_run")
        _need(len(candidates) == 1, "github_ambiguous_run")
        selected = candidates[0]
        self._run(selected, workflow_id, path, sha)
        run = self._get(f"/actions/runs/{selected['id']}")
        self._run(run, workflow_id, path, selected["head_sha"])
        _need(run["id"] == selected["id"] and run["run_attempt"] == selected["run_attempt"],
              "github_run_changed")
        return run

    def _jobs(self, run, names=None):
        rows = self._pages(f"/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs", "jobs")
        for name in names or (self._config.ci_policy_job, self._config.portable_job):
            matches = [row for row in rows if row.get("name") == name]
            _need(len(matches) == 1, "github_job_evidence")
            job = matches[0]
            _need(_id(job.get("run_id")) and job["run_id"] == run["id"]
                  and job.get("head_sha") == run["head_sha"]
                  and ("run_attempt" not in job or
                       _id(job["run_attempt"]) and job["run_attempt"] == run["run_attempt"])
                  and job.get("status") == "completed" and job.get("conclusion") == "success",
                  "github_job_evidence")

    def _merged_pr(self, sha):
        rows = self._pages(f"/commits/{sha}/pulls", identity="number")
        matches = []
        for row in rows:
            pr = self._get(f"/pulls/{row['number']}")
            _need(isinstance(pr, dict) and _id(pr.get("number"))
                  and pr["number"] == row["number"], "github_pull_request")
            base = pr.get("base")
            if (pr.get("merged") is True and pr.get("merge_commit_sha") == sha
                    and isinstance(base, dict) and base.get("ref") == BRANCH):
                self._same_repo(base.get("repo"))
                matches.append(pr)
        _need(len(matches) == 1, "github_merged_pr_evidence")
        return matches[0]

    def _artifact(self, value, run, artifact_id=None):
        _need(isinstance(value, dict), "github_artifact")
        linked = value.get("workflow_run")
        digest = value.get("digest")
        match = _DIGEST.fullmatch(digest) if isinstance(digest, str) else None
        _need(_id(value.get("id")) and (artifact_id is None or value["id"] == artifact_id)
              and value.get("name") == artifact_name(run["head_sha"], run["id"], run["run_attempt"])
              and value.get("expired") is False and positive(value.get("size_in_bytes"))
              and value["size_in_bytes"] <= MAX_ARCHIVE and match is not None,
              "github_artifact")
        _need(isinstance(linked, dict) and _id(linked.get("id"))
              and linked["id"] == run["id"] and linked.get("head_sha") == run["head_sha"]
              and _id(linked.get("repository_id")) and _id(linked.get("head_repository_id"))
              and linked["repository_id"] == self._config.repository_id
              and linked["head_repository_id"] == self._config.repository_id
              and ("head_branch" not in linked or linked["head_branch"] == BRANCH),
              "github_artifact_run")
        return match.group(1)

    def resolve_context(self, source_sha=None):
        _need(source_sha is None or valid_sha(source_sha), "github_source_sha")
        self._repository()
        if source_sha is None:
            source_sha = self._develop_sha()
        cfg = self._config
        self._workflow(cfg.workflow_id, cfg.workflow_path)
        self._workflow(cfg.codeql_workflow_id, cfg.codeql_workflow_path)
        run = self._select_run(cfg.workflow_id, cfg.workflow_path, source_sha)
        self._jobs(run)
        sha = run["head_sha"]
        pr = self._merged_pr(sha)
        codeql = self._select_run(cfg.codeql_workflow_id, cfg.codeql_workflow_path, sha)
        artifacts = self._pages(f"/actions/runs/{run['id']}/artifacts", "artifacts")
        expected_name = artifact_name(sha, run["id"], run["run_attempt"])
        matches = [v for v in artifacts if v.get("name") == expected_name]
        _need(len(matches) == 1, "github_artifact_evidence")
        listed = matches[0]
        digest = self._artifact(listed, run)
        artifact = self._get(f"/actions/artifacts/{listed['id']}")
        _need(self._artifact(artifact, run, listed["id"]) == digest
              and artifact["size_in_bytes"] == listed["size_in_bytes"], "github_artifact_changed")
        confirmed = self._get(f"/actions/runs/{run['id']}")
        self._run(confirmed, cfg.workflow_id, cfg.workflow_path, sha)
        _need(confirmed["id"] == run["id"] and confirmed["run_attempt"] == run["run_attempt"],
              "github_run_changed")
        evidence = ReleaseEvidence(sha, run["id"], run["run_attempt"], cfg.workflow_id,
                                   pr["number"], artifact["id"], digest,
                                   artifact["size_in_bytes"], cfg.repository_id,
                                   codeql_verified=True)
        detail = MappingProxyType({"number": pr["number"], "merged": True,
                                   "merge_commit_sha": sha, "base_ref": BRANCH,
                                   "base_repository_id": cfg.repository_id,
                                   "base_repository_full_name": REPOSITORY})
        return ResolvedContext(evidence, detail, codeql["id"], codeql["run_attempt"])

    def resolve(self, source_sha=None):
        return self.resolve_context(source_sha).evidence

    def build_context(self, source_sha, run_id, run_attempt):
        """Prebuild identity/merge/ci-policy proof; never installation evidence.

        A portable job cannot require its own success or finished artifact.
        The PC's resolve boundary independently requires those and CodeQL.
        """
        _need(valid_sha(source_sha) and _id(run_id) and _id(run_attempt),
              "github_build_input")
        self._repository()
        cfg = self._config
        self._workflow(cfg.workflow_id, cfg.workflow_path)

        def check(value):
            self._run_identity(value, cfg.workflow_id, cfg.workflow_path, source_sha)
            _need(value["id"] == run_id and value["run_attempt"] == run_attempt,
                  "github_build_run")
            _need((value.get("status") == "in_progress" and value.get("conclusion") is None)
                  or (value.get("status") == "completed" and value.get("conclusion") == "success"),
                  "github_build_run")
            return value

        run = check(self._get(f"/actions/runs/{run_id}"))
        self._jobs(run, (cfg.ci_policy_job,))
        pr = self._merged_pr(source_sha)
        check(self._get(f"/actions/runs/{run_id}"))
        return {"repository": REPOSITORY, "event": "push", "ref": "refs/heads/" + BRANCH,
                "source_sha": source_sha, "workflow_id": cfg.workflow_id,
                "workflow_path": cfg.workflow_path, "run_id": run_id,
                "run_attempt": run_attempt, "ci_policy": "success",
                "merged_pr": {"number": pr["number"], "merged": pr["merged"],
                              "merge_commit_sha": pr["merge_commit_sha"],
                              "base": {"ref": pr["base"]["ref"],
                                       "repo": {"id": pr["base"]["repo"]["id"],
                                                "full_name": pr["base"]["repo"]["full_name"]}}}}

    def compare(self, base, head):
        _need(valid_sha(base) and valid_sha(head), "github_compare_sha")
        if base == head:
            return False
        value = self._get(f"/compare/{base}...{head}", {"per_page": 1, "page": 1})
        _need(isinstance(value, dict) and type(value.get("ahead_by")) is int
              and type(value.get("behind_by")) is int and value["ahead_by"] >= 0
              and value["behind_by"] >= 0, "github_compare")
        return value.get("status") == "ahead" and value["ahead_by"] > 0 and value["behind_by"] == 0

    @staticmethod
    def _owns(path, identity):
        try:
            status = path.lstat()
            return not path.is_symlink() and (status.st_dev, status.st_ino) == identity
        except FileNotFoundError:
            return False

    def download(self, evidence, destination):
        _need(isinstance(evidence, ReleaseEvidence), "github_evidence")
        target = Path(destination).absolute()
        _need(not os.path.lexists(target), "download_destination_exists")
        # A caller's old metadata is not download authority. Recheck every
        # source/run/attempt/PR/CodeQL condition and the artifact's fresh record.
        current = self.resolve(evidence.source_sha)
        _need(current == evidence, "github_evidence_changed")
        run = {"id": current.run_id, "run_attempt": current.run_attempt, "head_sha": current.source_sha}
        artifact = self._get(f"/actions/artifacts/{current.artifact_id}")
        _need(self._artifact(artifact, run, current.artifact_id) == current.artifact_digest
              and artifact["size_in_bytes"] == current.artifact_size, "github_artifact_changed")
        process, handle, identity = None, None, None
        succeeded = False
        try:
            try:
                handle = target.open("xb")
            except FileExistsError as exc:
                raise DeliveryError("download_destination_exists") from exc
            status = os.fstat(handle.fileno())
            identity = (status.st_dev, status.st_ino)
            process = self._popen(self._command(f"{_PREFIX}/actions/artifacts/{current.artifact_id}/zip"),
                                  shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            count, hasher = 0, hashlib.sha256()
            for chunk in _chunks(process, _DOWNLOAD_TIMEOUT):
                count += len(chunk)
                _need(count <= MAX_ARCHIVE, "download_archive_too_large")
                _need(count <= current.artifact_size, "download_size_mismatch")
                handle.write(chunk)
                hasher.update(chunk)
            _need(process.wait(timeout=5) == 0, "github_download_failed")
            _need(count == current.artifact_size, "download_size_mismatch")
            _need(hasher.hexdigest() == current.artifact_digest, "download_digest_mismatch")
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            handle = None
            _need(self._owns(target, identity), "download_destination_changed")
            succeeded = True
            return target
        except DeliveryError:
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            raise DeliveryError("github_download_failed") from exc
        finally:
            if not succeeded and process is not None:
                try:
                    process.kill()
                except OSError:
                    pass
                try:
                    process.wait(timeout=5)
                except (OSError, subprocess.SubprocessError):
                    pass
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass
            if process is not None and getattr(process, "stdout", None) is not None:
                close = getattr(process.stdout, "close", None)
                if close is not None:
                    try:
                        close()
                    except OSError:
                        pass
            if not succeeded and identity is not None and self._owns(target, identity):
                try:
                    target.unlink()
                except OSError as exc:
                    raise DeliveryError("download_cleanup_failed") from exc


def _chunks(process, timeout):
    """Bounded pipe reads on Windows too; stderr is discarded, never logged."""
    queue, stop = Queue(maxsize=2), Event()
    def emit(item):
        while not stop.is_set():
            try:
                queue.put(item, timeout=.05)
                return
            except Full:
                continue
    def read():
        try:
            while not stop.is_set():
                value = process.stdout.read(_CHUNK)
                if not value:
                    emit(("done", None))
                    return
                if not isinstance(value, bytes) or len(value) > _CHUNK:
                    emit(("error", None))
                    return
                emit(("data", value))
        except Exception:
            emit(("error", None))
    worker = Thread(target=read, daemon=True)
    worker.start()
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            _need(remaining > 0, "github_download_timeout")
            try:
                kind, value = queue.get(timeout=remaining)
            except Empty as exc:
                raise DeliveryError("github_download_timeout") from exc
            _need(kind != "error", "github_download_stream")
            if kind == "done":
                break
            yield value
    finally:
        stop.set()
        worker.join(timeout=.1)
