---
ja_rev: "8d569e91f3b9"
---
# LLM Backends

AI supports optional world-draft generation/refinement and [synopsis/prose generation](generate-text.md). GA exploration and prose generation are separate, but rationality judging with κ>0 also calls the chosen judge. Check the saved generation destination in “AIの設定” (AI settings) under ⚙ [Settings](settings.md).

## Available backends

| Backend | Shown on screen | What it needs |
|---|---|---|
| `ollama` | ローカル Ollama (Local Ollama) | Ollama itself installed, model pulled |
| `llama-server` | ローカルLLM（llama-server） (Local LLM (llama-server)) | llama-server itself (an OpenAI-compatible API server) |
| `claude-cli` | Claude Code CLI | the `claude` command on PATH |
| `codex-cli` | Codex CLI | the `codex` command on PATH |
| `anthropic` | Anthropic API | an API key |
| `openai` | OpenAI API | an API key |
| `none` | 生成しない（プロンプト保存のみ） (Don't generate (save prompts only)) | nothing (doesn't generate text, just saves the prompts) |

The default backend is `llama-server` (`codex-cli` in the v1.0.0-viewer distributed build). Ollama and llama-server itself are not bundled with WorldBloom and must be installed separately.

### Who decides the model name { #model-defaults }

**The current settings screen** focuses on inspecting the saved destination and selecting Gemma 4. For another backend/model, configure `output.default_backend` and the backend section in `settings.json`. The settings API allows `claude-cli` to use `claude-sonnet-5` when the model is empty; other generation backends require an explicit model name.

**On the CLI** (running `scripts/synopsize.py` / `scripts/narrate.py` directly), if `settings.json` has no `model` set, each backend falls back to:

| Backend | Default model when unset in settings.json |
|---|---|
| `ollama` | `qwen3.6:35b` |
| `llama-server` | `bonsai2-27b` |
| `anthropic` | `claude-opus-5` |
| `openai` | `gpt-5.6` |
| `claude-cli` | `claude-sonnet-5` |
| `codex-cli` | none specified (called without `-m`, deferring to codex's own default model) |

The bundled samples (`samples/`) and the submission were generated with `qwen3.5:9b-q4_K_M` (Ollama). Switching to a larger model such as the default `qwen3.6:35b` changes the generation conditions.

## Gemma 4 and model-specific profiles

Selecting Gemma 4 in AI settings and saving uses Ollama's `gemma4:e4b-it-qat` and its model-specific profile. Run `ollama pull gemma4:e4b-it-qat` on the PC hosting Ollama. Refreshing installation status alone does not change the saved generation destination.

`output.<backend>.model_options.<model>` applies `think` and `options` only when that model is selected. This keeps Gemma's settings from being applied indiscriminately to other Ollama models.

## Using Ollama

1. Install [Ollama](https://ollama.com)
2. Pull a model (e.g. `ollama pull qwen3.5:9b-q4_K_M`)

When running from source, you can also write this directly in `settings.json`.

```json
{
  "output": {
    "default_backend": "ollama",
    "ollama": {
      "model": "qwen3.5:9b-q4_K_M",
      "base_url": "http://localhost:11434",
      "think": false,
      "options": {"num_ctx": 16384, "num_predict": 4096}
    }
  }
}
```

`options` is merged on top of the defaults (`num_ctx` 16384, `num_predict` 4096), so you only need to write the keys you want to change. `think` defaults to `false` to hold down thinking tokens. `settings.json` can contain API keys, so it's gitignored.

## Using Bonsai 2 27B (llama-server) { #bonsai2-llama-server }

PrismML's Ternary Bonsai 2 27B uses a proprietary quantization format, so it doesn't run on Ollama — only on the `llama-server` (OpenAI-compatible API) from PrismML's fork of llama.cpp.

1. Get the forked binary: from [PrismML-Eng/llama.cpp releases](https://github.com/PrismML-Eng/llama.cpp/releases), for Windows grab both the `win-cuda-12.4` `llama-...-bin-...zip` and the `cudart-...zip` (the CUDA runtime ships as a separate zip, so you need both)
2. Get the GGUF: `PTQ1_0` from Hugging Face `prism-ml/Ternary-Bonsai-2-27B-gguf`
3. Start the server.

```
llama-server.exe -m Ternary-Bonsai-2-27B-PTQ1_0.gguf --alias bonsai2-27b -ngl 99 -np 1 -c 10240 -fa on --port 8089 --host 127.0.0.1 --reasoning-budget 2048 --reasoning-budget-message "思考の上限に達した。ここで思考を終え、直ちに最終回答の本文だけを書く。"
```

`--alias` is required (omitting it turns the model name into the file path, which won't match `model` in `settings.json`). `--reasoning-budget` is also required. The reasoning-token cap only takes effect via this server startup flag — it's ignored if sent in the request — so without it the response gets cut off with `finish_reason: "length"`, which WorldBloom treats as a failure.

`settings.json` (if keeping it started manually):

```json
{
  "output": {
    "default_backend": "llama-server",
    "llama-server": {
      "base_url": "http://127.0.0.1:8089",
      "model": "bonsai2-27b",
      "think": true,
      "options": {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0, "presence_penalty": 1.0, "max_tokens": 8192}
    }
  }
}
```

Notes:

- With 8GB of VRAM, this can't coexist with an Ollama model. Before generating, check with `ollama ps` that the Ollama model is unloaded (see [GPU Guard](gpu-guard.md) for a way to automate this)
- A laptop GPU can reach 86°C under sustained generation. Take breaks during long continuous runs

## CLI backends (claude-cli / codex-cli)

The `claude` or `codex` executable must be on PATH and authenticated in advance. A missing executable cannot be used.

## API backends (anthropic / openai)

An API key is required. The current AI screen does not expose a generic API-key input, so check the corresponding backend section in `settings.json`. Never include keys in public repositories or world ZIP files.

## Don't generate (none)

Doesn't call the LLM at all — only saves the prompts that would be used for generation. Useful for checking the target scope and candidate count for synopsis/text generation before you've set up a backend.
