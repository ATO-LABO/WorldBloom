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
    violations, notes = [], []
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

            # R6: every subject with an exclude rule that targets `parent`
            # is a candidate for the negative check -- try each in turn (not
            # just the first) and only give up with a note (not a
            # violation) if none of them has a usable starting position.
            negative_world, negatives = instance()
            tried_someone, no_start_for = False, []
            for person in negatives:
                rules = [r for r in person.range_exclude if parent in r.get("zones", [])]
                if not rules:
                    continue
                # Only start from a neighbor this person is themselves
                # allowed into and not excluded from -- an out-of-range or
                # self-excluded start leaves reachable_paths nearly empty
                # regardless of the branch's own exclusion, so the check
                # would pass for the wrong reason (WB-WORLDGROW-001 R6).
                excluded_zones = {z for r in person.range_exclude for z in (r.get("zones") or [])}
                neighbors = sorted(
                    n for n, routes in negative_world.routes.items()
                    if n not in (parent, branch) and any(r.destination == parent for r in routes)
                    and n in person.range_zones and n not in excluded_zones
                )
                if not neighbors:
                    no_start_for.append(person.id)
                    continue
                tried_someone = True
                person.zone, person.stamina = neighbors[0], person.stamina_max
                for rule in rules:
                    if rule.get("until_item"):
                        person.inventory.pop(rule["until_item"], None)
                paths = negative_world.reachable_paths(person)
                if parent in paths or branch in paths:
                    violations.append(f"入場条件を回避できます: {person.id}/{branch}")
            if not tried_someone and no_start_for:
                notes.append(f"陰性検査の開始場所がありません: {'、'.join(no_start_for)}")
    except Exception as error:
        violations.append(f"世界と人物の契約検査に失敗: {error}")
    result = {"violations": violations}
    if notes:
        result["notes"] = notes
    return result


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
