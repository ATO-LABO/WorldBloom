"""Build an immutable facts_v1 input from an existing run; no model calls."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from execution.configs import ConfigStore
from execution.provenance import canonical, contained, sha256, ConfigError
from execution.fact_store import FactStore
from gapengine.fact_contract import extract_supplement, make_package, build_prompt
from viewer.run_catalog import RunCatalog


def load_supplement(configs, catalog, run_id, candidate_id, config_id):
    config, manifest, cfgroot = configs._bundle(config_id)
    root, legacy = catalog.resolve(run_id)
    if not legacy:
        from execution.provenance import read_json
        recorded = read_json(contained(root, "manifest.json"))
        if recorded["input_manifest_sha256"] != config["input_manifest_sha256"]:
            raise ConfigError("config_id", "実行時と異なる設定は利用できません")
    snapshot = catalog.snapshot(run_id)
    candidate = next((c for c in snapshot["candidates"]["candidates"] if c["candidate_id"] == candidate_id), None)
    if candidate is None:
        raise ConfigError("candidate_id", "候補がありません")
    raw = contained(root, candidate["log"]["relative_path"]).read_bytes()
    if sha256(raw) != candidate["source_log_sha256"]:
        raise ConfigError("source", "元ログが変更されています", code="snapshot_changed")
    inputs = {r["path"]: contained(cfgroot / "inputs", r["path"]).read_bytes() for r in manifest["files"]}
    supplement = extract_supplement(raw, inputs, {"run_id": run_id, "candidate_id": candidate_id,
        "config_sha256": sha256(canonical(config)), "input_manifest_sha256": config["input_manifest_sha256"]})
    return raw, inputs, supplement


def organize(configs, catalog, run_id, candidate_id, config_id, selection, styles=None):
    """Explicit source selection; unsupported legacy meanings stay unknown."""
    raw, inputs, supplement = load_supplement(configs, catalog, run_id, candidate_id, config_id)
    if (not isinstance(selection, dict) or not {"selected", "required"} <= set(selection)
            or set(selection) - {"selected", "required", "hidden", "viewpoint"}):
        raise ConfigError("selection", "採用・必須・非開示の指定が必要です")
    package = make_package(supplement, **selection, styles=styles)
    # Both stages must be reviewable before this immutable version is published.
    for kind in ("synopsize", "narrate"):
        build_prompt(package, kind)
    ref = FactStore(configs.control).save(package, raw=raw, inputs=inputs)
    return {"reference": ref, "package": package}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--styles", type=Path)
    args = parser.parse_args()
    configs = ConfigStore(args.repo, args.control, args.runs)
    result = organize(configs, RunCatalog(args.runs, args.control), args.run, args.candidate, args.config,
                      json.loads(args.selection.read_text(encoding="utf-8")),
                      json.loads(args.styles.read_text(encoding="utf-8")) if args.styles else None)
    print(json.dumps(result["reference"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
