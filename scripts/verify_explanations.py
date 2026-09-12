"""Reproduce the three handoff examples and measure new recording without touching old runs."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import sys
import time
from pathlib import Path
import yaml
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
from gapengine.explanations import extract_explanation
from gapengine.evolve import run_individual

CASES = [("detective","exp10-detective","II|low","g11/ind-7/seed-2",22,28),
         ("detective","exp10-detective","I|high","g2/ind-53/seed-3",31,32),
         ("romance","exp7-romance","II|high","g3/ind-92/seed-0",35,36)]


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs",type=Path,required=True)
    parser.add_argument("--out",type=Path,required=True)
    args=parser.parse_args(argv)
    if args.out.exists():
        parser.error("choose a new output directory; existing runs are never overwritten")
    args.out.mkdir(parents=True)
    report={"legacy":[],"new_recording":[],"old_replay_link":"not attempted: historical complete input provenance is unavailable"}
    new_archives={}
    for index,(genre,exp,cell,relative,turn,later) in enumerate(CASES):
        source=args.runs/exp/relative/"layers.jsonl"
        original_hash=digest(source)
        explanation=extract_explanation(source,experiment=exp,cell=cell)
        rep=explanation["representative"]
        assert rep["turn"] == turn
        assert rep["turning"]["confirmation"] == "confirmed"
        assert any(l["downstream"]["turn"] == later for l in rep["turning"]["links"])
        if genre == "romance":
            assert [(c["kind"],c["amount"]) for c in rep["cost"]["items"]] == [("trust",-.2)]
        else:
            assert rep["cost"]["status"] == "absent"
        assert rep["choice"]["alternatives"]["status"] == "unknown"
        assert explanation == extract_explanation(source,experiment=exp,cell=cell)
        if genre == "romance":
            by_line = {d["line"]: d for d in explanation["decisions"]}
            for line in (21, 58, 97):
                assert by_line[line]["cost"]["status"] == "confirmed"
            assert by_line[21]["cost"]["items"][0]["amount"] is None
            assert by_line[58]["cost"]["items"][0]["amount"] is None
            assert not by_line[58]["cost"]["complete"]
            assert by_line[97]["cost"]["items"][0]["amount"] == -1
            report["F1_legacy"] = [{"line": n, "cost": by_line[n]["cost"]} for n in (21,58,97)]
        for item in explanation["decisions"]:
            if item["turning"]["confirmation"] == "candidate":
                assert item["turning"]["status"] == "confirmed"
        write(args.out/f"legacy-{index+1}.json",explanation)
        report["legacy"].append({"source":str(source),"sha256":original_hash,"representative_line":rep["line"],
                                 "turn":turn,"downstream_turn":later,"cost":rep["cost"]["text"],"source_unchanged":digest(source)==original_hash})
        header=json.loads(source.read_text(encoding="utf-8").splitlines()[0])
        project=ROOT/"projects"/genre
        template=ROOT/"templates"/genre
        generation=relative.split("/")[0]
        precedent_path=args.runs/exp/generation/"precedent.json"
        precedent=precedent_path.read_text(encoding="utf-8")
        new_exp=args.out/f"new-{genre}"
        common={"index":index,"genome":header["genome"],"seeds":[header["seed"]],"logical_root":str(new_exp),
                "world_path":str(project/"world.yaml"),"subjects_dir":str(project/"subjects"),
                "action_graph_path":str(template/"action_graph.yaml"),
                "action_cfg":yaml.safe_load((template/"action_graph.yaml").read_text(encoding="utf-8")),
                "rules":yaml.safe_load((template/"rules.yaml").read_text(encoding="utf-8")),
                "qd_cfg":yaml.safe_load((template/"qd.yaml").read_text(encoding="utf-8")),
                "protagonist":header["protagonist"],"antagonist":header["antagonist"],"precedent_json":precedent}
        results=[]
        for enabled in (False,True):
            job={**common,"out_dir":str(new_exp/f"case-{index}"/str(enabled)),"record_explanations":enabled}
            started=time.perf_counter()
            result=run_individual(job)
            elapsed=time.perf_counter()-started
            log=new_exp/result["runs"][0]["layers_path"]
            rows=[json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
            for row in rows:
                row.pop("explanation",None);row.pop("explanation_recording",None)
            results.append((rows,elapsed,log.stat().st_size,result))
            write(new_exp/f"case-{index}"/f"job-{enabled}.json",job)
        assert results[0][0] == results[1][0]
        run=results[1][3]["runs"][0]
        on_log=new_exp/run["layers_path"]
        new_explanation=extract_explanation(on_log,experiment=new_exp.name,cell=cell)
        recorded_rows = [json.loads(l) for l in on_log.read_text(encoding="utf-8").splitlines()]
        measured_moves = []
        for item in new_explanation["decisions"]:
            row = recorded_rows[item["line"] - 1]
            baseline = row["explanation"]["cost_baseline"]
            assert baseline["version"] == 1 and baseline["timing"] == "before_execute"
            after = row["delta"]["actor"].get("stamina")
            if item["verb"] == "move" and after is not None and after < baseline["stamina"]:
                loss = next(c for c in item["cost"]["items"] if c.get("path") == ["stamina"])
                assert loss["amount"] == round(after - baseline["stamina"], 6)
                assert loss["before_source"]["line"] == item["line"]
                measured_moves.append({"line":item["line"],"subject":item["subject"],"before":baseline["stamina"],"after":after,"amount":loss["amount"]})
        assert measured_moves
        write(new_exp/f"case-{index}"/"move-costs.json",measured_moves)
        report.setdefault("F3_moves",[]).append({"genre":genre,"case":index,"count":len(measured_moves),
            "npc_count":sum(m["subject"] != header["protagonist"] for m in measured_moves)})
        write(new_exp/f"case-{index}"/"explanation.json",new_explanation)
        old_archive=json.loads((args.runs/exp/"archive.json").read_text(encoding="utf-8"))
        thresholds=old_archive["volatility_thresholds"]
        vol=run["volatility"]
        bin_name="low" if vol <= thresholds["low_max"] else "mid" if vol <= thresholds["mid_max"] else "high"
        actual_cell=f"{run['category']}|{bin_name}"
        archive=new_archives.setdefault(genre,{"volatility_thresholds":thresholds,"cells":{}})
        archive["cells"][actual_cell]={"descriptor":{"category":run["category"],"volatility":vol,"volatility_bin":bin_name},
            "exemplar":{k:run[k] for k in ("engine_hash","layers_path","precedent_hash","seed")},
            "generation":0,"genome":header["genome"],"parents":[],"quality":run["quality"],"reach_rate":float(run["reached"])}
        report["new_recording"].append({"genre":genre,"cell":actual_cell,"on_off_rows_equal":True,"off_seconds":results[0][1],
            "on_seconds":results[1][1],"off_bytes":results[0][2],"on_bytes":results[1][2],"reached":run["reached"],"log":str(on_log)})
        inputs=[*project.rglob("*.yaml"),*template.rglob("*.yaml"),*list((ROOT/"engine").glob("*.py")),*list((ROOT/"gapengine").glob("*.py"))]
        manifest={str(p.relative_to(ROOT)):digest(p) for p in sorted(set(inputs))}
        manifest["reference_precedent_sha256"]=digest(precedent_path)
        write(new_exp/f"case-{index}"/"input-sha256.json",manifest)
    for genre,archive in new_archives.items():
        write(args.out/f"new-{genre}"/"archive.json",archive)
        write(args.out/f"new-{genre}"/"summary.json",{"generations":[],"seeds":[],"note":"WB-EXPLAIN validation only; current inputs; not historical replay; thresholds copied for display"})
    write(args.out/"verification.json",report)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
