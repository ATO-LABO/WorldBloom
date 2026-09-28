---
ja_rev: "8a1bd4eb0e6f"
---
# Personality Growth (plasticity)

The nine scalars in the [Genome](genome.md) are an inborn policy that the GA evolves across generations. **Personality growth** is a separate, opt-in (default off) mechanism: within a single run, the protagonist's personality can shift slightly in response to what actually happened in the story (wins, losses, allies gained, betrayal, and so on) — a change acquired after birth, not an inherited one.

## Inborn vs. acquired

| | What changes it | When it changes | Does it inherit? |
|---|---|---|---|
| Genome (inborn, plasticity included) | GA crossover/mutation | Across generations | Yes |
| Personality growth (acquired, `acquired`) | Events that happen during the run (wins, losses, allies, betrayal, …) | Within that one run | No (`Policy.acquired` starts at `{}` for every fresh run and is never carried into the next generation) |

The [Genome](genome.md) design principle — "only the policy evolves" — is unchanged. Personality growth is a separate layer that governs how much that policy can move *within one run*, driven by what the character experiences. `plasticity` (how much that policy *can* move) is itself part of the genome, just like the other nine, and evolves across generations the same way. What inherits is this **capacity** — how easily experience moves the character — not the shift actually accumulated during a run.

## The plasticity gene { #plasticity }

`plasticity` ([0, 1], neutral value 0) is a 10th scalar added to the genome. Unlike the other nine, though, **it stays exactly 0 unless a caller explicitly opts in**.

- **Initialization** (`Genome.random`): only when passed `plastic=True` does it draw a random value (consuming randomness); otherwise (the default) it's 0 and consumes no randomness for this scalar at all
- **Crossover** (`Genome.crossover`): only when `plastic=True`, it inherits either parent's value with 50% probability, same as the other nine values (six category weights and three scalars) (always 0 when `plastic=False`)
- **Mutation** (`Genome.mutate`): only when `plastic=True`, with probability p (default 0.3) it adds Gaussian noise (σ=0.1), then clips to [0, 1] (always reset to 0 when `plastic=False`)
- **Carrying over from a previous experiment** (run settings' evolution.seed_genomes): even if the source experiment's plasticity was nonzero, **it's forced back to 0 if personality growth is off for this run** (`gapengine/seed_genomes.py`'s `reconcile`). If this run has it on but the source didn't, it stays carried over as 0.

A genome with `plasticity=0` has every mechanism below fully disabled, producing a byte-identical result to a run before this feature existed.

## Outcome-scope rules and growth events

A genre's `rules.yaml` can declare rules with `scope: outcome` (a separate bucket from the existing `candidate`/`turn` scopes — it never mixes into the ordinary candidate-weighting pass). These rules are evaluated *after* an action happens, and each match adds `plasticity × adjust` to that run's `Policy.acquired` (an accumulating shift on the genome), via `gapengine.policy.Policy.observe`. Outcome-scope rules are also excluded from the set meta-evolution can toggle rule-by-rule (`rule_bits`, `gapengine/evolve.py`'s `_rule_ids`) — only `candidate`/`turn`-scope rules can ever be turned on or off that way.

`observe` runs each time a decision or event row is written, for *any* subject with a Policy (genome), not just the protagonist. By default the antagonist has no Policy (`policy=None`), so only the protagonist grows — but with [coevolution](genome.md#coevolution) on and the antagonist given its own genome, the antagonist can grow too if its plasticity is nonzero.

Bindings the rule's `when` predicate can read:

| Binding | Meaning |
|---|---|
| `kind` | The log row's kind (only ever `decision` or `event`) |
| `verb` | The verb (`fight`, `downed`, `ally_gained`, `betrayal`, …) |
| `result` | The result (`won`/`lost`, etc. — `null` on rows with none) |
| `actor_is_self` | Whether this subject was the actor |
| `target` | The target subject's id (empty string when it cannot be resolved) |
| `category` | The classified category (I–VI) |
| `risk_class` | The risk class |
| `stance_sign` | Whether the action raises or lowers the relationship |
| `target_role` | The target's role |
| `effective` | Whether the action actually changed the seven layers |
| `stress_after` | The post-change stress value (`null` on non-`decision` rows / when there's nothing to report — comparing `None` to a number raises, so a `when` that reads this must guard it first, e.g. `stress_after is not None and ...`) |
| `involves_self` | Whether this subject is involved at all (actor, target, or a delta target) |

The five built-in rules in `templates/momotaro_plus2/rules.yaml`:

| Rule ID | Condition | Shift |
|---|---|---|
| `g_fight_lost` | Self fought and lost | `risk_tolerance -0.15` (a loss makes them more cautious) |
| `g_fight_won` | Self fought and won | `risk_tolerance +0.1`, `category_weight.I +0.1` (a win makes them bolder) |
| `g_downed` | Self was downed | `risk_tolerance -0.2` (being downed makes them more cautious) |
| `g_ally_gained` | An ally was gained | `category_weight.III +0.1`, `stance_shift_bias +0.1` (values relationships more) |
| `g_betrayed` | Self is involved, but self is not the one who betrayed (i.e. self was betrayed) | `stance_shift_bias -0.2`, `category_weight.III -0.1` (trusts people less) |

Each match writes `details.shift = plasticity × adjust` as a `verb: "growth"` derived event in `layers.jsonl`, and accumulates into `acquired` with no decay (a key that nets back to exactly 0 — e.g. a win cancelling a prior loss — is dropped rather than kept as a stray zero). What actually drives weighting is `Policy.current_genome() = clip(genome + acquired)`; `Policy.reweight` reads this as "the personality right now."

## Turning it on: CLI and run settings

- CLI: `python scripts/evolve.py ... --personality-growth` (default off). It is deliberately named `personality_growth` to keep it clearly distinct from WORLDGROW-002's unrelated "grow the world" feature.
- Screen: the **"Personality growth"** checkbox in section "03. Save & evolution" of [run settings](../usage/run-settings.md) (default off). It is separate from "grow the world" (which expands the world itself) and never changes the world's settings.

Left off, the GA's random-number consumption, weighting, and output file shapes are exactly the same as before this feature existed.

## Reflected in synopses and narration

A `growth` event, by itself, is **never** a reason a turn gets picked as "notable" (`gapengine/scenes.py`'s `SPECIAL_PRIORITY` deliberately leaves growth out). A personality shift is a consequence of that turn's other events — a fight, a betrayal, an ally gained — and letting it alone bump a turn ahead would let it outrank foreshadowing payoffs, objective transfers, and other events competing for a limited number of "notable turn" slots. **When a turn is already selected for some other reason**, though, any growth event on it is still reported alongside that turn's description.

The line added is rendered by `growth_event_text()` as "{the rule's description} ({gene's display label} {signed shift}, …)" (example, for `g_fight_lost` at `plasticity=1.0`: "a loss makes them more cautious (caution -0.150)" — the actual shift is `plasticity × adjust`, so it shrinks as plasticity does). The synopsis/narration prompt receives this as a "Personality change: …" line, along with the added instruction actually used (`gapengine/synopsis.py`): "write personality change as a consequence of events, and make the difference in behavior before and after the change come through." A run with no growth events at all produces byte-identical prompts to one without this feature.

## The "Personality change" panel in run details

Whenever a run has logged at least one `growth` row, its run-detail screen shows an additional "Personality change" card (`viewer/pages.py`'s `_growth_panel`):

- Side-by-side nine-stat bars for **personality (at start)** and **personality (now)** (`genome` vs. `genome + acquired` — this never includes a turn rule's temporary adjustment)
- A table of every growth event, by turn and by what triggered it and how it shifted

The temperament bars (`traits`) shown in the "People" table are unaffected here (traits are outside the GA's scope to begin with, and unrelated to personality growth).

## The QD map's third axis, "personality change" (opt-in) { #arc-axis }

With personality growth enabled *and* the genre's `templates/<genre>/qd.yaml` declaring `arc_bins: [none, small, large]`, the [QD map](qd-map.md) gains a third axis (the magnitude of personality change). If either condition is missing (growth off, or no `arc_bins` declared), this axis does not exist, and the archive's cell keys stay the original two-part form (`category|volatility`), byte-identical to before.

- **arc** (magnitude): the L1 sum of the absolute values of `acquired_after` at the run's last recorded `growth` event — since `acquired` accumulates with no decay, this is the total personality change across the whole run. A run with no growth events at all scores 0.0.
- **Bins**: `arc == 0` is always `none`. Runs with `arc > 0` are split into `small`/`large` by the median of that generation's *positive-arc* population only (the `split` threshold). This threshold is frozen at generation 0 and reused for every later generation and rerun via `archive.json`'s `arc_thresholds`. If generation 0 has no run with `arc > 0` at all (nobody grew that generation), `split` falls back to a fixed 0.1, so small/large stay distinguishable once growth does show up in a later generation.
- **Cell key**: only when the axis is active does the key become the three-part `"<category>|<volatility>|<arc bin>"` (e.g. `I|low|small`).

In the Sifting screen (the grid view), a "filter by personality change" toggle (all / none / small / large) appears above the grid whenever this axis is active for that experiment. Each cell shows its single highest-quality representative by default.

## Why bother with the GA? { #ga-vs-random }

A measured comparison between the GA (evolving a policy) and a random baseline (`scripts/random_baseline.py`), under a condition that includes personality growth and this third axis (momotaro-family genre, matched evaluation count of 400, 5 random seeds; the GA ran 20 generations × 20 population, the random baseline 1 generation × 400 population, both with 3 seeds per individual):

| | GA | Random |
|---|---|---|
| Reach rate (mean) | 0.132 | 0.061 |
| Occupied cells (mean) | 17.0 | 13.4 |
| QD score (mean, sum of cell qualities) | 4.80 | 3.47 |
| Best quality (mean) | 0.429 | 0.382 |

Under a condition without personality growth or the third axis (an 18-cell grid), the GA and random baseline showed almost no difference (occupied cells 7.2 vs. 7.2, QD score 2.12 vs. 2.21).

**Limits of this comparison**: each condition was only tried 5 times, and no statistical test was performed. The condition with personality growth and the third axis also has a larger cell cap (54 cells = 6×3×3, vs. 18 without it), and this measurement doesn't separate how much of the GA's larger lead comes from "personality growth gives the GA something extra to explore" versus simply "there are more cells available to fill." It's possible that adding "personality shifts with experience" as something to explore makes evolving a policy matter more, but the numbers above can't confirm that on their own.
