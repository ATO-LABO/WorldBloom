---
ja_rev: "4991e96b42e4"
---
# Changelog

The main changes that matter from a user's point of view, in date order. This isn't a list of individual commits. Get the latest release artifacts from [GitHub Releases](https://github.com/ATO-LABO/WorldBloom/releases).

## 2026-09-26

- Published Windows distributed build [v1.1.0](https://github.com/ATO-LABO/WorldBloom/releases/tag/v1.1.0), collecting the changes since v1.0.0-viewer
- World self-expansion now also takes demand from route-layer signals: it detects where the protagonist keeps "groping" (investigating without knowing what to do) in the same place, or can't form any plan to the ending (e.g. no reachable way to get 「縄」 (rope)), and feeds these through the existing propose → trial → effect → screen flow. Expansion patches can now add an acquisition source to an existing item or fact (`add.sources`)
- Growing worlds: carry the previous run's individuals into generation 0, chain runs to grow a world, and bring surviving expansions into other worlds as genre assets

## 2026-09-25

- Added the [route layer](../concepts/route-layer.md): classifies protagonist decisions as advance, prepare, detour (body/ignorance/belief/motive/no reason), or lost, and reins in unreasoned detours with weight ρ. A motive table (`motives.yaml`) can give a detour a reason (only 桃太郎＋2 / Peach Boy+2 has this so far). Added the "05. Route" section to run settings, and reason badges (timeline, four-field panel, breakdown at the top of the cell page) to the results screen
- Fixed a bug where GA runs launched from the screen (05. Route, 04. Rationality) didn't actually apply ρ, κ, or the rationality details you set. **Experiments created and run from the screen before this fix ran with ρ and κ disabled (ρ=0, κ off), regardless of what you specified**
- Published this documentation site: a bilingual (Japanese/English) structure with five chapters — getting started, concepts, usage, reference, and guides
- Changed the default backend for synopsis/text generation from `codex-cli` to `llama-server` (a local LLM)

## 2026-09-22

- Completed the world self-expansion feature (proposing, approving/rejecting, and comparing world-setting expansions from experiment results)
- Unified the look of the sidebar, buttons, headings, and top bar with design tokens

## 2026-09-18

- Added the local LLM generation backend `llama-server` (Bonsai 2 27B)
- Added GPU Guard (auto-starting llama-server, coordinating GPU use with Ollama, thermal guard)

## 2026-09-15

- Submitted to the 5th AI Art Grand Prix, Division D. Published a Windows distributed build ([v1.0.0-viewer](https://github.com/ATO-LABO/WorldBloom/releases/tag/v1.0.0-viewer), tagged sometime across 09-15 to 09-16): a read-only `WorldBloom.exe` and a `WorldBloom-Studio.exe` that can run GA experiments through to text generation
- Published the public viewer ([https://ato-labo.github.io/WorldBloom/](https://ato-labo.github.io/WorldBloom/)), letting anyone browse the Momotaro, romance, and detective experiments with no install

## Earlier

Changes before the above fall in the period when the core engine, GA × QD evaluation, coevolution, the output stage (synopsis generation), and the viewer were built in order, as implementation phases (Phase 0–4). See [Design Docs](../reference/design-docs.md) for the history.
