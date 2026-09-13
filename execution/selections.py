"""Immutable Sifting revisions, shared run locks and legacy star projection."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

from execution.provenance import (ConfigError, atomic_json, canonical, contained,
    directory_lock, identifier, read_json, sha256, write_bytes)
from execution.worker import TERMINAL
from viewer.run_catalog import document, invalid, positive, verified_json


class SelectionError(ConfigError):
    def __init__(self, message, *, code="conflict", current_revision=None):
        super().__init__("selection", message, code=code)
        self.current_revision = current_revision

    def as_dict(self):
        return {**super().as_dict(), "current_revision": self.current_revision}


class SelectionStore:
    def __init__(self, catalog):
        self.catalog = catalog
        self.root = contained(catalog.control, "selections")

    def _folder(self, run_id):
        return contained(self.root, identifier(run_id, "run_id"))

    @contextmanager
    def _guard(self, run_id):
        root, legacy = self.catalog.resolve(run_id)
        jobs = self.catalog.jobs
        jobs_root = contained(self.catalog.control, "jobs")
        # Establish the lock root before resolving its child lock path. On
        # Windows, concurrent creation can give resolve() mixed existence views.
        # This follows JobStore._lock, including for read-only catalog embedding.
        jobs_root.mkdir(parents=True, exist_ok=True)
        # Match the supervisor order: job table, then run. Hold both through commit.
        with jobs._lock() if jobs is not None else directory_lock(jobs_root):
            if jobs is not None:
                records = [jobs._reconcile(job) for job in jobs._all()]
            else:
                records = [read_json(contained(jobs_root, p.name + "/job.json"))
                           for p in jobs_root.iterdir()
                           if p.is_dir() and not p.name.startswith(".")]
            if any(j.get("run_id") == run_id and j.get("state") not in TERMINAL for j in records):
                raise SelectionError("実行中または状態未確認の結果は選定できません")
            if not legacy:
                manifest = read_json(contained(root, "manifest.json"))
                if not any(j.get("job_id") == manifest.get("job_id") and
                           j.get("run_id") == run_id and j.get("state") in TERMINAL for j in records):
                    raise SelectionError("終了ジョブを確認できません")
            lock_root = contained(self.catalog.legacy, run_id) if legacy else root
            with directory_lock(lock_root):
                yield

    @staticmethod
    def _validate_entries(entries):
        if not isinstance(entries, list):
            raise invalid()
        seen = set()
        for entry in entries:
            if (not isinstance(entry, dict) or not isinstance(entry.get("candidate_id"), str)
                    or entry["candidate_id"] in seen or entry.get("state") not in
                    ("adopted", "held", "rejected", "unclassified") or not isinstance(entry.get("note"), str)):
                raise invalid()
            seen.add(entry["candidate_id"])

    def _read(self, run_id, revision=None, digest=None):
        folder = self._folder(run_id)
        try:
            current = document(read_json(contained(folder, "current.json")), run_id)
        except FileNotFoundError:
            if revision is not None:
                raise invalid("未公開の選定版です")
            return None
        current_revision = positive(current.get("revision"))
        requested = current_revision if revision is None else positive(revision)
        cursor, expected = current_revision, current.get("sha256")
        while True:
            selected = document(verified_json(contained(folder, f"revisions/{cursor}.json"), expected), run_id, cursor)
            if cursor == requested:
                if digest is not None and expected != digest:
                    raise invalid("選定版のSHAが一致しません")
                digest, revision = expected, requested
                break
            parent = selected.get("parent_revision")
            if parent is None or type(parent) is not int or not requested <= parent < cursor:
                raise invalid("公開された選定履歴にない版です")
            cursor, expected = parent, selected.get("parent_sha256")
        self._validate_entries(selected.get("entries"))
        snap = self.catalog.snapshot(run_id, revision=selected["source_publication_revision"],
                                     manifest_sha256=selected["source_manifest_sha256"], observe=False)
        if selected["source_candidates_sha256"] != snap["candidates_sha256"]:
            raise invalid()
        ids = {c["candidate_id"] for c in snap["candidates"]["candidates"]}
        if any(e["candidate_id"] not in ids for e in selected["entries"]):
            raise invalid()
        return {**selected, "sha256": digest}

    def _initial(self, run_id, snapshot):
        root, _ = self.catalog.resolve(run_id)
        try:
            raw = read_json(contained(root, "selection.json"))
        except FileNotFoundError:
            raw = {"selected": []}
        if not isinstance(raw, dict) or not isinstance(raw.get("selected"), list) or any(
                not isinstance(v, str) for v in raw["selected"]):
            raise invalid("旧選定形式が正しくありません")
        reps = self.catalog.representatives(snapshot)
        entries = [{"candidate_id": reps[cell], "state": "adopted", "note": ""}
                   for cell in sorted(set(raw["selected"])) if cell in reps]
        return {"schema_version": 1, "run_id": run_id, "revision": 0, "parent_revision": None,
                "source_publication_revision": snapshot["revision"],
                "source_candidates_sha256": snapshot["candidates_sha256"],
                "source_manifest_sha256": snapshot["manifest_sha256"], "entries": entries, "sha256": None}

    def get(self, run_id, *, revision=None, sha256=None):
        self.catalog.resolve(run_id)
        found = self._read(run_id, revision, sha256)
        if found is not None:
            return found
        snapshot = self.catalog.snapshot(run_id)
        return self._initial(run_id, snapshot)

    def _projection(self, snapshot, selected):
        adopted = {e["candidate_id"] for e in selected["entries"] if e["state"] == "adopted"}
        return {cell for cell, cid in self.catalog.representatives(snapshot).items() if cid in adopted}

    def _project(self, run_id, snapshot, selected):
        root, _ = self.catalog.resolve(run_id)
        cells = self._projection(snapshot, selected)
        atomic_json(contained(root, "selection.json"), {"selected": sorted(cells)})
        return cells

    def projected(self, run_id):
        snapshot = self.catalog.snapshot(run_id)
        selected = self._read(run_id) or self._initial(run_id, snapshot)
        return self._projection(snapshot, selected)

    def _save(self, run_id, changes, expected_revision):
        snapshot = self.catalog.snapshot(run_id)
        previous = self._read(run_id) or self._initial(run_id, snapshot)
        if type(expected_revision) is not int or expected_revision < 0:
            raise SelectionError("expected_revisionは0以上の整数です", code="invalid_config")
        if expected_revision != previous["revision"]:
            raise SelectionError("選定が更新されています。最新の版を確認してください",
                                 current_revision=previous["revision"])
        if not isinstance(changes, list) or not changes:
            raise SelectionError("変更項目を指定してください", code="invalid_config")
        by_id = {c["candidate_id"]: c for c in snapshot["candidates"]["candidates"]}
        entries = {e["candidate_id"]: deepcopy(e) for e in previous["entries"]}
        if set(entries) - set(by_id):
            raise invalid("過去の選定候補が現在の累積台帳から失われています")
        seen = set()
        for change in changes:
            if not isinstance(change, dict) or set(change) - {"candidate_id", "state", "note"}:
                raise SelectionError("変更項目が正しくありません", code="invalid_config")
            cid = change.get("candidate_id")
            if not isinstance(cid, str) or cid not in by_id or cid in seen:
                raise SelectionError("候補IDが不明または重複しています", code="invalid_config")
            seen.add(cid)
            entry = entries.get(cid, {"candidate_id": cid, "state": "unclassified", "note": ""})
            if "state" in change:
                if change["state"] not in ("adopted", "held", "rejected", "unclassified"):
                    raise SelectionError("選定状態が正しくありません", code="invalid_config")
                if change["state"] == "adopted" and not by_id[cid]["screenable"]:
                    raise SelectionError("結末到達済みで原記録が一致する候補だけ採用できます", code="invalid_config")
                entry["state"] = change["state"]
            if "note" in change:
                if not isinstance(change["note"], str):
                    raise SelectionError("メモは文字列で指定してください", code="invalid_config")
                entry["note"] = change["note"]
            entries[cid] = entry
        folder = self._folder(run_id)
        revision = previous["revision"] + 1
        while contained(folder, f"revisions/{revision}.json").exists():
            revision += 1
        document = {"schema_version": 1, "run_id": run_id, "revision": revision,
                    "parent_revision": previous["revision"] or None,
                    "parent_sha256": previous["sha256"],
                    "source_publication_revision": snapshot["revision"],
                    "source_candidates_sha256": snapshot["candidates_sha256"],
                    "source_manifest_sha256": snapshot["manifest_sha256"],
                    "entries": sorted(entries.values(), key=lambda e: e["candidate_id"])}
        raw = canonical(document)
        write_bytes(contained(folder, f"revisions/{revision}.json"), raw)
        digest = sha256(raw)
        atomic_json(contained(folder, "current.json"),
                    {"schema_version": 1, "run_id": run_id, "revision": revision, "sha256": digest})
        try:
            self._project(run_id, snapshot, document)
        except (OSError, ConfigError) as error:
            raise SelectionError("選定版は保存済みですが旧表示への反映に失敗しました。再照合が必要です",
                                 code="projection_pending", current_revision=revision) from error
        return {**document, "sha256": digest}

    def update(self, run_id, changes, *, expected_revision):
        with self._guard(run_id):
            return self._save(run_id, changes, expected_revision)

    def toggle(self, run_id, cell, selected):
        if not isinstance(cell, str) or type(selected) is not bool:
            raise SelectionError("セルと選定状態が正しくありません", code="invalid_config")
        with self._guard(run_id):
            snapshot = self.catalog.snapshot(run_id)
            reps = self.catalog.representatives(snapshot)
            if cell not in reps:
                raise SelectionError("セルがありません", code="invalid_config")
            previous = self._read(run_id) or self._initial(run_id, snapshot)
            self._save(run_id, [{"candidate_id": reps[cell],
                "state": "adopted" if selected else "unclassified"}], previous["revision"])
            return self.projected(run_id)

    def repair_projection(self, run_id):
        with self._guard(run_id):
            selected = self._read(run_id)
            if selected is None:
                return self.projected(run_id)
            return self._project(run_id, self.catalog.snapshot(run_id), selected)

    def tray(self, states=("adopted", "held")):
        if set(states) - {"adopted", "held", "rejected", "unclassified"}:
            raise SelectionError("選定状態が正しくありません", code="invalid_config")
        result = []
        for run in self.catalog.history():
            if run["publication_revision"] is None:
                continue
            rid = run["run_id"]
            snapshot = self.catalog.snapshot(rid)
            selected = self._read(rid) or self._initial(rid, snapshot)
            entries = {e["candidate_id"]: e for e in selected["entries"]}
            for candidate in snapshot["candidates"]["candidates"]:
                entry = entries.get(candidate["candidate_id"], {"state": "unclassified", "note": ""})
                if entry["state"] in states:
                    result.append({**candidate, "state": entry["state"], "note": entry["note"],
                                   "run_id": rid, "experiment_name": run["experiment_name"],
                                   "selection_revision": selected["revision"]})
        return result
