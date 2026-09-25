---
sources:
  - "templates/momotaro/action_graph.yaml"
  - "templates/momotaro/canon.yaml"
  - "templates/momotaro/effects.yaml"
  - "templates/momotaro/qd.yaml"
  - "templates/momotaro/rules.yaml"
  - "templates/detective/qd.yaml"
  - "templates/romance/qd.yaml"
  - "templates/momotaro_plus2/rationality.yaml"
  - "projects/momotaro_plus2/world.yaml"
  - "projects/momotaro_plus2/subjects/01_ojiisan.yaml"
  - "templates/momotaro_plus2/route.yaml"
  - "templates/momotaro_plus2/motives.yaml"
  - "gapengine/route.py"
  - "docs/2026-09-11_gapengine-detailed-design.md"
reviewed: "9765a5cbd9e8fab7a136d509381a7f59bf3899cb"
---
# テンプレート

「ジャンル」（`templates/<ジャンル>/`）が世界の文法を、「世界」（`projects/<世界>/`）がその文法を使った具体的な舞台を定義します。ジャンルは複数の世界から共有できます（例: `templates/momotaro_plus2` は `projects/momotaro_plus2` から参照されます）。

## `templates/<ジャンル>/` の構成

| ファイル | 役割 |
|---|---|
| `action_graph.yaml` | 使える行動（動詞）の一覧と、行動分類 `category`（I〜VI）・`subtype`・`risk`・`sign` |
| `canon.yaml` | 「そのジャンルでよくある」文脈→行動の擬似観測件数（正典プライア）。新規性志向の遺伝子が避ける対象であり、世代0の前例表の初期値 |
| `effects.yaml` | 伏線（遅延効果）ライブラリ。設置条件（`plant`）と回収条件・効果（`payoff`） |
| `qd.yaml` | QD 格子の軸。`categories`（行の主導カテゴリ）と `volatility_bins`（列の起伏区分） |
| `rules.yaml` | 修飾ルール。述語が成立する間だけ遺伝子の重みを一時的に加減する |
| `rationality.yaml`（任意） | κ（Jev 合理性判定）の既定値・判定器のバックエンド設定。無いジャンルは κ=0 相当で動く |
| `route.yaml`（任意） | [道筋層](../concepts/route-layer.md)の設定（ρ の既定値・分類ごとの倍率など）。無いジャンルは道筋層自体が存在しない（実行設定にも05.道筋の区画は出ない） |
| `motives.yaml`（任意、route.yaml とセット） | 道筋層の動機表。「理由なし」の寄り道に理由を与える規則の並び |

すべて YAML で、述語文字列（`when`・`condition`）は `engine/predicate.py` のホワイトリスト評価器（`ast` ベース、`eval` は使わない）で読みます。同じ評価器を修飾ルール・伏線の回収条件・結末条件（`world.yaml` の `ending.when`）・`phase_rules` の4か所で共有します。

### action_graph.yaml

`templates/momotaro_plus2/action_graph.yaml` は `nodes:`（行動一覧）・`edges:`（行動間の前提条件）・`permission:`（対象の役割ごとの許可レベル）・`restricted_weight:`（`restricted` の重み係数）の4区画で構成されます。

```yaml
nodes:
  - {verb: train, category: I, subtype: self_strengthen, risk: neutral, sign: 0}
  - {verb: fight, category: I, subtype: weaken_direct, risk: risky, sign: -1}

permission:
  fight: {hostile: allow, neutral: restricted, ally: restricted}

restricted_weight: 0.15
```

`category` は QD 格子の行（I 自己強化・II 認識と伏線・III 関係構築・IV 身分・V 移動と停滞・VI 外部介入）。`sign` は行動の方向性（+1 協調的・-1 敵対的・0 中立）で novelty・品質 q の計算に使います。

分類は文脈単位で決まります。`nodes:` の同じ動詞でも `when` 条件が成立するかどうかで別の行に分類されます。例えば `investigate` は `when: gather` が成立すれば I（自己強化）、それ以外は II（認識と伏線）。`move` は `crossing`（身分の境界越え）が成立すれば V（移動と停滞）、それ以外は `category: null`（QD格子の対象外）です。

`permission:` は行動の対象の役割（`hostile`/`neutral`/`ally`）ごとに `allow`/`restricted`/`deny` を定義します。`restricted` は候補から除外されるのではなく、`restricted_weight`（既定 0.15）倍に重みが下がった状態で候補に残ります。

### canon.yaml

```yaml
weight: 1.0
entries:
  - ctx:
      phase: []
      hostile_present: false
      objective: hostile
      vitality: alive
      stance: hostile
    act: {category: III, verb: give_item, role: neutral}
    n: 6
```

`n` は文脈 `ctx` で行動 `act` を取った擬似観測件数。ラプラス平滑化で `p(act|ctx) = (n(ctx,act)+1) / (n(ctx)+|A_ctx|)` として前例表に混ぜ込まれます（`weight` は canon 全体の重み `w_canon`）。

### effects.yaml

```yaml
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
```

`mode: auto` は条件が揃ったら自動発火（行為の自然な帰結）、`mode: chosen` は「回収する」行動が候補として立ち、遺伝子に従って選ばれる（時機の選択自体が探索対象）。`chosen` を回収しないまま物語が終わると品質 q が −0.05/件 減点されます。

### qd.yaml

```yaml
categories: [I, II, III, IV, V, VI]
volatility_bins: [low, mid, high]
```

同梱ジャンル（`templates/` 配下: `basic`・`detective`・`momotaro`・`momotaro_plus`・`momotaro_plus2`・`romance`）のうち、`momotaro`（桃太郎系）は行動カテゴリを I〜VI 全6種、`detective`（探偵）・`romance`（恋愛）は I〜III の3種だけを軸にしています（後者はテンプレートの `action_graph.yaml` 自体が扱う行動の幅が狭いため）。`volatility_bins` はどのジャンルも共通で `[low, mid, high]`。閾値は世代0の母集団の分散から自動算出され、`archive.json` の `volatility_thresholds` に記録されます。

### rules.yaml

```yaml
- id: hostile_lean
  scope: candidate
  when: "stance(self, target) < -0.3"
  adjust:
    category_weight.I: 0.2
    risk_tolerance: 0.1
  description: 敵対相手にはより攻撃的に
```

`scope` は `candidate`（候補ごと。`target` が束縛される）か `turn`（ターンごと）。`adjust` は `category_weight.<I〜VI>`・`risk_tolerance`・`stance_shift_bias`・`novelty_drive` を一時的に加算するキー。`g_eff = clip(genome + Σ 該当ルールの adjust)` として実際の重みに使われます。

### rationality.yaml（κ／Jev）

```yaml
kappa: 0.0
method: choice
backend:
  type: ollama
  model: qwen3.6:35b
  base_url: http://localhost:11434
  timeout: 300
key_items: [きびだんご, 小判]
```

κ はそのジャンルが持つ合理性判定（Jev）の強さで、実行設定側で 0〜1 の値を上書きできます（詳しくは[実行設定](../usage/run-settings.md)）。このファイルが無いジャンルは κ=0 相当（判定を使わない）で動きます。

### route.yaml（ρ／道筋層）

```yaml
holder_belief_fact: treasure_thief
trial_reveal_facts:
  鬼の弟: 弟の消息

rho: 0.0
multipliers:
  advance: 1.0
  prepare: 0.5
  detour:body: 1.0
  detour:belief: 1.0
  detour:ignorance: 0.5
  detour:none: 0.02
  lost: 1.0

min_win_prob: 0.2

gene_affinity: 0.5
route_category:
  fight: I
  negotiate: III
```

| キー | 意味 |
|---|---|
| `holder_belief_fact` | 目的物の持ち主について、本人が誤って信じうる事実のID（[道筋層](../concepts/route-layer.md)の「思い込み」判定に使う） |
| `trial_reveal_facts` | 「この試練の依頼主は、対応する事実を知るまでプランナーの立ち寄り先として現れない」というマッピング（依頼主ID→事実ID） |
| `rho` | ρ の既定値。実行設定側の05.道筋で上書きできる。既定 0.0（無変調） |
| `multipliers` | 分類ごとの基礎倍率 b（`advance`/`prepare`/`detour:body`/`detour:belief`/`detour:ignorance`/`detour:none`/`lost`）。省略したキーは既定値のまま |
| `min_win_prob` | 持ち主への対決・妨害・無力化を「勝ち目がある挑戦」とみなす、本人が信じる最低勝率。これを下回ると寄り道〈理由なし〉（動機表次第でさらに再分類）扱いになる |
| `gene_affinity` | 目的物の入手手段が複数ある局面（対決 vs 交渉など）で、どちらを最善の計画とみなすかを遺伝子でどれだけ傾けるか（0〜1、既定 0＝常にコスト最小の方） |
| `route_category` | `gene_affinity` が参照する、経路名（`fight`/`negotiate` など）と行動カテゴリ（I〜VI）の対応 |

このファイルが無いジャンルには道筋層自体が存在しません（実行設定に05.道筋の区画も出ません）。

### motives.yaml（動機表）

```yaml
- id: care_for_ally
  label: 仲間を大事にする
  when: "stance(self, target) >= 0.6"
  verbs: [give_item, share_knowledge, persuade, pledge]
  gene: category_weight.III
  text: "{target}との絆を深めたくて{verb_text}"
```

`route.yaml` があるジャンルにだけ置ける、任意のファイルです。上から見て最初に条件（`when`）と対象の行動（`verbs`）が一致した1件が採用されます。

| キー | 意味 |
|---|---|
| `id` | 動機の一意なID |
| `label` | 画面のバッジに出す短い名前 |
| `when` | 述語文字列（`engine/predicate.py` の評価器）。`target` は候補の対象（無い候補では自分自身）に束縛される |
| `verbs` | この動機が対象にできる動詞の一覧 |
| `gene` | 動機の強さを読む遺伝子キー（`risk_tolerance`・`stance_shift_bias`・`novelty_drive`・`category_weight.<I〜VI>`）。先頭に `-` を付けると値を反転する（例: `-risk_tolerance` はリスク許容度が低いほど強い） |
| `text` | 理由文のテンプレート。`{target}`・`{verb_text}` などを差し込める |

動機表は「寄り道〈理由なし〉になるはずだった候補」だけに適用され、前進・準備・身体・思い込み・手探り・見通しなしの行動には一切影響しません。一致した動機の実際の重みは、`gene` で指定した遺伝子の強さ（0〜1）に応じて連続的に変わります（詳しくは[道筋層](../concepts/route-layer.md)）。

## `projects/<世界>/` の構成

| パス | 役割 |
|---|---|
| `world.yaml` | 舞台そのもの（地名・経路・登場人物の初期関係を除く世界共通の設定・結末条件） |
| `subjects/*.yaml` | 登場人物1人につき1ファイル（7層の初期状態） |

### world.yaml の主なキー

```yaml
name: 桃太郎＋2
time: {days: 20, slots: [朝, 昼, 夕方, 夜]}
protagonist: 桃太郎
antagonist: 鬼
zones: [{name: 村, note: ...}, ...]
routes:
  村: [{to: 道中}]
  海: [{to: 鬼ヶ島, cost: 4, requires_item: 船}]
gapengine:
  action_graph: templates/momotaro_plus2/action_graph.yaml
  effects: templates/momotaro_plus2/effects.yaml
items:
  - {name: 船, made_from: {木材: 2, 縄: 1}, requires: {knowledge: 造船術}, vehicle: true}
facts:
  - {id: oni_weakness, values: [金棒, 火, 塩], secrecy: 0.4, act_threshold: 0.6}
truth: {oni_weakness: 金棒, treasure_thief: 鬼}
ending:
  - id: homecoming
    when: {agent: 桃太郎, goal: attained}
    label: 鬼退治を果たし、宝を村へ持ち帰った
target_ending: [homecoming, homecoming_shared]
```

`ending.when` は述語文字列 or `{agent, goal}` の2形式。`target_ending` に列挙した結末に届いたランだけが[QD 格子](../concepts/qd-map.md)のアーカイブに入ります。`zones`/`routes` は7層の「フェーズ層」（`phase_rules` による越境判定の土台）、`items` は「資源層」（持ち物として `inventory` に入る）、`facts` は「認識層」（`knowledge`・`beliefs_about` の元になる秘匿事実）に対応する世界側のデータです（詳しくは[7層構造](../concepts/seven-layers.md)）。

### subjects/*.yaml の主なキー

```yaml
id: おじいさん
traits: {social: 0.55, stubbornness: 0.45, curiosity: 0.35, diligence: 0.75, temper: 0.30}
base: 10
modifiers: []
knowledge: []
inventory: {}
phase: []
verbs: [share_knowledge, give_item, rest]
identity: {"true": 山へ芝刈りに行くおじいさん, displayed: おじいさん}
goal: {target: null, deliver_to: null, obstacles: [], outcome: null}
relations:
  桃太郎: {affinity: 0.85, awareness: 1.0}
stamina: {max: 6, recover_per_slot: 0.5}
range: {zones: [村], entry: 村}
```

`traits`（固定気質。GA の対象外）以外はすべて7層の初期値です。`relations` は関係行列 `world.relations[a][b] = {affinity, awareness}` の初期値で、`stance(a,b) = affinity(a→b)` として述語評価器から読めます。
