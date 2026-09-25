---
ja_rev: "673e11f85db4"
---
# settings.json

The settings file shared across every world. Normally edited from the **⚙ 設定** (Settings) screen, but when running scripts directly from the CLI it's read via `--settings` (or the location below if omitted).

## Location

| How you run it | Location |
|---|---|
| From source | `settings.json` at the repository root |
| Distributed exe | `app/settings.json`, in the folder next to the exe |

It's excluded from version control by `.gitignore` (it may hold `anthropic`/`openai` API keys). It's fine for it not to exist — everything then falls back to its default.

## `output` (text generation)

`output.default_backend` is the backend currently in use (`ollama` / `llama-server` / `claude-cli` / `codex-cli` / `anthropic` / `openai` / `none`; default `llama-server`). Each backend's settings live under `output.<backend>`.

### Keys common to every backend

| Key | Type | Default | Meaning |
|---|---|---|---|
| `model` | string | varies by backend (table below) | The model name to use |
| `limits.max_calls` | integer | 0 for `none`, 1 otherwise | Max candidates handled per generation action |
| `limits.call_timeout_seconds` | integer | 900 for ollama/llama-server, 180 otherwise | Timeout (seconds) per candidate |
| `limits.wall_seconds` | integer | 3600 for ollama/llama-server, 240 otherwise | Timeout (seconds) for the whole generation job |
| `limits.max_saved_response_bytes` | integer | 128000 | Max bytes of the LLM's response to save |
| `verified_models` | array of strings | `[]` | History of model names for which the Settings screen's "接続を確認" (Check connection) succeeded (newest first, capped at 20). Not meant to be edited by hand |

Defaults used when `model` is unset (`gapengine/synopsis.py`):

| Backend | Default model |
|---|---|
| `ollama` | `qwen3.6:35b` |
| `llama-server` | `bonsai2-27b` |
| `anthropic` | `claude-opus-5` |
| `openai` | `gpt-5.6` |
| `claude-cli` | `claude-sonnet-5` |
| `codex-cli` | none (no `-m` is passed, leaving it to codex's own default) |

### Additional keys per backend

| Backend | Key | Type | Default | Meaning |
|---|---|---|---|---|
| `anthropic`, `openai` | `api_key` | string | none | The API key. Once saved from the Settings screen, the value itself is never shown again on screen (the read-only API only returns the `has_api_key` boolean) |
| `ollama` | `base_url` | string | `http://localhost:11434` | The Ollama server's URL |
| `ollama` | `think` | boolean | `false` | Whether to enable thinking mode |
| `ollama` | `options` | object | `{}` | Extra parameters merged straight into `/api/chat`'s `options` (temperature, etc.) |
| `ollama` | `seed` | integer | none | If set, applied to `options.seed` |
| `llama-server` | `base_url` | string | `http://127.0.0.1:8089` | llama-server's (OpenAI-compatible API) URL |
| `llama-server` | `think` | boolean | `true` | Applied to `chat_template_kwargs.enable_thinking` |
| `llama-server` | `options` | object | `{}` | Extra parameters merged into the `/v1/chat/completions` payload (`model`, `messages`, `stream`, and `chat_template_kwargs` are never overridden) |
| `llama-server` | `launch` | array of strings | none | The command used to auto-start the server if `base_url` doesn't respond (see the example in [GPU Guard](../usage/gpu-guard.md)) |
| `llama-server` | `startup_seconds` | number | 180 | Seconds to wait for `/health` to respond after auto-starting |
| `claude-cli` | `command` | string | `claude` | The executable name to run |
| `codex-cli` | `command` | string | `codex` | The executable name to run |
| `anthropic` | `max_tokens` | integer | 4096 | Max tokens in the response |

### `output.gpu_guard` (optional; setting it enables it)

A mechanism that coordinates GPU use between local LLMs (Ollama and llama-server). See [GPU Guard](../usage/gpu-guard.md) for the full behavior.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `thermal.pause_at` | number (°C) | 78 | Pause before generating once the temperature reaches this or above |
| `thermal.resume_at` | number (°C) | 70 | Resume once the temperature drops to this or below |
| `thermal.poll_seconds` | number (seconds) | 15 | How often to check while waiting on the thermal guard |
| `thermal.max_wait_seconds` | number (seconds) | 600 | How long to wait before giving up if it still hasn't dropped (giving up doesn't fail the generation itself) |
| `ollama_base_url` | string | `http://localhost:11434` (same default as `gapengine/ollama.py`) | The URL used to judge whether Ollama is in use |
| `observe_seconds` | number (seconds) | 15 | How long to observe Ollama for, to judge whether it's actually in use |

## `evolution` (GA compute resources)

| Key | Type | Default | Meaning |
|---|---|---|---|
| `evolution.processes` | integer (1 to CPU core count) | `min(8, os.cpu_count())` | How many individual evaluations the GA runs at once. Only changes speed — results (`archive.json`, etc.) stay the same |

## Environment variables

Items switched via environment variables rather than `settings.json` (only ones confirmed to actually exist, by searching for `os.environ`).

| Environment variable | Default | Meaning |
|---|---|---|
| `WORLDBLOOM_PYTHON` | `sys.executable` if unset | The real Python executable used by the GA experiment's subprocesses (`execution/jobs.py`, `worker.py`, `generation.py`, `output_requests.py`, `configs.py`). In the frozen exe (Studio build), this is set at startup to whatever `shutil.which("python"/"python3"/"py")` finds |
| `WORLDBLOOM_GPU_LEASE_DIR` | `%LOCALAPPDATA%\WorldBloom` if unset | Where the GPU lease (OS file lock) is kept |
| `WORLDBLOOM_STUDIO` | unset | Set to `"1"` to force the distributed Studio mode (full functionality) during development |
| `WORLDBLOOM_APP_ROOT` | `app/` next to the exe if unset | The root the distributed exe searches under for `viewer`/`execution`/`gapengine`/`engine`/`scripts`/`templates`/`projects` |
| `PORT` | 5401 | The port `viewer/server.py` listens on (when `--port` isn't given) |

## Full example

```json
{
  "output": {
    "default_backend": "llama-server",
    "llama-server": {
      "base_url": "http://127.0.0.1:8089",
      "model": "bonsai2-27b",
      "think": true,
      "options": {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0,
                  "presence_penalty": 1.0, "max_tokens": 8192},
      "launch": ["C:/path/to/llama-server.exe", "-m", "C:/path/to/model.gguf",
        "--alias", "bonsai2-27b", "-ngl", "99", "-np", "1", "-c", "10240",
        "--host", "127.0.0.1", "--port", "8089"],
      "startup_seconds": 180,
      "limits": {"max_calls": 1, "call_timeout_seconds": 900,
                 "wall_seconds": 3600, "max_saved_response_bytes": 128000}
    },
    "anthropic": {
      "api_key": "sk-ant-...",
      "model": "claude-opus-5"
    },
    "gpu_guard": {
      "thermal": {"pause_at": 78, "resume_at": 70, "poll_seconds": 15, "max_wait_seconds": 600},
      "ollama_base_url": "http://localhost:11434",
      "observe_seconds": 15
    }
  },
  "evolution": {
    "processes": 8
  }
}
```

Once an API key is saved from the Settings screen, its value is no longer shown on screen after that (only the `has_api_key` boolean is returned).
