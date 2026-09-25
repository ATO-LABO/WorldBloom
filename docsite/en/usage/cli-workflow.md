---
ja_rev: "4d2b001d7996"
---
# CLI Workflow

How to go from a GA experiment to synopsis/text generation using only the command line, without the Studio screens. See [CLI](../reference/cli.md) for the full list of arguments.

## 1. Run a GA experiment

```
python scripts/evolve.py --project projects/momotaro --template templates/momotaro \
  --out <output dir>/exp1 --generations 20 --population 100 --seeds 3 --keep reached --processes 4
```

- `--project` / `--template` can be any of the bundled `basic` / `momotaro` / `momotaro_plus` / `momotaro_plus2` / `detective` / `romance` (the full list is under `templates/`; `basic` holds the shared base rules and is rarely specified on its own as a genre)
- An output directory outside the repository is recommended (`.gitignore` ignores `runs/`, but you shouldn't keep a large volume of experiment output inside the repository anyway)
- Rough time cost: scales close to linearly with generations × population × seeds. The default size (20 generations × 100 population × 3 seeds) takes about an hour on 20 cores as a guideline (the process count is set under "Compute" in [Settings](settings.md))
- On completion, the grid (archive) is written to `<output dir>/exp1/archive.json`. `cells=<count>` is the number of representative individuals that reached the ending and stayed in the grid

You can also run a control experiment that measures the reach rate and diversity produced by purely random policies.

```
python scripts/random_baseline.py --project projects/momotaro --template templates/momotaro --out <output dir>/baseline
```

## 2. Generate synopsis/text

```
python scripts/synopsize.py --archive <output dir>/exp1/archive.json --runs <output dir>/exp1 \
  --out <output dir>/exp1/synopses.json --backend ollama --project projects/momotaro --template templates/momotaro

python scripts/narrate.py --archive <output dir>/exp1/archive.json --selection <output dir>/exp1/selection.json \
  --out <output dir>/exp1/stories --backend ollama --project projects/momotaro --template templates/momotaro
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
