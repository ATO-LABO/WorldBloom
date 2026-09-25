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
act_threshold 0.5, so the belief branch is provably unreachable here
(recorded only, per the design review -- a world-content question for
S2+, not fixed in S0).

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

S2 design judgment H (Opus review, WB-ROUTE-001 S2): a caution motive
(believed_weaker) was being attached to rest/withdraw candidates whose real
driver was fatigue or stress rather than a tactical read of the enemy --
53/82 of one sweep's caution picks were actually below-threshold
tired/stressed states. ``_body_or_belief_cause`` now widens "body" (S1's
exhausted/downed/under_threat bar is untouched) to also cover a tired rest
(stamina ratio <= ``REST_FATIGUE_STAMINA_RATIO``) and a stressed withdraw
(stress above ``WITHDRAW_STRESS_THRESHOLD``, matching the stress term in
``engine.actions._withdraw_candidates``'s own weight formula), each with
its own text; caution can now only ever attach to a rest/withdraw that
clears both bars (genuinely idle, not tired or stressed). ``h`` itself is
*not* used for this classification and never will be: it is a report-only
number that can jump non-monotonically across a ``strong_enough`` node's
open/closed edge (an ally joining or leaving, or a strength item being
gained/lost, changes what the backward search considers reachable), so it
is a fine thing to log but the wrong thing to key a cause off of.
"""

from __future__ import annotations

import math
from collections import deque
from pathlib import Path
from typing import Any, Mapping, Sequence, TYPE_CHECKING

import yaml

from engine.contest import believed_strength, strength
from engine.predicate import compile_predicate_syntax
from gapengine.genome import CATEGORIES

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
_BOOST_FALLBACK_COST = 1.0  # ponytail: S1 review 2 design judgment E -- flat
# one-action stand-in for "train until strong enough" when no acquirable
# item raises strength() either; train() itself has no effect size in this
# symbolic h model (same rationale as _STANCE_RAISE_COST above).


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


# S1 §2: base multiplier b per kind/cause -- route.yaml's own
# `multipliers:` block overrides individual entries; see Route.multiplier.
DEFAULT_MULTIPLIERS: dict[str, float] = {
    "advance": 1.0,
    "prepare": 0.5,
    "detour:body": 1.0,  # a physical need is never punished
    "detour:belief": 1.0,  # advances the subject's own (mistaken) plan
    "detour:ignorance": 0.5,
    "detour:none": 0.02,  # delta -- never fully zeroed out
    "lost": 1.0,  # h==inf: the route layer has nothing to say, no modulation
}


# S2 §2: the gene keys a motive's `gene:` field (optionally `-`-prefixed to
# invert) may name. Kept as a frozenset (not hardcoded to a template's
# active categories) so an unknown key is caught at load time rather than
# silently defaulting to some magic strength.
_MOTIVE_GENE_KEYS = frozenset(
    {"risk_tolerance", "stance_shift_bias", "novelty_drive"}
    | {f"category_weight.{category}" for category in CATEGORIES}
)


def load_motives(template_dir: str | Path) -> list[dict[str, Any]] | None:
    """``templates/<genre>/motives.yaml`` (S2 §1), or None when the template
    has none (no motive ever overrides a detour/none candidate -- same as a
    template with no route.yaml at all)."""

    path = Path(template_dir) / "motives.yaml"
    if not path.is_file():
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if not isinstance(raw, list):
        raise ValueError(f"{path} must be a YAML list of motive entries")

    motives: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(raw):
        if not isinstance(entry, Mapping):
            raise ValueError(f"{path}: entry {index} must be a mapping")
        motive_id = entry.get("id")
        if not isinstance(motive_id, str) or not motive_id:
            raise ValueError(f"{path}: entry {index} is missing a string id")
        if motive_id in seen_ids:
            raise ValueError(f"{path}: duplicate motive id {motive_id!r}")
        seen_ids.add(motive_id)

        when = entry.get("when")
        if not isinstance(when, str) or not when.strip():
            raise ValueError(f"{path}: {motive_id} is missing `when`")

        verbs = entry.get("verbs")
        if not isinstance(verbs, list) or not verbs:
            raise ValueError(f"{path}: {motive_id} is missing `verbs`")

        gene = entry.get("gene")
        if not isinstance(gene, str) or not gene:
            raise ValueError(f"{path}: {motive_id} is missing `gene`")
        base_gene = gene[1:] if gene.startswith("-") else gene
        if base_gene not in _MOTIVE_GENE_KEYS:
            raise ValueError(
                f"{path}: {motive_id} has unknown gene key {gene!r}"
            )

        text = entry.get("text")
        if not isinstance(text, str) or not text:
            raise ValueError(f"{path}: {motive_id} is missing `text`")

        motives.append(
            {
                "id": motive_id,
                "label": entry.get("label"),
                "when": when,
                "predicate": compile_predicate_syntax(when),
                "verbs": frozenset(str(verb) for verb in verbs),
                "gene": gene,
                "text": text,
            }
        )
    return motives


def load_route_config(template_dir: str | Path) -> dict[str, Any] | None:
    """``templates/<genre>/route.yaml``, or None when the template has none
    (route stays disabled -- S0 default for every template but
    momotaro_plus2)."""

    path = Path(template_dir) / "route.yaml"
    if not path.is_file():
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        "holder_belief_fact": raw.get("holder_belief_fact"),
        "trial_reveal_facts": {
            str(giver): str(fact_id)
            for giver, fact_id in (raw.get("trial_reveal_facts") or {}).items()
        },
        "rho": float(raw.get("rho", 0.0)),
        "multipliers": {
            str(key): float(value)
            for key, value in (raw.get("multipliers") or {}).items()
        },
        # Design judgment D: below this believed win probability, a fight/
        # sabotage/neutralize aimed at the true holder is a hopeless
        # gesture, not a real branch of the plan -- forced to detour/none
        # regardless of whether it happens to sit on best or alt.
        "min_win_prob": float(raw.get("min_win_prob", 0.2)),
        # S2 §4: gene_affinity (0 disables -- S1 identical) biases which
        # route (named in route_category, e.g. fight/negotiate) plan()
        # treats as cheapest, without changing the recorded h itself.
        "gene_affinity": float(raw.get("gene_affinity", 0.0)),
        "route_category": {
            str(key): str(value)
            for key, value in (raw.get("route_category") or {}).items()
        },
        # S2 §1: motives.yaml lives next to route.yaml -- loaded here so
        # every caller of load_route_config (evolve.py, the eval/probe
        # scripts, tests) gets it for free via Route.from_config.
        "motives": load_motives(template_dir),
    }


def _clip01(value: float) -> float:
    return min(1.0, max(0.0, value))


def gene_strength(
    genome: Any, gene: str, category_mean: float | None = None
) -> float:
    """S2 §2: the 0..1 "how strongly does this genome lean into `gene`"
    reading used both by motive multipliers (§3) and by gene_affinity route
    selection (§4). ``genome`` is duck-typed (``gapengine.genome.Genome`` or
    anything with the same four attributes) -- this module never imports
    ``gapengine.policy``. A leading ``-`` inverts (``1 - s``)."""

    negate = gene.startswith("-")
    key = gene[1:] if negate else gene
    if key.startswith("category_weight."):
        category = key.split(".", 1)[1]
        weight = float(genome.category_weight.get(category, 0.5))
        mean = float(category_mean) if category_mean else 0.0
        s = _clip01(weight / mean / 2.0) if mean > 0.0 else 0.5
    elif key == "risk_tolerance":
        s = _clip01(float(genome.risk_tolerance))
    elif key == "stance_shift_bias":
        s = _clip01((float(genome.stance_shift_bias) + 1.0) / 2.0)
    elif key == "novelty_drive":
        s = _clip01(float(genome.novelty_drive))
    else:
        raise ValueError(f"unknown gene key: {gene!r}")
    return 1.0 - s if negate else s


class Route:
    """Read-only planner/annotator, duck-typed into
    ``gapengine.policy.Policy`` via its ``route`` kwarg (policy.py never
    imports this module -- same pattern as ``rationality``).

    S1 §2: ``rho`` (0 by default) turns the S0 annotation into an actual
    weight multiplier, ``m_route = b ** rho`` where ``b`` is
    ``multipliers[kind]`` or ``multipliers[f"detour:{cause}"]``. At
    ``rho == 0``, ``b ** 0 == 1.0`` for any positive ``b``, so multiplying
    is always a mathematical no-op -- ``Route.enabled``/``Policy.reweight``
    still gate the multiplication and its meta key on ``rho > 0`` (not on
    whether a Route object exists at all) so a probe can keep constructing
    ``Route(rho=0.0)`` purely to get S0-style annotations, while the GA path
    (``gapengine/evolve.py``) constructs no Route object at all at rho=0 --
    guaranteeing byte-identity with a route-free run trivially, rather than
    relying on the "b**0==1.0" argument at runtime."""

    def __init__(
        self,
        *,
        holder_belief_fact: str | None = None,
        trial_reveal_facts: Mapping[str, str] | None = None,
        rho: float = 0.0,
        multipliers: Mapping[str, float] | None = None,
        min_win_prob: float = 0.2,
        motives: Sequence[Mapping[str, Any]] | None = None,
        gene_affinity: float = 0.0,
        route_category: Mapping[str, str] | None = None,
    ) -> None:
        self.holder_belief_fact = holder_belief_fact
        self.trial_reveal_facts = dict(trial_reveal_facts or {})
        self.rho = float(rho)
        if not 0.0 <= self.rho <= 1.0:
            raise ValueError(f"route.rho must be in [0, 1], got {self.rho!r}")
        self.multipliers = dict(DEFAULT_MULTIPLIERS)
        if multipliers:
            unknown = sorted(set(multipliers) - set(DEFAULT_MULTIPLIERS))
            if unknown:
                raise ValueError(f"route.multipliers has unknown key(s): {unknown}")
            self.multipliers.update({str(k): float(v) for k, v in multipliers.items()})
        self.min_win_prob = float(min_win_prob)
        if not 0.0 <= self.min_win_prob <= 1.0:
            raise ValueError(
                f"route.min_win_prob must be in [0, 1], got {self.min_win_prob!r}"
            )
        # S2 §1: pre-compiled motive rules -- already validated (unique id,
        # known gene key) by load_motives, but a directly-constructed Route
        # (tests) may pass raw dicts without a compiled "predicate" -- accept
        # either.
        self.motives = [
            motive if "predicate" in motive else {
                **motive,
                "predicate": compile_predicate_syntax(motive["when"]),
                "verbs": frozenset(motive["verbs"]),
            }
            for motive in (motives or [])
        ]
        # S2 §4: 0 (default) is exactly S1 -- plan() never prefers a route by
        # gene.
        self.gene_affinity = float(gene_affinity)
        if not 0.0 <= self.gene_affinity <= 1.0:
            raise ValueError(
                f"route.gene_affinity must be in [0, 1], got {self.gene_affinity!r}"
            )
        self.route_category = {
            str(key): str(value) for key, value in (route_category or {}).items()
        }

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "Route":
        return cls(
            holder_belief_fact=cfg.get("holder_belief_fact"),
            trial_reveal_facts=cfg.get("trial_reveal_facts"),
            rho=float(cfg.get("rho", 0.0)),
            multipliers=cfg.get("multipliers"),
            min_win_prob=float(cfg.get("min_win_prob", 0.2)),
            motives=cfg.get("motives"),
            gene_affinity=float(cfg.get("gene_affinity", 0.0)),
            route_category=cfg.get("route_category"),
        )

    @property
    def enabled(self) -> bool:
        """Whether ``multiplier()`` should ever return anything but 1.0 --
        S1 §2's "ρ=0: no multiplication, no meta key" gate."""

        return self.rho > 0.0

    def multiplier(
        self, kind: str, cause: str | None, gene_s: float | None = None
    ) -> float:
        """``b ** rho`` for this decision's ``kind``/``cause`` -- 1.0
        unconditionally when ``not self.enabled`` (rho<=0), so a probe's
        ``Route(rho=0.0)`` never touches a weight even though it still
        calls ``annotate()`` for its own S0-style measurement.

        S2 §3: ``cause == "motive"`` (annotate() only ever sets this when a
        motive actually matched, always alongside a ``gene_s`` reading) uses
        a dynamic base ``b = delta + (1 - delta) * gene_s`` instead of the
        static ``multipliers`` table -- delta is ``multipliers["detour:none"]``
        itself (no separate constant to keep in sync: at gene_s=0 a motive
        is exactly as weighted as an unreasoned detour)."""

        if not self.enabled:
            return 1.0
        if kind == "detour" and cause == "motive":
            delta = self.multipliers.get("detour:none", 0.02)
            s = 0.5 if gene_s is None else gene_s
            base = delta + (1.0 - delta) * s
            return base ** self.rho
        key = kind if kind in ("advance", "prepare", "lost") else f"detour:{cause or 'none'}"
        base = self.multipliers.get(key, 1.0)
        return base ** self.rho

    def multipliers_hash(self) -> str:
        """A short, stable fingerprint of the active multiplier table, for
        run headers/archive metadata (S1 §3) -- so two runs with the same
        rho but a different route.yaml/override are distinguishable without
        dumping the whole table into every header."""

        import hashlib
        import json

        payload = json.dumps(self.multipliers, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def motives_hash(self) -> str | None:
        """S2 §5: a fingerprint of motives.yaml (None when the template has
        none), for run headers/archive metadata alongside gene_affinity --
        multipliers_hash alone can't distinguish two runs whose motive table
        differs."""

        if not self.motives:
            return None
        import hashlib
        import json

        payload = json.dumps(
            [
                {
                    "id": motive["id"],
                    "when": motive["when"],
                    "verbs": sorted(motive["verbs"]),
                    "gene": motive["gene"],
                    "text": motive["text"],
                }
                for motive in self.motives
            ],
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def annotate(
        self,
        subject: Subject,
        world: World,
        present: Sequence[Subject],
        actions: Sequence[Action],
        *,
        turn: int = 0,
        day: int = 0,
        genome: Any = None,
        category_mean: float | None = None,
        targets: Sequence[str] | None = None,
        candidate_genomes: Sequence[Any] | None = None,
    ) -> list[dict[str, Any]]:
        return annotate(
            subject,
            world,
            present,
            actions,
            holder_belief_fact=self.holder_belief_fact,
            trial_reveal_facts=self.trial_reveal_facts,
            min_win_prob=self.min_win_prob,
            turn=turn,
            day=day,
            genome=genome,
            category_mean=category_mean,
            targets=targets,
            candidate_genomes=candidate_genomes,
            motives=self.motives,
            gene_affinity=self.gene_affinity,
            route_category=self.route_category,
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


def _reachable_shortest_path(
    world: World,
    subject: Subject,
    origin: str,
    dest: str,
    excluded: frozenset[str] = frozenset(),
) -> list[Any] | None:
    """Like ``_shortest_route_path``, but gated by ``world._route_allowed``
    (S1 review 1 required fix 2): a route requiring an item the subject
    doesn't currently hold (e.g. 船) is not a real path *right now*. Used
    only by ``annotate()`` to locate the nearest actionable leaf -- crediting
    a move as "advance" toward a leaf the subject has no way to reach yet
    would be wrong (that leaf's zone is simply excluded from the search, per
    plan review 1 §required fix 2)."""

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
            if not world._route_allowed(subject, route):
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
        # S1 §1.4: a deficit of n units costs ceil(n / count) investigate
        # actions, not a flat 1 -- e.g. 3 小判 (count=1/action) is 3 actions,
        # not 1 (previously every deficit, however large, cost exactly 1).
        deficit = max(1, needed - subject.inventory.get(item, 0))
        per_action = max(1, int(source.get("count", 1) or 1))
        gather_actions = math.ceil(deficit / per_action)
        options.append(
            (
                travel_h + float(gather_actions),
                frozenset({("has_item", item)}) | travel_best,
                travel_alt,
            )
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
    *,
    avoid_zone: str | None = None,
    avoid_fight_holder: str | None = None,
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
    does, making an ally-possession check largely moot for this purpose.

    ``avoid_zone``/``avoid_fight_holder`` (E-2, Opus review): while a
    danger-gated route's ``strong_enough`` node is still open, a candidate
    obtained by entering the dangerous holder's own zone, or by fighting
    them directly, is circular -- it presupposes the win the boost step
    exists to avoid -- so it's dropped from consideration outright, not
    merely kept out of ``best``."""

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
        if avoid_zone is not None and ("zone", avoid_zone) in item_best:
            continue
        if avoid_fight_holder is not None and ("win_fight", avoid_fight_holder) in item_best:
            continue
        tags = item_best | frozenset({("has_item", name), ("boost_fight", name)})
        options.append((item_h, tags, item_alt))
    return _combine(options)


def _is_hostile(subject: Subject, target: Subject, world: World) -> bool:
    """Whether the engine's own action_graph permission table actually
    allows ``subject`` to fight ``target`` (S1 §1.3's own check, factored
    out so design judgment D/E can share it without re-deriving it)."""

    return world.permission("fight", world.target_role(subject, target)) >= 1.0


def _believed_win_probability(
    subject: Subject, target: Subject, world: World, present: Sequence[Subject]
) -> float:
    """The same believed-strength-based win probability ``_acquire_from_
    subject``'s own fight-option ``rounds`` estimate uses -- factored out so
    design judgment D (annotate()'s hopeless-fight override) and design
    judgment E (the danger-zone gate below) never compute it differently."""

    mine = strength(subject, world, present)
    theirs = believed_strength(subject, target, world, present)
    scaled = max(-700.0, min(700.0, (mine - theirs) / world.contest["tau"]))
    return 1.0 / (1.0 + math.exp(-scaled))


def _acquire_from_subject(
    item: str,
    holder: Subject,
    subject: Subject,
    world: World,
    visiting: frozenset[Any],
    trial_reveal_facts: Mapping[str, str],
    *,
    is_objective: bool,
    min_win_prob: float = 0.2,
    genome: Any = None,
    category_mean: float | None = None,
    gene_affinity: float = 0.0,
    route_category: Mapping[str, str] | None = None,
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

    # S1 §1.3: only a target the engine's own action_graph permission table
    # actually allows fighting (typically: hostile only) is a fight
    # candidate here -- reusing world.permission/world.target_role (the
    # engine's own relation classification, not a reimplementation) instead
    # of the old "!= ally" check, which let a merely-neutral companion
    # (犬/猿/キジ, permission "restricted") register as a fight target for
    # their held materials, wrongly tagging them boost_fight-worthy.
    fight_allowed = _is_hostile(subject, holder, world)
    probability = (
        _believed_win_probability(subject, holder, world, world.present_subjects(subject.zone))
        if fight_allowed
        else None
    )

    # S1 review 2 design judgment E: for the objective's own *true* holder
    # (never a misattributed/believed one -- "誤認経路は対象外"), hostile and
    # believed-unwinnable, entering their zone at all is a real danger the
    # plan must route around first -- getting strong enough is its own
    # required node, ahead of travel. Scoped to is_objective (never a nested
    # material's holder, out of design E's stated scope).
    danger_gate = (
        is_objective
        and probability is not None
        and probability < min_win_prob
        and holder.id == world.holder(item)
    )

    travel_h, travel_best, travel_alt = _travel(subject, world, holder.zone, visiting, trial_reveal_facts)
    if travel_h == INF:
        return INF, frozenset(), frozenset(), None

    options: list[tuple[float, "Tags", "Tags", str]] = []

    if "fight" in subject.verbs and fight_allowed:
        assert probability is not None
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

    # S2 §4: gene_affinity==0 (default, S1) or a route name absent from
    # route_category (e.g. this is a non-objective material fight, which
    # never has more than one option anyway) falls back to plain h --
    # byte-identical winner/h/best/alt to S1. Only *which option wins* uses
    # h_eff; the h that bubbles up stays that winner's own plain h (S2 §4:
    # "記録する h は素の h のまま").
    if gene_affinity and route_category and genome is not None:

        def _eff_key(option: tuple[float, "Tags", "Tags", str]) -> float:
            h_plain, _best, _alt, name = option
            category = route_category.get(name)
            if category is None:
                return h_plain
            s_route = gene_strength(
                genome, f"category_weight.{category}", category_mean
            )
            return h_plain * (1.0 + gene_affinity * (1.0 - 2.0 * s_route))

        winner = min(options, key=_eff_key)
    else:
        winner = min(options, key=lambda option: option[0])
    route_name = winner[3]
    h = winner[0]
    best_tags: set[tuple[str, Any]] = set(winner[1])
    pool: set[tuple[str, Any]] = set(winner[2])
    for opt_h, opt_best, opt_alt, _name in options:
        pool |= opt_best
        pool |= opt_alt
    best = frozenset(best_tags)
    alt = frozenset(pool) - best

    strength_h: float = INF
    strength_best: "Tags" = frozenset()
    strength_alt: "Tags" = frozenset()
    if is_objective:
        # E-2 (Opus review): while strong_enough is still open (danger_gate),
        # a strength item fetched by fighting the dangerous holder, or found
        # only inside their zone, is circular -- it would require the very
        # win the boost step exists to avoid. Excluded from consideration
        # entirely (not just from best) so it can't leak into alt either.
        strength_h, strength_best, strength_alt = _first_strength_item(
            subject,
            world,
            visiting,
            trial_reveal_facts,
            avoid_zone=holder.zone if danger_gate else None,
            avoid_fight_holder=holder.id if danger_gate else None,
        )
        if strength_h != INF:
            alt |= (strength_best | strength_alt) - best

    if danger_gate:
        # Only the "enter holder.zone" step itself -- the final hop
        # landing there, plus the route-completion tags that only make
        # sense once actually there -- is downgraded to alt (still a
        # reasonable "prepare"), swapped out for the boost step as this
        # level's actual leaf. Any *other* prerequisite already in best
        # (materials/facts/stance needed en route, e.g. 船, or earlier hops
        # that don't yet enter the danger zone) is untouched: it has
        # nothing to do with the danger and stays advance-eligible.
        # _first_strength_item's own h is reused as a monotonic (not exact
        # -- design E accepts this) stand-in for "actions until p>=min_win_
        # prob"; train() itself has no effect size in this symbolic model,
        # hence the flat _BOOST_FALLBACK_COST when no item helps either.
        boost_h = strength_h if strength_h != INF else _BOOST_FALLBACK_COST
        strong_tags = frozenset({("strong_enough", holder.id)}) | strength_best
        entry_tags = frozenset(
            {
                ("zone", holder.zone),
                ("win_fight", holder.id),
                ("route", "fight"),
                ("route", "negotiate"),
                ("boost_fight", holder.id),
                # E-1 (Opus review): a negotiate offer that failed to find a
                # free trade leaves ("stance_ge", holder.id) in best as the
                # lever to raise instead -- that's also an "enter holder's
                # zone/deal with holder" prerequisite and must be demoted
                # alongside the others, or its own _leaf_zone (holder.zone)
                # keeps the danger zone reachable as an advance/prepare leaf.
                ("stance_ge", holder.id),
            }
        )
        demoted = best & entry_tags
        best = (best - entry_tags) | strong_tags
        alt = (alt | demoted | strength_alt) - strong_tags
        h = boost_h + h

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
    min_win_prob: float = 0.2,
    genome: Any = None,
    category_mean: float | None = None,
    gene_affinity: float = 0.0,
    route_category: Mapping[str, str] | None = None,
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
        is_objective=True, min_win_prob=min_win_prob,
        genome=genome, category_mean=category_mean,
        gene_affinity=gene_affinity, route_category=route_category,
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


def _leaf_tags(best_kinds: dict[str, set[Any]], world: World) -> list[tuple[str, Any]]:
    """S1 §1.1: the subset of ``best`` that is *actionable right now* -- its
    own prerequisites (materials for a craft, a trial's stance/item) are
    already satisfied, so it doesn't wait on another still-open ``best``
    node first. investigate/craft/trial/negotiate/fight candidates are
    already leaf-gated by the engine's own candidate generation (a craft
    candidate only exists when ``_can_craft`` is true, a trial candidate
    only when its stance/item are met) -- this is only consulted for
    classifying ``move`` (should leaving here read as advance, or only a
    step toward the *nearest* leaf?)."""

    leaves: list[tuple[str, Any]] = []
    for item in sorted(best_kinds.get("has_item", ())):
        recipe = world.recipes.get(item)
        if recipe is not None:
            if any(material in best_kinds.get("has_item", ()) for material in recipe):
                continue  # blocked: a material is itself still an open need
            # S1 review 1 recommended fix: a craft with a knowledge
            # requirement (e.g. 造船術 for 船) isn't actionable here either
            # while that fact is still an open "knows" need -- mirrors
            # engine.actions._can_craft's own gating.
            required_fact = (world.items.get(item, {}).get("requires") or {}).get(
                "knowledge"
            )
            if required_fact is not None and required_fact in best_kinds.get("knows", ()):
                continue
            leaves.append(("has_item", item))
            continue
        trial = next(
            (t for t in world.trials if (t.get("grants") or {}).get("item") == item),
            None,
        )
        if trial is not None:
            giver = str(trial.get("giver", ""))
            if giver in best_kinds.get("stance_ge", ()):
                continue
            required_item = (trial.get("requires") or {}).get("item")
            if required_item is not None and required_item in best_kinds.get("has_item", ()):
                continue
        leaves.append(("has_item", item))
    for fact_id in sorted(best_kinds.get("knows", ())):
        leaves.append(("knows", fact_id))
    for giver in sorted(best_kinds.get("stance_ge", ())):
        leaves.append(("stance_ge", giver))
    for holder_id in sorted(best_kinds.get("win_fight", ())):
        leaves.append(("win_fight", holder_id))
    # "strong_enough" (design judgment E) is deliberately NOT a leaf here --
    # unlike every other leaf kind, it has no zone of its own, so folding it
    # into this list would make leaves_here trivially true from anywhere,
    # burying real, zoned leaves (e.g. gathering 木材 at 森) under "prepare"
    # even though they have nothing to do with the danger. train()/
    # companion-recruiting read it directly off best_kinds in
    # _match_advance_or_prepare instead; annotate() falls back to it for
    # milestone only when no real leaf is open or reachable.
    return leaves


def _leaf_zone(tag: tuple[str, Any], world: World) -> str | None:
    """Where ``tag`` (one of ``_leaf_tags``'s results) can actually be
    worked on -- None means "wherever the subject already is" (e.g. a
    recipe with no ``craft_zone``)."""

    kind, value = tag
    if kind == "has_item":
        item = str(value)
        definition = world.items.get(item, {})
        if item in world.recipes:
            craft_zone = definition.get("craft_zone")
            return str(craft_zone) if craft_zone is not None else None
        for source in definition.get("sources", []) or []:
            if source.get("type") == "investigate" and source.get("zone"):
                return str(source["zone"])
        trial = next(
            (t for t in world.trials if (t.get("grants") or {}).get("item") == item),
            None,
        )
        if trial is not None:
            giver = world.subjects.get(str(trial.get("giver", "")))
            if giver is not None:
                return giver.zone
        # S1 review 1 recommended fix: an item with no recipe/investigate
        # source/trial grant is one taken by force from whoever holds it
        # (_acquire's "take it from whoever currently holds it" branch) --
        # that's a real zone, not "anywhere".
        holder_id = world.holder(item)
        if holder_id is not None and holder_id in world.subjects:
            return world.subjects[holder_id].zone
        return None
    if kind == "knows":
        for source in world.facts.get(str(value), {}).get("sources", []) or []:
            if source.get("type") != "investigate":
                continue
            if source.get("zone"):
                return str(source["zone"])
            agent = source.get("agent")
            if agent is not None:
                agent_subject = world.subjects.get(str(agent))
                if agent_subject is not None:
                    return agent_subject.zone
        return None
    if kind in ("stance_ge", "win_fight"):
        peer = world.subjects.get(str(value))
        return peer.zone if peer is not None else None
    return None


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
    leaves_here: bool,
    nearest_leaf_path_zones: frozenset[str],
) -> str | None:
    verb = action.verb

    if verb == "move":
        dest = action.meta.get("dest")
        # A misattributed believed holder's zone is never credited as
        # advance/prepare via the zone tag -- it falls through to
        # _detour_cause's "belief".
        if not holder_is_true and dest is not None and dest == believed_holder_zone:
            return None
        # S1 §1.1: something is actionable right where the subject stands
        # (a leaf whose own prerequisites are already met) -- no move can be
        # "advance" (leaving before using it isn't the shortest path), but a
        # move that still sits on the best route is a reasonable "prepare"
        # (e.g. scouting ahead while a trial here could also be done).
        if leaves_here:
            if dest in best_kinds.get("zone", ()) or dest in alt_kinds.get("zone", ()):
                return "prepare"
            return None
        # Nothing is actionable here -- only a move that is itself a hop on
        # the shortest path to the *nearest* leaf's zone is "advance"; a
        # move toward some other, farther leaf is still "prepare" (a
        # reasonable, non-optimal thing to try).
        if dest is not None and dest in nearest_leaf_path_zones:
            return "advance"
        if dest in best_kinds.get("zone", ()) or dest in alt_kinds.get("zone", ()):
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
        # S1 review 2 design judgment E: once "strong_enough" is the open
        # leaf (entering the holder's zone was deferred for being too
        # dangerous), training toward it is the actionable step -- advance,
        # not merely prepare.
        if best_kinds.get("strong_enough"):
            return "advance"
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
            # S1 review 2 design judgment E: recruiting help is itself an
            # advancing step once "strong_enough" is the open leaf.
            return "advance" if best_kinds.get("strong_enough") else "prepare"
        return None

    return None


# S2 design judgment H: rest/withdraw that the engine itself is already
# leaning on for a physical reason -- tired (stamina) or stressed -- but
# that doesn't (yet) meet the existing exhausted/downed/under_threat bar.
# Below these thresholds it used to fall all the way through to
# detour/none, where the caution motive (believed_weaker) could claim it
# even though the real driver was fatigue/stress, not a tactical read of
# the enemy. Both stay cause="body" (multiplier untouched, S1 §2's
# "detour:body": 1.0) -- only the *text* is more specific; caution now
# only ever attaches to a rest/withdraw that isn't covered by either.
REST_FATIGUE_STAMINA_RATIO = 0.6  # tired-but-not-exhausted band above
# exhausted_ratio (0.2 in every shipped world.yaml) that engine.actions's
# rest weight already responds to (rest_weight grows as (1-ratio)**2, so
# it is already climbing well before "exhausted" trips).
WITHDRAW_STRESS_THRESHOLD = 4.0  # engine/actions.py's _withdraw_candidates:
# weight = 0.08 + max(0.0, subject.stress - 4.0) * 0.35 -- 4.0 is exactly
# the stress level below which that term is 0 (weight sits at its 0.08
# floor); any stress above it is what actually pushes withdraw's weight up,
# so it's the minimal stress value that is a genuine driver of the choice.


def _body_or_belief_cause(
    action: Action,
    subject: Subject,
    world: World,
    believed_holder_id: str | None,
    believed_holder_zone: str | None,
) -> tuple[str, str] | tuple[None, None]:
    """The two causes that are meaningful *regardless* of whether the plan
    is reachable (h finite or lost): a physically-forced pause, or acting
    on a confident misattributed belief. Shared by ``_detour_cause`` and
    ``_lost_cause`` (S1 §1.2) -- 迷子 (h==inf) still knows *why* the
    subject rested or attacked the wrong target, even though it has
    nothing to say about the plan itself.

    Returns ``(cause, reason)``: ``reason`` only ever distinguishes which
    body sub-condition fired (for text), never affects the multiplier
    lookup (still keyed on ``cause`` alone)."""

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
            return "body", "existing"
        if verb == "rest" and ratio <= REST_FATIGUE_STAMINA_RATIO:
            return "body", "rest_fatigue"
        if verb == "withdraw" and subject.stress > WITHDRAW_STRESS_THRESHOLD:
            return "body", "withdraw_stress"

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
        return "belief", None

    if verb == "move" and misattributed:
        dest = action.meta.get("dest")
        if believed_holder_zone is not None and dest == believed_holder_zone:
            return "belief", None

    return None, None


def _detour_cause(
    action: Action,
    subject: Subject,
    world: World,
    believed_holder_id: str | None,
    believed_holder_zone: str | None,
) -> tuple[str, str | None]:
    cause, reason = _body_or_belief_cause(
        action, subject, world, believed_holder_id, believed_holder_zone
    )
    if cause is not None:
        return cause, reason

    # S1 §1.2: the h==inf branch that used to live here is gone -- an
    # unreachable plan is now its own kind, "lost" (see _lost_cause), not a
    # detour cause. This branch only ever fires with a finite h.
    if action.verb == "observe" and action.meta.get("target") == believed_holder_id:
        return "ignorance", None

    return "none", None


def _lost_cause(
    action: Action,
    subject: Subject,
    world: World,
    believed_holder_id: str | None,
    believed_holder_zone: str | None,
) -> tuple[str, str | None]:
    """S1 §1.2: when h is unreachable (None/inf), the route layer has
    nothing to say about the plan -- kind becomes "lost" and m_route is a
    flat 1.0 (no modulation) regardless of cause. body/belief still get
    recorded (for the text/report), but "ignorance" is not: it would be
    redundant with "lost" itself (h finite ignorance -- observing the
    believed holder -- is still a genuine detour cause, handled by
    ``_detour_cause``, never reached when lost)."""

    cause, reason = _body_or_belief_cause(
        action, subject, world, believed_holder_id, believed_holder_zone
    )
    return (cause, reason) if cause is not None else ("none", None)


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
    # S3.5 §2: worded as the subject's want ("...てほしくて"/"...ようと"), not
    # a claimed outcome -- an LLM given "仲間を増やすため{target}に品を渡し
    # た" tended to write it up as "仲間にした" (recruiting *achieved*),
    # which the fact log never claims (recruiting only succeeds once stance
    # crosses companionship's threshold, elsewhere in the log if at all).
    "share_knowledge": "{target}に仲間として頼れると分かってほしくて話した",
    "give_item": "{target}に仲間に加わってほしくて品を渡した",
    "persuade": "{target}に仲間に加わってほしくて説得した",
    "pledge": "{target}と支え合おうと誓いを結んだ",
}

def _advance_text(
    action: Action,
    subject: Subject,
    world: World,
    kinds: dict[str, set[Any]],
    *,
    believed_holder_id: str | None = None,
) -> str:
    """Built from ``kinds`` (the tag pool an action actually matched against
    -- ``best`` for an "advance" result, ``alt`` for "prepare"). S3.5 §1:
    a "move" is always textualized from the milestone that made it advance/
    prepare in the first place (which node in ``kinds`` is actually open),
    never the bare "移動して近づいた" while a real purpose can be named --
    that generic text is now the last resort, not the common case."""

    verb = action.verb

    if verb == "move":
        dest = action.meta.get("dest")
        for item in sorted(kinds.get("has_item", ())):
            definition = world.items.get(item, {})
            if str(definition.get("craft_zone")) == dest:
                return f"{item}を作るため{dest}へ向かった"
        # trial / stance_ge:Y -- raising stance with Y (a trial giver or a
        # negotiate/companion target) is "meeting them", never phrased as a
        # fight. Not zone-gated to ``dest`` (unlike craft above): "会うため
        # 向かった" is the purpose of the whole trip, true on any hop of it,
        # not a claim that Y is standing at this particular waypoint.
        for holder_id in sorted(kinds.get("stance_ge", ())):
            return f"{holder_id}と会うため{dest}へ向かった"
        # route:fight / win_fight:H
        for holder_id in sorted(kinds.get("win_fight", ())):
            return f"{holder_id}を倒すため{dest}へ向かった"
        # route:negotiate -- the winning plan settles for talking, not
        # fighting, so name that instead of the fight text above.
        if "negotiate" in kinds.get("route", ()) and believed_holder_id is not None:
            return f"{believed_holder_id}と話をつけるため{dest}へ向かった"
        # goal:deliver -- the goal item is already held; the only leaf left
        # is bringing it home (also not zone-gated: any hop homeward
        # qualifies, not just the final one landing in deliver_to).
        if subject.goal.target is not None and subject.has_item(subject.goal.target):
            return f"宝を持ち帰るため{dest}へ向かった"
        # 2026-09-24 review 2, recommended fix 5 / S3.5 §1 follow-up: name
        # what the trip is still for (a still-needed material/fact) rather
        # than falling straight to the generic "近づいた" whenever no
        # holder/craft_zone matched -- including a waypoint hop whose *own*
        # zone isn't the item's source (dest is just one hop closer to it;
        # "手に入れるため" states the purpose, not the current location).
        for item in sorted(kinds.get("has_item", ())):
            return f"{item}を手に入れるため{dest}へ向かった"
        for fact_id in sorted(kinds.get("knows", ())):
            return f"{fact_id}について調べるため{dest}へ向かった"
        # companion -- no leaf tag covers recruiting (annotate()'s move
        # classifier reads "strong_enough" for that instead, see
        # _leaf_tags's docstring), so a move toward a plausible recruit
        # standing at dest is named directly here.
        companion = next(
            (
                peer
                for peer in sorted(world.subjects.values(), key=lambda s: s.id)
                if peer.zone == dest and _is_companion_candidate(subject, peer, world)
            ),
            None,
        )
        if companion is not None:
            return f"仲間と合流するため{dest}へ向かった"
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
        # S1 review 2 design judgment E: named text once training is
        # specifically toward standing up to a too-strong holder.
        strong_targets = sorted(kinds.get("strong_enough", ()), key=str)
        if strong_targets:
            return f"{strong_targets[0]}に立ち向かえるだけの力をつけるため鍛えた"
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
        return _RELATION_VERB_TEXT[verb].format(target=target_id)

    return f"{verb}で先へ進んだ"


def _body_text(reason: str | None) -> str:
    if reason == "rest_fatigue":
        return "疲れが溜まっていたので休んだ"
    if reason == "withdraw_stress":
        return "心労がかさみ、ひとまず気を落ち着けた"
    return "疲労や危険のため一旦引いた"


def _detour_text(cause: str, reason: str | None = None) -> str:
    if cause == "body":
        return _body_text(reason)
    if cause == "ignorance":
        return "次の手が分からず調べた"
    if cause == "belief":
        return "誤った思い込みに基づいて動いた"
    return "特に理由のない寄り道"


def _lost_text(cause: str, reason: str | None = None) -> str:
    if cause == "body":
        return _body_text(reason)
    if cause == "belief":
        return "誤った思い込みに基づいて動いた"
    return "先の見えないまま動いた"


def annotate(
    subject: Subject,
    world: World,
    present: Sequence[Subject],
    actions: Sequence[Action],
    *,
    holder_belief_fact: str | None = None,
    trial_reveal_facts: Mapping[str, str] | None = None,
    min_win_prob: float = 0.2,
    turn: int = 0,
    day: int = 0,
    genome: Any = None,
    category_mean: float | None = None,
    targets: Sequence[str] | None = None,
    candidate_genomes: Sequence[Any] | None = None,
    motives: Sequence[Mapping[str, Any]] | None = None,
    gene_affinity: float = 0.0,
    route_category: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """One annotation dict per action in ``actions``, in order. Adds a few
    reporting-only fields (``zone``/``inventory``/``allies``) beyond the
    original plan so scripts/route_probe.py's report doesn't need to
    reconstruct per-turn state from partial deltas.

    S2: ``genome``/``category_mean`` (§4) bias plan()'s own fight-vs-
    negotiate route choice by gene, before any candidate is classified.
    ``motives``/``targets`` (§1-3) run as a single pass *after* every
    candidate's kind/cause is otherwise decided (below) -- only a candidate
    that already came out ``detour``/``cause=="none"`` (S0/S1's "no reason"
    bucket) is ever eligible to be relabelled ``cause="motive"``; advance/
    prepare/body/belief/ignorance/lost are never touched. ``targets`` (one
    candidate target id per action, defaulting to ``subject.id`` when
    omitted) must be derived the same way ``gapengine.policy``'s own
    candidate rules bind ``target`` -- Policy passes its own
    ``_candidate_target`` list in, so a motive's ``when`` (e.g. ``stance(self,
    target) >= 0.6``) means the same thing a policy rule's ``when`` does."""

    state = plan(
        subject,
        world,
        holder_belief_fact=holder_belief_fact,
        trial_reveal_facts=trial_reveal_facts,
        min_win_prob=min_win_prob,
        genome=genome,
        category_mean=category_mean,
        gene_affinity=gene_affinity,
        route_category=route_category,
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

    # S1 §1.2: an unreachable plan (h is None -- no target/holder at all --
    # or INF -- every considered branch failed) has nothing to say; every
    # candidate is "lost" and m_route never modulates it.
    is_lost = h_before is None or h_before == INF

    # S1 §1.1: precompute once per decision point (not per candidate) which
    # leaves are actionable right here, and -- if none are -- the nearest
    # leaf's zone and the shortest path to it (any hop on that path is
    # "advance" for a move; every other best/alt zone is only "prepare").
    leaves_here = False
    nearest_leaf_path_zones: frozenset[str] = frozenset()
    milestone: str | None = None
    if not is_lost:
        leaves = _leaf_tags(best_kinds, world)
        leaf_zones = [(tag, _leaf_zone(tag, world)) for tag in leaves]
        # _leaf_tags only ever covers has_item/knows/stance_ge/win_fight
        # tags. A "fight" route's completion is already covered that way
        # (_acquire_from_subject always pairs "route":"fight" with a
        # "win_fight" tag for the same holder) -- but "negotiate" adds no
        # such tag for the completing action itself, only (optionally) for
        # the offer being built. When the winning plan's last remaining
        # step is negotiating with the true holder directly -- nothing else
        # (has_item/knows/stance_ge) still open on ``best`` -- that step is
        # itself a leaf, located at the holder's own zone (S1 review 1
        # required fix 1: gated on "the rest is done", not unconditional --
        # otherwise a plan that still needs, say, an offer item first
        # wrongly treated "go negotiate" as already actionable, even while
        # something else on the winning route was still unmet).
        other_open = bool(
            best_kinds.get("has_item")
            or best_kinds.get("knows")
            or best_kinds.get("stance_ge")
        )
        if (
            "negotiate" in best_kinds.get("route", ())
            and believed_holder_zone is not None
            and not other_open
        ):
            leaf_zones.append((("route", "negotiate"), believed_holder_zone))
        # S1 review 1 required fix 3: once the goal item is already held,
        # ``best`` (built by ``_travel`` for the delivery leg) only ever
        # carries ("zone", ...) hop tags -- ``_leaf_tags`` has nothing to
        # say about it, so without this the delivery zone was never a leaf
        # and every homeward hop read as "prepare" at best.
        if (
            subject.goal.target is not None
            and subject.has_item(subject.goal.target)
            and subject.goal.deliver_to is not None
        ):
            leaf_zones.append((("goal", "deliver"), subject.goal.deliver_to))
        here_leaves = [
            (tag, leaf_zone)
            for tag, leaf_zone in leaf_zones
            if leaf_zone is None or leaf_zone == zone
        ]
        leaves_here = bool(here_leaves)
        if leaves_here:
            # S1 review 1 required fix 4: the recorded milestone is the
            # same "next node" the classifier itself is using -- the
            # nearest actionable leaf, current-zone leaves first -- not the
            # old plan()-only _milestone(best).
            tag, _leaf_zone_here = min(here_leaves, key=lambda pair: str(pair[0]))
            milestone = f"{tag[0]}:{tag[1]}"
        else:
            excluded = frozenset(world._excluded_zones(subject))
            nearest: tuple[int, str, tuple[str, Any], list[Any]] | None = None
            for tag, leaf_zone in leaf_zones:
                if leaf_zone is None:
                    continue
                # S1 review 1 required fix 2: gated by requires_item (e.g.
                # 船) -- a leaf across a route the subject can't actually
                # cross yet is not "nearest" by an unreachable hop count.
                edges = _reachable_shortest_path(world, subject, zone, leaf_zone, excluded)
                if edges is None:
                    continue
                candidate = (len(edges), str(tag), tag, edges)
                if nearest is None or candidate[:2] < nearest[:2]:
                    nearest = candidate
            if nearest is not None:
                nearest_leaf_path_zones = frozenset(
                    route.destination for route in nearest[3]
                )
                milestone = f"{nearest[2][0]}:{nearest[2][1]}"
            elif best_kinds.get("strong_enough"):
                # S1 review 2 design judgment E: no real, zoned leaf is open
                # or reachable right now -- the only remaining need is
                # getting strong enough, itself doable from anywhere.
                milestone = f"strong_enough:{sorted(best_kinds['strong_enough'])[0]}"

    # S1 review 2 design judgment E: standing right in a too-strong hostile
    # holder's own zone (rather than still approaching it) flips the
    # sensible move -- leaving is the advancing step, not staying. Scoped
    # to the true, correctly-identified holder ("誤認経路は対象外"), and
    # skipped once the subject *is* that holder (post-acquisition delivery,
    # where believed_holder_id trivially becomes subject.id).
    danger_zone_escape = False
    if (
        not is_lost
        and holder_is_true
        and believed_holder_id is not None
        and believed_holder_id != subject.id
        and zone == believed_holder_zone
    ):
        holder_subject = world.subjects.get(believed_holder_id)
        if holder_subject is not None and _is_hostile(subject, holder_subject, world):
            danger_zone_escape = (
                _believed_win_probability(subject, holder_subject, world, present) < min_win_prob
            )

    # S2 §1 (bravado): the same believed win probability design judgment D
    # uses below, computed once (identical for every candidate this
    # decision point) so the post-loop motive pass can tell a genuinely
    # hopeless fight against the *true* holder apart from an unrelated
    # hostile-target fight that also happened to fall through to
    # detour/none (which _match_advance_or_prepare never classifies for a
    # non-holder target).
    true_holder_subject = (
        world.subjects.get(believed_holder_id)
        if not is_lost and believed_holder_id is not None and holder_is_true
        else None
    )
    holder_win_probability = (
        _believed_win_probability(subject, true_holder_subject, world, present)
        if true_holder_subject is not None
        else None
    )
    holder_hopeless = (
        holder_win_probability is not None and holder_win_probability < min_win_prob
    )

    # S2 design judgment G: indices forced to detour/none by judgment F
    # (moving into the too-strong true holder's own zone, still hopeless) --
    # bravado's ``when`` treats these the same as D's hopeless fight, since
    # F fires on the same true-holder/hostile/win-probability gate
    # (danger_gate in compute_route_h) that produced holder_hopeless above.
    danger_zone_entries: dict[int, str] = {}

    results: list[dict[str, Any]] = []
    for action in actions:
        base = {
            "plan": route_name,
            "milestone": milestone,
            "zone": zone,
            "inventory": inventory,
            "allies": allies,
        }

        if is_lost:
            cause, reason = _lost_cause(
                action, subject, world, believed_holder_id, believed_holder_zone
            )
            results.append(
                {
                    **base,
                    "kind": "lost",
                    "cause": cause,
                    "h": [None, None],
                    "text": _lost_text(cause, reason),
                }
            )
            continue

        # S1 review 2 design judgment E: at the true, hostile holder's own
        # zone with a hopeless believed win probability, leaving is the
        # advancing step. E-3 (Opus review): only an actual "move" leaves
        # the zone -- engine.verbs._withdraw only lowers stress and never
        # relocates the subject (engine/verbs.py), so treating "withdraw"
        # as this same escape/advance was crediting an action that leaves
        # the subject standing right where they started. withdraw instead
        # falls through to the ordinary classification below, which (via
        # _body_or_belief_cause) already reads it as detour/body when
        # exhausted/downed/under_threat, and detour/none otherwise --
        # staying to do nothing (a voluntary "rest") likewise already
        # resolves to detour/none via the ordinary fallback, so neither
        # needs a special case here.
        if danger_zone_escape and action.verb == "move":
            results.append(
                {
                    **base,
                    "kind": "advance",
                    "cause": None,
                    "h": [_finite_or_none(h_before), _finite_or_none(h_before)],
                    "text": f"まだ{believed_holder_id}には敵わないので{zone}を離れた",
                }
            )
            continue

        # Design judgment D (Opus review 1): fight/sabotage/neutralize aimed
        # at the true holder, when the subject's own believed win
        # probability (same p -- believed_strength-based -- that
        # _acquire_from_subject uses for h) is below min_win_prob, is a
        # hopeless gesture -- forced to detour/none regardless of whether
        # it happens to sit on best (fight) or alt. Misattributed targets
        # are untouched (those already resolve to detour/belief, never
        # punished).
        if action.verb in ("fight", "sabotage", "neutralize"):
            target = action.meta.get("target")
            if target is None and action.verb == "fight" and action.args:
                target = action.args[0]
            if (
                target == believed_holder_id
                and holder_is_true
                and holder_win_probability is not None
            ):
                if holder_win_probability < min_win_prob:
                    results.append(
                        {
                            **base,
                            "kind": "detour",
                            "cause": "none",
                            "h": [_finite_or_none(h_before), _finite_or_none(h_before)],
                            "text": "勝ち目の薄い無謀な挑戦",
                        }
                    )
                    continue

        # Design judgment F (Opus review): while strong_enough is still
        # open, moving *into* a too-strong hostile holder's own zone is
        # never a reasonable "prepare" either (E-1/E-2 above already keep
        # it out of "advance") -- it's the reckless approach the boost step
        # exists to delay, so it's forced to detour/none here, the same
        # pattern as judgment D's hopeless-fight override just above. The
        # reverse move (leaving that zone) is untouched -- handled
        # separately by danger_zone_escape/E.
        if action.verb == "move" and best_kinds.get("strong_enough"):
            dest = action.meta.get("dest")
            danger_holder = next(
                (
                    holder_id
                    for holder_id in sorted(best_kinds["strong_enough"])
                    if holder_id in world.subjects and world.subjects[holder_id].zone == dest
                ),
                None,
            )
            if danger_holder is not None:
                results.append(
                    {
                        **base,
                        "kind": "detour",
                        "cause": "none",
                        "h": [_finite_or_none(h_before), _finite_or_none(h_before)],
                        "text": f"まだ敵わないのに{danger_holder}の元へ向かう無謀な行動",
                    }
                )
                danger_zone_entries[len(results) - 1] = danger_holder
                continue

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
            leaves_here,
            nearest_leaf_path_zones,
        )
        if matched is not None:
            # review 2, recommended fix 2: only an *advancing* action is
            # credited with reducing h -- prepare/detour keep h_before
            # unchanged, since neither literally completes a node on the
            # winning plan.
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
                        action,
                        subject,
                        world,
                        best_kinds if matched == "advance" else alt_kinds,
                        believed_holder_id=believed_holder_id,
                    ),
                }
            )
            continue

        cause, reason = _detour_cause(
            action, subject, world, believed_holder_id, believed_holder_zone
        )
        results.append(
            {
                **base,
                "kind": "detour",
                "cause": cause,
                "h": [_finite_or_none(h_before), _finite_or_none(h_before)],
                "text": _detour_text(cause, reason),
            }
        )

    # S2 §1-3: a single pass over the *finished* results, after every other
    # kind/cause decision above (D's hopeless-fight override, F's
    # danger-zone-entry override, and the ordinary _detour_cause fallback
    # all funnel into the same kind="detour"/cause="none" shape) -- only
    # that shape is ever eligible for a motive. First matching motive (verb
    # in motive["verbs"] and its predicate true) in motives.yaml's own
    # order wins; no match leaves the candidate exactly as S0/S1 produced
    # it ("特に理由のない寄り道").
    if motives:
        for index, action in enumerate(actions):
            result = results[index]
            if result["kind"] != "detour" or result["cause"] != "none":
                continue
            target = (
                targets[index]
                if targets is not None and index < len(targets)
                else subject.id
            )
            # Design judgment G: a move forced to detour/none by judgment F
            # (entering the too-strong true holder's own zone while still
            # hopeless) has no fight-style target of its own (a move's
            # target defaults to self) -- rebind it to that same holder so
            # both the bravado predicate and its {target} text substitution
            # read it as "the target holder", exactly like D's hopeless
            # fight does.
            danger_entry_holder = danger_zone_entries.get(index)
            if danger_entry_holder is not None:
                target = danger_entry_holder
            namespace = world.namespace(
                subject,
                present,
                turn=turn,
                day=day,
                bindings={
                    "target": target,
                    # S2 §1 (bravado): lets motives.yaml distinguish a
                    # hopeless fight against the true goal holder (design
                    # judgment D's own override) from an unrelated
                    # hostile-target fight that also fell through to
                    # detour/none.
                    "is_target_holder": (
                        (target == believed_holder_id and holder_is_true)
                        or danger_entry_holder is not None
                    ),
                    "holder_hopeless": holder_hopeless,
                },
            )
            for motive in motives:
                if action.verb not in motive["verbs"]:
                    continue
                if not motive["predicate"].evaluate(namespace):
                    continue
                motive_genome = (
                    candidate_genomes[index]
                    if candidate_genomes is not None and index < len(candidate_genomes)
                    else genome
                )
                gene_s = (
                    gene_strength(motive_genome, motive["gene"], category_mean)
                    if motive_genome is not None
                    else 0.5
                )
                fill = result["zone"] if target == subject.id else target
                text = motive["text"].replace("{target}", str(fill)).replace(
                    "{zone}", str(result["zone"])
                )
                result["cause"] = "motive"
                result["motive"] = motive["id"]
                result["text"] = text
                result["gene_s"] = round(gene_s, 6)
                break

    return results


def _finite_or_none(value: float | None) -> float | None:
    if value is None or value == INF:
        return None
    return round(value, 6)
