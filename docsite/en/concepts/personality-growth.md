---
ja_rev: "42f3037d523c"
---
# Personality Growth (plasticity)

The nine scalars in the [Genome](genome.md) are an inborn policy that the GA evolves across generations. **Personality growth** is a separate, opt-in (default off) mechanism: within a single run, the protagonist's personality can shift slightly in response to what actually happened in the story (wins, losses, allies gained, betrayal, and so on) — a change acquired after birth, not an inherited one.

## Inborn vs. acquired

| | What changes it | When it changes | Does it inherit? |
|---|---|---|---|
| Genome (inborn) | GA crossover/mutation | Across generations | Yes |
| Personality growth (acquired) | Events that happen during the run (wins, losses, allies, betrayal, …) | Within that one run | No (`Policy.acquired` starts at `{}` for every fresh run and is never carried into the next generation) |

The [Genome](genome.md) design principle — "only the policy evolves" — is unchanged. Personality growth is a separate layer that governs how much that policy can move *within one run*, driven by what the character experiences.

## The plasticity gene

`plasticity` ([0, 1], neutral value 0) is a 10th scalar added to the genome. Unlike the other nine, though, **it stays exactly 0 unless a caller explicitly opts in**. The GA (`Genome.random`/`crossover`/`mutate`) only draws a random value for it when passed `plastic=True`; otherwise (the default) no extra randomness is consumed and `plasticity` is 0. A genome with `plasticity=0` has every mechanism below fully disabled, producing a byte-identical result to a run before this feature existed.

## Outcome-scope rules and growth events

A genre's `rules.yaml` can declare rules with `scope: outcome` (a separate bucket from the existing `candidate`/`turn` scopes — it never mixes into the ordinary candidate-weighting pass). These rules are evaluated *after* an action happens, and each match adds `plasticity × adjust` to that run's `Policy.acquired` (an accumulating shift on the genome), via `gapengine.policy.Policy.observe`.

Bindings the rule's `when` predicate can read:

| Binding | Meaning |
|---|---|
| `kind` | The log row's kind (`decision`/`event`, etc.) |
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
| `g_betrayed` | A betrayal involving self (but not by self) occurred | `stance_shift_bias -0.2`, `category_weight.III -0.1` (trusts people less) |

Each match writes `details.shift = plasticity × adjust` as a `verb: "growth"` derived event in `layers.jsonl`, and accumulates into `acquired` with no decay (a key that nets back to exactly 0 — e.g. a win cancelling a prior loss — is dropped rather than kept as a stray zero). What actually drives weighting is `Policy.current_genome() = clip(genome + acquired)`; `Policy.reweight` reads this as "the personality right now."

## Turning it on: CLI and run settings

- CLI: `python scripts/evolve.py ... --personality-growth` (default off). It is deliberately named `personality_growth` to keep it clearly distinct from WORLDGROW-002's unrelated "grow the world" feature.
- Screen: the **"Personality growth"** checkbox in section "03. Save & evolution" of [run settings](../usage/run-settings.md) (default off). It is separate from "grow the world" (which expands the world itself) and never changes the world's settings.

Left off, the GA's random-number consumption, weighting, and output file shapes are exactly the same as before this feature existed.

## Reflected in synopses and narration

`gapengine/scenes.py` picks up any turn containing a `growth` event as a "notable turn," and `growth_event_text()` renders it as a short line: "{what triggered it} ({rule description} {signed shift}, …)." The synopsis/narration prompt receives this as a "Personality change: …" line, along with an added instruction to write personality change as a consequence of events (never fabricated as inner monologue). A run with no growth events at all produces byte-identical prompts to one without this feature.

## The "Personality change" panel in run details

Whenever a run has logged at least one `growth` row, its run-detail screen shows an additional "Personality change" card (`viewer/pages.py`'s `_growth_panel`):

- Side-by-side nine-stat bars for **personality (at start)** and **personality (now)** (`genome` vs. `genome + acquired` — this never includes a turn rule's temporary adjustment)
- A table of every growth event, by turn and by what triggered it and how it shifted

The temperament bars (`traits`) shown in the "People" table are unaffected here (traits are outside the GA's scope to begin with, and unrelated to personality growth).

## The QD map's third axis, "personality change" (opt-in) { #arc-axis }

With personality growth enabled *and* the genre's `templates/<genre>/qd.yaml` declaring `arc_bins: [none, small, large]`, the [QD map](qd-map.md) gains a third axis (the magnitude of personality change). If either condition is missing (growth off, or no `arc_bins` declared), this axis does not exist, and the archive's cell keys stay the original two-part form (`category|volatility`), byte-identical to before.

- **arc** (magnitude): the L1 sum of the absolute values in the run's last `growth` event's `acquired_after`. A run with no growth events at all scores 0.0.
- **Bins**: `arc == 0` is always `none`. Runs with `arc > 0` are split into `small`/`large` by the median of that generation's *positive-arc* population only (the `split` threshold). This threshold is frozen at generation 0 and reused for every later generation and rerun via `archive.json`'s `arc_thresholds`.
- **Cell key**: only when the axis is active does the key become the three-part `"<category>|<volatility>|<arc bin>"` (e.g. `I|low|small`).

In the Sifting screen (the grid view), a "filter by personality change" toggle (all / none / small / large) appears above the grid whenever this axis is active for that run. Each cell shows its single highest-quality representative by default.

## Why bother with the GA? { #ga-vs-random }

A measured comparison between the GA (evolving a policy) and a random baseline (`scripts/random_baseline.py`), under a condition that includes personality growth and this third axis (momotaro-family genre, matched population/generation counts, 5 random seeds, no statistical test performed):

| | GA | Random |
|---|---|---|
| Occupied cells (mean) | 17.0 | 13.4 |
| QD score (mean, sum of cell qualities) | 4.80 | 3.47 |
| Best quality (mean) | 0.429 | 0.382 |

Under a condition without personality growth or the third axis (an 18-cell grid), the GA and random baseline showed almost no difference. Adding "personality shifts with experience" as something to explore appears to make evolving a policy matter more.
