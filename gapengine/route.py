"""WB-ROUTE-001 S0: a symbolic, rng-free "route to the ending" planner and a
per-candidate advance/prepare/detour annotator.

Design: Notion "WorldBloom(StorySim×GA)" hub, 設計子ページ
https://app.notion.com/p/3e5e21ef1cac81218cb0c01e4a60446d (agreed
2026-09-24). The protagonist's decisions should mostly move toward the
ending by the shortest path; a detour needs a named cause (obstacle/
ignorance/motive/misperception/body) or gets recorded as "no reason"
(cause="none"). S0 only *measures* this -- weights are untouched, see
gapengine.policy.Policy.reweight's `route` hook.

Only the protagonist's own knowledge is used: world.truth is never read.
"Public" information the plan uses freely: a subject's current zone,
``world.holder(item)`` (whoever's inventory holds it right now -- real game
state, not narrative ground truth), ``relations.stance``, and
``engine.contest.believed_strength``. An item's *possession* by another
subject is only used when it would actually be observable -- a modifier
that isn't ``visible`` and that the protagonist hasn't separately learned of
(``beliefs_about[holder].known_modifiers``) is not read (``_holder_appears_to_have``;
2026-09-24 review fix 8: momotaro_plus2's 金棒 is such a modifier). Consumes
no randomness and never mutates ``subject``/``world``.

The planner is a small backward-chaining search over the same primitives the
engine itself exposes (item ``made_from``/``sources``/``craft_zone``, route
``requires_item``, ``world.trials``, fact ``sources``, ``world.holder``) --
it is not hardcoded to momotaro_plus2's item names, only to its shape (one
knob, ``trial_reveal_facts``, is template-specific config -- see below).
Three recursive functions do the work, each returning ``(h, best, alt)``:

- ``h``: the cheapest considered option's cost (an hour-count-ish number,
  approximately one engine action per unit).
- ``best``: the tag set describing exactly the *winning* (cheapest)
  composition -- an item/zone/fact/stance/fight-win a candidate action must
  match to count as "advance".
- ``alt``: every tag that appeared anywhere in a *non-winning* option (a
  more expensive way to get the same item, an alternate route, a losing
  offer candidate) but not in ``best`` -- what a candidate action must match
  to count as "prepare" (a sensible but non-optimal thing to try).

2026-09-24 review fix 2 (Opus, 1st pass, required): the first revision
unioned *every* considered option into one tag set and matched "advance"
against the whole thing, which is too permissive -- e.g. once the starting
勾玉 made negotiating free, persuading 鬼の弟 (an unused alternative offer)
still came back "advance". Splitting into best/alt is the fix: an action
only advances the plan if it matches the plan actually being pursued.

- ``_acquire(item, ...)``: every reasonably-costed way to get ``item`` into
  the subject's own inventory (already-held / craft / investigate / a
  trial's grant / take it from whoever holds it -- fight or negotiate).
- ``_travel(subject, world, dest, ..., origin=None)``: hop count from
  ``origin`` (default: ``subject.zone``) to ``dest``, recursively acquiring
  whatever item unlocks a gated route along the way. ``origin`` matters for
  ``plan()``'s delivery leg (fix 1, below).
- ``_acquire_from_subject(...)``: fight vs. negotiate to take an item away
  from whoever holds it.

2026-09-24 review fix 1 (required): the delivery leg (after acquiring the
goal item, travel to ``deliver_to``) was computed from the subject's
*current* zone even while still planning the acquisition -- so, e.g.,
retreating from 海 back toward 村 before ever reaching 鬼ヶ島 registered as
"advance" (it happens to be steps on the eventual homeward path) and made h
non-monotonic. ``plan()`` now computes the delivery leg's ``_travel`` with
``origin`` set to the believed holder's zone (where the subject will
actually be once the item is acquired), not the subject's current zone.

Classifying one candidate action is: does this action's own effect match a
``best`` tag ("advance") or an ``alt`` tag / one of two route-independent
side benefits ("prepare")? The two side benefits: recruiting a present,
not-yet-allied, not-hostile companion (``give_item``/``persuade``/
``pledge``/``share_knowledge``, including idle 雑談, and ``rescue``ing a
downed one -- fix, recommended 1) always helps ``engine.contest.strength``
regardless of which route is currently cheapest; ``train`` and any
``alt``/``best`` ``boost_fight`` tag likewise. Handing over an item a
considered plan still needs is never credited, even to a companion
candidate (2026-09-24 review fix 4: this now checks whether giving away one
unit would drop the subject below what *any* still-relevant recipe or
trial requires -- not just whether the planner currently has an open
"still missing" tag for it, which under-protected an exactly-sufficient
stock, e.g. holding precisely the 2 木材 船 needs).

Anything that matches neither is a "detour", split into a cause by a fixed
priority order (body > ignorance > belief > none). ``observe``ing the
believed holder, or an ``investigate``/``observe`` whose plan is entirely
unreachable (h==inf), is "ignorance" (recommended 1). Acting on a confident
misattributed belief about who holds the goal item (fight/negotiate/
sabotage/mislead against, or *moving toward*, the believed-but-wrong
holder's zone -- recommended 4) is "belief". This template's own numbers
never actually exercise "belief": momotaro_plus2's only misattribution
lever, road_gossip, tops out at confidence 0.4, below treasure_thief's
act_threshold 0.5, so the belief branch is provably unreachable here (记录
のみ per the design review -- a world-content question for S2+, not fixed
in S0).

``trial_reveal_facts`` (an optional ``route.yaml`` mapping of
``{giver_subject_id: fact_id}``): a trial giver who exists in the world data
but whom the subject has no in-fiction way to have heard of yet (e.g.
momotaro_plus2's 鬼の弟, only revealed by investigating 村 for 弟の消息)
should not be treated as a knowledge-free waypoint. When a giver is listed
here, ``_trial_cost`` folds in ``_learn_fact`` for the paired fact instead of
silently assuming the subject already knows to go looking for that person.

This trades a fully general effect simulator (which would need to either
mutate world/subject or duplicate every verb's -- some of them
rng-dependent -- engine.verbs logic) for a compact, deterministic model good
enough to *measure* S0's target: how often decisions already advance the
plan, and how often a detour has no named cause. Known approximations still
in place: h-after is estimated as h-1 for an advancing action only
(recommended 2: prepare/detour keep h_before unchanged, since neither
literally completes a node on the winning plan); a stance threshold is
treated as a flat +1 action once any lever exists, since the real effect
size is rng-dependent and the planner must never consume randomness;
``milestone`` names a single next best-branch node by a fixed priority
order, not every open need (recommended 3).

2026-09-24 review round 2 (Opus, e3b42ea, condition pass -- 83.3% agreement
against an 80% bar, but with concrete required/design/recommended items):
required fix A -- ``_acquire``'s early-return now compares against a
``needed`` quantity (threaded from the craft branch's own recipe amount)
instead of "holds at least 1", so being short by any amount still counts as
a live need; required fix B -- ``_acquire_from_subject`` only ever builds a
"negotiate" option when ``is_objective=True`` (only the top-level goal
item passed by ``plan()``; everything ``_acquire`` fetches recursively for
itself is fight-only), matching the real engine's ``_negotiate_candidates``
(objective-only), and ``_travel``/``_shortest_route_path`` now respect the
subject's ``range.exclude`` (momotaro can't re-enter 村 before holding the
treasure) via an ``excluded`` zone set; design decision C (confirmed by the
design role) -- ``_best_offer`` drops every alt entirely when the winning
offer already costs 0 (a free offer in hand), so fetching or building a
*second*, unneeded one is a plain detour, not "prepare"; recommended fix 1
-- the delivery leg's ``_travel`` call takes ``excluded=frozenset()`` (the
exclusion will already be lifted by delivery time) and ``assume_held`` for
any persistent (``vehicle``) item the acquire phase picked up, so the trip
home doesn't silently re-plan building a second 船; recommended fix 2 --
``_would_break_requirement`` only protects a recipe/trial that actually
appears somewhere in the live best/alt plan, so an irrelevant trial
elsewhere in the world file (猿's 猿の知恵) can't permanently lock the last
unit of an item (きびだんご) needed for something else entirely; recommended
fix 3 -- ``_acquire``'s take-from-a-subject branch now also gates on
``_holder_appears_to_have`` before even considering fighting for an
invisible-modifier item; recommended fix 5 -- a move's fallback text now
names a still-needed item/fact sourced at the destination before falling
back to a bare "近づいた".

Recommended fix 4 (best-tag priority so leaving a trial giver's zone before
actually completing the trial doesn't read as "advance" just because that
zone also sits on an unrelated route leg) is **not** implemented in S0:
doing this properly needs either an ordered plan graph (visit nodes in
dependency order, not a single flat h) or per-candidate lookahead, both
larger than a one-shot static h computation -- deferred to S1 alongside the
weighting work itself, per the review's own allowance.

2026-09-24 review round 3 (Opus, ed4c329, conditional pass -- three items
required before closing S0):

1. **Determinism.** ``_acquire_from_subject`` now returns its winning
   route name *explicitly* (from its own fixed-order ``options`` list),
   never scanned back out of a ``frozenset`` of tags: ``best`` can
   legitimately contain more than one ``("route", ...)`` tag (this level's
   own winner, plus a nested sub-acquisition's own "route":"fight" tag for
   a raw material fought for along the way), and a frozenset's iteration
   order for string elements depends on ``PYTHONHASHSEED`` -- two
   processes could (and, on neutral/seed 1 around t50-54, did) disagree on
   which tag came "first". Verified two ways: a subprocess-pair test
   (``DeterminismAcrossHashSeedsTests``) and a 30-run (6 genomes x 5 seeds)
   byte-identity sweep under ``PYTHONHASHSEED=1`` vs ``2`` (matching the
   review's own reproduction scripts), both clean. Every other function in
   this module already iterated tag/kind pools through ``sorted(...)`` or
   only for set *membership* (order-independent) -- ``_route_name`` (now
   removed) was the sole offender.
2. **Companion candidates with no strength payoff.** ``Subject.
   derived_modifiers`` grants an ally exactly ``peer.ally_value`` --
   momotaro_plus2's 鬼の弟 has ``ally_value: 0``, so recruiting him adds
   nothing. ``_is_companion_candidate`` now excludes ``ally_value <= 0``
   targets from the generic "any present non-hostile non-ally" rule;
   working toward him is still credited, but only through the existing
   stance_ge tag path (his trial's own ``requires.stance``, reached when
   弟の手紙 is actually on the live plan).
3. **Design decision C was over-applying.** Suppressing every alt tag once
   a free offer exists (round 2's design C) was also swallowing the
   *first*, not-yet-held copy of a genuine strength item (鉄砲) -- never
   redundant, since ``engine.contest.strength`` sums modifiers unconditionally
   per holder. ``_first_strength_item`` is a new, parallel consideration
   (independent of ``_best_offer``/design C) that tags the cheapest
   not-yet-held positive-modifier item as ``boost_fight``-worthy prepare,
   regardless of route or of an unrelated free offer; a *second* copy of
   something already held is still never tagged (unchanged from round 2's
   fix 3). Documented simplification: only checks the subject's own
   possession, not present/known allies' -- an ally's own inventory doesn't
   feed into the *subject's* ``strength()``, only ``peer.ally_value`` does,
   so ally-possession is largely moot here.
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

Tags = "frozenset[tuple[str, Any]]"

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
# Combine: h = cheapest option; best = its own tags; alt = everything else
# ---------------------------------------------------------------------------


def _combine(
    options: list[tuple[float, "Tags", "Tags"]],
) -> tuple[float, "Tags", "Tags"]:
    """``options`` is a list of ``(h, best, alt)`` triples, one per
    considered way to satisfy some need. Picks the cheapest (first, on a
    tie -- options are built in a fixed deterministic order) as the winner:
    its own ``best`` bubbles up as this level's ``best`` (plus whatever this
    caller adds itself); everything else considered anywhere (every other
    option's ``best`` and ``alt``, plus the winner's own ``alt``) becomes
    this level's ``alt``, minus whatever is already in ``best``."""

    if not options:
        return INF, frozenset(), frozenset()
    winner = min(options, key=lambda option: option[0])
    best_h, best_tags, winner_alt = winner
    pool: set[tuple[str, Any]] = set(winner_alt)
    for _, option_best, option_alt in options:
        pool |= option_best
        pool |= option_alt
    alt_tags = frozenset(pool) - best_tags
    return best_h, best_tags, alt_tags


# ---------------------------------------------------------------------------
# Zone travel (BFS ignoring item gating, then a recursive item unlock)
# ---------------------------------------------------------------------------


def _shortest_route_path(
    world: World,
    origin: str,
    dest: str,
    excluded: frozenset[str] = frozenset(),
) -> list[Any] | None:
    """Shortest route sequence by hop count, ignoring ``requires_item``
    gating and stamina (those are handled by the caller). None when the two
    zones aren't topologically connected at all, or only reachable through
    an ``excluded`` zone (2026-09-24 review 2, required fix B: a subject's
    ``range.exclude`` -- e.g. momotaro_plus2's protagonist can't re-enter
    村 before holding the treasure -- must gate the planner's travel the
    same way it gates the real engine's ``World.reachable_paths``)."""

    if origin == dest:
        return []

    prev: dict[str, tuple[str, Any] | None] = {origin: None}
    queue: deque[str] = deque([origin])
    while queue:
        zone = queue.popleft()
        if zone == dest:
            break
        for route in world.routes.get(zone, ()):
            if route.destination in prev or route.destination in excluded:
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
    *,
    origin: str | None = None,
    excluded: frozenset[str] | None = None,
    assume_held: frozenset[str] = frozenset(),
) -> tuple[float, "Tags", "Tags"]:
    """``origin`` defaults to ``subject.zone`` -- pass an explicit zone (e.g.
    the believed holder's) to plan a *future* leg from somewhere the subject
    isn't standing yet (review fix 1: ``plan()``'s delivery leg needs this
    so it isn't computed from "here", before the goal item is even
    acquired).

    ``excluded`` defaults to the subject's *current* ``range.exclude``
    zones (``World._excluded_zones``, review 2 required fix B) -- pass
    ``frozenset()`` explicitly for a future leg (e.g. delivery) where the
    excluding item will already have been acquired by then.

    ``assume_held`` (review 2 recommended fix 1): item names to treat as
    already in inventory for gating purposes only, without touching
    ``subject``. The delivery leg needs this for a persistent (``vehicle``)
    item like 船: the subject doesn't lose it on the way there, but
    ``_travel`` computed from a fresh ``subject.has_item`` check was
    otherwise re-planning (and double-counting the cost of) building a
    *second* one for the return trip."""

    origin_zone = subject.zone if origin is None else origin
    if origin_zone == dest:
        return 0.0, frozenset(), frozenset()
    excluded_zones = (
        frozenset(world._excluded_zones(subject)) if excluded is None else excluded
    )
    edges = _shortest_route_path(world, origin_zone, dest, excluded_zones)
    if edges is None:
        return INF, frozenset(), frozenset()

    # Every hop along the shortest path is itself an advancing move, not
    # only the final destination -- otherwise a multi-hop trip would only
    # ever credit its last leg.
    best_tags: set[tuple[str, Any]] = {("zone", route.destination) for route in edges}
    alt_tags: set[tuple[str, Any]] = set()
    extra_cost = 0.0
    for route in edges:
        item = route.requires_item
        if item is None or subject.has_item(item) or item in assume_held:
            continue
        item_h, item_best, item_alt = _acquire(item, subject, world, visiting, trial_reveal_facts)
        if item_h == INF:
            return INF, frozenset(), frozenset()
        extra_cost += item_h
        best_tags |= item_best
        alt_tags |= item_alt
    return float(len(edges)) + extra_cost, frozenset(best_tags), frozenset(alt_tags)


# ---------------------------------------------------------------------------
# Item acquisition (craft / investigate / trial / take-from-a-subject)
# ---------------------------------------------------------------------------


def _learn_fact(
    fact_id: str,
    subject: Subject,
    world: World,
    visiting: frozenset[Any],
    trial_reveal_facts: Mapping[str, str],
) -> tuple[float, "Tags", "Tags"]:
    if fact_id in subject.knowledge:
        return 0.0, frozenset(), frozenset()
    key = ("fact", fact_id)
    if key in visiting:
        return INF, frozenset(), frozenset()
    visiting = visiting | {key}

    options: list[tuple[float, "Tags", "Tags"]] = []
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
        travel_h, travel_best, travel_alt = _travel(
            subject, world, travel_zone, visiting, trial_reveal_facts
        )
        if travel_h == INF:
            continue
        options.append(
            (travel_h + 1.0, frozenset({("knows", fact_id)}) | travel_best, travel_alt)
        )

    return _combine(options)


def _acquire(
    item: str,
    subject: Subject,
    world: World,
    visiting: frozenset[Any],
    trial_reveal_facts: Mapping[str, str],
    *,
    needed: int = 1,
) -> tuple[float, "Tags", "Tags"]:
    # 2026-09-24 review 2, required fix A: the old unconditional
    # ``subject.has_item(item)`` (>=1) early-return made a recipe needing
    # e.g. 2 木材 read as fully satisfied by holding just 1 -- ``needed``
    # (passed by the craft branch below, per-material) makes this compare
    # against what's actually required here, not merely "at least one".
    if subject.inventory.get(item, 0) >= needed:
        return 0.0, frozenset(), frozenset()
    key = ("item", item)
    if key in visiting:
        return INF, frozenset(), frozenset()
    visiting = visiting | {key}

    options: list[tuple[float, "Tags", "Tags"]] = []
    definition = world.items.get(item, {})

    # 1. craft
    if item in world.recipes:
        needed_fact = (definition.get("requires") or {}).get("knowledge")
        fact_h, fact_best, fact_alt = 0.0, frozenset(), frozenset()
        fact_ok = True
        if needed_fact is not None and needed_fact not in subject.knowledge:
            fact_h, fact_best, fact_alt = _learn_fact(
                str(needed_fact), subject, world, visiting, trial_reveal_facts
            )
            fact_ok = fact_h != INF
        if fact_ok:
            material_h = 0.0
            material_best: set[tuple[str, Any]] = set()
            material_alt: set[tuple[str, Any]] = set()
            materials_ok = True
            for material, qty in sorted(world.recipes[item].items()):
                if subject.inventory.get(material, 0) >= qty:
                    continue
                mh, mbest, malt = _acquire(
                    material, subject, world, visiting, trial_reveal_facts, needed=qty
                )
                if mh == INF:
                    materials_ok = False
                    break
                material_h += mh
                material_best |= mbest
                material_best.add(("has_item", material))
                material_alt |= malt
            if materials_ok:
                craft_zone = definition.get("craft_zone")
                if craft_zone is None:
                    travel_h, travel_best, travel_alt = 0.0, frozenset(), frozenset()
                else:
                    travel_h, travel_best, travel_alt = _travel(
                        subject, world, str(craft_zone), visiting, trial_reveal_facts
                    )
                if travel_h != INF:
                    best = (
                        frozenset({("has_item", item)})
                        | fact_best
                        | frozenset(material_best)
                        | travel_best
                    )
                    alt = fact_alt | frozenset(material_alt) | travel_alt
                    options.append((fact_h + material_h + travel_h + 1.0, best, alt))

    # 2. investigate a source zone
    for source in definition.get("sources", []) or []:
        if source.get("type") != "investigate" or not source.get("zone"):
            continue
        travel_h, travel_best, travel_alt = _travel(
            subject, world, str(source["zone"]), visiting, trial_reveal_facts
        )
        if travel_h == INF:
            continue
        options.append(
            (travel_h + 1.0, frozenset({("has_item", item)}) | travel_best, travel_alt)
        )

    # 3. a trial's grant
    for trial in world.trials:
        grants = trial.get("grants") or {}
        if str(grants.get("item", "")) != item:
            continue
        trial_h, trial_best, trial_alt = _trial_cost(
            trial, subject, world, visiting, trial_reveal_facts
        )
        if trial_h != INF:
            options.append(
                (trial_h, trial_best | frozenset({("has_item", item)}), trial_alt)
            )

    # 4. take it from whoever currently holds it (fight only -- never
    # negotiate for a non-objective item, review 2 required fix B). Also
    # skip entirely when the subject has no in-fiction way to know this
    # holder has it (review 2 recommended fix 3: an invisible modifier's
    # possession, e.g. 鬼's 金棒, is not "public" the way world.holder
    # normally is -- see _holder_appears_to_have).
    holder_id = world.holder(item)
    if holder_id is not None and holder_id != subject.id and holder_id in world.subjects:
        holder_subject = world.subjects[holder_id]
        if _holder_appears_to_have(subject, holder_subject, item, world):
            take_h, take_best, take_alt, _take_route = _acquire_from_subject(
                item, holder_subject, subject, world, visiting, trial_reveal_facts,
                is_objective=False,
            )
            if take_h != INF:
                options.append(
                    (take_h, take_best | frozenset({("has_item", item)}), take_alt)
                )

    return _combine(options)


def _trial_cost(
    trial: dict[str, Any],
    subject: Subject,
    world: World,
    visiting: frozenset[Any],
    trial_reveal_facts: Mapping[str, str],
) -> tuple[float, "Tags", "Tags"]:
    giver_id = str(trial.get("giver", ""))
    giver = world.subjects.get(giver_id)
    if giver is None or giver_id == subject.id:
        return INF, frozenset(), frozenset()

    best: set[tuple[str, Any]] = set()
    alt: set[tuple[str, Any]] = set()
    cost = 0.0

    # A giver the subject has no in-fiction way to know about yet is not a
    # free waypoint -- fold in learning the revealing fact (momotaro_plus2's
    # 鬼の弟 must not look reachable before 村 is investigated for 弟の消息).
    reveal_fact = trial_reveal_facts.get(giver_id)
    if reveal_fact is not None and reveal_fact not in subject.knowledge:
        fact_h, fact_best, fact_alt = _learn_fact(
            reveal_fact, subject, world, visiting, trial_reveal_facts
        )
        if fact_h == INF:
            return INF, frozenset(), frozenset()
        cost += fact_h
        best |= fact_best
        alt |= fact_alt

    travel_h, travel_best, travel_alt = _travel(subject, world, giver.zone, visiting, trial_reveal_facts)
    if travel_h == INF:
        return INF, frozenset(), frozenset()
    best |= travel_best
    alt |= travel_alt

    requires = trial.get("requires") or {}
    threshold = float(requires.get("stance", -1.0))
    if world.relations.stance(giver_id, subject.id) < threshold:
        best.add(("stance_ge", giver_id))
        cost += _STANCE_RAISE_COST

    required_item = requires.get("item")
    if required_item is not None:
        item_h, item_best, item_alt = _acquire(
            str(required_item), subject, world, visiting, trial_reveal_facts
        )
        if item_h == INF:
            return INF, frozenset(), frozenset()
        cost += item_h
        best |= item_best
        alt |= item_alt

    return travel_h + cost + 1.0, frozenset(best), frozenset(alt)


def _holder_appears_to_have(
    subject: Subject, holder: Subject, item: str, world: World
) -> bool:
    """Whether the subject has any in-fiction way to know ``holder`` already
    has ``item`` -- true possession the subject could not have observed
    (an invisible modifier they haven't separately learned of) is not
    "public" the way ``world.holder`` normally is (2026-09-24 review fix 8:
    momotaro_plus2's 金棒 has ``visible: false``, so a plan must not silently
    know 鬼 already holds it when picking a negotiate offer)."""

    if not holder.has_item(item):
        return False
    spec = world.item_modifier(item)
    if spec is None or spec.get("visible", True):
        return True
    belief = subject.beliefs_about.get(holder.id)
    return belief is not None and item in belief.known_modifiers


def _best_offer(
    holder: Subject,
    subject: Subject,
    world: World,
    visiting: frozenset[Any],
    trial_reveal_facts: Mapping[str, str],
) -> tuple[float, "Tags", "Tags"]:
    """Every lootable+modifier item the subject could acquire and hand over
    to make a negotiate attractive (mirrors ``engine.actions.
    _concede_candidates``'s "attractive" test): the cheapest such item's own
    tags become ``best``, every other considered item's tags become
    ``alt``. An item the subject already holds contributes no ``has_item``
    tag (2026-09-24 review fix 3: tagging an already-held item meant
    crafting a *second* or *third* copy of it kept reading as "advance")."""

    options: list[tuple[float, "Tags", "Tags"]] = []
    for name, definition in sorted(world.items.items()):
        if not definition.get("lootable") or not definition.get("modifier"):
            continue
        if _holder_appears_to_have(subject, holder, name, world):
            continue
        item_h, item_best, item_alt = _acquire(name, subject, world, visiting, trial_reveal_facts)
        if item_h == INF:
            continue
        tag = frozenset() if subject.has_item(name) else frozenset({("has_item", name)})
        options.append((item_h, item_best | tag, item_alt))
    h, best, alt = _combine(options)
    # Design decision C (2026-09-24 review 2, confirmed by the design role):
    # a free offer already in hand (h==0) means fetching or building a
    # *second* one is not "prepare" -- it's a plain detour. Suppress every
    # other considered offer's tags entirely rather than exposing them as
    # prepare-eligible alt (strength-boosting prep -- the first 鉄砲 toward
    # boost_fight, train, companion recruiting -- is untouched: those tags
    # never come from this function).
    if h == 0.0:
        return h, best, frozenset()
    return h, best, alt


def _first_strength_item(
    subject: Subject,
    world: World,
    visiting: frozenset[Any],
    trial_reveal_facts: Mapping[str, str],
) -> tuple[float, "Tags", "Tags"]:
    """The cheapest positive-strength-modifier item the subject doesn't yet
    hold -- owning it raises ``engine.contest.strength`` (an unconditional
    per-holder sum, see ``engine.contest.strength``/``Subject.
    all_modifiers`` -- visibility only affects what *others* estimate about
    the holder, never the holder's own actual value) in any fight,
    independent of which route is being pursued or whether a negotiate
    offer is already free.

    2026-09-24 review 3, required fix 3: design decision C's "no second
    offer" suppression in ``_best_offer`` was also swallowing the *first*
    copy of a strength item once a free offer (勾玉) existed -- that first
    copy is never redundant. This is a separate, parallel consideration:
    it never contributes to ``best`` (never the literal winning acquisition
    route) and is merged into the caller's ``alt`` regardless of route.

    Simplification (documented judgment call): only checks the subject's
    *own* possession, not present/known allies' -- ``strength()`` only ever
    sums the holder's own modifiers, so an ally carrying a second copy
    doesn't raise the *subject's* strength() the way holding it personally
    does, making an ally-possession check largely moot for this purpose."""

    options: list[tuple[float, "Tags", "Tags"]] = []
    for name, definition in sorted(world.items.items()):
        modifier = definition.get("modifier") or {}
        value = float(modifier.get("value", 0.0))
        if value <= 0.0:
            continue
        if subject.has_item(name):
            continue
        item_h, item_best, item_alt = _acquire(name, subject, world, visiting, trial_reveal_facts)
        if item_h == INF:
            continue
        tags = item_best | frozenset({("has_item", name), ("boost_fight", name)})
        options.append((item_h, tags, item_alt))
    return _combine(options)


def _acquire_from_subject(
    item: str,
    holder: Subject,
    subject: Subject,
    world: World,
    visiting: frozenset[Any],
    trial_reveal_facts: Mapping[str, str],
    *,
    is_objective: bool,
) -> tuple[float, "Tags", "Tags", str | None]:
    """``is_objective`` (review 2, required fix B): the real engine's
    ``_negotiate_candidates`` only ever offers to negotiate for the
    subject's own ``goal.target`` -- an arbitrary material someone else
    happens to be holding (縄, 木材, ...) can only be taken by fighting for
    it, never "negotiated" for. Only the top-level acquisition of the goal
    item itself passes ``is_objective=True``.

    Returns ``(h, best, alt, route_name)`` -- ``route_name`` (review 3,
    required fix 1) is the *explicit* winning branch's name ("fight" or
    "negotiate"), determined locally from this function's own ``options``
    list (built in a fixed, non-hash-dependent order). It must never be
    read back out of ``best`` by scanning for a ``("route", ...)`` tag:
    ``best`` can legitimately contain more than one such tag (this level's
    own winning route, *plus* a nested sub-acquisition's "route":"fight"
    tag for a raw material fought for along the way), and a frozenset's
    iteration order depends on ``PYTHONHASHSEED`` for string elements --
    scanning it for "the first" was non-deterministic across processes
    (found via a cross-seed byte-identity check, review 3)."""

    key = ("subject", holder.id, item)
    if key in visiting:
        return INF, frozenset(), frozenset(), None
    visiting = visiting | {key}

    travel_h, travel_best, travel_alt = _travel(subject, world, holder.zone, visiting, trial_reveal_facts)
    if travel_h == INF:
        return INF, frozenset(), frozenset(), None

    options: list[tuple[float, "Tags", "Tags", str]] = []

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
            best = set(travel_best) | {("win_fight", holder.id), ("route", "fight")}
            if probability < 0.9:
                best.add(("boost_fight", holder.id))
            options.append((travel_h + rounds, frozenset(best), travel_alt, "fight"))

    if is_objective and "negotiate" in subject.verbs:
        best = set(travel_best) | {("route", "negotiate")}
        alt = set(travel_alt)
        extra_cost = 0.0
        if world.relations.stance(holder.id, subject.id) < world.negotiate_threshold:
            offer_h, offer_best, offer_alt = _best_offer(
                holder, subject, world, visiting, trial_reveal_facts
            )
            if offer_h != INF:
                extra_cost += offer_h
                best |= offer_best
                alt |= offer_alt
            else:
                best.add(("stance_ge", holder.id))
                extra_cost += _STANCE_RAISE_COST
        options.append((travel_h + extra_cost + 1.0, frozenset(best), frozenset(alt), "negotiate"))

    if not options:
        return INF, frozenset(), frozenset(), None

    winner = min(options, key=lambda option: option[0])
    route_name = winner[3]
    h, best, alt = _combine([(o[0], o[1], o[2]) for o in options])

    if is_objective:
        strength_h, strength_best, strength_alt = _first_strength_item(
            subject, world, visiting, trial_reveal_facts
        )
        if strength_h != INF:
            alt |= (strength_best | strength_alt) - best

    return h, best, alt, route_name


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

    ``best``/``alt`` split what an action must match to count as advance vs.
    prepare (review fix 2). ``route`` names the single winning branch
    (fight/negotiate), returned *explicitly* by ``_acquire_from_subject``
    (review 3, required fix 1) -- never scanned back out of ``best``, which
    can legitimately contain more than one ``("route", ...)`` tag (this
    level's own winner, plus a nested sub-acquisition's fight tag for a raw
    material fought for along the way)."""

    reveal_facts = trial_reveal_facts or {}
    target = subject.goal.target
    if target is None:
        return {"h": None, "believed_holder": None, "best": frozenset(), "alt": frozenset(), "route": None}

    if subject.has_item(target):
        deliver_to = subject.goal.deliver_to
        if deliver_to is None:
            return {"h": 0.0, "believed_holder": subject.id, "best": frozenset(), "alt": frozenset(), "route": None}
        h, best, alt = _travel(subject, world, deliver_to, frozenset(), reveal_facts)
        return {"h": h, "believed_holder": subject.id, "best": best, "alt": alt, "route": None}

    believed_holder_id = _believed_holder(subject, world, target, holder_belief_fact)
    if believed_holder_id is None or believed_holder_id not in world.subjects:
        return {"h": None, "believed_holder": believed_holder_id, "best": frozenset(), "alt": frozenset(), "route": None}

    holder_subject = world.subjects[believed_holder_id]
    acquire_h, acquire_best, acquire_alt, route_name = _acquire_from_subject(
        target, holder_subject, subject, world, frozenset({("item", target)}), reveal_facts,
        is_objective=True,
    )
    if acquire_h == INF:
        return {"h": INF, "believed_holder": believed_holder_id, "best": frozenset(), "alt": frozenset(), "route": None}

    deliver_h = 0.0
    if subject.goal.deliver_to is not None:
        # review fix 1: computed from the *holder's* zone (where the
        # subject will actually be once the item is taken), not from
        # subject.zone -- otherwise every step of the journey there already
        # counts the homeward trip and h stops decreasing monotonically.
        # Its tags are deliberately NOT merged into best/alt below: while
        # the item is still unacquired, delivery hasn't started yet, so a
        # move that happens to sit on the eventual homeward path (e.g.
        # retreating toward 村 before ever reaching the holder) must not
        # read as "advance" just because it will matter later. Only the
        # scalar h -- the true total remaining distance -- includes it.
        #
        # review 2, recommended fixes: ``excluded=frozenset()`` because the
        # subject's range.exclude (e.g. 村 until holding the treasure) will
        # already be lifted by the time delivery starts, and ``assume_held``
        # carries forward any persistent (vehicle) item the acquire phase's
        # winning branch picked up -- otherwise the delivery leg silently
        # re-planned (and double-counted the cost of) building a *second*
        # 船 for the trip home, since a fresh subject.has_item check has no
        # way to know the first one is still in hand.
        vehicle_items = frozenset(
            value
            for kind, value in acquire_best
            if kind == "has_item" and world.items.get(str(value), {}).get("vehicle")
        )
        deliver_h, _deliver_best, _deliver_alt = _travel(
            subject, world, subject.goal.deliver_to, frozenset(), reveal_facts,
            origin=holder_subject.zone, excluded=frozenset(), assume_held=vehicle_items,
        )
        if deliver_h == INF:
            return {"h": INF, "believed_holder": believed_holder_id, "best": frozenset(), "alt": frozenset(), "route": None}

    return {
        "h": acquire_h + deliver_h,
        "believed_holder": believed_holder_id,
        "best": acquire_best,
        "alt": acquire_alt,
        "route": route_name,
    }


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
    """A present, living peer who isn't hostile, isn't already an ally, and
    would actually raise the subject's strength once allied (companionship's
    own direction: the *peer's* stance toward self, matching ``Subject.
    derived_modifiers``/``_movement_candidates``'s companion checks
    elsewhere in the engine) -- a plausible companion-recruiting target.

    2026-09-24 review 3, required fix 2: ``Subject.derived_modifiers``
    grants an ally modifier worth exactly ``peer.ally_value``, so a peer
    with ``ally_value <= 0`` (momotaro_plus2's 鬼の弟) contributes nothing
    (or actively hurts) if recruited -- the generic "any present non-
    hostile non-ally is worth befriending" rule must not fire for them.
    Working toward 鬼の弟 specifically is still credited, but only through
    the existing stance_ge tag (his trial's own ``requires.stance``, when
    弟の手紙 is actually on the live best/alt plan)."""

    if target.id == subject.id or target.vitality == "dead":
        return False
    if world.target_role(subject, target) == "hostile":
        return False
    if target.ally_value <= 0.0:
        return False
    already_ally = (
        world.relations.stance(target.id, subject.id)
        >= world.companionship["threshold"]
    )
    return not already_ally


def _would_break_requirement(
    subject: Subject,
    world: World,
    item: str,
    relevant_products: set[Any],
    relevant_trial_items: set[Any],
    relevant_trial_facts: set[Any],
    give_qty: int = 1,
) -> bool:
    """Whether handing over ``give_qty`` of ``item`` would drop the subject
    below what a still-relevant recipe or trial requires (review fix 4:
    matching only "the planner currently has an open need for this"
    under-protected an *exactly* sufficient stock -- e.g. holding precisely
    the 2 木材 船 needs looked safe to give away, since nothing was
    "missing" yet).

    ``relevant_*`` (review 2, recommended fix 2) restrict protection to
    products/trials actually appearing somewhere in best/alt -- otherwise
    every trial in the whole world file (e.g. 猿's 猿の知恵, never on any
    considered path here) permanently protects its required item (きびだん
    ご), and the last one can never be credited for recruiting a companion
    even once every real need is satisfied."""

    remaining = subject.inventory.get(item, 0) - give_qty
    for product, materials in world.recipes.items():
        if item not in materials or subject.has_item(product):
            continue
        if product not in relevant_products:
            continue
        if remaining < materials[item]:
            return True
    for trial in world.trials:
        requires = trial.get("requires") or {}
        if str(requires.get("item", "")) != item:
            continue
        grants = trial.get("grants") or {}
        granted_item = grants.get("item")
        granted_fact = grants.get("fact")
        if granted_item not in relevant_trial_items and granted_fact not in relevant_trial_facts:
            continue
        already_done = (
            (granted_item and subject.has_item(str(granted_item)))
            or (granted_fact and str(granted_fact) in subject.knowledge)
        )
        if not already_done and remaining < 1:
            return True
    return False


def _kinds(tags: "Tags") -> dict[str, set[Any]]:
    grouped: dict[str, set[Any]] = {}
    for kind, value in tags:
        grouped.setdefault(kind, set()).add(value)
    return grouped


def _match_advance_or_prepare(
    action: Action,
    subject: Subject,
    world: World,
    present: Sequence[Subject],
    present_ids: set[str],
    best_kinds: dict[str, set[Any]],
    alt_kinds: dict[str, set[Any]],
    believed_holder_id: str | None,
    believed_holder_zone: str | None,
    holder_is_true: bool,
) -> str | None:
    verb = action.verb

    if verb == "move":
        dest = action.meta.get("dest")
        # A misattributed believed holder's zone is never credited as
        # advance/prepare via the zone tag -- it falls through to
        # _detour_cause's "belief" (2026-09-24 review recommended fix 4).
        if not holder_is_true and dest is not None and dest == believed_holder_zone:
            return None
        if dest in best_kinds.get("zone", ()):
            return "advance"
        if dest in alt_kinds.get("zone", ()):
            return "prepare"
        return None

    if verb == "investigate":
        zone = subject.zone
        if action.meta.get("gather"):
            if any(_sourced_at_zone(item, world, zone) for item in best_kinds.get("has_item", ())):
                return "advance"
            if any(_sourced_at_zone(item, world, zone) for item in alt_kinds.get("has_item", ())):
                return "prepare"
        if any(_fact_sourced_here(fact_id, world, zone, present_ids) for fact_id in best_kinds.get("knows", ())):
            return "advance"
        if any(_fact_sourced_here(fact_id, world, zone, present_ids) for fact_id in alt_kinds.get("knows", ())):
            return "prepare"
        return None

    if verb == "observe":
        target = action.meta.get("target")
        if target is not None and target in alt_kinds.get("boost_fight", set()) | best_kinds.get("boost_fight", set()):
            return "prepare"
        return None

    if verb == "craft":
        item = action.args[0] if action.args else None
        if item in best_kinds.get("has_item", ()):
            return "advance"
        if item in alt_kinds.get("has_item", ()):
            return "prepare"
        return None

    if verb == "trial":
        trial_id = action.meta.get("trial_id")
        trial = next(
            (t for t in world.trials if str(t.get("id")) == str(trial_id)), None
        )
        grants = (trial or {}).get("grants") or {}
        if grants.get("item") in best_kinds.get("has_item", ()) or grants.get("fact") in best_kinds.get("knows", ()):
            return "advance"
        if grants.get("item") in alt_kinds.get("has_item", ()) or grants.get("fact") in alt_kinds.get("knows", ()):
            return "prepare"
        return None

    if verb == "negotiate":
        target = action.meta.get("target")
        if target != believed_holder_id or not holder_is_true:
            return None
        if "negotiate" in best_kinds.get("route", ()):
            return "advance"
        if "negotiate" in alt_kinds.get("route", ()):
            return "prepare"
        return None

    if verb == "fight":
        target = action.args[0] if action.args else None
        if target != believed_holder_id or not holder_is_true:
            return None
        if "fight" in best_kinds.get("route", ()):
            return "advance"
        if "fight" in alt_kinds.get("route", ()):
            return "prepare"
        return None

    if verb == "train":
        if best_kinds.get("boost_fight") or alt_kinds.get("boost_fight"):
            return "prepare"
        return None

    if verb == "rescue":
        # rescue only ever targets a present, downed subject the protagonist
        # already has some affinity for (engine.actions._rescue_candidates)
        # -- restoring them restores their fight-strength contribution
        # regardless of which route is cheapest (recommended fix 1).
        return "prepare"

    if verb in ("give_item", "persuade", "pledge", "share_knowledge"):
        target_id = action.meta.get("target")
        if verb == "give_item":
            item = action.meta.get("item")
            if item is not None:
                relevant_items = best_kinds.get("has_item", set()) | alt_kinds.get("has_item", set())
                relevant_facts = best_kinds.get("knows", set()) | alt_kinds.get("knows", set())
                if _would_break_requirement(
                    subject, world, str(item), relevant_items, relevant_items, relevant_facts
                ):
                    return None
        if target_id in best_kinds.get("stance_ge", ()):
            return "advance"
        if target_id in alt_kinds.get("stance_ge", ()):
            return "prepare"
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
    believed_holder_zone: str | None,
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

    if verb == "observe" and action.meta.get("target") == believed_holder_id:
        return "ignorance"

    if verb in ("investigate", "observe") and (h is None or h == INF):
        return "ignorance"

    misattributed = (
        believed_holder_id is not None
        and believed_holder_id != world.holder(subject.goal.target)
    )

    target = action.meta.get("target")
    if target is None and action.args and isinstance(action.args[0], str):
        target = action.args[0]
    if (
        verb in ("fight", "negotiate", "sabotage", "mislead")
        and misattributed
        and target == believed_holder_id
    ):
        return "belief"

    if verb == "move" and misattributed:
        dest = action.meta.get("dest")
        if believed_holder_zone is not None and dest == believed_holder_zone:
            return "belief"

    return "none"


# ---------------------------------------------------------------------------
# Text and milestone (built from the winning branch, `best`, only)
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

_MILESTONE_PRIORITY = ("has_item", "knows", "stance_ge", "win_fight", "zone")


def _milestone(tags: "Tags") -> str | None:
    """A single next node from ``tags`` (the winning branch, ``best``), by a
    fixed priority order -- not every open need (2026-09-24 review
    recommended fix 3)."""

    grouped = _kinds(tags)
    for kind in _MILESTONE_PRIORITY:
        values = sorted(grouped.get(kind, ()), key=str)
        if values:
            return f"{kind}:{values[0]}"
    return None


def _advance_text(
    action: Action, subject: Subject, world: World, kinds: dict[str, set[Any]]
) -> str:
    """Built from ``kinds`` (the tag pool an action actually matched against
    -- ``best`` for an "advance" result, ``alt`` for "prepare")."""

    verb = action.verb

    if verb == "move":
        dest = action.meta.get("dest")
        for item in sorted(kinds.get("has_item", ())):
            definition = world.items.get(item, {})
            if str(definition.get("craft_zone")) == dest:
                return f"{item}を作るため{dest}へ向かった"
        for holder_id in sorted(kinds.get("win_fight", ())) + sorted(kinds.get("stance_ge", ())):
            holder = world.subjects.get(str(holder_id))
            if holder is not None and holder.zone == dest:
                return f"{holder_id}のもとへ向かった"
        # 2026-09-24 review 2, recommended fix 5: name what's actually at
        # dest (a still-needed material/fact source) rather than falling
        # straight to the generic "近づいた" whenever no holder/craft_zone
        # matched -- e.g. a waypoint hop toward a *further* zone otherwise
        # gave no hint of purpose at all.
        for item in sorted(kinds.get("has_item", ())):
            if _sourced_at_zone(item, world, str(dest)):
                return f"{item}を集めるため{dest}へ向かった"
        for fact_id in sorted(kinds.get("knows", ())):
            if _fact_sourced_here(fact_id, world, str(dest), set()):
                return f"{fact_id}について調べるため{dest}へ向かった"
        return f"{dest}へ移動して近づいた"

    if verb == "investigate":
        zone = subject.zone
        for item in sorted(kinds.get("has_item", ())):
            if not _sourced_at_zone(item, world, zone):
                continue
            product = next(
                (
                    name
                    for name, materials in world.recipes.items()
                    if item in materials and name in kinds.get("has_item", ())
                ),
                None,
            )
            return f"{product}のために{item}を集めた" if product else f"{item}を集めた"
        for fact_id in sorted(kinds.get("knows", ())):
            if _fact_sourced_here(fact_id, world, zone, set()):
                return f"{fact_id}について調べた"
        return "必要なものを調べた"

    if verb == "observe":
        return "相手の様子をうかがった"

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

    if verb == "rescue":
        return "倒れた仲間を助け起こした"

    if verb in _RELATION_VERB_TEXT:
        target_id = action.meta.get("target") or (action.args[0] if action.args else "相手")
        if target_id in kinds.get("stance_ge", ()):
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
    """One annotation dict per action in ``actions``, in order. Adds a few
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
    best = state["best"]
    alt = state["alt"]
    believed_holder_id = state["believed_holder"]
    route_name = state["route"]
    present_ids = {peer.id for peer in present}
    true_holder_id = (
        world.holder(subject.goal.target) if subject.goal.target is not None else None
    )
    holder_is_true = believed_holder_id == true_holder_id
    believed_holder_zone = (
        world.subjects[believed_holder_id].zone
        if believed_holder_id is not None and believed_holder_id in world.subjects
        else None
    )

    best_kinds = _kinds(best)
    alt_kinds = _kinds(alt)

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
            best_kinds,
            alt_kinds,
            believed_holder_id,
            believed_holder_zone,
            holder_is_true,
        )
        base = {
            "plan": route_name,
            "milestone": _milestone(best),
            "zone": zone,
            "inventory": inventory,
            "allies": allies,
        }
        if matched is not None:
            # 2026-09-24 review recommended fix 2: only an *advancing*
            # action is credited with reducing h -- prepare/detour keep
            # h_before unchanged, since neither literally completes a node
            # on the winning plan.
            h_after = (
                round(max(0.0, h_before - 1.0), 6)
                if matched == "advance" and h_before is not None and h_before != INF
                else _finite_or_none(h_before)
            )
            results.append(
                {
                    **base,
                    "kind": matched,
                    "cause": None,
                    "h": [_finite_or_none(h_before), h_after],
                    "text": _advance_text(
                        action, subject, world, best_kinds if matched == "advance" else alt_kinds
                    ),
                }
            )
            continue

        cause = _detour_cause(
            action, subject, world, believed_holder_id, believed_holder_zone, h_before
        )
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
