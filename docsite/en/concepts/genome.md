---
ja_rev: "8eda305a2b08"
---
# Genome

What WorldBloom evolves with its GA (genetic algorithm) is not the sequence of actions itself. It's just **9 numbers** the protagonist (and, under coevolution, the antagonist) carries — a policy called the "strategy vector". The actions themselves are chosen each time from the world state represented by the [Seven Layers](seven-layers.md); these numbers only act as multipliers that bias that choice.

## The 9 scalars

| Gene | Count | Range | Neutral value | Meaning |
|---|---|---|---|---|
| Action-category weights `category_weight` | 6 | each [0.05, 1.0] | all 0.5 | How much it favors category I (self-reinforcement), II (perception & foreshadowing), III (relationship building), IV (Identity), V (movement & stalling), or VI (external intervention) |
| Risk tolerance `risk_tolerance` | 1 | [0, 1] | 0.5 | Whether it takes on an opponent it believes is stronger, or backs off |
| Direction of relationship shift `stance_shift_bias` | 1 | [-1, 1] | 0 | Whether it leans toward actions that draw closer to the other party (persuasion, gifts) or push them away (conflict, betrayal) |
| Novelty drive `novelty_drive` | 1 | [0, 1] | 0 | How much it avoids storylines already seen in the canon or in prior generations |

The lower bound on category weights is 0.05 rather than 0 (which would allow disabling a category entirely) to keep the GA from settling into a local optimum like "zero out category II and discard observation altogether" (fully disabling a category is left to template-side pruning instead). When every gene sits at its neutral value (`Genome.neutral()`), the candidate weights receive no modulation at all, matching a Policy-free run byte for byte. This is the baseline point for the [determinism](determinism.md) regression tests.

## Personality (temperament) is not evolved

Characters also carry a **temperament** (`traits`) — sociability, stubbornness, curiosity, and so on — separately from the genome, but this belongs to the "setting" and never changes under the GA. Making temperament evolvable too would collapse into optimizing an ability ("raising stubbornness wins"), breaking the design principle that "only the policy is evolved." A candidate's weight is computed in two layers: base weight (determined by temperament and world state) × policy multiplier (determined by the genome and context).

## Four multipliers

For each action candidate, four multipliers are multiplied together depending on its classification (category, risk tier, sign, etc.).

- **m_cat**: the chosen category's weight divided by the average weight across enabled categories
- **m_risk**: a factor from risk tolerance, depending on whether the candidate is "dangerous" (e.g. confronting a hostile or superior opponent) or a "safe move under threat" (e.g. retreating, defending)
- **m_stance**: a factor from the relationship-shift direction, depending on whether the candidate raises or lowers the relationship with the other party
- **m_nov**: a factor that suppresses actions the "precedent table" (below) shows have been chosen often in that situation

In genres where κ (the rationality section of [Run Settings](../usage/run-settings.md)) is set above 0, a fifth multiplier, **m_rat**, from LLM-based rationality judgment also applies. At κ=0 (the default) or in genres with no rationality judgment, this multiplier is absent.

## Novelty drive and the "precedent table" { #novelty-precedent }

`novelty_drive` is a drive that operates within a single individual, separate from the selection-side diversity mechanism ([QD Map](qd-map.md)). It works by "avoiding actions common in precedent for the current context." Precedent is built deterministically (with no randomness consumed) from three layers:

1. **Canon prior**: the "script anyone would likely write" for that genre, defined by a human in the template's YAML
2. **Archive precedent**: tallied from the model runs left in the previous generation's QD archive (frozen at the start of the generation, so every individual in that generation reads the same table)
3. **Self-history**: decisions this individual has already made within the same run

Raising novelty drive suppresses actions that are frequent in precedent, making rare actions relatively more likely to be chosen. At 0, the precedent table has no influence.

## Crossover and mutation

The next generation is built from parents drawn from individuals left in the archive (and top individuals from the previous generation). Crossover is uniform per gene dimension (inheriting either parent's value); mutation adds Gaussian noise to each dimension with a fixed probability, then clips it to range. A child doesn't inherit its parent's cell — which cell it lands in is decided by the actual run result.

## Coevolution (giving the antagonist a genome too) { #coevolution }

By default (off), the antagonist carries no genome and acts purely on temperament (`policy=None`). Turning on coevolution in [Run Settings](../usage/run-settings.md) gives the antagonist the same 9-scalar genome and its own QD archive, separate from the protagonist's, evaluating the protagonist path and the antagonist path alternately each generation. Turning it on doubles the evaluation count, so run time roughly doubles too.

The antagonist's fitness isn't zero-sum (it isn't scored higher for beating the protagonist). It's evaluated to maximize **how much harder it made the protagonist's path, among runs that reached the ending** (turn count, volatility, how many times the balance of power flipped, etc.). This keeps evolution from drifting toward an antagonist so strong that no protagonist ever reaches the ending.
