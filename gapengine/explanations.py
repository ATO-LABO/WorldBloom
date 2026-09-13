"""Deterministic, evidence-bound explanations; independent of the viewer and prose."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path

VERSION = 1
CONFIRMED = "confirmed"
ABSENT = "absent"
UNKNOWN = "unknown"
NO_DIRECT_COST = {"observe", "investigate", "rethink", "share_knowledge", "pledge", "persuade", "mislead"}
COST_RULES = NO_DIRECT_COST | {"move", "neutralize", "confront", "give_item", "grand_gesture"}


def field(status, text, **extra):
    return {"status": status, "text": text, **extra}


def is_turning_candidate(row, protagonist):
    if row.get("subject") != protagonist:
        return False
    verb = row.get("verb")
    details = row.get("details") or {}
    if verb == "rethink":
        return details.get("before") != details.get("after")
    if verb == "learn_fact":
        beliefs = list(details.get("beliefs") or [])
        if isinstance(details.get("belief"), dict):
            beliefs.append(details["belief"])
        return any(b.get("before") != b.get("after") for b in beliefs)
    return verb in {"confront", "exposure", "betrayal", "payoff", "ending", "downed", "revived"}


def merge_after(state, delta):
    """Nested values are AFTER values, not increments. None means deletion."""
    for key, value in delta.items():
        if value is None:
            state.pop(key, None)
        elif isinstance(value, dict):
            if not isinstance(state.get(key), dict):
                state[key] = {}
            merge_after(state[key], value)
        else:
            state[key] = copy.deepcopy(value)


def _leaves(value, prefix=()):
    for key, item in value.items():
        path = prefix + (key,)
        if isinstance(item, dict) and item:
            yield from _leaves(item, path)
        else:
            yield path, item


def _get(state, path):
    for key in path:
        if not isinstance(state, dict) or key not in state:
            return None
        state = state[key]
    return state


def _cost(row, before, source, before_sources):
    actor = row.get("subject")
    verb = row.get("verb")
    delta = row.get("delta") or {}
    costs, changes, unknown = [], [], []
    if not {"actor", "relations", "targets"}.issubset(delta):
        unknown.append("delta_incomplete")
    supported = verb in COST_RULES or (verb == "payoff" and (row.get("details") or {}).get("applied", {}).get("kind") in {"stance", "reputation", "modifier", "neutralize", "enable_verb"})
    for relation in delta.get("relations", []):
        amount = relation.get("affinity", 0)
        if amount < 0 and relation.get("target") == actor and relation.get("observer") != actor:
            costs.append({"kind": "trust", "owner": actor, "from": relation.get("observer"), "amount": amount,
                          "text": f"{relation.get('observer')} → {actor} の好意 {amount:+g}", "source": source})
        elif amount:
            changes.append({"kind": "relation", "value": relation, "source": source})
    baseline = (row.get("explanation") or {}).get("cost_baseline") or {}
    baseline_ok = baseline.get("version") == 1 and baseline.get("timing") == "before_execute"
    for path, after in _leaves(delta.get("actor", {})):
        if path[0] not in {"resources", "stamina"}:
            changes.append({"kind": "state", "path": list(path), "after": after, "source": source})
            continue
        if path == ("resources", "bonds"):
            changes.append({"kind": "aggregate_relations", "after": after, "source": source})
            continue
        # Legacy stamina cannot be recovered across passive, unlogged recovery.
        prior = _get(baseline, path) if baseline_ok else None
        if (baseline_ok and len(path) == 3 and path[:2] == ("resources", "assets")
                and isinstance(_get(baseline, ("resources", "assets")), dict)
                and path[2] not in baseline["resources"]["assets"]):
            prior = 0  # complete recorded inventory: absent means no item, not unknown
        prior_source = source if prior is not None else before_sources.get(path)
        if prior is None and path[0] != "stamina":
            prior = _get(before, path)
        if len(path) == 3 and path[:2] == ("resources", "assets") and after is None:
            item = {"kind": "item", "owner": actor, "path": list(path), "after": 0,
                    "before": prior, "amount": None, "source": source,
                    "text": f"{actor} が所持品 {path[2]} を失った"}
            if isinstance(prior, (int, float)):
                item.update(amount=-prior, before_source=prior_source)
            costs.append(item)
            continue
        if not isinstance(prior, (int, float)) or not isinstance(after, (int, float)):
            unknown.append(".".join(path))
        elif after < prior:
            kind = "item" if path[:2] == ("resources", "assets") else "resource"
            amount = round(after - prior, 6)
            label = {("stamina",): "体力", ("resources", "reputation"): "評判"}.get(path, ".".join(path))
            if kind == "item" and len(path) == 3:
                label = f"所持品 {path[2]}"
            costs.append({"kind": kind, "owner": actor, "path": list(path), "before": prior, "after": after,
                          "before_source": prior_source,
                          "amount": amount, "text": f"{actor} の {label} {amount:+g}", "source": source})
        elif after != prior:
            changes.append({"kind": "resource", "path": list(path), "before": prior, "after": after, "source": source})
    if delta.get("targets"):
        changes.append({"kind": "other_subjects", "after": delta["targets"], "source": source})
    costs.sort(key=lambda c: ({"item": 0, "resource": 1, "trust": 2}[c["kind"]], c["text"]))
    if costs:
        result = field(CONFIRMED, "／".join(c["text"] for c in costs), items=costs)
    elif supported and not unknown:
        result = field(ABSENT, "確認範囲内では即時の代償なし", items=[])
    else:
        result = field(UNKNOWN, "即時の代償は記録不足または判定未対応", items=[])
    result.update(complete=supported and not unknown, unknown_paths=unknown,
                  delayed=field(UNKNOWN, "遅延した代償は未確認"), changes=changes)
    return result


def extract_explanation(path, *, experiment="", cell=""):
    path = Path(path)
    raw = path.read_bytes()
    rows = [json.loads(line) for line in raw.decode("utf-8-sig").splitlines()]
    return explain_rows(rows, layers_path=str(path), sha256=hashlib.sha256(raw).hexdigest(), experiment=experiment, cell=cell)


def explain_rows(rows, *, layers_path="", sha256="", experiment="", cell=""):
    header = next((r for r in rows if r.get("kind") == "header"), {})
    hero = header.get("protagonist", "")
    def source(line):
        r = rows[line - 1]
        return {"experiment": experiment, "cell": cell, "layers_path": layers_path, "sha256": sha256,
                "line": line, "turn": r.get("turn"), "kind": r.get("kind"), "version": VERSION}
    states, state_sources, decisions, decision_by_line = {}, {}, [], {}
    before_states, after_states = {}, {}
    # Updates between decisions are retained so invalidations cannot be skipped.
    transitions = []
    for line, row in enumerate(rows, 1):
        actor = row.get("subject")
        if not actor:
            continue
        state = states.setdefault(actor, {})
        sources = state_sources.setdefault(actor, {})
        details = row.get("details") or {}
        if row.get("kind") == "snapshot":
            remembered = {k: copy.deepcopy(state[k]) for k in ("knowledge",) if k in state}
            states[actor] = {**remembered, **copy.deepcopy(row.get("layers") or {})}
            state_sources[actor] = {p: source(line) for p, _ in _leaves(row.get("layers") or {})}
            for p, src in sources.items():
                if p[0] in remembered and p[0] not in (row.get("layers") or {}):
                    state_sources[actor][p] = src
            transitions.append((line, actor, copy.deepcopy(states[actor])))
            continue
        # rethink before is an explicit complete pre-execution belief map.
        if row.get("kind") == "decision" and row.get("verb") == "rethink" and isinstance(details.get("before"), dict):
            for old in list(sources):
                if old[0] == "valued_beliefs":
                    sources.pop(old)
            state["valued_beliefs"] = copy.deepcopy(details["before"])
            for p, _ in _leaves({"valued_beliefs": state["valued_beliefs"]}):
                sources[p] = source(line)
        before = copy.deepcopy(state)
        before_states[line] = before
        if row.get("kind") == "decision":
            context = row.get("explanation") or {}
            selection = context.get("selection")
            alternatives = field(CONFIRMED, "記録された候補（部分集合の場合あり）", **selection) if isinstance(selection, dict) and selection.get("version") == VERSION else field(UNKNOWN, "代替候補は未記録")
            known = {k: copy.deepcopy(state[k]) for k in ("valued_beliefs", "belief", "knowledge") if k in state}
            known_sources = [v for p, v in sources.items() if p[0] in known]
            if row.get("verb") == "rethink" and isinstance(details.get("evidence"), list):
                known["evidence_used_by_rethink"] = details["evidence"]
                known_sources.append(source(line))
            note = "決定前に記録された知識。心理的な動機は断定しない。" if known else "決定前の知識は記録不足"
            info = {"line": line, "turn": row.get("turn"), "subject": actor, "verb": row.get("verb"),
                    "args": row.get("args", []), "source": source(line),
                    "choice": field(CONFIRMED, f"{actor} が {row.get('verb')} {'・'.join(map(str,row.get('args', [])))}", source=source(line),
                                    zone=context.get("zone", state.get("zone")), present=context.get("present"), alternatives=alternatives),
                    "grounds": field(CONFIRMED if known else UNKNOWN, note, knowledge=known,
                                     sources=list({v["line"]: v for v in known_sources}.values()), complete=False),
                    "system": field(CONFIRMED if row.get("policy") else UNKNOWN, "抽選・重みの記録（本人の知識ではない）" if row.get("policy") else "選択要因は未記録",
                                    policy=row.get("policy"), classification=row.get("classification"), probability=row.get("choice_prob")),
                    "outcome": {"result": row.get("result"), "details": details, "source": source(line)},
                    "cost": _cost(row, before, source(line), sources),
                    "turning": field(CONFIRMED if is_turning_candidate(row, actor) else UNKNOWN,
                                     "転機候補" if is_turning_candidate(row, actor) else "転機は判定未対応または記録不足",
                                     confirmation="candidate" if is_turning_candidate(row, actor) else "unknown", links=[])}
            decisions.append(info)
            decision_by_line[line] = info
        delta = row.get("delta") or {}
        updates = {actor: delta.get("actor") or {}}
        updates.update(delta.get("targets") or {})
        for who, change in updates.items():
            target_state = states.setdefault(who, {})
            merge_after(target_state, change)
            target_sources = state_sources.setdefault(who, {})
            for p, value in _leaves(change):
                for old in list(target_sources):
                    if old[:len(p)] == p:
                        target_sources.pop(old)
                if value is not None:
                    target_sources[p] = source(line)
            if change:
                transitions.append((line, who, copy.deepcopy(target_state)))
        if row.get("verb") == "learn_fact":
            fact = details.get("fact")
            if fact:
                state["knowledge"] = sorted(set(state.get("knowledge", [])) | {fact})
                sources[("knowledge",)] = source(line)
        after_states[line] = copy.deepcopy(states[actor])

    def attach(origin, downstream, key, before, after, reader):
        item = decision_by_line.get(origin)
        if item is None or item["subject"] != downstream["subject"]:
            return
        link = {"state_key": key, "before": before, "after": after, "condition_before": False, "condition_after": True,
                "source": source(origin), "downstream": downstream["source"], "reader": reader}
        item["turning"]["links"].append(link)
        item["turning"].update(status=CONFIRMED, confirmation="confirmed",
            text=f"この変更が T{downstream['turn']} の {downstream['verb']} に必要な条件を成立させた")

    for downstream in decisions:
        actor, line, args = downstream["subject"], downstream["line"], downstream["args"]
        if downstream["verb"] == "confront" and len(args) == 2:
            target, fact = args
            # Target mismatch is a sufficient negative gate without guessing old thresholds.
            active, previous, initialized = None, None, False
            for at, who, state in transitions:
                if at >= line:
                    break
                if who != actor or "valued_beliefs" not in state:
                    continue
                belief = state["valued_beliefs"].get(fact)
                valid = isinstance(belief, dict) and belief.get("value") == target
                if not valid:
                    active = None
                elif initialized and not (isinstance(previous, dict) and previous.get("value") == target):
                    active = (at, previous, belief)
                previous, initialized = copy.deepcopy(belief), True
            # Explicit rethink before also establishes known absence at that decision.
            for candidate in decisions:
                at = candidate["line"]
                if at >= line or candidate["subject"] != actor or candidate["verb"] != "rethink":
                    continue
                before = before_states[at].get("valued_beliefs", {}).get(fact)
                after = after_states[at].get("valued_beliefs", {}).get(fact)
                if isinstance(after, dict) and after.get("value") == target and (not isinstance(before, dict) or before.get("value") != target):
                    later = [(t, s.get("valued_beliefs", {}).get(fact)) for t,w,s in transitions if at < t < line and w == actor and "valued_beliefs" in s]
                    if all(isinstance(b, dict) and b.get("value") == target for _,b in later):
                        active = (at, before, after)
            if active:
                at, before, after = active
                # Confidence drops can invalidate eligibility; require unchanged confidence through use.
                final = before_states[line].get("valued_beliefs", {}).get(fact)
                if final == after and all(s.get("valued_beliefs", {}).get(fact) == after for t,w,s in transitions if at < t < line and w == actor and "valued_beliefs" in s):
                    attach(at, downstream, f"{actor}.valued_beliefs.{fact}.value == {target}", before, after, "engine.actions._confront_candidates: target = present_by_id.get(belief.value)")
        if downstream["verb"] == "payoff":
            effect = downstream["outcome"]["details"].get("effect_id")
            active = None
            for at, who, state in transitions:
                if at >= line:
                    break
                if who != actor or "pending" not in state:
                    continue
                pending = next((p for p in state["pending"] if p.get("id") == effect), None)
                valid = pending and pending.get("mode") == "chosen" and not pending.get("resolved") and pending.get("planted_by") == actor
                if not valid:
                    active = None
                elif active is None and pending.get("planted_turn") == rows[at-1].get("turn") and at in decision_by_line:
                    previous = before_states[at].get("pending")
                    # A new pending is explicitly dated; absence is also checked where available.
                    if previous is not None and not any(p.get("id") == effect and not p.get("resolved") for p in previous):
                        active = (at, pending)
            if active:
                at, pending = active
                attach(at, downstream, f"{actor}.pending.{effect}.unresolved_chosen", None, pending,
                       "engine.phase2.ready_chosen_effects: planter, mode=chosen, unresolved")
    # Search the entire supplied run, including marker events after decisions.
    # Absence describes this choice's candidate events, never all possible causality.
    terminal = bool(rows and rows[-1].get("kind") == "event"
                    and rows[-1].get("verb") in {"ending", "aborted"})
    shape_complete = bool(rows and rows[0].get("kind") == "header" and hero)
    for row in rows[1:]:
        kind = row.get("kind")
        if kind in {"decision", "event"}:
            shape_complete &= {"subject", "turn", "verb", "delta", "details", "result"}.issubset(row)
            shape_complete &= isinstance(row.get("delta"), dict) and isinstance(row.get("details"), dict)
            if kind == "decision":
                shape_complete &= {"actor", "targets", "relations"}.issubset(row.get("delta") or {})
        elif kind != "snapshot":
            shape_complete = False
    for item in decisions:
        row = rows[item["line"] - 1]
        # Same-turn markers are conservatively associated with this choice; no causal claim.
        events = [source(n) for n, r in enumerate(rows, 1)
                  if r.get("turn") == item["turn"] and is_turning_candidate(r, item["subject"])]
        turning = item["turning"]
        turning["search"] = {"first_line": 1, "last_line": len(rows),
                             "terminal_recorded": terminal, "record_structure_complete": bool(shape_complete),
                             "subject": item["subject"], "turn": item["turn"], "candidate_sources": events,
                             "scope": "all run rows; candidate events for this subject and decision turn"}
        if turning["confirmation"] == "confirmed":
            continue
        if events:
            turning.update(status=CONFIRMED, confirmation="candidate", text="転機候補（この場面のイベントを記録）")
            continue
        delta = row.get("delta") or {}
        no_change = not any(delta.values())
        supported = item["verb"] in COST_RULES | {"rest"}
        if item["verb"] == "rethink":
            supported &= all(isinstance((row.get("details") or {}).get(k), dict) for k in ("before", "after"))
        # An unclassified marker may carry an unimplemented influence, even if the
        # decision delta itself is empty. Do not infer absence from missing semantics.
        unclassified_markers = [r for r in rows if r.get("kind") == "event"
                                and r.get("subject") == item["subject"] and r.get("turn") == item["turn"]
                                and not is_turning_candidate(r, item["subject"])]
        if terminal and shape_complete and supported and no_change and not row.get("policy") and not unclassified_markers:
            turning.update(status=ABSENT, confirmation="absent",
                           text="全行を確認。この選択に該当する転機候補イベントなし")
        # A state or policy change may influence later weights: remain unknown.

    eligible = [d for d in decisions if d["subject"] == hero]
    confirmed = [d for d in eligible if d["turning"]["confirmation"] == "confirmed"]
    candidates = [d for d in eligible if d["turning"]["confirmation"] == "candidate"]
    pool = confirmed or candidates or eligible
    def rank(d):
        links = d["turning"]["links"]
        # Unknown ending predicate relevance does not receive a fabricated preference.
        fact = min((l["state_key"] for l in links), default="")
        return (-len(links), -(d["turn"] or 0), fact, d["line"])
    representative = min(pool, key=rank) if pool else None
    signature = [(d["subject"], d["verb"], d["args"], d["outcome"]["result"]) for d in eligible]
    return {"version": VERSION, "source": {"layers_path": layers_path, "sha256": sha256, "experiment": experiment, "cell": cell},
            "representative": representative, "decisions": decisions,
            "trajectory_signature": hashlib.sha256(json.dumps(signature, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
            "limitations": ["重みを介した転機は判定未対応", "遅延した代償は未確認", "未記録の初期状態・同席者は不明", "結末述語の反転は未記録なら不明"]}
