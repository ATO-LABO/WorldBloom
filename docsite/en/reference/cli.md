---
ja_rev: "a442841ce61c"
---
# CLI

All commands run as `python <script> ...` from the repository root. The full help text is generated from each script's `--help` (`docsite/gen_cli.py`; regenerate it if a script's arguments change). `jev_*.py` and `rationality_probe.py` are dev-only measurement scripts and aren't covered here.

The help text below is printed by the scripts themselves and is partly in Japanese.

## scripts/evolve.py — run a GA experiment

Runs one deterministic MAP-Elites experiment. `--project` (world), `--template` (genre), and `--out` (output dir) are required.

```
--8<-- "cli/evolve.txt"
```

Example:

```powershell
python scripts/evolve.py --project projects/momotaro_plus2 --template templates/momotaro_plus2 `
  --out C:\WorldBloom-data\runs\my-exp --generations 20 --population 100 --seeds 3
```

Point the output directory at somewhere local, not the repository on Google Drive (see [Run Outputs](run-outputs.md)).

## scripts/synopsize.py — generate synopses

Generates a synopsis for each cell in `archive.json`. Omit `--cells` to update every cell, or pass it to update only the named cells (other entries are kept as-is).

```
--8<-- "cli/synopsize.txt"
```

## scripts/narrate.py — generate full text

Turns only the cells adopted in `selection.json` into full prose. Passing `--synopses` includes the synopsis content in the prompt.

```
--8<-- "cli/narrate.txt"
```

## scripts/random_baseline.py — measure the random baseline

A control experiment measuring the reach rate and diversity produced by purely random individuals with no policy (genome).

```
--8<-- "cli/random_baseline.txt"
```

## scripts/readable.py — reader-facing summaries

`generate` produces a short LLM summary, and `approve` has a human sign off on that output (with a sha256).

```
--8<-- "cli/readable.txt"
```

## scripts/world_patch.py — world self-expansion patches

Has subcommands for `propose`, `check` (trial run), `approve`/`reject`, and `list`/`reopen`/`repair`. See [Create a World](../usage/create-world.md#world-expansion) for details.

```
--8<-- "cli/world_patch.txt"
```

## scripts/world_demand.py — world-expansion demand report (read-only)

A read-only CLI that reads an experiment directory and reports which places and actions are missing elements, based on how often they came up empty (the groundwork for `world_patch.py propose`).

```
--8<-- "cli/world_demand.txt"
```

## scripts/export_static.py — export a public static HTML site

Exports experiment results as a read-only static site (for public release).

```
--8<-- "cli/export_static.txt"
```

## scripts/pack_samples.py — extract viewer samples

Copies just the minimal set of files the distributed viewer (Viewer exe) needs to read, from a run's output, into a separate folder.

```
--8<-- "cli/pack_samples.txt"
```

## viewer/server.py — start the screens

Without `--control`, it runs as a read-only viewer where every write API is rejected (see [HTTP API](http-api.md)).

```
--8<-- "cli/server.txt"
```

Example (starting with full functionality):

```powershell
python viewer/server.py --runs C:\WorldBloom-data\runs --control C:\WorldBloom-data\control
```
