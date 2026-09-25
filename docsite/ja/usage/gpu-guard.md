---
sources:
  - "gapengine/gpu_guard.py"
  - "viewer/pages.py"
reviewed: 72aaeae8379271e99357be2f967fa0cff3901a8d
---
# GPU ガード

1枚の GPU を Ollama と llama-server の両方で使い回すとき（[Bonsai 2 27B のセットアップ](llm-backends.md#bonsai2-llama-server)を参照）、手動での「生成前にサーバーを起動しておく」「Ollama 側のモデルを退避しておく」といった作業を、`settings.json` の `output.gpu_guard` を書くことで WorldBloom 側の仕組みに任せられます。

## 何をしてくれるか

`gpu_guard` を設定すると、生成の前に自動で次を行います。

- **リース**: マシン全体で1つの OS ファイルロック（既定 `%LOCALAPPDATA%\WorldBloom`、環境変数 `WORLDBLOOM_GPU_LEASE_DIR` で変更可）を取ってから生成します。他プロセスが握っていれば空くまで待ち、待っても空かなければ `GpuBusy` で失敗します（無期限には待ちません）。ロックを持つプロセスが異常終了すれば OS が自動的に解放します
- **Ollama との調停**: `llama-server` を使う前に Ollama の `/api/ps` を2回観測し、遊休（応答が進んでいない）なら `keep_alive: 0` で自動的に退避させます。使用中と判定した間は待ち、リースの締め切りを過ぎたら `GpuBusy` で失敗します。逆方向（Ollama を使う前に llama-server が動いているか）は待たず、動作中ならその場で即座に `GpuBusy` になります（先に起動している側を優先し、両方が同時に VRAM を使い切ることを防ぎます）
- **自動起動・自動停止**: `llama-server` の `base_url` に既に応答があれば、それ（人が起動したもの）をそのまま使い、止めません。応答が無く `launch`（起動コマンド）が設定されていれば自動で起動し、`startup_seconds`（既定180秒）まで起動を待ちます。生成が終われば、自分が起動したサーバーだけを自動で停止します。前回異常終了して残ったサーバーがあれば、次にリースを取った側が自動で終了させます
- **熱ガード**: 各生成の前に GPU 温度（`nvidia-smi`）を確認し、`pause_at`（既定78℃）以上なら `resume_at`（既定70℃）以下に下がるまで `poll_seconds`（既定15秒）間隔で一時的に待ちます。`max_wait_seconds`（既定600秒）を過ぎても下がらない場合は、それ以上は待たずに生成を続行します（失敗にはしません）。温度が読めない環境ではそもそも待ちません

## settings.json の書き方

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

`gpu_guard` キーが無ければ、これまでどおり何も起きません（既存の挙動を変えない no-op です）。キーがあれば、空オブジェクト `{}` でも有効になり、すべての項目に既定値が入ります。

`launch` の先頭要素は `.cmd` などのラッパーではなく、実行ファイル（exe）を直接指定してください。残ったサーバーの後始末は実行ファイル名の一致で本人確認しているため、ラッパー経由だと正しく後始末できません。

## 画面での確認

画面ヘッダーの「動作状況」アイコンから、GPU・ローカルAIの動作状況（GPU温度・Ollama/llama-serverの稼働状態など）を確認できます。

## GpuBusy になったら { #gpubusy }

「他プロセスが GPU を握っている」「Ollama が使用中」「別の llama-server が動いている」のいずれかで、リースの締め切りまでに解消しなかった場合に発生します。しばらく待ってから再試行するか、競合している側の処理（別の生成ジョブ、Ollama での対話など）を終えてから再試行してください。
