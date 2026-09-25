---
sources:
  - "docs/2026-09-11_gapengine-detailed-design.md"
  - "templates/momotaro/effects.yaml"
  - "gapengine/qd.py"
reviewed: 4394c7ef86af65bb0f995a9071da5f1744e3ef87
---
# 伏線（遅延効果）

[7層構造](seven-layers.md)の7番目の層は、先に置いた行動があとで効いてくる**遅延効果**を持ちます。「設置」（plant）と「回収」（payoff）という2つの瞬間を持ち、この間の遅延が、あとから振り返ったときに「伏線」として読める構造を生みます。

## 誰が回収の引き金を引くか — auto と chosen

伏線ライブラリ（`templates/<ジャンル>/effects.yaml`）は、1件ごとに回収の引き金を誰が引くかを宣言します。

- **`mode: auto`**: 行為の自然な帰結として、条件が揃った瞬間に自動で発火します。人物の選択ではありません。例: きびだんごを渡した相手が、後で戦闘に居合わせたときに自動で忠誠を発揮する。
- **`mode: chosen`**: 明かす・使う時機そのものに意味がある伏線です。条件が揃うと「回収する」（行動タイプ II-3、`payoff`）が候補として立ち、[遺伝子](genome.md)に従って選ばれるかどうかが決まります。例: 偵察で知った「鬼の力の源は金棒だけ」という事実を、上陸直後に使うか、仲間が揃うまで温存するか、追い詰められた最後の手として切るか——この時機の違いを GA が探索します。

```yaml
# templates/momotaro/effects.yaml
- id: kibidango_loyalty
  plant:
    verb: give_item
    item: きびだんご
    to_role: [neutral, ally]
  payoff:
    condition: "stance(target, planter) >= 0.5 and hostile_present(planter)"
    description: きびだんごの恩に報いる仲間の奮戦
    effect:
      modifier: {target: planter, source: "$target", value: 10, kind: loyal}
    mode: auto

- id: oni_gap
  plant:
    verb: observe
    reveals: {target: 鬼, source: 金棒}
  payoff:
    condition: "present(鬼) and vitality(planter) != 'downed'"
    description: 金棒の隙を見切って仲間に合図を送る
    effect:
      modifier: {target: planter, source: 見切り, value: 15}
    mode: chosen
```

`plant` は多くの場合、既存の動詞（`give_item`、`observe` など）の実行そのものが設置を兼ねます。専用の `plant` 動詞を使う伏線もあります。どの伏線を仕込むかは、行動タイプ II（情報）のカテゴリ重みと新規性志向によって変調されます。

## 回収されない伏線の減点

`chosen` の伏線が、条件を満たしたまま最後まで回収されずに物語が終わると、品質 q が**1件あたり −0.05** 減点されます（`gapengine/qd.py`）。「撃たれない銃」——仕込んだのに一度も使われない伏線——を GA に淘汰させるための仕組みです。`auto` の伏線は人物の選択を経ないため、この減点の対象にはなりません。

## ログ（layers.jsonl）での見え方

設置と回収はそれぞれ、実行ログ `layers.jsonl` に決定イベント／派生イベントとして記録されます。回収が発生すると、そのイベントの効果（`modifier` や `enable_verb` など）がその場で7層に反映されます。出力段では、この設置・回収のペアが「注目ターン」として抽出され、あらすじ生成のプロンプトに渡されます（詳しくは[実行結果のファイル](../reference/run-outputs.md)）。
