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
  - "gapengine/synopsis.py"
  - "gapengine/evolve.py"
  - "gapengine/seed_genomes.py"
  - "engine/sim.py"
reviewed: "5f8da48fb2b8a4b0bb9d84af92e23ff7e7a6e381"
---
# 性格の成長（plasticity）

[遺伝子（Genome）](genome.md)が表す9個の数値は、生まれつきの方針であり、GA が世代をまたいで進化させる対象です。**性格の成長**は、これとは別に「1回のランの中で、物語の中で起きた出来事に応じて、主人公の性格がわずかに動いていく」という、生まれたあとの変化を扱う任意（既定オフ）の機能です。

## 先天と後天

| | 何が変えるか | いつ変わるか | 遺伝するか |
|---|---|---|---|
| 遺伝子（生まれつき、plasticity を含む） | GA の交叉・突然変異 | 世代をまたいで | する |
| 性格の成長（生まれたあと。acquired） | ランの中で起きた出来事（勝敗・仲間・裏切りなど） | そのランの中で | しない（`Policy.acquired` は毎ラン `{}` から始まり、次の世代へは持ち越されない） |

「進化させるのは方針（遺伝子）だけ」という[遺伝子](genome.md)の設計原則は変わりません。性格の成長は、その方針が「1本のランの中で経験に応じてどれだけ動きうるか」を扱う、別の層の仕組みです。plasticity（「どれだけ動きうるか」の度合い）自体は他の9個と同じく遺伝子の一部であり、世代をまたいで交叉・突然変異します。遺伝するのはこの plasticity という**器**（＝経験でどれだけ動きやすいか）であって、ランの中で実際に蓄積された変化（acquired）そのものではありません。

## plasticity 遺伝子 { #plasticity }

`plasticity`（[0, 1]、中立値 0）は、遺伝子に追加された10個目のスカラーです。ただし他の9個と違って**明示的にオプトインしない限り、常に0のまま**です。

- **初期化**（`Genome.random`）: `plastic=True` を渡されたときだけ乱数を1回消費して値を引き、それ以外（既定）では乱数消費もなく `plasticity=0` になります
- **交叉**（`Genome.crossover`）: `plastic=True` のときだけ、他の8個のスカラーと同じく両親どちらかの値を50%の確率でそのまま継ぎます（`plastic=False` なら常に0）
- **突然変異**（`Genome.mutate`）: `plastic=True` のときだけ、確率 p（既定0.3）でガウス揺らぎ（σ=0.1）を加えたのち [0, 1] にクリップします（`plastic=False` なら常に0に戻されます）
- **前の実験から引き継ぐ**（[実行設定](../usage/run-settings.md)の evolution.seed_genomes）: 引き継ぎ元の実験で plasticity が非ゼロでも、**今回のランで性格の成長がオフなら 0 に戻されます**（`gapengine/seed_genomes.py` の `reconcile`）。オンかつ引き継ぎ元がオフだった場合は 0 のまま引き継がれます

plasticity=0 の個体は、以下のあらゆる仕組みが完全に無効化され、性格の成長が存在しなかったときとバイト単位で一致する結果になります。

## outcome スコープのルールと growth イベント

ジャンルの `rules.yaml` に、`scope: outcome` を持つルールを書けます（既存の `candidate`・`turn` スコープとは別枠で、通常の候補重み計算には一切混ざりません）。このルールは「行動が起きたあと」に評価され、条件に一致するたびに `plasticity × adjust` の分だけ、そのランの `Policy.acquired`（遺伝子への蓄積シフト）に加算されます（`gapengine/policy.py` の `Policy.observe`）。outcome スコープのルールは、[メタ進化](genome.md)がルールごとに有効・無効を探索する対象（`rule_bits`）にも含まれません（`gapengine/evolve.py` の `_rule_ids`）。オン・オフできるのは常に `candidate`・`turn` スコープのルールだけです。

`observe` は主人公に限らず、Policy（遺伝子）を持つ全主体で毎ターン呼ばれます。既定では敵役は Policy を持たない（`policy=None`）ため growth するのは主人公だけですが、[共進化](genome.md#coevolution)をオンにして敵役にも遺伝子を持たせた場合、敵役の plasticity が非ゼロなら敵役も同様に growth し得ます。

`when` 述語が読める束縛（bindings）:

| 束縛 | 意味 |
|---|---|
| `kind` | 行のログ種別（`decision` または `event` のいずれか） |
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
| `g_betrayed` | 自分が当事者だが、裏切った側は自分ではない（裏切られた） | `stance_shift_bias -0.2`・`category_weight.III -0.1`（人を信じにくくなる） |

一致するたびに `details.shift = plasticity × adjust` が `layers.jsonl` に `verb: "growth"` の派生イベントとして記録され、`acquired` に減衰なく積み上がっていきます（勝利と敗北のように打ち消し合って正味0に戻ったキーは記録から外れます）。実際に重み計算へ使われるのは `Policy.current_genome() = clip(genome + acquired)` で、`Policy.reweight` はこれを「今の性格」として読みます。

## CLI と実行設定での有効化

- CLI: `python scripts/evolve.py ... --personality-growth`（既定オフ）。名前は WORLDGROW-002 の「世界を育てる」とは無関係の別機能であることを区別するため、あえて `personality_growth` という設定キー名にしています
- 画面: [実行設定](../usage/run-settings.md)の「03. 保存と進化」区画にある **「性格の成長」** チェックボックス（既定オフ）。「世界を育てる」（世界そのものを拡張する機能）とは別物で、世界の設定は一切変えません

オフのままなら、GA の乱数消費・重み計算・出力ファイルの形は性格の成長が存在する前と完全に同じです。

## あらすじ・本文への反映

`growth` イベントそのものは、ターンを「注目ターン」として選ぶ理由には**なりません**（`gapengine/scenes.py` の `SPECIAL_PRIORITY` は growth を意図的に含みません）。性格の変化は勝敗・裏切りなど、その回の他の出来事の結果として起きるものであり、性格が動いたことだけを理由にそのターンを選んでしまうと、伏線の回収や対象の移転など、限られた「注目ターン」の枠を奪い合う他の出来事より優先されてしまうためです。**すでに何か別の理由で選ばれたターン**に growth イベントがあれば、そのターンの記述に性格の変化が添えられます。

添える文は `growth_event_text()` が「{ルールの description}（{遺伝子の表示名} {符号付き変化量}、…）」の形に整形します（`g_fight_lost` かつ `plasticity=1.0` の例: `敗北で慎重になる（慎重さ -0.150）`。実際の変化量は `plasticity × adjust` なので、plasticity が小さいほど値も小さくなります）。あらすじ・本文生成のプロンプトには、この行が「性格の変化: …」として渡り、実物の指示文（`gapengine/synopsis.py`）どおり「性格の変化は出来事の結果として書き、変化の前と後の行動の違いが伝わるようにする」という指示が追加で付きます。growth イベントが1件もないランでは、プロンプトはこの機能が無いときとバイト単位で一致します。

## ラン詳細の「性格の変化」パネル

そのランで `growth` 行が1件でも記録されていれば、ラン詳細画面に「性格の変化」というカードが追加表示されます（`viewer/pages.py` の `_growth_panel`）。

- **性格（開始時）**と**性格（今）**の9指標バーを並べて比較（`genome` と `genome + acquired` の並列表示。ターンごとの一時的なルール変調は含みません）
- growth イベントごとに「ターン・きっかけと変化」の一覧表

「人物」表に出る気質バー（`traits`）はここでは変わりません（気質は元々GAの対象外で、性格の成長とも無関係です）。

## QD の第3軸「性格の変化」（任意） { #arc-axis }

性格の成長を有効にしたうえで、ジャンルの `templates/<ジャンル>/qd.yaml` に `arc_bins: [none, small, large]` を宣言すると、[QD 格子](qd-map.md)に3つ目の軸（性格の変化の大きさ）が追加されます。どちらか一方が欠けていると（性格の成長オフ、または `arc_bins` の宣言なし）、この軸は存在せず、アーカイブのセルキーは従来どおり2要素（`カテゴリ|起伏`）のままバイト単位で一致します。

- **arc**（変化量）: そのランの主人公が最後に記録した `growth` イベント時点までの累積シフト（`acquired_after`）の絶対値の合計（L1ノルム）。`acquired` は減衰なく積み上がるので、この値はランを通じた性格の変化の総量になります。growth イベントが一度も無いランは 0.0
- **区分**: `arc == 0` は必ず `none`。`arc > 0` のランは、その世代の母集団のうち **`arc > 0` のものだけ**の中央値（`split`）で `small`/`large` に二分されます。閾値は世代0で凍結され、以降の世代・再実行でも同じ `archive.json` の `arc_thresholds` を使い続けます。世代0に `arc > 0` のランが1本も無かった場合（その世代では誰も成長しなかった場合）は、`split` が固定値 0.1 にフォールバックします（あとの世代で成長するランが現れても small/large の区別ができるように）
- **セルキー**: 軸が有効なときだけ `"<主導カテゴリ>|<起伏区分>|<性格変化区分>"`（例 `I|low|small`）の3要素になります

Sifting の画面（格子の一覧）では、この軸が有効な実験のときだけ「性格の変化で絞り込み」の切り替え（すべて／なし／小／大）が格子の上に出ます。既定では各セルに最良品質の1件だけが代表として表示されます。

## GA を使う意味 { #ga-vs-random }

「GA（方針を進化させる）」と「無作為（`scripts/random_baseline.py`）」で、この性格の成長・第3軸を含む条件を比べた実測です（桃太郎系、評価回数（400回）を揃え、乱数の種5通りで試行。GA は20世代×20個体、無作為は1世代×400個体、いずれも個体ごと3seed評価）。

| | GA | 無作為 |
|---|---|---|
| 到達率（平均） | 0.132 | 0.061 |
| 占有マス数（平均） | 17.0 | 13.4 |
| QD スコア（平均、セルの品質合計） | 4.80 | 3.47 |
| 最高の質（平均） | 0.429 | 0.382 |

性格の成長・第3軸を使わない条件（18マスの格子）では、GA と無作為の差はほとんど出ませんでした（占有マス数 7.2 対 7.2、QD スコア 2.12 対 2.21）。

**この比較の限界**: 試行はそれぞれ5回のみで、統計的な検定は行っていません。また、性格の成長・第3軸を有効にした条件はマスの上限が18から54（6×3×3）に増えており、GA と無作為の差が広がった原因が「性格の成長という探索対象が増えたこと」なのか「単にマス数の上限が増えたこと」なのかを、この実測では切り分けられていません。「経験に応じて性格が動く」という探索対象が増えたことで方針を進化させる意味がより出やすくなった可能性はありますが、上記の理由により断定はできません。
