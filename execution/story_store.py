"""Immutable story material/plan versions and explicit confirmation records."""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path
import json
import time
import uuid
from execution.provenance import (ConfigError, canonical, contained, directory_lock,
    identifier, read_json, sha256, write_bytes, atomic_json, publish_directory)
from execution.output_store import verified
from gapengine.story_materials import verify_material
from gapengine.story_plan import validate_plan, revise_identity

class StoryStore:
    def __init__(self, control):
        self.root = contained(Path(control), "stories")

    def folder(self, kind, value):
        if kind not in ("materials", "plans"):
            raise ConfigError("story", "保存種別が不正です")
        return contained(self.root, kind + "/" + identifier(value))

    def _publish(self, kind, value, files):
        parent = contained(self.root, kind)
        with directory_lock(parent):
            final = self.folder(kind, value)
            if final.exists():
                seal = read_json(final / "seal.json")
                if seal["files"] != {p: sha256(b) for p, b in files.items()}:
                    raise ConfigError("story", "同じ版IDの内容が異なります", code="conflict")
                self._read(kind, value)
                return
            pending = contained(parent, ".pending-" + uuid.uuid4().hex)
            pending.mkdir()
            for p, b in files.items():
                write_bytes(contained(pending, p), b)
            atomic_json(pending / "seal.json", {"schema_version": 1,
                "files": {p: sha256(b) for p, b in files.items()}})
            publish_directory(pending, final)

    def _read(self, kind, value):
        folder = self.folder(kind, value)
        seal = read_json(folder / "seal.json")
        blobs = {p: verified(contained(folder, p), h) for p, h in seal["files"].items()}
        name = "materials.json" if kind == "materials" else "plan.json"
        doc = json.loads(blobs[name])
        if doc.get("material_id" if kind == "materials" else "plan_id") != value:
            raise ConfigError("story", "版IDが一致しません", code="snapshot_changed")
        if kind == "materials":
            verify_material(doc)
        return doc, blobs

    def save_material(self, material, *, artifacts):
        verify_material(material)
        files = {**artifacts, "materials.json": canonical(material)}
        # Each binding must correspond to exact captured bytes.
        raw = files.get("inputs/layers.jsonl")
        if raw is None or sha256(raw) != material["source_binding"]["source_log_sha256"]:
            raise ConfigError("source", "元ログが素材と一致しません")
        for r in material["source_binding"]["input_files"]:
            if sha256(files.get("inputs/" + r["path"], b"")) != r["sha256"]:
                raise ConfigError("source", "世界設定が素材と一致しません")
        self._publish("materials", material["material_id"], files)
        return material

    def material(self, material_id):
        return self._read("materials", material_id)[0]

    def plan(self, plan_id):
        plan = self._read("plans", plan_id)[0]
        validate_plan(self.material(plan["material_ref"]["material_id"]), plan)
        return plan

    def create_plan(self, material, plan):
        validate_plan(material, plan)
        self._publish("plans", plan["plan_id"], {"plan.json": canonical(plan)})
        return plan

    def current(self, run_id, candidate_id):
        path = contained(self.root, "heads/" + identifier(run_id) + "/" + identifier(candidate_id) + ".json")
        try:
            doc = read_json(path)
        except FileNotFoundError:
            return None
        plan = self.plan(doc["plan_id"])
        return {"plan": plan, "plan_sha256": sha256(canonical(plan)),
                "confirmed": self.confirmation(plan["plan_id"], required=False) is not None}

    def set_current(self, run_id, candidate_id, plan, *, expected_revision):
        path = contained(self.root, "heads/" + identifier(run_id) + "/" + identifier(candidate_id) + ".json")
        with directory_lock(self.root):
            current = self.current(run_id, candidate_id)
            revision = current["plan"]["revision"] if current else 0
            if expected_revision != revision or plan["revision"] != revision + 1:
                raise ConfigError("revision", "構成案が更新されています", code="conflict")
            material = self.material(plan["material_ref"]["material_id"])
            if any(material["source_binding"].get(k) != v for k, v in (("run_id", run_id), ("candidate_id", candidate_id))):
                raise ConfigError("source", "別候補の構成案です")
            self.create_plan(material, plan)
            atomic_json(path, {"plan_id": plan["plan_id"]})
        return self.current(run_id, candidate_id)

    def edit(self, run_id, candidate_id, changes, *, expected_revision):
        if type(expected_revision) is not int or expected_revision < 1:
            raise ConfigError("revision", "構成案の整数の版を指定してください")
        current = self.current(run_id, candidate_id)
        if current is None:
            raise ConfigError("plan", "先に素材と構成案を作ってください")
        allowed = {"focus", "viewpoint", "tone", "beats", "required_event_ids", "narrative_threads", "omissions", "open_threads"}
        if not isinstance(changes, dict) or set(changes) - allowed:
            raise ConfigError("plan", "編集できない項目があります")
        edits=deepcopy(changes)
        if "omissions" in edits:
            supplied=edits["omissions"]
            if not isinstance(supplied,list) or any(not isinstance(o,dict) or not isinstance(o.get("event_ids"),list) or not o["event_ids"] or any(not isinstance(i,str) for i in o["event_ids"]) for o in supplied):
                raise ConfigError("omissions","省略する出来事IDと理由の配列を指定してください")
            flat=[i for o in supplied for i in o["event_ids"]]
            if len(set(flat))!=len(flat):
                raise ConfigError("omissions","省略する出来事IDが重複しています")
            replaced=set(flat)
            beats=edits.get("beats",current["plan"]["beats"])
            if not isinstance(beats,list) or any(not isinstance(b,dict) or not isinstance(b.get("event_ids"),list) or any(not isinstance(i,str) for i in b["event_ids"]) for b in beats):
                raise ConfigError("beats","場面の出来事ID配列を指定してください")
            selected={i for b in beats for i in b["event_ids"]}
            remaining=[{**o,"event_ids":[i for i in o["event_ids"] if i not in replaced|selected]} for o in current["plan"]["omissions"]]
            edits["omissions"]=[o for o in remaining if o["event_ids"]]+supplied
        if "required_event_ids" in edits and "narrative_threads" not in edits:
            keep=set(edits["required_event_ids"])
            edits["narrative_threads"]=[{**t,"event_ids":[i for i in t["event_ids"] if i in keep]}
                for t in current["plan"].get("narrative_threads",[]) if any(i in keep for i in t["event_ids"])]
        plan = {**deepcopy(current["plan"]), **edits, "revision": expected_revision + 1, "status": "draft"}
        plan = revise_identity(plan)
        return self.set_current(run_id, candidate_id, plan, expected_revision=expected_revision)

    def confirm(self, plan_id, expected_sha256, *, method="user"):
        with directory_lock(self.root):
            return self._confirm_locked(plan_id, expected_sha256, method=method)

    def _confirm_locked(self, plan_id, expected_sha256, *, method="user"):
        folder = self.folder("plans", plan_id)
        plan = self.plan(plan_id)
        material = self.material(plan["material_ref"]["material_id"])
        if sha256(canonical(plan)) != expected_sha256:
            raise ConfigError("plan", "確認する構成案のSHAが一致しません", code="conflict")
        current = self.current(material["source_binding"]["run_id"], material["source_binding"]["candidate_id"])
        if not current or current["plan"]["plan_id"] != plan_id:
            raise ConfigError("plan", "古い構成案は確認できません", code="conflict")
        with directory_lock(folder):
            existing = self.confirmation(plan_id, required=False)
            if existing:
                return existing
            doc = {"schema_version": 1, "plan_id": plan_id, "plan_sha256": expected_sha256,
                "material_sha256": material["content_sha256"], "method": method, "confirmed_at": time.time()}
            atomic_json(folder / "confirmation.json", doc)
            atomic_json(folder / "confirmation-seal.json", {"sha256": sha256(canonical(doc))})
        return doc

    def confirmation(self, plan_id, *, required=True):
        folder = self.folder("plans", plan_id)
        try:
            seal = read_json(folder / "confirmation-seal.json")
        except FileNotFoundError:
            if required:
                raise ConfigError("plan", "構成案の確認が必要です")
            return None
        doc = json.loads(verified(folder / "confirmation.json", seal["sha256"]))
        plan = self._read("plans", plan_id)[0]
        if (doc.get("schema_version") != 1 or doc.get("plan_id") != plan_id
                or doc.get("plan_sha256") != sha256(canonical(plan))
                or doc.get("material_sha256") != plan["material_ref"]["content_sha256"]):
            raise ConfigError("plan", "確認記録が一致しません", code="snapshot_changed")
        return doc

    def reference(self, plan_id):
        plan = self.plan(plan_id)
        confirmation = self.confirmation(plan_id)
        return {"material_id": plan["material_ref"]["material_id"],
            "material_sha256": plan["material_ref"]["content_sha256"],
            "plan_id": plan_id, "plan_sha256": sha256(canonical(plan)),
            "confirmation_sha256": sha256(canonical(confirmation))}

    def resolve(self, ref, *, binding=None):
        if not isinstance(ref, dict) or set(ref) != {"material_id", "material_sha256", "plan_id", "plan_sha256", "confirmation_sha256"}:
            raise ConfigError("story_refs", "素材・構成案・確認の版を指定してください")
        material, material_blobs = self._read("materials", ref["material_id"])
        plan = self.plan(ref["plan_id"])
        confirmation = self.confirmation(ref["plan_id"])
        if self.reference(ref["plan_id"]) != ref or plan["material_ref"]["material_id"] != material["material_id"]:
            raise ConfigError("story_refs", "確認済み骨格と一致しません", code="snapshot_changed")
        current = self.current(material["source_binding"]["run_id"], material["source_binding"]["candidate_id"])
        if not current or current["plan"]["plan_id"] != plan["plan_id"]:
            raise ConfigError("story_refs", "構成案が更新されています", code="conflict")
        if binding and any(material["source_binding"].get(k) != v for k, v in binding.items()):
            raise ConfigError("story_refs", "別候補・別設定の素材です", code="snapshot_changed")
        return material, plan, confirmation

    def list(self, run_id=None):
        rows = []
        root = contained(self.root, "heads")
        if root.exists():
            for p in sorted(root.glob("*/*.json")):
                if run_id is None or p.parent.name == run_id:
                    current = self.current(p.parent.name, p.stem)
                    rows.append({"run_id": p.parent.name, "candidate_id": p.stem, **current})
        return rows
