---
sources:
  - "execution/output_settings.py"
  - "execution/evolution_settings.py"
  - "gapengine/gpu_guard.py"
  - "gapengine/ollama.py"
  - "gapengine/llama_server.py"
  - "gapengine/synopsis.py"
  - "viewer/app_desktop.py"
  - "viewer/server.py"
  - ".gitignore"
reviewed: "72aaeae8379271e99357be2f967fa0cff3901a8d"
---
# settings.json

すべての世界に共通の設定ファイルです。⚙ [設定](../usage/settings.md)画面から編集するのが基本ですが、CLI から直接スクリプトを実行するときは`--settings`（省略時は下記の場所）で読み込まれます。

## 置き場所

| 実行方法 | 場所 |
|---|---|
| ソースから動かす場合 | リポジトリ直下の `settings.json` |
| 配布版exe | exeと同じフォルダの `app/settings.json` |

`.gitignore` で追跡対象から外しています（`anthropic`/`openai` の APIキーを含み得るため）。存在しなくても動作し、その場合はすべて既定値が使われます。

## `output`（文章生成）

`output.default_backend` が使用中の接続先（`ollama` / `llama-server` / `claude-cli` / `codex-cli` / `anthropic` / `openai` / `none`。既定 `llama-server`）。各接続先の設定は `output.<backend>` に置きます。

### 接続先共通のキー

| キー | 型 | 既定値 | 意味 |
|---|---|---|---|
| `model` | 文字列 | 接続先ごとに異なる（下表） | 使用するモデル名 |
| `limits.max_calls` | 整数 | `none` は0、他は1 | 1回の生成操作で扱う候補数の上限 |
| `limits.call_timeout_seconds` | 整数 | ollama/llama-server は900、他は180 | 候補1件あたりのタイムアウト（秒） |
| `limits.wall_seconds` | 整数 | ollama/llama-server は3600、他は240 | 生成ジョブ全体のタイムアウト（秒） |
| `limits.max_saved_response_bytes` | 整数 | 128000 | LLM の応答を保存する上限バイト数 |
| `verified_models` | 文字列の配列 | `[]` | ⚙設定の「接続を確認」が成功したモデル名の履歴（最新が先頭、最大20件）。手で書く項目ではない |

`model` 未設定時の既定値（`gapengine/synopsis.py`）:

| 接続先 | 既定モデル |
|---|---|
| `ollama` | `qwen3.6:35b` |
| `llama-server` | `bonsai2-27b` |
| `anthropic` | `claude-opus-5` |
| `openai` | `gpt-5.6` |
| `claude-cli` | `claude-sonnet-5` |
| `codex-cli` | 指定なし（`-m` を付けずに codex 側の既定に委ねる） |

### 接続先ごとの追加キー

| 接続先 | キー | 型 | 既定値 | 意味 |
|---|---|---|---|---|
| `anthropic`, `openai` | `api_key` | 文字列 | なし | APIキー。⚙設定から保存すると画面には値そのものが表示されない（読み取り専用APIは `has_api_key` の真偽値だけを返す） |
| `ollama` | `base_url` | 文字列 | `http://localhost:11434` | Ollama サーバーのURL |
| `ollama` | `think` | 真偽値 | `false` | 思考モードを有効にするか |
| `ollama` | `options` | オブジェクト | `{"num_ctx": 16384, "num_predict": 4096}` | `/api/chat` の `options` にマージする追加パラメータ（temperature等）。指定したキーだけがこの既定値を上書きし、他は既定値のまま残る |
| `ollama` | `seed` | 整数 | なし | 指定すると `options.seed` に反映 |
| `llama-server` | `base_url` | 文字列 | `http://127.0.0.1:8089` | llama-server（OpenAI互換API）のURL |
| `llama-server` | `think` | 真偽値 | `true` | `chat_template_kwargs.enable_thinking` に反映 |
| `llama-server` | `options` | オブジェクト | `{"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0, "presence_penalty": 1.0, "max_tokens": 8192}` | `/v1/chat/completions` のペイロードにマージする追加パラメータ。指定したキーだけがこの既定値を上書きする（`model`・`messages`・`stream`・`chat_template_kwargs` は上書きされない） |
| `llama-server` | `seed` | 整数 | なし | 指定すると `payload.seed` に反映 |
| `llama-server` | `launch` | 文字列の配列 | なし | `base_url` に応答が無いとき自動起動するコマンド（例は[GPU ガード](../usage/gpu-guard.md)の設定例を参照） |
| `llama-server` | `startup_seconds` | 数値 | 180 | 自動起動後、`/health` が応答するまで待つ秒数 |
| `claude-cli` | `command` | 文字列 | `claude` | 実行する実行ファイル名 |
| `codex-cli` | `command` | 文字列 | `codex` | 実行する実行ファイル名 |
| `anthropic` | `max_tokens` | 整数 | 4096 | 応答の最大トークン数 |

### `output.gpu_guard`（任意。設定すると有効化）

ローカルLLM（Ollama・llama-server）のGPU使用を調停する仕組みです。詳しい挙動は[GPU ガード](../usage/gpu-guard.md)を参照してください。

| キー | 型 | 既定値 | 意味 |
|---|---|---|---|
| `thermal.pause_at` | 数値（℃） | 78 | この温度以上で生成前に一時待機する |
| `thermal.resume_at` | 数値（℃） | 70 | この温度以下まで下がったら再開する |
| `thermal.poll_seconds` | 数値（秒） | 15 | 熱ガード待機中の確認間隔 |
| `thermal.max_wait_seconds` | 数値（秒） | 600 | それでも下がらない場合に待つのを諦めるまでの秒数（諦めても生成自体は失敗にしない） |
| `ollama_base_url` | 文字列 | `http://localhost:11434`（`gapengine/ollama.py` の既定と同じ） | Ollama の使用中判定に使うURL |
| `observe_seconds` | 数値（秒） | 15 | Ollama が実際に使用中かを判定するための観測時間 |

## `evolution`（GA の計算資源）

| キー | 型 | 既定値 | 意味 |
|---|---|---|---|
| `evolution.processes` | 整数（1〜CPUコア数） | `min(8, os.cpu_count())` | GA の個体評価を同時に何本走らせるか。結果（`archive.json` 等）は変わらず、速さだけが変わる |

## 環境変数

`settings.json` ではなく環境変数で切り替える項目（`os.environ` を検索して実在を確認したもののみ）。

| 環境変数 | 既定 | 意味 |
|---|---|---|
| `WORLDBLOOM_PYTHON` | 未設定時は `sys.executable` | GA実験のサブプロセス（`execution/jobs.py`・`worker.py`・`generation.py`・`output_requests.py`・`configs.py`）が使う実Pythonの実行ファイル。凍結exe（Studio版）では起動時に `shutil.which("python"/"python3"/"py")` で見つけた実行ファイルをここに設定する |
| `WORLDBLOOM_GPU_LEASE_DIR` | 未設定時は `%LOCALAPPDATA%\WorldBloom` | GPUリース（OSファイルロック）の置き場所 |
| `WORLDBLOOM_STUDIO` | 未設定 | `"1"` にすると開発時に配布版Studioモード（フル機能）を強制する |
| `WORLDBLOOM_APP_ROOT` | 未設定時はexeと同じフォルダの `app/` | 配布版exeが `viewer/execution/gapengine/engine/scripts/templates/projects` を探すルート |
| `PORT` | 5401 | `viewer/server.py` の待ち受けポート（`--port` 未指定時） |

## 完全な例

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

APIキーは⚙設定の画面から保存すると、保存後は画面に値そのものが再表示されません（`has_api_key` の真偽値だけが返ります）。
