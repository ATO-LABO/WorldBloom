"""Validate a GitHub artifact snapshot and stage only explicitly managed code."""
from io import BytesIO
from pathlib import Path
import stat
import zipfile

from .contract import (MAX_ARCHIVE, MAX_EXPANDED, MAX_FILE, MAX_FILES, MAX_MANIFEST,
                       MANIFEST_NAME, canonical, digest, managed_path,
                       normalized_path, safe_child, strict_json, validate_manifest, is_link)
from .errors import DeliveryError


def _snapshot(path, evidence):
    data = bytearray()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            if len(data) + len(chunk) > MAX_ARCHIVE:
                raise DeliveryError("archive_too_large")
            data.extend(chunk)
    snapshot = bytes(data)
    if len(snapshot) != evidence.artifact_size or digest(snapshot) != evidence.artifact_digest:
        raise DeliveryError("artifact_integrity")
    return snapshot


def verify_and_stage(archive, evidence, stage=None):
    """No application writes. The parsed bytes are the exact API-digested bytes."""
    snapshot = _snapshot(archive, evidence)
    try:
        z = zipfile.ZipFile(BytesIO(snapshot))
    except (ValueError, zipfile.BadZipFile) as exc:
        raise DeliveryError("invalid_zip") from exc
    with z:
        infos = z.infolist()
        if len(infos) > MAX_FILES * 2 + 10:
            raise DeliveryError("too_many_entries")
        seen, members, directories, total = set(), {}, set(), 0
        for info in infos:
            rel = info.filename.rstrip("/") if info.is_dir() else info.filename
            normalized_path(rel)
            key = rel.casefold()
            if key in seen or info.flag_bits & 1:
                raise DeliveryError("duplicate_or_encrypted_zip_entry")
            seen.add(key)
            kind = stat.S_IFMT(info.external_attr >> 16)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or info.external_attr & 0x400:
                raise DeliveryError("linked_zip_entry")
            if info.is_dir():
                if rel != "app" and not any(p.startswith(rel + "/") for p in ("app/viewer", "app/execution", "app/engine", "app/gapengine", "app/scripts")):
                    # Nested directories are allowed only beneath a managed code root.
                    managed_path(rel + "/.directory-placeholder")
                directories.add(key)
                continue
            if rel != MANIFEST_NAME:
                managed_path(rel)
            limit = MAX_MANIFEST if rel == MANIFEST_NAME else MAX_FILE
            if info.file_size > limit or info.file_size < 0:
                raise DeliveryError("zip_file_too_large")
            total += info.file_size
            if total > MAX_EXPANDED + MAX_MANIFEST:
                raise DeliveryError("zip_expansion_limit")
            members[rel] = info
        for rel in members:
            bits = rel.split("/")
            if any("/".join(bits[:n]).casefold() in {p.casefold() for p in members} for n in range(1, len(bits))):
                raise DeliveryError("zip_file_directory_collision")
        if MANIFEST_NAME not in members:
            raise DeliveryError("missing_manifest")
        try:
            manifest = validate_manifest(strict_json(z.read(members[MANIFEST_NAME])), evidence)
        except (RuntimeError, zipfile.BadZipFile, OSError) as exc:
            raise DeliveryError("zip_manifest_read") from exc
        expected = {f["path"]: f for f in manifest["files"]}
        if set(members) != set(expected) | {MANIFEST_NAME}:
            raise DeliveryError("zip_manifest_members")
        if stage is not None:
            stage = Path(stage).absolute()
            if any(is_link(p) for p in [stage, *stage.parents]):
                raise DeliveryError("linked_stage")
            if stage.exists():
                raise DeliveryError("stage_exists")
        # Verify every member before creating even the staging directory.
        for rel, entry in expected.items():
            info = members[rel]
            if info.file_size != entry["size"]:
                raise DeliveryError("file_size_mismatch")
            try:
                blob = z.read(info)
            except (RuntimeError, zipfile.BadZipFile, OSError) as exc:
                raise DeliveryError("zip_crc") from exc
            if len(blob) != entry["size"] or digest(blob) != entry["sha256"]:
                raise DeliveryError("file_integrity")
        if stage is None:
            return manifest
        stage.mkdir(parents=True, exist_ok=False)
        for rel, entry in expected.items():
            path = safe_child(stage, rel)
            path.parent.mkdir(parents=True, exist_ok=True)
            # zipfile is reading an immutable bytes snapshot; no extractall/path rewriting.
            path.write_bytes(z.read(members[rel]))
        safe_child(stage, MANIFEST_NAME, managed=False).write_bytes(canonical(manifest))
        return manifest
