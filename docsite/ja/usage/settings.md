---
sources:
- viewer/global_settings.py
- execution/output_settings.py
- execution/evolution_settings.py
- viewer/app_desktop.py
- execution/rationality_settings.py
- viewer/static/global-settings.js
- viewer/static/gemma-setup.js
reviewed: "2e70a9b216b7b2a80a4228a10a928148060e8fc8"
---
# 設定

画面右上（または `/configs`）の **⚙ 全体設定** から開きます。ここでの変更は特定の世界に紐づかず、**すべての世界に共通**です。実体はソースから動かす場合はリポジトリ直下、配布版exeでは `app/settings.json`（exeと同じフォルダの `app` フォルダの中）です。詳しいファイル仕様は[settings.json](../reference/settings-json.md)を参照してください。左メニューは「AIの設定」「計算」「実行設定の保存版」です。

## AIの設定 { #output-settings }

[世界の下書き作成](create-world.md)と[あらすじ・本文の生成](generate-text.md)で使用する接続先を確認します。現在の画面には「使用するAI」「生成の上限」「合理性の判定器（Jev）」があります。

「使用するAI」には保存済みの生成先とGemma 4の導入状況が表示されます。「Gemma 4を選ぶ」は編集内容を選択する操作で、**「設定を保存」を押すまで生成先は切り替わりません**。Ollamaを動かすPCで `ollama pull gemma4:e4b-it-qat` を実行し、「導入状況を再確認」で確認します。この確認は文章生成もモデルのダウンロードも行いません。

他の対応バックエンドやモデルは[LLM バックエンド](llm-backends.md)と[settings.json](../reference/settings-json.md)を参照してください。保存済みの別モデルは勝手にGemmaへ切り替わりません。モデル別のプロファイルでGemma用オプションを分け、他モデルの基本設定は保持します。

「生成の上限」で、選択中の方式のAI呼出し上限・1件の待ち時間・全体の待ち時間・応答の保存上限を変更できます。保存後に開始する生成へ適用され、実行中のジョブは変更しません。現在の保存値を確認し、世界のAI作成では複数回呼び出す方式がある点にも注意してください。

「合理性の判定器（Jev）」でTypeSafeのAPIキーを保存できます。保存前に1問の実判定で有効性を確認します。κ>0でJevを選んだ実験では、主人公の状況テキストが `api.typesafe.ai` へ送られます。キーは画面や実験記録に表示されません。利用する実験とκは[実行設定](run-settings.md#04)で選びます。

## 計算

GA 実験をこの PC でどれだけ並列に計算するかの設定です。

| 項目 | 内容 | 既定値 |
|---|---|---|
| GA の並列数（processes） | GA の個体評価を同時に何本走らせるか。**結果は変わらず、速さだけが変わります** | `min(8, このPCのCPUコア数)` |

開発環境（20コア）の実測では、並列数8前後で速度が頭打ちになりました。保存後に開始するGA実験から適用されます。

「GPU ガード」ではローカル文章生成前の温度確認を設定できます。上限に達すると8℃下がるまで最長10分待ちます。[GPU ガード](gpu-guard.md)も参照してください。


## 実行設定の保存版

これまでに保存した[実行設定](run-settings.md)の一覧です。保存版は編集できません（内容を変えたい場合は「複製して調整」から複製します）。各項目から、対応する世界・ジャンル・実行規模（世代数×個体数×seed数）・作成日時・複製元を確認できます。
