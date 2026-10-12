"""Real native pattern-0 validation, followed by a small fixed-world CPU search."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.sim import Simulation
from scripts.cultural_festival_probe import dump, digest, input_manifest, load, loaded_parameters, source_manifest


class FestivalPolicy:
    # This normal attendance plan is now executed, not postprocessed into a log.
    PLANS = {
        "蒼": {"08:00": ("move", ("校門",)), "08:30": ("move", ("部室",)),
            "09:00": ("costume_prepare", ()),
            "10:00": ("dialogue", ("蓮", "終盤短縮希望", "accept", "room")),
            "10:30": ("practice", ()), "11:00": ("practice", ()),
            "11:30": ("move", ("体育館",)), "12:00": ("practice", ()),
            "12:30": ("move", ("展示室",)), "13:00": ("exhibit_help", ()),
            "13:30": ("move", ("体育館",)),
            "14:30": ("dialogue", ("紗季", "出演", "accept", "room"))},
        "蓮": {"09:30": ("dialogue", ("蒼", "終盤短縮希望", "request", "room")),
            "11:30": ("move", ("体育館",)), "12:00": ("practice", ())},
        "紗季": {"10:00": ("dialogue", ("蒼", "音響手順", "consult", "room")),
            "11:30": ("move", ("体育館",)), "12:00": ("sound_check", ()),
            "14:00": ("dialogue", ("蒼", "出演", "request", "contact"))},
        "灯": {"13:00": ("exhibit_help", ()),
            "14:00": ("dialogue", ("蒼", "共同展示未完成", "request", "contact"))},
    }

    def __init__(self, slots, *, search=False, seed=0):
        self.slots, self.search = slots, search
        self.rng = random.Random(seed)

    def reweight(self, subject, world, present, weighted, *, turn, day):
        slot = self.slots[turn - 1]
        proposal = self.PLANS[subject.id].get(slot, ("wait", ()))
        reason = "共通の予定と約束に沿う"
        # Same traits/goals and same choice weights in every search run. Only
        # legal choices/interaction outcomes differ with seeded tie exploration.
        if self.search and subject.id == "蒼" and "13:30" <= slot <= "14:30":
            if subject.zone == "展示室":
                options = {("move", ("体育館",)): subject.traits["diligence"] * 2,
                           ("exhibit_help", ()): subject.traits["diligence"],
                           ("dialogue", ("灯", "共同展示未完成", "consult", "room")):
                               subject.traits["social"] * .4}
                for action, _ in weighted:
                    if action.verb == "dialogue" and action.args == (
                            "灯", "共同展示未完成", "accept", "room"):
                        options[(action.verb, action.args)] = subject.traits["stubbornness"] * .5
                choices = [(a, options[(a.verb, a.args)]) for a, _ in weighted
                           if (a.verb, a.args) in options]
                action = self.rng.choices([a for a, _ in choices],
                                           weights=[w for _, w in choices])[0]
                reason = ("出演の約束に向けて移動を選ぶ" if action.verb == "move"
                          else "共同展示への責任から、この枠の協力・相談を選ぶ")
                action.meta["reason"] = reason
                return [(action, 1.0)]
            if slot != "14:30":
                proposal = ("wait", ())
        if subject.id == "蒼" and slot >= "15:00" and subject.zone != "体育館":
            # A late move is recorded as late. Never rewrite the opening result.
            proposal, reason = ("move", ("体育館",)), "出演の約束に向けて移動を選ぶ"
        if subject.id == "蒼" and slot == "14:30" and subject.zone != "体育館":
            # Directed contact remains legal when room speech does not.
            proposal = ("dialogue", ("紗季", "出演", "accept", "contact"))
        choices = [(a, 1.0) for a, _ in weighted if (a.verb, a.args) == proposal]
        if not choices:
            raise RuntimeError(f"Plan action not offered: {subject.id} {slot} {proposal}")
        choices[0][0].meta["reason"] = reason
        return choices

    def record(self, subject, action):
        pass


def run_trial(out, *, search=False, seed=0):
    manifest = input_manifest()
    sources = source_manifest()
    world, people = load()
    initial = digest(loaded_parameters(people))
    policies = {name: FestivalPolicy(world.slots, search=search, seed=seed)
                for name in people}
    sim = Simulation(seed, world, people, out, policies=policies, record_explanations=True)
    cpu, wall = time.process_time(), time.perf_counter()
    path = sim.run()
    runtime = sim.slot_runtime
    performance = {"cpu_seconds": time.process_time() - cpu,
                   "wall_seconds": time.perf_counter() - wall, "log_bytes": path.stat().st_size}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert sources == source_manifest(), "Runtime sources changed during this run"
    opening = dict(runtime.opening)
    late = next((r["slot"] for r in runtime.arrivals_after_opening if r["zone"] == "体育館"), None)
    opening["first_late_arrival"] = late if not opening["primary_presence"] else None
    opening["status"] = ("present_declines" if opening["primary_presence"] and
                         opening["declines_to_perform"] is True else "present" if
                         opening["primary_presence"] else "late" if late else "absent")
    readiness = opening["readiness"]
    line_response = any(r["topic"] == "終盤短縮希望" and r["intent"] == "accept"
                        and r["from"] == "蒼" and r["completed_t"] < 18
                        for r in runtime.state["responses"])
    pattern0 = (opening["primary_presence"] and readiness["costume_prepared"]
        and readiness["practice_completed"] > 0 and readiness["sound_checked"]
        and readiness["ren_present"] and line_response and not runtime.failures
        and opening["declines_to_perform"] is False
        and runtime.state["exhibit_contributions"]["蒼"] > 0)
    snapshot = next(r for r in rows if r["kind"] == "knowledge_snapshot"
                    and r["subject"] == "紗季" and r["slot"] == "10:00")
    assert "終盤短縮希望" in snapshot["knowledge"]
    assert all(r["slot"] != "17:00" for r in rows if r["kind"] == "decision")
    assert all(r["details"]["completed_t"] == r["details"]["start_t"] + 1
               for r in runtime.completed)
    for name in people:
        dump(out / f"knowledge-{name}.json", [r for r in rows
             if r["kind"] == "knowledge_snapshot" and r["subject"] == name])
    decisions = [(r["slot"], r["subject"], r["verb"], r["args"])
                 for r in rows if r["kind"] == "decision"]
    classification = ("在場・出演拒否" if opening["status"] == "present_declines" else
        "在場・出演了承あり" if opening["primary_presence"] and opening["declines_to_perform"] is False else
        "在場・出演回答未了" if opening["primary_presence"] else
        "開演不在、後に遅刻" if opening["status"] == "late" else "開演不在")
    result = {"seed": seed, "search": search, "input_manifest": manifest, "source_manifest": sources,
        "loaded_initial_parameters_sha256": initial, "input_unchanged": manifest == input_manifest(),
        "native_layers_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "decision_fingerprint": digest(decisions), "performance": performance,
        "opening": opening, "pattern0_pass": bool(pattern0),
        "failures": runtime.failures, "completed_count": len(runtime.completed),
        "final_activity_state": runtime.state,
        "classification": classification,
        "classification_scope": "observed choice reasons and actual timing; no required class quotas",
        "not_implemented": ["two-slot joint transport", "performance's internal 15-minute acting"]}
    dump(out / "run.json", result)
    return result


def render_views(run_dir):
    verbs = {"practice": "練習", "costume_prepare": "衣装準備", "sound_check": "音響確認",
             "exhibit_help": "展示への協力", "wait": "待機"}
    intents = {"consult": "相談", "request": "依頼", "accept": "了承", "decline": "断り"}
    for name in ("蒼", "紗季", "蓮", "灯"):
        snapshots = json.loads((run_dir / f"knowledge-{name}.json").read_text(encoding="utf-8"))
        lines = [f"# 帰り道の約束 — {name}の記録", "",
                 "実際に選んだ行動・その場で見聞きしたこと・自分宛の連絡だけから構成した記録。", ""]
        last_room = None
        for row in snapshots:
            for obs in row["perceptions"]:
                text = None
                if obs["kind"] == "own_choice":
                    action, args = obs["verb"], obs["args"]
                    if action == "move":
                        text = f"私は{args[0]}へ向かうことにした。"
                    elif action in verbs and action != "wait":
                        text = f"私は{verbs[action]}に取りかかった。"
                    if text and obs.get("reason"):
                        text += obs["reason"] + "。"
                elif obs["kind"] == "speech":
                    who = "私" if obs["subject"] == name else obs["subject"]
                    to = "私" if obs["to"] == name else obs["to"]
                    channel = "連絡で" if obs["channel"] == "contact" else "その場で"
                    text = f"{who}は{to}に、{channel}「{obs['topic']}」について{intents[obs['intent']]}を伝えた。"
                elif obs["kind"] == "activity":
                    who = "私" if obs["subject"] == name else obs["subject"]
                    text = f"{who}が{verbs[obs['verb']]}を終えた。"
                elif obs["kind"] == "arrival":
                    who = "私" if obs["subject"] == name else obs["subject"]
                    text = f"{who}が{obs['zone']}に着いた。"
                elif obs["kind"] == "departure" and obs["subject"] != name:
                    text = f"{obs['subject']}が{obs['origin']}を離れた。"
                elif obs["kind"] == "room_observation":
                    room = (obs["zone"], tuple(obs["present"]))
                    if room != last_room or obs['slot'] == '15:00':
                        others = [n for n in obs["present"] if n != name]
                        text = f"私は{obs['zone']}にいる。" + (
                            "同じ場所には" + "、".join(others) + "がいる。" if others else "今は一人だ。")
                        last_room = room
                if text:
                    lines.append(f"{obs['slot']}　{text}")
        (run_dir / f"view-{name}.md").write_text("\n\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--candidates", type=int, default=8)
    args = parser.parse_args()
    out = args.out.resolve()
    if ROOT == out or ROOT in out.parents or out.exists():
        parser.error("Output must be a new directory outside checkout")
    if not 0 <= args.candidates <= 16:
        parser.error("Small CPU trial allows 0..16 candidates")
    baseline = run_trial(out / "pattern0")
    dump(out / "pattern0-validation.json", baseline)
    print(json.dumps({"pattern0_pass": baseline["pattern0_pass"],
                      "performance": baseline["performance"], "opening": baseline["opening"]}, ensure_ascii=False))
    if not baseline["pattern0_pass"]:
        return 2
    repeat = run_trial(out / "pattern0-replay")
    assert baseline["native_layers_sha256"] == repeat["native_layers_sha256"]
    assert baseline["loaded_initial_parameters_sha256"] == repeat["loaded_initial_parameters_sha256"]
    results = []
    for seed in range(args.candidates):
        result = run_trial(out / f"candidate-{seed:02d}", search=True, seed=seed)
        assert result["input_manifest"] == baseline["input_manifest"] and result["input_unchanged"]
        assert result["loaded_initial_parameters_sha256"] == baseline["loaded_initial_parameters_sha256"]
        results.append(result)
    chosen, seen = [], set()
    for result in results:
        if result["decision_fingerprint"] in seen:
            continue
        seen.add(result["decision_fingerprint"])
        chosen.append(result)
        if len(chosen) == 4:
            break
    for result in chosen:
        render_views(out / f"candidate-{result['seed']:02d}")
    summary = {"pattern0": baseline, "replay_identical": True,
        "candidates": results, "selected_seeds": [r["seed"] for r in chosen],
        "four_logs_four_views": len(chosen) == 4,
        "selection_rule": "first four distinct action sequences; no outcome/category quotas",
        "world_person_initial_state_frozen": True,
        "resource_scope": "CPU only; no LLM/API/GPU, no push/PR/merge"}
    dump(out / "summary.json", summary)
    print(json.dumps({"candidates": len(results), "selected_seeds": summary["selected_seeds"],
                      "outcomes": [r["opening"]["status"] for r in results],
                      "four_logs_four_views": summary["four_logs_four_views"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
