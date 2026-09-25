<div align="center">

# WorldBloom

**遺伝的アルゴリズム×LLM による結末固定型の物語生成エンジン**

[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Release](https://img.shields.io/github/v/release/ATO-LABO/WorldBloom)](https://github.com/ATO-LABO/WorldBloom/releases/latest)

<a href="https://youtu.be/Yozi2qb2IQg"><img src="https://img.youtube.com/vi/Yozi2qb2IQg/maxresdefault.jpg" width="640" alt="WorldBloom 3分ダイジェスト動画のサムネイル"></a>

[![Download WorldBloom Studio](https://img.shields.io/badge/Download-WorldBloom--Studio--portable.zip-2f6feb?style=for-the-badge&logo=windows&logoColor=white)](https://github.com/ATO-LABO/WorldBloom/releases/latest/download/WorldBloom-Studio-portable.zip)
[![公開ビューア](https://img.shields.io/badge/%E3%83%96%E3%83%A9%E3%82%A6%E3%82%B6%E3%81%A7%E8%A6%8B%E3%82%8B-%E5%85%AC%E9%96%8B%E3%83%93%E3%83%A5%E3%83%BC%E3%82%A2-2e7d4f?style=for-the-badge&logo=googlechrome&logoColor=white)](https://ato-labo.github.io/WorldBloom/)
[![3分動画](https://img.shields.io/badge/3%E5%88%86%E3%81%A7%E3%82%8F%E3%81%8B%E3%82%8B-YouTube-c4302b?style=for-the-badge&logo=youtube&logoColor=white)](https://youtu.be/Yozi2qb2IQg)
[![ドキュメント](https://img.shields.io/badge/%E3%83%89%E3%82%AD%E3%83%A5%E3%83%A1%E3%83%B3%E3%83%88-ato--labo.github.io-6f42c1?style=for-the-badge&logo=readthedocs&logoColor=white)](https://ato-labo.github.io/WorldBloom/docs/)

</div>

## これは何か

物語の**設定と結末を先に決める**と、そのあいだの道のりを何千通りもシミュレーションで試し、結末にたどり着いたものの中から面白い展開を選べるツールです。

昔話・恋愛もの・探偵ものなど「結末が分かっている物語」は、結末そのものより道中にこそ面白さがあります。WorldBloom は、その道中を人の代わりに探し出し、選ばれた展開だけを文章にします。

<p align="center">
  <img src="docs/images/readme-hero.png" width="820" alt="WorldBloomビューアのSifting画面。進化で見つかった物語の展開が格子状に並び、あらすじが表示されている">
</p>

## 制作哲学

人もまた、世界の中で起こる自然現象のひとつです。場所、人と人との関係、時間。主人公を取り巻く世界を十分に定義すれば、物語は自然とその方向へ転がり出します。だから物語を作ることは、世界を作ることです。

人の選択も、関係と条件の網の目から生まれます（スピノザ、サポルスキー、仏教の「縁起」）。WorldBloom は「自由意志は存在しない」とする説を支持する立場で作られています。それでも登場人物には自由があります。原因なしに選べることではなく、外から曲げられずに自分の性質のままに動けること、老荘思想の「自然（おのずから然る）」です。「キャラクターが勝手に動き出す」とはその自由を得た瞬間であり、「ご都合展開」とは作者の「作為」がその自由を奪った跡です。

そして意味は、出来事のあとからやってきます（予測符号化・能動的推論、ガザニガの「解釈者」、ベムの自己知覚理論）。だから WorldBloom は、まず GA とシミュレーションで世界の中の行動を起こし、それを LLM が解釈して物語にします。

全文 → [WorldBloom の制作哲学](https://www.atom-box.jp/worldbloom/philosophy/)

## 仕組み（要約）

主体は7層構造（力・認識・資源・段階・身分・目的物・伏線）＋ vitality で表現し、遺伝子は行動系列ではなく「戦略ベクトル」（9スカラー）です。固定シードで走らせたシミュレーションのうち、固定結末に到達したものだけを MAP-Elites 格子（主導カテゴリ I〜VI × volatility）に残します。**出来事の生成に LLM は関与しません。**格子に残ったあらすじは人が読んで選び、選ばれた道のりだけを LLM が本文化します。

StorySim（同作者の別プロジェクト。世界と人物をシミュレートし、面白かったログを物語に書き起こす方式）から要素を切り取って再構築したもので、フォークではありません。

詳しくは → [WorldBloom の仕組み](https://www.atom-box.jp/worldbloom/how-it-works/)

## できること

- **QD 格子での探索と選定（Sifting / Screening）**: 主導カテゴリ×volatilityの格子から気に入った展開を選び、あらすじ・本文だけを生成
- **世界の自己拡張**: 実験結果から世界設定の拡張案を提案させ、承認/却下したうえで拡張あり/なしの結果を比較
- **系譜・転機の可視化**: 世代を追った戦略の推移と、物語の転機をグラフで表示
- **Jev（合理性チェック層）**: 行動選択が「もっともらしいか」をκスライダーで調整しながら検証（開発中の機能）

GPU 調停・進捗表示など運用まわりの機能は、ドキュメントサイトの[使い方](https://ato-labo.github.io/WorldBloom/docs/usage/gpu-guard/)を参照してください。

## 試し方

**詳しい手順・スクリーンショット付きの説明は → [ドキュメントサイト](https://ato-labo.github.io/WorldBloom/docs/)（[インストール](https://ato-labo.github.io/WorldBloom/docs/getting-started/install/) / [クイックスタート](https://ato-labo.github.io/WorldBloom/docs/getting-started/quickstart/)）**

- **A. exe で（Windows）**: [WorldBloom-Studio-portable.zip](https://github.com/ATO-LABO/WorldBloom/releases/latest/download/WorldBloom-Studio-portable.zip) を展開して `WorldBloom-Studio.exe` を開くだけ。世界を選んでGA実験を実行し、Sifting・あらすじ/本文生成まで行えます（GA実験の実行には Python 3.11以上＋PyYAML が別途必要）。
- **B. ブラウザで見るだけ**: インストール不要の [公開ビューア](https://ato-labo.github.io/WorldBloom/) で、桃太郎・恋愛・探偵の3実験の格子・あらすじ・本文を読めます。
- **C. ソースから**: `git clone` して `pip install -r requirements.txt`（Python 3.11以上）。同梱の `samples/` をビューアで見る、自分で GA を回すなどの手順はドキュメントサイトの[インストール](https://ato-labo.github.io/WorldBloom/docs/getting-started/install/)にまとめています。

自分で進化を回す・あらすじ/本文を生成し直す・LLM バックエンドの設定・GPU ガードなどの使い方は、ドキュメントサイトの[使い方](https://ato-labo.github.io/WorldBloom/docs/usage/create-world/)を参照してください。開発者向け（回帰テスト・決定論の約束など）は [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)。

## もっと読む

### 読み物（ATOM-BOX サイト）

仕組みや背景を読み物として整理したページを公式サイトに置いています。コードを読む前の入口としてはこちらが向いています。

- [WorldBloom（ハブ）](https://www.atom-box.jp/worldbloom/) — 概要・できること・ダウンロード
- [WorldBloom の制作哲学](https://www.atom-box.jp/worldbloom/philosophy/) — 物語を書くことは世界を作ること。意味は行動のあとに生まれる
- [WorldBloom の仕組み](https://www.atom-box.jp/worldbloom/how-it-works/) — 7 層構造、遺伝子、結末固定、QD 格子、出口
- [WorldBloom を試す](https://www.atom-box.jp/worldbloom/get-started/) — Studio / 公開ビューア / ソースからの実行
- [なぜ GA と LLM を組み合わせるのか](https://www.atom-box.jp/worldbloom/ga-and-llm/) — 両者の得手不得手と分業の理由
- [物語生成研究の中での位置づけ](https://www.atom-box.jp/worldbloom/background/) — Tale-Spin、進化的生成、MAP-Elites、LLM 長編生成との関係と参考文献
- [生成例: 桃太郎](https://www.atom-box.jp/worldbloom/example-momotaro/) — 同梱サンプル exp12 の格子・あらすじ・本文
- [用語集](https://www.atom-box.jp/worldbloom/glossary/) — 画面と解説に出る用語の定義

### 開発者向け（docs/）

- 開発者向けガイド（GA実験・LLMバックエンド設定・回帰テスト）: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)
- 詳細設計: [docs/2026-09-11_gapengine-detailed-design.md](docs/2026-09-11_gapengine-detailed-design.md)
- ビューア UI/UX 設計: [docs/2026-09-12_viewer-ux-design.md](docs/2026-09-12_viewer-ux-design.md)

## リポジトリ構成

```
engine/      7層の主体・世界・動詞・勝負解決・述語評価・vitality・ログ
gapengine/   遺伝子・Policy・分類器・前例表・QD・進化ループ・あらすじ化・説明抽出
execution/   ビューアから進化・出力ジョブを操作するための設定/ジョブ/権限境界
viewer/      HTTP ビューア（標準ライブラリのみ。格子・あらすじ・本文・実行管理画面）
scripts/     進化・あらすじ化・本文化・無作為基準・静的公開・サンプル抽出などの実行スクリプト
templates/   ジャンルテンプレート（行動グラフ・修飾ルール・正典プライア・QD軸・伏線ライブラリ）
projects/    世界と主体の初期状態（7層）。momotaro / detective / romance
samples/     ビューアで開ける最小サンプル（3実験。scripts/pack_samples.py で生成）
tests/       回帰テスト（unittest）
docs/        設計書・実装計画・開示文書
```

ラン出力は既定でリポジトリ外に置く（`--out` で明示する）。`samples/` は例外で、配布用にリポジトリへ含めている。

## ライセンス・作者

[Apache License 2.0](LICENSE)。Copyright 2026 ATO-LABO.

開発: ATO-LABO（秋山智哉）。技術的な質問や共同制作の相談は [ATOM-BOX Contact](https://www.atom-box.jp/contact/) からどうぞ。
