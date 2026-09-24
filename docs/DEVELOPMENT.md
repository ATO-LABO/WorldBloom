# 開発者向けガイド

ソースから GA 実験を回す・独自の LLM バックエンドであらすじ/本文を生成し直す場合の手順です。同梱サンプルをビューアで眺めるだけなら [README](../README.md) の「試し方」で足ります。

運用まわりで次の機能も備えています。

- **ローカル GPU の自動調停**: Ollama と llama-server の同居調停・熱ガード・自動起動停止・プリロード/アンロード（→ [GPU ガード](#gpu-ガード自動起動調停熱ガード)）
- **実行進捗の可視化**: 生成中の進捗スピナー・パーセント・残り時間（ETA）表示

## LLM バックエンドの設定

あらすじ・本文の生成に LLM を使う場合（任意）。既定はローカルの Ollama＋Qwen3.5（本応募の提出物はこのモデルで生成。詳細は開示文書を参照）:

1. [Ollama](https://ollama.com) をインストール
2. `ollama pull qwen3.5:9b-q4_K_M`

より大きなモデル（`qwen3.6:35b` など）への切り替えも `--backend`/`settings.json` の `model` で可能ですが、提出済みの本文・あらすじの生成条件とは異なります。

代替として `claude-cli` / `codex-cli` / Anthropic API / OpenAI API のいずれかも選べます（`--backend` で切り替え）。

### settings.json を作る（Ollama を使う場合）

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

`options` は既定（`num_ctx` 16384・`num_predict` 4096）に上書きマージされるので、変えたいキーだけ書けばよい。`think` は思考トークンを抑えるため既定で `false`。`settings.json` は `.gitignore` 対象（APIキーを含み得るため）。

### Bonsai 2 27B（llama-server）を使う場合

PrismML の Ternary Bonsai 2 27B は独自量子化形式のため Ollama では動かず、PrismML フォーク版 llama.cpp の `llama-server`（OpenAI 互換 API）でのみ動く。

1. フォーク版バイナリを入手する: [PrismML-Eng/llama.cpp の releases](https://github.com/PrismML-Eng/llama.cpp/releases) から Windows なら `win-cuda-12.4` の `llama-...-bin-...zip` と `cudart-...zip` の両方（CUDA ランタイムが別 zip なので片方だけでは動かない）
2. GGUF を入手する: Hugging Face `prism-ml/Ternary-Bonsai-2-27B-gguf` の `PTQ1_0`
3. サーバーを起動する:

```
llama-server.exe -m Ternary-Bonsai-2-27B-PTQ1_0.gguf --alias bonsai2-27b -ngl 99 -np 1 -c 10240 -fa on --port 8089 --host 127.0.0.1 --reasoning-budget 2048 --reasoning-budget-message "思考の上限に達した。ここで思考を終え、直ちに最終回答の本文だけを書く。"
```

`--alias` は必須（省略するとモデル名がファイルパスになり、`settings.json` の `model` と一致しなくなる）。`--reasoning-budget` も必須（思考トークンの上限はサーバー起動フラグでしか効かず、リクエスト側で送っても無視されるため、これが無いと `finish_reason: "length"` で応答が打ち切られ、WorldBloom 側は失敗として扱う）。

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

注意:
- VRAM 8GB では Ollama のモデルと同居できない。生成前に `ollama ps` で Ollama 側のモデルが退避済み（unloaded）か確認する
- ノート GPU は連続生成で 86℃ に達することがある。長時間連続実行する場合は休止を挟む

#### GPU ガード（自動起動・調停・熱ガード）

上の2つの注意点と「生成前にサーバーを手で起動しておく」手間は、`settings.json` に `launch`（サーバー起動コマンド）と `gpu_guard` を書けば WorldBloom 側の仕組みで解決できる:

```json
{
  "output": {
    "default_backend": "llama-server",
    "gpu_guard": {
      "thermal": {"pause_at": 78, "resume_at": 70, "poll_seconds": 15, "max_wait_seconds": 600},
      "ollama_base_url": "http://localhost:11434",
      "observe_seconds": 15
    },
    "llama-server": {
      "base_url": "http://127.0.0.1:8089",
      "model": "bonsai2-27b",
      "think": true,
      "options": {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0, "presence_penalty": 1.0, "max_tokens": 8192},
      "launch": ["C:/path/to/llama-server.exe", "-m", "C:/path/to/Ternary-Bonsai-2-27B-PTQ1_0.gguf",
        "--alias", "bonsai2-27b", "-ngl", "99", "-np", "1", "-c", "10240", "-fa", "on",
        "--host", "127.0.0.1", "--port", "8089", "--reasoning-budget", "2048",
        "--reasoning-budget-message", "思考の上限に達した。ここで思考を終え、直ちに最終回答の本文だけを書く。"],
      "startup_seconds": 180
    }
  }
}
```

`gpu_guard` キーが無ければ今まで通り何も起きない（既存の挙動を変えない完全な no-op）。あれば生成の前に以下を自動で行う:

- **リース**: マシン全体で1つの OS ファイルロック（既定 `%LOCALAPPDATA%\WorldBloom`、`WORLDBLOOM_GPU_LEASE_DIR` で変更可）を取ってから生成する。他プロセスが握っていれば空くまで待ち、待っても空かなければ `GpuBusy` で失敗する（無期限には待たない）。ロックを持つプロセスが異常終了すれば OS が自動的に解放する
- **Ollama との調停**: `llama-server` を使う前に Ollama の `/api/ps` を2回観測し、遊休（応答の `expires_at` が進んでいない）なら `keep_alive: 0` で自動退避する。使用中（誰かが呼んでいる）と判定した間は待ち、リースの締め切りを過ぎたら `GpuBusy` で失敗する。Ollama 側から見ても、到達可能な llama-server が動いていれば `GpuBusy` で待たせるので、片方が VRAM を使い切ったまま両方が動くことはない
- **自動起動・自動停止**: `llama-server` の `base_url` に既に応答があればそれ（人が起動したもの）をそのまま使い、止めない。応答が無く `launch` があれば自動で起動し（`launch` の先頭要素は `.cmd` などのラッパーではなく exe を直接指すこと。残ったサーバーの後始末は実行ファイル名の一致で本人確認するため）、`startup_seconds`（既定180秒）まで起動を待つ。生成が終われば自分が起動したサーバーだけを自動で停止する。前回異常終了して残ったサーバーがあれば、次にリースを取った側が自動で終了させる
- **熱ガード**: 各生成の前に GPU 温度（`nvidia-smi`）を確認し、`pause_at`（既定78℃）以上なら `resume_at`（既定70℃）以下に下がるまで `poll_seconds` 間隔で待つ（`max_wait_seconds` で打ち切る）。温度が読めない環境では止めない
- `gpu_guard` は空オブジェクト `{}` でも有効（全項目に既定値が入る）

Jev などこの仕組みの外で GPU を使うコードも、`gapengine.gpu_guard.gpu_lease(owner, wait_seconds=...)` を取ってから GPU を使えば同じ調停に参加できる（同一プロセス内での再入は待たずに通る）。

## 自分で進化を回す

```
python scripts/evolve.py --project projects/momotaro --template templates/momotaro \
  --out <出力先>/exp1 --generations 20 --population 100 --seeds 3 --keep reached --processes 4
```

所要時間の目安: 20コアで約1時間（実測: exp12 が6プロセスで約70分。計測は2026-09-14時点のコードに基づくため、その後のGA/ビューア側の性能改善（WB-OPT-001〜003）で実際はこれより速くなっている可能性がある）。出力先はリポジトリ外を推奨（`.gitignore` は `runs/` を無視するが、リポジトリ内に大量の実験出力を置くべきではない）。`--project` / `--template` は `momotaro` / `detective` / `romance` の3ジャンルを同梱。

## あらすじ化・本文化

```
python scripts/synopsize.py --archive <出力先>/exp1/archive.json --runs <出力先>/exp1 \
  --out <出力先>/exp1/synopses.json --backend ollama --project projects/momotaro --template templates/momotaro

python scripts/narrate.py --archive <出力先>/exp1/archive.json --selection <出力先>/exp1/selection.json \
  --out <出力先>/exp1/stories --backend ollama --project projects/momotaro --template templates/momotaro
```

`selection.json` は `{"selected": ["III|high"]}` の形（格子のセルキー = カテゴリ|volatility_bin）。あらすじの確認・格子セルの選定はビューア（`--control` 付きで起動時）からも行える。

## 無作為基準との比較

```
python scripts/random_baseline.py --project projects/momotaro --template templates/momotaro --out <出力先>/baseline
```

方針を持たない完全無作為なシードだけで結末に届く経路の多様性を測る、GAの効果を測るための対照実験。

## 決定論・回帰テスト

同じ `(world, genome, seed, precedent)` の組で `layers.jsonl` はバイト一致します。回帰テスト:

```
python -m unittest discover -s tests
```
