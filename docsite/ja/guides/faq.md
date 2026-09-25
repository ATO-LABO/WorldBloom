---
sources:
  - "README.md"
  - "LICENSE"
  - "docsite/ja/usage/llm-backends.md"
  - "docsite/ja/usage/gpu-guard.md"
  - "templates/"
reviewed: 4394c7ef86af65bb0f995a9071da5f1744e3ef87
---

# FAQ

## GA 実験（進化）に LLM は必要ですか？

いいえ。GA 実験そのもの（世界のシミュレーション、遺伝子の進化、QD 格子への蓄積）に LLM は関与しません。LLM を使うのは、格子に残った候補を人が選んだあとの[あらすじ・本文の生成](../usage/generate-text.md)だけです。生成をせず結果だけ見たい場合は、⚙ 設定で接続先を「生成しない」（`none`）にすれば、プロンプトの保存だけを行い実際の生成はしません。詳しくは[LLM バックエンド](../usage/llm-backends.md)を参照してください。

## なぜ出来事の生成に LLM を使わないのですか？

出来事の列（誰が何をしたか）を LLM に直接書かせると、なぜその展開になったのかを機械的に追跡できなくなります。WorldBloom は出来事の生成をシミュレーションと乱数だけに限ることで、[決定論](../concepts/determinism.md)を保証し、ある物語がどの世代のどの親から、どんな分岐で生まれたかを[系譜・転機](../concepts/sifting.md#compare-lineage-turning)として機械的に示せるようにしています。詳しくは[全体の流れ](../concepts/overview.md)を参照してください。

## GPU が無い PC でも使えますか？

はい。GA 実験（進化）自体は GPU を必要としません。GPU が必要になるのは、あらすじ・本文の生成にローカル LLM（Ollama や llama-server）を使う場合だけです。GPU が無ければ、外部 API（Anthropic / OpenAI）や CLI 経由（Claude Code / Codex CLI）の接続先を使うか、生成をせずプロンプトの保存だけに留めることもできます。詳しくは[LLM バックエンド](../usage/llm-backends.md)・[GPU ガード](../usage/gpu-guard.md)を参照してください。

## 結果が毎回同じなのはなぜですか？

WorldBloom の個体評価は[決定論](../concepts/determinism.md)を設計の前提にしているためです。同じ世界・遺伝子・乱数の種・前例表・エンジンのコードなら、出力ログはバイト単位で一致します。逆に、エンジンのコード自体を変更すると、同じ設定でも別の物語になります。

## 自分の物語世界を作れますか？

はい。地名・経路・登場人物の初期状態などの「世界」と、そのジャンルの文法（行動グラフ・正典・伏線ライブラリ・QD の軸）を宣言する「テンプレート」を用意すれば、桃太郎・恋愛・探偵以外の世界も作れます。手順は[世界を作る](../usage/create-world.md)を参照してください。

## StorySim との関係は？

WorldBloom は StorySim（同作者の別プロジェクト）の延長ではなく、別プロジェクトとして再構築したものです。ゾーンと移動、関係行列、決定論の作法など一部の要素は StorySim を参照して設計・実装していますが、StorySim 本体のコードはそのまま流用していません。

## 商用利用・ライセンスは？

リポジトリは Apache License 2.0 で公開されています。ライセンス全文は [LICENSE](https://github.com/ATO-LABO/WorldBloom/blob/main/LICENSE) を確認してください。

## 英語の物語は作れますか？

画面（Viewer / Studio の UI）は日本語のみです。同梱の桃太郎・恋愛・探偵のテンプレートも、地名・人物名・述語の対象名・伏線の説明文まで日本語で書かれています。英語の世界・テンプレートを自作すること自体は、述語や YAML の構造が日本語に依存していないため妨げられませんが、公式に用意された英語テンプレートはありません。

## Windows 以外（Mac/Linux）でも使えますか？

配布版 exe（Viewer / Studio）は Windows 向けのビルドのみです。Mac/Linux では、リポジトリを clone して Python から動かす方法（[インストール](../getting-started/install.md)のソース版の手順）を使ってください。

## 公開ビューアと配布版 Studio の違いは？

[公開ビューア](https://ato-labo.github.io/WorldBloom/)とインストール不要の Windows 版 `WorldBloom.exe` は、あらかじめ生成済みの実験（桃太郎・恋愛・探偵）を閲覧するだけの読み取り専用です。自分で GA 実験を実行し、Sifting・あらすじ・本文生成まで行うには `WorldBloom-Studio.exe`（別途 Python が必要）か、ソースから動かす方法を使ってください。詳しくは[インストール](../getting-started/install.md)を参照してください。
