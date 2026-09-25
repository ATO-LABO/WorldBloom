---
ja_rev: "62512f147383"
---

# WorldBloom Docs

A tool that lets you fix a story's **setting and ending first**, then tries thousands of simulated paths between them and picks the interesting ones that actually reach that ending. A genetic algorithm (GA) drives the simulation, and only the chosen paths are turned into prose by an LLM.

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
