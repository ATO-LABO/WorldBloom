"""Finite deterministic projection search, deliberately separate from Policy.

Uniform slot durations, adjacent directed moves, wait, investigate and craft.
This is an explicit symbolic model, not a simulation of all engine effects.
Shortest and unreachable claims refer only to this model and its calendar.
"""

from __future__ import annotations

from copy import deepcopy
from collections import deque
from typing import Any

from gapengine.route_goals import GoalSpec, clock_minutes, _keys, _name, _positive_int


MODEL = "deterministic_slot_projection_v1"
ASSUMPTIONS = (
    "静的な移動・待機・調査入手・製作と対応済み予定イベントのモデル内で探索します。",
    "1行動は1スロット、移動は隣接する1辺です。辺のcostは時間ではありません。",
    "体力・性格・関係・乱数・他人物の自律行動はモデルに含みません。実際の通常経路とは別です。",
    "全状態の最短・実際の達成は保証しません。未対応の行動で達成できる可能性があります。",
)


class UnsupportedModel(ValueError):
    pass


def _calendar(world: Any) -> int:
    minutes = [clock_minutes(slot) for slot in world.slots]
    if len(minutes) < 2:
        raise UnsupportedModel("時刻探索には2つ以上の HH:MM スロットが必要です。")
    duration = minutes[1] - minutes[0]
    if duration <= 0 or any(b - a != duration for a, b in zip(minutes, minutes[1:])):
        raise UnsupportedModel("時刻探索には昇順で等間隔のスロットが必要です。")
    # Avoid silently treating the overnight gap as one regular action.
    if world.days != 1:
        raise UnsupportedModel("最小実装の時刻探索は1日内のみ対応しています。")
    return duration


def _event_validate(world: Any, subjects: dict) -> None:
    allowed = {"id", "label", "day", "slot", "targets", "move_to", "grants_item", "grants_fact"}
    for event in world.scheduled_events:
        if set(event) - allowed:
            raise UnsupportedModel("予定イベントには未対応の効果があります。")
        if event.get("day") != 1 or (event.get("slot") is not None and event["slot"] not in world.slots):
            raise ValueError("Scheduled event is outside the calendar")
        targets = event.get("targets", [])
        if not isinstance(targets, list) or any(not isinstance(target, str) for target in targets):
            raise ValueError("Scheduled event targets must be a list of names")
        if any(target not in subjects for target in targets):
            raise ValueError("Unknown scheduled event subject")
        if event.get("move_to") is not None and event["move_to"] not in world.zones:
            raise ValueError("Unknown scheduled event zone")
        if event.get("grants_item") is not None:
            grant = event["grants_item"]
            _keys(grant, {"name", "count"}, {"name"})
            if _name(grant["name"]) not in world.items:
                raise ValueError("Unknown scheduled event item")
            _positive_int(grant.get("count", 1))
        fact = event.get("grants_fact")
        if fact is not None and (fact not in world.facts or world.facts[fact].get("values") is not None):
            raise UnsupportedModel("予定イベントの情報は無値の既知情報のみ対応しています。")


def _events(world: Any, subjects: dict, slot: str | None) -> None:
    # Events are applied at the boundary before evaluating the goal there.
    for event in world.scheduled_for(1, slot):
        for person_id in event.get("targets", []):
            person = subjects[person_id]
            if person.vitality == "dead":
                continue
            if event.get("move_to") is not None:
                person.zone = event["move_to"]
            if event.get("grants_item"):
                grant = event["grants_item"]
                person.add_item(grant["name"], grant.get("count", 1))
            if event.get("grants_fact"):
                person.knowledge.add(event["grants_fact"])


def _fingerprint(subjects: dict) -> tuple:
    return tuple((key, person.zone, tuple(sorted(person.inventory.items())),
                  tuple(sorted(person.knowledge)), tuple(sorted(person.gathered.items())),
                  tuple(sorted(person.gather_progress.items())))
                 for key, person in sorted(subjects.items()))


def _successors(world: Any, subjects: dict, actor_id: str):
    actor = subjects[actor_id]
    if actor.vitality not in {"alive", "revived"}:
        yield "wait", "待機する", subjects
        return
    def allowed(verb: str) -> bool:
        return verb in actor.verbs and world.genre_allows(verb)
    if allowed("move"):
        for edge in world.routes.get(actor.zone, ()):
            if edge.destination not in actor.range_zones or edge.destination in world._excluded_zones(actor):
                continue
            if edge.requires_item is not None and not actor.has_item(edge.requires_item):
                continue
            after = deepcopy(subjects)
            after[actor_id].zone = edge.destination
            yield "move", f"{edge.destination}へ移動", after
    if allowed("investigate"):
        after = deepcopy(subjects)
        person = after[actor_id]
        for item, definition in sorted(world.items.items()):
            for index, source in enumerate(definition.get("sources", []) or []):
                if source.get("type") != "investigate" or source.get("zone") != actor.zone:
                    continue
                key = f"item:{item}:{actor.zone}:{index}"
                maximum = int(source.get("max", 1))
                if person.gathered.get(key, 0) >= maximum:
                    continue
                progress_key = f"progress:{key}"
                progress = person.gather_progress.get(progress_key, 0) + 1
                person.gather_progress[progress_key] = progress
                if progress >= max(1, int(source.get("count", 1))):
                    person.gather_progress[progress_key] = 0
                    person.gathered[key] = person.gathered.get(key, 0) + 1
                    person.add_item(item, 1)
        # Unvalued facts use the same count/progress convention as items.
        for fact, definition in sorted(world.facts.items()):
            if definition.get("values") is not None or fact in person.knowledge:
                continue
            for index, source in enumerate(definition.get("sources", []) or []):
                if source.get("type") != "investigate" or source.get("zone") != actor.zone or source.get("requires"):
                    continue
                key = f"fact:{fact}:{index}"
                progress = person.gather_progress.get(key, 0) + 1
                person.gather_progress[key] = progress
                if progress >= max(1, int(source.get("count", 1))):
                    person.knowledge.add(fact)
                    person.gather_progress[key] = 0
                    break
        if _fingerprint(after) != _fingerprint(subjects):
            yield "investigate", f"{actor.zone}を調査して入手・学習", after
    if allowed("craft"):
        for item, recipe in sorted(world.recipes.items()):
            definition = world.items[item]
            fact = (definition.get("requires") or {}).get("knowledge")
            if fact is not None and fact not in actor.knowledge:
                continue
            if definition.get("craft_zone") not in (None, actor.zone):
                continue
            if not all(actor.inventory.get(material, 0) >= int(count) for material, count in recipe.items()):
                continue
            after = deepcopy(subjects)
            person = after[actor_id]
            for material, count in recipe.items():
                person.remove_item(material, int(count))
            person.add_item(item, 1)
            yield "craft", f"{item}を製作", after
    yield "wait", "待機する", subjects


def search(world: Any, subjects: dict, goal: GoalSpec, options: Any = None) -> dict[str, Any]:
    """BFS over (time, full projected state), including waiting and quantities.

    Cost is elapsed slots including waits. Exhaustion proves no solution only
    in the finite projected model. A budget hit is always cutoff, never that
    proof. Goal checks precede the expansion budget, including at the horizon.
    """
    duration = _calendar(world)
    options = {} if options is None else options
    _keys(options, {"max_expansions"}, set())
    budget = _positive_int(options.get("max_expansions", 5000))
    if budget > 100000:
        raise ValueError("max_expansions must not exceed 100000")
    _event_validate(world, subjects)
    start = deepcopy(subjects)
    _events(world, start, None)
    _events(world, start, world.slots[0])
    end = len(world.slots) - 1 if goal.mode == "any" else world.slots.index(goal.slot)
    queue = deque([(0, start, ())])
    seen = {(0, _fingerprint(start))}
    expansions = 0
    base = {"model": MODEL, "assumptions": list(ASSUMPTIONS), "goal": goal.as_dict(),
            "optimality_scope": "elapsed_slots_in_model", "slot_minutes": duration}
    while queue:
        tick, state, steps = queue.popleft()
        if goal.satisfied(state, 1, world.slots[tick]):
            return {**base, "status": "found", "message": "モデル内の最短経路（待機を含む経過スロット数）",
                    "shortest_guaranteed": True, "steps": list(steps), "cost": tick,
                    "elapsed_minutes": tick * duration, "expansions": expansions,
                    "reached": {"day": 1, "slot": world.slots[tick]}, "start": start}
        if tick >= end:
            continue
        if expansions >= budget:
            return {**base, "status": "cutoff", "message": "探索上限で打ち切り（到達可能性は未判定）",
                    "shortest_guaranteed": False, "steps": [], "cost": None,
                    "expansions": expansions, "start": start}
        expansions += 1
        for kind, text, after in _successors(world, state, goal.actor):
            after = deepcopy(after)
            _events(world, after, world.slots[tick + 1])
            key = (tick + 1, _fingerprint(after))
            if key in seen:
                continue
            seen.add(key)
            step = {"kind": kind, "text": text, "zone": after[goal.actor].zone,
                    "cost": 1, "cumulative": tick + 1,
                    "at": {"day": 1, "slot": world.slots[tick]},
                    "next": {"day": 1, "slot": world.slots[tick + 1]}}
            queue.append((tick + 1, after, steps + (step,)))
    return {**base, "status": "unreachable", "message": "設定期間・対応モデル内では到達不能（全世界の証明ではありません）",
            "shortest_guaranteed": False, "steps": [], "cost": None,
            "expansions": expansions, "start": start}


def survey_goal(world: Any, subjects: dict, goal: GoalSpec, options: Any = None) -> dict[str, Any]:
    result = search(world, subjects, goal, options)
    start = result.pop("start")[goal.actor]
    routes = []
    if result["status"] == "found":
        routes.append({"rank": 1, "label": result["message"], "route": None,
                       "conditions": list(ASSUMPTIONS), "h": None, "cost": result["cost"],
                       "steps": result["steps"], "truncated": False,
                       "timing": "model", "shortest_guaranteed": True,
                       "result_label": result["message"]})
    return {**result, "timepoints": [{"label": f"開始時（1日目 {world.slots[0]}の予定イベント適用後）",
        "day": 1, "slot": world.slots[0], "events": [], "routes": routes,
        "message": result["message"], "blocked": [],
        "start": {"zone": start.zone, "inventory": dict(start.inventory), "knowledge": sorted(start.knowledge)}}]}
