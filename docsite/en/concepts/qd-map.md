---
ja_rev: "ce35761a8563"
---
# QD Map

Rather than keeping just the single highest-scoring run that reaches the ending, WorldBloom lines runs up in a **grid that balances quality and diversity (a MAP-Elites archive)**. This lets different kinds of stories — a Momotaro who reconciles with the ogres, and a Momotaro who wins by sacrificing companions — both survive.

## Qualifying for the archive

Each genome is run against several fixed random seeds. Only runs that reach the [ending](seven-layers.md#predicates) are eligible for the archive (runs that don't reach it are given only a partial score for "how far the conditions were satisfied," used for parent selection in the next generation). Since the world is never bent to fit the ending, reach rates generally come out low (this is never a target the search maximizes — it's strictly an entry gate for the archive).

## The two axes

| Axis | How it's decided |
|---|---|
| Leading category (row) | Among the protagonist's decision events, only those that actually changed the seven layers (`effective`) are counted, and the action category that had the most effect wins. Noise like repeated movement is excluded. **This is a different value from the genome's own category-weight tendency** — the two can disagree |
| Volatility (column) | Vectorizes the protagonist's seven-layer state per turn, and takes the variance of the change from the previous turn. Thresholds are fixed by splitting generation 0's population into terciles |

The default grid is 6 rows × 3 columns (splitting volatility into low / mid / high); the category range and number of bands used for the axes can be adjusted per genre template (`templates/<genre>/qd.yaml`). Each cell keeps only its **single highest-quality representative individual (the model run)**, replaced only when a higher-quality individual later appears.

## Quality q (an absolute scale)

Quality q is computed on an **absolute 0–1 scale**, not relative to other runs.

- Rewards: how many times the objective moved, the swing in relationships, recovering after being downed, reversals in power balance, gaining new facts, belief reversals, and chains of actions whose premises lined up (e.g. observing then neutralizing — how many times that kind of sequence completed)
- Penalties: how many actions came up empty (resolved to no effect), the same (action, target) three or more times in a row, the ratio of retreating/waiting, and unresolved foreshadowing (foreshadowing with a resolution condition that was never met by the end)

Being an absolute scale, the level you tend to see depends on how the world was built (a world seeded with a lot of foreshadowing that's prone to going unresolved will structurally score lower on q). The average across the whole archive is called q̄.

## Reading it on screen

The run-results screen shows the grid's **occupied cells** (number of filled cells — more means more different kinds of stories found) and **diversity** (the average of how different the paths taken by the runs left in the archive are from each other — closer to 1 means more diverse). Opening a cell shows that representative individual's **reach** (how many of this elite's evaluated seeds reached the ending) as well as the generation it was produced in and its parents (two individuals). See [Read Results](../usage/read-results.md) for more on how to read the screen.
