"""Authored F0-F5 contract example, not a result from the production simulator."""
import json
from copy import deepcopy
from gapengine.fact_contract import extract_supplement, make_package, packet


def records():
    def r(ident, kind, actor, at, content, deps=()):
        return {"schema_version": 1, "id": ident, "kind": kind, "actor": actor,
                "at": at, "content": content, "depends_on": list(deps)}
    return [
        r("hidden", "world_fact", "world", 0, {"predicate": "road", "value": "SECRET-道路は通行可能"}),
        r("F0", "belief", "mio", 0, {"predicate": "on_time", "value": True, "domain": "arrival_prediction", "basis": "initial"}),
        r("M1", "claim", "ritsu", 1, {"predicate": "late", "value": True, "message_id": "M"}),
        r("M2", "claim", "ritsu", 1, {"predicate": "road_closed", "value": True, "message_id": "M"}),
        r("F2", "delivery", "mio", 2, {"message_id": "M"}, ("M1", "M2")),
        r("argument", "situation", "mio", 2, {"predicate": "attention", "value": "口論中。気づきの成否を決定する一般則ではない"}),
        r("F3", "notice", "mio", 3, {"message_id": "M"}, ("F2",)),
        r("read-candidate", "action_candidate", "mio", 4, {"action_id": "read-M", "verb": "read", "feasible": True}, ("F3",)),
        r("read-choice", "action_selected", "mio", 4, {"action_id": "read-M"}, ("read-candidate",)),
        r("read-execution", "action_execution", "mio", 4, {"action_id": "read-M"}, ("read-choice",)),
        r("read-result", "action_result", "mio", 4, {"action_id": "read-M", "outcome": "opened_and_read"}, ("read-execution",)),
        r("F4", "understanding", "mio", 4, {"message_id": "M", "claim_ids": ["M1", "M2"]}, ("F3", "read-result", "M1", "M2")),
        r("F5-P", "belief", "mio", 5, {"predicate": "on_time", "value": False, "before": True,
            "domain": "arrival_prediction", "basis": "understood_claim", "claim_ids": ["M1"], "rule_version": "fixture-explicit-1"}, ("F4",)),
        r("F5-E", "belief", "mio", 5, {"predicate": "road_closed", "value": "withheld", "before": "unknown",
            "domain": "reason_assessment", "basis": "understood_claim", "claim_ids": ["M2"], "rule_version": "fixture-explicit-1"}, ("F4",)),
    ]


def raw_log(values=None):
    return b"\n".join(json.dumps({"kind": "event", "subject": r["actor"], "verb": "fact_record",
        "details": {"fact_record": r}}, ensure_ascii=False).encode("utf-8") for r in (records() if values is None else values)) + b"\n"


def example(inputs=None, binding=None):
    raw = raw_log()
    inputs = {"project/world.yaml": b"name: F0-F5\n"} if inputs is None else inputs
    binding = {"run_id": "example", "candidate_id": "mio"} if binding is None else binding
    supplement = extract_supplement(raw, inputs, binding)
    package = make_package(supplement, selected=[r["id"] for r in supplement["records"] if r["id"] != "hidden"],
                           required=["M1", "M2", "F2", "F3", "F4", "F5-P", "F5-E"], hidden=["hidden"])
    return raw, inputs, supplement, package


def response(package, kind="narrate"):
    return {"text": "律から『通行止めで遅れる』という連絡が届いた。後から読んだ澪は、律は間に合わないと考えたが、理由の真偽は保留した。",
            "assertions": [{"record_id": r["id"], "record": deepcopy(r)} for r in packet(package, kind)["records"]]}
