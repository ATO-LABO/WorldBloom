---
ja_rev: "63986ac88ef0"
---

# WorldBloom Docs

A tool that lets you fix a story's **setting and ending first**, then tries thousands of simulated paths between them and picks the interesting ones that actually reach that ending. A genetic algorithm (GA) drives the simulation, and only the chosen paths are turned into prose by an LLM.

## Working with the current source

[Create a World](usage/create-world.md) takes you through three stages: prepare a draft, edit its contents, then review and adopt it. Save and resume drafts, review optional AI refinements, edit the confirmed world’s conditions, and run an experiment before [choosing results](usage/read-results.md) and [generating text](usage/generate-text.md).

The current source edits and runs new-format worlds. For a first run, use 桃太郎＋3 (Momotaro +3, `momotaro_plus3_new`). Legacy worlds and previous results are retained, with no automatic format rewrite. EXE downloads follow their own Release contents, which differ from source changes.

## Three ways to try it

1. **Public viewer (no install)**: open [https://ato-labo.github.io/WorldBloom/](https://ato-labo.github.io/WorldBloom/). Browse three sample experiments (Momotaro, romance, detective).
2. **Distributed exe (Windows)**: a read-only Viewer, and a full-featured Studio that can run GA experiments and generate text. See [Install](getting-started/install.md).
3. **From source**: clone the repository and run it with Python. See [Install](getting-started/install.md).

## Site structure

- **Getting Started**: introduction and a first walkthrough (Quickstart)
- **Concepts**: the seven layers, genome, QD map, Sifting, determinism
- **Usage**: screen-by-screen operation, settings, LLM backends
- **Reference**: CLI arguments, settings.json, templates, output files, HTTP API
- **Guides**: troubleshooting, FAQ, changelog

The screens themselves are Japanese-only for now (see [Install](getting-started/install.md) for how button names are shown). Background and design philosophy are written up on the ATOM-BOX site ([https://www.atom-box.jp/worldbloom/](https://www.atom-box.jp/worldbloom/), Japanese).
