"""Run authored message conditions through the real shared calendar simulator."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml
from scripts.cultural_festival_v2 import write_fixture, load
from engine.sim import Simulation


def authored_model(actors):
    # These are controlled witness inputs, not calibrated production defaults.
    cap = {a: {"device_available": True, "read_while_moving": True, "send_while_moving": True,
               "read_minutes": 1, "send_minutes": 1} for a in actors}
    return {"schema_version": 1, "capabilities": cap,
        "messages": [
            {"id": "M", "sender": "蒼", "recipient": "灯", "delivery_delay_minutes": 2, "notice_at": 504,
             "claims": [{"id": "M1", "predicate": "late", "value": True},
                        {"id": "M2", "predicate": "road_closed", "value": True}]},
            {"id": "Q", "sender": "灯", "recipient": "蒼", "delivery_delay_minutes": 0, "notice_at": None,
             "claims": [{"id": "Q1", "predicate": "question_about_road", "value": "通行止めという説明を確認したい"}]}],
        "initial_beliefs": [{"id": "F0", "actor": "灯", "domain": "arrival_prediction", "predicate": "on_time", "value": True}],
        "contexts": [{"id": "argument", "actor": "灯", "at": 502, "predicate": "attention", "value": "別の人物と口論中"}],
        "updates": [
            {"id": "before-notice", "actor": "灯", "at": 503, "domain": "arrival_prediction", "predicate": "on_time", "value": False,
             "claim_ids": ["M1"], "rule_version": "authored-witness-1"},
            {"id": "F5-P", "actor": "灯", "at": 507, "domain": "arrival_prediction", "predicate": "on_time", "value": False,
             "claim_ids": ["M1"], "rule_version": "authored-witness-1"},
            {"id": "F5-E", "actor": "灯", "at": 507, "domain": "reason_assessment", "predicate": "road_closed", "value": "withheld",
             "claim_ids": ["M2"], "rule_version": "authored-witness-1"},
            {"id": "duplicate-P", "actor": "灯", "at": 508, "domain": "arrival_prediction", "predicate": "on_time", "value": True,
             "claim_ids": ["M2"], "rule_version": "authored-witness-1"}]}


def create_project(path, transform=None):
    path = Path(path)
    write_fixture(path)
    contract_path = path / "contract_v2.yaml"
    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    model = authored_model(contract["roles"])
    if transform is not None:
        transform(model)
    contract["fact_model"] = model
    contract["assumptions"].append("fact_modelは明示条件の試走。時間値と更新規則は作品用の既定値として採用していない。")
    contract_path.write_text(yaml.safe_dump(contract, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def witness_commands(*, combined=False):
    return [{"id": "send-M", "actor": "蒼", "at": 500, "verb": "send_message", "message_id": "M"},
            {"id": "read-M", "actor": "灯", "at": 505, "verb": "read_message", "message_id": "M"}] + (
                [{"id": "leave-and-ask", "actor": "灯", "at": 509, "verb": "move_and_send", "to": "教室", "message_id": "Q"}] if combined else [])


def run(project, output, commands=None, *, until=None, resume=False):
    world, people = load(project)
    sim = Simulation(0, world, people, Path(output))
    sim.slot_runtime.configure(witness_commands() if commands is None else commands)
    sim.slot_runtime.run(until=until, resume=resume)
    return sim.slot_runtime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--create-project", action="store_true")
    parser.add_argument("--combined", action="store_true")
    args = parser.parse_args()
    if args.create_project:
        create_project(args.project)
    runtime = run(args.project, args.out, witness_commands(combined=args.combined))
    print(json.dumps({"log": str(runtime.store.out / "layers.jsonl"), "api_calls": 0,
                      "fact_records": sum(r["verb"] == "fact_record" for r in runtime.rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
