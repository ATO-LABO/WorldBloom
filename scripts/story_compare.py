"""Prepare and run a saved-candidate comparison; never runs GA or Jev.

Preparation requires explicit --confirm-plans. Execution skips every started
attempt, including unknown results. It never retries an unknown request.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from execution.provenance import (ConfigError,atomic_json,canonical,sha256,code_snapshot,
    contained,identifier,materialize,python_executable,read_json,write_bytes)
from execution.story_service import organize_experiment,prepare_standalone,process_story_output
from execution.story_store import StoryStore
from execution.output_store import OutputStore,verified

def prepare(args):
    folder=contained(args.control,"stories/comparisons/"+identifier(args.comparison_id))
    if folder.exists():raise ConfigError("comparison","比較の保存先が既にあります",code="conflict")
    if not args.confirm_plans:raise ConfigError("plan","構成案のプロトコル確認を明示してください")
    items=[(p,read_json(p)) for p in sorted(args.selected_dir.glob("*.json"))]
    items.sort(key=lambda pair:pair[1]["blind_id"])
    if len(items)!=args.expected_count or len({d["blind_id"] for _,d in items})!=len(items):
        raise ConfigError("source","元候補の件数・匿名IDが一致しません")
    for _,d in items:
        if not re.fullmatch(r"作品[0-9]{2}",d["blind_id"]):raise ConfigError("source","匿名IDが不正です")
        raw=Path(d["layers_path"]).read_bytes()
        header=next((json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()),{})
        if sha256(raw)!=d["layers_sha256"] or header.get("seed")!=d["seed"]:
            raise ConfigError("source","元ログ・seedが一致しません")
        if not (args.old_reader/(d["blind_id"]+".md")).is_file():raise ConfigError("source","旧本文がありません")
    code,origin=code_snapshot(args.repo)
    runtime=folder/"source-runtime";materialize(runtime,code)
    atomic_json(folder/"origin-runtime-manifest.json",origin)
    from execution.output_worker import _output_settings
    output=_output_settings(args.settings)
    local=output.get("ollama",{})
    safe={k:local[k] for k in ("base_url","options","think","seed") if k in local}
    safe["model"]=args.model
    settings={"output":{"default_backend":"ollama","ollama":safe}}
    if "gpu_guard" in output:settings["output"]["gpu_guard"]=output["gpu_guard"]
    atomic_json(folder/"settings.json",settings)
    records=[];runtime_sha=None
    for selected,item in items:
        current=organize_experiment(args.control,runtime,selected,args.project,args.template,
            run_id=args.comparison_id)
        plan=current["plan"];stories=StoryStore(args.control)
        material=stories.material(plan["material_ref"]["material_id"])
        digest=material["source_binding"]["runtime_manifest_sha256"]
        if runtime_sha is not None and digest!=runtime_sha:raise ConfigError("runtime","比較内のコード版が違います")
        runtime_sha=digest
        stories.confirm(plan["plan_id"],current["plan_sha256"],method="cli_protocol_confirmation")
        request=prepare_standalone(args.control,plan["plan_id"],backend="ollama",model=args.model,
            limits={"max_calls":1,"call_timeout_seconds":1800,"wall_seconds":2100,"max_saved_response_bytes":1048576})
        blind=item["blind_id"];old=(args.old_reader/(blind+".md")).read_bytes()
        write_bytes(folder/(blind+"-old.txt"),old)
        write_bytes(folder/(blind+"-selected.json"),selected.read_bytes())
        records.append({"blind_id":blind,"candidate_id":item["internal"],"output_id":request["output_id"],
            "source_log_sha256":item["layers_sha256"],"seed":item["seed"],"generation":item["generation"],
            "individual":item["individual"],"old_text_sha256":sha256(old),"old_file":blind+"-old.txt",
            "selected_sha256":sha256(selected.read_bytes()),"material_id":material["material_id"],
            "plan_id":plan["plan_id"],"required_count":len(plan["required_event_ids"]),"beat_count":len(plan["beats"])})
        print(json.dumps({"prepared":blind,"beats":len(plan["beats"]),"required":len(plan["required_event_ids"])}),flush=True)
    from gapengine.story_narration import VERSION
    manifest={"schema_version":1,"comparison_id":args.comparison_id,"entries":records,"runtime_manifest_sha256":runtime_sha,
        "prompt_version":VERSION,"backend":"ollama","model":args.model,"settings_sha256":sha256(canonical(settings)),
        "length_policy":"unbounded","baseline_label":args.baseline_label,"calls_per_candidate":1,"confirmation":"cli_protocol_confirmation",
        "execution_budget_origin":"cli_execution_start",
        "human_adoption":"pending","interpretation_limit":"旧・新の差にはモデル・プロンプト・生成の揺れを含む。GA優位の再実験ではない。"}
    atomic_json(folder/"manifest.json",manifest)
    atomic_json(folder/"manifest-seal.json",{"sha256":sha256(canonical(manifest))})
    return manifest

def load(control,value):
    folder=contained(control,"stories/comparisons/"+identifier(value))
    seal=read_json(folder/"manifest-seal.json")
    manifest=json.loads(verified(folder/"manifest.json",seal["sha256"]))
    if manifest["comparison_id"]!=value:raise ConfigError("comparison","比較IDが一致しません")
    for row in manifest["entries"]:
        verified(contained(folder,row["old_file"]),row["old_text_sha256"])
    return folder,manifest

def execute(args):
    folder,manifest=load(args.control,args.comparison_id)
    settings=read_json(folder/"settings.json")
    if sha256(canonical(settings))!=manifest["settings_sha256"]:raise ConfigError("settings","比較の生成条件が変更されています")
    outputs=OutputStore(args.control)
    for entry in manifest["entries"]:
        oid,cid=entry["output_id"],entry["candidate_id"]
        root=outputs.folder(oid);outputs.verify_artifacts(oid)
        if not (root/"worker-started.json").exists():
            command=[python_executable(),"-I","-B",str(root/"runtime/scripts/story_narrate.py"),
                "--control",str(args.control),"--execute",oid,"--settings",str(folder/"settings.json")]
            # Dispatch exclusively the sealed runtime; a failed child is not retried.
            with (folder/(entry["blind_id"]+"-execution.txt")).open("xb") as log:
                completed=subprocess.run(command,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if __import__("os").name=="nt" else 0)
            if completed.returncode:print(json.dumps({"child_error":entry["blind_id"],"exit_code":completed.returncode}),flush=True)
        item=outputs.sink(oid,cid).current()
        result=process_story_output(outputs,oid,cid)
        if result:
            atomic_json(folder/(entry["blind_id"]+"-result.json"),result)
            if result["text"]:(folder/(entry["blind_id"]+"-new.txt")).write_text(result["text"],encoding="utf-8")
        print(json.dumps({"completed":entry["blind_id"],"transport":(item or {}).get("status","pending"),
            "structure":(result or {}).get("validation",{}).get("structural_status","not_run"),
            "chars":len((result or {}).get("text") or "")}),flush=True)
    return manifest

def main():
    p=argparse.ArgumentParser();p.add_argument("phase",choices=("prepare","execute"))
    p.add_argument("--control",type=Path,required=True);p.add_argument("--comparison-id",required=True)
    p.add_argument("--repo",type=Path,default=ROOT);p.add_argument("--selected-dir",type=Path)
    p.add_argument("--project",type=Path);p.add_argument("--template",type=Path);p.add_argument("--old-reader",type=Path)
    p.add_argument("--settings",type=Path);p.add_argument("--model",default="qwen3.5:9b-q4_K_M")
    p.add_argument("--baseline-label",default="旧本文");p.add_argument("--confirm-plans",action="store_true");p.add_argument("--expected-count",type=int,default=12)
    args=p.parse_args()
    if args.phase=="prepare":
        if any(getattr(args,k) is None for k in ("selected_dir","project","template","old_reader","settings")):
            p.error("preparation inputs are required")
        prepare(args)
    else:execute(args)
    return 0
if __name__=="__main__":raise SystemExit(main())
