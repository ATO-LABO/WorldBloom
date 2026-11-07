# Legacy stories: protagonist planning migration

The separately named `*_shared_planning` editions retain each original full
cast and world. They migrate the protagonist's final goal to shared causal
planning. Supporting characters retain legacy decisions; this is a controlled
migration experiment, not a completed conversion of every character or verb.

| Source | Cast | Original target reached / 4 | Migrated target reached / 4 |
| --- | ---: | ---: | ---: |
| momotaro | 7 | 0 | 3 |
| momotaro_plus | 8 | 0 | 2 |
| momotaro_plus2 | 8 | 0 | 1 |
| momotaro_plus3 | 15 | 0 | 1 |
| romance | 5 | 0 | 4 |
| detective | 6 | 0 | 4 |
| romance-x-detective | 5 | 0 | 0 |

These are 56 ordinary simulations, using fixed original definitions and seeds
0, 1, 2 and 3, without GA search, narration or external model calls. They are not
success probabilities or a literary-quality comparison. Altered action choices
also alter subsequent random consumption; equal seeds do not mean identical
external trajectories. Momotaro's migrated ending explicitly requires an actual
victory as well as treasure ownership and homecoming, unlike the original
item/return predicate. Therefore the two ending definitions are not identical.

Momotaro seed 0 gathers timber, crafts the existing boat, crosses the sea,
loses battles, observes the opponent, neutralizes the club, actually wins at
turn 26 and returns at turn 29. A forecast win does not complete the goal.
Seed 1 fails to complete the same goal before the deadline. The migrated
protagonists have no invalid action results in the 28 runs.

The romance edition observes that gifts fail to improve a capped relationship,
discovers and removes existing caps, then uses native dialogue. It preserves
the original numerical relation model; it is not a new consent or psychology
model. The detective edition can make a wrong accusation, review real evidence
and eventually expose a culprit. Planning does not consult culprit truth.
The grammar-crossed romance remains unsolved in these four trials: the native
detective permissions are retained, rather than unlocking romance-only verbs.

## Review and reproduce

Each edition's `migration.json` records exact source hashes, initial knowledge
assumptions, changed ending/forced-action rules and the actors still using
legacy decisions. Personal values stay on the protagonist; condition/outcome
rules stay in the world. The source worlds and saved histories are preserved.

The read-only purpose timeline labels battle loss and mistaken accusations as
failed attempts, separately from the goal's final verdict. Native completions
advance the displayed plan rather than looking like unexplained replanning.
Desktop 1280px and narrow 390px checks covered scene selection, all-actions
expansion, keyboard movement and preserved selected detail.

Run, into a fresh output folder:

```
python tools/run_legacy_story_comparison.py --out outputs/legacy-comparison
python -m unittest discover -s tests -p test_native_planning.py
python -m unittest discover -s tests -p test_legacy_story_editions.py
```

`docs/evidence/legacy-story-migration.json` retains compact per-trial results.
Full local layers logs and checksums accompany the delivered evidence archive.
Fourteen original-story comparisons against the pre-change checkout match every
row after excluding only the header's changed `engine_hash`. This is behavioral
compatibility, not a claim that the source implementation is byte-identical.

## Remaining migration

- Supporting characters' personal purposes and protection reactions have not
  all been converted to shared planning.
- Full old six-person calendar Melos and the older multi-day festival are not
  migrated by this ordinary-action adapter. Their existing source editions stay
  intact; the previously added shared Melos and festival editions remain separate.
- The adapter does not expose every native verb or timed native action.
- Loss, unavailable means and planning limits can still leave a goal unfulfilled.
  No outcome was forced to make all tests or all stories succeed.
- GA diversity, acceptance of the resulting prose and broader objective/risk
  policies need their own evaluation. These fixed runs do not establish them.

See [native operator contract](native-goal-planning.md) for supported mechanisms.
