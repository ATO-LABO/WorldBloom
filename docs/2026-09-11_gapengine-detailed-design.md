# GapEngine 詳細設計（WorldBloom = StorySim の要素を切り取って再構築する GA 物語エンジン）

作成: 2026-09-11 / 設計役 Fable（メイン会話）。Opus の初期レビューと Fable サブエージェントの独立レビュー（実コード・1000シード実測に基づく）を統合し、ユーザー判断（同日）を反映した **改訂版（v2）** / ステータス: **ユーザー承認済み・実装着手可**

正本の構想: Notion「WorldBloom(StorySim×GA)」ページID `3d8e21ef1cac800293b9c7b109d9df8f`（以下「構想」）。本書は構想を機構に落とす詳細設計であり、構想と本書が食い違う場合は **§18 に理由が書かれているものだけ** が意図的な変更である。

リポジトリ: `https://github.com/ATO-LABO/WorldBloom`（private）。ローカルは `G:\マイドライブ\Projects\WorldBloom_v2`。参照元コード: StorySim `G:\マイドライブ\Projects\StorySim`（**変更しない**。読むだけ）。

---

## 0. 一言でいうと

WorldBloom は StorySim の延長ではなく**別プロジェクト**。StorySim から必要な要素（ゾーンと移動、関係行列、アイテム／事実／信念、決定論の作法）を切り取り、**7層構造を第一級の状態モデル**として持つエンジンを WorldBloom_v2 に再構築する。主人公（共進化時は敵役も）に **Policy（戦略ベクトル＝遺伝子、9 スカラー）** を持たせ、行動抽選の直前で候補重みを変調する。GapEngine はシミュレーションと QD 評価を**包む外側ループ**で、各個体を固定シード集合で走らせ、**固定結末に到達したランだけ**を MAP-Elites アーカイブ（主導カテゴリ × volatility）に入れる。`novelty_drive` は「正典プライア＋前世代アーカイブ＋自己履歴」から成る**前例表**に対する個体内の駆動力として実装し、選抜側の QD とは別の機構として両立させる。

---

## 1. 設計原則

1. **構想が正本**。7層構造・戦略ベクトル・QD グリッド・3原則（対象限定を外す／stance 連続値／所有移転と勝敗の分離）は機構として実現する。既存実装に合わせて構想を削る判断はしない。
2. **StorySim は参照元であって土台ではない**。要素は「設計と実装を持ってくる／設計だけ参考にして書き直す／持ってこない」に仕分ける（§15.2）。StorySim 本体には触らない。
3. **状態は7層に1箇所で保持**。`Subject` が7層を文字どおり持ち、動詞はそれを直接読み書きし、ログはその差分。射影も翻訳レイヤーも作らない。
4. **個体評価は純関数**: `(genome, seed, world, precedent_{g−1}, engine hash)` → 物語。同じ入力なら `layers.jsonl` がバイト一致。乱数は sim 用と GA 用を分離し、Policy は乱数を消費しない。
5. **中立遺伝子は無変調**: 全カテゴリ等重み・risk 0.5・bias 0・novelty 0 のとき `policy=None` のランとバイト一致（回帰の錨）。
6. **工数より実現**（ユーザー指示）。段階分けは検証順序のためであり、スコープ削減ではない。

---

## 2. 全体構造

```
[構想の入力]
  設定(お題) ──→ World simulation ──→ 7層初期状態 + 固定結末(target_ending) ──┐
  harness設計時の人間設定:                                                     │
    テンプレート(行動グラフ・修飾ルール・正典プライア・QD軸・permission・伏線ライブラリ) ├─→ GapEngine
    共進化 ON/OFF                                                             │
                                                                              ┘
[GapEngine = 外側ループ]
  A ← {}（QDアーカイブ）; P_0 ← canon（正典プライア）
  for g in 1..G:
      pop ← g==1 ? 一様乱数 N 体 : 親プール(A ∪ 前世代の整形適応度上位)から交叉+変異で N 体（+10% 移民）
      for genome in pop（multiprocessing、各ランは純関数）:
          for seed in S（固定 K 個、全個体共通）:
              run(seed, 主人公.policy=genome, 敵役=固定 or B の標本, precedent=P_{g−1})
                  → layers.jsonl（7層差分＋決定イベント）
              reached, desc, q, shaped ← evaluate(layers, target_ending)
          模範ラン ← 到達ランのうち q 最大; desc ← 模範ランのマス; reach_rate ← |到達|/K
          if 到達ランあり: A.insert(cell(desc), genome, q, 模範ラン)   # 既存エリートより q が高ければ置換
      P_g ← build_precedent(A) ⊕ canon
  出力: A の各エリート → 場面抽出 → LLM あらすじ → 人間選定 → 本文化
```

- **Sifting は二役**: (a) GA ループ内の適応度・記述子の算出（`gapengine/qd.py`、**絶対スケール**）、(b) 出力段の場面抽出（あらすじ化する注目ターンの抽出。バッチ相対の分位でよい）。(a) に分位ランクを使ってはならない（世代を跨ぐと比較不能）。
- **計算量の見積**: StorySim 実測で 1 シード 0.27〜0.34 秒（7 人×16 日×4 スロット）。再構築版も同程度を目標とし、N=100・K=3・G=30 で約 9,000 ラン ≈ 単核 45 分、8 並列で数分。
- **結末到達率の参考値**: StorySim momotaro 1000 シードで `homecoming` 到達 11.2%、桃太郎死亡 39.9%。再構築版では vitality（§10.4）により死亡分の多くが「倒れる」に変わるため、世代 0 の到達率はこれより高くなる見込み（Phase 0 で実測）。

---

## 3. 遺伝子（Genome）と Policy 層

### 3.1 Genome（**9 スカラーで確定**）

```python
# gapengine/genome.py
@dataclass(frozen=True)
class Genome:
    category_weight: dict[str, float]   # {"I","II","III","IV","V","VI"} 各 [0.05, 1.0]
    risk_tolerance: float               # [0, 1]  中立 0.5
    stance_shift_bias: float            # [-1, 1] 中立 0
    novelty_drive: float                # [0, 1]  中立 0
```

- 下限 0.05: カテゴリを遺伝子で消させない（消すのはテンプレートの枝刈りの仕事。消せると GA が「II を 0 にして観測を捨てる」局所解に落ちる）。
- `Genome.neutral()` = 全カテゴリ 0.5・0.5・0・0。`Policy.reweight` は中立なら早期 return し、候補重みに触れない。
- 次元の追加は Phase 2 以降の実測で必要が示されたときのみ（アーカイブの互換性が切れるため、実験系列の区切りで行う）。2 層目（修飾ルール）は v1 ではテンプレート固定で遺伝子に含めない（§3.8）。

### 3.2 気質（personality）との関係 — **並置**

再構築版でも主体は固定の**気質**（`traits: {social, stubbornness, curiosity, diligence, temper}`）を持つ。これは「設定」に属し GA の対象外。理由: 気質は行動重みだけでなく、勝負のタイブレーク・信念更新の頑固さ・観測の進捗量・行動順に使われる（StorySim と同じ役割分担）。遺伝子にすると GA が能力チャネル（「stubbornness を上げれば勝てる」）を最適化し、構想の「進化させるのは方針だけ」を壊す。次元も対応しない（curiosity は I のアイテム獲得と II の観測に同時に効く）し、`novelty_drive` に対応物が無い。傍証: StorySim `docs/2026-07-29_greimas-surprisal-worldbuilding-design.md` §1（−log choice_prob を新規性指標にしたら人物固定の重みプロファイルの同語反復になった実測）。

**設計上の帰結**: 候補重みは `基礎重み(traits, 世界状態) × 方針乗数(genome, 文脈)` の二層。気質は「その人物らしさ」、遺伝子は「結末までの進み方」。

### 3.3 継ぎ目（policy hook）

行動選択 `Subject.choose_action` は、候補リスト `weighted: list[(Action, float)]` を組み立て、Policy があれば変調し、`rng.choices()` **1 回**で抽選する（StorySim の作法を継承。候補生成は乱数を消費せず決定論的なタイブレーク＝名前順）。

```python
weighted = self._candidates(world, others)            # rng 不消費
if self.policy is not None:
    weighted = self.policy.reweight(self, world, others, weighted)   # rng 不消費
i = rng.choices(range(len(weighted)), weights=[w for _, w in weighted], k=1)[0]
```

`reweight` は候補ごとに `分類 → 有効遺伝子(修飾ルール適用) → 4 乗数`:

```
w' = w × m_cat × m_risk × m_stance × m_nov
```

決定イベントに `policy: {category, subtype, target_role, m_cat, m_risk, m_stance, m_nov, p_prec, effective_genome, choice_prob}` を記録する（記述子はこれを読み、再分類しない）。

### 3.4 `category_weight` → 候補重みへの接続

- 各候補を `classify(action, subject, world) → (category, subtype, risk_class, stance_sign, target_role)` で分類（§7.1）。
- `m_cat = cw_eff[cat] / mean(cw_eff[active])`。`active` はテンプレートで有効なカテゴリ集合。**カテゴリ内の verb 数で割らない**（割ると語彙の多いカテゴリが不利になり GA が制御できない歪みが入る。verb 間の比率は基礎重みが担う）。
- 基礎重みは世界状態に依存し、実行可能な候補しか列挙されない。「行動は方針と世界状態から都度選ばれる」という構想の性質はそのまま保たれる。

### 3.5 `risk_tolerance`（判定は「信じている強さ」で行う）

- 候補に**リスク級**: `risky`（fight / confront / compete / steal_credit / sabotage / sacrifice / 敵対者のいるゾーンへの越境 move / **信じている強さで劣勢な相手への fight**）、`safe_under_threat`（敵対者同席時の rest / withdraw / guard / 停滞）、それ以外 `neutral`。
- `m_risk = r/0.5`（risky）、`(1−r)/0.5`（safe_under_threat）、1（neutral）。数的不利時の逃走加重も `(1−r)/0.5` でスケール。
- 劣勢判定は §6 の `believed_strength`。観測していない桃太郎は金棒を知らないので「80 vs 90 で優勢」と信じて挑み、真値 120 に負ける——認識層が物語を駆動する要になる。

### 3.6 `stance_shift_bias`（行動の符号＋対象選択）

- 関係を動かす候補に**符号**: `+1`（相手の stance を上げる: share_knowledge / give_item / persuade / grand_gesture / negotiate / concede）、`−1`（下げる: fight / steal_credit / confront / sabotage / mislead / neutralize）。`m_stance = 1 + b × sign`。b=0 で中立。
- 対象選択（§7.2）: 同一 verb の対象候補を同席者全員に列挙したうえで、各候補の役割プライア × `1 + b × sign × (−stance(self→target))`（bias>0 なら低 stance 相手への `+1` 行動＝懐柔が浮き、bias<0 なら高 stance 相手への `−1` 行動＝裏切りが浮く）。
- 「懐柔」「裏切り」「山分け」は行動タイプの追加ではなく、対象開放と原則②（stance は連続値）・原則③（§10.2 `negotiate/concede`）の組合せから自然発生する。

### 3.7 `novelty_drive` — 個体内の駆動力

**定義**: 「今の文脈で前例に多い行動ほど避ける」個体内の乗数。選抜側の QD（アーカイブの多様性）とは**別の機構**であり両立させる。QD は「残す物語の集合」を多様にし、novelty_drive は「一人の主人公が前例を外れる性格」を表す。

**「前例」の 3 層（すべて決定論的、乱数不消費）**:

| 層 | 内容 | 出所 |
|---|---|---|
| 正典プライア `P_canon` | ジャンルの「誰でも書く台本」を文脈→行動の擬似観測件数で記述した YAML（桃太郎: 仲間獲得→渡海→戦闘→持ち帰り） | `templates/<genre>/canon.yaml`、人間が書く。世代 0 からこれが前例＝構想の「人間が台本として書きそうにない組合せを残す」の直接実装 |
| アーカイブ前例 `P_archive(g−1)` | 前世代終了時の QD アーカイブの全エリートの模範ラン（主人公の決定イベント）から集計 | **世代開始時に凍結**。同世代の全個体が同じ表を参照（実行順・並列化に依存しない） |
| 自己履歴 `P_self` | 同一ラン内でその個体が既に選んだ決定（`Subject.decision_history: Counter`） | 単調反復（修行だけ）を抑える |

**キー**:
- `ctx` = `(phase 通過集合(sorted tuple), 敵対者同席: bool, 目的物の状態 ∈ {none, self, ally, hostile, other}, vitality, 主敵役への stance 区分 ∈ {hostile <−0.3, neutral, friendly >+0.3})`
- `act` = `(category, verb, target_role ∈ {self, ally, hostile, neutral, none})`

**確率**: ラプラス平滑化 `p(act|ctx) = (n(ctx,act)+1) / (n(ctx)+|A_ctx|)`。`A_ctx` は表で観測済みの act ∪ 今の候補集合（未観測候補は床値＝新規扱い）。`p_prec = λ·p_ref + (1−λ)·p_self`、`p_ref` は canon（擬似件数 × `w_canon`）と archive の件数和。λ 初期 0.7、`w_canon` 初期 1.0（archive が世代を重ねると相対的に薄まる）。未観測の文脈では全候補が同じ床値 → 乗数が全候補で等しく効果ゼロ。

**乗数**: `m_nov = (1 − p_prec + ε)^novelty_drive`（ε=0.02）。novelty_drive=0 で 1。前例で支配的な行動（p≈1）は強く抑えられ、稀な行動は相対的に浮く。

**再現性**: 表は `precedent.json` として世代ディレクトリに保存し、sim は読み取り専用で参照。GA 側乱数は `random.Random(ga_seed)` で sim の `random.Random(seed)` と分離。WB-WORLDGROW-001 段階5b「遺伝子の引き継ぎ」で `--seed-genomes` を指定すると、第0世代の先頭 seeded 個体分は `Genome.random(ga_rng, ...)` を呼ばず（乱数を消費しない）、残り枠のみ通常どおり `ga_rng` から生成する。乱数消費順が変わるのは seed_genomes 指定時のみで、未指定時は従来どおりバイト一致する。

**却下**: 「同世代の他個体」を前例にする案（逐次では未来参照、並列では順序依存で非決定）。「全評価個体」を前例にする案は E2 で「アーカイブのみ」と比較してから決める（既定はアーカイブのみ）。

### 3.8 階層化の 2 層目（修飾ルール）

v1 はテンプレート固定。述語＋説明の二層（構想「データフォーマット」§2）:

```yaml
# templates/momotaro/rules.yaml
- id: hostile_lean
  scope: candidate            # 候補ごと（target が束縛される）
  when: "stance(self, target) < -0.3"
  adjust: {category_weight.I: +0.2, risk_tolerance: +0.1}
  description: "敵対相手にはより攻撃的に"
- id: after_crossing
  scope: turn
  when: "'越境' in phase"
  adjust: {risk_tolerance: +0.2}
  description: "戸口を越えた後は大胆になる"
```

`g_eff = clip(genome + Σ adjust of matching rules)`。述語評価器 `engine/predicate.py` は `ast` ホワイトリスト（Compare / BoolOp / UnaryOp / Name / Attribute / Constant / Subscript / 限定 Call）で `eval` を使わず、**修飾ルール・伏線 payoffCondition・ending.when・phase_rules の 4 用途で共有**する。規則に `id` を必須にし、将来のメタ進化は「規則ごとの有効ビット」を遺伝子に足すだけで済むようにする。

### 3.9 交叉・突然変異・親選択

- 交叉: 次元ごと一様（親のどちらかを継ぐ）を既定、BLX-α ブレンドと E4 で比較。親はアーカイブから**異なるマス**を優先して 2 体（同マス確率 ≤ 0.2）。
- 突然変異: 次元ごと確率 0.3 でガウス揺らぎ σ=0.1（category_weight）/ 0.1（risk）/ 0.2（bias）/ 0.1（novelty）、範囲にクリップ。
- 親プール: `A ∪ 前世代の整形適応度上位 25%`（アーカイブ側を 3:1 で重く）。毎世代 10% は一様乱数の移民。
- 子のマスは親のマスを継承せず、実行結果で決まる（構想どおり）。

---

## 4. 7層構造 — 第一級の状態モデル

再構築なので、7層は射影ではなく**そのまま実行時の状態**。`engine/subject.py`:

```python
@dataclass
class Subject:
    id: str
    traits: dict[str, float]                       # 固定気質（§3.2）。GA 対象外
    policy: Policy | None                          # 戦略ベクトル（§3）
    # 1 能力層
    base: float                                    # 真の基礎値（修行↑・弱体化直接↓）
    modifiers: list[Modifier]                      # {id, source, value, kind∈{item,fact,ally,loyal,…}, visible, active}
    # 2 認識層（相手ごと）
    beliefs_about: dict[str, BeliefAbout]          # {target: {known_modifiers:set, base_estimate, identity_seen}}
    beliefs: dict[str, Belief]                     # valued fact の信念（値＋確信度。StorySim QUALITY-020 の設計を移植）
    knowledge: set[str]                            # boolean fact
    # 3 資源層
    inventory: dict[str, int]                      # assets
    reputation: float
    #   bonds は世界の関係行列の逆向き読み（§4.1）。Subject には持たない
    # 4 フェーズ層
    phase: set[str]                                # 通過した閾値 id（不可逆）
    verbs: set[str]                                # 素の使用可能動詞。availableActions は派生（§10.1）
    # 5 身分層
    identity_true: str
    identity_displayed: str
    # 6 対象層は世界側（world.objectives）。Subject は goal で参照
    goal: Goal                                     # {target(item), deliver_to, obstacle_ids, outcome}
    # 7 遅延効果層は世界側（world.pending_effects）。planted_by で参照
    # vitality
    vitality: str                                  # alive | downed | revived | dead
    downed_since: int | None
    # 内部
    stamina: float; stress: float
    decision_history: Counter                      # novelty の自己履歴
    zone: str
```

### 4.1 関係行列（stance / bonds / awareness）
`world.relations[a][b] = {affinity ∈ [−1,1], awareness ∈ [0,1]}`（StorySim から移植）。**stance(a,b) = affinity(a→b)**（a が b をどう見るか）、**bonds(a) = Σ_b affinity(b→a)** の正の部分（a が引き出せる関係資本）。同じ行列の双方向読みで、資源層 bonds と関係 stance を二重に持たない。ログはフラット行 `{observer, target, affinity, awareness}`（構想「データフォーマット」§1）。

### 4.2 世界側の層
- `world.objectives: {item: {claimants: [...], holder: 派生}}`（対象層。claimants は `goal.target` にそのアイテムを持つ主体から初期化）。
- `world.pending_effects: list[{id, planted_by, planted_turn, target, condition, description, effect, mode, resolved}]`（遅延効果層、§8）。
- `world.facts` / `world.items` / `world.recipes` / `world.questions`（StorySim から移植）。

### 4.3 ログ契約 `layers.jsonl`
1 行 = 1 イベント。決定イベント（主体の行動）と派生イベント（ally_gained / exposure / payoff / downed / revive / ending …）と世界イベント（scheduled / daily）。各行に `turn`（=slot 連番）・`day`・`slot`・`subject`・`verb`・`args`・`result`・`delta`（7層の差分。ペア関係はフラット行として同じ行の配列に）・決定なら `policy`（§3.3）・`effective: bool`（その決定の前後で7層に非零の差分があったか）。ターン末には主人公の層ベクトル（§12.1）のスナップショット行を 1 本書く。分析は JSONL → Parquet（DuckDB）。

述語の名前空間（§3.8 の評価器が公開する関数）: `stance(a,b)`, `bonds(a)`, `phase`, `holds(a,item)`, `holder(item)`, `vitality(a)`, `known(a,fact)`, `knows_modifier(a,b,src)`, `strength(a)`, `believed_strength(a,b)`, `zone(a)`, `turn`, `hostile_present`, `present(a)`。同じ辞書を LLM のあらすじプロンプトにも渡す。

---

## 5. 能力層と勝負解決（差分ゲーム）

```
S(a) = base(a) + Σ_{m ∈ modifiers(a), m.active} m.value + ε·(0.65·stub + 0.20·soc + 0.15·cur)   # 真値
P(a が b に勝つ) = σ((S(a) − S(b)) / τ)      # ロジスティック。τ≈10（差 30 で ≈0.95）、E6 で調整
判定: rng.random() < P                        # 乱数消費 1 回
```

- ε は気質のタイブレーク係数（base-100 単位で最大 5 点程度）。
- **決定は「信じている強さ」（§6）、解決は「真の強さ」**。金棒 `visible: false` が機構として意味を持つ。
- 派生 modifier: 道具（`items[].effects.score_bonus`）、事実（`facts[].effects.score_bonus`）、同席の味方（`ally`: affinity が `companionship.threshold` を越えた相手が同席するとき、その相手の `ally_value` が乗る）。明示 modifier: 修行・弱体化・犠牲・忠誠（伏線の効果）。
- 弱体化(直接) = 相手の `base` を削る／`downed` にする。弱体化(間接) = 相手の modifier を `source` 指定で `active=false`（前提: `known_modifiers ∋ source`）。奪った modifier の付け替え（構想の拡張案）は `source` の所有者変更で書ける。
- 勝負の帰結（敗者）: 既定は `downed`（§10.4）。`dead` は勝者の行為・道具が `lethal` を持つときのみ `lethal_chance` で判定。

**桃太郎への写像**: 桃太郎 base 50、犬猿キジは同席時の ally modifier（+15/+15/+10）、鬼 base 80、金棒 modifier +40（`visible: false`, `lethal: true`）。上陸→観測→封じ→90 vs 80 の逆転が、構想の数値例そのままで再現できる。

---

## 6. 認識層（相手のパラメータについての信念）

- `beliefs_about[target] = {known_modifiers: set[str], base_estimate: float, identity_seen: bool}`。初期値は主体 YAML。未記載の相手は `world.default_strength_prior`。
- `believed_strength(o, t) = base_estimate + Σ_{m ∈ t.modifiers, m.id ∈ known_modifiers, m.active} m.value`。`visible: true` の modifier は同席時に自動で known に入る。`visible: false` は `observe` でのみ入る。
- `observe(target)`（II-1）: 同席する target の hidden modifier を 1 件ずつ known に加え、`base_estimate` を真値へ寄せる（進捗は curiosity 依存）。`identity_seen` も累積で true（§9）。
- `mislead(target_observer, about, value)`（II-4）: 相手の `beliefs_about[about].base_estimate` や valued fact の信念に偽値を注入。受け手の stubbornness ゲートと伝聞信頼割引（StorySim の `update_belief` の設計を移植）を通る。`isAccurate` は真値との比較で派生。
- 露見（II-5）: `observe` 成功や `confront` 正解で派生イベント `exposure`。

---

## 7. 行動タイプ I〜VI の実体化

### 7.1 分類器（Action 実体単位）

分類は**動詞名ではなく Action 実体（verb＋args＋文脈）**で行う（`share_knowledge` は事実/雑談、`investigate` は採取/観測、`give_item` は贈与/犠牲/交渉、`fight` は敵/味方で意味が変わる）。`gapengine/classify.py`。決定時に `policy.category` として記録し、記述子は再分類しない。

| 型 | 動詞（再構築版で実装するもの。※は StorySim から設計を移植） | 備考 |
|---|---|---|
| I-1 自己強化 | `train` | base 逓減上昇、ターン消費。越境前のみ等はテンプレート |
| I-2 仲間獲得 | 派生イベント `ally_gained`（affinity 閾値越え） | ally modifier 付与。行為は III-2 |
| I-3 アイテム獲得 | `investigate`※（採取）、`craft`※、`fight` 勝利の loot※ | |
| I-4 弱体化(直接) | `fight`※、`steal_credit`※、`sabotage` | sabotage は相手の base 減、前提 II-1 |
| I-5 弱体化(間接) | `neutralize(target, source)` | 前提 `known_modifiers ∋ source` |
| I-6 犠牲 | `sacrifice`、`give_item`※ で価値ある資産を非味方へ | 資産・bonds を代償に modifier や phase 通過 |
| II-1 観測・偵察 | `observe(target)`、`investigate`※（agent 型 source） | §6 |
| II-2 伏線設置 | `plant(effect_id)`、または既存 verb が自動的に plant になる | §8 |
| II-3 伏線回収 | `payoff(effect_id)` | `mode: chosen` の条件充足時のみ候補に立つ |
| II-4 ミスリード | `mislead` | §6 |
| II-5 露見・暴露 | `confront`※ 正解、派生イベント `exposure` | |
| II-6 転換・気づき | `rethink` | 新事実なしに信念を再走査。Phase 4 |
| III-1 試練の通過 | `trial(giver)` | 贈与者の条件をクリアすると grants_item/fact |
| III-2 説得・関係構築 | `share_knowledge`※、`give_item`※、`persuade` | persuade は事実を伴わない stance 上昇 |
| III-3 契約・誓約 | `pledge(target)` | phase フラグ＋reputation/bonds の担保。破りは禁止せず、reputation 大幅減 |
| III-4 グランドジェスチャー | `grand_gesture(target)` | 大きな資産/bonds を公開で消費、相手の「傷」（観測済み fact）に対応する条件付きで stance を大きく動かす |
| IV-1 変装 | `disguise(displayed)` | §9 |
| IV-2 偽主人公・横取り | `steal_credit`※、同じ目的物を claim する別主体 | |
| V-1 越境 | `move`※ が閾値ゾーン／`requires_item` 経路を越える | phase 付与＋availableActions 切替 |
| V-2 難題 | `craft`※・`investigate`※ の回数ゲート | 専用 verb は不要 |
| V-3 追跡・逃走 | `pursue_target`※、数的不利時の逃走 move | |
| V-4 停滞・内省 | `rest`※、`withdraw`※ | risk_tolerance でゲート |
| V-5 疑似的な死と再生 | `downed` → `revive`（自動）／`rescue`（味方の行為） | §10.4 |
| VI-1 外部介入 | `scheduled_events`※／`daily_events`※ | 主体は選ばない。記述子では VI に計上 |
| VI-2/3 報酬・還元 | `donate`、結末の種類で派生 | reputation 更新 |

行動グラフ `templates/<genre>/action_graph.yaml`（DOT へ書き出し可）: ノード＝行動タイプ、エッジ＝前提（v1 は 1 行動→1 前提の AND のみ）、ノード属性＝`genres`（枝刈りタグ）・`permission ∈ {allow, restricted(ε), deny}`（都合主義回避レベル、ジャンルごと）・`risk_class`・`stance_sign`。前提を満たさない行動は候補に**立たない**（因果の一貫性は構築的に保証）。

### 7.2 原則①「対象限定を外す」

再構築版では最初から対象を開放する（StorySim の「敵にしか fight できない」「乱数で 1 人」は持ってこない）:
- 関係・勝負系 verb は**同席者全員＋自分（train / sacrifice）を各 1 候補**として列挙（名前順、乱数不消費）。
- 各候補に**役割プライア**: 行動グラフの `permission`（allow 1.0 / restricted ε=0.15 初期 / deny 0）× 従来の適格者（敵への fight 等）は 1.0。
- **verb 総量の正規化**: 同一 verb の候補重み合計が「基礎重み × (1+open_bonus)」になるよう割る（同席者が多いだけで share_knowledge が支配するのを防ぐ）。
- 目的物の譲渡は `negotiate/concede`（§10.2）で可能（StorySim の「自分の goal.target は give しない」制約は持ってこない）。
- 対象開放は vitality（downed）と permission と**同時に**入る（味方への fight が即死にならない）。

---

## 8. 遅延効果層（伏線）— `auto` / `chosen` の併用（**確定**）

伏線には「設置」と「回収」の 2 つの瞬間がある。回収の引き金を誰が引くかを、伏線ライブラリの 1 件ごとに宣言する:

- **`mode: auto`** — 行為の自然な帰結。条件が揃ったスロットで自動発火する。例: きびだんごを渡した犬の忠誠（犬の反応であって桃太郎の選択ではない）。
- **`mode: chosen`** — 明かす時機に意味があるもの。条件が揃うと「回収する」が行動タイプ II-3 の候補として立ち、遺伝子に従って選ばれる。例: 偵察で知った「鬼の強さは金棒だけ」を、上陸直後に使うか、仲間が揃うまで温存するか、全滅寸前の最後の手として切るか——この時機の違いを GA が探索する。
- **未回収の減点**: `chosen` の伏線が回収されないまま物語が終わったら品質を −0.05/件（「撃たれない銃」を GA に淘汰させる）。

```yaml
# templates/momotaro/effects.yaml
- id: kibidango_loyalty
  plant: {verb: give_item, item: きびだんご, to_role: ally_candidate}   # 既存 verb の実行が設置になる
  payoff:
    condition: "stance(target, self) >= 0.5 and hostile_present"          # 構造化述語（内部判定）
    description: "仲間が忠誠を発揮する場面"                                 # 自然言語（LLM 用）
    effect: {modifier: {target: self, source: "$target", value: 15, kind: loyal}}
    mode: auto
- id: oni_weakness_known
  plant: {verb: observe, reveals: {target: 鬼, modifier: 金棒}}           # 観測が設置になる
  payoff:
    condition: "'越境' in phase and present(鬼)"
    description: "鬼の力の源が金棒だと知っている"
    effect: {enable_verb: {verb: neutralize, target: 鬼, source: 金棒}}    # 回収＝封じる行動の解放
    mode: chosen
```

`plant` 専用 verb はライブラリのうち `plant.verb: plant` のものを、前提充足の範囲で列挙する。どの伏線を仕込むかは II の category_weight と novelty で変調。

---

## 9. 身分層

- `identity_true / identity_displayed`。`disguise(displayed)` で乖離。`observe` の累積または `confront` で `identity_seen=true`（露見、派生イベント `exposure`）。
- `perceived_name(observer, target)` = `identity_seen` なら true、さもなくば displayed。関係行列・敵対判定・役割プライアはこの名前で引く。テンプレートは displayed 人格に対する初期関係を書ける（例: 鬼 → 「旅の商人」 affinity 0.0）。
- 看破時に stance を true 人格の値へ切替（露見の劇的効果）。

---

## 10. フェーズ層・対象層・資源層・vitality

### 10.1 フェーズ
`phase_rules: [{id: 越境, when: "zone(self) in {北の島,東の島,南の島}", enable: [neutralize, observe], disable: [train]}]`。通過は不可逆。`availableActions = verbs ∩ 行動グラフの前提充足集合 ∩ phase_rules の enable/disable`（派生）。

### 10.2 対象層と原則③（所有移転 ≠ 勝敗）
- `negotiate(holder)`（claimant 側）: `stance(holder→self) ≥ θ_n` または資産交換（`offer`）を条件に `concede` を holder の候補に立てる。`concede`（holder 側）: 目的物を譲渡。stance が低くても資産交換で成立しうる（取引）。
- `concede` は所有移転だけでなく**和解**でもある（WB-JEV-004）: 成立時に双方の `goal.obstacles` から互いの id を外し、双方向 affinity を +0.2（結果が 0.0 未満なら 0.0 まで引き上げ）する。これをしないと `target_role`/`fight` の敵対判定が stance と obstacles のいずれかで残り、譲渡直後に戦闘で奪い返される（設計意図に反する）。さらに、和解済みの相手には敵対（hostile）に戻らない限り fight を仕掛けない（`world.settled` で追跡）。permission が hostile→neutral で `allow`→`restricted` に落ちるだけでは、policy を持たない NPC は他候補が乏しいと正規化後にまだ fight を選びうるため、候補生成自体で除外する。
- 桃太郎の固定結末「宝を村へ」は鬼を倒さなくても譲渡で到達できる——望ましい（同じ結末・別ルート）。ending 述語が「鬼の敗北」を含意しないことをテンプレートで明示。

### 10.3 資源層
`sacrifice` / `grand_gesture` は bonds（相手→自分の affinity）や assets を消費。`reputation` は `donate`（＋）、`pledge` 破り（−）、`grand_gesture`（公開行為、±）で更新。VI-3「恩恵の還元」は `donate` を結末条件の一部に持つ ending で表現。

### 10.4 vitality（**downed → revive / rescue を初期コアに含める。死は lethal のみ。確定**）

```
alive ──(敗北, 非lethal)──→ downed ──(R スロット経過 or rescue)──→ revived ──(再度敗北)──→ downed …
alive/downed ──(lethal な行為・道具, lethal_chance)──→ dead
```

- `downed` 中の候補は `rest` のみ（そこに倒れている）。整形適応度（§11.2）は低く、到達しなければアーカイブに入らない——**単独では死亡とほぼ同じ**。意味を持つのは復帰があるから:
  - **自動復活** `revive`: `downed_since + R` スロットで `revived`（base −δ または stress 上昇）。同席の味方 1 人につき R を短縮。
  - **救出** `rescue`: 同席する味方の行動。味方の stance と bonds が効く（関係を育てていた個体だけが助かる）。
- これにより「倒れた桃太郎」の一部が結末へ向かえるようになり、「全滅寸前から逆転」（高 volatility マスの代表例、V-5）が機構として発生する。
- **死は残す**: `lethal: true` の道具・行為（金棒）でのみ `dead`（`lethal_chance`）。通常の敗北では倒れるだけ、致死的な相手には本当に死にうる、という二段構え。テンプレートは `lethal_exempt: [桃太郎]` で主人公を致死からも守れる（ジャンルの約束事＝世界設定であり、エンジンの結末誘導ではない）。
- 死者は `dead` として退場、他者の関係に `grief`（StorySim の設計を移植）。

---

## 11. 結末固定

### 11.1 判定（述語）
```yaml
ending:
  - id: homecoming
    when: "holds(桃太郎, 鬼ヶ島の宝物) and zone(桃太郎) == '村'"
    label: 鬼退治を果たし、宝を村へ持ち帰った
    gather_to: 村
  - id: reconciliation
    when: "stance(桃太郎, 鬼) >= 0.5 and stance(鬼, 桃太郎) >= 0.5 and holder(鬼ヶ島の宝物) == '村'"
target_ending: homecoming
```
恋愛（相互 stance ≥ θ）・探偵（`confront` 正解）も同じ仕組み。`{agent, goal: attained}` 形式は糖衣として受け付ける。

### 11.2 方式 = アーカイブ入場ゲート（棄却）＋整形適応度
- **アーカイブには `target_ending` に到達したランしか入らない**。世界側を結末へ拘束しない（都合主義の回避）。
- **整形適応度 `shaped`**（未到達ランにも与える。親プールの選択にのみ使い、アーカイブには漏れない）: 述語の充足した連言肢の割合＋正規化マージン（`holds` なら所持 0/1、`zone` なら経路長の近さ、`stance` なら閾値までの距離）。
- 早期終了: 到達で `_ended`、「到達不能確定」（主人公 dead かつ蘇生手段なし、目的物消失）で即打ち切り。
- 到達率はアーカイブのメタデータに保存し、同点時のタイブレークに使う。GA の選択圧が到達率を上げる——これが「結末固定」の実体。

---

## 12. Sifting = QD（記述子・品質・アーカイブ）

### 12.1 記述子
- **軸1 主導カテゴリ**: 主人公の決定イベントのうち `effective=true` のものだけを数え、`share[c]` の argmax（同点はテンプレートのカテゴリ順）。move の反復を雑音として除く。遺伝子の `category_weight` の argmax は**使わない**。
- **軸2 volatility**: 主人公のスロット t ごとの層ベクトル `v_t`（[0,1] 正規化: base/100、Σmodifiers/100、bonds 平均、主敵役への stance、assets 数/上限、reputation、|phase|/|閾値|、beliefs_about 変化 0/1、identity 乖離 0/1、目的物所持 {self 1, ally 0.66, other 0.33, hostile 0}、vitality 順序値/3）、`δ_t = ‖v_t − v_{t−1}‖₁`、`volatility = Var(δ_t)`。閾値は**世代 0 の母集団（300 ラン程度）の三分位で決めてアーカイブのメタデータに凍結**。`mean(δ)` も併記（E1）。
- グリッドは `templates/<genre>/qd.yaml` で軸と段階数を宣言（既定 6×3。ジャンルで縦軸のカテゴリを絞る）。

### 12.2 品質 q（絶対スケール、[0,1]）
`q = Σ_i w_i·clip(metric_i/scale_i, 0, 1) − penalties`
- 起伏: 目的物の所持者交代回数、目的達成度の反転回数、主人公の `|Δaffinity|` 総和、信念の変化回数、downed→復帰の回数、**真の差分 `S(主人公)−S(主敵役)` と信じている差分の符号反転回数**（差分ゲームの「逆転」）
- 因果: 前提違反は構築的に 0（assert）。「完成した前提連鎖の数」（observe→neutralize、pledge→…）を加点
- 減点: `result: invalid` 件数、同一 (verb, target) の 3 連続以上、`rest/withdraw` の比率、**未回収の `chosen` 伏線（−0.05/件）**
- scale は固定値（世代を跨いで比較可能）。

### 12.3 アーカイブ
```json
{"cell": ["III", "high"], "genome": {...}, "quality": 0.72, "descriptor": {...}, "reach_rate": 0.67,
 "exemplar": {"seed": 7, "layers": "runs/exp1/g12/ind-034/seed-7/layers.jsonl", "engine_hash": "…", "precedent_hash": "…"},
 "generation": 12, "parents": ["g11/ind-002", "g11/ind-057"]}
```
模範ランのログを同梱し、エンジンのハッシュを記録する（エンジンを変えると乱数列が変わり同じ genome が別の物語になるため、物語はログとして保存し、genome は「再現手順」ではなく「系譜」として扱う）。同一設定の再実行でアーカイブが完全一致すること（Phase 0 合格条件）。

---

## 13. 共進化（ON/OFF）

- OFF（既定）: 敵役は `policy=None`（気質駆動）。
- ON: 敵役アーカイブ B を別に持ち、世代ごとに交互評価。アーカイブは (主人公 genome, 敵役 genome, seed) の組を保存。
- **敵役の適応度はゼロサムにしない**: 「結末が到達されたラン」の中で主人公の道中を最も険しくした度合い（ターン数・volatility・逆転回数）を最大化（E7）。
- 出力は可変長ログなので Stepper 側の固定長問題は生じない。`max_days` はテンプレートで上げられる。

---

## 14. 出力段（あらすじ化 → 人間選定 → 本文化）

1. アーカイブの各エリートの模範ランから注目ターンを抽出（`effective` かつ `|δ_t|` 上位、派生イベント、伏線の設置・回収）。
2. `gapengine/synopsis.py` が述語名前空間（§4.3）と注目ターンから短いあらすじプロンプトを生成。LLM 呼び出しは Claude/Codex CLI・各社 API の切替（StorySim の上演層の設計を参考に書き直す）。
3. 人間があらすじ一覧（マス座標・q・到達率付き）から複数選定 → 選ばれたものだけ本文化。
4. ビューア: アーカイブ格子ビュー＋物語ログ表示を新規に作り、`.claude/launch.json` に登録して Dev-Launcher に載せる（Phase 3）。

---

## 15. リポジトリ配置と StorySim からの切り取り

### 15.1 配置
```
G:\マイドライブ\Projects\WorldBloom_v2\      ← git、origin = github.com/ATO-LABO/WorldBloom (private)
  README.md  CLAUDE.md  requirements.txt  .gitignore  .claude\launch.json（Phase 3）
  docs\2026-09-11_gapengine-detailed-design.md   ← 本書
  engine\      subject.py world.py relations.py verbs.py contest.py predicate.py vitality.py effects.py sim.py log.py
  gapengine\   genome.py policy.py classify.py precedent.py qd.py evolve.py synopsis.py
  templates\momotaro\   action_graph.yaml rules.yaml canon.yaml qd.yaml effects.yaml
  projects\momotaro\    world.yaml  subjects\*.yaml   （7層の初期状態）
  scripts\     evolve.py analyze.py（JSONL→Parquet/DuckDB）
  tests\       決定論・中立遺伝子・前提違反ゼロ・伏線減点・vitality 遷移
C:\Projects\WorldBloom-local\runs\<experiment>\g<N>\ind-<i>\seed-<s>\layers.jsonl   ← ラン出力（Drive に置かない）
```
依存: PyYAML のみ（engine/gapengine）。Parquet/DuckDB は `scripts/analyze.py` に閉じる。

### 15.2 StorySim からの仕分け

| 持ってくる（設計と実装） | 設計だけ参考にして書き直す | 持ってこない |
|---|---|---|
| ゾーン・経路・移動と `requires_item` 通行条件、`range` | 行動抽選（重み→1 回の `rng.choices`。policy を最初から組み込む） | 亀住・銭湯・赤穂などの世界と専用機構（affiliation_groups、slot_rules、endure_silence、resource/compete_for_resource 等） |
| 関係行列 affinity/awareness とその更新（repair_pressure 含む） | 勝負解決（→ §5 ロジスティック差分ゲーム） | 既存 sift の分位ランク（出力段で必要なら移植） |
| items / recipes / facts / valued-fact の信念（implies/refutes・伝聞割引・confront） | 結末判定（→ §11 述語） | StorySim-viewer（格子ビューは新規） |
| scheduled / daily events、`grants_item/fact` | ログ形式（events.jsonl → `layers.jsonl`） | Phase 3 上演のプロンプト実装（設計のみ参考） |
| 決定論の作法（単一 rng、決定的タイブレーク、`choice_prob` 記録） | 死・grief（→ vitality の一部） | |
| 回帰テストの構成（決定性・死亡後 absent・ミニパイプライン） | | |

### 15.3 git 運用
- 初期化: `WorldBloom_v2` で `git init` → `origin` を `ATO-LABO/WorldBloom` に設定 → 本書と README を最初のコミットに。ラン出力はリポジトリ外（`C:\Projects\WorldBloom-local`）。
- コミット・プッシュはグローバル CLAUDE.md の標準（ユーザー指示時に実装役が実行）。
- 実装の進め方は AB テスト方式（本書＝①設計 → ② Codex GPT-5.6 sol によるテキスト納品 → Claude が適用 → ③ Opus レビュー → Fable 最終確認）。

---

## 16. 実装フェーズと合格条件

| Phase | 成果物 | 合格条件 |
|---|---|---|
| **0 コアエンジン再構築＋GA×QD 配管** | `Subject`（7層）・関係行列・ゾーン/移動・items/facts/知識・動詞（move / investigate / observe / share_knowledge / give_item / fight / train / rest / withdraw / pursue）・ロジスティック勝負・**vitality（downed→revive/rescue、lethal のみ死）**・ending 述語・`layers.jsonl`・Genome/Policy/4 乗数・分類器・novelty（canon＋自己履歴）・`qd.py`・MAP-Elites ループ・`precedent.json`・multiprocessing・桃太郎テンプレートと 7層初期状態・tests | ① 同一入力で `layers.jsonl` がバイト一致 ② 中立遺伝子のランが `policy=None` とバイト一致 ③ 桃太郎で N=100・G=20 が 30 分以内 ④ ≥4 マスが埋まり、世代を追って到達率が上がる ⑤ 同一設定の再実行でアーカイブが完全一致 ⑥ **同じ結末に対し QD アーカイブが同数の無作為シードより多様なルート（行動系列の編集距離）を残す** ⑦ downed から復帰して結末に到達するランが存在する |
| **1 認識層の完成・弱体化・三原則** | `mislead`、`neutralize`、`sabotage`、`sacrifice`、前提グラフ（action_graph.yaml）と permission、対象開放と verb 総量正規化、`negotiate/concede`、`pledge`、`persuade`、ally modifier | ①「観測→間接弱体化→逆転」がエリートに ≥1 体、事前定義なしで出る ② 前提違反 0（assert）③ 同じ結末に対し fight 経由と negotiate 経由の両方のエリートが存在 ④ 裏切り（pledge 後の fight）・懐柔（低 stance への give_item→ally）が出現し、あらすじで識別できる |
| **2 遅延効果・身分・儀礼** | `plant/payoff`（auto/chosen）と未回収減点、`disguise`＋`perceived_name`＋`exposure`、`grand_gesture`、`trial`、`donate`/reputation、phase_rules、修飾ルール（rules.yaml） | ① `chosen` 伏線の回収時機が個体間で異なる ② 未回収伏線を持つ個体がアーカイブで淘汰される ③ 変装→露見のあるランが高 volatility マスに現れる |
| **3 共進化と出力段** | 敵役アーカイブ、非ゼロサム適応度、あらすじ生成、人間選定リスト、本文化、格子ビューア（Dev-Launcher 登録） | ① 共進化 ON/OFF 両方で到達エリートが得られる ② あらすじ→選定→本文の 1 周が回る |
| **4 他ジャンルとメタ進化の準備** | 恋愛/探偵テンプレート、`rethink`、規則有効ビットの遺伝子化（必要なら） | 恋愛テンプレートで相互 stance の ending に到達するアーカイブが得られる |

最初に動かすのは Phase 0。ここで測る第一の問いは「同じ結末に対し、QD アーカイブは無作為シードより多様なルートを残すか」。

---

## 17. 実験計画（未解決論点はここで決める）

| 論点 | 実験 |
|---|---|
| volatility 閾値、mean(δ) との相関 | **E1**: 無作為 genome 300 体 × 3 シードで δ の分布を採り、三分位で凍結 |
| 前例の文脈粒度、λ、アーカイブのみ vs 全到達ラン | **E2**: novelty_drive ∈ {0, 0.5, 1.0} × 粒度 {粗, 中} × 前例源でアーカイブ充填率と行動系列の編集距離を比較 |
| 縦軸の集計（effective 比率 vs 生件数） | **E3**: 両方で分類し、マスの占有バランスとあらすじ 10 本の目視で選ぶ |
| 交叉方式（一様 vs BLX-α） | **E4**: 同一設定で G=20 の充填率・品質を比較 |
| K（個体あたりシード数） | **E5**: K∈{1,3,5} でアーカイブの世代間安定性を比較 |
| τ・base のスケール・ε・R（復活までのスロット数） | **E6**: 勝率カーブが構想の数値例（50 vs 120 → ほぼ負け、90 vs 80 → 優勢）に合うよう調整。R は「倒れてから復帰して到達する」ランが出る範囲で最短に |
| 共進化の敵役適応度 | **E7**（Phase 3）: ゼロサム vs 非ゼロサムで到達エリート数を比較 |
| risk_tolerance / stance_shift_bias の寄与 | **E8**: 各次元を中立固定するアブレーション |
| stance の懐柔・裏切り閾値 θ、grand_gesture の代償量、未回収減点の大きさ | **E9**（Phase 1〜2）: 実ログ分布から決める |

構想の保留に同意する点: サブプロット差分トラックの統合、メタ進化の導入時期、QD 軸の追加。

---

## 18. 構想からの変更点

| # | 構想の記述 | 本書 | 理由 |
|---|---|---|---|
| 1 | `World simulation → GapEngine → Sifting → Stepper` | GapEngine は外側ループ。Sifting は「GA 内の適応度・記述子」と「出力段の場面抽出」の二役 | §2 |
| 2 | 能力層は base+modifiers の「既存」二層 | StorySim には強さのスカラーが無い。再構築で一級の状態として実装し、勝負解決はロジスティック差分ゲーム | §5 |
| 3 | 結末を先に固定 | アーカイブ入場ゲート＋整形適応度。世界側を拘束しない | §11 |
| 4 | novelty_drive「前例と違う行動を好む」 | 前例＝正典＋前世代アーカイブ＋自己履歴の 3 層。個体内乗数 | §3.7 |
| 5 | 行動タイプ→verb の写像 | Action 実体（verb＋引数＋文脈）単位で分類 | §7.1 |
| 6 | 資源層 bonds と stance が別物 | 同一の関係行列の双方向読み | §4.1 |
| 7 | 伏線回収＝条件充足ターンに発火する処理 | 伏線ごとに `auto`（自動発火）/ `chosen`（II-3 の候補として個体が選ぶ）。未回収は減点 | §8 |
| 8 | 主人公の死（記述なし） | 通常の敗北は `downed`（復帰あり）、死は lethal のみ | §10.4 |
| 9 | QD 縦軸＝主導カテゴリ | effective な決定だけで集計 | §12.1 |
| 10 | volatility 閾値は回しながら調整 | 世代 0 の分位で決めて以後凍結 | §12.1 |
| 11 | 遺伝子と気質の関係（記述なし） | 並置。気質は設定側の固定値 | §3.2 |
| 12 | StorySim の harness に挟み込む | StorySim の要素を切り取り、別プロジェクトとして再構築 | §15 |

---

## 19. 決定事項（2026-09-11、ユーザー判断）

1. 伏線回収: `auto` / `chosen` の併用、`chosen` の未回収は減点 — **確定**
2. vitality: 通常の敗北は `downed`（`revive`/`rescue` で復帰）、死は `lethal` のみ、初期コアに含める — **確定**
3. StorySim 本体は変更しない。WorldBloom は別プロジェクトとして `ATO-LABO/WorldBloom` に構築 — **確定**
4. 遺伝子は 9 スカラー — **確定**
5. 配置は §15（要素を切り取って WorldBloom_v2 に再構築） — **確定**

次の一手: Phase 0 の実装計画（ファイル単位の仕様と Codex への納品依頼プロンプト）を作成し、リポジトリを初期化する。

## 20. Phase 4 で確定した設計判断と追加の未解決論点（2026-09-12 追記、設計役 Fable。経緯は Phase 4 計画書 §6〜§10。設計書 §16 の全フェーズ完了）

### 20.1 確定した設計判断
- **整形適応度の二系統**: `deliver` を持つ結末は従来式（0.4×所持＋0.3×距離＋0.3×到達）、それ以外は結末述語の連言肢平均（`holds`/`known`/`knows_modifier`/`confront_success` は 0/1、`zone` は最短 hop、`stance ≥ θ` は `(stance+1)/(θ+1)`）。複数 target は最大値。統一は実験系列の区切りで行う（§3.1 と同じ扱い）。
- **真相のシード抽選**: `truth.<fact>.candidates` を `random.Random(f"{seed}:{fact_id}")` で抽選（主 rng 不消費）、header に記録。`known_by: ["$truth"]` は抽選結果の主体にのみ confidence 1.0 の直接信念を与える。証拠の `implies/refutes.value` には `$truth` / `$innocent:1` / `$innocent:2`（候補の名前順から真相を除いた 1・2 番目）を許し、bind 時に解決する。ここでの「名前順」は **Python の文字列比較＝Unicode コードポイント順**で、日本語の直観的な並び（甲・乙・丙）とは一致しない（実際は 丙 < 乙 < 甲）。テンプレートは、この順序が物語上の役割（第一の容疑者・第二の容疑者）に対応している前提を置かないこと。
- **rethink（II-6）**: 候補は「verbs に含む・implies/refutes を持つ証拠を 2 件以上保持・新事実なしの停滞 N スロット」。効果は保持証拠が触れる valued 信念を再導出（refutes→implies、confidence 降順、fact id の順）、保護は真相所有者の直接知（confidence 1.0）のみ。quality には `rethink` で値が反転した fact 数を `dramatic_turns` として計上。
- **`rule_bits`（メタ進化の準備）**: `meta_evolution: false` では空で 9 スカラーと byte 互換。ON で規則 id ごとに一様交叉・反転 0.1、Policy は無効規則を無視。
- **`affinity_cap`（能力→関係の層間作用）**: 能力層の active な modifier が、所有者を observer とする affinity の上昇を `max(cap, 現在値)` で止める（初期 bind は clamp、`affinity_cap_targets` で対象を限定、複数は最小）。ally modifier（関係→能力）の逆向き。`neutralize` や伏線の payoff で解除。cap を持たない世界では不変。
- **ジャンル枝刈りと QD 縦軸**: `action_graph.genre` と `nodes[].genres` の交差で候補を枝刈り。恋愛・探偵の縦軸は `[I, II, III]`（IV は恋愛では valued fact が無く `confront` が立たない、探偵では変装・潜入が存在しない）。`move` は越境でない限り `category: null`（§12.1）。
- **恋愛**: A→B は雑談で早期に飽和し、物語は「B が心を開く過程」。B の `防衛`（cap 0.35）と C の噂（B 側の cap 0.5、C を observe した auto 伏線で解除）の両方の解除が `mutual` の必要条件。C の `reputation:rumor` は露見後も残る。
- **探偵**: mislead の permission は stance 役割ベース（全容疑者 hostile → allow）。真犯人の自白は `share_min_affinity: 0.6` で「心を開かせたときだけ」。

### 20.2 未解決の論点（追加）
- `_mislead_candidates` が全主体で `world.truth` を参照し真値を候補から除外する（Phase 1 由来）。無実の主体も真犯人を決して指さない漏洩。修正は桃太郎の golden を変えるため系列境界で。
- 裏切り判定が verb 固定（neutralize/fight/…）で、テンプレートの `stance_sign` を見ない。恋愛の `neutralize`（sign +1）が誓約後に裏切り扱いになる。分類器の `stance_sign` ベースへ。
- `confront_success` 型の結末は shaped に勾配が無い（0/1）。マージン（真相値への確信度）の導入を検討。
- cap 専用 modifier（value 0）は関係層の制約を能力層に間借りしている。増えるなら関係層側 `caps` を検討。
- 探偵の能力層（証拠力 base・アリバイ modifier）が `solved` に効かない。「アリバイを崩さないと confront が通らない」等の結合を検討。
- 恋愛で A→B が雑談で飽和する（affinity 増分 vs θ のスケール）。関係層の抵抗の設計。
- NPC には Policy が無い（`--coevolve` の敵役のみ）。rules.yaml は主人公専用。NPC 既定 Policy の導入は保留。
- `_delivery_ending_score` の到達判定が個別 ending ではなく target 全体（deliver 結末が複数ある世界での混線）。
- `rethink` は「真相所有者以外の非派生な直接知」も破棄する。自白や伝聞で真相を教わった探偵が、手元の証拠が足りないまま rethink すると誤った結論に戻りうる（`share_min_affinity` で残した「心を開かせれば漏れる」経路と衝突する）。保護の範囲を「証拠で否定されない直接知」まで広げるか検討。
- 真相所有者の保護判定は実装では `world.fact_owners` のメンバシップで行っている（設計上の記述は「confidence 1.0・`derived=False`」）。`apply_evidence` が `reinforced` になると `derived` が True に化けるため、実装の述語の方が堅牢だが、保護範囲は `known_by`/`secret_of` の保有者全般に広がる。どちらを正とするか確定させる。
- 消去（`refutes`）は残った候補の確信度を押し上げない（結論の確信度は反証側の confidence に等しい）。「2 人を消したから残りが確実」という推論の強さを表現できていない。
