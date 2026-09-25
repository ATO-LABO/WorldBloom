---
sources:
  - "viewer/app_desktop.py"
  - "viewer/workbench_pages.py"
  - "gapengine/gpu_guard.py"
  - "viewer/output_pages.py"
reviewed: 72aaeae8379271e99357be2f967fa0cff3901a8d
---
# トラブルシューティング

## 起動できない

| 症状 | 原因 | 対処 |
|---|---|---|
| Windows SmartScreen の警告が出る | 配布版 exe は署名されていない | 「詳細情報」→「実行」の順にクリックしてください。詳しくは[インストール](../getting-started/install.md)を参照 |
| `pywebview の読み込みに失敗しました。配布フォルダが壊れている可能性があります。展開し直してください。` | 配布 zip の展開が不完全 | zip を展開し直してください |
| `WorldBloom.exe と同じフォルダに app フォルダが見つかりません。`（Studio では `WorldBloom Studio.exe と同じフォルダに...`） | exe だけを移動・単体配布した | 配布フォルダの構成（`WorldBloom.exe`／`WorldBloom-Studio.exe` と `app/` フォルダ）を崩さず、同じ場所に置いてください |
| Studio が「GA実験の実行にはPython 3.11以上（PyYAML導入済み）が別途必要です」と表示する | Studio 起動時に PATH 上の `python`/`python3`/`py` が見つからない、または見つかったものが 3.11 未満か PyYAML 未導入 | [python.org](https://www.python.org/downloads/) からインストールし、インストーラーで「Add python.exe to PATH」にチェック。インストール後 `pip install pyyaml` を実行し、Studio を再起動してください |

## 実行中に起きること

| 症状 | 原因 | 対処 |
|---|---|---|
| GA 実験が `preparation_failed`（入力とコードの固定に失敗しました）で即座に止まる | 実行設定の入力に問題があるか、ソースから動かしている場合は `requirements.txt` など必要なファイルが揃っていない | 設定の入力を確認してください。ソース版では `requirements.txt` が読み込み対象のフォルダに存在するか確認してください |
| 実行中に停止しても結果の一部が欲しい | 停止は仕様どおりの動作 | 「停止すると、閉じた世代までの結果は残ります」。[実行設定](../usage/run-settings.md)を参照 |
| ポートが使用中で起動しない（ソースから `viewer/server.py` を起動する場合） | 指定したポートを他のプロセスが使っている | `--port` に別の番号を指定して起動し直してください |

## Viewer（閲覧専用ビルド）で操作すると 403 になる

WorldBloom.exe（閲覧専用ビルド）は、採用・選定チェックボックスなど画面上の操作要素は表示されますが、書き込み系の操作（POST）はすべてサーバー側で `403 read-only build` として拒否します。**これは仕様どおりの挙動です。**編集・実行するには WorldBloom-Studio.exe を使ってください。詳しくは[インストール](../getting-started/install.md)を参照してください。

## LLM 生成が失敗する

| 症状 | 原因 | 対処 |
|---|---|---|
| モデルを指定してください | ⚙ 設定でモデル名が未入力（`claude-cli` 以外はどの接続先でも自動では決まらない） | ⚙ 設定の「文章生成」タブでモデル名を入力してください。[LLM バックエンド](../usage/llm-backends.md#model-defaults)を参照 |
| 実行ファイルが見つかりません | `claude-cli` / `codex-cli` を選んでいるが `claude`/`codex` コマンドが PATH にない | 該当する CLI をインストールし、PATH を通してください |
| 資格情報がありません | `anthropic` / `openai` を選んでいるが APIキーが未保存 | ⚙ 設定の「APIキー」欄からキーを保存してください |
| そのモデルは見つかりません | 指定したモデル名が接続先に存在しない（Ollama で pull していない、API側にそのモデル名が無い等） | モデル名を確認するか、[LLM バックエンド](../usage/llm-backends.md)の手順でモデルを取得してください |
| サーバーに接続できません | Ollama / llama-server 本体が起動していない、または `base_url` が誤っている | サーバーを起動する（または[GPU ガード](../usage/gpu-guard.md)で自動起動を設定する）か、`base_url` を確認してください |
| settings.json を読めません | `settings.json` が壊れている、または読み取れない | ファイルの内容（JSON 構文）を確認してください |
| モデル名が不正です | モデル名に使えない文字が含まれている | 半角英数字・`.`・`_`・`:`・`/`・`-` のみで指定してください |
| 応答が途中で切れる（`finish_reason: "length"`） | 特に llama-server / Bonsai 2 で、サーバー起動時に `--reasoning-budget` を指定していない | サーバー起動コマンドに `--reasoning-budget` を必ず含めてください。詳しくは[LLM バックエンド](../usage/llm-backends.md#bonsai2-llama-server)を参照 |
| `GpuBusy` で生成が失敗する | GPU ガードのリースが取れない（他プロセスが GPU を使用中） | しばらく待ってから再試行するか、競合している処理を終えてください。詳しくは[GPU ガード](../usage/gpu-guard.md#gpubusy)を参照 |
| 生成結果が「結果不明」になる | LLM 呼び出し後に成否が確定できなかった | 二重生成の可能性を確認したうえで、案内される手順で明示的に再生成してください。詳しくは[文章を生成する](../usage/generate-text.md#retry)を参照 |
