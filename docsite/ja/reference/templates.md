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
  - "docs/2026-09-11_gapengine-detailed-design.md"
reviewed: "72aaeae8379271e99357be2f967fa0cff3901a8d"
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

すべて YAML で、述語文字列（`when`・`condition`）は `engine/predicate.py` のホワイトリスト評価器（`ast` ベース、`eval` は使わない）で読みます。同じ評価器を修飾ルール・伏線の回収条件・結末条件（`world.yaml` の `ending.when`）・`phase_rules` の4か所で共有します。

### action_graph.yaml

```yaml
- {verb: train, category: I, subtype: self_strengthen, risk: neutral, sign: 0}
- {verb: investigate, category: I, subtype: gather, risk: neutral, sign: 0, when: gather}
- {verb: fight, category: I, subtype: weaken_direct, risk: risky, sign: -1}
- {verb: give_item, category: III, subtype: gift, risk: neutral, sign: 1}
```

`category` は QD 格子の行（I 自己強化・II 認識と伏線・III 関係構築・IV 身分・V 移動と停滞・VI 外部介入）。`sign` は行動の方向性（+1 協調的・-1 敵対的・0 中立）で novelty・品質 q の計算に使います。

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

同梱3ジャンルの違い: `momotaro`（桃太郎系）は行動カテゴリを I〜VI 全6種、`detective`（探偵）・`romance`（恋愛）は I〜III の3種だけを軸にしています（後者はテンプレートの `action_graph.yaml` 自体が扱う行動の幅が狭いため）。`volatility_bins` はどのジャンルも共通で `[low, mid, high]`。閾値は世代0の母集団の分散から自動算出され、`archive.json` の `volatility_thresholds` に記録されます。

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

`scope` は `candidate`（候補ごと。`target` が束縛される）か `turn`（ターンごと）。`adjust` は `category_weight.<I〜VI>` または `risk_tolerance` を一時的に加算するキー。`g_eff = clip(genome + Σ 該当ルールの adjust)` として実際の重みに使われます。

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

`ending.when` は述語文字列 or `{agent, goal}` の2形式。`target_ending` に列挙した結末に届いたランだけが[QD 格子](../concepts/qd-map.md)のアーカイブに入ります。`zones`/`routes`/`items`/`facts` はそれぞれ7層の「対象層」「資源層」「認識層」に対応する世界側のデータです（詳しくは[7層構造](../concepts/seven-layers.md)）。

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
