"""Choose evidenced turning points and thematic threads for story composition.

The full event log remains in materials. This layer selects events a story must
explain; it does not turn temporal proximity into a claim of causation.
"""
from collections import defaultdict


THREADS = {
    "goal": "主人公は何を目指し、どの手段で結末へ至るか",
    "relationship": "誰が何をして関係が変わり、それが後の行動にどう現れるか",
    "conflict": "障害と敗北の後に何が変わり、どう決着するか",
    "identity": "誰が何をいつ知り、その後の対立にどう関わるか",
    "choice": "特徴的な選択にはどんな結果が残るか",
}


def _involves(event, *actors):
    details = event["details"]
    values = (event["actor_id"], details.get("target"), details.get("winner"),
              details.get("loser"), details.get("ally"), details.get("by"))
    return any(actor is not None and actor in values for actor in actors)


def pivotal_event_ids(material, selected):
    """Select story turns and their recorded prerequisites, without event quotas."""
    if not selected:
        return []
    hero = material["world"].get("protagonist")
    foe = material["world"].get("antagonist")
    by_id = {e["event_id"]: e for e in material["events"]}
    chosen = set()
    first_move = next((e for e in selected if e["verb"] == "move" and e["actor_id"] == hero), None)
    if first_move:
        chosen.add(first_move["event_id"])
    chosen.update(e["event_id"] for e in selected if e["type"] == "ending" or e["verb"] == "dead")
    # One defeat and the decisive victory are legible; repeated combat stays
    # available as support instead of becoming a sequence of required lines.
    fights = [e for e in selected if e["verb"] == "fight" and _involves(e, hero, foe)]
    defeats = [e for e in fights if e["details"].get("loser") == hero]
    victories = [e for e in fights if e["details"].get("winner") == hero]
    for event in (defeats[:1] + victories[-1:]):
        chosen.add(event["event_id"])
    loot_victories = [e for e in victories if e["details"].get("loot")]
    if loot_victories:
        chosen.add(loot_victories[0]["event_id"])
    # If a final death follows a separate fight, keep that fight too.
    for event in selected:
        if event["verb"] != "dead":
            continue
        previous = [e for e in fights if e["order"] < event["order"]
                    and e["details"].get("loser") == event["actor_id"]]
        if previous:
            chosen.add(previous[-1]["event_id"])
        # A later death after a nonfatal defeat requires the recorded recovery.
        recoveries = [e for e in selected if e["order"] < event["order"]
                      and e["actor_id"] == event["actor_id"] and e["verb"] == "revived"]
        if recoveries:
            chosen.add(recoveries[-1]["event_id"])
            earlier = [e for e in selected if e["order"] < recoveries[-1]["order"]
                       and e["actor_id"] == event["actor_id"] and e["verb"] == "downed"]
            if earlier:
                chosen.add(earlier[-1]["event_id"])
    # A specific identity change or choice deserves space when present.
    for verbs in ({"disguise"}, {"exposure", "identity_revealed"},
                  {"sacrifice", "trial", "sabotage", "mislead", "neutralize", "pledge", "grand_gesture"}):
        matches = [e for e in selected if e["verb"] in verbs and _involves(e, hero, foe)]
        if matches:
            chosen.add(matches[0]["event_id"])
    # A distinctive acquired tool can matter to the causal route. Common
    # consumable refills and repeated construction are supporting details.
    tools = [e for e in selected if e["actor_id"] == hero and e["verb"] in {"buy", "craft"}
             and e["details"].get("item") != "きびだんご"]
    if tools:
        chosen.add(tools[0]["event_id"])
    # Relation payoffs without a recorded cause cannot be a mandatory scene.
    # They stay in immutable materials for review and can be chosen by an editor.
    # Keep a real gift and its ally transition together. Where many allies
    # form, start with the first pair; later pairs remain available as support.
    gift_links = [link for link in material["causal_links"] if link["kind"] == "gift_to_ally"]
    for link in gift_links:
        ally = by_id.get(link["to"])
        if ally and _involves(ally, hero):
            chosen.update((link["from"], link["to"]))
            break
    # Preserve the first loss→recovery and the recorded rescuer when one exists.
    revivals = [e for e in selected if e["verb"] == "revived" and e["actor_id"] == hero]
    first_revival = revivals[:1]
    named_revival = [e for e in revivals if e["details"].get("by")]
    for event in first_revival + named_revival[:1]:
        chosen.add(event["event_id"])
        earlier = [e for e in selected if e["order"] < event["order"]
                   and e["actor_id"] == event["actor_id"] and e["verb"] == "downed"]
        if earlier:
            chosen.add(earlier[-1]["event_id"])
        helper = event["details"].get("by")
        if helper:
            for link in gift_links:
                ally = by_id.get(link["to"])
                if ally and _involves(ally, helper, hero):
                    chosen.update((link["from"], link["to"]))
    # The decisive fight's immediate vital change is a single outcome.
    for event in selected:
        if event["verb"] == "fight" and event["event_id"] in chosen:
            subsequent = [e for e in selected if e["order"] > event["order"]
                          and e["actor_id"] == event["details"].get("loser")
                          and e["verb"] in {"downed", "dead"}]
            if subsequent and subsequent[0]["order"] <= event["order"] + 2:
                chosen.add(subsequent[0]["event_id"])
    # An identity reveal needs its earlier disguise when the log contains it.
    for event in selected:
        if event["event_id"] in chosen and event["verb"] in {"exposure", "identity_revealed"}:
            target = event["details"].get("target")
            earlier = [e for e in selected if e["order"] < event["order"]
                       and e["verb"] == "disguise" and e["actor_id"] == target]
            if earlier:
                chosen.add(earlier[-1]["event_id"])
    if len(chosen) < 3:
        chosen.add(selected[len(selected) // 2]["event_id"])
    return [e["event_id"] for e in selected if e["event_id"] in chosen]


def narrative_threads(material, selected, pivot_ids):
    """Group required events by dramatic function without asserting invented causes."""
    pivot_set = set(pivot_ids)
    grouped = defaultdict(list)
    for event in selected:
        if event["event_id"] not in pivot_set:
            continue
        verb = event["verb"]
        if verb in {"fight", "downed", "revived", "dead", "neutralize", "sabotage", "trial"}:
            kind = "conflict"
        elif verb in {"give_item", "ally_gained", "payoff", "pledge", "grand_gesture"}:
            kind = "relationship"
        elif verb in {"disguise", "exposure", "identity_revealed", "mislead"}:
            kind = "identity"
        elif verb in {"move", "craft", "ending"}:
            kind = "goal"
        else:
            kind = "choice"
        grouped[kind].append(event["event_id"])
    return [{"kind": kind, "question": question, "event_ids": grouped[kind]}
            for kind, question in THREADS.items() if grouped[kind]]
