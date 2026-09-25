---
sources:
  - "execution/output_settings.py"
  - "gapengine/ollama.py"
  - "gapengine/llama_server.py"
  - "gapengine/synopsis.py"
  - "viewer/global_settings.py"
  - "viewer/workbench_pages.py"
---
# LLM バックエンド

あらすじ・本文の[文章を生成する](generate-text.md)ときにだけ LLM を使います。GA 実験そのものは LLM を必要としません。接続先は ⚙ [設定](settings.md)の「文章生成」タブで選びます。

## 選べる接続先

| 接続先（backend） | 画面での表示 | 必要なもの |
|---|---|---|
| `ollama` | ローカル Ollama | Ollama 本体のインストール、モデルの pull |
| `llama-server` | ローカルLLM（llama-server） | llama-server 本体（OpenAI 互換 API サーバー） |
| `claude-cli` | Claude Code CLI | `claude` コマンドが PATH にあること |
| `codex-cli` | Codex CLI | `codex` コマンドが PATH にあること |
| `anthropic` | Anthropic API | APIキー |
| `openai` | OpenAI API | APIキー |
| `none` | 生成しない（プロンプト保存のみ） | 不要（本文を生成せず、プロンプトだけ保存します） |

既定の接続先は `llama-server` です（配布版 v1.0.0-viewer では `codex-cli`）。Ollama・llama-server 本体は WorldBloom に同梱されていないため、別途インストールが必要です。

### モデル名は誰が決めるか { #model-defaults }

**⚙ 設定の画面から使う場合**は、`claude-cli` だけモデル未入力でも `claude-sonnet-5` が使われます。それ以外の接続先はモデル名の入力が必須です（未入力のまま接続を確認しようとすると「モデルを指定してください」と表示されます）。

**CLI（`scripts/synopsize.py` / `scripts/narrate.py` を直接実行する場合）**は、`settings.json` に `model` を書かなければ、接続先ごとに次の既定値が使われます。

| 接続先 | settings.json 未設定時の既定モデル |
|---|---|
| `ollama` | `qwen3.6:35b` |
| `llama-server` | `bonsai2-27b` |
| `anthropic` | `claude-opus-5` |
| `openai` | `gpt-5.6` |
| `claude-cli` | `claude-sonnet-5` |
| `codex-cli` | 指定なし（`-m` を付けずに呼び出し、codex 側の既定モデルに委ねます） |

同梱サンプル（`samples/`）・提出物の生成条件は `qwen3.5:9b-q4_K_M`（Ollama）です。既定値の `qwen3.6:35b` など、より大きなモデルに切り替えると生成条件が変わります。

## Ollama を使う場合

1. [Ollama](https://ollama.com) をインストールします
2. モデルを取得します（例: `ollama pull qwen3.5:9b-q4_K_M`）

ソースから動かす場合、`settings.json` に直接書くこともできます。

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

`options` は既定値（`num_ctx` 16384・`num_predict` 4096）に上書きマージされるので、変えたいキーだけ書けば十分です。`think` は思考トークンを抑えるため既定で `false` です。`settings.json` は APIキーを含み得るため `.gitignore` の対象です。

## Bonsai 2 27B（llama-server）を使う場合 { #bonsai2-llama-server }

PrismML の Ternary Bonsai 2 27B は独自量子化形式のため Ollama では動かず、PrismML フォーク版 llama.cpp の `llama-server`（OpenAI 互換 API）でのみ動きます。

1. フォーク版バイナリを入手します: [PrismML-Eng/llama.cpp の releases](https://github.com/PrismML-Eng/llama.cpp/releases) から、Windows なら `win-cuda-12.4` の `llama-...-bin-...zip` と `cudart-...zip` の両方（CUDA ランタイムが別 zip なので片方だけでは動きません）
2. GGUF を入手します: Hugging Face `prism-ml/Ternary-Bonsai-2-27B-gguf` の `PTQ1_0`
3. サーバーを起動します。

```
llama-server.exe -m Ternary-Bonsai-2-27B-PTQ1_0.gguf --alias bonsai2-27b -ngl 99 -np 1 -c 10240 -fa on --port 8089 --host 127.0.0.1 --reasoning-budget 2048 --reasoning-budget-message "思考の上限に達した。ここで思考を終え、直ちに最終回答の本文だけを書く。"
```

`--alias` は必須です（省略するとモデル名がファイルパスになり、`settings.json` の `model` と一致しなくなります）。`--reasoning-budget` も必須です。思考トークンの上限はサーバー起動フラグでしか効かず、リクエスト側で送っても無視されるため、これが無いと `finish_reason: "length"` で応答が打ち切られ、WorldBloom 側は失敗として扱います。

`settings.json`（手動起動のまま使う場合）:

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

注意点:

- VRAM 8GB では Ollama のモデルと同居できません。生成前に `ollama ps` で Ollama 側のモデルが退避済み（unloaded）か確認してください（自動化する方法は[GPU ガード](gpu-guard.md)を参照）
- ノート PC の GPU は連続生成で86℃に達することがあります。長時間連続実行する場合は休止を挟んでください

## CLI 接続先（claude-cli / codex-cli）

`claude` または `codex` コマンドが PATH にあれば使えます。実行ファイルが見つからない場合、⚙ 設定の「接続を確認」は失敗と表示されます。

## API 接続先（anthropic / openai）

APIキーが必要です。⚙ 設定の「APIキー」欄から個別に保存します（保存済みの値は画面に表示されません）。

## 生成しない（none）

LLM を呼び出さず、生成に使うプロンプトだけを保存します。接続先を用意する前に、あらすじ・本文生成の対象や候補数を確認したいときに使えます。
