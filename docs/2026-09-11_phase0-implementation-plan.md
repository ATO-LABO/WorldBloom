# Phase 0 実装計画: コアエンジン再構築＋GA×QD 配管

作成: 2026-09-11（設計役 Fable）/ 親設計: `docs/2026-09-11_gapengine-detailed-design.md`（以下「設計書」）/ ステータス: 実装指示（納品 D1 = engine、D2 = gapengine の2回に分ける）

## 0. Phase 0 の狙いと範囲

新しい物語機構を最小限に抑えたまま、①7層を第一級に持つエンジン、②Policy（遺伝子）の継ぎ目、③QD アーカイブの外側ループ、④vitality（倒れる→起き上がる）を通しで動かし、「同じ結末に対し QD アーカイブは無作為シードより多様なルートを残すか」を実測できる状態にする。

**Phase 0 に含めるもの**: `Subject`（7層すべてのフィールド。Phase 0 で使わない層＝身分・遅延効果も型とログは持つ）、ゾーン/経路/移動、関係行列、items/recipes/boolean facts、動詞 13 種（§3.6）、ロジスティック勝負、vitality（downed/revive/rescue/dead）、ending 述語、`layers.jsonl`、Genome/Policy/4 乗数、分類器、前例表（canon＋自己履歴。アーカイブ前例は evolve が世代ごとに生成）、QD（記述子・品質・アーカイブ）、進化ループ、桃太郎テンプレート、tests。

**Phase 0 で意図的に落とすもの**（Phase 1 以降）: valued fact の信念（implies/refutes/伝聞割引/confront）、`mislead`、`neutralize/sabotage/sacrifice/negotiate/concede/pledge/persuade/plant/payoff/disguise/grand_gesture/trial/donate/rethink`、行動グラフの前提エッジと permission、対象開放の verb 総量正規化、修飾ルールの適用（ファイルは空で置く）、共進化、あらすじ化。StorySim の where 型の問い（鬼ヶ島がどの島か）も落とし、鬼ヶ島は 1 つの固定ゾーンにする。

**StorySim からの参照**（読むだけ。パスは `G:\マイドライブ\Projects\StorySim\`）: `engine/world.py`（`reachable_paths`・経路探索・items/recipes 解析）、`engine/agent.py`（`choose_action` の重み式・`change_relation`・`leadership`）、`engine/verbs.py`（`investigate` の採取・`craft`・`give_item`・`share_knowledge`・`fight` の帰結）、`engine/sim.py`（`run` の日/スロット構造・`_record_encounters`・体力回復）、`projects/momotaro/world/momotaro.yaml`・`agents/momotaro/*.yaml`（桃太郎の数値）、`tests/test_regression.py`（テストの流儀）。**設計だけ参考にし、コードはコピーしない**（構造が違う）。

---

## 1. リポジトリ構成（Phase 0 で作るファイル）

```
engine/__init__.py
engine/predicate.py      述語評価器（ast ホワイトリスト）
engine/subject.py        Subject（7層）・Modifier・BeliefAbout・Goal・from_yaml・layer_vector
engine/world.py          World（zones/routes/items/recipes/facts/objectives/thresholds/ending/events）・経路探索
engine/relations.py      関係行列（stance/bonds/awareness、更新、スナップショット）
engine/contest.py        ロジスティック勝負（strength / believed_strength / resolve）
engine/vitality.py       vitality 遷移（down / revive / rescue / kill / grief）
engine/actions.py        Action dataclass・候補生成（choose_action の候補列挙。乱数不消費）
engine/verbs.py          動詞の実行（VerbEngine.execute → イベント列）
engine/log.py            layers.jsonl の書き出し（イベント行・スナップショット行・差分計算・effective 判定）
engine/sim.py            Simulation（日/スロットのループ、行動順、イベント、ending、run）
gapengine/__init__.py
gapengine/genome.py      Genome（9 スカラー）・neutral/random/crossover/mutate/clip/serialize
gapengine/classify.py    classify(action, subject, world) → Classification
gapengine/precedent.py   PrecedentTable（canon/archive/self）・ctx_key/act_key・p_prec・save/load
gapengine/policy.py      Policy.reweight（4 乗数）・修飾ルール適用（Phase 0 は空リストを受けるだけ）
gapengine/qd.py          descriptor / quality / shaped / Archive
gapengine/evolve.py      進化ループ（run_individual は純関数、multiprocessing）
scripts/evolve.py        CLI
scripts/random_baseline.py  無作為シード（policy=None）との多様性比較（合格条件⑥）
templates/momotaro/action_graph.yaml  verb→カテゴリ/risk/sign（Phase 0 は前提エッジ無し）
templates/momotaro/canon.yaml         正典プライア
templates/momotaro/qd.yaml            QD 軸定義
templates/momotaro/rules.yaml         [] （Phase 2 で使う）
templates/momotaro/effects.yaml       [] （Phase 2 で使う）
projects/momotaro/world.yaml
projects/momotaro/subjects/{01_ojiisan,02_obaasan,03_momotaro,04_inu,05_saru,06_kiji,07_oni}.yaml
tests/test_engine.py     決定論・vitality・ending・前提（requires_item/recipe）・layers.jsonl 形式
tests/test_gapengine.py  中立遺伝子の無変調・Genome 演算の範囲・classify 表・precedent の p・Archive 決定論
```

依存は PyYAML のみ。Python 3.11+（実機は 3.13）。型ヒント必須、`from __future__ import annotations`。

---

## 2. 述語評価器 `engine/predicate.py`

```python
def compile_predicate(src: str) -> Predicate     # 構文検証は読み込み時（不正なら ValueError）
class Predicate:
    def evaluate(self, ns: Namespace) -> bool
```
- `ast.parse(src, mode="eval")` → 許可ノードのみ: `Expression, BoolOp(And/Or), UnaryOp(Not), Compare(Eq/NotEq/Lt/LtE/Gt/GtE/In/NotIn), Name, Constant(str/int/float/bool), Call(Name 関数のみ、位置引数のみ), Subscript/Attribute は不許可, Set/Tuple/List リテラル（要素は Constant）`。それ以外は `ValueError`。`eval` は使わない——自前の再帰評価。
- 名前空間 `Namespace`（dict-like）に関数を登録: `stance(a,b)`, `bonds(a)`, `awareness(a,b)`, `holds(a,item)`, `holder(item)`（所持者 id、誰も持たなければ None、`deliver` 済みならゾーン名）, `zone(a)`, `present(a)`, `vitality(a)`, `known(a,fact)`, `knows_modifier(a,b,src)`, `strength(a)`, `believed_strength(a,b)`, `hostile_present(a)`, `turn`, `day`, 変数 `phase`（評価対象主体の phase 集合、`'越境' in phase` 用）, `self`（評価対象主体 id）。
- `when: {agent: X, goal: attained}` の糖衣は World 側で `"holds(X, <goal.target>) and zone(X) == '<deliver_to>'"` に展開する。

---

## 3. エンジン

### 3.1 世界定義 `projects/momotaro/world.yaml`（形式）

```yaml
name: 桃太郎
time: {days: 16, slots: [朝, 昼, 夕方, 夜]}
protagonist: 桃太郎                    # 記述子・スナップショットの対象
antagonist: 鬼                         # 層ベクトルの stance 軸の相手
zones:
  - {name: 村,  note: "..."}
  - {name: 道中, note: "..."}
  - {name: 森,  note: "..."}
  - {name: 海,  note: "..."}
  - {name: 鬼ヶ島, note: "..."}
routes:                                # 無向ではなく有向。cost は体力コスト（既定 1.0）
  村:   [{to: 道中}]
  道中: [{to: 村}, {to: 森}, {to: 海}]
  森:   [{to: 道中}]
  海:   [{to: 道中}, {to: 鬼ヶ島, cost: 4, requires_item: 船}]
  鬼ヶ島: [{to: 海, cost: 4, requires_item: 船}]
movement: {action_weight: 2.5, hop_decay: 0.6, destination_weights: {村: 1.0, 道中: 2.2, 森: 1.5, 海: 1.2, 鬼ヶ島: 0.8}}
stamina: {default_max: 10, default_recover_per_slot: 1.0, exhausted_ratio: 0.2}
companionship: {threshold: 0.6, weight: 2.0}
awareness_per_encounter: 0.15
pulls: {pursue: 1.5, pursue_from_day: 4, deliver: 3.0, gather: 2.0, craft: 3.0}
thresholds:                            # フェーズ層の閾値。通過は不可逆。Phase 0 は phase 集合への追加だけ
  - {id: 出発, when: "zone(self) != '村'"}
  - {id: 越境, when: "zone(self) == '鬼ヶ島'"}
items:
  - {name: きびだんご, give: {receiver_affinity: 0.5, giver_affinity: 0.1}}
  - {name: 木材, sources: [{type: investigate, zone: 森, count: 1, max: 2}]}
  - {name: 縄,   sources: [{type: investigate, zone: 村, count: 2, max: 2}]}
  - {name: 鬼ヶ島の宝物, lootable: true, objective: true}
  - {name: 金棒, lootable: true, modifier: {value: 40, visible: false, lethal: true, lethal_chance: 0.5}}
  - {name: 船, made_from: {木材: 2, 縄: 1}, requires: {knowledge: 造船術}, craft_zone: 海, lootable: true,
     vehicle: true, access: {mode: owner, allow: [companions]}}
facts:                                 # boolean fact のみ
  - {id: 造船術, label: 船の組み方を知っている, secrecy: 0.2, share_min_affinity: 0.3}
default_strength_prior: 50
contest: {tau: 10.0, epsilon: 5.0}
vitality: {revive_after: 4, ally_speedup: 1, revive_base_penalty: 2, lethal_exempt: []}
grief: {stress: 1.0, affinity_to_killer: -0.6}
ending:
  - {id: homecoming, when: "holds(桃太郎, 鬼ヶ島の宝物) and zone(桃太郎) == '村'", label: 鬼退治を果たし、宝を村へ持ち帰った}
target_ending: homecoming
scheduled_events:
  - {id: departure_day, day: 1, slot: 朝, targets: [桃太郎], label: 旅立ちの朝, grants_item: {name: 縄, count: 1}, move_to: 道中, stress_delta: 0.3}
daily_events:                          # 任意。VI-1 外部介入。無ければ乱数を消費しない
  chance: 0.35
  events: [{id: steep_path, label: 険しい山道, weight: 2, stress_delta: 0.5}, ...]
```

- `objectives` は `items[].objective: true` から導出（claimants = そのアイテムを `goal.target` に持つ主体）。
- 検証: 未知ゾーン・未知アイテム・未知 fact・`made_from` の循環・述語の構文は読み込み時に `ValueError`。

### 3.2 主体定義 `projects/momotaro/subjects/*.yaml`（形式）

```yaml
id: 桃太郎
traits: {social: 0.65, stubbornness: 0.55, curiosity: 0.55, diligence: 0.70, temper: 0.70}
base: 50
modifiers: []                          # 明示 modifier（{id, source, value, kind, visible}）。派生は実行時
beliefs_about: {鬼: {base_estimate: 80}}
knowledge: [造船術]
inventory: {きびだんご: 3}
reputation: 0.0
verbs: [move, investigate, observe, share_knowledge, give_item, craft, fight, train, rescue, rest, withdraw]
identity: {true: 桃から生まれた者, displayed: 桃太郎}
goal: {target: 鬼ヶ島の宝物, deliver_to: 村, obstacles: [鬼]}
relations: {おじいさん: {affinity: 0.85, awareness: 1.0}, 鬼: {affinity: -0.5, awareness: 0.6}}
stamina: {max: 14, recover_per_slot: 1.5}
range: {zones: [村, 道中, 森, 海, 鬼ヶ島], entry: 村, exclude: [{zones: [村], until_item: 鬼ヶ島の宝物}]}
companions: [桃太郎, 猿, 犬, キジ]
ally_value: 15                         # 同席の味方としてこの主体が与える派生 modifier の値
```
- 鬼: `base: 80`, `inventory: {鬼ヶ島の宝物: 1, 金棒: 1}`, `verbs: [guard, fight, rest]`, `range: {zones: [鬼ヶ島], entry: 鬼ヶ島}`, `goal: {target: 鬼ヶ島の宝物, obstacles: [桃太郎]}`, `beliefs_about: {桃太郎: {base_estimate: 50}}`。
- 犬/猿/キジ: `base: 30`, `ally_value: 15/15/10`, `verbs: [move, investigate, share_knowledge, give_item, fight, rescue, rest, withdraw]`, `goal: {target: 鬼ヶ島の宝物, obstacles: [鬼]}`（桃太郎に同行して奪う側の claimant にはしない: `objective_claimant: false`）。
- おじいさん/おばあさん: `base: 10`, 村から出ない（`range: {zones: [村]}`）、`verbs: [share_knowledge, give_item, rest]`。
- 数値は StorySim の `agents/momotaro/*.yaml` を参考にする（traits・relations・stamina）。

### 3.3 `engine/subject.py`

```python
@dataclass
class Modifier: id: str; source: str; value: float; kind: str; visible: bool = True; active: bool = True; lethal: bool = False; lethal_chance: float = 0.0
@dataclass
class BeliefAbout: known_modifiers: set[str]; base_estimate: float; identity_seen: bool = False; observe_progress: float = 0.0
@dataclass
class Goal: target: str | None; deliver_to: str | None; obstacles: list[str]; outcome: str | None = None
@dataclass
class Subject:
    id, traits, base, modifiers(list[Modifier]), beliefs_about(dict), knowledge(set), inventory(dict), reputation,
    phase(set[str]), verbs(set[str]), identity_true, identity_displayed, goal, stamina, stamina_max, stamina_recover,
    exhausted(bool), stress(float 0..10), vitality(str), downed_since(int|None), zone(str), range_zones(set), range_exclude(list),
    companions(list|None), ally_value(float), decision_history(Counter), gather_progress(dict), gathered(dict), policy(Any|None)
    @classmethod from_yaml(path) -> Subject
    def derived_modifiers(self, world, present: list[Subject]) -> list[Modifier]   # items の modifier + 同席味方の ally
    def all_modifiers(self, world, present) -> list[Modifier]                       # 明示 + 派生
    def layer_snapshot(self, world, present) -> dict                                # 7層の辞書（ログ・差分・effective 判定用）
```
- `layer_snapshot` の形: `{"ability": {"base", "modifiers": [{id, value, active, visible}]}, "belief": {target: {known_modifiers: sorted, base_estimate, identity_seen}}, "resources": {"assets": {item: n}, "reputation", "bonds"}, "phase": sorted, "identity": {"true","displayed"}, "objective": {item: holder}, "pending": [], "vitality", "zone", "stress", "stamina"}`。`bonds` は関係行列から（§3.4）。決定論のため辞書はキー順ソートで出力。
- 同席味方の ally modifier: `present` のうち `affinity(peer→self) ≥ companionship.threshold` かつ `peer.vitality in {alive, revived}` の peer ごとに `Modifier(id=f"ally:{peer.id}", source=peer.id, value=peer.ally_value, kind="ally")`。

### 3.4 `engine/relations.py`

`Relations` は `{a: {b: {"affinity": float, "awareness": float}}}` を保持。`stance(a,b)`（無ければ 0.0）、`bonds(a) = Σ_b max(0, affinity(b→a))`、`change(a, b, affinity=0, awareness=0) -> delta dict`（clamp [−1,1] / [0,1]、丸め 4 桁）、`snapshot()`（ソート済み）、`flat_rows()`（`{observer, target, affinity, awareness}` の配列）。初期化は各 Subject YAML の `relations` から。

### 3.5 `engine/contest.py`

```python
def strength(subject, world, present) -> float          # base + Σ active modifiers + ε·(0.65·stub+0.20·soc+0.15·cur)
def believed_strength(observer, target, world, present) -> float
    # target.beliefs_about 不要。observer.beliefs_about[target.id]（無ければ base_estimate=world.default_strength_prior）
    # = base_estimate + Σ_{m in target.all_modifiers(present): (m.visible or m.id in known_modifiers) and m.active} m.value
    #   ※ visible な modifier は同席していれば見えている扱い（known に入れなくても加算）
def resolve(actor, rival, world, present, rng) -> tuple[winner, loser, p_actor, roll]
    # p = 1/(1+exp(-(S(actor)-S(rival))/tau)); roll = rng.random(); actor wins iff roll < p
```

### 3.6 動詞 `engine/verbs.py` と候補生成 `engine/actions.py`

`Action(verb: str, args: tuple, meta: dict)`（`meta` に分類のヒント: `target`, `dest`, `item`, `crossing`（閾値ゾーンへの移動）, `outmatched`（信じている強さで劣勢）等）。候補生成 `candidates(subject, world, sim) -> list[tuple[Action, float]]` は**乱数を消費しない**。`downed` の主体は `[(Action("rest"), 1.0)]` のみ。`dead` は行動しない。

| verb | 候補の条件と基礎重み | 効果 |
|---|---|---|
| `move(dest)` | 到達可能ゾーン（`reachable`: range 内、`exclude` 未解除のゾーン除外、`requires_item` は所持者か `access.allow: companions` の同行者のみ通行、体力コスト以内）**ごとに 1 候補**。重み `= action_weight × w(dest) / Σw` （`w(dest) = destination_weights[dest] × hop_decay^(hops−1) × pull(dest)`）。exhausted なら ×0.3。`pull`: 目的物の所持者がいるゾーンへ `pulls.pursue`（`day ≥ pursue_from_day`、自分が持っていないとき）、`deliver_to` へ `pulls.deliver`（目的物を所持中）、採取可能な材料の source ゾーンへ `pulls.gather`（レシピ材料が不足）、`craft_zone` へ `pulls.craft`（材料・知識が揃ったとき）、companionship 閾値以上の相手のゾーンへ `companionship.weight`（名前順で最初の 1 人） | ゾーン移動、体力 −cost。閾値 `when` を満たせば `phase` に追加（派生イベント `threshold_crossed`） |
| `rest` | 体力が有効なら常に。重み `max(0.05, action_weight × 2 × (1 − stamina/max)^2)`、exhausted ×2、downed 中は唯一の候補 | 体力回復はスロット末に一律。rest は追加で `+recover_per_slot` |
| `investigate(zone)` | verbs に含む。重み `0.35 + curiosity + gather_bonus`（`gather_bonus` = このゾーンに不足材料の source があれば `+1.0 + diligence`） | 材料採取（`sources` の count/max、StorySim `_gather_from_investigate` と同じ規則）、fact の `sources`（zone/agent 型）の進捗 |
| `observe(target)` | 同席する target ごとに 1 候補。target に未知の hidden modifier があるか `identity_seen=False` のとき。重み `0.3 + curiosity × 0.6` | `observe_progress += 0.5 + curiosity × 0.5`; ≥1.0 で hidden modifier を 1 件（id 順）`known_modifiers` に追加、`base_estimate` を真値へ（一致させる）、`identity_seen=True`、progress を 0 に戻す。イベント `observe`（`details.revealed`） |
| `share_knowledge(target, fact_or_topic)` | 同席者ごとに: 相手が知らない fact を自分が知っていれば fact 候補（重み `(0.2+social)×(1−secrecy)`、`affinity(self→target) ≥ share_min_affinity` のみ）、それとは別に雑談候補（重み `0.2 + social`） | affinity self→target +0.12、target→self +0.06、awareness 両 +0.2。fact なら target.knowledge に追加（イベント `learn_fact`） |
| `give_item(target, item)` | 同席者ごと × 所持アイテム（目的物・レシピ成果物・乗り物は除く。材料は「相手が作り手（recipes.requires.knowledge を持つ）」のときだけ）。重み `0.25 + social`（材料を作り手へ渡す候補は `× 2`） | item を 1 個移動。affinity: item の `give` 指定（無ければ receiver +0.2 / giver +0.05）、awareness receiver +0.3 / giver +0.1。渡した後 `affinity(target→self) ≥ companionship.threshold` になった瞬間、派生イベント `ally_gained`（一度だけ） |
| `craft(item)` | レシピの材料・知識が揃い `craft_zone`（あれば）にいる。重み `1.0 + diligence` | 材料消費、成果物 +1。イベント `craft` |
| `fight(target)` | 同席する敵対者（`affinity(self→target) < −0.2` または `goal.obstacles` に含む）ごと。重み `(0.1 + stubbornness × 0.4) × (2 × temper)`。`meta.outmatched = believed_strength(self, target) < strength(self)` の否定 | `contest.resolve`。勝者: `lootable` を全て奪う、awareness +0.2。敗者: affinity→勝者 −0.3、stress +1.2、`vitality.down(loser)`。勝者の active modifier に `lethal` があり `rng.random() < lethal_chance` かつ敗者が `lethal_exempt` に無ければ `vitality.kill(loser)`（lethal の判定は down の後、必ず 1 回だけ乱数を消費——modifier が無ければ消費しない） |
| `train` | verbs に含む、敵対者が同席していない。重み `0.15 + diligence × 0.5` | `base += 4 × (1 − base/100)`（丸め 2 桁）、体力 −1。イベント `train` |
| `rescue(target)` | 同席する `downed` の相手で `affinity(self→target) ≥ 0.3`。重み `0.3 + social + affinity` | `vitality.revive(target, by=self)`。affinity target→self +0.2 |
| `withdraw` | verbs に含む。重み `0.08 + max(0, stress − 4) × 0.35`、敵対者同席時 `+ max(0, 0.5 − temper) × 0.6` | stress −0.5、そのスロットは他に何もしない |
| `guard` | 目的物を所持している。重み `0.25 + stubbornness` | 何も変えない（イベントのみ）。敵役の待機行動 |

ally modifier は「同席」の派生なので verb を持たない。`pursue` は独立 verb にせず move の pull で表現する。

### 3.7 `engine/vitality.py`

```python
def down(subject, world, turn) -> events      # alive/revived → downed, downed_since=turn, stress+1
def revive(subject, world, by: str|None) -> events   # downed → revived, base = max(1, base − revive_base_penalty)
def kill(subject, world, killer) -> events    # → dead, zone から退場, 所持品は殺した側へ（lootable のみ）, grief: dead に affinity ≥ companionship.threshold の生存者に stress+grief.stress, affinity→killer += grief.affinity_to_killer
def tick(subject, world, turn, present) -> events   # スロット末: downed かつ turn − downed_since ≥ revive_after − ally_speedup × (同席味方数) なら revive(by=None)
```
順序値: alive=3, revived=2, downed=1, dead=0（層ベクトル用）。

### 3.8 `engine/sim.py`

```python
class Simulation:
    def __init__(self, seed: int, world: World, subjects: dict[str, Subject], out_dir: Path, policies: dict[str, Policy] | None = None, precedent=None)
    def run(self) -> Path   # layers.jsonl のパス
```
- `rng = random.Random(seed)`。**乱数を消費する箇所はこの 5 つだけ**: daily event の抽選（設定があるとき）、`choose_action` の最終 `rng.choices`、`contest.resolve` の `rng.random()`、lethal 判定の `rng.random()`、（他に無い）。候補生成・Policy・移動先の列挙・行動順は乱数不消費。
- `turn` = 通しのスロット番号（1 始まり）。
- 1 日のループ: `scheduled_events`（`targets` に `grants_item`・`move_to`・`stress_delta`）→ 各スロット: (1) 同席の記録（同じゾーンの生存者ペアに awareness `+awareness_per_encounter`、イベント `encounter` は書かない——差分行に含める）(2) 行動順 = `leadership` 降順・id 昇順（`leadership = 同席の follower 数 + 0.5 × (social+curiosity+temper)/3`、follower = affinity(peer→self) ≥ threshold の同席者）(3) 各主体が 1 回 `choose_action` → `execute` → ログ (4) スロット末: `vitality.tick`、体力回復（`+recover_per_slot`、exhausted は `stamina ≥ max × exhausted_ratio` で解除）、閾値の再評価、`ending` 判定（`target_ending` を含むどれかが真なら `ending` イベントを書いて終了）(5) 日末: 主人公のスナップショット行。
- `choose_action(subject)`: `candidates()` → `policy.reweight()`（あれば）→ `rng.choices(range(n), weights, k=1)` → `choice_prob = w_i / Σw`。候補が空なら `rest`（乱数不消費、`choice_prob=None`）。
- 早期終了: `target_ending` に到達、または「主人公が dead」で `aborted` イベントを書いて終了。

### 3.9 `engine/log.py` — `layers.jsonl` の契約

1 行 1 JSON、`ensure_ascii=False`、キー順固定（`sort_keys=True`）。行の種類 `kind`:

- `"decision"`: `{kind, turn, day, slot, subject, verb, args, result, choice_prob, policy: {...}|null, classification: {category, subtype, risk_class, stance_sign, target_role}|null, effective: bool, delta: {actor: <layer diff>, targets: {id: <layer diff>}, relations: [{observer, target, affinity, awareness}], objective: {item: holder}|null}, details: {...}}`
  - `effective` = `delta.actor` または `delta.targets` または `delta.objective` が空でない。`layer diff` は `layer_snapshot` の前後比較で変化したキーだけ（ネストは葉まで）。
- `"event"`: 派生・世界イベント（`threshold_crossed`, `ally_gained`, `learn_fact`, `downed`, `revived`, `dead`, `grief`, `scheduled_event`, `daily_event`, `ending`, `aborted`）。同じ `delta` 形式。
- `"snapshot"`: 日末の主人公: `{kind, turn, day, subject, vector: [11 floats], layers: <layer_snapshot>, relations: [...全ペア]}`。`vector` の定義は §4.5。
- 先頭行 `"header"`: `{kind, seed, world, protagonist, antagonist, genome: dict|null, precedent_hash: str|null, engine_hash: str}`。`engine_hash` は `engine/*.py` の内容の sha256 先頭 12 桁。

---

## 4. GapEngine

### 4.1 `gapengine/genome.py`

`Genome`（frozen dataclass、設計書 §3.1）。`CATEGORIES = ("I","II","III","IV","V","VI")`。`neutral()`、`random(rng)`（各次元一様）、`crossover(a, b, rng)`（次元ごとに一様に親を選ぶ。category_weight は 6 次元それぞれ独立）、`mutate(g, rng, p=0.3, sigma={"cw":0.1,"risk":0.1,"bias":0.2,"novelty":0.1})`、`clip()`、`to_dict()/from_dict()`、`is_neutral()`（許容誤差 1e−9）。

### 4.2 `gapengine/classify.py`

`Classification(category: str|None, subtype: str, risk_class: str, stance_sign: int, target_role: str)`。`classify(action, subject, world, present, cfg)`。`cfg` は `templates/<genre>/action_graph.yaml`:

```yaml
nodes:
  - {verb: train,           category: I,   subtype: self_strengthen, risk: neutral, sign: 0}
  - {verb: craft,           category: I,   subtype: item_gain,       risk: neutral, sign: 0}
  - {verb: investigate,     category: I,   subtype: gather,          risk: neutral, sign: 0, when: gather}      # 採取が起きうるゾーン
  - {verb: investigate,     category: II,  subtype: scout,           risk: neutral, sign: 0}                    # それ以外
  - {verb: observe,         category: II,  subtype: observe,         risk: neutral, sign: 0}
  - {verb: fight,           category: I,   subtype: weaken_direct,   risk: risky,   sign: -1}
  - {verb: share_knowledge, category: III, subtype: persuade,        risk: neutral, sign: +1}
  - {verb: give_item,       category: III, subtype: gift,            risk: neutral, sign: +1}
  - {verb: rescue,          category: III, subtype: rescue,          risk: neutral, sign: +1}
  - {verb: move,            category: V,   subtype: crossing,        risk: risky,   sign: 0, when: crossing}    # 閾値ゾーンへ入る move
  - {verb: move,            category: null, subtype: move,           risk: neutral, sign: 0}
  - {verb: rest,            category: V,   subtype: stall,           risk: safe_under_threat, sign: 0}
  - {verb: withdraw,        category: V,   subtype: stall,           risk: safe_under_threat, sign: 0}
  - {verb: guard,           category: null, subtype: guard,          risk: neutral, sign: 0}
edges: []          # 前提エッジ（Phase 1 から）
```
- 同じ verb に複数ノードがあれば `when` 条件（`gather` / `crossing` / 無条件）を上から評価して最初に合うもの。
- `risk_class` の上書き: `fight` で `meta.outmatched` なら `risky`（そのまま）、`move` で行き先に敵対者がいれば `risky`、`rest/withdraw/guard` は敵対者が同席しているときだけ `safe_under_threat`、それ以外は `neutral`。
- `target_role`: `self` / `ally`（affinity(self→t) ≥ companionship.threshold）/ `hostile`（affinity < −0.2 または obstacles）/ `neutral` / `none`。

### 4.3 `gapengine/precedent.py`

```python
def ctx_key(subject, world, present) -> tuple   # (tuple(sorted(phase)), hostile_present, objective_state, vitality, stance_bucket)
def act_key(cls: Classification, action) -> tuple   # (category or "-", verb, target_role)
class PrecedentTable:
    counts: dict[ctx_key, Counter[act_key]]
    def add(ctx, act, n=1)
    def p(self, ctx, act, candidate_acts: set) -> float   # (n(ctx,act)+1)/(n(ctx)+|A|), A = 表の act ∪ candidate_acts
    def merge(other, weight=1.0) -> PrecedentTable
    def to_json()/from_json()   # キーは "|" 連結の文字列にして保存。sha256 を precedent_hash に
def load_canon(path) -> PrecedentTable   # templates/<genre>/canon.yaml
def from_runs(layer_files: list[Path], protagonist) -> PrecedentTable   # decision 行の classification と ctx（decision 行に ctx も記録する: policy.ctx）
```
`objective_state`: 目的物の holder が self/ally/hostile/other/none。`stance_bucket`: antagonist への stance が < −0.3 → "hostile"、> 0.3 → "friendly"、他 "neutral"。

`canon.yaml`（桃太郎の「誰でも書く台本」、擬似件数）:
```yaml
weight: 1.0
entries:
  - {ctx: {phase: [], hostile_present: false, objective: hostile, vitality: alive, stance: hostile}, act: {category: III, verb: give_item, role: neutral}, n: 6}
  - {ctx: {phase: [出発], hostile_present: false, objective: hostile, vitality: alive, stance: hostile}, act: {category: I, verb: craft, role: none}, n: 4}
  - {ctx: {phase: [出発, 越境], hostile_present: true, objective: hostile, vitality: alive, stance: hostile}, act: {category: I, verb: fight, role: hostile}, n: 8}
  - {ctx: {phase: [出発, 越境], hostile_present: false, objective: self, vitality: alive, stance: hostile}, act: {category: null, verb: move, role: none}, n: 6}
```

### 4.4 `gapengine/policy.py`

```python
class Policy:
    def __init__(self, genome, precedent: PrecedentTable | None, rules: list = (), lam=0.7, eps=0.02, self_table: PrecedentTable | None = None)
    def reweight(self, subject, world, present, weighted: list[tuple[Action, float]], cfg) -> list[tuple[Action, float]]
```
- `genome.is_neutral()` かつ rules 空なら **入力をそのまま返す**（同一オブジェクト）。
- 各候補: `cls = classify(...)`; `m_cat = cw[cls.category]/mean(cw[active])`（category None → 1）; `m_risk`; `m_stance = 1 + bias × sign`; `m_nov = (1 − p_prec + eps) ** novelty` （`p_prec = lam × p_ref + (1−lam) × p_self`、`p_ref` は与えられた前例表（canon⊕archive）、`p_self` は `subject.decision_history` から作った自己表。前例表が None なら `p_ref = p_self`）。
- 決定後（sim 側から呼ぶ）`Policy.record(subject, cls, action, ctx)` で `decision_history[(ctx, act)] += 1`。
- 各候補の `Action.meta["policy"]` に `{m_cat, m_risk, m_stance, m_nov, p_prec, ctx, effective_genome}` を書き、sim がログに転記する。`classification` も同様に `meta["classification"]`。

### 4.5 `gapengine/qd.py`

- `layer_vector(snapshot_layers, world_meta) -> list[float]`（11 次元、設計書 §12.1 の順）: `base/100`, `Σ active modifiers/100`, `bonds/(3×人数)` を [0,1] に clip, `(stance→antagonist + 1)/2`, `min(1, Σ assets/10)`, `clip(reputation)`, `|phase|/max(1,|thresholds|)`, `beliefs_about が前スナップショットから変化 0/1`, `identity 乖離 0/1`, `objective holder ∈ {self:1, ally:0.66, other:0.33, hostile:0, none:0.5}`, `vitality/3`。**sim 側の snapshot 行にこの vector を書く**（engine は `world_meta` を知っているので engine 側で計算し、qd.py は読むだけ）。
- `descriptor(rows, cfg) -> Descriptor(category: str, volatility: float, volatility_bin: str|None)`: 主人公の `decision` 行で `effective=True` かつ `classification.category` が None でないものの件数分布 → argmax（同点は `qd.yaml` の順）。volatility = 主人公 snapshot 行の `vector` の連続差分 L1 の**分散**（スナップショットは日末だが Phase 0 ではこれで測る。E1 で粒度を見直す）。bin は `thresholds` が与えられたときのみ。
- `quality(rows, world_meta) -> float`（[0,1]）: 5 指標を各 scale で clip して等重み平均 − 減点。指標: 目的物 holder の交代回数（scale 2）、主人公の |Δaffinity| 総和（scale 3）、`revived` 回数（scale 2）、`strength(主人公) − strength(敵役)` の符号反転回数（decision/event 行の `details.strength_diff` から。sim が主人公と敵役が**同席した**ターンに書く。scale 2）、主人公の `learn_fact`＋`observe` 件数（scale 3）。減点: 同一 `(verb, target)` の 3 連続以上 1 回につき 0.05、`rest/withdraw` の比率 × 0.3。
- `shaped(rows, ending_pred_src) -> float`: Phase 0 は簡易: `0.4 × holds(目的物) + 0.3 × (1 − 村までの最短 hop / 最大 hop) + 0.3 × (到達なら 1)`。
- `reached(rows, target_ending) -> bool`: `ending` 行の `id == target_ending`。
- `class Archive`: `cells: dict[(category, vbin), Elite]`、`Elite = {genome, quality, descriptor, reach_rate, exemplar: {seed, layers_path, engine_hash, precedent_hash}, generation, parents}`。`insert()` は同マスで `quality` が高いときだけ置換（同点は先着維持）。`freeze_thresholds(volatilities)` で三分位を計算して保持。`save(path)/load(path)`（JSON、キー順固定）。

### 4.6 `gapengine/evolve.py`

```python
def run_individual(job) -> dict   # job = {genome, seeds, world_path, subjects_dir, template_dir, precedent_json, out_dir, protagonist}. 純関数。各 seed で Simulation → layers.jsonl → reached/desc/q/shaped
def evolve(cfg) -> Archive
```
- 世代 0: N 体を `Genome.random(ga_rng)`。以降: 親プール = アーカイブ全体 ∪ 前世代の shaped 上位 25%（重み 3:1）から 2 体（マスが異なる組を優先: 同マスなら確率 0.8 で引き直し、最大 5 回）→ crossover → mutate。10% は random。
- 各世代: `precedent_g = canon ⊕ from_runs(前世代アーカイブの模範ラン)` を `g<N>/precedent.json` に保存し、全個体に渡す。
- 評価: `multiprocessing.Pool(processes)`、`imap` で順序を固定（結果は個体 index 順に処理）。世代 0 の全 volatility で `freeze_thresholds`（アーカイブ未凍結のとき 1 回だけ）。
- 出力: `runs/<exp>/g<N>/ind-<i>/seed-<s>/layers.jsonl`、`g<N>/population.json`、`g<N>/results.json`、`archive.json`（毎世代上書き）、`summary.json`（世代ごとの到達率・占有マス数・平均 q）。
- 決定論: `ga_rng = random.Random(ga_seed)`、Pool の結果を index 順で消費、Genome の辞書はキー順で serialize。

### 4.7 `scripts/evolve.py` / `scripts/random_baseline.py`

```
python scripts/evolve.py --project projects/momotaro --template templates/momotaro --out C:\Projects\WorldBloom-local\runs\exp1 --generations 20 --population 100 --seeds 3 --ga-seed 1 --processes 8
python scripts/random_baseline.py --project ... --out ... --seeds 300      # policy=None を 300 シード。到達率・descriptor 分布・行動系列の相異度を出力
```
多様性の比較指標（合格条件⑥）: 到達ランの主人公の「effective な決定の (category, verb, target_role) 系列」のペアワイズ正規化編集距離の平均。`scripts/random_baseline.py` と `evolve` の `summary.json` の両方に出す。

### 4.8 `templates/momotaro/qd.yaml`
```yaml
categories: [I, II, III, IV, V, VI]     # 縦軸の順（同点タイブレーク順）
volatility_bins: [low, mid, high]       # 三分位
```

---

## 5. テスト（`python -m unittest discover -s tests -v`）

`tests/test_engine.py`
1. **決定論**: 同じ (world, subjects, seed) で 2 回 run → `layers.jsonl` がバイト一致（header の engine_hash を含む）。
2. **vitality**: fight で敗者が `downed` になり、`revive_after` スロット後に `revived`（味方同席で早まる）。`lethal` 付き modifier の勝者が `lethal_chance=1.0` なら `dead`、`lethal_exempt` なら死なない。
3. **ending**: 桃太郎が宝を持って村に着いたスロットで `ending` 行が書かれ、以後の行が無い。
4. **前提**: 船を持たずに鬼ヶ島へ行く move 候補が立たない。材料が揃うまで craft 候補が立たない。
5. **ログ形式**: 全行が `kind` を持ち、decision 行は `effective` と `delta` を持つ。日末 snapshot 行の `vector` が 11 要素で [0,1]。

`tests/test_gapengine.py`
6. **中立遺伝子**: `Policy(Genome.neutral(), None)` を付けたランと `policies=None` のランで `layers.jsonl` が **`policy`/`classification` フィールドを除いて一致**（`header.genome` と各行の `policy`・`classification` を落として比較）。
7. **Genome 演算**: random/crossover/mutate の結果が範囲内、`is_neutral` の判定。
8. **classify 表**: 代表的な Action（train / fight to hostile / give_item to neutral / move crossing / rest with hostile）の分類が期待どおり。
9. **precedent**: 空表で全候補の p が等しい。件数を足すと p が上がる。JSON 往復でハッシュ一致。
10. **Archive 決定論**: N=6, G=2, K=1 の小さな evolve を 2 回走らせて `archive.json` がバイト一致。

---

## 6. 納品の分割と適用順

- **D1（engine）**: §2・§3 と `projects/momotaro/*`、`tests/test_engine.py`。合格: テスト 1〜5 が通る、`python -c "from engine.sim import ..."` で 3 シードを走らせて `layers.jsonl` が出る。
- **D2（gapengine）**: §4 と `templates/momotaro/*`、`scripts/*`、`tests/test_gapengine.py`。合格: テスト 6〜10 が通る、`scripts/evolve.py` が N=20・G=3・K=2 で完走し `archive.json` に ≥2 マス入る。
- D2 完了後に本番実験（N=100・G=20・K=3）と `random_baseline.py` を回し、設計書 §16 Phase 0 の合格条件 ③〜⑦ を判定する。
