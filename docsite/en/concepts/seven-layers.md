---
ja_rev: "2bae66f21de0"
---
# Seven Layers

WorldBloom's characters aren't represented as lines of dialogue or personality prose — they're numbers and sets held directly in **seven layers** at runtime. Actions (verbs) read and write these layers directly, and the log written to `layers.jsonl` is their diff. There's no projection or intermediate layer; these seven layers are the sole state actions judge against.

| Layer | Contents | Role in the story |
|---|---|---|
| 1. Ability | The true base value `base`, plus modifiers `modifiers` from items, allies, and facts | Decides who wins a confrontation. Rises through training, falls through debuffs |
| 2. Perception | Beliefs about others' strength `beliefs_about`, known facts `knowledge` | Produces "challenges without knowing about the iron club and loses." Gaps in perception drive the story |
| 3. Resources | Possessions `inventory`, reputation `reputation`, and drawable relationship capital (bonds) | The material for sacrifice or negotiation |
| 4. Phase | Thresholds crossed `phase` (irreversible), available actions `verbs` | Crossing to Ogre Island can't be undone, and some actions only become available after crossing |
| 5. Identity | True identity `identity_true` vs. displayed identity `identity_displayed` | Disguise, a false protagonist, exposure |
| 6. Objective | Who's after what (`goal`) and who currently holds it (`world.objectives`) | Contests over a treasure. Transfer of possession and victory/defeat are handled separately |
| 7. Delayed effects | Planted foreshadowing (`world.pending_effects`) and its resolution conditions | An earlier action pays off later. Unresolved foreshadowing is penalized in quality |

On top of this there's **vitality**. It transitions `alive` → `downed` → `revived` / `dead`, and a downed character can return to `alive` via rescue by an ally or self-recovery. Death only happens when the winning side's action or item carries `lethal`. This lets a "pseudo death and revival" arise naturally as part of the story.

## The relationship matrix (stance / bonds)

Relationships between characters are represented by a single matrix, `world.relations[a][b] = {affinity, awareness}`. **stance(a, b)** is "how a sees b" (reading affinity in that direction); **bonds(a)** is "the relationship capital a can draw on" (the sum of the positive parts of affinity from others). Reading the same matrix both ways avoids holding bonds in the resource layer and stance in relationships as two separate things.

## Combat is resolved as a "difference game"

A confrontation is decided by a probability equal to a logistic function of the true strength difference between the two sides (consuming exactly one random draw). What matters here is the asymmetry: **the side choosing an action uses "believed strength" (the perception layer), while the actual outcome is decided by "true strength" (the ability layer)**. A Momotaro who doesn't know about the iron club (a modifier with `visible: false`) challenges believing he has the edge at 80 vs. 90, and loses to the true value of 120 — this gap in perception is exactly the mechanism that drives the story.

## Predicates (the shared vocabulary of endings, foreshadowing conditions, and rules) { #predicates }

The ending's judgment expression, foreshadowing's resolution conditions, and action modulation rules all share the same predicate namespace. Common ones include `holds(a, item)` (possession), `zone(a)` (location), `stance(a, b)` (relationship), `vitality(a)` (vitality state), and `believed_strength(a, b)` (how strong a believes b is). Momotaro's ending can be written like this:

```yaml
ending:
  - id: homecoming
    when: "holds(桃太郎, 鬼ヶ島の宝物) and zone(桃太郎) == '村'"
    label: 鬼退治を果たし、宝を村へ持ち帰った
```

This same vocabulary is passed to the LLM's synopsis prompt as the same dictionary.

## No LLM is involved in generating events

Each turn, the protagonist enumerates the action candidates possible at that moment, multiplies the base weight (from temperament and world state) by the [genome](genome.md)'s multipliers, and draws once. Candidates that don't satisfy an action's premise (e.g. observe before neutralizing) never even make it into the candidate list, so causal consistency is guaranteed by construction. No LLM enters here at all — the sequence of events is purely a simulation result. Given the same world, the same genome, and the same random seed, the log matches byte for byte (see [Determinism](determinism.md) for details).
