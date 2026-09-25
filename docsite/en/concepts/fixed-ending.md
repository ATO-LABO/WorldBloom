---
ja_rev: "e5a7559cbd42"
---
# Fixed Ending

Rather than "adjusting the world so interesting events happen," WorldBloom works the other way around: **fix a single ending first, and use evolution only to search for paths that reach it.** It never leans how the world is built or the characters' starting state toward what's convenient for that ending.

## An ending is written as a predicate

An ending is written into the world's definition as a condition expression (`when`), using the [predicate vocabulary shared with the Seven Layers](seven-layers.md#predicates).

```yaml
ending:
  - id: homecoming
    when: "holds(桃太郎, 鬼ヶ島の宝物) and zone(桃太郎) == '村'"
    label: 鬼退治を果たし、宝を村へ持ち帰った
  - id: reconciliation
    when: "stance(桃太郎, 鬼) >= 0.5 and stance(鬼, 桃太郎) >= 0.5 and holder(鬼ヶ島の宝物) == '村'"
target_ending: homecoming
```

Combining predicates like `holds` (possession), `zone` (location), and `stance` (relationship) is enough to write endings for romance (mutual stance above a threshold) or detective stories (naming the culprit correctly with `confront`) using the same mechanism. A world can list multiple candidate endings, and only the one(s) named in `target_ending` are actually used for the reach check.

## When reaching is checked

The check looks at the `ending` events recorded in `layers.jsonl`. If a run records even one `ending` event whose ID is in `target_ending`, that run is judged to have "reached" it. A run is cut off the moment reaching is confirmed, or the moment reaching becomes provably impossible (e.g. the protagonist has died with no way to revive, or the target object is gone).

## What happens to individuals that didn't reach it

Runs that didn't reach the fixed ending never enter the [QD Map](qd-map.md)'s archive. Rather than bending the world to fit the ending, **the archive works as an "entry gate" for reaching runs only**. That said, a run that didn't reach the ending isn't wasted either — it carries a "shaped fitness" measuring how much of the ending's condition expression it satisfied (the fraction of a conjunction that held, or closeness to a threshold), used only when selecting next generation's parents (it never leaks into the archive).

## Reach rate

Even with the same genome, running across multiple fixed seeds produces a mix of runs that reach the ending and runs that don't. The **reach rate** (the fraction of runs across a whole generation, for that genre, that reached it) is recorded in the archive's metadata and also used to break ties. Because the design never constrains the world toward the ending, the reach rate tends to come out low — this isn't a target the search maximizes, only a condition for entering the archive. The fact that the GA's selection pressure pushes the reach rate up generation after generation is itself the real effect of the "fixed ending" design.
