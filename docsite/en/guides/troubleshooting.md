---
ja_rev: "ea93e6869372"
---
# Troubleshooting

## Won't start

| Symptom | Cause | Fix |
|---|---|---|
| Windows SmartScreen warns you | The distributed exe is unsigned | Click "More info" then "Run anyway". See [Install](../getting-started/install.md) |
| `pywebview の読み込みに失敗しました。配布フォルダが壊れている可能性があります。展開し直してください。` (Failed to load pywebview. The distribution folder may be corrupted. Please re-extract it.) | The distributed zip was extracted incompletely | Re-extract the zip |
| `WorldBloom.exe と同じフォルダに app フォルダが見つかりません。` (No app folder found next to WorldBloom.exe.) (Studio shows `WorldBloom Studio.exe と同じフォルダに...`, "...next to WorldBloom Studio.exe") | Only the exe was moved / distributed on its own | Keep the distribution folder's layout intact (`WorldBloom.exe` / `WorldBloom-Studio.exe` alongside the `app/` folder) and place it together in the same location |
| Studio shows "GA実験の実行にはPython 3.11以上（PyYAML導入済み）が別途必要です" (Running a GA experiment separately needs Python 3.11+ with PyYAML installed) | On Studio startup, `python`/`python3`/`py` wasn't found on PATH, or what was found is below 3.11 or missing PyYAML | Install from [python.org](https://www.python.org/downloads/), checking "Add python.exe to PATH" in the installer. After installing, run `pip install pyyaml` and restart Studio |

## While a run is in progress

| Symptom | Cause | Fix |
|---|---|---|
| A GA experiment immediately stops with `preparation_failed` (failed to fix inputs and code) | A problem with the run-settings input, or — when running from source — a required file such as `requirements.txt` is missing | Check the settings input. In the source build, confirm `requirements.txt` is present in the folder being read |
| You want partial results after stopping mid-run | Stopping is working as intended | "Stopping keeps results through the last completed generation." See [Run Settings](../usage/run-settings.md) |
| Won't start because a port is in use (when starting `viewer/server.py` from source) | Another process is using the specified port | Restart with a different `--port` number |

## Getting a 403 when operating the Viewer (read-only build)

WorldBloom.exe (the read-only build) still shows on-screen controls like the adopt/select checkboxes, but every write operation (POST) is rejected by the server as `403 read-only build`. **This is expected behavior.** Use WorldBloom-Studio.exe for editing and running. See [Install](../getting-started/install.md) for details.

## LLM generation fails

| Symptom | Cause | Fix |
|---|---|---|
| モデルを指定してください (Please specify a model) | No model name entered in ⚙ Settings (no backend other than `claude-cli` fills this in automatically) | Enter a model name under the "文章生成" (Text generation) tab in ⚙ Settings. See [LLM Backends](../usage/llm-backends.md#model-defaults) |
| 実行ファイルが見つかりません (Executable not found) | `claude-cli` / `codex-cli` is selected but the `claude`/`codex` command isn't on PATH | Install the relevant CLI and add it to PATH |
| 資格情報がありません (No credentials) | `anthropic` / `openai` is selected but no API key is saved | Save a key under the "APIキー" (API key) field in ⚙ Settings |
| そのモデルは見つかりません (That model was not found) | The given model name doesn't exist on the backend (not pulled in Ollama, doesn't exist on the API, etc.) | Verify the model name, or fetch it following [LLM Backends](../usage/llm-backends.md) |
| サーバーに接続できません (Can't reach the server) | Ollama / llama-server itself isn't running, or `base_url` is wrong | Start the server (or set up auto-start via [GPU Guard](../usage/gpu-guard.md)), or check `base_url` |
| settings.json を読めません (Can't read settings.json) | `settings.json` is corrupted or unreadable | Check the file's contents (JSON syntax) |
| モデル名が不正です (Invalid model name) | The model name contains disallowed characters | Use only alphanumerics, `.`, `_`, `:`, `/`, `-` |
| The response cuts off mid-way (`finish_reason: "length"`) | Especially with llama-server / Bonsai 2, `--reasoning-budget` wasn't specified when starting the server | Always include `--reasoning-budget` in the server startup command. See [LLM Backends](../usage/llm-backends.md#bonsai2-llama-server) |
| Generation fails with `GpuBusy` | The GPU guard couldn't acquire a lease (another process is using the GPU) | Wait a while and retry, or finish the conflicting process. See [GPU Guard](../usage/gpu-guard.md#gpubusy) |
| A generation result comes back "結果不明" (Unknown) | Success/failure couldn't be determined after the LLM call | After checking for possible duplicate generation, explicitly regenerate following the on-screen guidance. See [Generate Text](../usage/generate-text.md#retry) |
