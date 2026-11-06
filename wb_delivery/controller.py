"""Explicit check/apply coordinator. No timers, auth setup, or activity on import."""
from pathlib import Path

from .bundle import verify_and_stage, _snapshot
from .contract import is_link, safe_child
from .errors import DeliveryError
from .github_reader import GitHubReader
from .preflight import CopiedDataValidator, WindowsRuntimeProbe
from .transaction import Installer


class Controller:
    def __init__(self, config, *, reader_factory=GitHubReader, runtime_probe=None, compatibility=None):
        self.config = dict(config)
        self.reader_factory = reader_factory
        self.runtime_probe = runtime_probe
        self.compatibility = compatibility

    def check_once(self, *, apply_update=False, source_sha=None):
        if self.config.get("enabled") is not True:
            return {"status": "disabled", "can_launch": True}
        if apply_update and self.config.get("allow_apply") is not True:
            return {"status": "apply_disabled", "can_launch": True}
        target, state = self.config.get("target"), self.config.get("state")
        if not isinstance(target, str) or not isinstance(state, str) or not Path(target).is_absolute() or not Path(state).is_absolute():
            raise DeliveryError("local_config_paths")
        reader = self.reader_factory(self.config)
        installer = Installer(target, state,
            runtime_probe=self.runtime_probe or WindowsRuntimeProbe(),
            compatibility=self.compatibility or CopiedDataValidator(Path(state) / "validation"),
            forward_check=reader.compare)
        # A previous partial transaction must never become a silent launch/update.
        if installer._pending():
            return {"status": "recovery_required", "can_launch": False}
        try:
            evidence = reader.resolve(source_sha)
        except DeliveryError as exc:
            return {"status": "update_unavailable", "code": exc.code, "can_launch": True}
        try:
            installed = installer._receipt()
        except DeliveryError as exc:
            if exc.code == "not_enrolled":
                return {"status": "first_enrollment_required", "sha": evidence.source_sha, "can_launch": True}
            raise
        try:
            installer._unchanged(installed)
        except DeliveryError as exc:
            if exc.code == "local_code_edits":
                return {"status": "local_changes", "can_launch": True}
            raise
        if evidence.source_sha in installed.get("held_shas", []):
            return {"status": "rollback_hold", "sha": evidence.source_sha, "can_launch": True}
        if installed["kind"] == "ci" and installed["source_sha"] == evidence.source_sha:
            return {"status": "current", "sha": evidence.source_sha, "can_launch": True}
        if installed["kind"] == "local":
            if installed.get("allowed_first_sha") != evidence.source_sha:
                return {"status": "first_sha_not_approved", "sha": evidence.source_sha, "can_launch": True}
        else:
            try:
                forward = reader.compare(installed["source_sha"], evidence.source_sha)
            except DeliveryError as exc:
                return {"status": "update_unavailable", "code": exc.code, "can_launch": True}
            if forward is not True:
                return {"status": "not_forward_update", "sha": evidence.source_sha, "can_launch": True}
        archive = safe_child(Path(state), "downloads/" + str(evidence.artifact_id) + "-" + evidence.artifact_digest + ".zip", managed=False)
        archive.parent.mkdir(parents=True, exist_ok=True)
        try:
            if archive.exists():
                if is_link(archive):
                    raise DeliveryError("linked_download")
                _snapshot(archive, evidence)
            else:
                reader.download(evidence, archive)
            verify_and_stage(archive, evidence)
        except DeliveryError as exc:
            return {"status": "artifact_unavailable", "code": exc.code, "can_launch": True}
        if not apply_update:
            return {"status": "available", "sha": evidence.source_sha, "can_launch": True}
        try:
            result = installer.install(archive, evidence)
            return {**result, "can_launch": True}
        except DeliveryError as exc:
            if exc.code in ("in_use", "in_use_or_unknown", "runtime_unknown"):
                return {"status": "pending_in_use", "sha": evidence.source_sha, "can_launch": True}
            if installer._pending():
                return {"status": "recovery_required", "code": exc.code, "can_launch": False}
            return {"status": "update_blocked", "code": exc.code, "can_launch": True}
