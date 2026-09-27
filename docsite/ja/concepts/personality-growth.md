---
sources:
  - "gapengine/genome.py"
  - "gapengine/policy.py"
  - "gapengine/qd.py"
  - "gapengine/scenes.py"
  - "templates/momotaro_plus2/rules.yaml"
  - "templates/momotaro_plus2/qd.yaml"
  - "viewer/run_settings.py"
  - "viewer/pages.py"
  - "scripts/evolve.py"
reviewed: "780367c9518f18cdcc3cadd5907f8dfa2331d614"
---
# 性格の成長（plasticity）

[遺伝子（Genome）](genome.md)が表す9個の数値は、生まれつきの方針であり、GA が世代をまたいで進化させる対象です。**性格の成長**は、これとは別に「1回のランの中で、物語の中で起きた出来事に応じて、主人公の性格がわずかに動いていく」という、生まれたあとの変化を扱う任意（既定オフ）の機能です。

## 先天と後天

| | 何が変えるか | いつ変わるか | 遺伝するか |
|---|---|---|---|
| 遺伝子（生まれつき） | GA の交叉・突然変異 | 世代をまたいで | する |
| 性格の成長（生まれたあと） | ランの中で起きた出来事（勝敗・仲間・裏切りなど） | そのランの中で | しない（`Policy.acquired` は毎ラン `{}` から始まり、次の世代へは持ち越されない） |

「進化させるのは方針（遺伝子）だけ」という[遺伝子](genome.md)の設計原則は変わりません。性格の成長は、その方針が「1本のランの中で経験に応じてどれだけ動きうるか」を扱う、別の層の仕組みです。

## plasticity 遺伝子

`plasticity`（[0, 1]、中立値 0）は、遺伝子に追加された10個目のスカラーです。ただし他の9個と違って**明示的にオプトインしない限り、常に0のまま**です。GA（`Genome.random`/`crossover`/`mutate`）が `plastic=True` を渡されたときだけ乱数を1回消費して値を引き、それ以外（既定）では乱数消費もなく `plasticity=0` になります。plasticity=0 の個体は、以下のあらゆる仕組みが完全に無効化され、性格の成長が存在しなかったときとバイト単位で一致する結果になります。

## outcome スコープのルールと growth イベント

ジャンルの `rules.yaml` に、`scope: outcome` を持つルールを書けます（既存の `candidate`・`turn` スコープとは別枠で、通常の候補重み計算には一切混ざりません）。このルールは「行動が起きたあと」に評価され、条件に一致するたびに `plasticity × adjust` の分だけ、そのランの `Policy.acquired`（遺伝子への蓄積シフト）に加算されます（`gapengine/policy.py` の `Policy.observe`）。

`when` 述語が読める束縛（bindings）:

| 束縛 | 意味 |
|---|---|
| `kind` | 行のログ種別（`decision`/`event` など） |
| `verb` | 動詞（`fight`・`downed`・`ally_gained`・`betrayal` など） |
| `result` | 結果（`won`/`lost` など。無い行では `null`） |
| `actor_is_self` | この主体自身の行動か |
| `target` | 対象の主体ID（解決できないときは空文字列） |
| `category` | 分類済みカテゴリ（I〜VI） |
| `risk_class` | リスク級 |
| `stance_sign` | 関係を上げる/下げる方向 |
| `target_role` | 対象の役割 |
| `effective` | 実際に7層へ変化を与えたか |
| `stress_after` | 変化後のストレス値（`decision` 以外・値が無いときは `null`。`None` と数値の比較は例外になるため、`when` 側で必ず `stress_after is not None and ...` のように先にガードすること） |
| `involves_self` | 自分自身が関わる出来事か（行為者・対象・delta の対象のいずれか） |

桃太郎＋2 の `templates/momotaro_plus2/rules.yaml` に定義済みの初期5本:

| ルールID | 条件 | 変化 |
|---|---|---|
| `g_fight_lost` | 自分が戦って敗れた | `risk_tolerance -0.15`（敗北で慎重に） |
| `g_fight_won` | 自分が戦って勝った | `risk_tolerance +0.1`・`category_weight.I +0.1`（勝利で大胆に） |
| `g_downed` | 自分が倒れた | `risk_tolerance -0.2`（倒れて慎重に） |
| `g_ally_gained` | 仲間を得た | `category_weight.III +0.1`・`stance_shift_bias +0.1`（人との関わりを重んじる） |
| `g_betrayed` | 自分以外が関わる裏切りに巻き込まれた | `stance_shift_bias -0.2`・`category_weight.III -0.1`（人を信じにくくなる） |

一致するたびに `details.shift = plasticity × adjust` が `layers.jsonl` に `verb: "growth"` の派生イベントとして記録され、`acquired` に減衰なく積み上がっていきます（勝利と敗北のように打ち消し合って正味0に戻ったキーは記録から外れます）。実際に重み計算へ使われるのは `Policy.current_genome() = clip(genome + acquired)` で、`Policy.reweight` はこれを「今の性格」として読みます。

## CLI と実行設定での有効化

- CLI: `python scripts/evolve.py ... --personality-growth`（既定オフ）。名前は WORLDGROW-002 の「世界を育てる」とは無関係の別機能であることを区別するため、あえて `personality_growth` という設定キー名にしています
- 画面: [実行設定](../usage/run-settings.md)の「03. 保存と進化」区画にある **「性格の成長」** チェックボックス（既定オフ）。「世界を育てる」（世界そのものを拡張する機能）とは別物で、世界の設定は一切変えません

オフのままなら、GA の乱数消費・重み計算・出力ファイルの形は性格の成長が存在する前と完全に同じです。

## あらすじ・本文への反映

`gapengine/scenes.py` は `growth` イベントを含むターンを「注目ターン」として拾い上げ、`growth_event_text()` が「{きっかけの説明}（{ルールの説明} {符号付きの変化量}、…）」という短い文に整形します。あらすじ・本文生成のプロンプトには、この行が「性格の変化: …」として渡り、"性格の変化は出来事の結果として書く（心情の独白として捏造しない）" という指示が追加で付きます。growth イベントが1件もないランでは、プロンプトはこの機能が無いときとバイト単位で一致します。

## ラン詳細の「性格の変化」パネル

そのランで `growth` 行が1件でも記録されていれば、ラン詳細画面に「性格の変化」というカードが追加表示されます（`viewer/pages.py` の `_growth_panel`）。

- **性格（開始時）**と**性格（今）**の9指標バーを並べて比較（`genome` と `genome + acquired` の並列表示。ターンごとの一時的なルール変調は含みません）
- growth イベントごとに「ターン・きっかけと変化」の一覧表

「人物」表に出る気質バー（`traits`）はここでは変わりません（気質は元々GAの対象外で、性格の成長とも無関係です）。

## QD の第3軸「性格の変化」（任意） { #arc-axis }

性格の成長を有効にしたうえで、ジャンルの `templates/<ジャンル>/qd.yaml` に `arc_bins: [none, small, large]` を宣言すると、[QD 格子](qd-map.md)に3つ目の軸（性格の変化の大きさ）が追加されます。どちらか一方が欠けていると（性格の成長オフ、または `arc_bins` の宣言なし）、この軸は存在せず、アーカイブのセルキーは従来どおり2要素（`カテゴリ|起伏`）のままバイト単位で一致します。

- **arc**（変化量）: そのランの主人公の最後の `growth` イベントが持つ `acquired_after` の絶対値の合計（L1ノルム）。growth イベントが一度も無いランは 0.0
- **区分**: `arc == 0` は必ず `none`。`arc > 0` のランは、その世代の母集団のうち **`arc > 0` のものだけ**の中央値（`split`）で `small`/`large` に二分されます。閾値は世代0で凍結され、以降の世代・再実行でも同じ `archive.json` の `arc_thresholds` を使い続けます
- **セルキー**: 軸が有効なときだけ `"<主導カテゴリ>|<起伏区分>|<性格変化区分>"`（例 `I|low|small`）の3要素になります

Sifting の画面（格子の一覧）では、この軸が有効なランのときだけ「性格の変化で絞り込み」の切り替え（すべて／なし／小／大）が格子の上に出ます。既定では各セルに最良品質の1件だけが代表として表示されます。

## GA を使う意味 { #ga-vs-random }

「GA（方針を進化させる）」と「無作為（`scripts/random_baseline.py`）」で、この性格の成長・第3軸を含む条件を比べた実測です（桃太郎系、個体・世代の規模を揃え、乱数の種5通りで試行。検定は行っていません）。

| | GA | 無作為 |
|---|---|---|
| 占有マス数（平均） | 17.0 | 13.4 |
| QD スコア（平均、セルの品質合計） | 4.80 | 3.47 |
| 最高の質（平均） | 0.429 | 0.382 |

性格の成長・第3軸を使わない条件（18マスの格子）では、GA と無作為の差はほとんど出ませんでした。「経験に応じて性格が動く」という探索対象が増えたことで、方針を進化させる意味がより出やすくなったと考えられます。
