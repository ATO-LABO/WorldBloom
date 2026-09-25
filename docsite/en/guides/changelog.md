---
ja_rev: "907437b76082"
---
# Changelog

The main changes that matter from a user's point of view, in date order. This isn't a list of individual commits. Get the latest release artifacts from [GitHub Releases](https://github.com/ATO-LABO/WorldBloom/releases).

## 2026-09-25

- Published this documentation site: a bilingual (Japanese/English) structure with five chapters — getting started, concepts, usage, reference, and guides
- Changed the default backend for synopsis/text generation from `codex-cli` to `llama-server` (a local LLM)

## 2026-09-22

- Completed the world self-expansion feature (proposing, approving/rejecting, and comparing world-setting expansions from experiment results)
- Unified the look of the sidebar, buttons, headings, and top bar with design tokens

## 2026-09-18

- Added the local LLM generation backend `llama-server` (Bonsai 2 27B)
- Added GPU Guard (auto-starting llama-server, coordinating GPU use with Ollama, thermal guard)

## 2026-09-15

- Submitted to the 5th AI Art Grand Prix, Division D. Published a Windows distributed build ([v1.0.0-viewer](https://github.com/ATO-LABO/WorldBloom/releases/tag/v1.0.0-viewer)): a read-only `WorldBloom.exe` and a `WorldBloom-Studio.exe` that can run GA experiments through to text generation
- Published the public viewer ([https://ato-labo.github.io/WorldBloom/](https://ato-labo.github.io/WorldBloom/)), letting anyone browse the Momotaro, romance, and detective experiments with no install

## Earlier

Changes before the above fall in the period when the core engine, GA × QD evaluation, coevolution, the output stage (synopsis generation), and the viewer were built in order, as implementation phases (Phase 0–4). See [Design Docs](../reference/design-docs.md) for the history.
