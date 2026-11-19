---
ja_rev: "105244ca8f81"
---
# Overview

WorldBloom builds a story in five stages. Each has its own detailed page; this page is just the map.

| Stage | What happens | Who/what does it | See also |
|---|---|---|---|
| 1. Fix the setting and the ending | Prepare the place names, routes, and characters' starting state (the world) and the genre's grammar (the template) it uses, and decide the [ending](fixed-ending.md) to aim for up front | A person (optionally using AI draft generation/refinement, reviewing and adopting the result) | [Create a World](../usage/create-world.md), [Fixed Ending](fixed-ending.md) |
| 2. Simulate with a protagonist that carries a genome | The protagonist carries a "strategy vector" ([genome](genome.md), 9 numbers) and picks a candidate action each time from the world state represented by the [Seven Layers](seven-layers.md). This runs hundreds of individuals across many generations, over a fixed set of seeds | The GA (genetic algorithm) and simulation; a rationality evaluator also participates when κ>0 | [Genome](genome.md), [Determinism](determinism.md) |
| 3. Keep only runs that reached the ending, in the grid | Only runs that reached the fixed ending are kept, one best run per cell, in the [QD Map](qd-map.md) (leading category × volatility), which balances quality and diversity | The GA's evaluation loop | [QD Map](qd-map.md) |
| 4. A person chooses | A person reads the surviving candidates' synopses and rationale, and picks the ones worth turning into full text ([Sifting](sifting.md)) | A person | [Sifting](sifting.md) |
| 5. The LLM writes the text | Only for the chosen candidates, the LLM writes the synopsis and full-text prose. The events themselves are never rewritten | The LLM | [Generate Text](../usage/generate-text.md), [LLM Backends](../usage/llm-backends.md) |

## Three things worth remembering

- **The engine generates events.** At κ=0, actions are selected without an external rationality evaluator. At κ>0, Ollama or Jev judgments affect candidate weights (see [Rationality Layer](rationality-layer.md)). Beyond this optional evaluation, LLMs can also help [generate and refine worlds](../usage/create-world.md) before a run and turn selected events into prose afterward.
- **What's being evolved is the policy (genome), not the sequence of actions.** The protagonist crosses over and mutates only 9 numbers, generation to generation. The actual actions are chosen each time from that policy and the world state.
- **Whether something is interesting is always judged by a person.** The GA only looks as far as "did it reach the ending" and "quality and diversity." Which candidates get turned into text is decided by a person on the Sifting screen.
