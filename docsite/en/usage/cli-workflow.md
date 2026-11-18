---
ja_rev: "5538d402ac90"
---
# CLI Workflow

How to go from a GA experiment to synopsis/text generation using only the command line, without the Studio screens. See [CLI](../reference/cli.md) for the full list of arguments.

## 1. Run a GA experiment

```
python scripts/evolve.py --project projects/momotaro_plus3_new --template templates/momotaro_plus3 \
  --out <output dir>/exp1 --generations 20 --population 100 --seeds 3 --keep reached --processes 4
```

- The bundled current-format example is `--project projects/momotaro_plus3_new --template templates/momotaro_plus3`. Legacy worlds such as `momotaro`, `momotaro_plus2`, `detective`, and `romance` remain on disk but cannot run unchanged through the current loader. For a newly created world, use its corresponding settings.
- An output directory outside the repository is recommended (`.gitignore` ignores `runs/`, but you shouldn't keep a large volume of experiment output inside the repository anyway)
- Adding `--personality-growth` enables [personality growth](../concepts/personality-growth.md) (off by default). When it's on and the genre's `qd.yaml` declares `arc_bins`, the `selection.json` cell keys below can become three-part (e.g. `I|low|small`)
- Runtime depends on generations, population, seeds, and world complexity. Start with a small run and scale up based on measured time. Set CLI concurrency with `--processes`.
- On completion, the grid (archive) is written to `<output dir>/exp1/archive.json`. `cells=<count>` is the number of representative individuals that reached the ending and stayed in the grid

You can also run a control experiment that measures the reach rate and diversity produced by purely random policies.

```
python scripts/random_baseline.py --project projects/momotaro_plus3_new --template templates/momotaro_plus3 --out <output dir>/baseline
```

## 2. Generate synopsis/text

```
python scripts/synopsize.py --archive <output dir>/exp1/archive.json --runs <output dir>/exp1 \
  --out <output dir>/exp1/synopses.json --backend ollama --project projects/momotaro_plus3_new --template templates/momotaro_plus3

python scripts/narrate.py --archive <output dir>/exp1/archive.json --selection <output dir>/exp1/selection.json \
  --out <output dir>/exp1/stories --backend ollama --project projects/momotaro_plus3_new --template templates/momotaro_plus3
```

- `--backend` can be `claude-cli` / `codex-cli` / `anthropic` / `openai` / `ollama` / `llama-server` / `none` (see [LLM Backends](llm-backends.md#model-defaults)). `none` skips the LLM call entirely and only saves the prompts
- The `--backend ollama` example above requires the default model `qwen3.6:35b` if there is no `settings.json` (or it has no `model` set). If you already pulled something like `qwen3.5:9b-q4_K_M` following [LLM Backends](llm-backends.md), put that model name under `output.ollama.model` in `settings.json`
- `selection.json` has the shape `{"selected": ["III|high"]}` (the key is a grid cell = `category|volatility band`; examples actually seen: `I|mid`, `II|high`, `III|low`, etc.)
- Reviewing synopses and picking grid cells can also be done from the viewer screens started with `--control`

## 3. Determinism / regression tests

```
python -m unittest discover -s tests -v
```

Checks determinism, that the neutral genome applies no modulation, zero premise violations, foreshadowing penalties, and vitality transitions. Always run this after changing `engine/` or `gapengine/`. See [Determinism](../concepts/determinism.md) for details.
