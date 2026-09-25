---
ja_rev: "589f434c8ed6"
---
# Route Layer

The protagonist basically walks the straightest path to the ending. Straying from it (a detour) should, in principle, have a cause — a bodily need, missing information, a mistaken belief, a feeling. The **route layer** classifies each of the protagonist's GA-generated decisions by what it means relative to the ending, reins in unreasoned detours with weight ρ, and lets a motive table supply reasons for the ones that do have them. It leaves the seven layers themselves untouched, acting as one more multiplier when [Policy](genome.md) decides a candidate's weight.

Only genres with a `route.yaml` support it. Currently that's just 桃太郎＋2 (Peach Boy+2, `momotaro_plus2`) — other genres have no route layer at all, and it doesn't appear on screen for them.

## Classifying actions

Each of the protagonist's decisions is classified against "the shortest plan to the ending," as follows. In a GA experiment (screen or CLI), this classification only happens — and gets recorded per decision — when run with ρ>0. At ρ=0 (the default), the route layer doesn't run at all, and nothing is classified or recorded (measurement-only scripts like `scripts/route_probe.py`/`scripts/route_eval.py` always classify, regardless of ρ). This same record is later used as the reason badge in explanations.

| Classification | Meaning |
|---|---|
| Advance | An action that actually moves the planned route forward |
| Prepare | Not the optimal choice, but a sensible one (laying groundwork for another route, recruiting an ally, etc.) |
| Detour ⟨body⟩ | An action driven by the protagonist's own bodily needs — fatigue, injury (e.g. resting). Never penalized |
| Detour ⟨ignorance⟩ | An action exploring something the protagonist doesn't yet know (investigating, observing) |
| Detour ⟨belief⟩ | An action based on information the protagonist mistakenly believes. It makes sense from their own point of view |
| Detour ⟨motive⟩ | An action matching an entry in the motive table below. How it's treated depends on the strength (genome) of that motive |
| Detour ⟨no reason⟩ | A detour from the plan that matches none of the above |
| Lost | A state where no plan to the ending exists at all (h is infinite). The route layer makes no judgment here |

The premise throughout is "judge only from the protagonist's own knowledge." The world's hidden truth (`world.truth`) is never read — only what the protagonist actually knows or believes (a target's current location, relationships, believed strength, etc.) is used. The protagonist never acts as if it can see through information it was never given.

## Weight ρ (how hard to rein in detours)

Each classification has a base multiplier b, and `m_route = b ** ρ` is multiplied together with the other multipliers that come from the [genome](genome.md) (m_cat, m_risk, m_stance, m_nov, and m_rat too for genres using κ) to produce the final weight. ρ decides both whether a candidate gets classified (advance / prepare / each detour cause / lost) at all, and how much that classification affects the weight.

| Classification | Base multiplier b | Meaning at ρ=1 |
|---|---|---|
| Advance | 1.0 | Unchanged |
| Prepare | 0.5 | Halved |
| Detour ⟨body⟩ | 1.0 | Never penalized (a bodily need is always legitimate) |
| Detour ⟨belief⟩ | 1.0 | Never penalized (the protagonist is still pursuing a plan that makes sense to them) |
| Detour ⟨ignorance⟩ | 0.5 | Halved |
| Detour ⟨no reason⟩ | 0.02 | Almost never chosen |
| Lost | 1.0 | No judgment (a state the route layer has nothing to say about) |

At ρ=0, the route layer doesn't run at all (a GA experiment carries no route record whatsoever), so output stays byte-identical to an existing run. The higher ρ goes, the less likely unreasoned detours are to be chosen. The default is 0 across the engine, CLI, and template (`route.yaml`'s `rho`). In run settings on screen, only the new-config screen and the "1-click run" button (for a template with a `route.yaml`) default ρ to 1.0. Set this from [05. Route](../usage/run-settings.md#05) in run settings.

## The motive table (motives.yaml) — detours with a reason

Only candidates that would otherwise become "detour ⟨no reason⟩" are checked against the motive table (advance, prepare, body, belief, ignorance, and lost actions are never eligible). The motive table is a prioritized list of rules per genre, at `templates/<genre>/motives.yaml` — the first entry (reading top to bottom) that matches wins.

The five motives defined for 桃太郎＋2 (Peach Boy+2):

| Motive | Condition (example) | Applies to | Genome that matters |
|---|---|---|---|
| Caring for an ally | High affinity toward the target | give_item, share_knowledge, persuade, pledge | Relationship-building (category III) weight |
| Bravado | Standing up to a target despite low odds | fight, sabotage, neutralize, moving into their zone | Risk tolerance |
| Grudge | Low affinity toward the target | fight, sabotage, mislead | Stance-shift bias (stronger the more it points away) |
| Curiosity | Always | investigate, observe | Novelty drive |
| Caution | Believing oneself weaker than the target | rest, withdraw | Risk tolerance (stronger the lower it is) |

Each entry carries a target, a condition, and the motive's reason text — for bravado, for example, the recorded reason is "勝ち目が薄いと知りながら、{target}に立ち向かわずにいられなかった" (unable to stop confronting {target}, even knowing the odds were slim).

### Genome strength changes how much it's chosen

A detour matched to a motive doesn't automatically get the unconditional pass (b=1.0) that body/belief detours do. Instead, its base multiplier varies continuously with the strength `gene_s` (0–1) of the gene the motive names:

```
base = delta + (1 - delta) * gene_s   (delta is the "no reason" multiplier, 0.02)
m_route = base ** rho
```

For an individual with a weak lean toward that gene (gene_s=0), matching the motive gets treated almost the same as "no reason." For one with a strong lean (gene_s=1), it's treated the same as body/belief (b=1.0, unpenalized). Simply appearing in the motive table doesn't grant free rein to detour — without a genome strong enough for that motive, the detour is still reined in.

## Choosing a route by genome (gene_affinity)

Setting `route.yaml`'s `gene_affinity` (default 0) and `route_category` (a mapping from route name to action category) lets the genome's corresponding category weight slightly tilt which of several ways to take the goal item from its holder (fight vs. negotiate, say) counts as "the best plan." At `gene_affinity=0` (the default) the lowest-cost option is always the best one. Raising it makes an individual with a stronger weight in the matching category more likely to see that route as "best" (the recorded h itself stays plain; only the choice of best route is affected). 桃太郎＋2 (Peach Boy+2)'s `route.yaml` sets `gene_affinity: 0.5` and `route_category: {fight: I, negotiate: III}`.

## Feeding into synopsis/text generation

The reason text for every protagonist decision that carries a route record — advance and prepare included, regardless of classification or cause — is passed one at a time to the LLM that writes the synopsis/text, as "the action's reason." A detour ⟨no reason⟩ has no reason text; instead it's spelled out explicitly as "(no clear reason was recorded)." (Lost gets the fixed text "acted without any prospect of a way forward," except when its cause is body or belief.) The generation prompt itself is instructed not to invent an unrecorded motive for an action with no recorded reason. On screen, the same reason text appears in [Read Results](../usage/read-results.md)'s timeline, four-field panel, and the breakdown at the top of the cell page.
