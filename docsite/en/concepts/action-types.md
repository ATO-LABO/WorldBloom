---
ja_rev: "f8d917944f66"
---
# Action Types I–VI

The actions a character can take are sorted into six categories, not by the verb's name but by "what the action actually does." This classification is the exact unit the [genome](genome.md)'s category weights `category_weight` correspond to, and it's also what the [QD Map](qd-map.md)'s vertical axis, "leading category," uses.

## The six categories

| Category | Short name | Representative actions |
|---|---|---|
| I | Power (self-reinforcement / weakening) | `train` (training), `fight` (confrontation), `steal_credit` (taking credit), `sabotage` (sabotage), `neutralize` (weakness-based neutralization), item acquisition via `investigate`/`craft` |
| II | Information (perception & foreshadowing) | `observe` (observation/reconnaissance), `plant` (planting foreshadowing), `payoff` (resolving foreshadowing), `mislead` (misdirection), `confront` (exposure), `rethink` (re-scanning beliefs) |
| III | Social (relationship building) | `share_knowledge` (sharing a fact), `give_item` (gifting), `persuade` (persuasion), `pledge` (a contract/vow), `grand_gesture` (a large gift or public act), `trial` (passing a trial) |
| IV | Identity | `disguise` (disguise), or a different agent posing as a false protagonist/taking credit toward the same objective, via `steal_credit` |
| V | Movement & stalling | `move` (movement, including crossing borders), a challenge from repeated `craft`/`investigate`, `pursue_target` (pursuit/flight), `rest`/`withdraw` (stalling/introspection), `downed`→`revive`/`rescue` (pseudo death and revival) |
| VI | External & reciprocation | `scheduled_events`/`daily_events` (external events no agent chooses), `donate` (reward/giving back) |

The short names (Power, Information, Social, Identity, Movement & stalling, External & reciprocation) are the names used in the static viewer (`scripts/export_static.py`) and graph display. The design docs also call them "I self-reinforcement," "II perception & foreshadowing," "III relationship building," "IV Identity," "V movement & stalling," and "VI external intervention."

## Classification is per execution context, not per verb

The same verb can fall into a different category depending on the situation at execution time. For example, `investigate` is category I (self-reinforcement) in a `gather` context, but II (perception & foreshadowing) otherwise. `move` is category V (movement & stalling) in a `crossing` (identity-passage) context, but outside the QD grid (`category: null`) otherwise. This classification is recorded at the moment the action executes (the decision event) and is never reclassified later when computing the QD descriptor.

## Premises and the action graph

Each genre's template declares, in `templates/<genre>/action_graph.yaml`, the premises (an AND condition, `edges:`) for each action and its allowance level per target role (`hostile`/`neutral`/`ally`) in that genre (`allow` / `restricted` / `deny`). A candidate action whose premises aren't met is never even enumerated, so a causal order like "observe to learn the weakness, then neutralize" is structurally guaranteed at the point candidates are listed, not verified after the fact. `restricted` doesn't exclude a candidate — it stays in the pool with its weight scaled down by `restricted_weight` (set per template; e.g. `momotaro_plus2` uses 0.15).

## Genres use different numbers of categories

The range of categories used on the QD grid's vertical axis is declared in `templates/<genre>/qd.yaml`'s `categories`. In the bundled templates, the number of categories used actually differs by genre:

| Genre | Categories used |
|---|---|
| Momotaro (`momotaro_plus2`) | I, II, III, IV, V, VI (all six) |
| Detective (`detective`) | I, II, III only |
| Romance (`romance`) | I, II, III only |

Genres like detective and romance, which barely use identity swaps or external intervention, narrow the grid's axes to match the actual range of actions those genres cover.
