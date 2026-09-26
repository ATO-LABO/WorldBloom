---
sources:
  - "viewer/world_create.py"
  - "viewer/library_pages.py"
  - "execution/library.py"
  - "execution/world_editor.py"
  - "docs/world-import-guide.md"
reviewed: "281fe6aede8ab22d63e61f07b5a7a6bd8280b5e0"
---
# 世界を ZIP から取り込む

AI（Codex・Claude Code・Gemini など）に作らせた世界設定を、ZIPファイルとして
WorldBloom に取り込めます。[世界を作る](create-world.md)の「新しい世界を作る」画面に
ある3つ目の作成方法です。

## 入口

ホーム画面（**世界を選ぶ**）の **＋ 新しい世界を作る** →「**ZIPから取り込む**」を選び、
`world.yaml` と `subjects/*.yaml` をまとめたZIPファイルを選択します。

## ZIPに入れるもの

ZIPの作り方の仕様書は
[`docs/world-import-guide.md`](https://github.com/ATO-LABO/WorldBloom/blob/main/docs/world-import-guide.md)
（GitHub）です。この文書をAIツールにそのまま渡して依頼すると、互換性のあるZIPを
作ってもらえます。

## 上限

- 1ファイルあたり256KB以内
- ZIP本体は47KB以内
- ファイル数は200件以内
- 展開後の合計サイズは2MB以内

超えるとアップロード時点でエラーになります。

## 名前とジャンル

- **表示名**: フォームの「世界の名前」欄が空欄なら、ZIPの `world.yaml` の `name` を
  使います。フォームに入力すれば、その値で上書きされます。
- **ジャンル**: ZIPの指定に従います。`world.yaml` に `gapengine`（`action_graph`・
  `effects`）を書けばそのジャンルで取り込まれ、書かなければ**未設定**で取り込まれます。

## 取り込み後

取り込んだ世界は、そのまま[世界設定画面](create-world.md#世界設定画面世界ごとのタブ)に進みます。

- **ジャンルが未設定の場合**: 「世界の概要」タブのジャンル行から選べます。選ぶまでは
  GA実験を実行できません。
- 内容の確認は、実行設定の「検証」、または世界設定画面の各タブで行ってください。

## エラーが出たら

主なメッセージと原因です（詳しくは
[`docs/world-import-guide.md`](https://github.com/ATO-LABO/WorldBloom/blob/main/docs/world-import-guide.md)
の8章を参照）。

| メッセージ | 原因 |
|---|---|
| `ZIPにworld.yamlがありません` | `world.yaml` がZIP直下（または単一の共通フォルダ直下）にない |
| `<path>: 対象外のファイル名です` | `subjects/` 配下のファイル名が規則外 |
| `ZIPは47KB以内にしてください` | ZIP本体が大きすぎる |
| `ZIPのジャンル「<id>」（templates/<id>/）がありません...` | 指定したジャンルが存在しない |
| `名前を入力してください（ZIPのnameも空です）` | フォームの表示名も `world.yaml` の `name` も空 |

## 閲覧専用の Viewer では使えません

配布版の **Viewer exe**（読み取り専用ビューア）はすべての書き込みを拒否するため、
この画面自体を開けません。取り込みは **Studio exe** または `--control` 付きで
起動したサーバーから行ってください。
