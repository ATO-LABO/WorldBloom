"""Create immutable materials and chronological plans; extraction uses no LLM."""
import argparse
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
from execution.provenance import read_json, atomic_json
from execution.story_store import StoryStore

def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument("--control",type=Path)
    p.add_argument("--selected",type=Path)
    p.add_argument("--project",type=Path)
    p.add_argument("--template",type=Path)
    p.add_argument("--run-id",default="ait101-comparison")
    p.add_argument("--confirm")
    p.add_argument("--plan-sha")
    p.add_argument("--edit",type=Path)
    p.add_argument("--candidate")
    p.add_argument("--expected-revision",type=int)
    p.add_argument("--extract-internal",type=Path)
    args=p.parse_args(argv)
    if args.extract_internal:
        from gapengine.story_materials import extract_materials
        root=args.extract_internal
        inputs={x.relative_to(root/"inputs").as_posix():x.read_bytes()
                for x in (root/"inputs").rglob("*.yaml")}
        doc=extract_materials((root/"inputs/layers.jsonl").read_bytes(),inputs,read_json(root/"source-binding.json"))
        atomic_json(root/"materials.json",doc)
        return 0
    if not args.control:
        p.error("--control is required")
    store=StoryStore(args.control)
    if args.confirm:
        if not args.plan_sha: p.error("--plan-sha is required")
        store.confirm(args.confirm,args.plan_sha,method="explicit_cli")
        result=store.reference(args.confirm)
    elif args.edit:
        if args.expected_revision is None or not args.candidate:
            p.error("--candidate and --expected-revision are required")
        result=store.edit(args.run_id,args.candidate,read_json(args.edit),expected_revision=args.expected_revision)
    else:
        if not args.selected or not args.project or not args.template:
            p.error("--selected, --project, --template are required")
        from execution.story_service import organize_experiment
        result=organize_experiment(args.control,ROOT,args.selected,args.project,args.template,run_id=args.run_id)
    import json
    print(json.dumps(result,ensure_ascii=False))
    return 0
if __name__=="__main__":
    raise SystemExit(main())
