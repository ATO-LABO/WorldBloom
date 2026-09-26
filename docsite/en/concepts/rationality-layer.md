---
ja_rev: "b8203f9d75d0"
---
# Rationality layer and Jev

## What the rationality layer is

The protagonist basically picks actions by the preferences the genome ([Genome](genome.md)) expresses. The **rationality layer** has a judge model score each candidate for how much sense it makes *from the protagonist's own point of view*, and multiplies that in as one more multiplier alongside the genome-driven ones (m_cat/m_risk/m_stance/m_nov).

```
m_rat = (p / mean p) ** kappa
```

`p` is the probability the judge model gave that a candidate "makes sense"; `mean p` is the average across every candidate in that situation. The higher κ (kappa, 0–1) is, the harder a below-average candidate gets discounted. At κ=0 the rationality layer doesn't run at all, and GA output stays byte-identical to a run without κ.

Judgment happens using only what the protagonist actually knows or believes. The world's hidden truth (`world.truth`) is never passed to the judge model, so a judgment never sees through what's really going on behind the scenes. The roles stay separate — **rationality is the judge (a constraint), preference is the genome, detours are the [route layer](route-layer.md)/novelty, and misperception is the seven layers' perception**. The rationality layer never decides *what* the protagonist wants; it only judges whether a given means makes sense for that.

Judgments are cached in a judgment table. For experiments started from the UI, there is one table per genre × judge model, shared across experiments (Ollama and Jev judgments never mix). The same situation (same candidates, same context) is simply reused from that table when it's already there, so determinism — the same world, genome, seed, precedent table, and judgment table producing byte-identical output — still holds.

## Two kinds of judge

| Judge | What it does | Speed | What it needs |
|---|---|---|---|
| Ollama (local) | Sends one question at a time to a local `qwen3.6:35b` in choice mode, and reads the probability off its logprobs | Roughly 2–4 seconds per call (uses the GPU; waits for it to cool down when it runs hot) | Ollama itself, with the model pulled |
| Jev (TypeSafe) | Calls TypeSafe's API (`jev-1.13.0`) | About 0.2 seconds per call (no GPU needed) | A TypeSafe API key. Every judgment call sends the protagonist's situation text to an external service (api.typesafe.ai) |

Jev only reports probabilities on a coarse 0.01 grid. Its 0.00 doesn't mean "truly zero" — it means "below 0.005" — so using it as-is would make `(p/mean p)**kappa` equal zero and ban that candidate outright (an Ollama judgment never drops that low). To prevent this, Jev's 0.00 is floored to 0.005 before use, so no candidate is ever fully excluded.

## Enabling Jev

The "計算" (Compute) tab of ⚙ 全体設定 (Global settings) has a "合理性の判定器（Jev）" (Rationality judge (Jev)) block. Enter your TypeSafe API key and press "キーを保存" (Save key); before saving, it sends one real judgment call with that key to verify it, and only saves if that succeeds. Once valid, the status changes to "有効（jev-1.13.0、確認した日付）" (Enabled (jev-1.13.0, verified on <date>)). The key is stored only in `settings.json` — it never appears on screen, in the API, or in any experiment record.

Which judge an experiment uses is chosen per run in the "合理性" (Rationality) section of [Run Settings](../usage/run-settings.md#04). A new config defaults to Jev with κ=0.45 whenever a verified key is on file (duplicating a config keeps its own saved judge and κ). Only when κ is above 0 and Jev is selected does each judgment call send the protagonist's goal, possessions, companions, and candidate-action descriptions to TypeSafe. Trying to run a config still set to Jev without a registered API key is rejected up front.

From the CLI, pass `--rationality-backend jev --rationality-model jev-1.13.0` and set the `TYPESAFE_API_KEY` environment variable to the key.

## What using Jev actually does (measured, 2026-09-27)

Measured on Momotaro-based genres, 12 individuals × 2 seeds (one world, one GA seed — Jev's behavior may shift with a different model version; judgment tables are also kept separate per model version).

**Basic Momotaro (2 generations, 48 runs)**

| | κ=0 (off) | Jev κ=0.45 | Jev κ=0.6 | Ollama 35b κ=0.6 |
|---|---|---|---|---|
| Reach rate | 2.1% | 14.6% | 31.2% | 29.2% |
| Occupied cells | 1 | 4 | 6 | 7 |
| Odd-action rate | 3.5% | 1.5% | 1.2% | 1.5% |
| Time taken | a few seconds | ~1 minute | 80 seconds | 79 minutes |

**50 generations, 1,200 runs**

| | κ=0 | Jev κ=0.6 | Ollama 35b κ=0.6 |
|---|---|---|---|
| Reach rate | 4.3% | 33.3% | 26.8% |
| Occupied cells | 6 | 11 | 12 |
| Odd-action rate | 3.0% | 1.0% | 1.3% |
| Diversity among reached runs | 0.46 | 0.74 | 0.88 |
| Time taken | 11 minutes | 20 minutes | ~11.5 hours (~3.7 hours of which was thermal waits) |

**Expanded Momotaro world (momotaro_plus2: added a gun alongside the gold coins, and a letter/negotiation route to the ogre's younger brother. 8 generations, 192 runs)**

| | κ=0 | Jev κ=0.3 | Jev κ=0.45 | Jev κ=0.6 | Ollama 35b κ=0.6 |
|---|---|---|---|---|---|
| Reached runs | 27 | 33 | 35 | 32 | 47 |
| Dominant reached route | fight with the gun 93% | fight with the gun 82% | fight with the gun 71% | fight with the gun 100% | fight with the gun 45%, give favor 21%, straightforward fight 26%, hand over the gun 9% |
| Route split by personality (of 3 checks) | 1/3 | 2/3 | 3/3 | 0/3 | 3/3 |
| Offered to negotiate | 9% | 23% | 27% | 13% | 40% |
| Time taken | — | a few minutes | a few minutes | 6 minutes | 7.5 hours |

Jev runs roughly 10–60× faster than the 35b Ollama judge and its judgment tendencies are similar (a rank correlation of 0.75 across 12 decision points, and 0.89 agreement on which move is a bad one). But Jev pushes its single "best" answer harder than the 35b judge does — for example, it scores "buy the gun" about 4× higher than the 35b judge does, and "take on the younger brother's trial" at about 1/6. That tendency means running the expanded genre at κ=0.6 pulls every individual onto the gun route, and the genome stops splitting the path by personality (the "route split by personality" row above reads 0/3). Dropping to κ=0.45 restores that split, but roughly halves the reach rate for basic Momotaro compared to κ=0.6.

WorldBloom is meant to produce branching stories from "world expansion × personality preference," so Jev's default κ is 0.45. If reach rate matters more for a given run, raise κ with the slider. Ollama's default κ stays 0.6.
