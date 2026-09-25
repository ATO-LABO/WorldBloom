---
sources:
  - "docs/2026-09-11_gapengine-detailed-design.md"
  - "templates/momotaro_plus2/action_graph.yaml"
  - "templates/detective/qd.yaml"
  - "templates/romance/qd.yaml"
  - "scripts/export_static.py"
reviewed: "4394c7ef86af65bb0f995a9071da5f1744e3ef87"
---
# 行動タイプ I〜VI

登場人物が取りうる行動は、動詞の名前ではなく「その行動が何をするものか」で6つのカテゴリに分類されます。この分類は[遺伝子](genome.md)のカテゴリ重み `category_weight` が直接対応する単位であり、[QD 格子](qd-map.md)の縦軸「主導カテゴリ」もこの分類を使います。

## 6つのカテゴリ

| カテゴリ | 短い呼び名 | 代表的な行動 |
|---|---|---|
| I | 力（自己強化・弱体化） | `train`（修行）、`fight`（勝負）、`steal_credit`（横取り）、`sabotage`（妨害）、`neutralize`（弱点を突いた無力化）、`investigate`・`craft` によるアイテム獲得 |
| II | 情報（認識と伏線） | `observe`（観測・偵察）、`plant`（伏線設置）、`payoff`（伏線回収）、`mislead`（ミスリード）、`confront`（露見・暴露）、`rethink`（信念の再走査） |
| III | 社交（関係構築） | `share_knowledge`（事実を教える）、`give_item`（贈与）、`persuade`（説得）、`pledge`（契約・誓約）、`grand_gesture`（大きな贈り物や公開の行為）、`trial`（試練の通過） |
| IV | 身分 | `disguise`（変装）、`steal_credit` や同じ目的を狙う別主体による偽主人公・横取り |
| V | 移動と停滞 | `move`（越境を含む移動）、`craft`・`investigate` の回数による難題、`pursue_target`（追跡・逃走）、`rest`・`withdraw`（停滞・内省）、`downed`→`revive`/`rescue`（疑似的な死と再生） |
| VI | 外部と還元 | `scheduled_events`・`daily_events`（主体が選ばない外部イベント）、`donate`（報酬・還元） |

短い呼び名（力・情報・社交・身分・移動と停滞・外部と還元）は静的ビューア（`scripts/export_static.py`）やグラフの表示に使われる名前です。設計書ではそれぞれ「I 自己強化」「II 認識と伏線」「III 関係構築」「IV 身分」「V 移動と停滞」「VI 外部介入」とも呼ばれます。

## 分類は動詞単位ではなく実行時の文脈単位

同じ動詞でも、実行時の状況によって属するカテゴリが変わります。たとえば `investigate` は、`gather`（探索）の文脈なら I（自己強化）ですが、それ以外の文脈では II（認識と伏線）に分類されます。`move` も、`crossing`（身分の境界越え）の文脈なら V（移動と停滞）ですが、それ以外では QD 格子の対象外（`category: null`）になります。この分類は行動の実行時（決定イベント）に記録され、あとから QD の記述子を計算するときに再分類はしません。

## 前提条件と行動グラフ

各ジャンルのテンプレートは `templates/<ジャンル>/action_graph.yaml` に、行動タイプごとの前提条件（AND 条件、`edges:`）と、対象の役割（`hostile`/`neutral`/`ally`）ごとの許可レベル（`allow` / `restricted` / `deny`）を宣言します。前提を満たさない行動候補はそもそも列挙されないため、「観測して弱点を知ってから無力化する」のような因果の順序は、あとから検証するのではなく、候補が並ぶ時点で構造的に保証されます。`restricted` は候補から除外されるのではなく、`restricted_weight`（テンプレートごとに設定。例: `momotaro_plus2` は 0.15）倍に重みが下がった状態で候補に残ります。

## ジャンルによってカテゴリ数が違う

QD 格子の縦軸に使うカテゴリの範囲は、`templates/<ジャンル>/qd.yaml` の `categories` で宣言します。実際のテンプレートでは次のように、ジャンルによって使うカテゴリ数が異なります。

| ジャンル | 使うカテゴリ |
|---|---|
| 桃太郎（`momotaro_plus2`） | I, II, III, IV, V, VI（6カテゴリすべて） |
| 探偵（`detective`） | I, II, III のみ |
| 恋愛（`romance`） | I, II, III のみ |

探偵・恋愛のように身分の入れ替わりや外部介入をほとんど使わないジャンルでは、格子の軸を絞ることで、実際に起こりうる行動の幅に合わせた粒度になります。
