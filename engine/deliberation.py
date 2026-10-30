"""Opt-in, finite proposal deliberation on the existing World/Subject state.

Affection is not assent. Profiles, alternatives and procedural authority are
authored inputs; this module neither infers politics nor calls a language model.
All actions are offered and checked again at execution against the current
proposal revision. No new behaviour is enabled for worlds without this block.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from engine.deliberation_choice import bind_policy, choice_basis, validate_policy, number


VERBS = frozenset({"submit_proposal", "present_proposal", "review_proposal",
                   "amend_proposal", "request_decision", "decide_proposal"})
STATUSES = ("approved", "rejected", "deferred")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def text(value, label, limit=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError("Invalid deliberation " + label)
    return value


def shape(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys.split()):
        raise ValueError("Invalid deliberation fields: " + label)


def names(value, label, maximum=20):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError("Invalid deliberation list: " + label)
    for item in value:
        text(item, label, 120)
    if len(value) != len(set(value)):
        raise ValueError("Duplicate deliberation reference: " + label)
    return value


def integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("Invalid deliberation integer: " + label)


class Deliberation:
    def __init__(self, raw):
        shape(raw, "version state proposals", "root")
        if type(raw["version"]) is not int or raw["version"] != 1:
            raise ValueError("Unsupported deliberation version")
        if not isinstance(raw["state"], dict) or not 1 <= len(raw["state"]) <= 40:
            raise ValueError("Bounded explicit deliberation state required")
        for key, value in raw["state"].items():
            text(key, "state key", 120)
            text(value, "state value", 256)
        if not isinstance(raw["proposals"], list) or not 1 <= len(raw["proposals"]) <= 8:
            raise ValueError("Bounded proposal list required")
        self.config = deepcopy(raw)
        self.specs = {}
        for spec in self.config["proposals"]:
            base_spec = {k: v for k, v in spec.items() if k not in {"choice_policy", "concern_projections"}} if isinstance(spec, dict) else spec
            shape(base_spec, "id title place proposer authority reviewers required_support quorum alternatives initial profiles outcomes decision_lines requires_state request_line", "proposal")
            pid = text(spec["id"], "proposal id", 120)
            if pid in self.specs:
                raise ValueError("Duplicate proposal id")
            for key in ("title", "place", "proposer", "authority"):
                text(spec[key], key)
            text(spec["request_line"], "request statement")
            reviewers = names(spec["reviewers"], "reviewers")
            required = names(spec["required_support"], "required support")
            if not reviewers or spec["authority"] not in reviewers or not set(required) <= set(reviewers):
                raise ValueError("Authority and required supporters must be reviewers")
            integer(spec["quorum"], 1, len(reviewers), "quorum")
            alternatives = spec["alternatives"]
            if not isinstance(alternatives, list) or not 1 <= len(alternatives) <= 12:
                raise ValueError("Bounded alternatives required")
            choices, term_keys, term_values = {}, None, {}
            for alt in alternatives:
                shape(alt, "id label terms statement", "alternative")
                aid = text(alt["id"], "alternative id", 120)
                if aid in choices:
                    raise ValueError("Duplicate alternative id")
                text(alt["label"], "alternative label")
                text(alt["statement"], "proposal statement")
                terms = alt["terms"]
                if not isinstance(terms, dict) or not 1 <= len(terms) <= 20:
                    raise ValueError("Bounded explicit terms required")
                if term_keys is not None and set(terms) != term_keys:
                    raise ValueError("Every alternative must specify the same terms")
                term_keys = set(terms)
                for key, value in terms.items():
                    text(key, "term", 120)
                    text(value, "term value", 256)
                    term_values.setdefault(key, set()).add(value)
                if any(other["terms"] == terms for other in choices.values()):
                    raise ValueError("Duplicate alternative terms")
                choices[aid] = alt
            if not isinstance(spec["initial"], str) or spec["initial"] not in choices:
                raise ValueError("Unknown initial alternative")
            profiles = spec["profiles"]
            if not isinstance(profiles, dict) or set(profiles) != set(reviewers) | {spec["proposer"]}:
                raise ValueError("Explicit profiles required for proposer and reviewers")
            for actor, profile in profiles.items():
                text(actor, "profile actor", 120)
                base_profile = {k:v for k,v in profile.items() if k!='evidence_trust'} if isinstance(profile,dict) else profile
                shape(base_profile, "weights minimum constraints requires support_line oppose_line unknown_line", "profile")
                if 'evidence_trust' in profile:
                    trust = profile['evidence_trust']
                    if (actor == spec['proposer'] or not isinstance(trust,dict)
                            or not set(trust)<=set(reviewers) or any(not number(v,0,1) for v in trust.values())):
                        raise ValueError('Invalid reviewer evidence trust')
                integer(profile["minimum"], -10000, 10000, "minimum score")
                names(profile["requires"], "required knowledge")
                for key in ("support_line", "oppose_line", "unknown_line"):
                    text(profile[key], key)
                for field in ("weights", "constraints"):
                    if not isinstance(profile[field], dict) or not set(profile[field]) <= term_keys:
                        raise ValueError("Unknown profile term")
                for key, weights in profile["weights"].items():
                    if not isinstance(weights, dict) or not weights or not set(weights) <= term_values[key]:
                        raise ValueError("Unknown weighted term value")
                    for weight in weights.values():
                        integer(weight, -100, 100, "term weight")
                for key, values in profile["constraints"].items():
                    names(values, "constraint values")
                    if not values or not set(values) <= term_values[key]:
                        raise ValueError("Unknown constrained term value")
            if not isinstance(spec["outcomes"], dict) or set(spec["outcomes"]) != set(STATUSES):
                raise ValueError("Explicit decision effects required")
            for effects in [spec["requires_state"], *spec["outcomes"].values()]:
                if not isinstance(effects, dict) or not set(effects) <= set(raw["state"]):
                    raise ValueError("Unknown deliberation state reference")
                for value in effects.values():
                    text(value, "state effect", 256)
            if not isinstance(spec["decision_lines"], dict) or set(spec["decision_lines"]) != set(STATUSES):
                raise ValueError("Explicit decision statements required")
            for line in spec["decision_lines"].values():
                text(line, "decision statement")
            validate_policy(spec)
            self.specs[pid] = spec
        self.reset()

    def reset(self):
        self.state = deepcopy(self.config["state"])
        self.records = {pid: {"revision": 0, "status": "draft", "alternative": None,
                              "terms_hash": None, "known_by": {}, "reviews": {},
                              "versions": [], "review_history": [], "decision": None}
                        for pid in self.specs}

    def bind(self, world):
        for spec in self.specs.values():
            if spec["place"] not in world.zones or not set(spec["profiles"]) <= set(world.subjects):
                raise ValueError("Unknown deliberation person or place")
            for profile in spec["profiles"].values():
                if not set(profile["requires"]) <= set(world.facts):
                    raise ValueError("Unknown deliberation knowledge reference")
            bind_policy(spec, world)
            if "concern_projections" in spec:
                if world.concerns is None:
                    raise ValueError("Concern projections require registered conditions")
                world.concerns.validate_projections(world, spec)
                if not all(world.concerns.has_owner(world, actor) for actor in spec['profiles']):
                    raise ValueError("Concern projections require each participant's commitments")
        if world.slot_execution is not None:
            raise ValueError("Deliberation v1 supports logical-slot execution only")
        self.reset()

    def snapshot(self):
        return deepcopy({"version": 1, "state": self.state, "proposals": self.records})

    def decided(self, pid, status):
        return status in STATUSES and pid in self.records and self.records[pid]["status"] == status

    def state_is(self, key, value):
        return self.state.get(key) == value

    def _audience(self, world, place):
        return sorted(p.id for p in world.present_subjects(place) if p.vitality in {"alive", "revived"})

    def _assessment(self, world, spec, actor, terms):
        profile = spec["profiles"][actor]
        missing = sorted(set(profile["requires"]) - world.subjects[actor].knowledge)
        score = sum(weights.get(terms[key], 0) for key, weights in profile["weights"].items())
        blockers = [{"term": key, "actual": terms[key], "allowed": values}
                    for key, values in sorted(profile["constraints"].items()) if terms[key] not in values]
        stance = "undecided" if missing else "support" if not blockers and score >= profile["minimum"] else "oppose"
        line = profile[{"undecided": "unknown_line", "support": "support_line", "oppose": "oppose_line"}[stance]]
        result = {"stance": stance, "score": score, "minimum": profile["minimum"],
                "blockers": blockers, "missing_knowledge": missing,
                "known_evidence": sorted(set(profile["requires"]) & world.subjects[actor].knowledge),
                "statement": line}
        if 'concern_projections' in spec:
            alternative = next(a for a in spec['alternatives'] if a['terms'] == terms)
            projection = spec['concern_projections'][alternative['id']]
            from engine.deliberation_choice import evidence_sources
            sources = evidence_sources(world, spec, self.records[spec['id']], actor)
            justified = set(projection['requires']) <= set(sources)
            valuation = world.concerns.assessment(world, actor, projection=projection, justified=justified)
            if stance != 'oppose' or not blockers:
                if missing or valuation['stance'] == 'undecided': stance = 'undecided'
                elif stance == 'oppose' or valuation['stance'] == 'oppose': stance = 'oppose'
                else: stance = 'support'
            result.update(stance=stance, concern_assessment=valuation,
                          projection_evidence=[sources[f] for f in projection['requires'] if f in sources],
                          missing_projection_evidence=sorted(set(projection['requires'])-set(sources)),
                          concern_objections=[{'condition':e['condition'],'state':e['state']}
                                              for e in valuation['entries'] if e['state'] != 'true'],
                          statement=profile[{'undecided':'unknown_line','support':'support_line','oppose':'oppose_line'}[stance]])
        return result

    def _fresh_reviews(self, world, spec, record):
        if set(record["reviews"]) != set(spec["reviewers"]):
            return False
        for actor, review in record["reviews"].items():
            if world.subjects[actor].vitality not in {"alive", "revived"}:
                return False
            current = self._assessment(world, spec, actor, record["versions"][-1]["terms"])
            if any(review[key] != value for key, value in current.items()):
                return False
        return True

    def candidates(self, world, actor, *, turn=0):
        """Return finite, current-revision choices, not a prewritten outcome."""
        person = world.subjects[actor]
        if person.vitality not in {"alive", "revived"}:
            return []
        result = []
        def offer(verb, args, weight):
            if verb in person.verbs:
                result.append((verb, tuple(args), weight))
        for pid, spec in sorted(self.specs.items()):
            record = self.records[pid]
            if person.zone != spec["place"] or actor not in spec["profiles"] or record["status"] in STATUSES:
                continue
            revision = str(record["revision"])
            audience = self._audience(world, spec["place"])
            if record["revision"] == 0:
                if actor == spec["proposer"] and all(self.state.get(k) == v for k, v in spec["requires_state"].items()):
                    offer("submit_proposal", [pid, spec["initial"], revision], 4.0)
                continue
            if actor == spec["proposer"] and record["status"] == "open":
                for reviewer in spec["reviewers"]:
                    if reviewer in audience and record["known_by"].get(reviewer) != record["revision"]:
                        offer("present_proposal", [pid, reviewer, revision], 4.0)
                # A revision responds to heard objections, not private profiles.
                heard = [r for r in record["reviews"].values() if actor in r["audience"]]
                reviewed = self._fresh_reviews(world, spec, record) and len(heard) == len(spec["reviewers"])
                if reviewed and "choice_policy" in spec:
                    basis = choice_basis(self, world, spec, record, actor, turn)
                    for option in basis["options"]:
                        if option["available"]:
                            offer(option["verb"], option["args"], option["weight"])
                elif reviewed:
                    offer("request_decision", [pid, revision], 4.0)
                if reviewed and "choice_policy" not in spec and any(r["stance"] in {"oppose", "undecided"} for r in heard):
                    used = {v["alternative"] for v in record["versions"]}
                    for alternative in spec["alternatives"]:
                        if alternative["id"] not in used:
                            own = self._assessment(world, spec, actor, alternative["terms"])
                            if own["stance"] == "support":
                                offer("amend_proposal", [pid, alternative["id"], revision], 2.0 + max(0, own["score"]) / 10)
            if actor in spec["reviewers"] and record["known_by"].get(actor) == record["revision"] and spec["authority"] in audience:
                version = record["versions"][-1]
                assessment = self._assessment(world, spec, actor, version["terms"])
                previous = record["reviews"].get(actor)
                if previous is None or any(previous[k] != assessment[k] for k in assessment) or not set(audience) <= set(previous["audience"]):
                    offer("review_proposal", [pid, revision], 6.0)
            if actor == spec["authority"] and record["status"] == "submitted":
                if self._fresh_reviews(world, spec, record):
                    offer("decide_proposal", [pid, revision], 4.0)
        return sorted(result, key=lambda row: (row[0], row[1]))

    def execute(self, world, actor, verb, args, *, turn):
        if not any(v == verb and offered == tuple(args) for v, offered, _ in self.candidates(world, actor, turn=turn)):
            return "invalid", {"reason": "deliberation_action_not_available"}, []
        pid = args[0]
        spec, record = self.specs[pid], self.records[pid]
        before = self.snapshot()
        audience = self._audience(world, spec["place"])
        event = {"proposal": pid, "actor": actor, "turn": turn, "verb": verb, "audience": audience}
        if verb in {"amend_proposal", "request_decision"} and "choice_policy" in spec:
            basis = choice_basis(self, world, spec, record, actor, turn)
            basis["chosen"] = {"verb": verb, "args": list(args)}
            event["choice_basis"] = basis
        if verb in {"submit_proposal", "amend_proposal"}:
            alternative = next(a for a in spec["alternatives"] if a["id"] == args[1])
            record["revision"] += 1
            version = {"revision": record["revision"], "alternative": alternative["id"],
                       "terms": deepcopy(alternative["terms"]), "label": alternative["label"],
                       "terms_hash": digest({"proposal": pid, "revision": record["revision"], "terms": alternative["terms"]}),
                       "actor": actor, "turn": turn, "statement": alternative["statement"]}
            record.update(status="open", alternative=alternative["id"], terms_hash=version["terms_hash"], reviews={})
            record["versions"].append(version)
            for who in audience:
                if who in spec["profiles"]:
                    record["known_by"][who] = record["revision"]
            event.update(deepcopy(version))
        elif verb == "present_proposal":
            record["known_by"][args[1]] = record["revision"]
            event.update(recipient=args[1], statement=record["versions"][-1]["statement"])
        elif verb == "review_proposal":
            review = {**self._assessment(world, spec, actor, record["versions"][-1]["terms"]),
                      "revision": record["revision"], "terms_hash": record["terms_hash"],
                      "actor": actor, "turn": turn, "audience": audience}
            record["reviews"][actor] = review
            record["review_history"].append(deepcopy(review))
            event.update(deepcopy(review))
        elif verb == "request_decision":
            record["status"] = "submitted"
            event.update(statement=spec["request_line"])
        else:
            reviews = record["reviews"]
            supporters = sorted(who for who, review in reviews.items() if review["stance"] == "support")
            unresolved = sorted(who for who, review in reviews.items() if review["stance"] == "undecided")
            required = sorted(set(spec["required_support"]) | {spec["authority"]})
            missing_support = sorted(set(required) - set(supporters))
            status = "deferred" if unresolved else "approved" if not missing_support and len(supporters) >= spec["quorum"] else "rejected"
            decision = {"status": status, "revision": record["revision"], "terms_hash": record["terms_hash"],
                        "actor": actor, "turn": turn, "supporters": supporters, "unresolved": unresolved,
                        "missing_support": missing_support, "quorum": spec["quorum"],
                        "review_evidence": deepcopy(reviews), "state_effects": deepcopy(spec["outcomes"][status]),
                        "statement": spec["decision_lines"][status]}
            record.update(status=status, decision=decision)
            self.state.update(spec["outcomes"][status])
            event.update(deepcopy(decision))
        event.update(revision=record["revision"], terms_hash=record["terms_hash"])
        if 'concern_projections' in spec:
            # Author evidence only; never fed into the actor's choice basis.
            event['world_concerns'] = world.concerns.world_report(world)
        return "applied", {"deliberation_event": event,
                           "deliberation": {"before": before, "after": self.snapshot()}}, []
