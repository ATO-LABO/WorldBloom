"""CPU-only native compatibility probe; never claim a diagnostic is pattern 0.

Run from any directory: python3 scripts/cultural_festival_probe.py --out PATH
Exit 2 means the approved contract is blocked, even if native diagnostics ran.
Outputs must live outside the checkout, following docs/DEVELOPMENT.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import random
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml
from engine.actions import Action, candidates
from engine.sim import Simulation
from engine.subject import Subject
from engine.verbs import HANDLED_VERBS, VerbEngine
from engine.world import World

PROJECT = ROOT / "projects" / "cultural_festival"


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
                    + "\n", encoding="utf-8")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def input_manifest(project=PROJECT):
    """Includes the contract, authored plan, raw goals, and all subject data."""
    files = {"projects/cultural_festival/"+str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(project.rglob("*.yaml"))}
    raw_subjects = [yaml.safe_load(p.read_text(encoding="utf-8"))
                    for p in sorted((project / "subjects").glob("*.yaml"))]
    return {"files": files, "common_settings_sha256": digest(files),
            "person_parameters_sha256": digest(raw_subjects)}


def source_manifest():
    paths = sorted(list((ROOT / "engine").glob("*.py"))
        + list((ROOT / "gapengine").glob("*.py"))
        + list((ROOT / "scripts").glob("cultural_festival*.py")))
    files = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    return {"files": files, "sha256": digest(files)}


def protected_manifest():
    """All original tracked files are protected, including Linux/route files."""
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    return {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
            for p in paths if p and (ROOT / p).is_file()
            and not p.startswith(("projects/cultural_festival/",
                                  "scripts/cultural_festival_probe.py",
                                  "tests/test_cultural_festival.py",
                                  "docs/cultural-festival-cpu-validation.md"))}


def load(*, legacy=False, project=PROJECT):
    if legacy:
        # Historical audit/compatibility only, not a candidate run. Same narrative
        # inputs with the new execution switch absent, exercising legacy code.
        raw = yaml.safe_load((project / "world.yaml").read_text(encoding="utf-8"))
        raw.pop("slot_execution", None)
        world = World(raw, project / "world.yaml")
    else:
        world = World.from_yaml(project / "world.yaml")
    subjects = {s.id: s for s in [Subject.from_yaml(p) for p in
                                sorted((project / "subjects").glob("*.yaml"))]}
    world.resolve_truth(0)
    world.bind_subjects(subjects)
    return world, subjects


def loaded_parameters(subjects):
    return {name: {"traits": s.traits, "goals_native": vars(s.goal),
                   "initial_zone": s.zone, "knowledge": sorted(s.knowledge),
                   "inventory": s.inventory, "relations": s.initial_relations,
                   "verbs": sorted(s.verbs)} for name, s in sorted(subjects.items())}


def static_plan_feasibility():
    """Check an authored witness, not a generated trajectory or engine replay."""
    contract = yaml.safe_load((PROJECT / "contract_v1.yaml").read_text(encoding="utf-8"))
    plan = yaml.safe_load((PROJECT / "pattern0_plan.yaml").read_text(encoding="utf-8"))
    slots = contract["clock"]["slots"]
    positions = dict(contract["initial_locations"])
    pending = []
    errors = []
    primary = None
    readiness = {"costume_prepared": False, "ao_practice_slots": 0,
                 "sound_checked": False, "ren_present": None}
    for t, slot in enumerate(slots):
        for due, actor, step in pending[:]:
            if due != t:
                continue
            action = step["action"]
            if action == "move":
                positions[actor] = step["to"]
            elif action == "costume_prepare":
                readiness["costume_prepared"] = True
            elif action == "practice" and actor == "蒼":
                readiness["ao_practice_slots"] += 1
            elif action == "sound_check":
                readiness["sound_checked"] = True
            pending.remove((due, actor, step))
        if slot == "15:00":
            primary = positions["蒼"] == "体育館"
            readiness["ren_present"] = positions["蓮"] == "体育館"
        for step in [x for x in plan["steps"] if x["time"] == slot]:
            actor, action = step["actor"], step["action"]
            if any(a == actor for _, a, _ in pending):
                errors.append(f"{slot}: {actor} is already occupied")
            if t == 22:
                errors.append("17:00 has no next action")
            if action not in contract["actions"]:
                errors.append(f"Unsupported contract action: {action}")
                continue
            definition = contract["actions"][action]
            if action == "joint_transport":
                errors.append("Joint transport excluded from initial one-slot witness")
            if action == "move":
                if step["to"] not in contract["routes"][positions[actor]]:
                    errors.append(f"{slot}: nonadjacent movement")
                if "校門" in (positions[actor], step["to"]) and slot < "08:00":
                    errors.append(f"{slot}: closed gate")
            if definition.get("zone") and positions[actor] != definition["zone"]:
                errors.append(f"{slot}: wrong zone for {action}")
            if definition.get("actor") and actor != definition["actor"]:
                errors.append(f"{slot}: wrong actor for {action}")
            if action == "consult_request_accept_decline" and positions[actor] != positions[step["to"]]:
                errors.append(f"{slot}: consultation requires explicit addressed channel")
            pending.append((t + definition["duration_slots"], actor, step))
    return {"evidence_kind": "static authored plan analysis; NOT native execution",
            "errors": errors, "plan_feasible": not errors and primary,
            "expected_primary_presence": primary, "expected_readiness": readiness}


def capability_probes():
    """Counterexamples use isolated fixtures, not modified candidate worlds."""
    world, subjects = load(legacy=True)
    sim = Simulation(0, world, subjects, Path("/tmp/unused-festival-probe"))
    sim.day, sim.turn, sim.slot = 1, 1, "06:00"
    moves = [a for a, _ in candidates(subjects["蒼"], world, sim) if a.verb == "move"]
    gate_offered_at_six = any(a.args[0] == "校門" for a in moves)
    gate_result, gate_details, _ = sim.verb_engine.execute(subjects["蒼"], Action("move", ("校門",)),
                                                         turn=1, day=1)
    # Apply the actual configured gate event at 08:00, without forcing movement.
    gate_event = next(e for e in world.scheduled_for(1, "08:00") if e["id"] == "gate-opens")
    sim._apply_event_effects(gate_event, list(subjects.values()))
    sim.turn, sim.slot = 5, "08:00"
    moves = [a for a, _ in candidates(subjects["蒼"], world, sim) if a.verb == "move"]
    nonadjacent = [a.args[0] for a in moves if a.meta.get("hops", 1) > 1]
    before = subjects["蒼"].zone
    result, details, _ = sim.verb_engine.execute(subjects["蒼"], Action("move", ("校門",)),
                                              turn=5, day=1)
    immediate = before == "自宅" and subjects["蒼"].zone == "校門"

    world, subjects = load(legacy=True)
    ve = VerbEngine(world, random.Random(0))
    remote_result, remote_details, _ = ve.execute(subjects["蓮"],
        Action("share_knowledge", ("蒼", "終盤短縮希望")), turn=1, day=1)
    # Explicitly arranged fixture to test co-room transfer and overhearing.
    subjects["蒼"].zone = "部室"
    local_result, local_details, markers = ve.execute(subjects["蓮"],
        Action("share_knowledge", ("蒼", "終盤短縮希望")), turn=1, day=1)
    same_tick_delivery = "終盤短縮希望" in subjects["蒼"].knowledge
    bystander_heard = "終盤短縮希望" in subjects["紗季"].knowledge
    unknown = {}
    for verb in ("practice", "costume_prepare", "sound_check", "consult", "request",
                 "accept", "decline", "exhibit_help", "joint_transport"):
        r, d, _ = ve.execute(subjects["蒼"], Action(verb), turn=1, day=1)
        unknown[verb] = {"handled": verb in HANDLED_VERBS, "result": r, "details": d}
    return {
        "fixture_warning": "direct isolated capability tests; not candidate logs",
        "nonadjacent_move_candidates_at_08": nonadjacent,
        "closed_gate_move_offered_at_06": gate_offered_at_six,
        "closed_gate_execute_at_06": {"result": gate_result, "details": gate_details},
        "open_gate_move_offered_at_08": any(a.args[0] == "校門" for a in moves),
        "movement": {"result": result, "details": details, "same_tick_arrival": immediate},
        "remote_contact": {"result": remote_result, "details": remote_details},
        "co_room_speech": {"result": local_result, "details": local_details,
                           "same_tick_delivery": same_tick_delivery,
                           "bystander_heard": bystander_heard, "markers": markers},
        "domain_verbs": unknown,
        "note": "train changes base strength; craft creates inventory. Neither is mapped to preparation without validated semantics."}


class DiagnosticPolicy:
    """Only supported, time-scripted choices; no scheduled force_action."""
    PROPOSALS = {
        "蒼": {"08:00": ("move", ("校門",)), "08:30": ("move", ("部室",)),
                "11:30": ("move", ("体育館",)), "12:30": ("move", ("展示室",)),
                "13:30": ("move", ("体育館",))},
        "紗季": {"10:00": ("share_knowledge", ("蒼", "音響手順")),
                  "11:30": ("move", ("体育館",))},
        "蓮": {"09:30": ("share_knowledge", ("蒼", "終盤短縮希望")),
                "11:30": ("move", ("体育館",))},
        "灯": {},
    }

    def __init__(self, slots):
        self.slots = slots
        self.missing = []

    def reweight(self, subject, world, present, weighted, *, turn, day):
        slot = self.slots[turn - 1]
        proposal = self.PROPOSALS[subject.id].get(slot, ("rest", ()))
        match = [(a, 1.0) for a, _ in weighted if (a.verb, a.args) == proposal]
        if match:
            return match
        self.missing.append({"subject": subject.id, "slot": slot, "proposal": proposal})
        raise RuntimeError(f"Diagnostic proposal not offered: {subject.id} {slot} {proposal}")

    def record(self, subject, action):
        pass


class ObservedSimulation(Simulation):
    """Read-only instrumentation. Native ordering/execution are unchanged."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.observations = []
        self.opening = None

    def snapshot(self, phase, trigger=None):
        self.observations.append({"phase": phase, "native_turn": self.turn,
            "t": self.world.slots.index(self.slot) if self.slot is not None else None,
            "slot": self.slot, "trigger": trigger,
            "knowledge": {n: sorted(s.knowledge) for n, s in self.subjects.items()},
            "positions_inspector_only": {n: s.zone for n, s in self.subjects.items()}})

    def _apply_scheduled(self, writer, *, slot):
        super()._apply_scheduled(writer, slot=slot)
        if slot is not None:
            self.snapshot("before_slot_actions")
            if slot == "15:00":
                self.opening = {"primary_presence": self.subjects["蒼"].zone == "体育館",
                    "ao_zone": self.subjects["蒼"].zone,
                    "readiness": {"costume_prepared": None, "practice_completed": None,
                                  "sound_checked": None,
                                  "ren_present": self.subjects["蓮"].zone == "体育館"},
                    "declines_to_perform": None,
                    "execution_failure": False,
                    "evaluation_phase": "15:00 before all slot actions",
                    "scope": "native diagnostic only; approved contract not satisfied"}

    def _observe_row(self, row):
        super()._observe_row(row)
        if row.get("kind") in ("event", "decision"):
            self.snapshot("after_native_row", {k: row.get(k) for k in
                          ("kind", "subject", "verb", "result", "slot")})


def native_run(out):
    manifest = input_manifest()
    world, subjects = load(legacy=True)
    parameters = loaded_parameters(subjects)
    policies = {name: DiagnosticPolicy(world.slots) for name in subjects}
    sim = ObservedSimulation(0, world, subjects, out, policies=policies,
                             record_explanations=True)
    cpu_start, wall_start = time.process_time(), time.perf_counter()
    path = sim.run()
    performance = {"cpu_seconds": time.process_time() - cpu_start,
                   "wall_seconds": time.perf_counter() - wall_start,
                   "log_bytes": path.stat().st_size}
    dump(out / "observations.json", sim.observations)
    # Four own-knowledge views; inspector positions are never given to policies.
    for name in subjects:
        rows = [{"phase": x["phase"], "native_turn": x["native_turn"],
                 "t": x["t"], "slot": x["slot"],
                 "own_knowledge": x["knowledge"][name]} for x in sim.observations]
        dump(out / f"knowledge-{name}.json", rows)
    opening = sim.opening
    future = [x for x in sim.observations if x["slot"] > "15:00"
              and x["phase"] == "before_slot_actions"]
    opening["first_observed_late_arrival"] = (next((x["slot"] for x in future
        if x["positions_inspector_only"]["蒼"] == "体育館"), None)
        if not opening["primary_presence"] else None)
    opening["status"] = "present" if opening["primary_presence"] else (
        "absent_at_opening_with_later_observed_arrival" if opening["first_observed_late_arrival"]
        else "absent_at_opening")
    dump(out / "opening.json", opening)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    result = {"input_manifest": manifest, "loaded_initial_parameters": parameters,
              "loaded_initial_parameters_sha256": digest(parameters),
              "input_unchanged": manifest == input_manifest(),
              "native_layers_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
              "decision_count": sum(r["kind"] == "decision" for r in rows),
              "invalid_count": sum(r.get("result") == "invalid" for r in rows),
              "performance": performance, "opening": opening,
              "pattern0_pass": False, "missing_proposals": [x for p in policies.values() for x in p.missing],
              "scope": "supported move/rest/targeted co-room sharing only; not pattern0"}
    dump(out / "run.json", result)
    return result


BLOCKERS = [
    "B01: native move candidates include multiple-edge destinations in one action",
    "B02: native move/share results apply immediately, rather than at next tick completion",
    "B04: remote addressed contact is rejected as target_not_present",
    "B05: targeted co-room sharing does not deliver spoken fact to co-room bystander",
    "B06: preparation/consultation/help domain actions and readiness state are not implemented",
    "B07: structured promises, accept/decline and joint transport duration/interruption not implemented",
    "B08: multi-goal personality metadata is retained in files but not used by native decision policy",
    "B09: Simulation runs next actions at final 17:00 observation tick",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if out == ROOT or ROOT in out.parents:
        parser.error("--out must be outside the checkout")
    if out.exists() and any(out.iterdir()):
        parser.error("--out must be empty; existing evidence will not be overwritten")
    protected_before = protected_manifest()
    out.mkdir(parents=True, exist_ok=True)
    dump(out / "settings-manifest.json", input_manifest())
    probes = capability_probes()
    dump(out / "capability-probes.json", probes)
    witness = static_plan_feasibility()
    dump(out / "authored-plan-validation.json", witness)
    first = native_run(out / "native-diagnostic")
    # Replaying the SAME diagnostic is determinism checking, not candidate search.
    second = native_run(out / "native-replay")
    equal = first["native_layers_sha256"] == second["native_layers_sha256"]
    observations_equal = (out / "native-diagnostic/observations.json").read_bytes() == (
        out / "native-replay/observations.json").read_bytes()
    report = {"repository": "ATO-LABO/WorldBloom",
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(),
        "branch": subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT).decode().strip(),
        "python": platform.python_version(), "PyYAML": yaml.__version__,
        "status": "BLOCKED_NATIVE_CONTRACT", "schema_load": "PASS",
        "authored_plan_feasibility": witness, "native_diagnostic": first,
        "native_repeat_layers_identical": equal,
        "native_repeat_observations_identical": observations_equal,
        "same_input_manifest": first["input_manifest"] == second["input_manifest"],
        "same_loaded_initial_parameters": first["loaded_initial_parameters_sha256"] == second["loaded_initial_parameters_sha256"],
        "protected_tracked_files_unchanged": protected_before == protected_manifest(),
        "blocked_requirements": BLOCKERS, "candidate_search_performed": False,
        "four_logs_by_four_views_produced": False,
        "classification": "not attempted; no absence or class quotas forced",
        "resources": "CPU only; no network/LLM/GPU in probe; no authentication changes"}
    dump(out / "validation.json", report)
    print(json.dumps({"status": report["status"], "schema_load": report["schema_load"],
                      "static_plan_feasible": witness["plan_feasible"],
                      "native_primary_presence": first["opening"]["primary_presence"],
                      "pattern0_pass": False, "native_repeat_layers_identical": equal,
                      "cpu_seconds": first["performance"]["cpu_seconds"],
                      "output": str(out)}, ensure_ascii=False))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
