"""WB-ROUTE-PATHS-001: "最短経路を調査" -- enumerate a handful of distinct,
ordered step-by-step walkthroughs from the protagonist's current state to
the story's ending, reusing ``gapengine.route``'s existing backward-chaining
planner (``plan()``) as the only source of truth for cost (``h``) and for
which branch (fight/negotiate/craft/investigate/trial) is currently
cheapest. This module never mutates ``route.py`` and never touches its
private functions' *behaviour* -- it only calls them read-only, on its own
throwaway ``World``/``Subject`` copies (freshly reloaded from YAML for every
timepoint and every knockout combination), and duplicates a small amount of
``_acquire``'s own branch-priority logic (craft -> investigate -> trial ->
take, same order, same cost formulas) purely to narrate *which* concrete
action realizes a plan the real ``_acquire`` already decided was cheapest --
see ``_apply_has_item_leaf`` below.

Deterministic and read-only: every helper here only ever calls ``plan()``
(and the few private route.py helpers tests already import) on a subject/
world pair built fresh from the project's YAML files for this call; no
randomness is consumed, no shared mutable state survives past one
``survey()`` call, and the original project/template YAML files are never
written to.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable, Sequence

from engine.actions import Action
from engine.contest import believed_strength, strength
from engine.subject import Subject
from engine.sim import Simulation
from engine.world import World

from gapengine.evolve import _load_subjects
from gapengine.route import (
    INF,
    _acquire,
    _believed_win_probability,
    _blocked_on,
    _holder_appears_to_have,
    _kinds,
    _leaf_tags,
    _leaf_zone,
    _MAX_FIGHT_ROUNDS,
    _reachable_shortest_path,
    _STANCE_RAISE_COST,
    _BOOST_FALLBACK_COST,
    load_route_config,
    plan,
)

NO_ROUTE_CONFIG_MESSAGE = "このジャンルには道筋設定（route.yaml）が無いため計算できません。"

Mutator = Callable[[World, dict[str, Subject]], None]

_SIGNATURE_KINDS = frozenset(
    {"route", "has_item", "win_fight", "stance_ge", "knows", "boost_fight", "strong_enough"}
)
_MAX_ROUTES = 3
_MAX_PLAN_CALLS = 12
_MAX_WALK_STEPS = 60


class _NullWriter:
    """Discards every row -- ``state_at`` reuses ``Simulation``'s own day/
    slot scheduled-event replay (``_apply_scheduled``) purely for its
    (deterministic, rng-free) state mutation, never for its logging."""

    def write(self, row: Any) -> None:  # noqa: ARG002 -- intentionally a no-op
        return None


# ---------------------------------------------------------------------------
# State reconstruction (fresh from YAML every time -- never mutates the
# project's own files, never reuses a Subject/World instance across calls)
# ---------------------------------------------------------------------------


def _load_world_and_subjects(
    project_dir: str | Path, template_dir: str | Path
) -> tuple[World, dict[str, Subject]]:
    project_dir = Path(project_dir)
    template_dir = Path(template_dir)
    world = World.from_yaml(
        project_dir / "world.yaml", action_graph_path=template_dir / "action_graph.yaml"
    )
    subjects = _load_subjects(project_dir / "subjects")
    return world, subjects


def state_at(
    project_dir: str | Path,
    template_dir: str | Path,
    upto: tuple[int, str | None],
    mutate: Mutator | None = None,
) -> tuple[World, dict[str, Subject]]:
    """A fresh ``(world, subjects)`` pair, reloaded from the project's own
    YAML and replayed only up through and including ``upto=(day, slot)``'s
    scheduled events (day-opening ``slot=None`` events, then each day's
    slots in order) -- day-daily random events and per-subject actions are
    never applied (the planner only ever needs *scheduled* state, and must
    never consume randomness). ``mutate`` (used by knockout re-planning) is
    applied once, right after loading, before any event replay.

    ``Simulation(0, ...)`` fixes both the RNG seed *and* (via ``World.
    resolve_truth``, called from ``Simulation.__init__``) any ``truth_
    candidates`` this world declares to their seed-0 draw -- deterministic
    and safe for a fetch-and-deliver world like momotaro_plus2 (the
    protagonist's own plan never reads ``world.truth`` at all, per route.py's
    module docstring). A genre whose route.yaml goal instead depended on a
    *resolved* truth fact (none currently do -- detective/romance have no
    route.yaml) would need this call site revisited, since "seed 0's truth"
    is one arbitrary draw, not the template's canonical answer."""

    world, subjects = _load_world_and_subjects(project_dir, template_dir)
    if mutate is not None:
        mutate(world, subjects)

    sim = Simulation(0, world, subjects, out_dir=Path("."))
    writer = _NullWriter()
    target_day, target_slot = upto
    slots = list(world.slots)

    for day in range(1, target_day + 1):
        sim.day = day
        sim.slot = None
        sim._apply_scheduled(writer, slot=None)
        if target_slot is None and day == target_day:
            return sim.world, sim.subjects
        for slot in slots:
            sim.slot = slot
            sim._apply_scheduled(writer, slot=slot)
            _apply_forced_move(sim, day)
            if day == target_day and slot == target_slot:
                return sim.world, sim.subjects

    return sim.world, sim.subjects


def _apply_forced_move(sim: Simulation, day: int) -> None:
    """A scheduled ``force_action`` (WB-TIMEEVENT-001) replaces the
    protagonist's action for this slot, so it is certain and belongs in the
    starting state (momotaro's departure_day: 村 -> 道中). Only ``move`` is
    replayed -- it is rng-free (``VerbEngine._move``); any other forced verb
    is left to the actual run."""

    protagonist = sim.world.protagonist
    forced = sim._forced.pop(protagonist, None)
    if forced and forced["verb"] == "move" and protagonist in sim.subjects:
        sim.verb_engine._move(
            sim.subjects[protagonist],
            Action("move", tuple(forced["args"])),
            turn=sim.turn,
            day=day,
        )


def timepoints(world: World) -> list[tuple[int, str | None]]:
    """``(1, slots[0])`` (the "start" moment, S0's own definition of "day 1,
    right after that day's own scheduled events") is always first; every
    later ``(day, slot)`` with a non-empty ``world.scheduled_for`` follows,
    in day/slot order. ``scheduled_events`` is static YAML data, so this
    never needs a replayed state to compute."""

    slots = list(world.slots)
    if not slots:
        return [(1, None)]
    order: list[tuple[int, str | None]] = []
    for day in range(1, world.days + 1):
        order.append((day, None))
        for slot in slots:
            order.append((day, slot))
    start = (1, slots[0])
    start_index = order.index(start)
    result = [start]
    for day, slot in order[start_index + 1 :]:
        if world.scheduled_for(day, slot):
            result.append((day, slot))
    return result


def _timepoint_label(day: int, slot: str | None, world: World) -> str:
    slots = list(world.slots)
    if (day, slot) == (1, slots[0] if slots else None):
        return f"開始時（{day}日目 朝の予定イベント適用後）"
    events = world.scheduled_for(day, slot)
    names = "、".join(
        str(event.get("label") or event.get("id")) for event in events
    )
    # nice (Opus review): slot=None (the day-opening moment, before any of
    # that day's own named time slots) must read differently from that same
    # day's *first* named slot -- both can carry scheduled events, and
    # conflating them ("朝" for slot=None regardless of what the first real
    # slot is actually called) made two distinct timepoints look identical.
    slot_label = slot if slot is not None else f"{day}日目の始まり"
    return f"{day}日目 {slot_label}『{names}』の後"


# ---------------------------------------------------------------------------
# Route enumeration via mechanical knockouts of the baseline plan
# ---------------------------------------------------------------------------


def _signature(plan_result: dict[str, Any]) -> tuple:
    """The distinctness key for route enumeration. ``plan_result["route"]``
    (fight/negotiate/None) must be included explicitly: while a
    ``strong_enough`` danger-gate node is open, ``_acquire_from_subject``
    demotes every ``("route", ...)`` tag out of ``best`` entirely (see
    route.py's own docstring, "design judgment E"), so two plans that will
    ultimately resolve to different routes can otherwise look identical
    from their ``best`` tags alone (S1, Opus review)."""

    tags = tuple(
        sorted((kind, value) for kind, value in plan_result["best"] if kind in _SIGNATURE_KINDS)
    )
    return (("route_name", plan_result["route"]),) + tags


def _make_item_knockout(item: str) -> Mutator:
    def _mutate(world: World, subjects: dict[str, Subject]) -> None:  # noqa: ARG001
        world.recipes.pop(item, None)
        if item in world.items:
            world.items[item]["sources"] = []
        world.trials[:] = [
            trial for trial in world.trials if (trial.get("grants") or {}).get("item") != item
        ]

    return _mutate


def _make_trial_knockout(giver: str) -> Mutator:
    def _mutate(world: World, subjects: dict[str, Subject]) -> None:  # noqa: ARG001
        world.trials[:] = [trial for trial in world.trials if str(trial.get("giver", "")) != giver]

    return _mutate


def _make_verb_knockout(verb: str, subject_id: str) -> Mutator:
    def _mutate(world: World, subjects: dict[str, Subject]) -> None:  # noqa: ARG001
        subjects[subject_id].verbs.discard(verb)

    return _mutate


def _knockouts_for(
    plan_result: dict[str, Any], subject_id: str, world: World, subjects: dict[str, Subject]
) -> list[tuple[str, Mutator]]:
    """One mechanical knockout per still-live lever, in a fixed
    rule-then-tag-name order (BFS determinism). S1 (Opus review): the
    fight/negotiate knockouts key off ``plan_result["route"]`` -- the
    explicit winning branch ``_acquire_from_subject`` returns -- rather than
    a ``("route", ...)`` tag in ``best``, which a danger-gated
    (``strong_enough``) plan never carries even when it will resolve to
    fight or negotiate once the gate clears."""

    best = plan_result["best"]
    kinds = _kinds(best)
    subject = subjects[subject_id]
    result: list[tuple[str, Mutator]] = []
    if plan_result["route"] == "fight" and "fight" in subject.verbs:
        result.append(("K-fight", _make_verb_knockout("fight", subject_id)))
    if plan_result["route"] == "negotiate" and "negotiate" in subject.verbs:
        result.append(("K-negotiate", _make_verb_knockout("negotiate", subject_id)))
    for item in sorted(kinds.get("has_item", ())):
        if subject.has_item(str(item)):
            continue
        result.append((f"K-item({item})", _make_item_knockout(str(item))))
    for giver in sorted(kinds.get("stance_ge", ())):
        result.append((f"K-trial({giver})", _make_trial_knockout(str(giver))))
    return result


def _knockout_condition_text(name: str) -> str:
    if name == "K-fight":
        return "戦わない"
    if name == "K-negotiate":
        return "交渉しない"
    if name.startswith("K-item(") and name.endswith(")"):
        return f"{name[len('K-item('):-1]}が手に入らない"
    if name.startswith("K-trial(") and name.endswith(")"):
        return f"{name[len('K-trial('):-1]}の試練を受けられない"
    return name


def _compose(chain: Sequence[Mutator]) -> Mutator:
    def _mutate(world: World, subjects: dict[str, Subject]) -> None:
        for mutator in chain:
            mutator(world, subjects)

    return _mutate


def _enumerate_routes(
    project_dir: Path,
    template_dir: Path,
    upto: tuple[int, str | None],
    route_cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    """Baseline (no knockout) plus up to ``_MAX_ROUTES - 1`` more distinct
    (by ``_signature``) plans reached by mechanically knocking out one lever
    of an already-found plan at a time, breadth-first, depth <= 2, within
    ``_MAX_PLAN_CALLS`` total ``plan()`` calls. Baseline always sorts
    first; the rest by ``h`` ascending (ties broken by signature)."""

    world0, subjects0 = state_at(project_dir, template_dir, upto)
    subject_id = world0.protagonist
    baseline = plan(
        subjects0[subject_id],
        world0,
        holder_belief_fact=route_cfg["holder_belief_fact"],
        trial_reveal_facts=route_cfg["trial_reveal_facts"],
        min_win_prob=route_cfg["min_win_prob"],
    )
    calls = 1
    seen = {_signature(baseline)}
    # Each frontier/accepted entry carries its own knockout-name chain
    # alongside the mutator chain, so the UI can show "この段取りになる
    # 条件" without re-deriving it from opaque mutator closures.
    accepted: list[dict[str, Any]] = [
        {"chain": (), "plan": baseline, "sig": _signature(baseline), "knockouts": ()}
    ]
    frontier: list[tuple[tuple[Mutator, ...], tuple[str, ...], dict[str, Any]]] = [
        ((), (), baseline)
    ]

    depth = 0
    while len(accepted) < _MAX_ROUTES and frontier and depth < 2 and calls < _MAX_PLAN_CALLS:
        next_frontier: list[tuple[tuple[Mutator, ...], tuple[str, ...], dict[str, Any]]] = []
        for chain, names, plan_result in frontier:
            if plan_result["h"] in (None, INF):
                continue
            world_c, subjects_c = state_at(project_dir, template_dir, upto, mutate=_compose(chain))
            knockouts = _knockouts_for(plan_result, subject_id, world_c, subjects_c)
            for name, mutator in knockouts:
                if len(accepted) >= _MAX_ROUTES or calls >= _MAX_PLAN_CALLS:
                    break
                new_chain = chain + (mutator,)
                new_names = names + (name,)
                world2, subjects2 = state_at(
                    project_dir, template_dir, upto, mutate=_compose(new_chain)
                )
                plan2 = plan(
                    subjects2[subject_id],
                    world2,
                    holder_belief_fact=route_cfg["holder_belief_fact"],
                    trial_reveal_facts=route_cfg["trial_reveal_facts"],
                    min_win_prob=route_cfg["min_win_prob"],
                )
                calls += 1
                if plan2["h"] in (None, INF):
                    continue
                sig2 = _signature(plan2)
                if sig2 in seen:
                    continue
                seen.add(sig2)
                entry = {"chain": new_chain, "plan": plan2, "sig": sig2, "knockouts": new_names}
                accepted.append(entry)
                next_frontier.append((new_chain, new_names, plan2))
        frontier = next_frontier
        depth += 1

    rest = sorted(accepted[1:], key=lambda entry: (entry["plan"]["h"], entry["sig"]))
    return [accepted[0]] + rest


# ---------------------------------------------------------------------------
# Ordered step-by-step walkthrough for one (already knocked-out) plan
# ---------------------------------------------------------------------------


def _needed_qty(item: str, best_kinds: dict[str, set], world: World) -> int:
    consumers = [
        world.recipes[product][item]
        for product in best_kinds.get("has_item", ())
        if product in world.recipes and item in world.recipes[product]
    ]
    # Phase B: a priced (buy) item's currency is a "consumer" the same way a
    # recipe's material is -- e.g. 街 labor must gather all 3 小判 a 鉄砲
    # costs, not just 1.
    consumers += [
        int(world.items[product]["price"][item])
        for product in best_kinds.get("has_item", ())
        if world.items.get(product, {}).get("price")
        and item in world.items[product]["price"]
    ]
    return max(consumers, default=1)


def _fight_rounds(subject: Subject, holder: Subject, world: World) -> float:
    probability = _believed_win_probability(subject, holder, world, world.present_subjects(subject.zone))
    if probability <= 0.0:
        return INF
    return float(math.ceil(min(_MAX_FIGHT_ROUNDS, 1.0 / probability)))


def _state_fingerprint(subject: Subject, world: World) -> tuple:
    """M2 (Opus review): a full, deterministic snapshot of everything a
    ``plan()`` call actually reads off ``subject``/``world`` state --
    location, inventory, knowledge, fight strength, and every giver's
    stance toward the subject (route.py's own stance checks are always
    ``giver -> subject``, see ``_trial_cost``/``_acquire_from_subject``).
    Used only to detect a stalled walk (the exact same state recurring),
    never to change what action is chosen."""

    return (
        subject.zone,
        tuple(sorted(subject.inventory.items())),
        tuple(sorted(subject.knowledge)),
        round(subject.base, 6),
        tuple(
            sorted(
                (giver_id, round(world.relations.stance(giver_id, subject.id), 6))
                for giver_id in world.subjects
            )
        ),
    )


def _hop_toward(subject: Subject, world: World, dest: str) -> dict[str, Any] | None:
    """One single hop (not the whole path) toward ``dest`` -- the caller
    re-plans from scratch every step, so only the *first* edge of the
    current shortest path is ever actually taken."""

    excluded = frozenset(world._excluded_zones(subject))
    path = _reachable_shortest_path(world, subject, subject.zone, dest, excluded)
    if not path:
        return None
    route = path[0]
    subject.zone = route.destination
    return {"zone": route.destination, "cost": 1.0}


def _apply_has_item_leaf(
    item: str,
    subject: Subject,
    world: World,
    best_kinds: dict[str, set],
) -> dict[str, Any]:
    """Same branch priority order as ``_acquire`` (craft -> buy ->
    investigate/labor -> trial -> take), re-derived here (not reused from
    ``_acquire`` -- that function only ever returns tags/cost, never "which
    branch won") purely to narrate + apply the one already-cheapest,
    already-leaf-gated
    acquisition. By construction (``_leaf_tags`` only exposes a leaf once
    its own prerequisites are already satisfied), every branch below finds
    its materials/stance/travel already in place."""

    definition = world.items.get(item, {})
    if item in world.recipes:
        for material, qty in world.recipes[item].items():
            subject.remove_item(material, qty)
        subject.add_item(item, 1)
        return {"kind": "craft", "text": f"{subject.zone}で{item}を作る", "cost": 1.0}

    price = definition.get("price")
    shop_zone = definition.get("shop_zone")
    if price and shop_zone:
        for currency, qty in sorted(price.items()):
            subject.remove_item(currency, int(qty))
        subject.add_item(item, 1)
        return {"kind": "buy", "text": f"{shop_zone}で{item}を買う", "cost": 1.0}

    for source in definition.get("sources", []) or []:
        if source.get("type") in ("investigate", "labor") and source.get("zone") == subject.zone:
            needed = _needed_qty(item, best_kinds, world)
            deficit = max(1, needed - subject.inventory.get(item, 0))
            per_action = max(1, int(source.get("count", 1) or 1))
            actions = math.ceil(deficit / per_action)
            subject.add_item(item, deficit)
            verb_label = "働く" if source.get("type") == "labor" else "調べる"
            label = f"{subject.zone}で{item}を{verb_label}" + (f" ×{actions}" if actions > 1 else "")
            kind = "labor" if source.get("type") == "labor" else "investigate"
            return {"kind": kind, "text": label, "cost": float(actions), "count": actions}

    trial = next(
        (t for t in world.trials if (t.get("grants") or {}).get("item") == item), None
    )
    if trial is not None:
        giver_id = str(trial.get("giver", ""))
        subject.add_item(item, 1)
        return {"kind": "trial", "text": f"{giver_id}から{item}を受け取る（試練）", "cost": 1.0}

    holder_id = world.holder(item)
    if holder_id is not None and holder_id in world.subjects:
        holder = world.subjects[holder_id]
        rounds = _fight_rounds(subject, holder, world)
        cost = 1.0 if rounds == INF else rounds
        holder.remove_item(item, 1)
        subject.add_item(item, 1)
        return {"kind": "fight", "text": f"{holder_id}から{item}を奪う", "cost": cost}

    # Should not happen once _leaf_tags has already gated this item as
    # actionable -- a defensive, still-costed fallback rather than a crash.
    subject.add_item(item, 1)
    return {"kind": "unknown", "text": f"{item}を手に入れる", "cost": 1.0}


def _apply_stance_leaf(
    giver_id: str, subject: Subject, world: World, believed_holder_id: str | None
) -> dict[str, Any]:
    """M2 (Opus review): the threshold to clear is not always a trial's own
    ``requires.stance`` -- when ``giver_id`` is the objective's believed
    holder, ``_acquire_from_subject``'s negotiate branch instead raises
    stance against ``world.negotiate_threshold`` (route.py line ~1222,
    "if world.relations.stance(holder.id, subject.id) < world.negotiate_
    threshold"). Using only the trial threshold (default 0.0, when the
    giver has no trial at all) under-shot the real requirement and made
    negotiate's own stance_ge tag re-open every single step -- observed
    looping "○○と親しくなる" ~53 times before the 60-step budget cut it off."""

    trial_threshold = max(
        (
            float((trial.get("requires") or {}).get("stance", -1.0))
            for trial in world.trials
            if str(trial.get("giver", "")) == giver_id
        ),
        default=0.0,
    )
    threshold = trial_threshold
    if giver_id == believed_holder_id:
        threshold = max(threshold, world.negotiate_threshold)
    current = world.relations.stance(giver_id, subject.id)
    world.relations.change(giver_id, subject.id, affinity=(threshold - current + 0.01))
    return {"kind": "stance", "text": f"{giver_id}と親しくなる", "cost": _STANCE_RAISE_COST}


def _apply_knows_leaf(fact_id: str, subject: Subject, world: World) -> dict[str, Any]:
    subject.knowledge.add(fact_id)
    return {"kind": "knows", "text": f"{subject.zone}で{fact_id}を調べる", "cost": 1.0}


def _negotiate_offer_item(
    holder: Subject,
    subject: Subject,
    world: World,
    trial_reveal_facts: Any,
) -> str | None:
    """Same rule as route.py's own ``_best_offer``: nothing to offer at all
    once stance is already high enough (``world.negotiate_threshold``);
    otherwise the cheapest-to-acquire lootable+modifier item the holder
    isn't already believed to have (an already-held item is free, ``h``==0,
    and so always wins the tie -- ``_acquire`` returns 0.0 exactly for what
    the subject already holds). M1 (Opus review): walk() used to offer
    whatever lootable+modifier item it had *most recently picked up* for any
    reason (e.g. a pure strength item fetched via ``_first_strength_item``),
    which could name an item route.py's own plan never offered."""

    if world.relations.stance(holder.id, subject.id) >= world.negotiate_threshold:
        return None
    candidates: list[tuple[float, str]] = []
    for name, definition in sorted(world.items.items()):
        if not definition.get("lootable") or not definition.get("modifier"):
            continue
        if _holder_appears_to_have(subject, holder, name, world):
            continue
        item_h, _best, _alt = _acquire(name, subject, world, frozenset(), trial_reveal_facts)
        if item_h == INF:
            continue
        candidates.append((item_h, name))
    if not candidates:
        return None
    return min(candidates, key=lambda pair: pair[0])[1]


def walk(
    project_dir: str | Path,
    template_dir: str | Path,
    upto: tuple[int, str | None],
    route_cfg: dict[str, Any],
    mutate: Mutator | None = None,
) -> dict[str, Any]:
    """One ordered, deterministic step-by-step walkthrough -- re-plans from
    scratch (``plan()``) before every single atomic step (one travel hop, or
    one leaf action), so the classification of "what to do next" always
    matches the freshest state.

    ``h`` (route.py's own cost estimate) is *not* a true shortest-remaining-
    distance metric -- it sums each currently-open need's own ``_travel``
    distance independently from the subject's *current* zone (route.py's own
    documented limitation, module docstring: "h itself is not used for
    [cause] classification and never will be: it is a report-only number
    that can jump non-monotonically"). Empirically confirmed here too: a
    single correct hop toward the nearest open leaf can raise ``h`` simply
    because some *other*, unrelated need's distance-from-here changed. So
    ``h`` is never used as a per-step progress gate here.

    ``truncated`` instead fires on a step budget (``_MAX_WALK_STEPS``) or a
    *repeated* full state fingerprint (M2, Opus review): zone + inventory +
    knowledge + strength ``base`` + every stance toward the subject, taken
    right after each step. A step that leaves every one of those unchanged
    means nothing about the world actually moved, so re-planning next turn
    can only pick the exact same leaf again -- an infinite loop, not
    progress. Caught this way after ``_apply_stance_leaf`` was found looping
    "○○と親しくなる" ~53 times in a fixture with no fight/negotiate verb and
    no craftable/tradeable item at all (the fingerprint repeats after the
    very first such step, since the stance leaf's own threshold bug -- fixed
    alongside this -- kept re-raising the same relation by the same delta)."""

    world, subjects = state_at(project_dir, template_dir, upto, mutate=mutate)
    subject_id = world.protagonist
    subject = subjects[subject_id]
    target = subject.goal.target
    deliver_to = subject.goal.deliver_to

    steps: list[dict[str, Any]] = []
    cumulative = 0.0
    truncated = False
    seen_fingerprints: set[tuple] = set()

    for _ in range(_MAX_WALK_STEPS):
        result = plan(
            subject,
            world,
            holder_belief_fact=route_cfg["holder_belief_fact"],
            trial_reveal_facts=route_cfg["trial_reveal_facts"],
            min_win_prob=route_cfg["min_win_prob"],
        )
        h = result["h"]
        if h is None or h == 0.0:
            break
        if h == INF:
            truncated = True
            break

        best_kinds = _kinds(result["best"])
        leaves = _leaf_tags(best_kinds, world)

        action: dict[str, Any] | None = None
        if leaves:
            best_choice: tuple[float, tuple[str, Any], list] | None = None
            for tag in leaves:
                zone = _leaf_zone(tag, world)
                if zone is None or zone == subject.zone:
                    distance = 0
                    path = []
                else:
                    excluded = frozenset(world._excluded_zones(subject))
                    path = _reachable_shortest_path(world, subject, subject.zone, zone, excluded)
                    if path is None:
                        continue
                    distance = len(path)
                if best_choice is None or distance < best_choice[0]:
                    best_choice = (distance, tag, path)
            if best_choice is not None:
                distance, tag, path = best_choice
                if distance > 0:
                    hop = _hop_toward(subject, world, _leaf_zone(tag, world))
                    if hop is not None:
                        action = {"kind": "move", "zone": hop["zone"], "text": f"{hop['zone']}へ移動", "cost": hop["cost"]}
                else:
                    kind, value = tag
                    if kind == "has_item":
                        action = _apply_has_item_leaf(str(value), subject, world, best_kinds)
                    elif kind == "knows":
                        action = _apply_knows_leaf(str(value), subject, world)
                    elif kind == "stance_ge":
                        action = _apply_stance_leaf(
                            str(value), subject, world, result["believed_holder"]
                        )
                    elif kind == "win_fight":
                        holder = world.subjects.get(str(value))
                        if holder is not None:
                            rounds = _fight_rounds(subject, holder, world)
                            cost = 1.0 if rounds == INF else rounds
                            if str(value) == result["believed_holder"] and not subject.has_item(str(target)):
                                subject.add_item(str(target), 1)
                            action = {"kind": "fight", "text": f"{value}と戦う", "cost": cost}
        elif subject.has_item(str(target)):
            if deliver_to is not None and subject.zone != deliver_to:
                hop = _hop_toward(subject, world, deliver_to)
                if hop is not None:
                    text = f"{deliver_to}へ届ける" if hop["zone"] == deliver_to else f"{hop['zone']}へ移動"
                    action = {"kind": "move", "zone": hop["zone"], "text": text, "cost": hop["cost"]}
        elif "strong_enough" in best_kinds:
            target_holder = sorted(best_kinds["strong_enough"])[0]
            holder = world.subjects.get(str(target_holder))
            if holder is not None:
                current = strength(subject, world, world.present_subjects(subject.zone))
                theirs = believed_strength(subject, holder, world, world.present_subjects(subject.zone))
                tau = world.contest["tau"]
                needed_gap = tau * math.log(route_cfg["min_win_prob"] / (1.0 - route_cfg["min_win_prob"]))
                bump = max(0.0, needed_gap - (current - theirs)) + 0.01
                subject.base += bump
                action = {"kind": "train", "text": f"鍛錬して{target_holder}に対抗できる強さになる", "cost": _BOOST_FALLBACK_COST}
        elif result["route"] == "negotiate":
            holder_id = result["believed_holder"]
            holder = world.subjects.get(str(holder_id)) if holder_id else None
            if holder is not None:
                if subject.zone != holder.zone:
                    hop = _hop_toward(subject, world, holder.zone)
                    if hop is not None:
                        action = {"kind": "move", "zone": hop["zone"], "text": f"{hop['zone']}へ移動", "cost": hop["cost"]}
                else:
                    offer_item = _negotiate_offer_item(
                        holder, subject, world, route_cfg["trial_reveal_facts"]
                    )
                    if offer_item is not None and subject.has_item(offer_item):
                        subject.remove_item(offer_item, 1)
                        offer_text = f"（{offer_item}を差し出す）"
                    else:
                        offer_text = ""
                    subject.add_item(str(target), 1)
                    action = {"kind": "negotiate", "text": f"{holder_id}と交渉する{offer_text}", "cost": 1.0}

        if action is None:
            truncated = True
            break

        cumulative += action["cost"]
        steps.append(
            {
                "kind": action["kind"],
                "zone": subject.zone,
                "text": action["text"],
                **({"count": action["count"]} if "count" in action else {}),
                "cost": action["cost"],
                "cumulative": round(cumulative, 4),
            }
        )

        fingerprint = _state_fingerprint(subject, world)
        if fingerprint in seen_fingerprints:
            truncated = True
            break
        seen_fingerprints.add(fingerprint)
    else:
        truncated = True

    return {"steps": steps, "truncated": truncated}


# ---------------------------------------------------------------------------
# Top-level survey
# ---------------------------------------------------------------------------


def _blocked_text(blocked: tuple[str, str, Any]) -> str:
    """A short Japanese sentence for one ``gapengine.route._blocked_on``
    result -- nice-to-have readability, never fed back into any decision."""

    kind, value, detail = blocked
    detail = detail or {}
    if kind == "has_item":
        held_by = detail.get("held_by")
        return f"{value}が手に入らない" + (f"（{held_by}が所持）" if held_by else "")
    if kind == "knows":
        return f"{value}を知る手段がない"
    if kind == "reach":
        return f"{value}へたどり着けない"
    return f"{kind}:{value}"


def survey(project_dir: str | Path, template_dir: str | Path) -> dict[str, Any]:
    project_dir = Path(project_dir)
    template_dir = Path(template_dir)
    cfg = load_route_config(template_dir)
    if cfg is None:
        return {
            "status": "no_route_config",
            "message": NO_ROUTE_CONFIG_MESSAGE,
            "timepoints": [],
        }

    world0, subjects0 = _load_world_and_subjects(project_dir, template_dir)
    protagonist = subjects0[world0.protagonist]
    if protagonist.goal.target is None:
        return {
            "status": "no_goal",
            "message": (
                "主人公の目標が『品物を手に入れて届ける』型ではないため、"
                "現在は計算できません（探偵・恋愛は未対応）。"
            ),
            "timepoints": [],
        }

    tps = timepoints(world0)
    timepoint_results: list[dict[str, Any]] = []
    any_reachable = False

    for day, slot in tps:
        world, subjects = state_at(project_dir, template_dir, (day, slot))
        subject = subjects[world.protagonist]
        baseline = plan(
            subject,
            world,
            holder_belief_fact=cfg["holder_belief_fact"],
            trial_reveal_facts=cfg["trial_reveal_facts"],
            min_win_prob=cfg["min_win_prob"],
        )
        start_summary = {
            "zone": subject.zone,
            "inventory": dict(subject.inventory),
            "knowledge": sorted(subject.knowledge),
        }
        if baseline["h"] in (None, INF):
            blocked = _blocked_on(subject, world, cfg["holder_belief_fact"], cfg["trial_reveal_facts"])
            blocked_entries = (
                [{"kind": blocked[0], "value": blocked[1], "detail": blocked[2], "text": _blocked_text(blocked)}]
                if blocked
                else []
            )
            timepoint_results.append(
                {
                    "label": _timepoint_label(day, slot, world),
                    "day": day,
                    "slot": slot,
                    "events": [
                        str(event.get("label") or event.get("id"))
                        for event in world.scheduled_for(day, slot)
                    ],
                    "start": start_summary,
                    "routes": [],
                    "blocked": blocked_entries,
                }
            )
            continue

        any_reachable = True
        entries = _enumerate_routes(project_dir, template_dir, (day, slot), cfg)
        routes = []
        for rank, entry in enumerate(entries, start=1):
            walked = walk(
                project_dir,
                template_dir,
                (day, slot),
                cfg,
                mutate=_compose(entry["chain"]),
            )
            conditions = [_knockout_condition_text(name) for name in entry["knockouts"]]
            # S2 (Opus review): entry["plan"]["h"] is route.py's own upfront
            # *estimate* (computed once, before any knockout-specific detour
            # like a danger-gate boost item is actually walked through step
            # by step) -- it can differ from the walked total (e.g. h=25 vs
            # an actually-completed walk of 16, once a danger gate resolves
            # earlier than the flat _BOOST_FALLBACK_COST estimate assumed).
            # The heading shows the walked, *actual* total when the walk
            # completed; h always ships too, labeled as an estimate.
            completed_cost = (
                walked["steps"][-1]["cumulative"]
                if walked["steps"] and not walked["truncated"]
                else None
            )
            if completed_cost is not None:
                label = f"#{rank} 所要 {completed_cost:.0f}"
            else:
                label = f"#{rank} 所要 {entry['plan']['h']:.0f}（目安）"
            routes.append(
                {
                    "rank": rank,
                    "label": label,
                    "h": entry["plan"]["h"],
                    "cost": completed_cost,
                    "route": entry["plan"]["route"],
                    "conditions": conditions,
                    "signature": list(entry["sig"]),
                    "steps": walked["steps"],
                    "truncated": walked["truncated"],
                }
            )

        timepoint_results.append(
            {
                "label": _timepoint_label(day, slot, world),
                "day": day,
                "slot": slot,
                "events": [
                    str(event.get("label") or event.get("id"))
                    for event in world.scheduled_for(day, slot)
                ],
                "start": start_summary,
                "routes": routes,
                "blocked": [],
            }
        )

    status = "ok" if any_reachable else "unreachable"
    message = "" if any_reachable else "結末に到達する段取りが見つかりません"
    return {"status": status, "message": message, "timepoints": timepoint_results}
