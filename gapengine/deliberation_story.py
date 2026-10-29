"""Evidence for prose, extracted only from a verified saved episode.

This is an author-facing record, not generated fiction or inferred interiority.
Speaker text comes from the exact statements emitted during real actions.
"""
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json

from engine.run_record import describe_run


def scene_material(folder):
    folder = Path(folder)
    descriptor = describe_run(SimpleNamespace(out_dir=folder))
    if descriptor.get("descriptor_scope") != "saved_boundary_v1" or not descriptor.get("verified_execution"):
        raise ValueError("A verified saved episode is required")
    raw = (folder / "layers.jsonl").read_bytes()
    events = []
    for line, encoded in enumerate(raw.decode("utf-8").splitlines(), 1):
        row = json.loads(encoded)
        event = row.get("details", {}).get("deliberation_event")
        if row.get("result") == "applied" and event:
            events.append({"source_line": line, "turn": row["turn"], "slot": row["slot"], **event})
    if not events:
        raise ValueError("No deliberation events in this saved episode")
    final_rows = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
    states = [r["details"]["deliberation"]["after"] for r in final_rows if "deliberation" in r.get("details", {})]
    return {"schema_version": 1, "source_ledger_sha256": hashlib.sha256(raw).hexdigest(),
            "saved_revision_hash": descriptor["saved_revision_hash"], "events": events,
            "final_state": states[-1], "terminal": descriptor["actual_terminal"],
            "prose_contract": ["Keep actors, order, revisions, assent, objections and judgment scope.",
                               "Approval changes only the declared state; do not invent enactment or completed rescue.",
                               "Keep editorial setting/gesture/interiority separate from simulated facts."]}
