"""Versioned objective promises for calendar_promises_v2.

This ledger observes actual task-completion evidence. It neither performs work,
reserves resources nor changes relationships. Nonempty extra preconditions are
unsupported; task feasibility belongs to the frozen action/task contract.
"""
from __future__ import annotations

from copy import deepcopy
from functools import wraps
import hashlib
import json


SCHEMA_VERSION = 1
CONTRACT = "calendar_promises_v2"
TERMINAL = {"fulfilled", "breached", "cancelled", "renegotiated"}
STATUSES = TERMINAL | {"offered", "accepted", "active", "partial", "overdue"}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _id(value, label):
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > 256:
        raise ValueError(f"Invalid {label}")
    return value


def _minute(value, label):
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer minute")
    return value


def _catalog(values, label):
    if not isinstance(values, (set, frozenset, list, tuple)) or not values:
        raise ValueError(f"Nonempty {label} catalog required")
    for value in values:
        _id(value, label)
    if len(set(values)) != len(values):
        raise ValueError(f"Duplicate {label}")
    return frozenset(values)


def _atomic(method):
    """Invalid commands and evidence batches leave the registry byte-identical."""
    @wraps(method)
    def run(self, *args, **kwargs):
        staged = deepcopy(self)
        result = method(staged, *args, **kwargs)
        self.__dict__.update(staged.__dict__)
        return deepcopy(result)
    return run


class PromiseRegistry:
    def __init__(self, actors, tasks, places):
        self.actors = _catalog(actors, "actor")
        self.tasks = _catalog(tasks, "task")
        self.places = _catalog(places, "place")
        self._promises = {}
        self._events = {}
        self._completions = {}
        self._transitions = []
        self._transition_ids = set()
        self._reported_transitions = 0
        self._clock = 0
        self._evaluated_at = -1

    def _member(self, value, catalog, label):
        if _id(value, label) not in catalog:
            raise ValueError(f"Unregistered {label}: {value}")
        return value

    def _terms(self, raw):
        required = {"promise_id", "version", "proposer", "promisor", "beneficiary",
                    "acceptance_parties", "place", "start", "deadline", "grace_until", "requirements"}
        if not isinstance(raw, dict) or not required <= raw.keys() or set(raw) - required - {"outcome_reason", "preconditions"}:
            raise ValueError("Missing or unsupported promise terms")
        terms = deepcopy(raw)
        _id(terms["promise_id"], "promise_id")
        if type(terms["version"]) is not int or terms["version"] < 1:
            raise ValueError("Positive integer version required")
        for role in ("proposer", "promisor", "beneficiary"):
            self._member(terms[role], self.actors, role)
        parties = terms["acceptance_parties"]
        if not isinstance(parties, list) or not parties:
            raise ValueError("Unique acceptance_parties required")
        for actor in parties:
            self._member(actor, self.actors, "acceptance party")
        if len(parties) != len(set(parties)):
            raise ValueError("Unique acceptance_parties required")
        if not {terms["promisor"], terms["beneficiary"]} <= set(parties):
            raise ValueError("Both promisor and beneficiary must accept all terms")
        terms["acceptance_parties"] = sorted(parties)
        self._member(terms["place"], self.places, "place")
        for key in ("start", "deadline"):
            _minute(terms[key], key)
        if terms["start"] > terms["deadline"]:
            raise ValueError("Deadline precedes fulfillment window")
        if terms["grace_until"] is not None:
            _minute(terms["grace_until"], "grace_until")
            if terms["grace_until"] < terms["deadline"]:
                raise ValueError("Grace precedes deadline")
        if terms.get("preconditions", []) not in ([], {}):
            raise ValueError("unsupported: additional promise preconditions")
        terms["preconditions"] = []
        reason = terms.get("outcome_reason", "choice_or_unconfirmed")
        # Causal claims need external evidence rather than an unsupported label.
        if reason != "choice_or_unconfirmed":
            raise ValueError("unsupported: evidenced outcome_reason required for causal claims")
        terms["outcome_reason"] = reason
        requirements = terms["requirements"]
        if not isinstance(requirements, list) or not 1 <= len(requirements) <= 100:
            raise ValueError("Nonempty bounded concrete requirements required")
        normalized = []
        keys = set()
        for raw_requirement in requirements:
            required_fields = {"task_id", "units", "contribution_by"}
            if (not isinstance(raw_requirement, dict) or not required_fields <= raw_requirement.keys()
                    or set(raw_requirement) - required_fields - {"delivered_to"}):
                raise ValueError("Unsupported concrete requirement")
            requirement = deepcopy(raw_requirement)
            self._member(requirement["task_id"], self.tasks, "task_id")
            self._member(requirement["contribution_by"], self.actors, "contribution_by")
            if type(requirement["units"]) is not int or requirement["units"] <= 0:
                raise ValueError("Positive integer units required")
            recipient = requirement.get("delivered_to")
            if recipient is not None:
                self._member(recipient, self.actors, "delivered_to")
            requirement["delivered_to"] = recipient
            key = (requirement["task_id"], requirement["contribution_by"], recipient)
            if key in keys:
                raise ValueError("Duplicate concrete requirement")
            keys.add(key)
            normalized.append(requirement)
        terms["requirements"] = sorted(normalized, key=_json)
        return terms

    def terms_hash(self, terms):
        return _hash(self._terms(terms))

    def _signature(self, terms, *, deadline=True):
        fields = {key: terms[key] for key in ("promisor", "beneficiary", "place")}
        # Quantity changes belong to the existing obligation's next version,
        # rather than a second promise about the same concrete task and parties.
        fields["requirements"] = [{key: r[key] for key in ("task_id", "contribution_by", "delivered_to")}
                                  for r in terms["requirements"]]
        if deadline:
            fields["deadline"] = terms["deadline"]
        return _hash(fields)

    def _entry(self, promise_id, version=None):
        _id(promise_id, "promise_id")
        if promise_id not in self._promises:
            raise ValueError("Unknown promise_id")
        entry = self._promises[promise_id]
        if version is None:
            version = entry["current_version"]
        if type(version) is not int or str(version) not in entry["versions"]:
            raise ValueError("Unknown promise version")
        return entry, entry["versions"][str(version)]

    def get(self, promise_id, version=None):
        return deepcopy(self._entry(promise_id, version)[1])

    def _begin(self, event_id, request, now):
        _id(event_id, "event_id")
        _minute(now, "now")
        prior = self._events.get(event_id)
        if prior is not None:
            if prior["request"] != request:
                raise ValueError("Conflicting reuse of event_id")
            return deepcopy(prior["result"])
        if now < self._clock:
            raise ValueError("Time reversal requires restoring an earlier checkpoint")
        return None

    def _finish(self, event_id, request, now, record):
        self._clock = now
        result = deepcopy(record)
        self._events[event_id] = {"request": deepcopy(request), "result": result}
        return result

    def _new_record(self, terms, now, event_id):
        return {"promise_id": terms["promise_id"], "version": terms["version"], "terms": terms,
                "terms_hash": _hash(terms), "status": "offered", "created_at": now,
                "effective_at": None, "closed_at": None, "source_event_ids": [event_id],
                "evidence_start": None, "inherited_evidence_ids": [],
                "acceptances": {}, "cancel_acceptances": {}, "evidence_ids": [],
                "progress": [{"task_id": r["task_id"], "contribution_by": r["contribution_by"],
                              "delivered_to": r["delivered_to"], "required_units": r["units"],
                              "completed_units": 0} for r in terms["requirements"]],
                "due_met": None, "is_overdue": False, "was_overdue": False,
                "outcome_reason": terms["outcome_reason"], "history": [], "evaluated_through": now - 1}

    def _emit(self, record, status, at, evidence_ids=(), transition=None):
        name = transition or status
        key = [record["promise_id"], record["version"], record["terms"]["deadline"], name]
        effect_id = "promise-effect-" + _hash(key)
        if effect_id in self._transition_ids:
            return
        record["status"] = status
        row = {"id": effect_id, "effect_id": effect_id, "promise_id": record["promise_id"],
               "version": record["version"], "status": status, "transition": name, "at": at,
               "deadline": record["terms"]["deadline"], "terms_hash": record["terms_hash"],
               "evidence_ids": sorted(set(evidence_ids)), "progress": deepcopy(record["progress"]),
               "due_met": record["due_met"], "is_overdue": record["is_overdue"],
               "outcome_reason": record["outcome_reason"]}
        self._transition_ids.add(effect_id)
        self._transitions.append(deepcopy(row))
        record["history"].append(row)

    @_atomic
    def offer(self, terms, now, event_id):
        terms = self._terms(terms)
        request = {"operation": "offer", "terms": terms, "now": now}
        cached = self._begin(event_id, request, now)
        if cached is not None:
            return cached
        if terms["version"] != 1 or terms["promise_id"] in self._promises:
            raise ValueError("New promise_id and version1 required")
        if now > terms["deadline"]:
            raise ValueError("Cannot offer an already expired deadline")
        for entry in self._promises.values():
            for record in entry["versions"].values():
                if self._signature(record["terms"]) == self._signature(terms):
                    raise ValueError(f"Duplicate concrete promise; reference {record['promise_id']}")
                if (record["status"] == "fulfilled" and
                        self._signature(record["terms"], deadline=False) == self._signature(terms, deadline=False)):
                    raise ValueError(f"Completed work is already referenced by {record['promise_id']}")
        record = self._new_record(terms, now, event_id)
        self._promises[terms["promise_id"]] = {"current_version": 1, "pending_version": None,
                                               "versions": {"1": record}}
        self._emit(record, "offered", now, [event_id])
        return self._finish(event_id, request, now, record)

    @_atomic
    def propose_change(self, promise_id, new_terms, now, event_id):
        terms = self._terms(new_terms)
        request = {"operation": "propose_change", "promise_id": promise_id, "terms": terms, "now": now}
        cached = self._begin(event_id, request, now)
        if cached is not None:
            return cached
        entry, current = self._entry(promise_id)
        if current["status"] in {"fulfilled", "cancelled", "renegotiated"}:
            raise ValueError("Closed obligation cannot be changed")
        if current["effective_at"] is None:
            raise ValueError("An unaccepted offer is not an obligation to renegotiate")
        if (terms["promise_id"] != promise_id or terms["version"] != entry["current_version"] + 1
                or entry["pending_version"] is not None):
            raise ValueError("Exactly one next-version proposal allowed")
        if (not set(current["terms"]["acceptance_parties"]) <= set(terms["acceptance_parties"])
                or terms["proposer"] not in current["terms"]["acceptance_parties"]):
            raise ValueError("Existing parties must agree to release their old obligation")
        if now > terms["deadline"]:
            raise ValueError("New deadline already expired")
        record = self._new_record(terms, now, event_id)
        entry["pending_version"] = terms["version"]
        entry["versions"][str(terms["version"])] = record
        self._emit(record, "offered", now, [event_id])
        return self._finish(event_id, request, now, record)

    def _accept(self, promise_id, version, actor, terms_hash, now, event_id, operation):
        request = {"operation": operation, "promise_id": promise_id, "version": version,
                   "actor": actor, "terms_hash": terms_hash, "now": now}
        cached = self._begin(event_id, request, now)
        if cached is not None:
            return cached
        entry, record = self._entry(promise_id, version)
        change = operation == "accept_change"
        if change != (entry["pending_version"] == version):
            raise ValueError("Use acceptance appropriate to current offer/change")
        if actor not in record["terms"]["acceptance_parties"] or terms_hash != record["terms_hash"]:
            raise ValueError("Acceptance requires a registered party and identical terms_hash")
        if now > record["terms"]["deadline"]:
            raise ValueError("Expired offer needs a new concrete deadline")
        if change:
            old = entry["versions"][str(entry["current_version"])]
            if old["status"] in {"fulfilled", "cancelled", "renegotiated"}:
                raise ValueError("Old obligation was closed while its change was pending")
        if record["status"] != "offered":
            if actor in record["acceptances"] and record["status"] not in TERMINAL:
                return self._finish(event_id, request, now, record)
            raise ValueError("Offer is closed")
        if actor in record["acceptances"]:
            return self._finish(event_id, request, now, record)
        record["acceptances"][actor] = {"event_id": event_id, "at": now, "terms_hash": terms_hash}
        record["source_event_ids"].append(event_id)
        if set(record["acceptances"]) == set(record["terms"]["acceptance_parties"]):
            if change:
                old = entry["versions"][str(entry["current_version"])]
                self._advance(old, now)
                if old["status"] in {"fulfilled", "cancelled", "renegotiated"}:
                    raise ValueError("Old obligation completed before change became effective")
                old["closed_at"] = now
                self._emit(old, "renegotiated", now, record["source_event_ids"])
                record["replaces_version"] = old["version"]
                # Reuse only actual partial evidence admitted by the old
                # obligation, never arbitrary pre-agreement work on other tasks.
                record["evidence_start"] = old["evidence_start"]
                record["inherited_evidence_ids"] = list(old["evidence_ids"])
                old["replacement_version"] = version
                entry["current_version"] = version
                entry["pending_version"] = None
            record["effective_at"] = now
            if record["evidence_start"] is None:
                record["evidence_start"] = now
            record["evaluated_through"] = now - 1
            self._emit(record, "accepted", now, record["source_event_ids"])
            if record["terms"]["start"] <= now:
                self._emit(record, "active", now, record["source_event_ids"])
        return self._finish(event_id, request, now, record)

    @_atomic
    def accept(self, promise_id, version, actor, terms_hash, now, event_id):
        return self._accept(promise_id, version, actor, terms_hash, now, event_id, "accept")

    @_atomic
    def accept_change(self, promise_id, version, actor, terms_hash, now, event_id):
        return self._accept(promise_id, version, actor, terms_hash, now, event_id, "accept_change")

    @_atomic
    def cancel(self, promise_id, version, actor, terms_hash, now, event_id):
        request = {"operation": "cancel", "promise_id": promise_id, "version": version,
                   "actor": actor, "terms_hash": terms_hash, "now": now}
        cached = self._begin(event_id, request, now)
        if cached is not None:
            return cached
        entry, record = self._entry(promise_id, version)
        if (version != entry["current_version"] or record["status"] in TERMINAL
                or record["effective_at"] is None):
            raise ValueError("Only live accepted obligations can be released")
        if actor not in record["terms"]["acceptance_parties"] or terms_hash != record["terms_hash"]:
            raise ValueError("Cancellation requires all parties' same-hash consent")
        if actor not in record["cancel_acceptances"]:
            record["cancel_acceptances"][actor] = {"event_id": event_id, "at": now, "terms_hash": terms_hash}
            record["source_event_ids"].append(event_id)
        if set(record["cancel_acceptances"]) == set(record["terms"]["acceptance_parties"]):
            self._advance(record, now)
            if record["status"] in TERMINAL:
                raise ValueError("Deadline/fulfillment already closed the obligation")
            record["closed_at"] = now
            self._emit(record, "cancelled", now, record["source_event_ids"])
        return self._finish(event_id, request, now, record)

    def _completion(self, raw):
        required = {"id", "task_id", "actor", "units", "at", "place"}
        if not isinstance(raw, dict) or not required <= raw.keys() or set(raw) - required - {"delivered_to"}:
            raise ValueError("unsupported: completion evidence schema")
        event = deepcopy(raw)
        _id(event["id"], "completion event id")
        self._member(event["task_id"], self.tasks, "task_id")
        self._member(event["actor"], self.actors, "actor")
        self._member(event["place"], self.places, "place")
        _minute(event["at"], "completion minute")
        if type(event["units"]) is not int or event["units"] <= 0:
            raise ValueError("Positive integer completion units required")
        recipient = event.get("delivered_to")
        if recipient is not None:
            self._member(recipient, self.actors, "delivered_to")
        event["delivered_to"] = recipient
        return event

    def _progress(self, record, at):
        terms = record["terms"]
        start = max(terms["start"], record["evidence_start"])
        progress = []
        evidence = set()
        for requirement in terms["requirements"]:
            units = 0
            for event in sorted(self._completions.values(), key=lambda e: (e["at"], e["id"])):
                if (not start <= event["at"] <= at or event["place"] != terms["place"]
                        or (event["at"] < record["effective_at"] and event["id"] not in record["inherited_evidence_ids"])
                        or event["task_id"] != requirement["task_id"]
                        or event["actor"] != requirement["contribution_by"]
                        or (requirement["delivered_to"] is not None and event["delivered_to"] != requirement["delivered_to"])):
                    continue
                if units < requirement["units"]:
                    evidence.add(event["id"])
                    units = min(requirement["units"], units + event["units"])
            progress.append({"task_id": requirement["task_id"], "contribution_by": requirement["contribution_by"],
                             "delivered_to": requirement["delivered_to"], "required_units": requirement["units"],
                             "completed_units": units})
        return progress, sorted(evidence)

    def _advance(self, record, now):
        if record["effective_at"] is None or record["status"] in TERMINAL:
            return
        terms = record["terms"]
        cursor = record["evaluated_through"]
        start = max(terms["start"], record["effective_at"])
        times = {start, terms["deadline"], now}
        if terms["grace_until"] is not None:
            times.add(terms["grace_until"])
        times.update(e["at"] for e in self._completions.values() if e["at"] >= start)
        for at in sorted(t for t in times if cursor < t <= now and t >= record["effective_at"]):
            if record["status"] == "accepted" and at >= start:
                self._emit(record, "active", at, record["source_event_ids"])
            progress, evidence = self._progress(record, at)
            if progress != record["progress"]:
                record["progress"] = progress
                record["evidence_ids"] = evidence
                self._emit(record, record["status"], at, evidence, "progress:" + _hash(progress))
            if at >= start and all(p["completed_units"] == p["required_units"] for p in progress):
                record["due_met"] = at <= terms["deadline"]
                record["is_overdue"] = False
                record["closed_at"] = at
                self._emit(record, "fulfilled", at, evidence)
                break
            if at >= terms["deadline"] and record["status"] not in {"partial", "overdue"}:
                record["due_met"] = False
                record["is_overdue"] = record["was_overdue"] = True
                status = "partial" if any(p["completed_units"] for p in progress) else "overdue"
                self._emit(record, status, at, evidence)
            end = terms["grace_until"] if terms["grace_until"] is not None else terms["deadline"]
            if at >= end:
                record["closed_at"] = at
                self._emit(record, "breached", at, evidence)
                break
        record["evaluated_through"] = now

    @_atomic
    def evaluate(self, now, ledger, events=()):
        _minute(now, "now")
        if now < self._clock or now < self._evaluated_at:
            raise ValueError("Time reversal requires restoring an earlier checkpoint")
        if not isinstance(ledger, dict) or not isinstance(ledger.get("completion_events"), list):
            raise ValueError("Actual ledger completion_events list required")
        if not isinstance(events, (list, tuple)):
            raise ValueError("Completion events must be a sequence")
        for raw in [*ledger["completion_events"], *events]:
            event = self._completion(raw)
            prior = self._completions.get(event["id"])
            if prior is not None and prior != event:
                raise ValueError("Conflicting duplicate completion event id")
            if prior is None and event["at"] <= self._evaluated_at:
                raise ValueError("Retrospective completion evidence unsupported")
            self._completions[event["id"]] = event
        # Include command-created acceptance/cancellation/renegotiation events,
        # not only transitions produced by this evaluation. The persistent
        # cursor keeps runtime observations and resumed effects exactly once.
        beginning = self._reported_transitions
        # Preserve global chronological transition ordering even when a caller
        # jumps across several event/deadline boundaries in one evaluation.
        boundaries = {now}
        boundaries.update(e["at"] for e in self._completions.values() if e["at"] <= now)
        for entry in self._promises.values():
            record = entry["versions"][str(entry["current_version"])]
            if record["effective_at"] is not None and record["status"] not in TERMINAL:
                boundaries.update(t for t in (max(record["effective_at"], record["terms"]["start"]),
                                              record["terms"]["deadline"], record["terms"]["grace_until"])
                                  if t is not None and t <= now)
        for at in sorted(t for t in boundaries if t >= self._evaluated_at):
            for promise_id in sorted(self._promises):
                entry = self._promises[promise_id]
                self._advance(entry["versions"][str(entry["current_version"])], at)
        self._clock = now
        self._evaluated_at = now
        self._reported_transitions = len(self._transitions)
        return self._transitions[beginning:]

    def export_state(self):
        body = {"contract": CONTRACT, "schema_version": SCHEMA_VERSION,
                "catalogs": {"actors": sorted(self.actors), "tasks": sorted(self.tasks), "places": sorted(self.places)},
                "promises": deepcopy(self._promises), "events": deepcopy(self._events),
                "completions": deepcopy(self._completions), "transitions": deepcopy(self._transitions),
                "reported_transitions": self._reported_transitions,
                "clock": self._clock, "evaluated_at": self._evaluated_at}
        return {**body, "state_hash": _hash(body)}

    @classmethod
    def from_state(cls, raw):
        try:
            return cls._restore(raw)
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("Malformed promise checkpoint") from error

    @classmethod
    def _restore(cls, raw):
        if not isinstance(raw, dict):
            raise ValueError("Promise state mapping required")
        state = deepcopy(raw)
        checksum = state.pop("state_hash", None)
        fields = {"contract", "schema_version", "catalogs", "promises", "events", "completions", "reported_transitions",
                  "transitions", "clock", "evaluated_at"}
        if set(state) != fields or state["contract"] != CONTRACT or type(state["schema_version"]) is not int or state["schema_version"] != SCHEMA_VERSION:
            raise ValueError("Unsupported promise checkpoint version or fields")
        if checksum != _hash(state):
            raise ValueError("Corrupt promise checkpoint")
        if not isinstance(state["catalogs"], dict) or set(state["catalogs"]) != {"actors", "tasks", "places"}:
            raise ValueError("Invalid promise catalogs")
        registry = cls(**state["catalogs"])
        _minute(state["clock"], "clock")
        if type(state["evaluated_at"]) is not int or not -1 <= state["evaluated_at"] <= state["clock"]:
            raise ValueError("Invalid evaluation clock")
        for key in ("promises", "events", "completions"):
            if not isinstance(state[key], dict):
                raise ValueError("Invalid checkpoint mappings")
        for event_id, event in state["completions"].items():
            if event != registry._completion(event) or event["id"] != event_id:
                raise ValueError("Invalid completion checkpoint")
        if not isinstance(state["transitions"], list):
            raise ValueError("Invalid transitions")
        if type(state["reported_transitions"]) is not int or not 0 <= state["reported_transitions"] <= len(state["transitions"]):
            raise ValueError("Invalid transition reporting cursor")
        seen = set()
        for row in state["transitions"]:
            expected = "promise-effect-" + _hash([row["promise_id"], row["version"], row["deadline"], row["transition"]])
            if row["id"] != expected or row["effect_id"] != expected or expected in seen or row["status"] not in STATUSES:
                raise ValueError("Invalid or duplicate transition effect")
            _minute(row["at"], "transition time")
            if row["at"] > state["clock"]:
                raise ValueError("Future transition")
            seen.add(expected)
        all_history = []
        for promise_id, entry in state["promises"].items():
            if set(entry) != {"current_version", "pending_version", "versions"} or not isinstance(entry["versions"], dict):
                raise ValueError("Invalid version chain")
            if (type(entry["current_version"]) is not int or str(entry["current_version"]) not in entry["versions"]
                    or (entry["pending_version"] is not None and entry["pending_version"] != entry["current_version"] + 1)):
                raise ValueError("Invalid current or pending version")
            for version, record in entry["versions"].items():
                terms = registry._terms(record["terms"])
                if (terms != record["terms"] or record["terms_hash"] != _hash(terms)
                        or terms["promise_id"] != promise_id or str(terms["version"]) != version
                        or record["version"] != terms["version"] or record["promise_id"] != promise_id
                        or record["status"] not in STATUSES):
                    raise ValueError("Invalid version terms")
                _minute(record["created_at"], "created_at")
                if record["created_at"] > state["clock"]:
                    raise ValueError("Future promise creation")
                if record["effective_at"] is not None:
                    _minute(record["effective_at"], "effective_at")
                    if not record["created_at"] <= record["effective_at"] <= state["clock"]:
                        raise ValueError("Invalid effective time")
                    if set(record["acceptances"]) != set(terms["acceptance_parties"]):
                        raise ValueError("Obligation missing party acceptance")
                    _minute(record["evidence_start"], "evidence_start")
                    if record["evidence_start"] > record["effective_at"]:
                        raise ValueError("Evidence floor exceeds effective time")
                elif record["status"] != "offered":
                    raise ValueError("Unaccepted obligation state")
                elif record["evidence_start"] is not None or record["inherited_evidence_ids"]:
                    raise ValueError("Unaccepted offer cannot inherit work evidence")
                inherited = record["inherited_evidence_ids"]
                if not isinstance(inherited, list) or len(inherited) != len(set(inherited)):
                    raise ValueError("Invalid inherited evidence")
                if "replaces_version" in record:
                    previous = entry["versions"][str(record["replaces_version"])]
                    if not set(inherited) <= set(previous["evidence_ids"]):
                        raise ValueError("Inherited evidence was not admitted by old obligation")
                elif inherited:
                    raise ValueError("Initial offer cannot inherit completed work")
                if not set(inherited) <= set(state["completions"]):
                    raise ValueError("Missing inherited actual completion")
                if record["closed_at"] is not None:
                    _minute(record["closed_at"], "closed_at")
                    if record["closed_at"] > state["clock"] or record["status"] not in TERMINAL:
                        raise ValueError("Invalid terminal time")
                elif record["status"] in TERMINAL:
                    raise ValueError("Terminal obligation lacks closed_at")
                if (type(record["is_overdue"]) is not bool or type(record["was_overdue"]) is not bool
                        or (record["due_met"] is not None and type(record["due_met"]) is not bool)):
                    raise ValueError("Invalid outcome flags")
                expected_progress = [{"task_id": r["task_id"], "contribution_by": r["contribution_by"],
                                      "delivered_to": r["delivered_to"], "required_units": r["units"]}
                                     for r in terms["requirements"]]
                if not isinstance(record["progress"], list) or len(record["progress"]) != len(expected_progress):
                    raise ValueError("Invalid progress requirements")
                for p, expected_p in zip(record["progress"], expected_progress):
                    if (set(p) != set(expected_p) | {"completed_units"}
                            or {key: p[key] for key in expected_p} != expected_p
                            or type(p["completed_units"]) is not int
                            or not 0 <= p["completed_units"] <= p["required_units"]):
                        raise ValueError("Invalid saved progress")
                for approvals in (record["acceptances"], record["cancel_acceptances"]):
                    for actor, approval in approvals.items():
                        if actor not in terms["acceptance_parties"] or approval["terms_hash"] != record["terms_hash"]:
                            raise ValueError("Invalid saved acceptance")
                        _minute(approval["at"], "acceptance time")
                if (not record["history"] or record["history"][-1]["status"] != record["status"]
                        or record["history"][-1]["progress"] != record["progress"]):
                    raise ValueError("State differs from transition history")
                all_history.extend(record["history"])
        if sorted(all_history, key=lambda r: r["id"]) != sorted(state["transitions"], key=lambda r: r["id"]):
            raise ValueError("Version history differs from global transitions")
        for event_id, cached in state["events"].items():
            _id(event_id, "saved command id")
            if not isinstance(cached, dict) or set(cached) != {"request", "result"}:
                raise ValueError("Invalid saved command")
            request, result = cached["request"], cached["result"]
            if request["operation"] not in {"offer", "propose_change", "accept", "accept_change", "cancel"}:
                raise ValueError("Unsupported saved command")
            _minute(request["now"], "command minute")
            if request["now"] > state["clock"]:
                raise ValueError("Future saved command")
            actual = state["promises"][result["promise_id"]]["versions"][str(result["version"])]
            if (result["terms"] != actual["terms"] or result["terms_hash"] != actual["terms_hash"]
                    or not result["history"]
                    or result["history"] != actual["history"][:len(result["history"])]
                    or result["status"] != result["history"][-1]["status"]
                    or result["progress"] != result["history"][-1]["progress"]):
                raise ValueError("Invalid cached command outcome")
        registry._promises, registry._events, registry._completions = state["promises"], state["events"], state["completions"]
        registry._transitions, registry._transition_ids = state["transitions"], seen
        registry._reported_transitions = state["reported_transitions"]
        registry._clock, registry._evaluated_at = state["clock"], state["evaluated_at"]
        return registry
