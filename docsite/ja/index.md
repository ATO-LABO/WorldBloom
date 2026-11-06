---
reviewed: "2e70a9b216b7b2a80a4228a10a928148060e8fc8"
---

# WorldBloom ドキュメント

物語の**設定と結末を先に決める**と、そのあいだの道のりを何千通りもシミュレーションで試し、結末にたどり着いたものの中から面白い展開を選べるツールです。遺伝的アルゴリズム（GA）でシミュレーションを回し、選ばれた展開だけを LLM が文章にします。

## 現行ソースでの作業

[世界を作る](usage/create-world.md)では、「下書きを用意 → 内容を整える → 確認して採用」の3段階を進め、途中の保存・再開や任意のAI精緻化を行えます。確定した世界の条件を編集してから実験を実行し、[結果を選ぶ](usage/read-results.md)・[文章を生成する](usage/generate-text.md)へ進みます。

現行ソースで編集・実行できるのは新形式の世界です。最初の実行には「桃太郎＋3」（`momotaro_plus3_new`）を使います。旧世界と過去の結果は保持し、形式を自動で書き換えません。配布EXEの内容は各Releaseに従い、ソース版の変更履歴とは区別してください。

## 3つの試し方

1. **公開ビューア（インストール不要）**: [https://ato-labo.github.io/WorldBloom/](https://ato-labo.github.io/WorldBloom/) を開くだけ。桃太郎・恋愛・探偵の3実験を閲覧できます。
2. **配布版 exe（Windows）**: 閲覧専用の Viewer、GA実験の実行から文章生成まで行える Studio の2種類。→ [インストール](getting-started/install.md)
3. **ソースから**: リポジトリを clone して Python から動かす。→ [インストール](getting-started/install.md)

## このサイトの構成

- **はじめに**: 導入と最初の一周（クイックスタート）
- **概念**: 7層構造・遺伝子・QD 格子・Sifting・決定論の考え方
- **使い方**: 画面ごとの操作方法、設定、LLM バックエンド
- **リファレンス**: CLI 引数・settings.json・テンプレート・出力ファイル・HTTP API
- **ガイド**: トラブルシューティング・FAQ・変更履歴

考え方や制作背景は ATOM-BOX の解説ページ（[https://www.atom-box.jp/worldbloom/](https://www.atom-box.jp/worldbloom/)）にまとまっています。仕組みを読み物として理解したい場合はそちらもどうぞ。
