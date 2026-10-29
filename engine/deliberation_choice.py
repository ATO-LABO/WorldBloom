"""Situated, auditable proposer choices over finite authored alternatives.

Only the proposer's own profile/knowledge/traits, explicit trust, public rules,
and reviews they actually heard are inputs. Predictions address stated blockers;
they never consult a reviewer's private profile or promise future assent.
"""
from copy import deepcopy
import math


def number(value, low, high):
    return (type(value) in (int, float) and low <= value <= high
            and math.isfinite(value))


def validate_policy(spec):
    policy = spec.get("choice_policy")
    if policy is None:
        if "choice_policy" in spec:
            raise ValueError("choice_policy must be an explicit mapping")
        return
    keys = {"version", "trust",
            "evidence_threshold", "alternative_requires", "amendment_deadline",
            "revision_steps"}
    if isinstance(policy, dict) and policy.get('version') == 1:
        keys |= {'goal_priority', 'compromise_limit'}
    if not isinstance(policy, dict) or set(policy) != keys:
        raise ValueError("Invalid proposer choice_policy fields")
    if type(policy["version"]) is not int or policy["version"] not in {1,2}:
        raise ValueError("Unsupported proposer choice_policy version")
    if policy['version'] == 2 and 'concern_projections' not in spec:
        raise ValueError('Choice policy v2 requires concern projections')
    for key, low, high in ([('goal_priority',0,10)] if policy['version']==1 else []) + [("evidence_threshold", 0, 1)]:
        if not number(policy[key], low, high):
            raise ValueError("Invalid proposer choice_policy " + key)
    for key, low, high in ([('compromise_limit',0,2000)] if policy['version']==1 else []) + [("revision_steps", 1, 100)]:
        if type(policy[key]) is not int or not low <= policy[key] <= high:
            raise ValueError("Invalid proposer choice_policy " + key)
    deadline = policy["amendment_deadline"]
    if deadline is not None and (type(deadline) is not int or not 1 <= deadline <= 100000):
        raise ValueError("Invalid proposer amendment_deadline")
    trust = policy["trust"]
    if (not isinstance(trust, dict) or not set(trust) <= set(spec["reviewers"])
            or any(not number(v, 0, 1) for v in trust.values())):
        raise ValueError("Invalid proposer trust references or values")
    requires = policy["alternative_requires"]
    alternatives = {a["id"] for a in spec["alternatives"]}
    if not isinstance(requires, dict) or not set(requires) <= alternatives:
        raise ValueError("Unknown choice_policy alternative")
    for facts in requires.values():
        if (not isinstance(facts, list) or len(facts) > 20
                or any(not isinstance(f, str) or not f.strip() or len(f) > 120 for f in facts)
                or len(facts) != len(set(facts))):
            raise ValueError("Invalid choice_policy prerequisite knowledge")


def bind_policy(spec, world):
    policy = spec.get("choice_policy")
    if policy is None:
        return
    if any(not set(facts) <= set(world.facts) for facts in policy["alternative_requires"].values()):
        raise ValueError("Unknown choice_policy knowledge reference")
    person = world.subjects[spec["proposer"]]
    for key in ("stubbornness", "diligence"):
        if not number(person.traits[key], 0, 1):
            raise ValueError("Situated proposer traits must be finite within [0, 1]")


def evidence_sources(world, spec, record, actor):
    """A public cited prerequisite can be credited, never private knowledge."""
    sources={fact:{'fact':fact,'source':'firsthand','actor':actor,'confidence':1.0}
             for fact in world.subjects[actor].knowledge}
    policy=spec.get('choice_policy',{})
    trust = policy.get('trust',{}) if actor==spec['proposer'] else spec['profiles'][actor].get('evidence_trust',{})
    for _, review in sorted(record['reviews'].items()):
        if actor not in review['audience'] or review['revision']!=record['revision'] or review['terms_hash']!=record['terms_hash']:
            continue
        confidence=trust.get(review['actor'],.5)
        if confidence<=0 or confidence<policy.get('evidence_threshold',.6):continue
        for fact in review['known_evidence']:
            if fact not in sources:
                sources[fact]={'fact':fact,'source':'heard_testimony','actor':review['actor'],'confidence':confidence}
    return sources


def choice_basis(runtime, world, spec, record, actor, turn):
    """Return pre-policy weights and their exact inputs; do not sample or mutate."""
    policy = spec["choice_policy"]
    person = world.subjects[actor]
    traits = {key: person.traits[key] for key in ("stubbornness", "diligence")}
    if any(not number(v, 0, 1) for v in traits.values()):
        raise ValueError("Situated proposer traits must be finite within [0, 1]")
    heard = [deepcopy(r) for _, r in sorted(record["reviews"].items())
             if actor in r["audience"] and r["revision"] == record["revision"]
             and r["terms_hash"] == record["terms_hash"]]
    firsthand = set(person.knowledge)
    observed = []
    for review in heard:
        speaker = review["actor"]
        cited = review["known_evidence"]
        independently_known = bool(cited) and set(cited) <= firsthand
        trust = policy["trust"].get(speaker, 0.5)
        credibility = 1.0 if independently_known else trust
        credited = credibility if credibility >= policy["evidence_threshold"] else 0.0
        observed.append({"speaker": speaker, "review": review, "trust": trust,
                         "firsthand_corroboration": independently_known,
                         "credited_confidence": credited})
    mandatory = set(spec["required_support"]) | {spec["authority"]}
    total_importance = sum(2 if who in mandatory else 1 for who in spec["reviewers"])
    current = runtime._assessment(world, spec, actor, record["versions"][-1]["terms"])
    options = [{"verb": "request_decision", "args": [spec["id"], str(record["revision"])],
                "available": "request_decision" in person.verbs, "exclusions": [],
                "utility_relative_to_request": 0.0, "weight": 4.0}]
    if not options[0]["available"]:
        options[0]["exclusions"].append("verb_unavailable")
    used = {v["alternative"] for v in record["versions"]}
    objections = [r for r in observed if r["review"]["stance"] in {"oppose", "undecided"}]
    for alternative in spec["alternatives"]:
        if alternative["id"] in used:
            continue
        exclusions, evidence, addressed = [], [], []
        own = runtime._assessment(world, spec, actor, alternative["terms"])
        loss = max(0, current["score"] - own["score"])
        if "amend_proposal" not in person.verbs:
            exclusions.append("verb_unavailable")
        if not objections:
            exclusions.append("no_heard_objection")
        if own["stance"] != "support":
            exclusions.append("own_conditions_not_met")
        if policy['version']==1 and loss > policy["compromise_limit"]:
            exclusions.append("compromise_limit_exceeded")
        deadline = policy["amendment_deadline"]
        if deadline is not None and turn + policy["revision_steps"] > deadline:
            exclusions.append("insufficient_revision_time")
        for fact in sorted(policy["alternative_requires"].get(alternative["id"], [])):
            if fact in firsthand:
                evidence.append({"fact": fact, "source": "firsthand", "actor": actor, "confidence": 1.0})
            else:
                sources = [r for r in observed if fact in r["review"]["known_evidence"] and r["credited_confidence"] > 0]
                if sources:
                    source = max(sources, key=lambda r: (r["credited_confidence"], r["speaker"]))
                    evidence.append({"fact": fact, "source": "heard_testimony", "actor": source["speaker"],
                                     "confidence": source["credited_confidence"]})
                else:
                    exclusions.append("missing_evidence:" + fact)
        relief = 0.0
        for observation in objections:
            review = observation["review"]
            blockers = review["blockers"]
            addresses_terms = bool(blockers) and all(alternative["terms"][b["term"]] in b["allowed"] for b in blockers)
            concerns = review.get('concern_objections',[])
            addresses_concerns = False
            if concerns and 'concern_projections' in spec:
                projection=spec['concern_projections'][alternative['id']]
                sources=evidence_sources(world,spec,record,actor)
                justified=set(projection['requires'])<=set(sources)
                addresses_concerns=all(world.concerns.evaluate(world,c['condition'],actor=actor,
                    projection=projection,justified=justified)['state']=='true' for c in concerns)
            if (review["stance"] == "oppose" and (addresses_terms or addresses_concerns)):
                importance = 2 if observation["speaker"] in mandatory else 1
                contribution = importance * observation["credited_confidence"] / total_importance
                relief += contribution
                addressed.append({"speaker": observation["speaker"], "contribution": contribution})
        gain = None
        if policy['version']==2:
            current_values=current['concern_assessment']
            gain=world.concerns.gain(current_values,own['concern_assessment'])
            benefit=8*gain['normalized']*(0.5+traits['diligence'])+2*relief*traits['diligence']
        else:
            benefit = 4 * policy["goal_priority"] * relief * (0.5 + traits["diligence"])
        resistance = 8 * traits["stubbornness"]
        compromise_cost = loss * (0.5 + traits["stubbornness"]) if policy['version']==1 else 0.0
        effort_cost = 0.25 * policy["revision_steps"]
        utility = benefit - resistance - compromise_cost - effort_cost
        weight = 4.0 * math.exp(max(-12.0, min(12.0, utility)))
        options.append({"verb": "amend_proposal", "args": [spec["id"], alternative["id"], str(record["revision"])],
                        "available": not exclusions, "exclusions": exclusions,
                        "evidence": evidence, "own_assessment": own, "own_score_loss": loss,
                        "addressed_stated_objections": addressed,
                        "factors": {"anticipated_objection_relief": benefit, "resistance_to_revision": resistance,
                                    "compromise_cost": compromise_cost, "revision_effort": effort_cost},
                        "utility_relative_to_request": utility, "weight": weight})
        if gain is not None:
            options[-1]['priority_gain']=gain
    basis = {"mechanism": "situated_proposer_v1", "actor": actor, "turn": turn,
            "revision": record["revision"], "terms_hash": record["terms_hash"],
            "traits": traits, "policy": deepcopy(policy), "firsthand_knowledge": sorted(firsthand),
            "heard_reviews": observed, "options": options,
            "weight_scope": "before_other_action_candidates_and_existing_policy",
            "prediction_scope": "addresses_stated_objections_only_not_guaranteed_assent"}
    if policy['version']==2:
        basis['mechanism']='protected_conditions_v1'
        basis['current_concerns']=current['concern_assessment']
    return basis
