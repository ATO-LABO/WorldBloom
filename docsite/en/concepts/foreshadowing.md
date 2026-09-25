---
ja_rev: "65765483ec17"
---
# Foreshadowing (Delayed Effects)

The [Seven Layers](seven-layers.md)' seventh layer carries **delayed effects**, where an action placed earlier pays off later. It has two moments — "plant" and "payoff" — and the delay between them is what reads as "foreshadowing" in hindsight.

## Who pulls the trigger on payoff — auto vs. chosen

The foreshadowing library (`templates/<genre>/effects.yaml`) declares, for each entry, who pulls the trigger on its payoff.

- **`mode: auto`**: fires automatically the instant conditions are met, as the natural consequence of an act — not a character's choice. Example: someone who received kibidango automatically shows loyalty later when present at a battle.
- **`mode: chosen`**: foreshadowing where the timing of revealing or using it matters in itself. Once conditions are met, "pay it off" (action type II-3, `payoff`) becomes a candidate, and whether it's chosen follows the [genome](genome.md). Example: whether the fact learned through reconnaissance — "the ogre's only weakness is the iron club" — gets used right after landing, held back until allies gather, or played as a last resort when cornered: the GA searches over this difference in timing.

```yaml
# templates/momotaro/effects.yaml
- id: kibidango_loyalty
  plant:
    verb: give_item
    item: きびだんご
    to_role: [neutral, ally]
  payoff:
    condition: "stance(target, planter) >= 0.5 and hostile_present(planter)"
    description: きびだんごの恩に報いる仲間の奮戦
    effect:
      modifier: {target: planter, source: "$target", value: 10, kind: loyal}
    mode: auto

- id: oni_gap
  plant:
    verb: observe
    reveals: {target: 鬼, source: 金棒}
  payoff:
    condition: "present(鬼) and vitality(planter) != 'downed'"
    description: 金棒の隙を見切って仲間に合図を送る
    effect:
      modifier: {target: planter, source: 見切り, value: 15}
    mode: chosen
```

In most cases `plant` piggybacks on an existing verb's execution (`give_item`, `observe`, etc.) itself. Some foreshadowing uses a dedicated `plant` verb instead. Which foreshadowing gets planted is modulated by action type II's (information) category weight and novelty drive.

## Penalty for unresolved foreshadowing

If a `chosen` piece of foreshadowing meets its condition but the story ends without ever paying it off, quality q is docked **-0.05 per case** (`gapengine/qd.py`). This is the mechanism that lets the GA select against "a gun that's never fired" — foreshadowing that was planted but never used. `auto` foreshadowing never goes through a character's choice, so it's exempt from this penalty.

## How it appears in the log (layers.jsonl)

Planting and payoff are each recorded in the execution log `layers.jsonl` as a decision event or derived event. When a payoff fires, its effect (`modifier`, `enable_verb`, etc.) is reflected into the seven layers right there. At the output stage, this plant/payoff pair is extracted as a "notable turn" and passed into the synopsis-generation prompt (see [Run Outputs](../reference/run-outputs.md) for details).
