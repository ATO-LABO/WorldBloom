"""Fixed-personality, subjective-state policy for the existing native runtime.

No authored action sequence, random window, genome mutation, or external judge.
Every choice is sampled by Simulation's seeded RNG from current legal actions.
The coefficients below are explicit implementation heuristics, not Jev scores.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
import hashlib
import json

from gapengine.communication_repeat import CommunicationRepeat


MODEL = "fixed_goals_subjective_policy_v2"


def distance(routes, origin, destination):
    queue, seen = deque([(origin, 0)]), {origin}
    while queue:
        zone, steps = queue.popleft()
        if zone == destination:
            return steps
        for peer in routes[zone]:
            if peer not in seen:
                seen.add(peer)
                queue.append((peer, steps + 1))
    return None


class FestivalStatePolicy:
    def __init__(self, *, role, goals, public, fixed_traits, communication_config=None):
        self.role, self.goals = role, tuple(goals)
        # Explicit vocabulary for this frozen scenario, not a general-purpose
        # natural-language goal interpreter. These flags participate in scores.
        self.purposes = {
            "performance": any(any(word in goal for word in ("出演", "開演", "演劇準備")) for goal in self.goals),
            "exhibit": any("共同展示" in goal for goal in self.goals),
            "line_change": any("短縮" in goal for goal in self.goals),
            "avoid_unprepared": any("不十分" in goal for goal in self.goals),
            "sound_preparation": any("演劇準備" in goal for goal in self.goals),
        }
        self.public = deepcopy(public)
        self.fixed_traits = dict(fixed_traits)
        self.last_tick = None
        self.sent = {}
        self.responses_received = {}
        self.contributions = 0
        self.last_practice = -1
        self.last_exhibit_help = -1
        self.last_line_agreement = -1
        self.last_exhibit_agreement = -1
        self.sound_observed = False if role == "organizer" else None
        self.audits = []
        self.selection_semantics = "Simulation seeded weighted sampling; no PLANS or forced attendance"
        self.communication_repeat = (CommunicationRepeat(communication_config)
                                     if communication_config is not None else None)

    def absorb(self, subject, tick):
        if self.last_tick == tick:
            return
        for event in getattr(subject, "slot_context", {}).get("perceptions", []):
            if event["kind"] == "speech":
                key = (event["to"], event["topic"])
                if self.communication_repeat is None and event["subject"] == subject.id and event["intent"] in ("consult", "request"):
                    self.sent[key] = {"intent": event["intent"], "tick": event["t"]}
                if self.communication_repeat is None and event["to"] == subject.id and event["intent"] in ("accept", "decline"):
                    self.responses_received[(event["subject"], event["topic"])] = {
                        "intent": event["intent"], "tick": event["t"]}
                if event["intent"] == "accept" and event["topic"] == "終盤短縮希望" and (
                        event["subject"] == subject.id or event["to"] == subject.id):
                    self.last_line_agreement = event["t"]
                if event["subject"] == subject.id and event["intent"] == "accept":
                    if event["topic"] == "共同展示未完成":
                        self.last_exhibit_agreement = event["t"]
            if event["kind"] == "activity":
                if event["verb"] == "sound_check":
                    self.sound_observed = True
                if event["verb"] == "exhibit_help" and event["subject"] == subject.id:
                    self.contributions += 1
                    self.last_exhibit_help = event["t"]
                if event["verb"] == "practice" and event["subject"] == subject.id:
                    self.last_practice = event["t"]
        self.last_tick = tick

    def reweight(self, subject, world, present, weighted, *, turn, day):
        # world and peer Subject internals are intentionally not read here.
        assert subject.traits == self.fixed_traits
        tick = turn - 1
        self.absorb(subject, tick)
        context = getattr(subject, "slot_context", {})
        if self.communication_repeat is not None:
            self.communication_repeat.absorb(subject.id, context, tick)
        practice = context.get("own_practice_completed", 0)
        costume = context.get("own_costume_prepared", False)
        needs_change_rehearsal = self.last_line_agreement > self.last_practice
        owes_exhibit_help = self.last_exhibit_agreement > self.last_exhibit_help
        opening_tick = self.public["slots"].index(self.public["opening"])
        remaining = opening_tick - tick
        social, diligence = subject.traits["social"], subject.traits["diligence"]
        stubborn = subject.traits["stubbornness"]
        routes = self.public["routes"]
        stage = self.public["stage"]
        goals = []
        # These are goals/potentials, never a scheduled action or forced route.
        if self.role in ("lead", "partner", "organizer") and self.purposes["performance"]:
            upcoming = next((s for s in self.public["schedules"] if s.get("zone")
                             and self.public["slots"].index(s["time"]) >= tick), None)
            if upcoming:
                slack = self.public["slots"].index(upcoming["time"]) - tick
                importance = .4 + 3.5 / (slack + 1)
                goals.append(("known_schedule", upcoming["zone"], importance))
            if tick <= opening_tick + 1:
                goals.append(("performance_promise", stage, .4 + 4 / (max(0, remaining) + 1)))
        if self.role == "lead" and self.purposes["performance"] and not costume:
            goals.append(("costume_not_prepared", self.public["costume_zone"], 2 * diligence))
        if self.role == "organizer" and self.purposes["sound_preparation"] and not self.sound_observed:
            goals.append(("sound_not_checked_by_self", stage, 3 * diligence))
        if self.role in ("lead", "exhibitor") and self.purposes["exhibit"] and "共同展示未完成" in subject.knowledge:
            importance = (1.5 if self.role == "lead" else 3) * diligence / (1 + .25 * self.contributions)
            if owes_exhibit_help:
                importance += 1.5*diligence
            goals.append(("unfinished_exhibit_progress", self.public["exhibit_zone"], importance))
        incoming = context.get("own_requests", [])
        replied = {r["request_id"] for r in context.get("own_responses", [])}
        view = {"subject": subject.id, "role": self.role, "own_goals": list(self.goals),
            "purpose_flags_used": dict(self.purposes),
            "t": tick, "slot": self.public["slots"][tick], "own_zone": subject.zone,
            "own_knowledge": sorted(subject.knowledge), "own_inventory": dict(subject.inventory),
            "fixed_traits": dict(subject.traits), "own_practice_completed": practice,
            "own_costume_prepared": costume, "sound_observed": self.sound_observed,
            "own_exhibit_contributions_observed": self.contributions,
            "last_own_practice_t": self.last_practice,
            "last_own_exhibit_help_t": self.last_exhibit_help,
            "last_known_own_line_agreement_t": self.last_line_agreement,
            "last_own_exhibit_acceptance_t": self.last_exhibit_agreement,
            "needs_rehearsal_after_line_agreement": needs_change_rehearsal,
            "has_not_helped_after_exhibit_acceptance": owes_exhibit_help,
            "own_incoming_requests": deepcopy(incoming), "own_replied_request_ids": sorted(replied),
            "own_completed_sent_messages": [dict(to=k[0], topic=k[1], **v) for k, v in sorted(self.sent.items())],
            "received_responses": [dict(speaker=k[0], topic=k[1], **v) for k,v in sorted(self.responses_received.items())],
            "goal_potentials": [dict(goal=g, zone=z, importance=round(v,6)) for g,z,v in goals],
            "opening_slack_slots": remaining}
        scored = []
        eligible = []
        if self.communication_repeat is not None:
            view["communication_repeat"] = self.communication_repeat.view()
        for action, _ in weighted:
            terms = {}
            repeat_assessment = None
            verb, args = action.verb, action.args
            if verb == "wait":
                terms["pause_possible"] = .65
                if subject.zone == stage and self.role != "exhibitor":
                    terms["already_at_scheduled_place"] = .6
            elif verb == "move":
                terms["optional_reposition"] = .025
                for goal, target, importance in goals:
                    old = distance(routes, subject.zone, target)
                    new = distance(routes, args[0], target)
                    if old is not None and new is not None and new < old:
                        terms[goal] = importance
            elif verb == "practice" and self.role in ("lead", "partner") and self.purposes["performance"]:
                # A soft aspiration, not a changed world/ability threshold.
                terms["prepare_for_role"] = diligence * (1.8 if practice < 3 else .12)
                if needs_change_rehearsal:
                    terms["rehearse_after_agreed_line_change"] = 2*diligence
            elif verb == "costume_prepare" and self.role == "lead" and self.purposes["performance"] and not costume:
                terms["prepare_owned_role_costume"] = 5 * diligence
            elif verb == "sound_check" and self.role == "organizer" and self.purposes["sound_preparation"]:
                terms["prepare_sound_using_known_procedure"] = 6 * diligence
            elif verb == "exhibit_help" and self.role in ("lead", "exhibitor") and self.purposes["exhibit"]:
                terms["progress_on_unfinished_panel"] = (2 if self.role == "lead" else 4) * diligence / (1 + .25*self.contributions)
                if owes_exhibit_help:
                    terms["help_after_own_acceptance"] = 2*diligence
            elif verb == "dialogue":
                peer, topic, intent, channel = args
                last = self.sent.get((peer, topic))
                recently_sent = last is not None and tick - last["tick"] < 4
                response = self.responses_received.get((peer, topic))
                has_unanswered_sent = last is not None and last["intent"] == "request" and (
                    response is None or response["tick"] < last["tick"])
                if intent in ("accept", "decline"):
                    request = next((r for r in incoming if r["id"] == action.meta.get("request_id")
                                    and r["id"] not in replied and (self.communication_repeat is None or (
                                        r.get("from") == peer and r.get("to") == subject.id
                                        and r.get("topic") == topic and r.get("version", "unknown")
                                        == action.meta.get("request_version", "unknown")))), None)
                    if request:
                        if topic == "出演" and self.role == "lead":
                            concern = (int(not costume) + int(practice < 3) + int(needs_change_rehearsal)) * stubborn
                            if intent == "accept":
                                terms["honor_performance_request"] = 2*social + diligence
                            else:
                                if concern > 0 and self.purposes["avoid_unprepared"]:
                                    terms["do_not_show_unprepared_work"] = concern / (max(0,remaining)+1)
                        elif topic == "共同展示未完成" and self.role == "lead":
                            terms["exhibit_responsibility" if intent == "accept" else "reserve_time_for_play"] = (
                                2*diligence if intent == "accept" else .3 + 2/(max(0,remaining)+1))
                        elif topic == "終盤短縮希望" and self.role == "lead":
                            terms["respond_to_partner_change"] = 2*social if intent == "accept" else .25*stubborn
                        else:
                            terms["respond_to_received_request"] = social if intent == "accept" else .15
                elif self.communication_repeat is not None or (not recently_sent and not has_unanswered_sent):
                    if self.role == "partner" and self.purposes["line_change"] and peer == self.public["lead"] and topic == "終盤短縮希望":
                        if self.communication_repeat is not None or response is None or response["intent"] != "accept":
                            terms["consult_about_own_line_change"] = (3 if intent == "request" else 1)*social
                    elif self.role == "organizer" and peer == self.public["lead"] and topic == "出演":
                        if self.communication_repeat is not None or response is None or response["intent"] != "accept" or remaining <= 3:
                            terms["coordinate_known_opening"] = (.4 + 3/(max(0,remaining)+1))*diligence
                    elif self.role == "exhibitor" and peer == self.public["lead"] and topic == "共同展示未完成":
                        # Respect the known play; this is a soft preference,
                        # neither a ban nor a request intended to ruin it.
                        terms["ask_joint_exhibitor"] = (1.5 if remaining > 2 else .12)*social
                    elif self.role == "lead" and peer == self.public["exhibitor"] and topic == "共同展示未完成":
                        terms["coordinate_joint_panel"] = (1.1 if intent == "consult" else .45)*social
                    elif self.role == "lead" and peer == self.public["organizer"] and topic == "出演" and intent == "consult":
                        terms["consult_preparation_concern"] = social * (.2 + int(not costume) + int(practice<3) + int(needs_change_rehearsal))
                if terms and channel == "room":
                    terms = {k: v*1.25 for k,v in terms.items()}
                if self.communication_repeat is not None and intent in ("consult", "request"):
                    repeat_assessment = self.communication_repeat.assess(subject.id, action, context, tick)
                    if not repeat_assessment["allowed"]:
                        terms = {}
            weight = round(sum(terms.values()), 12)
            score = {"verb": verb, "args": list(args), "weight": weight,
                     "components": {k: round(v,12) for k,v in terms.items()}}
            scored.append(score)
            if repeat_assessment is not None:
                score["communication_repeat"] = repeat_assessment
            if weight > 0:
                eligible.append((action, weight, score))
        # Simulation already handles an empty reweighted set as rest/wait.
        # Never restore a suppressed dialogue just to keep a candidate alive.
        if self.communication_repeat is None:
            assert eligible
        decision = {"model": MODEL, "basis": view, "scores": scored,
            "allowed_count": len(weighted), "positive_count": len(eligible),
            "selection": self.selection_semantics,
            "not_jev_probability": True}
        fingerprint = hashlib.sha256(json.dumps(decision,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        self.audits.append({**decision, "basis_sha256": fingerprint})
        result = []
        for action, weight, score in eligible:
            action.meta["reason"] = " / ".join(score["components"])
            action.meta["decision_basis"] = {"model": MODEL, "basis_sha256": fingerprint,
                "selected": score, "audit_index": len(self.audits)-1}
            result.append((action, weight))
        return result

    def record(self, subject, action):
        if self.communication_repeat is not None:
            self.communication_repeat.record(subject.id, action)

    def export_communication_state(self):
        return self.communication_repeat.export_state() if self.communication_repeat is not None else None

    def checkpoint_sources(self):
        from pathlib import Path
        from gapengine import communication_repeat
        return {'communication_repeat':Path(communication_repeat.__file__)}

    def checkpoint_identity(self):
        from pathlib import Path
        from engine.decision_provider import digest
        config={'model':MODEL,'role':self.role,'goals':list(self.goals),'public':self.public,
                'fixed_traits':self.fixed_traits,'communication':None if self.communication_repeat is None else self.communication_repeat.config,
                'genome_kind':'none_fixed_personality_heuristics'}
        return {'adapter':'festival_fixed_personality_v1','schema_version':1,
                'source_hash':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'config_hash':digest(config),'table_hash':None,'rng_owner':'simulation'}

    def export_state(self):
        return {'schema_version':1,'last_tick':self.last_tick,
            'sent':[[list(k),deepcopy(v)] for k,v in sorted(self.sent.items())],
            'responses_received':[[list(k),deepcopy(v)] for k,v in sorted(self.responses_received.items())],
            'contributions':self.contributions,'last_practice':self.last_practice,
            'last_exhibit_help':self.last_exhibit_help,'last_line_agreement':self.last_line_agreement,
            'last_exhibit_agreement':self.last_exhibit_agreement,'sound_observed':self.sound_observed,
            'audits':deepcopy(self.audits),'communication_repeat':self.export_communication_state()}

    def restore_state(self,state):
        if not isinstance(state,dict) or set(state)!=set(self.export_state()) or state['schema_version']!=1:
            raise ValueError('Unsupported complete festival policy state')
        scalar=('last_tick','contributions','last_practice','last_exhibit_help','last_line_agreement','last_exhibit_agreement','sound_observed','audits')
        if state['last_tick'] is not None and (type(state['last_tick']) is not int or state['last_tick']<0):raise ValueError('Invalid policy time')
        for field in ('contributions','last_practice','last_exhibit_help','last_line_agreement','last_exhibit_agreement'):
            if type(state[field]) is not int or state[field]<(0 if field=='contributions' else -1):raise ValueError('Invalid policy counter')
        if state['sound_observed'] is not None and type(state['sound_observed']) is not bool:raise ValueError('Invalid sound observation')
        if not isinstance(state['audits'],list) or any(not isinstance(v,dict) for v in state['audits']):raise ValueError('Invalid policy audit history')
        histories={}
        for field in ('sent','responses_received'):
            rows=state[field]
            if (not isinstance(rows,list) or any(not isinstance(row,list) or len(row)!=2 for row in rows)
                    or any(not isinstance(k,list) or len(k)!=2 or any(type(part) is not str for part in k) or not isinstance(v,dict) for k,v in rows)
                    or len({tuple(k) for k,v in rows})!=len(rows)):raise ValueError('Invalid policy history keys')
            histories[field]={tuple(k):deepcopy(v) for k,v in rows}
        staged=deepcopy(self.communication_repeat)
        if self.communication_repeat is None:
            if state['communication_repeat'] is not None:raise ValueError('Communication configuration mismatch')
        else:staged.restore_state(deepcopy(state['communication_repeat']))
        for field in scalar:setattr(self,field,deepcopy(state[field]))
        for field,value in histories.items():setattr(self,field,value)
        self.communication_repeat=staged

    def restore_communication_state(self, state):
        if self.communication_repeat is None:
            raise ValueError("Communication state requires opt-in configuration")
        self.communication_repeat.restore_state(state)


def build_policies(contract, *, communication_config=None):
    lead = contract["play"]["lead"]
    partner = contract["play"]["partner"]
    organizer = contract["actions"]["sound_check"]["actor"]
    exhibitor = next(n for n in contract["initial_locations"] if n not in (lead,partner,organizer))
    public = {"slots": contract["clock"]["slots"], "routes": contract["routes"],
        "schedules": contract["known_schedules"], "opening": contract["play"]["start"],
        "stage": contract["play"]["zone"], "costume_zone": contract["initial_state"]["costume_zone"],
        "exhibit_zone": "展示室", "lead": lead, "partner": partner,
        "organizer": organizer, "exhibitor": exhibitor}
    roles = {lead:"lead",partner:"partner",organizer:"organizer",exhibitor:"exhibitor"}
    return {name: FestivalStatePolicy(role=role, goals=contract["goals"][name],
            public=public, fixed_traits=contract["personality"][name],
            communication_config=communication_config) for name,role in roles.items()}
