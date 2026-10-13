"""Small, state-conditioned seed search on the existing native Simulation.

This is not GA evolution and does not call Jev or any external judge.
The authored PLANS remain only in the independent baseline validation script.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.sim import Simulation
from gapengine.festival_policy import MODEL, build_policies
from gapengine.qd import descriptor, effective_sequence, quality, reached
from scripts.cultural_festival_probe import dump, digest, input_manifest, load, loaded_parameters, source_manifest,PROJECT
from scripts.cultural_festival_trial import render_views


class AuditSimulation(Simulation):
    def __init__(self, *args, sources, choice_control=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.sources = sources
        self.choice_control = choice_control

    def _header(self):
        header = super()._header()
        header["search_method"] = "state_conditioned_seed_search"
        header["policy_model"] = MODEL
        header["source_manifest_sha256"] = self.sources["sha256"]
        header["ga_evolution"] = False
        header["jev_calls"] = 0
        if self.choice_control is not None:
            header["search_method"] = "ga_choice_individual"
            header["choice_genome"] = self.choice_control.choice_genome.to_dict()
            header["sampling_control"] = "genetic quantile on fixed subjective weights"
        return header


def audit_rows(rows, policies):
    snapshots = {(r["subject"], r["t"]): r for r in rows if r["kind"] == "knowledge_snapshot"}
    activity = {r["t"]: r for r in rows if r["kind"] == "activity_snapshot"}
    decisions = [r for r in rows if r["kind"] == "decision"]
    failures = []
    observed_sound = {n: False if p.role == "organizer" else None for n,p in policies.items()}
    timestamps = {n: dict(practice=-1, exhibit=-1, line=-1, cooperation=-1) for n in policies}
    for row in decisions:
        name, tick = row["subject"], row["turn"] - 1
        chosen = row["details"]["decision_basis"]
        audit = policies[name].audits[chosen["audit_index"]]
        view = audit["basis"]
        snapshot = snapshots[name, tick]
        for obs in snapshot["perceptions"]:
            if obs["kind"] == "activity" and obs["verb"] == "sound_check":
                observed_sound[name] = True
            if obs['kind'] == 'activity' and obs['subject'] == name:
                if obs['verb'] == 'practice': timestamps[name]['practice'] = obs['t']
                if obs['verb'] == 'exhibit_help': timestamps[name]['exhibit'] = obs['t']
            if obs['kind'] == 'speech' and obs['intent'] == 'accept':
                if obs['topic'] == '終盤短縮希望' and (obs['subject'] == name or obs['to'] == name):
                    timestamps[name]['line'] = obs['t']
                if obs['topic'] == '共同展示未完成' and obs['subject'] == name:
                    timestamps[name]['cooperation'] = obs['t']
        checks = {
            "own_zone": view["own_zone"] == snapshot["own_zone"],
            "own_knowledge": view["own_knowledge"] == snapshot["knowledge"],
            "no_unobserved_sound": view["sound_observed"] == observed_sound[name],
            "own_practice": view["own_practice_completed"] == activity[tick]["state"]["practice_completed"][name],
            "own_costume": view["own_costume_prepared"] == activity[tick]["state"]["costume_prepared"][name],
            "own_contribution": view["own_exhibit_contributions_observed"] == activity[tick]["state"]["exhibit_contributions"][name],
            "practice_history": view['last_own_practice_t'] == timestamps[name]['practice'],
            "cooperation_history": view['last_own_exhibit_help_t'] == timestamps[name]['exhibit'],
            "own_line_agreement": view['last_known_own_line_agreement_t'] == timestamps[name]['line'],
            "own_cooperation_agreement": view['last_own_exhibit_acceptance_t'] == timestamps[name]['cooperation'],
            "incoming_only_addressed": all(r["to"] == name for r in view["own_incoming_requests"]),
            "selected_matches_log": chosen["selected"]["verb"] == row["verb"] and chosen["selected"]["args"] == row["args"],
            "weights_sum": abs(row["choice_prob"] - chosen["selected"]["weight"] /
                                sum(s["weight"] for s in audit["scores"])) < 1e-7,
            "score_terms": abs(chosen["selected"]["weight"] - sum(chosen["selected"]["components"].values())) < 1e-7,
            "decline_has_actual_concern": 'do_not_show_unprepared_work' not in chosen['selected']['components'] or (
                not view['own_costume_prepared'] or view['own_practice_completed'] < 3
                or view['needs_rehearsal_after_line_agreement']),
        }
        if not all(checks.values()):
            failures.append({"subject": name, "t": tick, "checks": checks})
    completions = [r for r in rows if r.get("verb") == "action_completed"]
    assert len(decisions) == 88 and len(completions) == 88
    assert all(r["details"]["completed_t"] == r["details"]["start_t"] + 1 for r in completions)
    assert not failures, failures
    return {"decision_checks": len(decisions), "failures": failures,
            "completion_checks": len(completions), "knowledge_scope": "actor-only actual snapshots and perceived events"}


def run(out, seed, *, choice_genome=None, project=PROJECT):
    settings, sources = input_manifest(project), source_manifest()
    world, people = load(project=project)
    parameters = deepcopy(loaded_parameters(people))
    policies = build_policies(world.slot_contract)
    control=None
    if choice_genome is not None:
        from gapengine.festival_choices import ChoiceRandom,wrap_policies
        control=ChoiceRandom(seed,choice_genome)
        policies=wrap_policies(policies,control)
    sim = AuditSimulation(seed, world, people, out, policies=policies,
                           record_explanations=True, sources=sources, choice_control=control)
    if control is not None:
        sim.rng=control
        sim.verb_engine.rng=control
    cpu, wall = time.process_time(), time.perf_counter()
    path = sim.run()
    performance = {"cpu_seconds": time.process_time() - cpu,
                   "wall_seconds": time.perf_counter() - wall, "log_bytes": path.stat().st_size}
    assert sources == source_manifest() and settings == input_manifest(project)
    assert all(people[n].traits == parameters[n]["traits"] for n in people)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    audit = audit_rows(rows, policies)
    runtime = sim.slot_runtime
    opening = deepcopy(runtime.opening)
    later = next((a["slot"] for a in runtime.arrivals_after_opening if a["zone"] == "体育館"),None)
    opening["first_late_arrival"] = later if not opening["primary_presence"] else None
    opening["status"] = ("present_declines" if opening["primary_presence"] and opening["declines_to_perform"] is True
        else "present" if opening["primary_presence"] else "late" if later else "absent")
    interactions = [r for r in rows if r.get("verb") == "action_completed" and r["details"]["action"] == "dialogue"]
    for name, policy in policies.items():
        dump(out / f"decision-audit-{name}.json", policy.audits)
        dump(out / f"knowledge-{name}.json", [r for r in rows if r["kind"] == "knowledge_snapshot" and r["subject"] == name])
    result = {"seed": seed, "method": "ga_choice_individual" if control else "state_conditioned_seed_search", "ga_evolution": False,
        "choice_genome":choice_genome.to_dict() if choice_genome is not None else None,
        "jev_calls": 0, "input_manifest": settings, "source_manifest": sources,
        "loaded_initial_parameters_sha256": digest(parameters), "traits_unchanged": True,
        "performance": performance, "opening": opening, "failures": runtime.failures,
        "audit": audit, "interaction_count": len(interactions),
        "interactions": [{"slot": r["slot"], "subject": r["subject"], "args": r["details"]["args"]} for r in interactions],
        "decision_fingerprint": digest([(r["slot"],r["subject"],r["verb"],r["args"]) for r in rows if r["kind"]=="decision"]),
        "native_layers_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "current_ga_readonly_metrics": {"descriptor": descriptor(rows,{}).to_dict(),
            "effective_sequence": effective_sequence(rows),
            "reached": reached(rows,world.target_ending),
            "quality": quality(rows,{"protagonist":world.protagonist})},
        "ga_metric_interpretation": "unadapted legacy metrics, not cultural-festival fitness or a GA result",
        "classification": "presence/refusal/later arrival observed; no required absence categories"}
    if control is not None:
        assert len(control.draws)==88
        dump(out/'choice-draws.json',control.draws)
        result['choice_genome_sha256']=choice_genome.sha256
        result['choice_prob_scope']='fixed policy prior weight, not probability under deterministic genotype'
    dump(out / "run.json", result)
    dump(out / "source-manifest.json", sources)
    render_views(out)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--seeds", type=int, default=4)
    args = parser.parse_args()
    out = args.out.resolve()
    if out == ROOT or ROOT in out.parents or out.exists():
        parser.error("Output must be a new directory outside checkout")
    if not 1 <= args.seeds <= 8:
        parser.error("Initial state-driven trial is bounded to 1..8 seeds")
    results = [run(out/f"seed-{seed}",seed) for seed in range(args.seeds)]
    baseline = results[0]
    assert all(r["input_manifest"] == baseline["input_manifest"] and
        r["source_manifest"] == baseline["source_manifest"] and
        r["loaded_initial_parameters_sha256"] == baseline["loaded_initial_parameters_sha256"] for r in results)
    repeat = run(out/"seed-0-replay",0)
    assert repeat["native_layers_sha256"] == baseline["native_layers_sha256"]
    summary = {"method":"state_conditioned_seed_search", "not_ga_evolution":True,
        "results":results,"all_four_actors_all_22_action_points":True,
        "handwritten_plans_used":False,"fixed_15_move_used":False,
        "same_world_person_initial_hashes":True,"replay_identical":True,
        "scale":"small CPU feasibility and epistemic audit; no outcome quota"}
    dump(out/"summary.json",summary)
    print(json.dumps({"seeds":args.seeds,"method":summary["method"],
        "outcomes":[r["opening"]["status"] for r in results],
        "dialogues":[r["interaction_count"] for r in results],
        "cpu_seconds":[r["performance"]["cpu_seconds"] for r in results],
        "audit_failures":sum(len(r["audit"]["failures"]) for r in results)},ensure_ascii=False))


if __name__ == "__main__":
    main()
