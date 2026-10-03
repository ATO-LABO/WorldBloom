"""Generate once from an explicitly confirmed plan; recheck saved responses locally."""
import argparse
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from execution.output_store import OutputStore
from execution.provenance import read_json, write_bytes, contained, atomic_json

def execute(control, output_id, settings):
    from execution.output_worker import credentials, _output_settings
    from execution.generation import run_generation, result
    from execution.story_service import process_story_output
    from gapengine.gpu_guard import local_gpu_session
    store=OutputStore(control)
    request=store.request(output_id)
    store.verify_artifacts(output_id)
    # Permanent marker plus AttemptSink reservations prevent CLI restarts from resending.
    write_bytes(store.folder(output_id)/"worker-started.json",b'{"schema_version":1}')
    deadline=store.begin_cli_execution(output_id)+request["limits"]["wall_seconds"]
    config=_output_settings(settings)
    with local_gpu_session(request["backend"],config,owner="story:"+output_id,
                           wait_seconds=max(0,deadline-time.time())):
        for cid in request["candidate_ids"]:
            sink=store.sink(output_id,cid)
            if time.time()>=deadline:
                sink.finish(result(sink.identity,"skipped_limit","limit_reached",retry_policy="new_budget_request"))
                continue
            call=sink.call_request(credentials(settings,request["backend"]))
            call["deadline"]=min(deadline,time.time()+request["limits"]["call_timeout_seconds"])
            run_generation(call,sink)
            process_story_output(store,output_id,cid)
    return store.project(output_id)

def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument("--control",type=Path)
    p.add_argument("--plan")
    p.add_argument("--backend",default="none")
    p.add_argument("--model")
    p.add_argument("--settings",type=Path)
    p.add_argument("--timeout",type=int,default=900)
    p.add_argument("--execute")
    p.add_argument("--recheck")
    p.add_argument("--build-internal",type=Path)
    p.add_argument("--candidate")
    args=p.parse_args(argv)
    if args.build_internal:
        from gapengine.story_narration import build_story_prompt
        root=contained(args.build_internal,"inputs/story/"+args.candidate)
        prompt=build_story_prompt(read_json(root/"materials.json"),read_json(root/"plan.json"))
        (args.build_internal/"built-prompt.txt").write_text(prompt,encoding="utf-8")
        return 0
    if not args.control: p.error("--control is required")
    if args.execute:
        result=execute(args.control,args.execute,args.settings)
    elif args.recheck:
        from execution.story_service import process_story_output
        store=OutputStore(args.control)
        result={cid:process_story_output(store,args.recheck,cid) for cid in store.request(args.recheck)["candidate_ids"]}
    else:
        if not args.plan: p.error("--plan is required")
        if (args.backend=="none") != (args.model is None):
            p.error("explicit backend/model required")
        from execution.story_service import prepare_standalone
        result=prepare_standalone(args.control,args.plan,backend=args.backend,model=args.model,
            limits={"max_calls":1,"call_timeout_seconds":args.timeout,"wall_seconds":args.timeout+120,
                    "max_saved_response_bytes":1048576})
        # Execute with the exact runtime already sealed into the output.
        import subprocess
        from execution.provenance import python_executable
        root=OutputStore(args.control).folder(result["output_id"])
        command=[python_executable(),"-I","-B",str(root/"runtime/scripts/story_narrate.py"),
                 "--control",str(args.control),"--execute",result["output_id"]]
        if args.settings: command += ["--settings",str(args.settings)]
        run=subprocess.run(command,stdin=subprocess.DEVNULL)
        if run.returncode: return run.returncode
        result=OutputStore(args.control).project(result["output_id"])
    import json
    print(json.dumps(result,ensure_ascii=False))
    return 0
if __name__=="__main__": raise SystemExit(main())
