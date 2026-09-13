"""Verified, cumulative candidate catalogs and read-only legacy registration."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import uuid

from execution.provenance import (ConfigError, atomic_json, canonical, contained,
    directory_lock, identifier, read_json, sha256, write_bytes)
from execution.configs import ConfigStore


def invalid(message="保存済み候補の整合性を確認できません"):
    return ConfigError("catalog", message, code="snapshot_changed")


def positive(value):
    if type(value) is not int or value < 1:
        raise invalid("版番号が正しくありません")
    return value


def document(value, run_id=None, revision=None):
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise invalid()
    if run_id is not None and value.get("run_id") != run_id:
        raise invalid()
    if revision is not None and (type(value.get("revision")) is not int or value["revision"] != revision):
        raise invalid()
    return value


def verified_json(path, digest):
    raw = path.read_bytes()
    if not isinstance(digest, str) or sha256(raw) != digest:
        raise invalid()
    return json.loads(raw)


def validate_candidates(candidates, run_id):
    if not isinstance(candidates, list):
        raise invalid()
    seen = set()
    for item in candidates:
        if not isinstance(item, dict):
            raise invalid()
        identity = item.get("identity")
        if not isinstance(identity, dict) or identity.get("run_id") != run_id:
            raise invalid()
        cid = "cand-" + sha256(canonical(identity))
        if item.get("candidate_id") != cid or cid in seen:
            raise invalid()
        seen.add(cid)
        scheme = identity.get("scheme")
        if scheme == "recorded-v1":
            for field in ("generation", "individual_index", "seed"):
                if type(identity.get(field)) is not int or identity[field] < 0 or type(item.get(field)) is not int or item[field] != identity[field]:
                    raise invalid()
            if identity.get("role") not in ("protagonist", "antagonist") or item.get("role") != identity["role"]:
                raise invalid()
        elif scheme in ("legacy-recorded-v1", "legacy-missing-v1"):
            for field in ("legacy_import_id", "source_archive_sha256", "source_entry_key"):
                if not isinstance(identity.get(field), str) or not identity[field]:
                    raise invalid()
        else:
            raise invalid()
        digest = item.get("source_log_sha256")
        if scheme == "legacy-missing-v1":
            if digest is not None or "source_log_sha256" in identity:
                raise invalid()
        elif (not isinstance(digest, str) or len(digest) != 64 or
              any(c not in "0123456789abcdef" for c in digest) or identity.get("source_log_sha256") != digest):
            raise invalid()
        if type(item.get("reached")) is not bool and not (scheme.startswith("legacy-") and item.get("reached") is None):
            raise invalid()
        if not isinstance(item.get("cell_key"), str) or not isinstance(item.get("log"), dict):
            raise invalid()
        if item["log"].get("availability") not in ("present", "pruned", "missing", "stale"):
            raise invalid()
    return candidates


def observe_candidate(root, item):
    item = deepcopy(item)
    log = item["log"]
    relative = log.get("relative_path")
    if relative is None:
        if not item["identity"]["scheme"].startswith("legacy-"):
            raise invalid()
        observed = None
    else:
        if not isinstance(relative, str) or not relative or relative == ".":
            raise invalid()
        path = contained(root, relative)
        try:
            observed = sha256(path.read_bytes())
        except FileNotFoundError:
            observed = None
    source = item["source_log_sha256"]
    if observed is None:
        availability = "pruned" if log["availability"] == "pruned" else "missing"
    else:
        availability = "present" if source is not None and source == observed else "stale"
    item["log"] = {**log, "availability": availability, "observed_sha256": observed}
    item["screenable"] = item["reached"] is True and availability == "present"
    item["unavailable_reason"] = None if item["screenable"] else (
        "結末未到達または未記録" if item["reached"] is not True else "記録なし／上映不可" if observed is None else "記録不一致／上映不可")
    return item


def read_publication(root, run_id, revision=None, manifest_sha256=None):
    """The pointer is the commit record; an unreferenced folder is not published."""
    pointer = document(read_json(contained(root, "published/current.json")), run_id)
    current = positive(pointer.get("revision"))
    revision = current if revision is None else positive(revision)
    if revision > current:
        raise invalid("未公開の版です")
    expected = pointer["manifest_sha256"] if revision == current else manifest_sha256
    if expected is None:
        raise invalid("過去の公開版には保存時のmanifest SHAが必要です")
    manifest = document(verified_json(contained(root, f"published/{revision}/manifest.json"), expected), run_id, revision)
    if type(manifest.get("completed_generations")) is not int or manifest["completed_generations"] < 0:
        raise invalid()
    files = manifest.get("files")
    if not isinstance(files, dict) or not {"archive", "summary", "candidates"} <= files.keys():
        raise invalid()
    result = {"manifest": manifest, "manifest_sha256": expected}
    for name, ref in files.items():
        if not isinstance(ref, dict) or ref.get("path") != f"published/{revision}/{name}.json":
            raise invalid()
        result[name] = verified_json(contained(root, ref["path"]), ref.get("sha256"))
    catalog = document(result["candidates"], run_id, revision)
    validate_candidates(catalog.get("candidates"), run_id)
    if not isinstance(result["archive"], dict) or not isinstance(result["archive"].get("cells"), dict):
        raise invalid()
    result["revision"] = revision
    result["candidates_sha256"] = files["candidates"]["sha256"]
    return result


class RunCatalog:
    def __init__(self, runs_root, control_root, jobs=None):
        self.runs = Path(runs_root).absolute()
        self.control = Path(control_root).absolute()
        self.jobs = jobs
        self.legacy = contained(self.control, "legacy")

    def _experiment(self, name):
        if not isinstance(name, str) or not name or name in (".", "..") or any(c in name for c in "/\\\0"):
            raise ConfigError("experiment", "実験名が正しくありません")
        path = contained(self.runs, name)
        if not path.is_dir():
            raise ConfigError("run_id", "実行がありません", code="not_found")
        return path

    def run_id(self, name):
        root = self._experiment(name)
        if (root / "manifest.json").is_file():
            manifest = read_json(contained(root, "manifest.json"))
            if manifest.get("schema_version") == 1 and manifest.get("run_id") == name:
                identifier(name, "run_id")
                verified_json(contained(root, "manifest.json"), read_json(contained(root, "complete.json"))["manifest_sha256"])
                return name
            raise invalid("実行manifestの識別子が一致しません")
        return "legacy-" + sha256(str(root).encode("utf-8"))

    def resolve(self, run_id):
        identifier(run_id, "run_id")
        direct = contained(self.runs, run_id)
        if direct.is_dir() and self.run_id(run_id) == run_id:
            return direct, False
        registration = contained(self.legacy, run_id + "/registration.json")
        try:
            record = document(read_json(registration), run_id)
        except FileNotFoundError as error:
            raise ConfigError("run_id", "実行がありません", code="not_found") from error
        root = self._experiment(record["experiment_name"])
        if self.run_id(root.name) != run_id:
            raise invalid()
        return root, True

    def register_legacy(self, name, *, refresh=False):
        root = self._experiment(name)
        rid = self.run_id(name)
        if not rid.startswith("legacy-"):
            return rid
        # Initialize both lock ancestors before concurrent child resolution,
        # following the same first-write ordering as JobStore.
        self.legacy.mkdir(parents=True, exist_ok=True)
        folder = contained(self.legacy, rid)
        folder.mkdir(parents=True, exist_ok=True)
        with directory_lock(folder):
            archive_raw = contained(root, "archive.json").read_bytes()
            archive_hash = sha256(archive_raw)
            archive = json.loads(archive_raw)
            if not isinstance(archive, dict) or not isinstance(archive.get("cells"), dict):
                raise invalid()
            try:
                prior = read_publication(folder, rid)
            except FileNotFoundError:
                if (folder / "published/current.json").exists():
                    raise
                prior = None
            if prior and prior["manifest"].get("source_archive_sha256") == archive_hash and not refresh:
                return rid
            revision = prior["revision"] + 1 if prior else 1
            import_id = "import-" + uuid.uuid4().hex
            candidates = deepcopy(prior["candidates"]["candidates"]) if prior else []
            representatives = {}
            previous = prior["candidates"].get("representatives", {}) if prior else {}
            for key, entry in sorted(archive["cells"].items()):
                reused = [c for c in candidates if c["identity"].get("source_archive_sha256") == archive_hash
                          and c["identity"].get("source_entry_key") == key]
                if reused and not refresh:
                    representatives[key] = reused[-1]["candidate_id"]
                    continue
                if not isinstance(entry, dict):
                    raise invalid()
                exemplar = entry.get("exemplar") or {}
                relative = exemplar.get("layers_path")
                observed = None
                if relative is not None:
                    try:
                        observed = sha256(contained(root, relative).read_bytes())
                    except FileNotFoundError:
                        pass
                identity = {"scheme": "legacy-recorded-v1" if observed else "legacy-missing-v1",
                    "run_id": rid, "legacy_import_id": import_id,
                    "source_archive_sha256": archive_hash, "source_entry_key": key}
                if observed:
                    identity["source_log_sha256"] = observed
                cid = "cand-" + sha256(canonical(identity))
                reached = entry.get("reached")
                # An archive elite with a recorded positive reach rate is reached.
                if type(reached) is not bool:
                    rate = entry.get("reach_rate")
                    reached = rate > 0 if type(rate) in (int, float) else None
                integer = lambda value: value if type(value) is int else None
                candidates.append({"candidate_id": cid, "identity": identity,
                    "role": "unknown", "generation": integer(entry.get("generation")),
                    "individual_index": None, "seed": integer(exemplar.get("seed")),
                    "cell_key": key, "reached": reached, "source_log_sha256": observed,
                    "log": {"relative_path": relative, "availability": "present" if observed else "missing",
                            "observed_sha256": observed},
                    "quality": entry.get("quality"), "parents": entry.get("parents"),
                    "genome": entry.get("genome"), "related_candidate_id": previous.get(key)})
                representatives[key] = cid
            validate_candidates(candidates, rid)
            summary_path = contained(root, "summary.json")
            try:
                summary = read_json(summary_path)
            except FileNotFoundError:
                summary = {}
            payloads = {"archive": archive, "summary": summary,
                "candidates": {"schema_version": 1, "run_id": rid, "revision": revision,
                               "candidates": candidates, "representatives": representatives}}
            # Orphan revisions from a failed pointer write are preserved, never overwritten.
            while (folder / f"published/{revision}").exists():
                revision += 1
            payloads["candidates"]["revision"] = revision
            files = {}
            for key, value in payloads.items():
                raw = canonical(value)
                relative = f"published/{revision}/{key}.json"
                write_bytes(contained(folder, relative), raw)
                files[key] = {"path": relative, "sha256": sha256(raw)}
            manifest = {"schema_version": 1, "run_id": rid, "revision": revision,
                "completed_generations": 0, "legacy_import_id": import_id,
                "source_archive_sha256": archive_hash, "files": files}
            raw = canonical(manifest)
            write_bytes(contained(folder, f"published/{revision}/manifest.json"), raw)
            registration = folder / "registration.json"
            if not registration.exists():
                write_bytes(registration, canonical({"schema_version": 1, "run_id": rid,
                                                     "experiment_name": name}))
            atomic_json(contained(folder, "published/current.json"),
                        {"schema_version": 1, "run_id": rid, "revision": revision, "manifest_sha256": sha256(raw)})
            return rid

    def snapshot(self, run_id, *, revision=None, manifest_sha256=None, observe=True):
        root, legacy = self.resolve(run_id)
        base = contained(self.legacy, run_id) if legacy else root
        result = read_publication(base, run_id, revision, manifest_sha256)
        result.update(run_id=run_id, experiment_name=root.name, legacy=legacy)
        if observe:
            result["candidates"] = deepcopy(result["candidates"])
            result["candidates"]["candidates"] = [
                observe_candidate(root, c) for c in result["candidates"]["candidates"]]
        return result

    def representatives(self, snapshot):
        if snapshot["legacy"]:
            return dict(snapshot["candidates"]["representatives"])
        result = {}
        candidates = snapshot["candidates"]["candidates"]
        for cell, elite in snapshot["archive"]["cells"].items():
            exemplar = elite.get("exemplar") or {}
            matches = [c["candidate_id"] for c in candidates
                if c["role"] == "protagonist" and c["generation"] == elite.get("generation")
                and c["seed"] == exemplar.get("seed")
                and c["log"]["relative_path"] == exemplar.get("layers_path")]
            if len(matches) != 1:
                raise invalid("代表セルを固定候補へ一意に解決できません")
            result[cell] = matches[0]
        return result

    def history(self):
        if self.jobs is not None:
            jobs = self.jobs.list()
        else:
            jobs_root = contained(self.control, "jobs")
            jobs = [read_json(contained(jobs_root, p.name + "/job.json")) for p in sorted(jobs_root.iterdir())
                    if p.is_dir() and not p.name.startswith(".")] if jobs_root.exists() else []
        records = {j["run_id"]: {"schema_version": 1, "run_id": j["run_id"],
            "experiment_name": j["run_id"], "legacy": False, "state": j["state"],
            "phase": j.get("phase"), "job_id": j["job_id"], "config_id": j.get("config_id"),
            "publication_revision": j.get("publication_revision"), "settings": None} for j in jobs}
        for root in sorted(self.runs.iterdir()):
            # prepare_run publishes .pending-* only after sealing the final run ID.
            # Until then, preparation/failure belongs to the job ledger, not this scan.
            if root.name.startswith(".pending-") or not root.is_dir():
                continue
            contained(self.runs, root.name)
            if not (root / "archive.json").is_file() and not (root / "manifest.json").is_file():
                continue
            rid = self.run_id(root.name)
            legacy = rid.startswith("legacy-")
            if legacy:
                rid = self.register_legacy(root.name)
                snap = self.snapshot(rid, observe=False)
                settings = ConfigStore.legacy_settings(snap["summary"])
                state = "legacy"
                revision = snap["revision"]
            else:
                manifest = read_json(contained(root, "manifest.json"))
                settings = {"provenance": "run_manifest", "config_id": manifest.get("config_id"),
                            "evolution": manifest.get("evolution"), "target_endings": manifest.get("target_endings")}
                state = records.get(rid, {}).get("state", "untracked")
                try:
                    revision = self.snapshot(rid, observe=False)["revision"]
                except FileNotFoundError:
                    if (root / "published/current.json").exists():
                        raise
                    revision = None
            records.setdefault(rid, {"schema_version": 1, "run_id": rid, "job_id": None, "phase": None})
            records[rid].update(experiment_name=root.name, legacy=legacy, state=state,
                               publication_revision=revision, settings=settings)
        return sorted(records.values(), key=lambda r: r["run_id"])

    def candidates(self, run_id, **filters):
        allowed = {"generation", "individual_index", "seed", "role", "reached", "availability"}
        for key, value in filters.items():
            if key in ("generation", "individual_index", "seed") and (type(value) is not int or value < 0):
                raise ConfigError(key, "0以上の整数を指定してください")
            if key == "reached" and type(value) is not bool:
                raise ConfigError(key, "真偽値を指定してください")
            if key == "role" and value not in ("protagonist", "antagonist", "unknown"):
                raise ConfigError(key, "roleが正しくありません")
            if key == "availability" and value not in ("present", "pruned", "missing", "stale"):
                raise ConfigError(key, "記録状態が正しくありません")
        if set(filters) - allowed:
            raise ConfigError("filters", "未対応の絞り込みです")
        snapshot = self.snapshot(run_id)
        values = snapshot["candidates"]["candidates"]
        for key, value in filters.items():
            values = [c for c in values if (c["log"]["availability"] if key == "availability" else c[key]) == value]
        return {"schema_version": 1, "run_id": run_id, "revision": snapshot["revision"],
                "manifest_sha256": snapshot["manifest_sha256"], "candidates_sha256": snapshot["candidates_sha256"],
                "candidates": values}


def dispatch(handler, parts, method):
    """Thin JSON boundary; UI-007 owns the subsequent HTML workbench."""
    if len(parts) < 2 or parts[:2] not in (["api", "runs"], ["api", "selected"]):
        return False
    from http import HTTPStatus
    from urllib.parse import parse_qs, urlsplit
    from viewer import job_api
    try:
        catalog, selections = handler.repository.catalog, handler.repository.selections
        if catalog is None:
            raise ConfigError("service", "選定管理は未設定です", code="unavailable")
        body = None
        if method == "POST":
            # Consume the bounded body before rejecting headers, as job_api does.
            # Closing a Windows socket with unread POST bytes can reset a 403.
            handler.connection.settimeout(5)
            try:
                body = handler._request_json()
            except ValueError as error:
                raise ConfigError("request", "JSON本文が不正です", code="bad_request") from error
        job_api.boundary(handler, client_header=method == "POST", body_required=method == "POST")
        if method == "GET" and parts == ["api", "runs"]:
            result = {"schema_version": 1, "runs": catalog.history()}
        elif method == "GET" and parts == ["api", "selected"]:
            result = {"schema_version": 1, "candidates": selections.tray()}
        elif len(parts) == 4 and parts[1] == "runs":
            rid, action = parts[2:]
            if action == "selection" and method == "GET":
                result = selections.get(rid)
            elif action == "selection" and method == "POST":
                if set(body) != {"expected_revision", "changes"}:
                    raise ConfigError("request", "版番号と変更項目を指定してください", code="bad_request")
                result = selections.update(rid, body["changes"], expected_revision=body["expected_revision"])
            elif action == "candidates" and method == "GET":
                query = parse_qs(urlsplit(handler.path).query, keep_blank_values=True)
                filters = {}
                state = query.pop("state", None)
                if state is not None and (len(state) != 1 or state[0] not in ("adopted", "held", "rejected", "unclassified")):
                    raise ConfigError("state", "選定状態が正しくありません")
                for key, values in query.items():
                    if len(values) != 1:
                        raise ConfigError("filters", "絞り込み値は1つ指定してください")
                    value = values[0]
                    if key in ("generation", "individual_index", "seed"):
                        if not value.isascii() or not value.isdigit():
                            raise ConfigError(key, "0以上の整数で指定してください")
                        value = int(value)
                    elif key == "reached":
                        if value not in ("true", "false"):
                            raise ConfigError(key, "trueまたはfalseで指定してください")
                        value = value == "true"
                    filters[key] = value
                result = catalog.candidates(rid, **filters)
                selected = selections.get(rid)
                entries = {e["candidate_id"]: e for e in selected["entries"]}
                result["selection_revision"] = selected["revision"]
                result["candidates"] = [{**c, "state": entries.get(c["candidate_id"], {}).get("state", "unclassified"),
                    "note": entries.get(c["candidate_id"], {}).get("note", "")} for c in result["candidates"]]
                if state is not None:
                    result["candidates"] = [c for c in result["candidates"] if c["state"] == state[0]]
            else:
                raise ConfigError("route", "APIがありません", code="not_found")
        else:
            raise ConfigError("route", "APIがありません", code="not_found")
        handler._send_json(HTTPStatus.OK, result)
    except ConfigError as error:
        job_api.send_error(handler, error)
    except FileNotFoundError:
        job_api.send_error(handler, ConfigError("resource", "公開済み記録がありません", code="not_found"))
    except (OSError, ValueError, TypeError, KeyError):
        handler._send_json(HTTPStatus.INTERNAL_SERVER_ERROR,
            {"code": "storage_error", "message": "保存済み記録を処理できません",
             "field_errors": {}, "retryable": False, "current_revision": None})
    return True
