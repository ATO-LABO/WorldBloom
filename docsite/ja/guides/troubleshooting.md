---
sources:
  - "viewer/global_settings.py"
  - "execution/output_settings.py"
  - "viewer/app_desktop.py"
  - "viewer/workbench_pages.py"
  - "gapengine/gpu_guard.py"
  - "viewer/output_pages.py"
reviewed: "2e70a9b216b7b2a80a4228a10a928148060e8fc8"
---
# トラブルシューティング

## 起動できない

| 症状 | 原因 | 対処 |
|---|---|---|
| Windows SmartScreen の警告が出る | 配布版 exe は署名されていない | 公式Releaseの入手元と公開チェックサムを確認し、不明なファイルは実行しないでください。詳しくは[インストール](../getting-started/install.md)を参照 |
| `pywebview の読み込みに失敗しました。配布フォルダが壊れている可能性があります。展開し直してください。` | 配布 zip の展開が不完全 | zip を展開し直してください |
| `WorldBloom.exe と同じフォルダに app フォルダが見つかりません。`（Studio では `WorldBloom Studio.exe と同じフォルダに...`） | exe だけを移動・単体配布した | 配布フォルダの構成（`WorldBloom.exe`／`WorldBloom-Studio.exe` と `app/` フォルダ）を崩さず、同じ場所に置いてください |
| Studio が「GA実験の実行にはPython 3.12以上（PyYAML導入済み）が別途必要です」と表示する | Studio 起動時に PATH 上の `python`/`python3`/`py` が見つからない、または見つかったものが 3.12 未満か PyYAML 未導入 | [python.org](https://www.python.org/downloads/) からインストールし、インストーラーで「Add python.exe to PATH」にチェック。インストール後 `pip install pyyaml` を実行し、Studio を再起動してください |

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
| モデルを指定してください | 保存済みの生成設定にモデル名がない（`claude-cli` 以外は自動では決まらない） | ⚙ 全体設定 → AIの設定 → 使用するAIで保存済みの生成先を確認してください。Gemma 4は画面で選択・保存し、他モデルは [settings.json](../reference/settings-json.md) の `output` を設定します。モデルの導入・接続も確認してください。[LLM バックエンド](../usage/llm-backends.md#model-defaults)を参照 |
| 実行ファイルが見つかりません | `claude-cli` / `codex-cli` を選んでいるが `claude`/`codex` コマンドが PATH にない | 該当する CLI をインストールし、PATH を通してください |
| 資格情報がありません | `anthropic` / `openai` を選んでいるが APIキーが未保存 | 利用する接続先の資格情報を対応する設定に保存してください。現行画面とCLIの違いは[LLM バックエンド](../usage/llm-backends.md)を参照 |
| そのモデルは見つかりません | 指定したモデル名が接続先に存在しない（Ollama で pull していない、API側にそのモデル名が無い等） | モデル名を確認するか、[LLM バックエンド](../usage/llm-backends.md)の手順でモデルを取得してください |
| サーバーに接続できません | Ollama / llama-server 本体が起動していない、または `base_url` が誤っている | サーバーを起動する（または[GPU ガード](../usage/gpu-guard.md)で自動起動を設定する）か、`base_url` を確認してください |
| settings.json を読めません | `settings.json` が壊れている、または読み取れない | ファイルの内容（JSON 構文）を確認してください |
| モデル名が不正です | モデル名に使えない文字が含まれている | 半角英数字・`.`・`_`・`:`・`/`・`-` のみで指定してください |
| 応答が途中で切れる（`finish_reason: "length"`） | 特に llama-server / Bonsai 2 で、サーバー起動時に `--reasoning-budget` を指定していない | サーバー起動コマンドに `--reasoning-budget` を必ず含めてください。詳しくは[LLM バックエンド](../usage/llm-backends.md#bonsai2-llama-server)を参照 |
| `GpuBusy` で生成が失敗する | GPU ガードのリースが取れない（他プロセスが GPU を使用中） | しばらく待ってから再試行するか、競合している処理を終えてください。詳しくは[GPU ガード](../usage/gpu-guard.md#gpubusy)を参照 |
| 生成結果が「結果不明」になる | LLM 呼び出し後に成否が確定できなかった | 二重生成の可能性を確認したうえで、案内される手順で明示的に再生成してください。詳しくは[文章を生成する](../usage/generate-text.md#retry)を参照 |

## 旧世界形式と表示されたら

現行ソースは新形式の世界だけを編集・実行します。「桃太郎＋3」（`momotaro_plus3_new`）または新規作成で確定した世界を選んでください。旧世界は自動変換しません。形式番号だけを追加して実行を通そうとせず、原本と過去の実行結果を保持してください。

## 下書きの保存やAI精緻化が止まったら

競合（409）では最新の保存版と手元の入力を比較し、別のコピーとして保存する選択肢を使ってください。保存結果が不明なときは保存済み状態を確認し、無条件で再送しないでください。AI候補が検査で拒否された場合も、要求の一部だけを採用せず理由を確認してください。操作手順は[世界を作る](../usage/create-world.md)を参照してください。
