"""Actor-local, opt-in repeat control; selections are never sent messages.

The caller owns issue/evidence/deadline attribution. Historical speech without
metadata remains unclassified. No world or other actor object is accepted here.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import json


VERSION = "actor_communication_repeat_v1"
DECISION_VERSION = "actor_communication_repeat_v2"
IDENTITY_VERSION = "actor_known_communication_v1"


@dataclass(frozen=True)
class CommunicationIdentity:
    """Immutable actor-known identity; null explicitly means a new request.

    Serialized copies are validated at each boundary. This schema is separate
    from repeat policy v1: enabling it requires DECISION_VERSION, changing the
    configuration hash and rendered candidate/cache input.
    """
    version: str
    actor_id: str
    epoch: str
    to: str
    topic: str
    intent: str
    request_version: str
    original_request_id: str | None

    def __post_init__(self):
        if self.version != IDENTITY_VERSION or self.intent not in ("consult", "request"):
            raise ValueError("Unsupported communication decision identity")
        if any(not isinstance(v, str) or not v for v in
               (self.actor_id, self.epoch, self.to, self.topic, self.request_version)):
            raise ValueError("Invalid communication decision identity")
        if self.original_request_id is not None and (
                not isinstance(self.original_request_id, str) or not self.original_request_id
                or self.original_request_id == "unknown" or self.intent != "request"):
            raise ValueError("Invalid original request identity")


def decision_identity(meta, actor_id):
    """Parse only the explicitly versioned payload, never infer from topic."""
    raw = meta.get("communication_identity")
    if raw is None:
        return None
    try:
        identity = CommunicationIdentity(**raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid communication decision payload") from exc
    if identity.actor_id != actor_id or any(getattr(identity, k) != meta.get(k)
            for k in ("actor_id", "epoch", "to", "topic", "intent", "request_version")):
        raise ValueError("Communication decision owner/content mismatch")
    if meta.get("token") != digest({k: v for k, v in meta.items() if k != "token"}):
        raise ValueError("Communication decision payload changed")
    return identity


COMPLETION_FIELDS = {"completed_t", "consumed_evidence_refs", "request_id",
                     "completion_receipt", "completion_sha256"}


def completion_digest(entry):
    # Integrity check, not authentication: a fully fabricated consistent
    # snapshot is outside this actor-local restoration trust boundary.
    return digest({k: entry[k] for k in
                   ("token", "completed_t", "request_id", "completion_receipt")})


def validate_completion(entry, actor_id):
    """A first request needs its actual completion receipt, not a topic match."""
    if not COMPLETION_FIELDS.issubset(entry):
        raise ValueError("Missing communication completion fields")
    request_id = entry["request_id"]
    if not isinstance(request_id, str) or not request_id:
        raise ValueError("Missing communication canonical request identity")
    payload = {k: v for k, v in entry.items() if k not in COMPLETION_FIELDS}
    identity = decision_identity(payload, actor_id)
    if identity is None:
        raise ValueError("Missing completed communication decision identity")
    receipt = entry["completion_receipt"]
    canonical = None
    if receipt is not None:
        if (not isinstance(receipt, dict) or receipt.get("kind") != "own_communication_completed"
                or receipt.get("result") != "completed" or receipt.get("subject") != actor_id
                or receipt.get("t") != entry["completed_t"]
                or receipt.get("communication_repeat") != payload):
            raise ValueError("Communication completion receipt mismatch")
        canonical = receipt.get("canonical_request_id")
        if canonical is not None and (not isinstance(canonical, str) or not canonical):
            raise ValueError("Invalid communication completion receipt identity")
    expected = canonical if canonical is not None else "unknown"
    if request_id != expected:
        raise ValueError("Communication request identity disagrees with its receipt")
    if identity.original_request_id is not None and request_id != identity.original_request_id:
        raise ValueError("Communication completion changed original request")
    if entry["completion_sha256"] != completion_digest(entry):
        raise ValueError("Communication completion integrity mismatch")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


class CommunicationRepeat:
    def __init__(self, config):
        allowed = {"version", "epoch", "minimum_repeat_interval_slots",
                   "deadline_window_slots", "actor_tendencies", "completion_mode"}
        if not isinstance(config, dict) or set(config) - allowed:
            raise ValueError("Unsupported communication configuration")
        if config.get("version") not in (VERSION, DECISION_VERSION) or not isinstance(config.get("epoch"), str) or not config["epoch"]:
            raise ValueError("Communication version and caller-supplied run epoch required")
        self.config = {"version": config["version"], "epoch": config["epoch"],
            "minimum_repeat_interval_slots": config.get("minimum_repeat_interval_slots", 4),
            "deadline_window_slots": config.get("deadline_window_slots", 3),
            "actor_tendencies": deepcopy(config.get("actor_tendencies", {})),
            "completion_mode": config.get("completion_mode", "explicit")}
        for key in ("minimum_repeat_interval_slots", "deadline_window_slots"):
            if not integer(self.config[key]) or self.config[key] < 0:
                raise ValueError("Communication slot thresholds must be nonnegative integers")
        if self.config["completion_mode"] not in ("explicit", "native_one_slot"):
            raise ValueError("Unsupported completion mode")
        tendencies = self.config["actor_tendencies"]
        if not isinstance(tendencies, dict) or any(
                not isinstance(actor, str) or tendency not in ("reminder", "reconfirm")
                for actor, tendency in tendencies.items()):
            raise ValueError("Only explicitly authored per-actor reminder/reconfirm tendencies supported")
        self.config_hash = digest(self.config)
        self.owner = None
        self.sent = {}
        self.pending = {}
        self.observed = {}

    def bind(self, actor_id):
        if self.owner is not None and self.owner != actor_id:
            raise ValueError("Communication state belongs to another actor")
        self.owner = actor_id

    def issue_id(self, actor_id, peer, topic):
        # A new policy-authored issue, never an attribution of an old log.
        return "festival-auto:" + digest([actor_id, peer, topic])

    def _evidence(self, actor_id, issue_id, context, tick):
        refs = set()
        for evidence in context.get("communication_evidence", []):
            if (evidence.get("actor_id") == actor_id and evidence.get("issue_id") == issue_id
                    and evidence.get("epoch") == self.config["epoch"]
                    and integer(evidence.get("observed_t")) and evidence["observed_t"] <= tick
                    and isinstance(evidence.get("evidence_id"), str) and evidence["evidence_id"]):
                refs.add(evidence["evidence_id"])
        return sorted(refs)

    def _deadline(self, actor_id, issue_id, context, tick):
        # Missing, past, or unobserved deadlines do not mean "near".
        near = []
        for deadline in context.get("communication_deadlines", []):
            due, known = deadline.get("deadline_t"), deadline.get("observed_t")
            if (deadline.get("actor_id") == actor_id and deadline.get("issue_id") == issue_id
                    and deadline.get("epoch") == self.config["epoch"]
                    and integer(due) and integer(known) and known <= tick
                    and 0 <= due - tick <= self.config["deadline_window_slots"]):
                near.append(due)
        return min(near) if near else None

    def assess(self, actor_id, action, context, tick):
        self.bind(actor_id)
        peer, topic, intent, channel = action.args
        issue_id = self.issue_id(actor_id, peer, topic)
        # Changing transport is not changing content. Consult/request stay
        # distinct communicative acts; a consult has unknown reply expectation.
        identity = dict(actor_id=actor_id, to=peer, topic=topic, intent=intent,
                        issue_id=issue_id, content_version=1, epoch=self.config["epoch"])
        key = digest(identity)
        last = self.sent.get(key)
        if last is not None and self.config["version"] == DECISION_VERSION:
            validate_completion(last, actor_id)
        refs = self._evidence(actor_id, issue_id, context, tick)
        deadline = self._deadline(actor_id, issue_id, context, tick)
        tendency = self.config["actor_tendencies"].get(actor_id)
        reason, classification, allowed = "first_transmission", "automatic", True
        if any(p["key"] == key for p in self.pending.values()):
            reason, allowed = "own_selected_pending", False
        elif last is not None:
            allowed = False
            reason = "unchanged_automatic_repeat"
            if tick - last["completed_t"] < self.config["minimum_repeat_interval_slots"]:
                reason = "minimum_repeat_interval"
            elif set(refs) - set(last["consumed_evidence_refs"]):
                allowed, reason, classification = True, "explicit_new_actor_evidence", "update"
            elif deadline is not None:
                allowed, reason, classification = True, "known_deadline_near", "reminder"
            elif tendency:
                allowed, reason, classification = True, "explicit_actor_tendency", tendency
        result = {**identity, "key": key, "token": digest([key, tick, channel]),
                "channel": channel, "allowed": allowed,
                "classification": classification, "repeat_reason": reason,
                "reply_expected": True if intent == "request" else "unknown",
                # content_version is our new policy content schema. Native
                # request records have no request-version field to observe.
                "request_version": "unknown",
                "evidence_refs": refs, "known_deadline_t": deadline,
                "decision_t": tick, "config_sha256": self.config_hash}
        if self.config["version"] == DECISION_VERSION:
            original = last.get("request_id") if last and intent == "request" else None
            if original == "unknown":
                # Completion without an actor-visible canonical receipt cannot
                # authorize an identity-linked repeat, even with a matching topic.
                original = None
                result["allowed"] = False
                result["repeat_reason"] = "original_request_not_observed"
            result["communication_identity"] = asdict(CommunicationIdentity(
                IDENTITY_VERSION, actor_id, self.config["epoch"], peer, topic,
                intent, result["request_version"], original))
            result["token"] = digest({k: v for k, v in result.items() if k != "token"})
        return result

    def validate_decision(self, actor_id, meta):
        """Validate against this actor's state before selection/start."""
        identity = decision_identity(meta, actor_id)
        if (identity is not None) != (self.config["version"] == DECISION_VERSION):
            raise ValueError("Communication decision schema/configuration mismatch")
        if identity is None:
            return
        if meta.get("config_sha256") != self.config_hash or identity.epoch != self.config["epoch"]:
            raise ValueError("Communication decision configuration mismatch")
        fields = ("actor_id", "to", "topic", "intent", "issue_id", "content_version", "epoch")
        if meta.get("key") != digest({k: meta.get(k) for k in fields}):
            raise ValueError("Communication decision key mismatch")
        previous = self.sent.get(meta["key"])
        if previous is not None:
            validate_completion(previous, actor_id)
        original = previous.get("request_id") if previous and identity.intent == "request" else None
        if original == "unknown":
            raise ValueError("Original request has not been observed")
        if identity.original_request_id != original:
            raise ValueError("Communication original request changed")

    def record(self, actor_id, action):
        self.bind(actor_id)
        basis = action.meta.get("decision_basis", {})
        meta = basis.get("selected", {}).get("communication_repeat")
        if not meta or not meta["allowed"] or meta["actor_id"] != actor_id:
            return
        if meta["config_sha256"] != self.config_hash or meta["epoch"] != self.config["epoch"]:
            raise ValueError("Selected communication configuration mismatch")
        expected = [meta["to"], meta["topic"], meta["intent"], meta["channel"]]
        if action.verb != "dialogue" or list(action.args) != expected:
            raise ValueError("Selected communication action mismatch")
        self.validate_decision(actor_id, meta)
        if self.config["version"] == VERSION and meta.get("token") != digest([meta["key"], meta["decision_t"], meta["channel"]]):
            raise ValueError("Selected communication token mismatch")
        # Copy the published payload verbatim; an ordinary producer can retain
        # this same payload in its personal completion observation.
        pending = deepcopy(meta)
        self.pending[pending["token"]] = pending

    def absorb(self, actor_id, context, tick):
        self.bind(actor_id)
        events = context.get("perceptions", [])
        # Keep directional IDs/version if supplied; never relate by topic alone.
        for kind, rows in (("received_request", context.get("own_requests", [])),
                           ("sent_response", context.get("own_responses", []))):
            for row in rows:
                observation = {"observation_kind": kind, **deepcopy(row)}
                self.observed[digest(observation)] = observation
        for event in events:
            if event.get("kind") == "speech":
                observation = {**deepcopy(event), "classification": "unknown",
                               "issue_id": "unknown", "reply_expected": "unknown"}
                self.observed[digest(observation)] = observation
        epoch_matches = context.get("communication_epoch", self.config["epoch"]) == self.config["epoch"]
        for token, pending in list(self.pending.items()):
            expected = [pending["to"], pending["topic"], pending["intent"], pending["channel"]]
            start = pending["decision_t"]
            completed = False
            if epoch_matches and self.config["completion_mode"] == "native_one_slot":
                choice = any(e.get("kind") == "own_choice" and e.get("verb") == "dialogue"
                    and e.get("args") == expected and e.get("t") == start
                    and e.get("duration_slots") == 1 for e in events)
                speech = any(e.get("kind") == "speech" and e.get("subject") == actor_id
                    and [e.get(k) for k in ("to", "topic", "intent", "channel")] == expected
                    and e.get("t") == start + 1 for e in events)
                completed = tick == start + 1 and choice and speech
            elif epoch_matches:
                completed = any(e.get("kind") == "own_communication_completed"
                    and e.get("result") == "completed" and e.get("subject") == actor_id
                    and e.get("communication_repeat") == pending
                    and e.get("t") == tick and tick > start for e in events)
                terminal_failure = any(e.get("kind") == "own_communication_completed"
                    and e.get("result") in ("failed", "interrupted") and e.get("subject") == actor_id
                    and e.get("communication_repeat") == pending
                    and e.get("t") == tick and tick >= start for e in events)
                if terminal_failure:
                    del self.pending[token]
                    continue
            if completed:
                previous = self.sent.get(pending["key"], {})
                consumed = sorted(set(previous.get("consumed_evidence_refs", [])) | set(pending["evidence_refs"]))
                canonical_id=next((e.get('canonical_request_id') for e in events
                    if e.get('kind')=='own_communication_completed' and e.get('subject')==actor_id
                    and e.get('result')=='completed' and e.get('communication_repeat')==pending
                    and e.get('t')==tick and isinstance(e.get('canonical_request_id'),str)),None)
                if self.config["version"] == DECISION_VERSION and pending["intent"] == "request":
                    original = pending["communication_identity"]["original_request_id"]
                    if original is not None and canonical_id != original:
                        raise ValueError("Completion changed original request identity")
                self.sent[pending["key"]] = {**deepcopy(pending), "completed_t": tick,
                    "consumed_evidence_refs": consumed,
                    "request_id": canonical_id or (f"{start}:{actor_id}:{pending['to']}:{pending['topic']}"
                        if self.config["version"] == VERSION and pending["intent"] == "request" and self.config["completion_mode"] == "native_one_slot"
                        else "unknown")}
                if self.config["version"] == DECISION_VERSION:
                    receipt = next((deepcopy(e) for e in events
                        if e.get("kind") == "own_communication_completed"
                        and e.get("subject") == actor_id and e.get("result") == "completed"
                        and e.get("communication_repeat") == pending and e.get("t") == tick), None)
                    entry = self.sent[pending["key"]]
                    entry["completion_receipt"] = receipt
                    entry["completion_sha256"] = completion_digest(entry)
                    try:
                        validate_completion(entry, actor_id)
                    except ValueError:
                        if previous:
                            self.sent[pending["key"]] = previous
                        else:
                            del self.sent[pending["key"]]
                        raise
                del self.pending[token]
            elif self.config["completion_mode"] == "native_one_slot" and tick >= start + 1:
                # A selection whose completion was absent/mismatched is not sent.
                del self.pending[token]

    def view(self):
        return {"version": self.config["version"], "epoch": self.config["epoch"],
                "config_sha256": self.config_hash,
                "completed_sent": [deepcopy(self.sent[k]) for k in sorted(self.sent)],
                "pending_selected": [deepcopy(self.pending[k]) for k in sorted(self.pending)],
                "directional_observations": [deepcopy(self.observed[k]) for k in sorted(self.observed)]}

    def export_state(self):
        return {"version": self.config["version"], "config_sha256": self.config_hash, "owner": self.owner,
                "sent": deepcopy(self.sent), "pending": deepcopy(self.pending),
                "observed": deepcopy(self.observed)}

    def restore_state(self, state):
        if (state.get("version") != self.config["version"] or state.get("config_sha256") != self.config_hash
                or set(state) != {"version", "config_sha256", "owner", "sent", "pending", "observed"}):
            raise ValueError("Communication state/configuration mismatch")
        if self.owner is not None and self.owner != state["owner"]:
            raise ValueError("Communication restore actor mismatch")
        if any(not isinstance(state[k], dict) for k in ("sent", "pending", "observed")):
            raise ValueError("Invalid communication state")
        for bucket in ("sent", "pending"):
            for key, entry in state[bucket].items():
                if (entry.get("actor_id") != state["owner"] or entry.get("epoch") != self.config["epoch"]
                        or entry.get("config_sha256") != self.config_hash
                        or entry.get("intent") not in ("consult", "request")
                        or not integer(entry.get("decision_t"))):
                    raise ValueError("Invalid communication state identity")
                identity = {k: entry.get(k) for k in ("actor_id", "to", "topic", "intent", "issue_id",
                                                    "content_version", "epoch")}
                if entry.get("key") != digest(identity):
                    raise ValueError("Invalid communication state content")
                if self.config["version"] == DECISION_VERSION:
                    # Completed bookkeeping is outside the immutable decision.
                    payload = {k: v for k, v in entry.items()
                               if k not in COMPLETION_FIELDS}
                    parsed = decision_identity(payload, state["owner"])
                    if parsed is None:
                        raise ValueError("Missing communication decision identity")
                    if bucket == "sent":
                        validate_completion(entry, state["owner"])
                    elif any(k in entry for k in COMPLETION_FIELDS):
                        raise ValueError("Pending communication cannot contain completion fields")
                    token = payload["token"]
                else:
                    if "communication_identity" in entry:
                        raise ValueError("Decision identity requires v2 configuration")
                    token = digest([entry["key"], entry["decision_t"], entry["channel"]])
                expected_key = entry["key"] if bucket == "sent" else token
                if key != expected_key or (bucket == "sent" and
                        (not integer(entry.get("completed_t")) or entry["completed_t"] <= entry["decision_t"])):
                    raise ValueError("Invalid communication state completion")
                if bucket == "sent":
                    consumed = entry.get("consumed_evidence_refs")
                    if (not isinstance(consumed, list) or any(not isinstance(ref, str) or not ref for ref in consumed)
                            or consumed != sorted(set(consumed))
                            or not set(entry["evidence_refs"]).issubset(consumed)):
                        raise ValueError("Invalid communication evidence consumption state")
        if self.config["version"] == DECISION_VERSION:
            staged = deepcopy(self)
            staged.sent = deepcopy(state["sent"])
            for pending in state["pending"].values():
                staged.validate_decision(state["owner"], pending)
        self.owner, self.sent, self.pending, self.observed = (deepcopy(state[k]) for k in
                                                          ("owner", "sent", "pending", "observed"))
