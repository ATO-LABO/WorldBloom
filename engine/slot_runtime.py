"""Opt-in cultural-festival contract execution with real next-boundary effects.

This bounded runtime supports one day, adjacent moves, one-slot activities and
addressed dialogue. It is not a generic replacement for legacy combat/effects.
Legacy Simulation.run is untouched unless slot_execution is explicitly set.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
from pathlib import Path

from engine.actions import Action
from engine.log import LayersWriter, nested_diff
from engine.yaml_cache import load_yaml


def load_contract(world, config):
    if not isinstance(config, dict) or set(config) != {"mode", "contract"}:
        raise ValueError("slot_execution requires exactly mode and contract")
    if config["mode"] != "cultural_festival_next_boundary_v1":
        raise ValueError("Unsupported slot_execution mode")
    path = (world.source.parent / config["contract"]).resolve()
    contract = load_yaml(path)
    if not isinstance(contract, dict) or contract.get("version") != 1:
        raise ValueError("Unsupported festival contract version")
    if world.days != 1 or list(world.slots) != contract["clock"]["slots"]:
        raise ValueError("Festival clock must match the unchanged contract")
    if set(world.zones) != set(contract["zones"]):
        raise ValueError("Festival zones must match contract")
    for zone, destinations in contract["routes"].items():
        if set(destinations) != {edge.destination for edge in world.routes.get(zone, ())}:
            raise ValueError("Festival adjacency must match contract")
    if world.daily_events or getattr(world, "phase_rules", ()) or world.thresholds or world.effect_library:
        raise ValueError("This runtime does not support daily events/rules/thresholds/effects")
    allowed = {"id", "day", "slot", "label", "targets", "grants_item"}
    if any(set(event) - allowed for event in world.scheduled_events):
        raise ValueError("Festival runtime supports notice/item scheduled events only")
    if any(contract["actions"][name]["duration_slots"] != 1 for name in
           ("move", "wait", "practice", "costume_prepare", "sound_check",
            "consult_request_accept_decline", "exhibit_help")):
        raise ValueError("Initial runtime supports one-slot actions only")
    return contract


class SlotRuntime:
    def __init__(self, simulation):
        self.sim = simulation
        self.world = simulation.world
        self.people = simulation.subjects
        self.contract = self.world.slot_contract
        c = self.contract
        if set(self.people) != set(c["initial_locations"]):
            raise ValueError("Festival subjects must match contract")
        for name, person in self.people.items():
            if person.zone != c["initial_locations"][name] or person.traits != c["personality"][name]:
                raise ValueError("Festival initial position/personality mismatch")
            if person.knowledge != set(c["knowledge"]["initial_facts"][name]):
                raise ValueError("Festival initial knowledge mismatch")
            expected_inventory = {item: 1 for item in c["initial_state"].get(name, {}).get("inventory", [])}
            if person.inventory != expected_inventory:
                raise ValueError("Festival initial inventory mismatch")
        self.lead = c["play"]["lead"]
        self.partner = c["play"]["partner"]
        self.pending = []
        self.perceptions = defaultdict(list)
        self.state = {"costume_prepared": {name: False for name in self.people},
            "practice_completed": {name: 0 for name in self.people},
            "sound_checked": False,
            "exhibit_contributions": {name: 0 for name in self.people},
            "requests": [], "responses": [], "declines_to_perform": None}
        self.opening = None
        self.arrivals_after_opening = []
        self.completed = []
        self.failures = []
        self.writer = None

    @property
    def tick(self):
        return self.sim.turn - 1

    def available(self, person, verb):
        if verb == "move":
            return "move" in person.verbs and self.world.genre_allows("move")
        return True

    def candidates(self, person):
        actions = [Action("wait", meta={"duration_slots": 1})]
        if self.available(person, "move"):
            for edge in self.world.routes.get(person.zone, ()):
                if (edge.destination in person.range_zones
                        and edge.destination not in self.world._excluded_zones(person)
                        and self.world._route_allowed(person, edge)
                        and person.stamina >= edge.cost):
                    actions.append(Action("move", (edge.destination,),
                        {"duration_slots": 1, "cost": edge.cost, "hops": 1}))
        actions.append(Action("practice", meta={"duration_slots": 1}))
        if (person.id == self.lead
                and person.zone == self.contract["actions"]["costume_prepare"]["zone"]
                and not self.state["costume_prepared"][person.id]):
            actions.append(Action("costume_prepare", meta={"duration_slots": 1}))
        sound = self.contract["actions"]["sound_check"]
        if (person.id == sound["actor"] and person.zone == sound["zone"]
                and "音響手順" in person.knowledge and not self.state["sound_checked"]):
            actions.append(Action("sound_check", meta={"duration_slots": 1}))
        if person.zone == "展示室":
            actions.append(Action("exhibit_help", meta={"duration_slots": 1}))
        for peer in sorted(self.people):
            if peer == person.id:
                continue
            channels = ["contact"]
            if self.people[peer].zone == person.zone:
                channels.append("room")
            # Consultation/information and requests have distinct actual states.
            topics = sorted(person.knowledge) + ["出演"]
            for topic in topics:
                for kind in ("consult", "request"):
                    for channel in channels:
                        actions.append(Action("dialogue", (peer, topic, kind, channel),
                                              {"duration_slots": 1, "target": peer}))
            for request in self.state["requests"]:
                if request["to"] != person.id or request["from"] != peer:
                    continue
                if any(response["request_id"] == request["id"] for response in self.state["responses"]):
                    continue
                for kind in ("accept", "decline"):
                    for channel in channels:
                        actions.append(Action("dialogue", (peer, request["topic"], kind, channel),
                            {"duration_slots": 1, "target": peer, "request_id": request["id"]}))
        return [(a, 1.0) for a in sorted(actions, key=lambda a: (a.verb, a.args))]

    def emit(self, verb, actor=None, result="applied", **details):
        self.writer.write(self.sim._event_row(verb=verb, subject=actor, result=result,
                                               delta={}, details=details))

    def perceive(self, audience, observation):
        for name in sorted(set(audience)):
            self.perceptions[name].append({"t": self.tick, "slot": self.sim.slot, **observation})

    def start(self, person, action, probability):
        if action.verb == "rest":
            action = Action("wait", meta={"duration_slots": 1})
        offered = self.candidates(person)
        canonical = next((a for a, _ in offered if (a.verb, a.args) == (action.verb, action.args)), None)
        if canonical is None:
            raise ValueError("Not a legal one-slot festival action")
        # Preconditions, durations and request references are engine-owned.
        action = Action(action.verb, action.args, {**canonical.meta,
            "reason": action.meta.get("reason"),
            "decision_basis": deepcopy(action.meta.get("decision_basis"))})
        audience = [n for n, p in self.people.items() if p.zone == person.zone]
        pending = {"actor": person.id, "action": deepcopy(action), "start_t": self.tick,
                   "due_t": self.tick + 1, "origin": person.zone, "audience": audience}
        self.pending.append(pending)
        details = {"start_t": self.tick, "completes_t": self.tick + 1, "duration_slots": 1,
                   "completion_semantics": "next_boundary"}
        if action.meta["decision_basis"] is not None:
            details["decision_basis"] = action.meta["decision_basis"]
        self.writer.write(self.sim._decision_row(person, action, "started", details, probability, {}))
        reason = action.meta.get("reason")
        self.perceive([person.id], {"kind": "own_choice", "verb": action.verb,
            "args": list(action.args), "reason": reason, "duration_slots": 1})

    def complete(self, pending):
        actor = self.people[pending["actor"]]
        action = pending["action"]
        before, before_state = self.sim._capture(), deepcopy(self.state)
        result, details = "completed", {}
        if actor.vitality == "dead" or actor.zone != pending["origin"]:
            result, details = "failed", {"reason": "actor_unavailable_at_completion"}
        elif action.verb == "move":
            # Execute exactly the queued edge, never native multi-hop pathfinding.
            edge = next((e for e in self.world.routes[actor.zone] if e.destination == action.args[0]), None)
            if edge is None or not self.world._route_allowed(actor, edge):
                result, details = "failed", {"reason": "edge_unavailable_at_completion"}
            else:
                actor.zone = edge.destination
                actor.change_stamina(-edge.cost)
                details = {"origin": pending["origin"], "dest": actor.zone, "hops": 1}
                self.perceive(pending["audience"], {"kind": "departure", "subject": actor.id,
                                                   "origin": pending["origin"]})
                audience = [n for n, p in self.people.items() if p.zone == actor.zone]
                self.perceive(audience, {"kind": "arrival", "subject": actor.id, "zone": actor.zone})
                if actor.id == self.lead and self.sim.slot > self.contract["play"]["start"]:
                    self.arrivals_after_opening.append({"slot": self.sim.slot, "zone": actor.zone})
        elif action.verb == "practice":
            self.state["practice_completed"][actor.id] += 1
        elif action.verb == "costume_prepare":
            self.state["costume_prepared"][actor.id] = True
            actor.add_item("衣装", 1)
        elif action.verb == "sound_check":
            self.state["sound_checked"] = True
        elif action.verb == "exhibit_help":
            self.state["exhibit_contributions"][actor.id] += 1
        elif action.verb == "dialogue":
            peer, topic, kind, channel = action.args
            # Room speech is audible to the people present during its scene;
            # addressed contact goes only to its recipient when it completes.
            audience = pending["audience"] if channel == "room" else [actor.id, peer]
            if channel == "room" and peer not in audience:
                result, details = "failed", {"reason": "recipient_not_in_speech_scene"}
            elif topic != "出演" and topic not in actor.knowledge:
                result, details = "failed", {"reason": "speaker_does_not_know_fact"}
            else:
                details = {"to": peer, "topic": topic, "intent": kind, "channel": channel,
                           "audience": sorted(audience)}
                if topic in self.world.facts:
                    for name in audience:
                        receiver = self.people[name]
                        if topic not in receiver.knowledge:
                            receiver.knowledge.add(topic)
                            self.emit("learn_fact", name, fact=topic, speaker=actor.id,
                                      channel=channel, action_start_t=pending["start_t"])
                observation = {"kind": "speech", "subject": actor.id, **details}
                self.perceive(audience, observation)
                if kind == "request":
                    self.state["requests"].append({"id": f"{pending['start_t']}:{actor.id}:{peer}:{topic}",
                        "from": actor.id, "to": peer, "topic": topic, "completed_t": self.tick})
                elif kind in ("accept", "decline"):
                    request_id = action.meta.get("request_id")
                    if not any(r["id"] == request_id and r["to"] == actor.id for r in self.state["requests"]):
                        result, details = "failed", {"reason": "request_not_received"}
                    else:
                        self.state["responses"].append({"request_id": request_id, "from": actor.id,
                            "to": peer, "topic": topic, "intent": kind, "completed_t": self.tick})
                        if actor.id == self.lead and topic == "出演":
                            self.state["declines_to_perform"] = kind == "decline"
        elif action.verb != "wait":
            raise ValueError("Unknown queued action")
        after = self.sim._capture()
        row = self.sim._event_row(verb="action_completed", subject=actor.id,
            result=result, delta=self.sim._delta(actor.id, before, after), details={
            "action": action.verb, "args": list(action.args), "start_t": pending["start_t"],
            "completed_t": self.tick, "duration_slots": 1,
            "activity_delta": nested_diff(before_state, self.state), **details})
        self.writer.write(row)
        self.completed.append(row)
        if result == "failed":
            self.failures.append(row)
        elif action.verb not in ("move", "dialogue", "wait"):
            self.perceive(pending["audience"], {"kind": "activity", "subject": actor.id,
                                               "verb": action.verb, "zone": actor.zone})

    def opening_check(self):
        lead = self.people[self.lead]
        self.opening = {"t": self.tick, "slot": self.sim.slot,
            "primary_presence": lead.zone == self.contract["play"]["zone"],
            "ao_zone": lead.zone, "readiness": {
                "costume_prepared": self.state["costume_prepared"][self.lead],
                "practice_completed": self.state["practice_completed"][self.lead],
                "sound_checked": self.state["sound_checked"],
                "ren_present": self.people[self.partner].zone == self.contract["play"]["zone"]},
            "declines_to_perform": self.state["declines_to_perform"],
            "execution_failure": bool(self.failures),
            "phase": "after_completion_schedule_observation_before_choices"}
        self.emit("opening_check", self.lead, **self.opening)

    def run(self):
        sim = self.sim
        path = sim.out_dir / "layers.jsonl"
        with LayersWriter(path, on_write=sim._observe_row) as writer:
            self.writer = sim._writer = writer
            header = sim._header()
            header["execution_mode"] = "cultural_festival_next_boundary_v1"
            header["contract_sha256"] = hashlib.sha256(
                (self.world.source.parent / self.world.slot_execution["contract"]).read_bytes()).hexdigest()
            header["unsupported"] = ["joint_transport_two_slots", "legacy_combat_effects"]
            writer.write(header)
            sim.day, sim.turn, sim.slot = 1, 1, None
            sim._apply_scheduled(writer, slot=None)
            for tick, slot in enumerate(self.world.slots):
                sim.turn, sim.slot = tick + 1, slot
                for pending in sorted(self.pending, key=lambda p: p["actor"]):
                    self.complete(pending)
                self.pending.clear()
                sim._apply_scheduled(writer, slot=slot)
                sim._recover_stamina()
                sim._record_encounters(writer)
                for name, person in self.people.items():
                    self.perceive([name], {"kind": "room_observation", "zone": person.zone,
                        "present": sorted(n for n, p in self.people.items() if p.zone == person.zone)})
                for name, person in self.people.items():
                    person.slot_context = {
                        "own_zone": person.zone, "own_knowledge": sorted(person.knowledge),
                        "own_requests": deepcopy([r for r in self.state["requests"] if r["to"] == name]),
                        "own_responses": deepcopy([r for r in self.state["responses"] if r["from"] == name]),
                        "own_practice_completed": self.state["practice_completed"][name],
                        "own_costume_prepared": self.state["costume_prepared"][name],
                        "perceptions": deepcopy(self.perceptions[name]),
                    }
                    writer.write({"kind": "knowledge_snapshot", "t": tick, "turn": sim.turn,
                        "slot": slot, "subject": name, "own_zone": person.zone,
                        "knowledge": sorted(person.knowledge),
                        "perceptions": deepcopy(self.perceptions[name])})
                    self.perceptions[name].clear()
                writer.write({"kind": "activity_snapshot", "t": tick, "turn": sim.turn,
                    "slot": slot, "state": deepcopy(self.state),
                    "positions_inspector_only": {n: p.zone for n, p in self.people.items()}})
                if slot == self.contract["play"]["start"]:
                    self.opening_check()
                if tick == len(self.world.slots) - 1:
                    self.emit("observation_finished", self.lead, t=tick, no_next_action=True)
                    break
                for name in sim._action_order():
                    action, probability = sim.choose_action(self.people[name])
                    self.start(self.people[name], action, probability)
        return path
