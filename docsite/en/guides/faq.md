---
ja_rev: "b0616ed7ae88"
---
# FAQ

## Does the GA experiment (evolution) need an LLM?

An LLM is not required. The base simulation, evolution, and accumulation into the QD grid run without one. With the optional [rationality layer](../concepts/rationality-layer.md) (κ > 0), Ollama or Jev judgments adjust candidate-action weights. LLMs are used for AI-assisted [world creation and refinement](../usage/create-world.md) before a run and [synopsis and prose generation](../usage/generate-text.md) afterward. Browsing saved results requires no generation. CLI text generation supports `--backend none` to save prompts without an LLM call. See [LLM Backends](../usage/llm-backends.md) for the current screen and backend choices.

## Why doesn't event generation use an LLM?

Letting an LLM write the sequence of events (who did what) directly would make it impossible to mechanically trace why things unfolded the way they did. By limiting event generation to simulation and random draws alone, WorldBloom guarantees [determinism](../concepts/determinism.md) and can mechanically show which generation and parent a given story came from, and at what branch point, as [lineage and turning points](../concepts/sifting.md#compare-lineage-turning). See [Overview](../concepts/overview.md) for details.

## Why bother with the GA at all?

You can generate stories without evolving a policy (genome) at all, using only random individuals (`scripts/random_baseline.py`). A measured comparison under a condition with [personality growth](../concepts/personality-growth.md) and its third axis enabled (matched evaluation count of 400, 5 random seeds) shows the GA ahead on every metric — reach rate (mean 0.132 vs. 0.061), occupied cells (mean 17.0 vs. 13.4), QD score (mean 4.80 vs. 3.47), and best quality (mean 0.429 vs. 0.382). Under a condition without personality growth or the third axis (an 18-cell grid), the GA and random baseline showed almost no difference (occupied cells 7.2 vs. 7.2, QD score 2.12 vs. 2.21). Each condition was only tried 5 times with no statistical test, and the personality-growth/third-axis condition also has a larger cell cap (18 → 54), so this measurement can't separate how much of the GA's lead comes from the added exploration target versus simply more cells being available. See [Personality Growth](../concepts/personality-growth.md#ga-vs-random) for details.

## Can I use it on a PC with no GPU?

Yes. The GA experiment (evolution) itself doesn't need a GPU. Local-LLM speed and memory needs depend on the model and runtime. Some models can run on a CPU, while larger models may need a GPU for practical generation speeds. Without a GPU, you can use an external API (Anthropic / OpenAI) or a CLI-based backend (Claude Code / Codex CLI), or just save prompts without generating. See [LLM Backends](../usage/llm-backends.md) and [GPU Guard](../usage/gpu-guard.md) for details.

## Why do I get the same result every time?

Because WorldBloom's individual evaluation is designed around [determinism](../concepts/determinism.md). Given the same Python version, world, genome, random seed, precedent table, and engine code, the output log matches byte for byte. Conversely, changing the engine code itself produces a different story even with the same settings.

## Can I build my own story world?

Yes. Prepare a "world" (place names, routes, characters' starting state, etc.) and a "template" declaring that genre's grammar (the action graph, canon, foreshadowing library, QD axes), and you can build worlds beyond Momotaro, romance, and detective. See [Create a World](../usage/create-world.md) for the steps.

## What's the relationship with StorySim?

WorldBloom isn't an extension of StorySim (a separate project by the same author) — it's a rebuild as its own separate project. Some elements — zones and movement, the relationship matrix, determinism conventions — were designed and implemented with reference to StorySim, but none of StorySim's own code is reused as-is.

## Commercial use / license?

The repository is published under the Apache License 2.0. See the full text at [LICENSE](https://github.com/ATO-LABO/WorldBloom/blob/main/LICENSE).

## Can I write stories in English?

The screens (the Viewer/Studio UI) are Japanese only. The bundled Momotaro, romance, and detective templates are also written entirely in Japanese, down to place names, character names, predicate target names, and foreshadowing descriptions. Building your own English world or template isn't blocked by anything — the predicates and YAML structure don't depend on Japanese — but there's no officially provided English template.

## Does it run on something other than Windows (Mac/Linux)?

The distributed EXEs are Windows builds. Source-based job management supports Windows and Linux, with additional process-supervision requirements on Linux. macOS job execution is not supported. The public samples remain available in a browser. See [Install](../getting-started/install.md).

## What's the difference between the public viewer and the distributed Studio build?

The [public viewer](https://ato-labo.github.io/WorldBloom/) and the no-install Windows `WorldBloom.exe` are both read-only, for browsing pre-generated experiments (Momotaro, romance, detective) only. To run your own GA experiments and go all the way through Sifting and synopsis/text generation, use `WorldBloom-Studio.exe` (needs a separate Python install) or run from source. See [Install](../getting-started/install.md) for details.
