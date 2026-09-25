---
ja_rev: "19ff03f4d37c"
---
# Settings

Open this from **⚙ 全体設定** (Global settings) at the top right of the screen (or `/configs`). Changes here aren't tied to a specific world — they're **shared across every world**. The underlying file is at the repository root when running from source, or `app/settings.json` (inside the `app` folder next to the exe) in the distributed exe build. See [settings.json](../reference/settings-json.md) for the full file spec. There are four tabs.

## Text generation { #output-settings }

Settings used for [generating synopses/text](generate-text.md).

| Item | Contents | Default |
|---|---|---|
| 生成方式 (Backend) | Claude Code CLI / Codex CLI / Anthropic API / OpenAI API / ローカル Ollama (Local Ollama) / ローカルLLM（llama-server） (Local LLM (llama-server)) / 生成しない（プロンプト保存のみ） (Don't generate (prompts only)) | `llama-server` (`codex-cli` in the v1.0.0-viewer distributed build) |
| モデル (Model) | Model name to use | `claude-cli` falls back to `claude-sonnet-5` when left empty. Every other backend requires it (see [LLM Backends](llm-backends.md#model-defaults)) |
| APIキー (API key) | Shown only when Anthropic API / OpenAI API is chosen. A saved value is never shown on screen | — |
| 候補数の上限 (Candidate count limit) | Max candidates handled per generation action | `none` is 0, everything else is 1 |
| 1件の待ち時間 (Per-item timeout) | Timeout (seconds) per candidate | 900 seconds for Ollama/llama-server, 180 seconds for everything else |
| 全体の待ち時間 (Overall timeout) | Timeout (seconds) for the whole generation job | 3600 seconds for Ollama/llama-server, 240 seconds for everything else |
| 応答の保存上限 (Response save limit, advanced) | Max bytes of the LLM's response to save | 128000 bytes |

The "接続を確認" (Check connection) button only checks the backend/runtime — it doesn't generate anything. Saved settings apply to generation jobs started after the save (jobs already in progress are unaffected).

## Compute

Settings for how much this PC parallelizes GA experiments.

| Item | Contents | Default |
|---|---|---|
| GA の並列数 (GA process count, processes) | How many individual evaluations run at once in the GA. **Only affects speed, never the results** | `min(8, this PC's CPU core count)` |

On a development machine (20 cores), measurements showed speed plateauing around 8 processes. Applies to GA experiments started after the save.

## Saved run-setting versions

A list of previously saved [run settings](run-settings.md). Saved versions can't be edited (use "複製して調整", Duplicate and adjust, to duplicate one if you want different contents). Each entry shows its world, genre, run size (generations × population × seeds), creation time, and duplication source.

## Genres

A list of registered genres (templates). For each genre you can see which worlds use it and its config files (action links, situational conventions, action effects, progression rules, candidate classification axes). Edit via "ジャンルを編集" (Edit genre) (see [Create a World](create-world.md#advanced-settings)).
