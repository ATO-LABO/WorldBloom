"""Engine-facing patch contracts and typed usage counters."""
from pathlib import Path
from engine.subject import Subject
from engine.world import World
from gapengine.qd import read_rows
from gapengine.world_demand import _zone_after


def contract_check(world_path, subjects_dir, patch, *, action_graph_path=None):
    def instance():
        world = World.from_yaml(world_path, action_graph_path=action_graph_path)
        people = [Subject.from_yaml(p) for p in sorted(Path(subjects_dir).glob("*.yaml"))]
        world.bind_subjects({p.id: p for p in people})
        return world, people
    violations, negative = [], []
    try:
        world, people = instance()
        for zone in patch.get("add", {}).get("zones", []):
            parent, branch = zone["parent"], zone["name"]
            eligible = sorted((p for p in people if parent in p.range_zones),
                              key=lambda p: (p.id != world.protagonist, p.id))
            if not eligible:
                violations.append(f"新しい場所に入れる人物がいません: {branch}")
                continue
            person = eligible[0]
            person.zone, person.stamina = parent, person.stamina_max
            for rule in person.range_exclude:
                if {parent, branch}.intersection(rule.get("zones", [])) and rule.get("until_item"):
                    person.inventory[rule["until_item"]] = 1
            if branch not in world.reachable_paths(person):
                violations.append(f"親から枝へ入れません: {branch}（試した人物: {person.id}）")

            # N3 (WB-WORLDGROW-001, Astra review): every subject with at
            # least one exclude rule that targets `parent` is a candidate
            # for the negative check, tested one rule at a time. Each rule
            # gets its own fresh subject state (a new instance()) with only
            # *that* rule's own until_item removed -- a different,
            # already-satisfied exclude rule on the same subject must not
            # shrink the pool of candidate starting zones (the previous R6
            # fix still unioned every rule's zones regardless of whether
            # that rule's condition currently held, so a currently-inert
            # rule elsewhere could exclude the only real neighbor and
            # silently skip the check instead of running it). Every subject
            # gets a recorded status, not just the ones with no start (a
            # subject skipped alongside others who *were* checked used to
            # vanish from the output entirely).
            for base_person in people:
                rule_indices = [i for i, r in enumerate(base_person.range_exclude)
                                 if parent in (r.get("zones") or [])]
                if not rule_indices:
                    negative.append({"subject": base_person.id, "rule_index": None,
                                      "status": "not_applicable"})
                    continue
                for rule_index in rule_indices:
                    fresh_world, fresh_people = instance()
                    subject = next(p for p in fresh_people if p.id == base_person.id)
                    rule = subject.range_exclude[rule_index]
                    if rule.get("until_item"):
                        subject.inventory.pop(rule["until_item"], None)
                    # The engine's own runtime notion of "currently
                    # excluded" (an until_item rule only counts while the
                    # item is actually missing) -- reused rather than
                    # reimplemented so the candidate search agrees with how
                    # the simulation itself would treat these zones.
                    excluded_now = fresh_world._excluded_zones(subject)
                    candidates = subject.range_zones - excluded_now - {parent, branch}
                    neighbors = sorted(
                        z for z in candidates
                        if any(route.destination == parent for route in fresh_world.routes.get(z, ())))
                    ordered = sorted(candidates)
                    start = neighbors[0] if neighbors else (ordered[0] if ordered else None)
                    if start is None:
                        negative.append({"subject": base_person.id, "rule_index": rule_index,
                                          "status": "skipped", "start": None,
                                          "reason": "合法な開始場所を構成できません"})
                        continue
                    subject.zone, subject.stamina = start, subject.stamina_max
                    paths = fresh_world.reachable_paths(subject)
                    negative.append({"subject": base_person.id, "rule_index": rule_index,
                                      "status": "checked", "start": start})
                    if parent in paths or branch in paths:
                        violations.append(f"入場条件を回避できます: {base_person.id}/{branch}")
    except Exception as error:
        violations.append(f"世界と人物の契約検査に失敗: {error}")
    result = {"violations": violations}
    if negative:
        result["negative"] = negative
    return result


def reachable_zones_from(world_path, subjects_dir, protagonist, stuck_zones) -> set:
    """WB-WORLDGROW-002 S2: static reachability for a "blocked" trigger's
    check_trigger_coverage -- builds one fresh World+Subject (never touches a
    live run) and unions World.reachable_paths() for `protagonist`, started
    from each of `stuck_zones` in turn. Reuses the engine's own range/
    range_exclude logic (e.g. 村's until_item rule) instead of re-deriving a
    parallel notion of "reachable" -- a zone the protagonist's exclude rule
    currently blocks (they don't hold the item yet, which is exactly the
    blocked trigger's own situation) correctly drops out here too."""
    world = World.from_yaml(world_path)
    people = [Subject.from_yaml(p) for p in sorted(Path(subjects_dir).glob("*.yaml"))]
    world.bind_subjects({p.id: p for p in people})
    subject = next((p for p in people if p.id == protagonist), None)
    if subject is None:
        return set()
    reachable: set = set()
    for zone in stuck_zones:
        if not isinstance(zone, str):
            continue
        subject.zone, subject.stamina = zone, subject.stamina_max
        reachable |= set(world.reachable_paths(subject)) | {zone}
    return reachable


def new_usage(paths, patch, protagonist):
    add = patch.get("add", {})
    zones = {z["name"] for z in add.get("zones", [])}
    items = {i["name"] for i in add.get("items", [])}
    facts = {f["id"] for f in add.get("facts", [])}
    counts = dict.fromkeys(("decisions_in_new_zones", "moves_into_new_zones", "gathered_new_items",
                           "learned_new_facts", "shared_new_facts", "gave_new_items"), 0)
    for path in paths:
        tracked = None
        for row in read_rows(path):
            before = tracked
            tracked = _zone_after(row, protagonist, tracked)
            if row.get("kind") != "decision" or row.get("subject") != protagonist:
                continue
            zone = (row.get("explanation") or {}).get("zone") or before
            counts["decisions_in_new_zones"] += int(zone in zones)
            verb, result = row.get("verb"), row.get("result")
            details, args = row.get("details") or {}, row.get("args") or []
            if result in ("invalid", "failed", "failure", None):
                continue
            if verb == "move" and tracked in zones and tracked != before:
                counts["moves_into_new_zones"] += 1
            counts["gathered_new_items"] += sum(1 for item in details.get("gathered", [])
                                              if isinstance(item, dict) and item.get("item") in items)
            counts["learned_new_facts"] += sum(f in facts for f in details.get("learned", []) if isinstance(f, str))
            argument = args[1] if isinstance(args, list) and len(args) > 1 else None
            if verb == "share_knowledge" and result == "shared" and argument in facts:
                counts["shared_new_facts"] += 1
            if verb == "give_item" and result == "given" and argument in items:
                counts["gave_new_items"] += 1
    return counts


def milestones(paths):
    counts = {"endings": {}, "thresholds": {}}
    for path in paths:
        seen = set()
        for row in read_rows(path):
            if row.get("verb") not in ("ending", "threshold_crossed"):
                continue
            key = str(row.get("id") or (row.get("details") or {}).get("threshold"))
            category = "endings" if row["verb"] == "ending" else "thresholds"
            if (category, key) not in seen:
                counts[category][key] = counts[category].get(key, 0) + 1
                seen.add((category, key))
    return counts
