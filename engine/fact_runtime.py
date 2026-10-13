"""Opt-in explicit message semantics on the real calendar runtime.

Notice times, communication durations and update rules are authored inputs.
This module does not choose probabilities, infer intentions or call a model.
"""
from copy import deepcopy
from execution.provenance import canonical, sha256, identifier
from gapengine.fact_contract import entity_identifier, validate_records

VERBS = {"send_message", "read_message", "move_and_send", "move_and_read"}
DOMAINS = {"arrival_prediction", "reason_assessment", "intent_inference", "other"}


def stable(prefix, *parts):
    return prefix + "-" + sha256(canonical(parts))[:24]


def validate_model(model, actors, start, end):
    required = {"schema_version", "capabilities", "messages", "initial_beliefs", "updates", "contexts"}
    if not isinstance(model, dict) or set(model) != required or type(model["schema_version"]) is not int or model["schema_version"] != 1:
        raise ValueError("Unsupported fact_model schema")
    if not isinstance(model["capabilities"], dict) or set(model["capabilities"]) != set(actors):
        raise ValueError("Explicit communication capabilities required for every actor")
    for actor, c in model["capabilities"].items():
        entity_identifier(actor)
        if not isinstance(c, dict) or set(c) != {"device_available", "read_while_moving", "send_while_moving", "read_minutes", "send_minutes"}:
            raise ValueError("Incomplete actor communication capabilities")
        if any(type(c[k]) is not bool for k in ("device_available", "read_while_moving", "send_while_moving")):
            raise ValueError("Communication capabilities must be booleans")
        if any(type(c[k]) is not int or not 1 <= c[k] <= 1440 for k in ("read_minutes", "send_minutes")):
            raise ValueError("Explicit bounded communication durations required")
    for field in ("messages", "initial_beliefs", "updates", "contexts"):
        if not isinstance(model[field], list) or len(model[field]) > 1000:
            raise ValueError("Bounded fact_model list required: " + field)
    identities, messages, claims = set(), {}, {}
    def ident(value):
        identifier(value)
        if value in identities:
            raise ValueError("Duplicate semantic identity")
        identities.add(value)
    def at(value):
        if type(value) is not int or not start <= value <= end:
            raise ValueError("Semantic time outside observation")
    def actor(value):
        if value not in actors:
            raise ValueError("Unknown semantic actor")
    for m in model["messages"]:
        if not isinstance(m, dict) or set(m) != {"id", "sender", "recipient", "claims", "delivery_delay_minutes", "notice_at"}:
            raise ValueError("Incomplete semantic message")
        ident(m["id"]); actor(m["sender"]); actor(m["recipient"])
        if m["sender"] == m["recipient"] or type(m["delivery_delay_minutes"]) is not int or not 0 <= m["delivery_delay_minutes"] <= 10080:
            raise ValueError("Invalid message audience/delay")
        if m["notice_at"] is not None:
            at(m["notice_at"])
        if not isinstance(m["claims"], list) or not m["claims"] or len(m["claims"]) > 32:
            raise ValueError("Bounded nonempty message claims required")
        for c in m["claims"]:
            if not isinstance(c, dict) or set(c) != {"id", "predicate", "value"} or not isinstance(c["predicate"], str) or not c["predicate"]:
                raise ValueError("Structured speaker claim required")
            ident(c["id"]); claims[c["id"]] = m["id"]
        messages[m["id"]] = m
    for field in ("initial_beliefs", "updates"):
        for b in model[field]:
            keys = {"id", "actor", "domain", "predicate", "value"}
            if field == "updates":
                keys |= {"at", "claim_ids", "rule_version"}
            if (not isinstance(b, dict) or set(b) != keys or b["domain"] not in DOMAINS
                    or not isinstance(b["predicate"], str) or not b["predicate"]):
                raise ValueError("Invalid scoped belief")
            ident(b["id"]); actor(b["actor"])
            if field == "updates":
                at(b["at"])
                if (not isinstance(b["claim_ids"], list) or not b["claim_ids"] or any(not isinstance(i, str) or i not in claims for i in b["claim_ids"])
                        or len(set(b["claim_ids"])) != len(b["claim_ids"]) or not isinstance(b["rule_version"], str) or not b["rule_version"]):
                    raise ValueError("Explicit update evidence and rule version required")
                if any(messages[claims[i]]["recipient"] != b["actor"] for i in b["claim_ids"]):
                    raise ValueError("Belief evidence must address its holder")
    for c in model["contexts"]:
        if not isinstance(c, dict) or set(c) != {"id", "actor", "at", "predicate", "value"} or not isinstance(c["predicate"], str) or not c["predicate"]:
            raise ValueError("Invalid explicit attention context")
        ident(c["id"]); actor(c["actor"]); at(c["at"])
    canonical(model)
    return model


class FactRuntime:
    def __init__(self, runtime, model):
        self.rt = runtime
        self.model = deepcopy(validate_model(model, set(runtime.people), runtime.now, runtime.end))
        self.definitions = {m["id"]: m for m in model["messages"]}
        self.state = {"schema_version": 1, "messages": {}, "communications": {}, "beliefs": {}, "applied_evidence": [], "processed": []}
        self._capturing = False

    def record(self, ident, kind, actor, content, deps=()):
        fact = {"schema_version": 1, "id": ident, "kind": kind, "actor": actor,
                "at": self.rt.now, "content": deepcopy(content), "depends_on": list(dict.fromkeys(deps))}
        self.rt.emit("fact-" + ident, "fact_record", actor, fact_record=fact)
        return ident

    def initialize(self):
        for b in self.model["initial_beliefs"]:
            self.record(b["id"], "belief", b["actor"], {"domain": b["domain"], "predicate": b["predicate"], "value": b["value"], "basis": "initial"})
            self.state["beliefs"][stable("belief", b["actor"], b["domain"], b["predicate"])] = deepcopy(b["value"])

    def actor_busy(self, actor):
        return actor in self.state["communications"]

    def can_overlap(self, actor, verb):
        active = self.state["communications"].get(actor)
        if active is None:
            return True
        kind = "read" if active["verb"] == "read_message" else "send"
        return verb == "move" and self.model["capabilities"][actor][kind + "_while_moving"]

    def _available(self, actor, verb, mid):
        cap = self.model["capabilities"][actor]
        if not cap["device_available"] or self.actor_busy(actor):
            return False
        active = self.rt.busy.get(actor)
        kind = "read" if verb == "read_message" else "send"
        if active is not None and (active["verb"] != "move" or not cap[kind + "_while_moving"]):
            return False
        m = self.definitions[mid]; s = self.state["messages"].get(mid, {})
        if kind == "send":
            return m["sender"] == actor and not s
        return m["recipient"] == actor and s.get("noticed_at") is not None and s.get("understood_at") is None

    def candidates(self, actor, baseline):
        result = [c for c in baseline if self.can_overlap(actor, c["verb"])]
        cap = self.model["capabilities"][actor]
        for mid in sorted(self.definitions):
            for verb, kind in (("send_message", "send"), ("read_message", "read")):
                if not self._available(actor, verb, mid) or self.rt.now + cap[kind + "_minutes"] > self.rt.end:
                    continue
                result.append({"verb": verb, "message_id": mid, "minutes": cap[kind + "_minutes"]})
                if actor not in self.rt.busy and cap[kind + "_while_moving"]:
                    for to, duration in sorted(self.rt.contract["travel_minutes"].get(self.rt.people[actor].zone, {}).items()):
                        if self.rt.now + max(duration, cap[kind + "_minutes"]) <= self.rt.end:
                            result.append({"verb": "move_and_" + kind, "message_id": mid, "to": to,
                                           "minutes": max(duration, cap[kind + "_minutes"]), "simultaneous": True})
        return result

    def start(self, command):
        actor, verb, ident, mid = command["actor"], command["verb"], command["id"], command["message_id"]
        offered = self.rt.candidates(actor)
        match = any(c["verb"] == verb and c.get("message_id") == mid and c.get("to") == command.get("to") for c in offered)
        self.rt.emit("decision:" + ident, "decision", actor, choice=command, candidates=offered,
                     selection_mechanism="explicit_semantic_command", knowledge_event_ids=sorted(self.rt.people[actor].knowledge))
        if not match:
            self.rt.emit("reject:" + ident, "action_rejected", actor, result="rejected", choice=command, reason="semantic_action_unavailable")
            return
        if verb.startswith("move_and_"):
            move = {"id": ident + "-move", "actor": actor, "at": self.rt.now, "verb": "move", "to": command["to"]}
            component = {"id": ident + "-message", "actor": actor, "at": self.rt.now,
                         "verb": "read_message" if verb.endswith("read") else "send_message", "message_id": mid}
            self.rt._start(move)
            self.start(component)
            return
        cap = self.model["capabilities"][actor]
        duration = cap[("read" if verb == "read_message" else "send") + "_minutes"]
        action = {**deepcopy(command), "start": self.rt.now, "finish": self.rt.now + duration}
        self.state["communications"][actor] = action
        self.rt.emit("start:" + ident, "action_started", actor, choice=command, duration_minutes=duration, finish_at=action["finish"])

    def points(self):
        points = {a["finish"] for a in self.state["communications"].values()}
        for mid, state in self.state["messages"].items():
            if state.get("delivered_at") is None:
                points.add(state["delivery_due"])
            notice = self.definitions[mid]["notice_at"]
            if (notice is not None and state.get("noticed_at") is None and state.get("delivered_at") is not None
                    and self.model["capabilities"][self.definitions[mid]["recipient"]]["device_available"]):
                points.add(notice)
        points.update(x["at"] for f in ("updates", "contexts") for x in self.model[f] if x["id"] not in self.state["processed"])
        return points

    def tick(self):
        now = self.rt.now
        for actor, a in sorted(list(self.state["communications"].items())):
            if a["finish"] != now:
                continue
            del self.state["communications"][actor]
            self.rt.emit("complete:" + a["id"], "action_completed", actor, choice=a,
                         duration_minutes=now-a["start"], started_at=a["start"])
            mid = a["message_id"]; m = self.definitions[mid]
            result = stable("result", "complete:" + a["id"])
            if a["verb"] == "send_message":
                self.state["messages"][mid] = {"sent_at": now, "delivery_due": now + m["delivery_delay_minutes"],
                                                 "delivered_at": None, "noticed_at": None, "understood_at": None}
                for c in m["claims"]:
                    self.record(c["id"], "claim", actor, {"message_id": mid, "predicate": c["predicate"], "value": c["value"], "recipient": m["recipient"]}, [result])
            else:
                s = self.state["messages"][mid]
                s["understood_at"] = now
                rid = stable("understood", mid)
                self.record(rid, "understanding", actor, {"message_id": mid, "claim_ids": [c["id"] for c in m["claims"]]},
                            [stable("notice", mid), result] + [c["id"] for c in m["claims"]])
                self.rt.people[actor].knowledge.add(rid)
        for mid, s in sorted(self.state["messages"].items()):
            m = self.definitions[mid]
            if s["delivered_at"] is None and s["delivery_due"] <= now:
                s["delivered_at"] = now
                self.record(stable("delivery", mid), "delivery", m["recipient"], {"message_id": mid, "sender": m["sender"]}, [c["id"] for c in m["claims"]])
            if s["delivered_at"] is not None and s["noticed_at"] is None and m["notice_at"] is not None and m["notice_at"] <= now:
                if self.model["capabilities"][m["recipient"]]["device_available"]:
                    s["noticed_at"] = now
                    self.record(stable("notice", mid), "notice", m["recipient"], {"message_id": mid, "rule_version": "explicit-notice-1"}, [stable("delivery", mid)])
        for c in self.model["contexts"]:
            if c["at"] == now and c["id"] not in self.state["processed"]:
                self.record(c["id"], "situation", c["actor"], {"predicate": c["predicate"], "value": c["value"]})
                self.state["processed"].append(c["id"])
        for b in self.model["updates"]:
            if b["at"] != now or b["id"] in self.state["processed"]:
                continue
            self.state["processed"].append(b["id"])
            mids = sorted({m["id"] for m in self.definitions.values() for c in m["claims"] if c["id"] in b["claim_ids"]})
            keys = [stable("evidence", b["actor"], b["domain"], b["predicate"], mid) for mid in mids]
            reason = "not_understood" if any(self.state["messages"].get(mid, {}).get("understood_at") is None for mid in mids) else None
            if any(key in self.state["applied_evidence"] for key in keys):
                reason = "same_message_evidence_already_applied"
            if reason:
                self.rt.emit("fact-skipped-" + b["id"], "fact_update_not_applied", b["actor"], reason=reason, rule_version=b["rule_version"])
                continue
            key = stable("belief", b["actor"], b["domain"], b["predicate"])
            before = deepcopy(self.state["beliefs"].get(key, {"status": "not_recorded"}))
            previous = next((r['details']['fact_record']['id'] for r in reversed(self.rt.rows)
                             if r['verb'] == 'fact_record' and r['details']['fact_record']['kind'] == 'belief'
                             and r['details']['fact_record']['actor'] == b['actor']
                             and r['details']['fact_record']['content']['domain'] == b['domain']
                             and r['details']['fact_record']['content']['predicate'] == b['predicate']), None)
            self.state["beliefs"][key] = deepcopy(b["value"])
            self.state["applied_evidence"].extend(keys)
            self.record(b["id"], "belief", b["actor"], {"domain": b["domain"], "predicate": b["predicate"], "value": b["value"],
                        "before": before, "basis": "understood_claim", "claim_ids": b["claim_ids"], "rule_version": b["rule_version"],
                        "evidence_messages": mids}, [stable("understood", mid) for mid in mids] + ([previous] if previous else []))

    def capture(self, row):
        if self._capturing or row["verb"] == "fact_record":
            return
        self._capturing = True
        try:
            actor, d, rid, verb = row["subject"], row["details"], row["id"], row["verb"]
            if verb == "decision":
                choice = d["choice"]; offered = d["candidates"]
                match = next((i for i, c in enumerate(offered) if c["verb"] == choice["verb"] and
                              all(c.get(k) == choice.get(k) for k in ("to", "message_id") if k in choice)), None)
                for i, c in enumerate(offered):
                    self.record(stable("candidate", rid, i), "action_candidate", actor,
                                {"action_id": choice["id"], "candidate": c, "feasible": True if c["verb"] in VERBS else None})
                if match is None:
                    match = len(offered)
                    self.record(stable("candidate", rid, match), "action_candidate", actor, {"action_id": choice["id"], "candidate": choice, "feasible": False})
                self.record(stable("selected", choice["id"]), "action_selected", actor,
                            {"action_id": choice["id"], "choice": choice, "selection_mechanism": d["selection_mechanism"],
                             "available_knowledge_ids": d.get('knowledge_event_ids', [])},
                            [stable("candidate", rid, match)] + [i for i in d.get('knowledge_event_ids', []) if i in self.knowledge_ids(actor)])
            elif verb == "action_started":
                choice = d["choice"]
                self.record(stable("execution", choice["id"]), "action_execution", actor, {"action_id": choice["id"], "choice": choice}, [stable("selected", choice["id"])])
            elif verb in {"action_completed", "work_interrupted"}:
                choice = d["choice"]
                self.record(stable("result", rid), "action_result", actor,
                            {"action_id": choice["id"], "outcome": "interrupted" if verb == "work_interrupted" else row["result"], "verb": choice["verb"], "details": d}, [stable("execution", choice["id"])])
            elif verb == "action_rejected":
                self.record(stable("rejected", rid), "situation", actor, {"predicate": "action_rejected", "value": d["reason"], "choice": d["choice"]}, [stable("selected", d["choice"]["id"])])
            elif verb == "promise_transition":
                t = d["promise_transition"]; p = self.rt.registry.get(t["promise_id"], t["version"])
                self.record(stable("agreement", rid), "agreement", p["terms"]["promisor"],
                            {"promise_id": t["promise_id"], "version": t["version"], "terms_hash": p["terms_hash"], "status": t["status"], "terms": p["terms"]})
        finally:
            self._capturing = False

    def export_state(self):
        return deepcopy(self.state)

    def knowledge_ids(self, actor):
        return {stable('understood', mid) for mid, s in self.state['messages'].items()
                if self.definitions[mid]['recipient'] == actor and s.get('understood_at') is not None}

    def validate_state(self, state, rows, now, commands):
        if not isinstance(state, dict) or set(state) != set(self.state) or type(state.get("schema_version")) is not int or state["schema_version"] != 1:
            raise ValueError("Unsupported semantic checkpoint")
        if any(not isinstance(state[k], dict) for k in ("messages", "communications", "beliefs")) or any(not isinstance(state[k], list) for k in ("applied_evidence", "processed")):
            raise ValueError("Invalid semantic checkpoint containers")
        if not set(state["messages"]) <= set(self.definitions) or not set(state["communications"]) <= set(self.rt.people):
            raise ValueError("Unknown message/actor in checkpoint")
        typed = []
        for row in rows:
            r = row.get("details", {}).get("fact_record")
            if r is not None:
                typed.append({**{k: v for k, v in r.items() if k != "schema_version"}, "evidence": [{"row": 1}]})
        validate_records(typed)
        byid = {r['id']: r for r in typed}
        beliefs, evidence = {}, []
        for r in typed:
            if r['kind'] == 'belief':
                c = r['content']
                beliefs[stable('belief', r['actor'], c['domain'], c['predicate'])] = c['value']
                if c.get('basis') != 'initial':
                    mids = sorted({byid[i]['content']['message_id'] for i in c['claim_ids']})
                    evidence.extend(stable('evidence', r['actor'], c['domain'], c['predicate'], m) for m in mids)
        if canonical(beliefs) != canonical(state['beliefs']) or evidence != state['applied_evidence']:
            raise ValueError('Semantic state differs from recorded belief/evidence history')
        effect_ids = {r['id'] for field in ('contexts', 'updates') for r in self.model[field]}
        processed = []
        for row in rows:
            record = row.get('details', {}).get('fact_record')
            if record is not None and record['id'] in effect_ids:
                processed.append(record['id'])
            elif row['verb'] == 'fact_update_not_applied':
                ident = row['id'].removeprefix('fact-skipped-')
                if ident not in effect_ids:
                    raise ValueError('Unknown skipped semantic effect')
                processed.append(ident)
        if processed != state['processed']:
            raise ValueError('Processed semantic effects differ from their event history')
        for mid, s in state["messages"].items():
            if not isinstance(s, dict) or set(s) != {"sent_at", "delivery_due", "delivered_at", "noticed_at", "understood_at"}:
                raise ValueError("Invalid message state")
            if type(s['sent_at']) is not int or type(s['delivery_due']) is not int:
                raise ValueError('Sent message requires explicit send/delivery times')
            previous = -1
            for field in ("sent_at", "delivered_at", "noticed_at", "understood_at"):
                value = s[field]
                if value is not None and (type(value) is not int or not previous <= value <= now):
                    raise ValueError("Invalid notification stage time")
                if value is not None:
                    previous = value
            if s["understood_at"] is not None and s["noticed_at"] is None or s["noticed_at"] is not None and s["delivered_at"] is None:
                raise ValueError("Missing prior notification stage")
            m = self.definitions[mid]
            if s['delivery_due'] != s['sent_at'] + m['delivery_delay_minutes'] or any(byid.get(c['id'], {}).get('at') != s['sent_at'] for c in m['claims']):
                raise ValueError('Message receipt does not match its sent claims')
            for field, prefix in (('delivered_at', 'delivery'), ('noticed_at', 'notice'), ('understood_at', 'understood')):
                recorded = byid.get(stable(prefix, mid))
                if (None if recorded is None else recorded['at']) != s[field]:
                    raise ValueError('Notification state differs from its event history')
        valid_ids = self.command_ids(commands)
        for actor, a in state["communications"].items():
            if (not isinstance(a, dict) or a.get("id") not in valid_ids or a.get("actor") != actor or a.get("verb") not in {"read_message", "send_message"}
                    or a.get("message_id") not in self.definitions or type(a.get("finish")) is not int or a["finish"] <= now):
                raise ValueError("Invalid pending communication")
            started = next((r for r in rows if r['id'] == 'start:' + a['id']), None)
            if started is None or canonical(a) != canonical({**started['details']['choice'], 'start': started['absolute_minute'], 'finish': started['details']['finish_at']}):
                raise ValueError('Pending communication differs from its recorded execution')
        if len(set(state["applied_evidence"])) != len(state["applied_evidence"]) or len(set(state["processed"])) != len(state["processed"]):
            raise ValueError("Duplicate semantic effects")
        canonical(state)

    @staticmethod
    def command_ids(commands):
        return {i for c in commands for i in ([c['id'], c['id']+'-move', c['id']+'-message'] if c['verb'] in {'move_and_read', 'move_and_send'} else [c['id']])}
