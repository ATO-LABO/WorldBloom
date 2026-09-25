---
ja_rev: "7c3e188e092a"
---
# Glossary

A summary of terms used on WorldBloom's screens and throughout this documentation. These match the definitions in the on-screen help (the explanation shown when hovering over a term). Grouped into four areas: input, evolution, grid (Sifting), and output.

## Input

| Term | Meaning |
|---|---|
| World | A bundle of place names, routes, day count, and characters' starting state, plus a target ending. Lives under `projects/<world>`. See [Create a World](../usage/create-world.md) |
| Genre (template) | The set of grammar a world uses: the action graph (available actions and their premises), canon tendencies, effect tables, rules, and grid axes. Lives under `templates/<genre>` |
| Seven layers | The state model for a character: ability, perception, resources, phase, identity, objective, and foreshadowing (delayed effects). Actions read and write these layers directly. See [Seven Layers](../concepts/seven-layers.md) |
| Vitality | Alive / downed / revived / dead. Even after being downed, a character can return via rescue by an ally or self-recovery |
| Target ending | A condition expressed over state. Only runs that satisfy it enter the [QD Map](../concepts/qd-map.md)'s archive |
| Canon | The "commonly seen" tendencies of actions in that genre. What a novelty-seeking genome avoids, and the initial value of the precedent table |
| Foreshadowing (delayed effect) | A mechanism where an earlier action pays off later. Has a setup and a resolution condition; ending unresolved costs a penalty to quality q |

## Evolution

| Term | Meaning |
|---|---|
| Genome (strategy vector) | 9 numbers representing the protagonist's policy. See [Genome](../concepts/genome.md) |
| Individual | A single genome. Evaluated once per population count, every generation |
| Generation | One iteration of the GA. Evaluates individuals and updates the grid each time |
| Seed | A random seed. Even the same genome produces a different set of starting conditions — and so a different outcome — under a different seed (e.g. a different culprit, in a detective story) |
| Run | One execution of the world for one genome × one seed. Its result becomes a log called `layers.jsonl` |
| Experiment (run) | One full GA execution. Born from a single run config, with its record kept under `runs/<experiment name>` |
| Reach / reach rate | Whether the fixed ending was reached. The reach rate is the fraction of all runs in that generation (population × seeds) that reached it — a gate for entering the grid, not a target the search maximizes |
| Precedent table | A table of "storylines already seen," built from canon, the previous generation's archive, and self-history. What a novelty-seeking genome avoids |
| Coevolution | A mode where the antagonist also carries a genome and evolves alongside the protagonist. Off by default |
| Meta-evolution | A mode that, in addition to the 9 numeric genes, also searches over enabling/disabling each rule. Off by default |
| Random baseline | A control experiment (`scripts/random_baseline.py`) measuring the reach rate and diversity of purely policy-free, fully random individuals |
| Determinism | The property that the log matches byte for byte given the same world, genome, seed, precedent table, and engine. See [Determinism](../concepts/determinism.md) |
| κ (kappa, rationality) | The strength of a genre's rationality judgment (Jev). Set as a value from 0–1 in run settings. See [Run Settings](../usage/run-settings.md) |

## Grid (Sifting)

| Term | Meaning |
|---|---|
| Archive / grid | The MAP-Elites grid holding runs that reached the ending. Each cell keeps only its single best run. See [QD Map](../concepts/qd-map.md) |
| Cell | A cell of the grid. Rows are the leading category, columns are volatility (low/mid/high) |
| Leading category | The kind of action that actually had the most effect in that run (I self-reinforcement, II perception & foreshadowing, III relationship building, IV status, V movement & stalling, VI external intervention). A different value from the genome's tendency |
| Volatility | The variance of how much the protagonist's seven-layer state swung turn to turn. Thresholds are fixed from generation 0's population |
| Quality q | The quality of a run that reached the ending. An absolute scale (0–1) that counts objective movement, swings in relationships, recoveries, reversals of power, gaining new facts, belief reversals, and chains of premise-aligned actions, and penalizes actions that came up empty. q̄ is the average across the whole archive |
| Occupied cells | The number of filled cells in the archive. More means more different kinds of stories found |
| Diversity | The average of how different the paths of runs left in the archive are from each other. Closer to 1 means more diverse |
| Tendency | The action category that individual's genome weights most heavily. Can differ from the leading category |
| Parent | The two individuals that produced this individual |
| Lineage (main line) | The single line traced back through parents from an elite. Of the two parents, only the one with the closer genome is followed |
| Turning point | The point, when re-running an adjacent parent/child pair on the main line with the same seed, where the protagonist's decision first diverged |
| Sifting | The process of sifting through the candidates an experiment left behind and adopting the ones worth reading. See [Sifting](../concepts/sifting.md) |
| Selection status | ✔ Adopted (proceed to generation) / ⏸ Held / ✖ Excluded / ○ Unsorted |

## Output

| Term | Meaning |
|---|---|
| Candidate ID | An ID uniquely identifying an (experiment, generation, individual, seed) combination. Selection and generation are recorded at this granularity |
| Synopsis generation (synopsize) | The process of pulling notable scenes from each run in the grid and having the LLM write a short synopsis |
| Narration generation (narrate) | The process of turning only the adopted candidates into full-length prose with the LLM |
| backend / model | The method and model name used to generate the text (e.g. `ollama` / `qwen3.5:9b-q4_K_M`). See [LLM Backends](../usage/llm-backends.md) |
| Choice, rationale, cost, turning point | Four fields shown in a candidate's detail: what belief a given choice was made on, what it cost, and what it led to afterward, mechanically extracted from the log |
| World self-expansion | A mechanism that proposes missing elements (places, items, facts) from places and actions that kept coming up empty during a GA experiment. See [Create a World](../usage/create-world.md#world-expansion) |
| GPU Guard | A mechanism that coordinates GPU use between the local LLM (llama-server) and Ollama, and pauses briefly until temperature drops. See [GPU Guard](../usage/gpu-guard.md) |
