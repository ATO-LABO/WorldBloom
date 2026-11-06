---
sources:
  - "README.md"
  - "LICENSE"
  - "docsite/ja/usage/llm-backends.md"
  - "docsite/ja/usage/gpu-guard.md"
  - "templates/"
  - "docsite/ja/concepts/personality-growth.md"
reviewed: "2e70a9b216b7b2a80a4228a10a928148060e8fc8"
---

# FAQ

## GA 実験（進化）に LLM は必要ですか？

必須ではありません。基本のGAシミュレーション・進化・QD格子への蓄積はLLMなしで動きます。任意の[合理性層](../concepts/rationality-layer.md)（κ > 0）ではOllamaまたはJevが候補行動を判定し、その重みに反映します。LLMを使うのは、実行前のAIによる[世界作成・精緻化](../usage/create-world.md)と、実行後の[あらすじ・本文生成](../usage/generate-text.md)です。保存済みの結果を見るだけなら生成は不要です。CLIの文章生成では `--backend none` でプロンプト保存だけを行えます。画面の設定と対応する接続先は[LLM バックエンド](../usage/llm-backends.md)を参照してください。

## なぜ出来事の生成に LLM を使わないのですか？

出来事の列（誰が何をしたか）を LLM に直接書かせると、なぜその展開になったのかを機械的に追跡できなくなります。WorldBloom は出来事の生成をシミュレーションと乱数だけに限ることで、[決定論](../concepts/determinism.md)を保証し、ある物語がどの世代のどの親から、どんな分岐で生まれたかを[系譜・転機](../concepts/sifting.md#compare-lineage-turning)として機械的に示せるようにしています。詳しくは[全体の流れ](../concepts/overview.md)を参照してください。

## GA を使う意味は？

方針（遺伝子）を進化させずに、無作為な個体だけで走らせても物語は生成できます（`scripts/random_baseline.py`）。[性格の成長](../concepts/personality-growth.md)とその第3軸を有効にした条件で、評価回数（400回）を揃えて比べた実測（乱数の種5通り）では、GA の方が到達率（平均0.132 対 0.061）・占有マス数（平均17.0 対 13.4）・QD スコア（平均4.80 対 3.47）・最高の質（平均0.429 対 0.382）のいずれも上回りました。ただし性格の成長・第3軸を使わない条件（18マスの格子）では、GA と無作為の差はほとんど出ませんでした（占有マス数7.2対7.2、QDスコア2.12対2.21）。試行はそれぞれ5回のみで検定は行っておらず、性格の成長・第3軸ありの条件はマスの上限自体も18から54に増えているため、GAが上回った理由を探索対象の増加とマス数上限の増加とに切り分けられていません。詳しくは[性格の成長](../concepts/personality-growth.md#ga-vs-random)を参照してください。

## GPU が無い PC でも使えますか？

はい。GA 実験（進化）自体は GPU を必要としません。ローカルLLMの速度や必要メモリはモデルと実行環境に依存します。CPUで動かせるモデルもありますが、大きなモデルの生成にはGPUが実用上必要になる場合があります。GPU が無ければ、外部 API（Anthropic / OpenAI）や CLI 経由（Claude Code / Codex CLI）の接続先を使うか、生成をせずプロンプトの保存だけに留めることもできます。詳しくは[LLM バックエンド](../usage/llm-backends.md)・[GPU ガード](../usage/gpu-guard.md)を参照してください。

## 結果が毎回同じなのはなぜですか？

WorldBloom の個体評価は[決定論](../concepts/determinism.md)を設計の前提にしているためです。同じPythonバージョン・世界・遺伝子・乱数の種・前例表・エンジンのコードなら、出力ログはバイト単位で一致します。逆に、エンジンのコード自体を変更すると、同じ設定でも別の物語になります。

## 自分の物語世界を作れますか？

はい。地名・経路・登場人物の初期状態などの「世界」と、そのジャンルの文法（行動グラフ・正典・伏線ライブラリ・QD の軸）を宣言する「テンプレート」を用意すれば、桃太郎・恋愛・探偵以外の世界も作れます。手順は[世界を作る](../usage/create-world.md)を参照してください。

## StorySim との関係は？

WorldBloom は StorySim（同作者の別プロジェクト）の延長ではなく、別プロジェクトとして再構築したものです。ゾーンと移動、関係行列、決定論の作法など一部の要素は StorySim を参照して設計・実装していますが、StorySim 本体のコードはそのまま流用していません。

## 商用利用・ライセンスは？

リポジトリは Apache License 2.0 で公開されています。ライセンス全文は [LICENSE](https://github.com/ATO-LABO/WorldBloom/blob/main/LICENSE) を確認してください。

## 英語の物語は作れますか？

画面（Viewer / Studio の UI）は日本語のみです。同梱の桃太郎・恋愛・探偵のテンプレートも、地名・人物名・述語の対象名・伏線の説明文まで日本語で書かれています。英語の世界・テンプレートを自作すること自体は、述語や YAML の構造が日本語に依存していないため妨げられませんが、公式に用意された英語テンプレートはありません。

## Windows 以外（Mac/Linux）でも使えますか？

配布版exe（Viewer / Studio）はWindows向けです。ソース版のジョブ管理はWindows/Linuxに対応し、Linuxにはプロセス監視の要件があります。macOSでのジョブ実行は対応対象ではありません。ブラウザーからの公開サンプル閲覧は利用できます。詳しくは[インストール](../getting-started/install.md)を参照してください。

## 公開ビューアと配布版 Studio の違いは？

[公開ビューア](https://ato-labo.github.io/WorldBloom/)とインストール不要の Windows 版 `WorldBloom.exe` は、あらかじめ生成済みの実験（桃太郎・恋愛・探偵）を閲覧するだけの読み取り専用です。自分で GA 実験を実行し、Sifting・あらすじ・本文生成まで行うには `WorldBloom-Studio.exe`（別途 Python が必要）か、ソースから動かす方法を使ってください。詳しくは[インストール](../getting-started/install.md)を参照してください。
