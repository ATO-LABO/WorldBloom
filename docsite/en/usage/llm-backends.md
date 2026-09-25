---
ja_rev: "e4768d5b342b"
---
# LLM Backends

An LLM is only used when [generating text](generate-text.md) (synopsis/prose). The GA experiment itself never needs one. Pick the backend under the "文章生成" (Text generation) tab of ⚙ [Settings](settings.md).

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

**When using the ⚙ 全体設定 (Global settings) screen**, only `claude-cli` falls back to `claude-sonnet-5` when the model field is empty. Every other backend requires you to enter a model name (trying to check the connection with it empty shows "モデルを指定してください", Please specify a model).

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

## Using Ollama

1. Install [Ollama](https://ollama.com)
2. Pull a model (e.g. `ollama pull qwen3.5:9b-q4_K_M`)

When running from source, you can also write this directly in `settings.json`.

```json
{
  "output": {
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

These work as long as the `claude` or `codex` command is on PATH. If the executable isn't found, "接続を確認" (Check connection) in ⚙ 全体設定 (Global settings) reports failure.

## API backends (anthropic / openai)

These need an API key. Save it separately under the "APIキー" (API key) field in ⚙ 全体設定 (Global settings) (a saved value is never shown on screen).

## Don't generate (none)

Doesn't call the LLM at all — only saves the prompts that would be used for generation. Useful for checking the target scope and candidate count for synopsis/text generation before you've set up a backend.
