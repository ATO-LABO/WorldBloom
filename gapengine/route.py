"""WB-ROUTE-001 S0: a symbolic, rng-free "route to the ending" planner and a
per-candidate advance/prepare/detour annotator.

Design (route_design.md, agreed 2026-09-24; classifier revised 2026-09-24
after the design role reviewed report.md from the first probe run and found
it too strict to be usable as a measurement -- see the revision notes below
each affected function). The protagonist's decisions should mostly move
toward the ending by the shortest path; a detour needs a named cause
(obstacle/ignorance/motive/misperception/body) or gets recorded as "no
reason" (cause="none"). S0 only *measures* this -- weights are untouched,
see gapengine.policy.Policy.reweight's `route` hook.

Only the protagonist's own knowledge is used: world.truth is never read.
"Public" information the plan uses freely (per route_design.md §1.1): a
subject's current zone, ``world.holder(item)`` (whoever's inventory holds it
right now -- real game state, not narrative ground truth),
``relations.stance``, and ``engine.contest.believed_strength``. Consumes no
randomness and never mutates ``subject``/``world``.

The planner is a small backward-chaining search over the same primitives the
engine itself exposes (item ``made_from``/``sources``/``craft_zone``, route
``requires_item``, ``world.trials``, fact ``sources``, ``world.holder``) --
it is not hardcoded to momotaro_plus2's item names, only to its shape (one
knob, ``trial_reveal_facts``, is template-specific config -- see below).
Three recursive functions do the work:

- ``_acquire(item, ...)``: every reasonably-costed way to get ``item`` into
  the subject's own inventory (already-held / craft / investigate / a
  trial's grant / take it from whoever holds it -- fight or negotiate).
- ``_travel(subject, world, dest, ...)``: hop count to a zone, recursively
  acquiring whatever item unlocks a gated route along the way.
- ``_acquire_from_subject(...)``: fight vs. negotiate to take an item away
  from whoever holds it.

Each of these returns ``(h, tags)``: ``h`` is the *cheapest* option's cost
(an hour-count-ish number, approximately one engine action per unit), used
only to report overall progress (``plan()``'s ``h`` field) and to pick a
single "best route" label for the report. ``tags`` is the **union of every
option considered whose own cost was finite** -- not just the cheapest one.
This is a deliberate revision (2026-09-24): scoring only the single cheapest
branch meant, e.g., that once the protagonist's starting 勾玉 made
negotiating free, crafting 鉄砲 or building rapport with 鬼の弟 (both also
valid ways to get a negotiable offer) never earned a tag at all, and
genuinely useful companion-recruiting or reconnaissance candidates were
scored as unreasoned detours purely because a cheaper alternative existed.
Broadening to "every considered option" makes the tag set answer "is this a
sensible thing to try", not "is this the literal argmin".

Classifying one candidate action is then: does this action's own effect
match one of those tags? A match on an item/zone/fact actually needed by
some considered branch is "advance"; a match that only helps a
strength-boosting side path (recruiting a companion, training, or building
toward an alternate offer/route) is "prepare"; anything else is a "detour",
further split into a cause by a fixed priority order (body > ignorance >
belief > none). Recruiting a present, not-yet-allied, not-hostile companion
(``give_item``/``persuade``/``pledge``/``share_knowledge``, including idle
小話) is *always* "prepare" -- companionship raises ``engine.contest.
strength`` regardless of which route is currently cheapest, so it is a
generically sensible thing to do -- **unless** the item being given away is
itself something a considered plan still needs (handing over a needed
material is never credited).

``trial_reveal_facts`` (an optional ``route.yaml`` mapping of
``{giver_subject_id: fact_id}``): a trial giver who exists in the world data
but whom the subject has no in-fiction way to have heard of yet (e.g.
momotaro_plus2's 鬼の弟, only revealed by investigating 村 for 弟の消息)
should not be treated as a knowledge-free waypoint. When a giver is listed
here, ``_trial_cost`` folds in ``_learn_fact`` for the paired fact instead of
silently assuming the subject already knows to go looking for that person --
which also means investigating that fact's source zone now correctly tags
as advance/prepare (a "knows" leaf), rather than being misread as
"ignorance" (the old behavior, now reserved for a genuinely unreachable
plan: h==inf).

This trades a fully general effect simulator (which would need to either
mutate world/subject or duplicate every verb's -- some of them
rng-dependent -- engine.verbs logic) for a compact, deterministic model good
enough to *measure* S0's target: how often decisions already advance the
plan, and how often a detour has no named cause. See docs/route_s0_plan.md
§1.2-§1.3 for the original design and the S0 report for the concrete
approximations still in place (h-after is estimated as h-1 per advancing
action rather than recomputed exactly; a stance threshold is treated as a
flat +1 action once any lever exists, since the real effect size is
rng-dependent and the planner must never consume randomness).
"""

from __future__ import annotations

import math
from collections import deque
from pathlib import Path
from typing import Any, Mapping, Sequence, TYPE_CHECKING

import yaml

from engine.contest import believed_strength, strength

if TYPE_CHECKING:
    from engine.actions import Action
    from engine.subject import Subject
    from engine.world import World

INF = float("inf")
_MAX_FIGHT_ROUNDS = 30  # ponytail: cap so a near-zero win probability
# doesn't blow h up into a meaningless number; upgrade if S1 needs a truer
# expected-value estimate.
_STANCE_RAISE_COST = 1.0  # ponytail: flat one-action cost once *some* lever
# (a giftable item, or persuasion) exists -- exact effect sizes are
# rng-dependent (engine.verbs), so a precise cost isn't computable without
# simulating (forbidden: the planner must not consume randomness).


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def load_route_config(template_dir: str | Path) -> dict[str, Any] | None:
    """``templates/<genre>/route.yaml``, or None when the template has none
    (route stays disabled -- S0 default for every template but
    momotaro_plus2)."""

    path = Path(template_dir) / "route.yaml"
    if not path.is_file():
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        "delta": float(raw.get("delta", 0.02)),
        "holder_belief_fact": raw.get("holder_belief_fact"),
        "trial_reveal_facts": {
            str(giver): str(fact_id)
            for giver, fact_id in (raw.get("trial_reveal_facts") or {}).items()
        },
    }


class Route:
    """S0: read-only planner/annotator, duck-typed into
    ``gapengine.policy.Policy`` via its ``route`` kwarg (policy.py never
    imports this module -- same pattern as ``rationality``)."""

    def __init__(
        self,
        *,
        delta: float = 0.02,
        holder_belief_fact: str | None = None,
        trial_reveal_facts: Mapping[str, str] | None = None,
    ) -> None:
        self.delta = float(delta)
        self.holder_belief_fact = holder_belief_fact
        self.trial_reveal_facts = dict(trial_reveal_facts or {})

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "Route":
        return cls(
            delta=float(cfg.get("delta", 0.02)),
            holder_belief_fact=cfg.get("holder_belief_fact"),
            trial_reveal_facts=cfg.get("trial_reveal_facts"),
        )

    def annotate(
        self,
        subject: Subject,
        world: World,
        present: Sequence[Subject],
        actions: Sequence[Action],
    ) -> list[dict[str, Any]]:
        return annotate(
            subject,
            world,
            present,
            actions,
            holder_belief_fact=self.holder_belief_fact,
            trial_reveal_facts=self.trial_reveal_facts,
        )


# ---------------------------------------------------------------------------
# Zone travel (BFS ignoring item gating, then a recursive item unlock)
# ---------------------------------------------------------------------------


def _shortest_route_path(
    world: World,
    origin: str,
    dest: str,
) -> list[Any] | None:
    """Shortest route sequence by hop count, ignoring ``requires_item``
    gating and stamina (those are handled by the caller). None when the two
    zones aren't topologically connected at all."""

    if origin == dest:
        return []

    prev: dict[str, tuple[str, Any] | None] = {origin: None}
    queue: deque[str] = deque([origin])
    while queue:
        zone = queue.popleft()
        if zone == dest:
            break
        for route in world.routes.get(zone, ()):
            if route.destination in prev:
                continue
            prev[route.destination] = (zone, route)
            queue.append(route.destination)

    if dest not in prev:
        return None
    edges: list[Any] = []
    cursor = dest
    while cursor != origin:
        zone, route = prev[cursor]
        edges.append(route)
        cursor = zone
    edges.reverse()
    return edges


def _travel(
    subject: Subject,
    world: World,
    dest: str,
    visiting: frozenset[Any],
    trial_reveal_facts: Mapping[str, str],
) -> tuple[float, frozenset[tuple[str, Any]]]:
    if subject.zone == dest:
        return 0.0, frozenset()
    edges = _shortest_route_path(world, subject.zone, dest)
    if edges is None:
        return INF, frozenset()

    # Every hop along the shortest path is itself an advancing move, not
    # only the final destination -- otherwise a multi-hop trip would only
    # ever credit its last leg (Claude-side fix, found via the first S0
    # probe run: without this the measured advance rate was ~12%, since
    # most "move toward the goal" candidates target an intermediate zone).
    tags: set[tuple[str, Any]] = {("zone", route.destination) for route in edges}
    extra = 0.0
    for route in edges:
        item = route.requires_item
        if item is None or subject.has_item(item):
            continue
        item_h, item_tags = _acquire(item, subject, world, visiting, trial_reveal_facts)
        if item_h == INF:
            return INF, frozenset()
        extra += item_h
        tags |= item_tags
    return float(len(edges)) + extra, frozenset(tags)


# ---------------------------------------------------------------------------
# Item acquisition (craft / investigate / trial / take-from-a-subject)
# ---------------------------------------------------------------------------


def _learn_fact(
    fact_id: str,
    subject: Subject,
    world: World,
    visiting: frozenset[Any],
    trial_reveal_facts: Mapping[str, str],
) -> tuple[float, frozenset[tuple[str, Any]]]:
    if fact_id in subject.knowledge:
        return 0.0, frozenset()
    key = ("fact", fact_id)
    if key in visiting:
        return INF, frozenset()
    visiting = visiting | {key}

    options: list[tuple[float, frozenset[tuple[str, Any]]]] = []
    for source in world.facts.get(fact_id, {}).get("sources", []) or []:
        if source.get("type") != "investigate":
            continue
        zone = source.get("zone")
        agent = source.get("agent")
        if zone is not None:
            travel_zone = str(zone)
        elif agent is not None:
            agent_subject = world.subjects.get(str(agent))
            if agent_subject is None:
                continue
            travel_zone = agent_subject.zone
        else:
            continue
        travel_h, travel_tags = _travel(subject, world, travel_zone, visiting, trial_reveal_facts)
        if travel_h == INF:
            continue
        options.append(
            (travel_h + 1.0, frozenset({("knows", fact_id)}) | travel_tags)
        )

    return _best_and_union(options)


def _best_and_union(
    options: list[tuple[float, frozenset[tuple[str, Any]]]],
) -> tuple[float, frozenset[tuple[str, Any]]]:
    """h = the cheapest option; tags = the union of every option's tags
    (2026-09-24 revision -- see module docstring)."""

    if not options:
        return INF, frozenset()
    best_h = min(h for h, _ in options)
    all_tags: frozenset[tuple[str, Any]] = frozenset()
    for _, tags in options:
        all_tags |= tags
    return best_h, all_tags


def _acquire(
    item: str,
    subject: Subject,
    world: World,
    visiting: frozenset[Any],
    trial_reveal_facts: Mapping[str, str],
) -> tuple[float, frozenset[tuple[str, Any]]]:
    if subject.has_item(item):
        return 0.0, frozenset()
    key = ("item", item)
    if key in visiting:
        return INF, frozenset()
    visiting = visiting | {key}

    options: list[tuple[float, frozenset[tuple[str, Any]]]] = []
    definition = world.items.get(item, {})

    # 1. craft
    if item in world.recipes:
        needed_fact = (definition.get("requires") or {}).get("knowledge")
        fact_h, fact_tags = (0.0, frozenset())
        fact_ok = True
        if needed_fact is not None and needed_fact not in subject.knowledge:
            fact_h, fact_tags = _learn_fact(
                str(needed_fact), subject, world, visiting, trial_reveal_facts
            )
            fact_ok = fact_h != INF
        if fact_ok:
            material_h = 0.0
            material_tags: set[tuple[str, Any]] = set()
            materials_ok = True
            for material, qty in sorted(world.recipes[item].items()):
                if subject.inventory.get(material, 0) >= qty:
                    continue
                mh, mtags = _acquire(material, subject, world, visiting, trial_reveal_facts)
                if mh == INF:
                    materials_ok = False
                    break
                material_h += mh
                material_tags |= mtags
                material_tags.add(("has_item", material))
            if materials_ok:
                craft_zone = definition.get("craft_zone")
                travel_h, travel_tags = (
                    (0.0, frozenset())
                    if craft_zone is None
                    else _travel(subject, world, str(craft_zone), visiting, trial_reveal_facts)
                )
                if travel_h != INF:
                    tags = (
                        frozenset({("has_item", item)})
                        | fact_tags
                        | frozenset(material_tags)
                        | travel_tags
                    )
                    options.append((fact_h + material_h + travel_h + 1.0, tags))

    # 2. investigate a source zone
    for source in definition.get("sources", []) or []:
        if source.get("type") != "investigate" or not source.get("zone"):
            continue
        travel_h, travel_tags = _travel(
            subject, world, str(source["zone"]), visiting, trial_reveal_facts
        )
        if travel_h == INF:
            continue
        options.append(
            (travel_h + 1.0, frozenset({("has_item", item)}) | travel_tags)
        )

    # 3. a trial's grant
    for trial in world.trials:
        grants = trial.get("grants") or {}
        if str(grants.get("item", "")) != item:
            continue
        trial_h, trial_tags = _trial_cost(trial, subject, world, visiting, trial_reveal_facts)
        if trial_h != INF:
            options.append((trial_h, trial_tags | frozenset({("has_item", item)})))

    # 4. take it from whoever currently holds it (fight or negotiate)
    holder_id = world.holder(item)
    if holder_id is not None and holder_id != subject.id and holder_id in world.subjects:
        take_h, take_tags = _acquire_from_subject(
            item, world.subjects[holder_id], subject, world, visiting, trial_reveal_facts
        )
        if take_h != INF:
            options.append((take_h, take_tags | frozenset({("has_item", item)})))

    return _best_and_union(options)


def _trial_cost(
    trial: dict[str, Any],
    subject: Subject,
    world: World,
    visiting: frozenset[Any],
    trial_reveal_facts: Mapping[str, str],
) -> tuple[float, frozenset[tuple[str, Any]]]:
    giver_id = str(trial.get("giver", ""))
    giver = world.subjects.get(giver_id)
    if giver is None or giver_id == subject.id:
        return INF, frozenset()

    tags: set[tuple[str, Any]] = set()
    extra = 0.0

    # A giver the subject has no in-fiction way to know about yet is not a
    # free waypoint -- fold in learning the revealing fact (2026-09-24
    # addendum requested after the design review: momotaro_plus2's 鬼の弟
    # must not look reachable before 村 is investigated for 弟の消息).
    reveal_fact = trial_reveal_facts.get(giver_id)
    if reveal_fact is not None and reveal_fact not in subject.knowledge:
        fact_h, fact_tags = _learn_fact(reveal_fact, subject, world, visiting, trial_reveal_facts)
        if fact_h == INF:
            return INF, frozenset()
        extra += fact_h
        tags |= fact_tags

    travel_h, travel_tags = _travel(subject, world, giver.zone, visiting, trial_reveal_facts)
    if travel_h == INF:
        return INF, frozenset()
    tags |= travel_tags

    requires = trial.get("requires") or {}
    threshold = float(requires.get("stance", -1.0))
    if world.relations.stance(giver_id, subject.id) < threshold:
        tags.add(("stance_ge", giver_id))
        extra += _STANCE_RAISE_COST

    required_item = requires.get("item")
    if required_item is not None:
        item_h, item_tags = _acquire(str(required_item), subject, world, visiting, trial_reveal_facts)
        if item_h == INF:
            return INF, frozenset()
        extra += item_h
        tags |= item_tags

    return travel_h + extra + 1.0, frozenset(tags)


def _best_offer(
    holder: Subject,
    subject: Subject,
    world: World,
    visiting: frozenset[Any],
    trial_reveal_facts: Mapping[str, str],
) -> tuple[float, frozenset[tuple[str, Any]]]:
    """Every lootable+modifier item the subject could acquire and hand over
    to make a negotiate attractive (mirrors ``engine.actions.
    _concede_candidates``'s "attractive" test) -- h is the cheapest one's
    cost, tags is the union across all of them (2026-09-24 revision: so
    crafting 鉄砲 or earning 弟の手紙 still tag as prepare/advance even when
    momotaro already holds a free offer, 勾玉, from the start)."""

    options: list[tuple[float, frozenset[tuple[str, Any]]]] = []
    for name, definition in sorted(world.items.items()):
        if not definition.get("lootable") or not definition.get("modifier"):
            continue
        if holder.has_item(name):
            continue
        item_h, item_tags = _acquire(name, subject, world, visiting, trial_reveal_facts)
        if item_h != INF:
            options.append((item_h, item_tags | frozenset({("has_item", name)})))
    return _best_and_union(options)


def _acquire_from_subject(
    item: str,
    holder: Subject,
    subject: Subject,
    world: World,
    visiting: frozenset[Any],
    trial_reveal_facts: Mapping[str, str],
) -> tuple[float, frozenset[tuple[str, Any]]]:
    key = ("subject", holder.id, item)
    if key in visiting:
        return INF, frozenset()
    visiting = visiting | {key}

    travel_h, travel_tags = _travel(subject, world, holder.zone, visiting, trial_reveal_facts)
    if travel_h == INF:
        return INF, frozenset()

    options: list[tuple[float, frozenset[tuple[str, Any]]]] = []

    if "fight" in subject.verbs and world.target_role(subject, holder) != "ally":
        present_here = world.present_subjects(subject.zone)
        mine = strength(subject, world, present_here)
        theirs = believed_strength(subject, holder, world, present_here)
        scaled = max(-700.0, min(700.0, (mine - theirs) / world.contest["tau"]))
        probability = 1.0 / (1.0 + math.exp(-scaled))
        rounds = (
            INF
            if probability <= 0.0
            else float(math.ceil(min(_MAX_FIGHT_ROUNDS, 1.0 / probability)))
        )
        if rounds != INF:
            tags = set(travel_tags) | {("win_fight", holder.id), ("route", "fight")}
            if probability < 0.9:
                tags.add(("boost_fight", holder.id))
            options.append((travel_h + rounds, frozenset(tags)))

    if "negotiate" in subject.verbs:
        tags = set(travel_tags) | {("route", "negotiate")}
        extra = 0.0
        if world.relations.stance(holder.id, subject.id) < world.negotiate_threshold:
            offer_h, offer_tags = _best_offer(holder, subject, world, visiting, trial_reveal_facts)
            if offer_h != INF:
                extra += offer_h
                tags |= offer_tags
            else:
                tags.add(("stance_ge", holder.id))
                extra += _STANCE_RAISE_COST
        options.append((travel_h + extra + 1.0, frozenset(tags)))

    return _best_and_union(options)


# ---------------------------------------------------------------------------
# Top-level plan
# ---------------------------------------------------------------------------


def _believed_holder(
    subject: Subject,
    world: World,
    target: str,
    holder_belief_fact: str | None,
) -> str | None:
    true_holder = world.holder(target)
    if holder_belief_fact is None:
        return true_holder
    belief = subject.beliefs.get(holder_belief_fact)
    if belief is None:
        return true_holder
    definition = world.facts.get(holder_belief_fact, {})
    threshold = float(definition.get("act_threshold", 0.6))
    if belief.confidence < threshold:
        return true_holder
    if belief.value == true_holder or belief.value not in world.subjects:
        return true_holder
    return belief.value  # a confident misattribution (誤認)


def plan(
    subject: Subject,
    world: World,
    *,
    holder_belief_fact: str | None = None,
    trial_reveal_facts: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """The current best route to the ending, from ``subject``'s own
    knowledge. Read-only, rng-free. See the module docstring for the model.

    ``tags`` is the union of every considered (finite-cost) option, not only
    the cheapest one -- see ``_best_and_union``. ``route`` names the single
    cheapest branch only (for the report's "plan" column), and is not used
    to gate classification any more (2026-09-24 revision)."""

    reveal_facts = trial_reveal_facts or {}
    target = subject.goal.target
    if target is None:
        return {"h": None, "believed_holder": None, "tags": frozenset(), "route": None}

    if subject.has_item(target):
        deliver_to = subject.goal.deliver_to
        if deliver_to is None:
            return {"h": 0.0, "believed_holder": subject.id, "tags": frozenset(), "route": None}
        h, tags = _travel(subject, world, deliver_to, frozenset(), reveal_facts)
        return {"h": h, "believed_holder": subject.id, "tags": tags, "route": None}

    believed_holder_id = _believed_holder(subject, world, target, holder_belief_fact)
    if believed_holder_id is None or believed_holder_id not in world.subjects:
        return {"h": None, "believed_holder": believed_holder_id, "tags": frozenset(), "route": None}

    holder_subject = world.subjects[believed_holder_id]
    acquire_h, acquire_tags = _acquire_from_subject(
        target, holder_subject, subject, world, frozenset({("item", target)}), reveal_facts
    )
    if acquire_h == INF:
        return {"h": INF, "believed_holder": believed_holder_id, "tags": frozenset(), "route": None}

    deliver_h, deliver_tags = (0.0, frozenset())
    if subject.goal.deliver_to is not None:
        deliver_h, deliver_tags = _travel(
            subject, world, subject.goal.deliver_to, frozenset(), reveal_facts
        )
        if deliver_h == INF:
            return {"h": INF, "believed_holder": believed_holder_id, "tags": frozenset(), "route": None}

    # The single cheapest acquisition branch's own route label, purely for
    # the report -- recomputed narrowly (not from the broadened union) so it
    # names one concrete route rather than "fight,negotiate" ambiguously.
    route_name = _cheapest_route_name(
        target, holder_subject, subject, world, frozenset({("item", target)}), reveal_facts
    )

    return {
        "h": acquire_h + deliver_h,
        "believed_holder": believed_holder_id,
        "tags": acquire_tags | deliver_tags,
        "route": route_name,
    }


def _cheapest_route_name(
    item: str,
    holder: Subject,
    subject: Subject,
    world: World,
    visiting: frozenset[Any],
    trial_reveal_facts: Mapping[str, str],
) -> str | None:
    travel_h, travel_tags = _travel(subject, world, holder.zone, visiting, trial_reveal_facts)
    if travel_h == INF:
        return None
    candidates: list[tuple[float, str]] = []
    if "fight" in subject.verbs and world.target_role(subject, holder) != "ally":
        present_here = world.present_subjects(subject.zone)
        mine = strength(subject, world, present_here)
        theirs = believed_strength(subject, holder, world, present_here)
        scaled = max(-700.0, min(700.0, (mine - theirs) / world.contest["tau"]))
        probability = 1.0 / (1.0 + math.exp(-scaled))
        if probability > 0.0:
            rounds = float(math.ceil(min(_MAX_FIGHT_ROUNDS, 1.0 / probability)))
            candidates.append((travel_h + rounds, "fight"))
    if "negotiate" in subject.verbs:
        extra = 0.0
        if world.relations.stance(holder.id, subject.id) < world.negotiate_threshold:
            offer_h, _ = _best_offer(holder, subject, world, visiting, trial_reveal_facts)
            extra = offer_h if offer_h != INF else _STANCE_RAISE_COST
        candidates.append((travel_h + extra + 1.0, "negotiate"))
    if not candidates:
        return None
    return min(candidates, key=lambda pair: pair[0])[1]


# ---------------------------------------------------------------------------
# Per-candidate classification
# ---------------------------------------------------------------------------


def _sourced_at_zone(item: str, world: World, zone: str) -> bool:
    for source in world.items.get(item, {}).get("sources", []) or []:
        if source.get("type") == "investigate" and source.get("zone") == zone:
            return True
    return False


def _fact_sourced_here(
    fact_id: str, world: World, zone: str, present_ids: set[str]
) -> bool:
    for source in world.facts.get(fact_id, {}).get("sources", []) or []:
        if source.get("type") != "investigate":
            continue
        if source.get("zone") == zone:
            return True
        agent = source.get("agent")
        if agent is not None and str(agent) in present_ids:
            return True
    return False


def _is_companion_candidate(subject: Subject, target: Subject, world: World) -> bool:
    """A present, living peer who isn't hostile and isn't already an ally --
    a plausible companion-recruiting target (companionship's own direction:
    the *peer's* stance toward self, matching ``Subject.derived_modifiers``
    /``_movement_candidates``'s companion checks elsewhere in the engine)."""

    if target.id == subject.id or target.vitality == "dead":
        return False
    if world.target_role(subject, target) == "hostile":
        return False
    already_ally = (
        world.relations.stance(target.id, subject.id)
        >= world.companionship["threshold"]
    )
    return not already_ally


def _match_advance_or_prepare(
    action: Action,
    subject: Subject,
    world: World,
    present: Sequence[Subject],
    present_ids: set[str],
    tags: frozenset[tuple[str, Any]],
    believed_holder_id: str | None,
    holder_is_true: bool,
) -> str | None:
    verb = action.verb
    tag_kinds: dict[str, set[Any]] = {}
    for kind, value in tags:
        tag_kinds.setdefault(kind, set()).add(value)

    if verb == "move":
        dest = action.meta.get("dest")
        if dest in tag_kinds.get("zone", ()):
            return "advance"
        return None

    if verb == "investigate":
        zone = subject.zone
        if action.meta.get("gather") and any(
            _sourced_at_zone(item, world, zone) for item in tag_kinds.get("has_item", ())
        ):
            return "advance"
        if any(
            _fact_sourced_here(fact_id, world, zone, present_ids)
            for fact_id in tag_kinds.get("knows", ())
        ):
            return "advance"
        return None

    if verb == "craft":
        item = action.args[0] if action.args else None
        if item in tag_kinds.get("has_item", ()):
            return "advance"
        return None

    if verb == "trial":
        trial_id = action.meta.get("trial_id")
        trial = next(
            (t for t in world.trials if str(t.get("id")) == str(trial_id)), None
        )
        grants = (trial or {}).get("grants") or {}
        if grants.get("item") in tag_kinds.get("has_item", ()):
            return "advance"
        if grants.get("fact") in tag_kinds.get("knows", ()):
            return "advance"
        return None

    if verb == "negotiate":
        target = action.meta.get("target")
        if (
            "negotiate" in tag_kinds.get("route", ())
            and target == believed_holder_id
            and holder_is_true
        ):
            return "advance"
        return None

    if verb == "fight":
        target = action.args[0] if action.args else None
        if (
            "fight" in tag_kinds.get("route", ())
            and target == believed_holder_id
            and holder_is_true
        ):
            return "advance"
        return None

    if verb == "train":
        if tag_kinds.get("boost_fight"):
            return "prepare"
        return None

    if verb in ("give_item", "persuade", "pledge", "share_knowledge"):
        target_id = action.meta.get("target")
        if verb == "give_item":
            # Handing over something a considered plan still needs is never
            # credited, even to a companion candidate (2026-09-24 addendum:
            # e.g. giving 鬼の弟 the 木材 the ship still needs stays a
            # detour).
            item = action.meta.get("item")
            if item in tag_kinds.get("has_item", ()):
                return None
        if target_id in tag_kinds.get("stance_ge", ()):
            return "advance" if "negotiate" in tag_kinds.get("route", ()) else "prepare"
        target = next((peer for peer in present if peer.id == target_id), None)
        if target is not None and _is_companion_candidate(subject, target, world):
            return "prepare"
        return None

    return None


def _detour_cause(
    action: Action,
    subject: Subject,
    world: World,
    believed_holder_id: str | None,
    h: float | None,
) -> str:
    verb = action.verb

    if verb in ("rest", "withdraw"):
        exhausted_ratio = world.stamina["exhausted_ratio"]
        ratio = subject.stamina / subject.stamina_max if subject.stamina_max else 0.0
        if (
            subject.exhausted
            or subject.vitality == "downed"
            or ratio <= exhausted_ratio
            or bool(action.meta.get("under_threat"))
        ):
            return "body"

    if verb in ("investigate", "observe") and (h is None or h == INF):
        return "ignorance"

    target = action.meta.get("target")
    if target is None and action.args and isinstance(action.args[0], str):
        target = action.args[0]
    if (
        verb in ("fight", "negotiate", "sabotage", "mislead")
        and believed_holder_id is not None
        and target == believed_holder_id
        and believed_holder_id != world.holder(subject.goal.target)
    ):
        return "belief"

    return "none"


# ---------------------------------------------------------------------------
# Text (a short, milestone-chained Japanese sentence -- S3 will hand this to
# scenes/synopsis prompts; S0 only records it)
# ---------------------------------------------------------------------------


def _trial_for_giver(world: World, giver_id: str) -> dict[str, Any] | None:
    return next((t for t in world.trials if str(t.get("giver")) == giver_id), None)


def _grant_purpose_text(trial: dict[str, Any] | None) -> str | None:
    grants = (trial or {}).get("grants") or {}
    if grants.get("item"):
        return f"{grants['item']}を得るため"
    if grants.get("fact"):
        return f"{grants['fact']}を知るため"
    return None


_RELATION_VERB_TEXT = {
    "share_knowledge": "と話した",
    "give_item": "に品を渡した",
    "persuade": "を説得した",
    "pledge": "と誓いを結んだ",
}


def _advance_text(
    action: Action, subject: Subject, world: World, tag_kinds: dict[str, set[Any]]
) -> str:
    verb = action.verb

    if verb == "move":
        dest = action.meta.get("dest")
        for item in sorted(tag_kinds.get("has_item", ())):
            definition = world.items.get(item, {})
            if str(definition.get("craft_zone")) == dest:
                return f"{item}を作るため{dest}へ向かった"
        believed_holder = tag_kinds.get("win_fight") or tag_kinds.get("stance_ge")
        if believed_holder and any(
            world.subjects.get(holder_id, subject).zone == dest
            for holder_id in believed_holder
            if isinstance(holder_id, str)
        ):
            return f"{dest}へ向かった"
        return f"{dest}へ移動して近づいた"

    if verb == "investigate":
        zone = subject.zone
        for item in sorted(tag_kinds.get("has_item", ())):
            if not _sourced_at_zone(item, world, zone):
                continue
            product = next(
                (
                    name
                    for name, materials in world.recipes.items()
                    if item in materials and name in tag_kinds.get("has_item", ())
                ),
                None,
            )
            return f"{product}のために{item}を集めた" if product else f"{item}を集めた"
        for fact_id in sorted(tag_kinds.get("knows", ())):
            if _fact_sourced_here(fact_id, world, zone, set()):
                return f"{fact_id}について調べた"
        return "必要なものを調べた"

    if verb == "craft":
        item = action.args[0] if action.args else "品"
        return f"{item}を作った"

    if verb == "trial":
        trial_id = action.meta.get("trial_id")
        trial = next((t for t in world.trials if str(t.get("id")) == str(trial_id)), None)
        purpose = _grant_purpose_text(trial)
        giver = str(trial.get("giver")) if trial else "相手"
        return f"{purpose}{giver}の試練に挑んだ" if purpose else f"{giver}の試練に挑んだ"

    if verb == "negotiate":
        return "目的物を譲るよう交渉した"

    if verb == "fight":
        return "目的物の持ち主と戦った"

    if verb == "train":
        return "戦いに備えて力をつけるため鍛えた"

    if verb in _RELATION_VERB_TEXT:
        target_id = action.meta.get("target") or (action.args[0] if action.args else "相手")
        if target_id in tag_kinds.get("stance_ge", ()):
            trial = _trial_for_giver(world, str(target_id))
            purpose = _grant_purpose_text(trial)
            if purpose:
                return f"{purpose}{target_id}と親しくなった"
        return f"仲間を増やすため{target_id}{_RELATION_VERB_TEXT[verb]}"

    return f"{verb}で先へ進んだ"


def _detour_text(cause: str) -> str:
    if cause == "body":
        return "疲労や危険のため一旦引いた"
    if cause == "ignorance":
        return "次の手が分からず調べた"
    if cause == "belief":
        return "誤った思い込みに基づいて動いた"
    return "特に理由のない寄り道"


def annotate(
    subject: Subject,
    world: World,
    present: Sequence[Subject],
    actions: Sequence[Action],
    *,
    holder_belief_fact: str | None = None,
    trial_reveal_facts: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """One annotation dict per action in ``actions``, in order. See the
    module docstring / route_s0_plan.md §1.3 for the schema. Adds a few
    reporting-only fields (``zone``/``inventory``/``allies``) beyond the
    original plan so scripts/route_probe.py's report doesn't need to
    reconstruct per-turn state from partial deltas."""

    state = plan(
        subject,
        world,
        holder_belief_fact=holder_belief_fact,
        trial_reveal_facts=trial_reveal_facts,
    )
    h_before = state["h"]
    tags = state["tags"]
    believed_holder_id = state["believed_holder"]
    route_name = state["route"]
    present_ids = {peer.id for peer in present}
    true_holder_id = (
        world.holder(subject.goal.target) if subject.goal.target is not None else None
    )
    holder_is_true = believed_holder_id == true_holder_id

    tag_kinds: dict[str, set[Any]] = {}
    for kind, value in tags:
        tag_kinds.setdefault(kind, set()).add(value)

    zone = subject.zone
    inventory = sorted(item for item, count in subject.inventory.items() if count > 0)
    allies = sorted(
        peer.id
        for peer in present
        if peer.id != subject.id
        and world.relations.stance(peer.id, subject.id) >= world.companionship["threshold"]
    )

    results: list[dict[str, Any]] = []
    for action in actions:
        matched = _match_advance_or_prepare(
            action,
            subject,
            world,
            present,
            present_ids,
            tags,
            believed_holder_id,
            holder_is_true,
        )
        base = {
            "plan": route_name,
            "milestone": _milestone(tags),
            "zone": zone,
            "inventory": inventory,
            "allies": allies,
        }
        if matched is not None:
            h_after = (
                None
                if h_before is None or h_before == INF
                else round(max(0.0, h_before - 1.0), 6)
            )
            results.append(
                {
                    **base,
                    "kind": matched,
                    "cause": None,
                    "h": [_finite_or_none(h_before), h_after],
                    "text": _advance_text(action, subject, world, tag_kinds),
                }
            )
            continue

        cause = _detour_cause(action, subject, world, believed_holder_id, h_before)
        results.append(
            {
                **base,
                "kind": "detour",
                "cause": cause,
                "h": [_finite_or_none(h_before), _finite_or_none(h_before)],
                "text": _detour_text(cause),
            }
        )
    return results


def _finite_or_none(value: float | None) -> float | None:
    if value is None or value == INF:
        return None
    return round(value, 6)


def _milestone(tags: frozenset[tuple[str, Any]]) -> str | None:
    pieces = sorted(f"{kind}:{value}" for kind, value in tags if kind != "route")
    return "、".join(pieces) if pieces else None
