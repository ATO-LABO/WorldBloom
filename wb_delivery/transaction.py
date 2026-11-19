"""Code-only journaled installation, rollback and interrupted-update recovery."""
from pathlib import Path
import os
import uuid

from .bundle import verify_and_stage
from .contract import (PROTECTED, REPOSITORY, canonical, digest, is_link, managed_path,
                       safe_child, strict_json, valid_sha)
from .errors import DeliveryError
from .lease import lease


def file_hash(path):
    if not path.exists():
        return None
    if not path.is_file() or is_link(path):
        raise DeliveryError("not_regular_file")
    return digest(path.read_bytes())


_UNSET = object()


def atomic_write(root, rel, blob, *, managed=False, expected_current=_UNSET):
    path = safe_child(root, rel, managed=managed)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / (".wb-update-" + uuid.uuid4().hex)
    try:
        with temp.open("xb") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        path = safe_child(root, rel, managed=managed)
        if expected_current is not _UNSET and file_hash(path) != expected_current:
            raise DeliveryError("concurrent_code_edit", rel)
        os.replace(temp, path)
    finally:
        if temp.exists():
            if temp.parent.resolve() != path.parent.resolve() or is_link(temp):
                raise DeliveryError("unsafe_temp_cleanup")
            temp.unlink()


def protected_snapshot(root):
    result = {}
    for rel in PROTECTED:
        path = safe_child(root, rel, managed=False)
        paths = [path] if path.is_file() else list(path.rglob("*")) if path.is_dir() else []
        for p in paths:
            if is_link(p):
                raise DeliveryError("linked_userdata")
            if p.is_file():
                name = p.relative_to(root).as_posix()
                safe_child(root, name, managed=False)
                result[name] = file_hash(p)
    return result


class Installer:
    """Runtime and compatibility callbacks must return explicit verified results.

    No defaults assume an idle app or compatible data. The caller must keep the
    app/worker shared-lease hooks connected before enabling background updates.
    """

    def __init__(self, target, state, *, runtime_probe=None, compatibility=None, forward_check=None, fault=None):
        self.target = Path(target).absolute()
        self.state = Path(state).absolute()
        for root in (self.target, self.state):
            for p in [root, *root.parents]:
                if is_link(p):
                    raise DeliveryError("linked_root")
        if not self.target.is_dir() or self.target.parent == self.target:
            raise DeliveryError("invalid_target")
        if (self.state.resolve() == self.target.resolve() or self.state.resolve().is_relative_to(self.target.resolve())
                or self.target.resolve().is_relative_to(self.state.resolve())):
            raise DeliveryError("overlapping_state")
        self.runtime_probe = runtime_probe
        self.compatibility = compatibility
        self.forward_check = forward_check
        self.fault = fault or (lambda phase, index: None)

    def _read(self, rel):
        return strict_json(safe_child(self.state, rel, managed=False).read_bytes())

    def _save(self, rel, value):
        atomic_write(self.state, rel, canonical(value))

    def _idle(self):
        if self.runtime_probe is None:
            raise DeliveryError("runtime_probe_not_configured")
        try:
            status = self.runtime_probe(self.target)
        except Exception as exc:
            raise DeliveryError("runtime_unknown") from exc
        if not isinstance(status, dict) or status.get("app_running") is not False or status.get("jobs_running") is not False:
            raise DeliveryError("in_use_or_unknown")

    def _validate_receipt(self, r):
        if (not isinstance(r, dict) or r.get("repository") != REPOSITORY or r.get("schema_version") != 1
                or r.get("kind") not in ("local", "ci") or not valid_sha(r.get("source_sha"))
                or not isinstance(r.get("files"), dict) or not r["files"]):
            raise DeliveryError("invalid_installed_receipt")
        for rel, sha in r["files"].items():
            managed_path(rel)
            if not valid_sha(sha, 64):
                raise DeliveryError("invalid_installed_receipt")
        held = r.get("held_shas", [])
        if not isinstance(held, list) or any(not valid_sha(sha) for sha in held):
            raise DeliveryError("invalid_installed_receipt")
        return r

    def _receipt(self):
        try:
            return self._validate_receipt(self._read("installed.json"))
        except FileNotFoundError as exc:
            raise DeliveryError("not_enrolled") from exc

    def _unchanged(self, receipt):
        for rel, expected in receipt["files"].items():
            if file_hash(safe_child(self.target, rel)) != expected:
                raise DeliveryError("local_code_edits", rel)

    def _pending(self):
        root = safe_child(self.state, "transactions", managed=False)
        if not root.exists():
            return []
        pending = []
        for folder in root.iterdir():
            if is_link(folder) or not folder.is_dir():
                raise DeliveryError("invalid_transaction_store")
            path = folder / "journal.json"
            if path.exists():
                journal = self._read("transactions/" + folder.name + "/journal.json")
                if not isinstance(journal, dict):
                    raise DeliveryError("invalid_journal")
                if journal.get("status") not in ("committed", "rolled_back"):
                    pending.append(folder.name)
        return pending

    def enroll(self, paths, *, source_sha, allowed_first_sha, confirmed=False):
        if confirmed is not True or not valid_sha(source_sha) or not valid_sha(allowed_first_sha):
            raise DeliveryError("first_enrollment_confirmation_required")
        with lease(self.state / "update.lock", exclusive=True):
            self._idle()
            if (self.state / "installed.json").exists() or self._pending():
                raise DeliveryError("already_enrolled_or_pending")
            files = {}
            for rel in paths:
                managed_path(rel)
                h = file_hash(safe_child(self.target, rel))
                if h is None:
                    raise DeliveryError("missing_baseline_file", rel)
                files[rel] = h
            if not files:
                raise DeliveryError("empty_baseline")
            self._save("installed.json", {"schema_version": 1, "repository": REPOSITORY,
                       "kind": "local", "source_sha": source_sha, "allowed_first_sha": allowed_first_sha,
                       "files": files, "transaction": None})

    def install(self, archive, evidence):
        with lease(self.state / "update.lock", exclusive=True):
            self._idle()
            if self._pending():
                raise DeliveryError("recovery_required")
            previous = self._receipt()
            self._unchanged(previous)
            if evidence.source_sha in previous.get("held_shas", []):
                raise DeliveryError("release_on_hold")
            if previous["source_sha"] == evidence.source_sha and previous["kind"] == "ci":
                return {"status": "already_installed", "sha": evidence.source_sha}
            if previous["kind"] == "local":
                if previous.get("allowed_first_sha") != evidence.source_sha:
                    raise DeliveryError("first_sha_not_approved")
            else:
                if self.forward_check is None or self.forward_check(previous["source_sha"], evidence.source_sha) is not True:
                    raise DeliveryError("not_forward_update")
            tx = uuid.uuid4().hex
            stage = safe_child(self.state, "staging/" + tx, managed=False)
            manifest = verify_and_stage(archive, evidence, stage)
            incoming = {f["path"]: f["sha256"] for f in manifest["files"]}
            for rel in incoming:
                if rel not in previous["files"] and safe_child(self.target, rel).exists():
                    raise DeliveryError("unmanaged_file_collision", rel)
            if self.compatibility is None:
                raise DeliveryError("compatibility_adapter_not_configured")
            protected = protected_snapshot(self.target)
            try:
                compatible = self.compatibility(stage, self.target, manifest)
            except Exception as exc:
                raise DeliveryError("data_validation_failed") from exc
            if compatible is not True or protected_snapshot(self.target) != protected:
                raise DeliveryError("data_incompatible_or_changed")
            self._idle()
            self._unchanged(previous)
            entries = []
            for rel in sorted(set(previous["files"]) | set(incoming)):
                before = previous["files"].get(rel)
                after = incoming.get(rel)
                if before == after:
                    continue
                entry = {"path": rel, "before": before, "after": after}
                if before is not None:
                    blob = safe_child(self.target, rel).read_bytes()
                    if digest(blob) != before:
                        raise DeliveryError("local_code_edits", rel)
                    atomic_write(self.state, "transactions/" + tx + "/backup/" + rel, blob)
                entries.append(entry)
            journal = {"schema_version": 1, "transaction": tx, "target": str(self.target.resolve()),
                       "status": "applying", "entries": entries, "previous": previous,
                       "protected": protected, "new_files": incoming, "source_sha": evidence.source_sha}
            journal_rel = "transactions/" + tx + "/journal.json"
            self._save(journal_rel, journal)
            try:
                for index, entry in enumerate(entries):
                    rel = entry["path"]
                    if file_hash(safe_child(self.target, rel)) != entry["before"]:
                        raise DeliveryError("concurrent_code_edit", rel)
                    self.fault("before_write", index)
                    if file_hash(safe_child(self.target, rel)) != entry["before"]:
                        raise DeliveryError("concurrent_code_edit", rel)
                    if entry["after"] is None:
                        # Only a verified old managed file; never a tree or user data.
                        safe_child(self.target, rel).unlink()
                    else:
                        blob = safe_child(stage, rel).read_bytes()
                        if digest(blob) != entry["after"]:
                            raise DeliveryError("staged_file_changed", rel)
                        atomic_write(self.target, rel, blob, managed=True, expected_current=entry["before"])
                    self.fault("after_write", index)
                for rel, expected in incoming.items():
                    if file_hash(safe_child(self.target, rel)) != expected:
                        raise DeliveryError("post_install_integrity")
                if protected_snapshot(self.target) != protected:
                    raise DeliveryError("userdata_changed")
                self._save("installed.json", {"schema_version": 1, "repository": REPOSITORY,
                           "kind": "ci", "source_sha": evidence.source_sha, "files": incoming,
                           "run_id": evidence.run_id, "run_attempt": evidence.run_attempt,
                           "artifact_digest": evidence.artifact_digest, "transaction": tx,
                           "held_shas": previous.get("held_shas", [])})
                self.fault("after_receipt", len(entries))
                journal["status"] = "committed"
                self._save(journal_rel, journal)
                return {"status": "installed", "sha": evidence.source_sha, "transaction": tx}
            except Exception as exc:
                try:
                    self._restore(tx, journal)
                except Exception:
                    journal["status"] = "recovery_required"
                    self._save(journal_rel, journal)
                    raise DeliveryError("recovery_required") from exc
                raise DeliveryError("install_failed_rolled_back") from exc

    def _restore(self, tx, journal):
        if (not isinstance(journal, dict) or len(tx) != 32 or any(c not in "0123456789abcdef" for c in tx)
                or journal.get("transaction") != tx or journal.get("target") != str(self.target.resolve())):
            raise DeliveryError("invalid_journal")
        entries = journal.get("entries")
        if not isinstance(entries, list):
            raise DeliveryError("invalid_journal")
        previous = self._validate_receipt(journal.get("previous"))
        incoming = journal.get("new_files")
        if not isinstance(incoming, dict) or not incoming or not valid_sha(journal.get("source_sha")):
            raise DeliveryError("invalid_journal")
        for rel, h in incoming.items():
            managed_path(rel)
            if not valid_sha(h, 64):
                raise DeliveryError("invalid_journal")
        if any(not isinstance(e, dict) or set(e) != {"path", "before", "after"} or not isinstance(e["path"], str) for e in entries):
            raise DeliveryError("invalid_journal")
        expected_paths = {p for p in set(previous["files"]) | set(incoming)
                          if previous["files"].get(p) != incoming.get(p)}
        if {e.get("path") for e in entries if isinstance(e, dict)} != expected_paths or len(entries) != len(expected_paths):
            raise DeliveryError("invalid_journal")
        for rel in set(previous["files"]) - expected_paths:
            if file_hash(safe_child(self.target, rel)) != previous["files"][rel]:
                raise DeliveryError("recovery_code_conflict", rel)
        # Validate every target and backup before changing any code.
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"path", "before", "after"}:
                raise DeliveryError("invalid_journal")
            rel = managed_path(entry["path"])
            if entry["before"] != previous["files"].get(rel) or entry["after"] != incoming.get(rel):
                raise DeliveryError("invalid_journal")
            if entry["before"] is not None and not valid_sha(entry["before"], 64):
                raise DeliveryError("invalid_journal")
            if entry["after"] is not None and not valid_sha(entry["after"], 64):
                raise DeliveryError("invalid_journal")
            if file_hash(safe_child(self.target, rel)) not in (entry["before"], entry["after"]):
                raise DeliveryError("recovery_code_conflict", rel)
            if entry["before"] is not None:
                b = safe_child(self.state, "transactions/" + tx + "/backup/" + rel, managed=False)
                if file_hash(b) != entry["before"]:
                    raise DeliveryError("backup_integrity")
        for entry in reversed(entries):
            rel = entry["path"]
            current = file_hash(safe_child(self.target, rel))
            if current not in (entry["before"], entry["after"]):
                raise DeliveryError("recovery_code_conflict", rel)
            if entry["before"] is None:
                p = safe_child(self.target, rel)
                if p.exists():
                    if file_hash(p) != entry["after"]:
                        raise DeliveryError("recovery_code_conflict", rel)
                    p.unlink()
            else:
                blob = safe_child(self.state, "transactions/" + tx + "/backup/" + rel, managed=False).read_bytes()
                if digest(blob) != entry["before"]:
                    raise DeliveryError("backup_integrity")
                atomic_write(self.target, rel, blob, managed=True, expected_current=current)
        for rel, expected in previous["files"].items():
            if file_hash(safe_child(self.target, rel)) != expected:
                raise DeliveryError("rollback_integrity")
        restored = {**journal["previous"], "held_shas": sorted(set(journal["previous"].get("held_shas", [])) | {journal["source_sha"]})}
        self._save("installed.json", restored)
        journal["status"] = "rolled_back"
        self._save("transactions/" + tx + "/journal.json", journal)

    def rollback(self, tx):
        with lease(self.state / "update.lock", exclusive=True):
            self._idle()
            receipt = self._receipt()
            if receipt.get("transaction") != tx:
                raise DeliveryError("rollback_not_current")
            self._unchanged(receipt)
            journal = self._read("transactions/" + tx + "/journal.json")
            if journal.get("status") != "committed":
                raise DeliveryError("recovery_required")
            protected = protected_snapshot(self.target)
            journal["status"] = "rolling_back"
            self._save("transactions/" + tx + "/journal.json", journal)
            try:
                self._restore(tx, journal)
            except Exception:
                journal["status"] = "recovery_required"
                self._save("transactions/" + tx + "/journal.json", journal)
                raise
            if protected_snapshot(self.target) != protected:
                raise DeliveryError("userdata_changed")
            return {"status": "rolled_back", "transaction": tx}

    def recover(self):
        with lease(self.state / "update.lock", exclusive=True):
            self._idle()
            recovered = []
            for tx in self._pending():
                journal = self._read("transactions/" + tx + "/journal.json")
                protected = protected_snapshot(self.target)
                self._restore(tx, journal)
                if protected_snapshot(self.target) != protected:
                    raise DeliveryError("userdata_changed")
                recovered.append(tx)
            return {"status": "recovered", "transactions": recovered}

    def clear_hold(self, source_sha, *, confirmed=False):
        if confirmed is not True or not valid_sha(source_sha):
            raise DeliveryError("hold_confirmation_required")
        with lease(self.state / "update.lock", exclusive=True):
            self._idle()
            if self._pending():
                raise DeliveryError("recovery_required")
            receipt = self._receipt()
            self._unchanged(receipt)
            receipt["held_shas"] = [sha for sha in receipt.get("held_shas", []) if sha != source_sha]
            self._save("installed.json", receipt)
