"""Author drafts and optimistic, append-only fact/prompt version history."""
from copy import deepcopy
from pathlib import Path
import json
import time
from execution.provenance import ConfigError, canonical, contained, directory_lock, read_json, sha256, write_bytes, atomic_json
from execution.output_store import verified, OutputStore
from execution.fact_store import FactStore
from execution.fact_service import synopsis_from_output
from gapengine.fact_contract import COMMON, VERSION, DEFAULTS, make_package, packet, build_prompt, style_findings
from scripts.fact_materials import load_supplement


class FactWorkspace:
    def __init__(self, configs, catalog):
        self.configs, self.catalog = configs, catalog
        self.root = contained(configs.control, "fact-workspaces")
        self.facts = FactStore(configs.control)

    def folder(self, rid, cid):
        from execution.provenance import identifier
        return contained(self.root, identifier(rid) + "/" + identifier(cid))

    def current(self, rid, cid, revision=None):
        folder = self.folder(rid, cid)
        try:
            head = read_json(folder / "current.json")
        except FileNotFoundError:
            if revision is not None:
                raise ConfigError("revision", "保存済みの版がありません", code="not_found")
            return None
        if (not isinstance(head, dict) or set(head) != {"schema_version", "revision", "sha256"}
                or type(head["schema_version"]) is not int or head["schema_version"] != 1
                or type(head["revision"]) is not int or head["revision"] < 1):
            raise ConfigError("history", "保存履歴の版が不正です")
        if revision is not None and (type(revision) is not int or not 1 <= revision <= head["revision"]):
            raise ConfigError("revision", "保存履歴にない版です", code="not_found")
        selected = head["revision"] if revision is None else revision
        # Walk only the sealed published chain; orphaned revision files are not history.
        expected = head["sha256"]
        for number in range(head["revision"], selected-1, -1):
            doc = json.loads(verified(folder / "revisions" / (str(number) + ".json"), expected))
            if (type(doc.get("schema_version")) is not int or doc["schema_version"] != 1 or type(doc.get("revision")) is not int
                    or doc["revision"] != number or doc.get("run_id") != rid or doc.get("candidate_id") != cid):
                raise ConfigError("history", "保存履歴の参照が一致しません")
            expected = doc["parent_sha256"]
        package = self.facts.resolve(doc["reference"], binding={"run_id": rid, "candidate_id": cid})
        return {**doc, "package": package, "current_revision": head["revision"]}

    def context(self, rid, cid, config_id, revision=None):
        saved = self.current(rid, cid, revision)
        if saved is not None:
            config_id = saved["config_id"]
        package = saved["package"] if saved else None
        stale = False
        try:
            raw, inputs, supplement = load_supplement(self.configs, self.catalog, rid, cid, config_id)
            stale = package is not None and canonical(package["supplement"]) != canonical(supplement)
        except (OSError, ValueError):
            if package is None:
                raise
            supplement, stale = deepcopy(package["supplement"]), True
        if revision is not None and package is not None:
            supplement = deepcopy(package["supplement"])
        records = supplement["records"]
        selection = deepcopy(package["selection"] if package else {
            "selected": [r["id"] for r in records], "required": [], "hidden": [],
            "viewpoint": "第三者。人物の知識と読者への開示を区別する"})
        return {"config_id": config_id, "saved": saved, "supplement": supplement,
                "selection": selection, "styles": deepcopy(package["styles"] if package else DEFAULTS),
                "source_changed": stale, "defaults": deepcopy(DEFAULTS), "common": COMMON, "common_version": VERSION}

    def _draft(self, rid, cid, body):
        if (not isinstance(body, dict) or not {"config_id", "selection", "styles"} <= set(body)
                or set(body) - {"config_id", "selection", "styles", "synopsis_output_id"}):
            raise ConfigError("request", "設定・入力選択・表現設定を指定してください")
        raw, inputs, supplement = load_supplement(self.configs, self.catalog, rid, cid, body["config_id"])
        selection = body["selection"]
        if not isinstance(selection, dict) or set(selection) != {"selected", "required", "hidden", "viewpoint"}:
            raise ConfigError("selection", "採用・必須・非開示・視点の指定が必要です")
        synopsis = None
        if body.get("synopsis_output_id"):
            synopsis = synopsis_from_output(OutputStore(self.configs.control), body["synopsis_output_id"], cid)
        package = make_package(supplement, **selection, styles=body["styles"], synopsis=synopsis)
        # Preview checks the same synopsis binding as publication, without writing.
        self.facts._verify_synopsis(package)
        return raw, inputs, package

    def preview(self, rid, cid, body):
        _, _, package = self._draft(rid, cid, body)
        conflicts = {k: style_findings(v) for k, v in package["styles"].items()}
        return {"common": COMMON, "common_version": VERSION, "styles": package["styles"],
                "style_version": package["style_version"], "conflicts": conflicts,
                "style_review_status": "not_reviewed", "source_binding": package["supplement"]["source_binding"],
                "inputs": {k: packet(package, k) for k in DEFAULTS},
                "prompts": {k: None if conflicts[k] else build_prompt(package, k) for k in DEFAULTS},
                "package_sha256": package["sha256"]}

    def save(self, rid, cid, body, expected_revision):
        if type(expected_revision) is not int or expected_revision < 0:
            raise ConfigError("revision", "保存する設定の版が不正です")
        folder = self.folder(rid, cid)
        with directory_lock(folder):
            previous = self.current(rid, cid)
            revision = previous["revision"] if previous else 0
            if revision != expected_revision:
                raise ConfigError("revision", "別の編集が保存されています。入力を保持して現行版を確認してください", code="conflict")
            raw, inputs, package = self._draft(rid, cid, body)
            for kind in DEFAULTS:
                build_prompt(package, kind)
            reference = self.facts.save(package, raw=raw, inputs=inputs)
            doc = {"schema_version": 1, "run_id": rid, "candidate_id": cid, "config_id": body["config_id"],
                   "revision": revision+1, "parent_sha256": sha256(canonical({k:v for k,v in previous.items() if k not in {"package", "current_revision"}})) if previous else None,
                   "reference": reference, "saved_at": time.time()}
            path = folder / "revisions" / (str(revision+1) + ".json")
            if path.exists():
                raise ConfigError("history", "未公開の保存記録があります。履歴を保全して確認してください", code="conflict")
            write_bytes(path, canonical(doc))
            atomic_json(folder / "current.json", {"schema_version": 1, "revision": revision+1, "sha256": sha256(canonical(doc))})
        return self.current(rid, cid)

    def history(self, rid, cid):
        head = self.current(rid, cid)
        if head is None:
            return []
        return [{k: v for k,v in self.current(rid, cid, n).items() if k not in {"package", "current_revision"}}
                for n in range(head["revision"], 0, -1)]

    def generate(self, jobs, rid, cid, body, settings_path):
        if not isinstance(body, dict) or set(body) != {"expected_revision", "package_sha256", "kind", "request_id"} or body["kind"] not in DEFAULTS:
            raise ConfigError("request", "保存済み版と生成種別を指定してください")
        if type(body["expected_revision"]) is not int:
            raise ConfigError("revision", "保存済み版を指定してください")
        with directory_lock(self.folder(rid, cid)):
            saved = self.current(rid, cid)
            if (saved is None or body["expected_revision"] != saved["revision"]
                    or body["package_sha256"] != saved["reference"]["package_sha256"]):
                raise ConfigError("revision", "保存済み設定が変わっています", code="conflict")
            from execution.output_settings import resolve_generation
            from execution.selections import SelectionStore
            selection = SelectionStore(self.catalog).get(rid)
            request = {"schema_version": 3, "pipeline": "facts_v1", "fact_refs": {cid: saved["reference"]},
                       "request_id": body["request_id"], "kind": body["kind"], "run_id": rid,
                       "config_id": saved["config_id"], "selection_revision": selection["revision"], "candidate_ids": [cid],
                       **resolve_generation(settings_path), "mode": "regenerate", "synopsis_refs": {}}
            job, created = jobs.submit(request, settings_path=settings_path)
            # Preparation owns creation of the output folder; it may not exist
            # immediately after admission. The job surface polls that lifecycle.
            return {"job": job, "created": created, "url": "/jobs/" + job["job_id"]}
