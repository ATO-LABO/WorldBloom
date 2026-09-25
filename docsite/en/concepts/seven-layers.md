---
ja_rev: "a8bfc67bd92a"
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

On top of this there's **vitality**. It transitions `alive` → `downed` → `revived` / `dead`, and a downed character can return to `revived` via rescue by an ally or self-recovery. Death only happens when the winning side's action or item carries `lethal`. This lets a "pseudo death and revival" arise naturally as part of the story.

## Identity layer { #identity-layer }

`identity_true` (true identity) and `identity_displayed` (displayed identity) are held separately. The `disguise` action splits the two apart, and until `identity_seen` (exposed) is set — through repeated `observe` or a `confront` — every other character can only treat this character by the name in `identity_displayed`. The relationship matrix, hostility checks, and role priors for action candidates are all read against the true identity once exposed, or the displayed identity otherwise (`perceived_name`). The moment of exposure switches the other party's stance to the value for the true identity, which is why a reveal scene works as a turning point in the story. A world template can pre-declare an initial relationship toward the disguised identity (e.g. affinity toward "a traveling merchant").

## Phase layer { #phase-layer }

`phase` is the set of irreversible thresholds a character has crossed (e.g. "crossed the border"). A world template's `phase_rules` declares which zone entry crosses which threshold, and which actions that enables or disables. Because crossing can't be undone, it produces irreversible progression like "once you cross to Ogre Island, you can't go back to the village — but in exchange, some actions are only available there." The actually-selectable actions (`availableActions`) are the intersection of that character's verbs, those whose [action-type](action-types.md) premises are satisfied, and those not disabled by the current `phase`.

## Objective layer and transfer of possession { #target-layer }

"Who's after what" (`goal`) and "who currently holds that objective" (the holder in `world.objectives`) are held separately. This separation means **transfer of possession and the outcome of a confrontation are handled as separate events**. An objective can change hands not only by fighting for it, but also through `negotiate` (the requesting side) and `concede` (the holding side conceding) — it can transfer without a fight if the relationship is good enough, or even without that if an exchange of assets (a trade) goes through. `concede` is both a transfer of possession and a reconciliation: once it goes through, hostility between the two sides is cleared, so a fight can't be picked again right after the handover. Momotaro's "bring the treasure home" ending can be reached via this negotiation route without ever defeating the ogre (a different path to the same ending).

## Resource layer { #resource-layer }

Three things: possessions `inventory`, reputation `reputation`, and drawable relationship capital (bonds). `sacrifice` and `grand_gesture` (a large gift or public act) are ways to spend assets or bonds in exchange for ability modifiers or identity-passage conditions. Reputation rises through `donate`, drops sharply when a `pledge` is broken, and can move either way with `grand_gesture` since it's a public act whose success or failure is visible.

## Vitality transitions { #vitality-transitions }

An `alive` character who loses to a non-lethal action becomes `downed`. The only action available while downed is `rest`, but they automatically recover to `revived` after a set number of turns, or immediately via a present ally's `rescue` action. The number of turns before automatic recovery is shortened by the count of present allies whose `stance` is at or above a threshold (it's the headcount of qualifying allies that matters, not the graded strength of any one relationship). `dead` only happens through `lethal_chance`, when the winning side's action or item carries `lethal` — an ordinary defeat only downs the character. A character the template marks `lethal_exempt` is protected even from lethal actions (a genre-level convention, not the engine steering toward an ending). This mechanism lets a high-volatility turn — "downed, then saved by an ally, then the tables turn" — arise from the ordinary rules rather than as a special-cased event.

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

## No LLM is involved in generating events { #no-llm-in-simulation }

Each turn, the protagonist enumerates the action candidates possible at that moment, multiplies the base weight (from temperament and world state) by the [genome](genome.md)'s multipliers, and draws once. Candidates that don't satisfy an action's premise (e.g. observe before neutralizing) never even make it into the candidate list, so causal consistency is guaranteed by construction. No LLM enters here at all — the sequence of events is purely a simulation result. Given the same world, the same genome, and the same random seed, the log matches byte for byte (see [Determinism](determinism.md) for details).
