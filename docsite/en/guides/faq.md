---
ja_rev: "19139d2636fb"
---
# FAQ

## Does the GA experiment (evolution) need an LLM?

No. No LLM is involved in the GA experiment itself (simulating the world, evolving the genome, accumulating results into the QD map). The LLM is only used for [generating synopses and full text](../usage/generate-text.md), after a person has chosen candidates left in the grid. If you just want to see results without generating text, set the backend to "生成しない" (Don't generate, `none`) in ⚙ 全体設定 (Global settings) — it only saves the prompts and skips actual generation. See [LLM Backends](../usage/llm-backends.md) for details.

## Why doesn't event generation use an LLM?

Letting an LLM write the sequence of events (who did what) directly would make it impossible to mechanically trace why things unfolded the way they did. By limiting event generation to simulation and random draws alone, WorldBloom guarantees [determinism](../concepts/determinism.md) and can mechanically show which generation and parent a given story came from, and at what branch point, as [lineage and turning points](../concepts/sifting.md#compare-lineage-turning). See [Overview](../concepts/overview.md) for details.

## Can I use it on a PC with no GPU?

Yes. The GA experiment (evolution) itself doesn't need a GPU. A GPU is only needed if you use a local LLM (Ollama or llama-server) for synopsis/text generation. Without a GPU, you can use an external API (Anthropic / OpenAI) or a CLI-based backend (Claude Code / Codex CLI), or just save prompts without generating. See [LLM Backends](../usage/llm-backends.md) and [GPU Guard](../usage/gpu-guard.md) for details.

## Why do I get the same result every time?

Because WorldBloom's individual evaluation is designed around [determinism](../concepts/determinism.md). Given the same world, genome, random seed, precedent table, and engine code, the output log matches byte for byte. Conversely, changing the engine code itself produces a different story even with the same settings.

## Can I build my own story world?

Yes. Prepare a "world" (place names, routes, characters' starting state, etc.) and a "template" declaring that genre's grammar (the action graph, canon, foreshadowing library, QD axes), and you can build worlds beyond Momotaro, romance, and detective. See [Create a World](../usage/create-world.md) for the steps.

## What's the relationship with StorySim?

WorldBloom isn't an extension of StorySim (a separate project by the same author) — it's a rebuild as its own separate project. Some elements — zones and movement, the relationship matrix, determinism conventions — were designed and implemented with reference to StorySim, but none of StorySim's own code is reused as-is.

## Commercial use / license?

The repository is published under the Apache License 2.0. See the full text at [LICENSE](https://github.com/ATO-LABO/WorldBloom/blob/main/LICENSE).

## Can I write stories in English?

The screens (the Viewer/Studio UI) are Japanese only. The bundled Momotaro, romance, and detective templates are also written entirely in Japanese, down to place names, character names, predicate target names, and foreshadowing descriptions. Building your own English world or template isn't blocked by anything — the predicates and YAML structure don't depend on Japanese — but there's no officially provided English template.

## Does it run on something other than Windows (Mac/Linux)?

The distributed exes (Viewer/Studio) are Windows builds only. On Mac/Linux, clone the repository and run it from Python instead (the from-source steps in [Install](../getting-started/install.md)).

## What's the difference between the public viewer and the distributed Studio build?

The [public viewer](https://ato-labo.github.io/WorldBloom/) and the no-install Windows `WorldBloom.exe` are both read-only, for browsing pre-generated experiments (Momotaro, romance, detective) only. To run your own GA experiments and go all the way through Sifting and synopsis/text generation, use `WorldBloom-Studio.exe` (needs a separate Python install) or run from source. See [Install](../getting-started/install.md) for details.
