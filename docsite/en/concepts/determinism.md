---
ja_rev: "4d99230e82ed"
---
# Determinism

WorldBloom's individual evaluation is designed as a pure function. **Given the same `(world, genome, seed, precedent, engine hash)`, the output log `layers.jsonl` matches byte for byte.** This isn't incidental reproducibility — it's a design premise, and it's protected by regression tests.

## Why determinism matters

If the same input produced a different story every time, there'd be no way to trace back "why did this story turn out this way". By preserving determinism, WorldBloom can mechanically show which generation and parent a candidate came from and at which choice it diverged, as [lineage and turning points](sifting.md#compare-lineage-turning). This is a property that pure LLM-driven story generation doesn't have.

## Three guarantees that make determinism hold

1. **A single `random.Random(seed)` for randomness**: the RNG used for simulation and the RNG used for the GA (crossover, mutation, parent selection, `random.Random(ga_seed)`) are kept completely separate. Action selection calls `rng.choices()` exactly once against the candidate list's weights, and consumes no randomness anywhere else.
2. **Candidate generation and the Policy (genome) consume no randomness**: enumerating action candidates and the genome's weight modulation (computing the four multipliers) are both fully deterministic. The "precedent table" (the canon prior, archive precedent, and self-history that [Genome](genome.md#novelty-precedent) reads from) is likewise frozen at the start of the generation without using any randomness.
3. **Ties break by name order**: when multiple candidates tie, the order is always decided by name (string) order. Note that Python string comparison is by Unicode code point, which doesn't always match the intuitive Japanese ordering (e.g. 甲・乙・丙) — in fact "丙 < 乙 < 甲" here. When writing templates, don't assume this order lines up with a narrative role (e.g. first suspect, second suspect).

## Changing the engine changes the story

What determinism guarantees is "the same engine code can be reproduced" — changing the engine itself (`engine/`, `gapengine/`) changes the order randomness is consumed in, so the same genome and seed produce a different story. That's why the [QD Map](qd-map.md)'s archive stores not just the genome but the actual model run's log body and the engine's hash (`engine_hash`). A story isn't treated as "a reproduction recipe you can rebuild from the genome" but as "the log that was actually produced," kept together with its lineage.

## Verifying with regression tests

Determinism, that the neutral genome applies no modulation, zero premise violations, foreshadowing penalties, and vitality transitions can all be checked with:

```
python -m unittest discover -s tests -v
```

Always run this regression suite after changing `engine/` or `gapengine/` (the GA, classifiers, precedent table, seven-layer logic). A change that alters the order randomness is consumed in must be noted as such in the design docs.
