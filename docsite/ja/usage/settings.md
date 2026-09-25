---
sources:
  - "viewer/global_settings.py"
  - "execution/output_settings.py"
  - "execution/evolution_settings.py"
  - "viewer/app_desktop.py"
reviewed: 72aaeae8379271e99357be2f967fa0cff3901a8d
---
# 設定

画面右上（または `/configs`）の **⚙ 全体設定** から開きます。ここでの変更は特定の世界に紐づかず、**すべての世界に共通**です。実体はソースから動かす場合はリポジトリ直下、配布版exeでは `app/settings.json`（exeと同じフォルダの `app` フォルダの中）です。詳しいファイル仕様は[settings.json](../reference/settings-json.md)を参照してください。4つのタブがあります。

## 文章生成 { #output-settings }

[あらすじ・本文の生成](generate-text.md)に使う設定です。

| 項目 | 内容 | 既定値 |
|---|---|---|
| 生成方式（backend） | Claude Code CLI / Codex CLI / Anthropic API / OpenAI API / ローカル Ollama / ローカルLLM（llama-server）/ 生成しない（プロンプト保存のみ） | `llama-server`（配布版 v1.0.0-viewer では `codex-cli`） |
| モデル | 使用するモデル名 | `claude-cli` は未入力でも `claude-sonnet-5` が使われます。それ以外は入力が必須です（[LLM バックエンド](llm-backends.md#model-defaults)を参照） |
| APIキー | Anthropic API / OpenAI API を選んだときのみ表示。保存済みの値は画面に表示されません | — |
| 候補数の上限 | 1回の生成操作で扱う候補数の上限 | `none` は0件、それ以外は1件 |
| 1件の待ち時間 | 候補1件あたりのタイムアウト（秒） | Ollama/llama-server は900秒、それ以外は180秒 |
| 全体の待ち時間 | 生成ジョブ全体のタイムアウト（秒） | Ollama/llama-server は3600秒、それ以外は240秒 |
| 応答の保存上限（詳細設定） | LLM の応答を保存する上限バイト数 | 128000 bytes |

「接続を確認」ボタンは接続先・実行環境を確認するだけで、文章生成そのものは行いません。保存した設定は、保存後に開始する生成から適用されます（生成中のジョブには影響しません）。

## 計算

GA 実験をこの PC でどれだけ並列に計算するかの設定です。

| 項目 | 内容 | 既定値 |
|---|---|---|
| GA の並列数（processes） | GA の個体評価を同時に何本走らせるか。**結果は変わらず、速さだけが変わります** | `min(8, このPCのCPUコア数)` |

開発環境（20コア）の実測では、並列数8前後で速度が頭打ちになりました。保存後に開始するGA実験から適用されます。

## 実行設定の保存版

これまでに保存した[実行設定](run-settings.md)の一覧です。保存版は編集できません（内容を変えたい場合は「複製して調整」から複製します）。各項目から、対応する世界・ジャンル・実行規模（世代数×個体数×seed数）・作成日時・複製元を確認できます。

## ジャンル

登録済みのジャンル（テンプレート）の一覧です。各ジャンルについて、利用中の世界と、含まれる設定ファイル（行動のつながり・状況ごとの定石・行動の効果・展開のルール・候補の分類軸）を確認できます。編集は「ジャンルを編集」から行います（[世界を作る](create-world.md#advanced-settings)を参照）。
