"""Immutable supplement/input/style versions with captured source bytes."""
import json
import uuid
from execution.provenance import ConfigError, canonical, contained, directory_lock, read_json, sha256, write_bytes, atomic_json, publish_directory
from execution.output_store import verified
from gapengine.fact_contract import verify_package, extract_supplement


def verify_sources(package, raw, inputs):
    supplement = package["supplement"]
    binding = supplement["source_binding"]
    base = {k: v for k, v in binding.items() if k not in {"source_log_sha256", "input_files"}}
    if extract_supplement(raw, inputs, base) != supplement:
        raise ConfigError("facts", "補助資料が固定した原ログ・設定と一致しません", code="snapshot_changed")


class FactStore:
    def __init__(self, control):
        self.root = contained(control, "fact-packages")

    def save(self, package, *, raw, inputs):
        verify_package(package)
        verify_sources(package, raw, inputs)
        self._verify_synopsis(package)
        files = {"package.json": canonical(package), "sources/layers.jsonl": raw,
                 **{"sources/inputs/" + p: b for p, b in inputs.items()}}
        with directory_lock(self.root):
            final = contained(self.root, package["id"])
            if final.exists():
                self.resolve(self.reference(package))
                return self.reference(package)
            pending = contained(self.root, ".pending-" + uuid.uuid4().hex)
            pending.mkdir()
            for p, b in files.items():
                write_bytes(contained(pending, p), b)
            atomic_json(pending / "seal.json", {"schema_version": 1, "files": {p: sha256(b) for p, b in files.items()}})
            publish_directory(pending, final)
        return self.reference(package)

    def _verify_synopsis(self, package):
        synopsis = package["synopsis"]
        if synopsis is None:
            return
        from execution.output_store import OutputStore
        from execution.fact_service import synopsis_from_output, pinned_package
        store = OutputStore(self.root.parent)
        cid, oid = synopsis["candidate_id"], synopsis["output_id"]
        if synopsis_from_output(store, oid, cid) != synopsis:
            raise ConfigError("synopsis", "あらすじの固定参照が一致しません")
        req = store.request(oid)
        prior = pinned_package(store.folder(oid), cid, req)
        if (prior["supplement"] != package["supplement"] or prior["selection"] != package["selection"]):
            raise ConfigError("synopsis", "異なる原記録・選択・開示範囲のあらすじです")

    @staticmethod
    def reference(package):
        return {"package_id": package["id"], "package_sha256": package["sha256"]}

    def resolve(self, ref, *, binding=None):
        from execution.provenance import identifier
        folder = contained(self.root, identifier(ref["package_id"]))
        seal = read_json(contained(folder, "seal.json"))
        if seal.get("schema_version") != 1:
            raise ConfigError("facts", "未対応の保存版です")
        blobs = {p: verified(contained(folder, p), h) for p, h in seal["files"].items()}
        package = json.loads(blobs["package.json"])
        verify_package(package)
        if self.reference(package) != ref:
            raise ConfigError("facts", "生成入力の参照が一致しません", code="snapshot_changed")
        source = package["supplement"]["source_binding"]
        expected = {"package.json", "sources/layers.jsonl"} | {"sources/inputs/" + p for p in source["input_files"]}
        if set(blobs) != expected:
            raise ConfigError("facts", "保存された原記録のファイル集合が一致しません")
        if any(source.get(k) != v for k, v in (binding or {}).items()):
            raise ConfigError("facts", "別の候補・原ログ・設定の入力です", code="snapshot_changed")
        inputs = {p: blobs["sources/inputs/" + p] for p in source["input_files"]}
        verify_sources(package, blobs["sources/layers.jsonl"], inputs)
        self._verify_synopsis(package)
        return package
