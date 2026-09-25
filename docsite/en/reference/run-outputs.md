---
ja_rev: "5e1353ae5105"
---
# Run Outputs

Running `python scripts/evolve.py --out <run dir>` produces the following under `<run dir>` (from an actual measurement — 1 generation, 2 individuals, 1 seed on `momotaro_plus2`, under `C:\Projects\WorldBloom-local\runs\docs-phase3\`).

```
<run dir>/
  archive.json          # the QD archive (the single best run per cell among those that reached the ending)
  summary.json          # per-generation aggregates (reach rate, occupied cell count, etc.)
  ga_state.json         # GA internal state for --resume (RNG state, completed generation count, etc.)
  g<N>/
    population.json     # every individual in that generation (genome, parents)
    precedent.json       # that generation's precedent table (observation counts of context → action)
    results.json         # every run's result in that generation (individual × seed)
    ind-<i>/seed-<s>/layers.jsonl   # one run's raw log (all individuals only with --keep all)
  prompts/               # generation prompts saved by synopsize.py / narrate.py
  synopses.json          # synopsize.py's output
  selection.json         # adopted/held/excluded choices made by a person on screen
  stories/               # full text written by narrate.py (adopted cells only)
```

## `archive.json`

The QD (MAP-Elites) archive itself.

```json
{"cells": {"I|low": {...}}, "volatility_thresholds": {"low_max": 0.216, "mid_max": 0.226}}
```

`cells` keys are `"<leading category>|<volatility band>"` (e.g. `I|low`). `{}` for an empty run. Each cell's value is the single best elite that survived in that cell among runs that reached the ending (genome, quality, descriptor, etc. — `gapengine/qd.py`'s `Elite`). `volatility_thresholds` are the `low_max`/`mid_max` boundary values decided from generation 0's population variance, fixed for the whole run (see [QD Map](../concepts/qd-map.md)).

## `summary.json`

```json
{"generations": [{"generation": 0, "reach_rate": 0.0, "occupied_cells": 0,
                   "action_share": {"I/craft/none": 0.5, ...}, "allies_mean_final": 2.0,
                   "archive_dissimilarity": null, "average_archive_quality": null}],
 "seed_base": 0, "seed_count": 1, "seeds": [0],
 "target_ending": ["homecoming", "homecoming_shared"], "keep": "reached"}
```

One entry per generation. Tracks `reach_rate` (fraction reaching the ending), `occupied_cells` (occupied cells in the archive), `average_archive_quality` (q̄), `archive_dissimilarity` (diversity), and more. Many values are `null` in early generations, before anything has reached the ending.

## `g<N>/results.json`

An array of results for every run (individual × seed) in that generation. Each entry looks roughly like this.

```json
{"index": 0, "generation": 0, "genome": {"category_weight": {"I": 0.18, "II": 0.86, "III": 0.78},
  "novelty_drive": 0.09, "risk_tolerance": 0.65, "stance_shift_bias": 0.58},
 "parents": [], "reach_rate": 0.0,
 "runs": [{"cell": null, "classification_status": "not_reached",
           "category": "III", "effective_sequence": [["I","investigate","none"], ["III","give_item","neutral"]],
           "allies_final": 2, "contest_turn": null, "allies_at_contest": null}]}
```

`classification_status` is whether the ending was reached (`reached`/`not_reached`). `effective_sequence` is the sequence of only the actions that actually had a non-zero effect on the seven layers (`[category, verb, role]`).

## `g<N>/ind-<i>/seed-<s>/layers.jsonl`

One run's raw log. One line = one event (JSON Lines). The first line has `kind: "header"` (genome, engine hash, precedent-table hash, world name, etc.), followed by decision events (an agent's action), derived events, and world events (`daily_event`, etc.). A real example:

```json
{"kind":"header","genome":{"category_weight":{"I":0.18,"II":0.86}},"engine_hash":"6ba7089b2241","precedent_hash":"b032ca...","world":"桃太郎＋2","protagonist":"桃太郎","antagonist":"鬼","seed":0}
{"turn":1,"day":1,"slot":null,"subject":"桃太郎","verb":"daily_event","id":"sudden_storm","result":"applied","delta":{"actor":{"stress":0.7},"objective":null,"relations":[],"targets":{}}}
```

Each line's `delta` is the seven layers' diff before and after that action. Determinism is verified as: given the same `(world, genome, seed, precedent)`, `layers.jsonl` matches byte for byte (see [Determinism](../concepts/determinism.md)). For analysis, it's converted from JSONL to Parquet (DuckDB).

## `synopses.json` / `selection.json` / `stories/`

`synopsize.py`, `narrate.py`, and the on-screen workflow connect as follows.

- `synopses.json`: `{"entries": [{"cell": "I|high", "generation": 3, "seed": 0, "layers_path": "g3/ind-31/seed-0/layers.jsonl", "quality": 0.36, "reach_rate": 0.33, "synopsis": "…", "status": "ok"}], "backend": "ollama", "archive": "…/archive.json"}`
- `selection.json`: `{"selected": ["III|high", "I|low", "V|mid"]}` (an array of cell keys, matching ✔ Adopted on screen)
- `stories/<cell>.md`: the full text for each adopted cell. `stories/index.json` holds the listing

## `runs/` vs. `control/`

- **`runs/<experiment>/`**: the raw experiment data `scripts/evolve.py`, `synopsize.py`, and `narrate.py` read and write directly (the structure above). Same location, same format whether run from the CLI or from the screens.
- **`control/`** (enabled by `viewer/server.py --control`): state the screens keep for job management. `control/configs/<config_id>/` holds run settings saved from the screen, `control/jobs/` holds job (running/completed) state, and `control/outputs/<output_id>/` holds a generation job's request, prompt, and response (`request.json`, `item.json`, `prompt.txt`, `quota.json`). This folder doesn't exist at all in the read-only viewer mode (no `--control`).
