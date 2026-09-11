# Phase 1 実装計画: 認識層の完成・弱体化・三原則

作成: 2026-09-11（設計役 Fable）/ 親設計: `docs/2026-09-11_gapengine-detailed-design.md` §16 Phase 1 / 前提: Phase 0（D1・D2）適用済み・合格 / ステータス: 実装指示（納品 D3 = engine 側、D4 = gapengine/テンプレート側）

## 0. 狙いと合格条件

「観測→間接弱体化→逆転」「懐柔」「裏切り」「譲渡による到達」が**事前定義なしに**アーカイブに現れることを狙う。合格条件（設計書 §16）:
1. 「observe → neutralize → 逆転（strength_diff の符号反転）」がエリートに ≥1 体。
2. 前提違反 0（候補に立たない。assert で検証）。
3. 同じ結末に対し fight 経由と negotiate 経由の両方のエリートが存在。
4. 裏切り（pledge 後の fight）・懐柔（低 stance への give_item → ally_gained）が出現し、ログで識別できる。

Phase 0 で確立した規約（乱数消費箇所の限定、ログ契約、決定論、中立遺伝子の無変調）はすべて維持する。乱数消費箇所を増やす場合は §5 に列挙する。

---

## 1. 認識層の完成（valued fact の信念・confront・mislead）

### 1.1 valued fact（StorySim QUALITY-020 の設計を移植）
`world.facts[]` に `values: [...]` を持つ fact を許す。主体は `beliefs: {fact_id: {value, confidence}}` を持つ（Phase 0 の `Subject.beliefs` は空 dict として存在済み）。
- 証拠（boolean fact）に `implies: {fact, value, confidence}` / `refutes: {fact, value, confidence}`。証拠を得た瞬間（investigate の source 到達・learn_fact・share_knowledge）に信念を更新: `implies` は `confidence = max(cur, c)`（別の value を持っていれば、新 c > cur × (1 + stubbornness) のときだけ置き換え）、`refutes` は当該 value の confidence を `cur × (1 − c)` に下げる。
- 伝聞（share_knowledge で信念を伝える）: 受け手の confidence は `c × (0.5 + 0.5 × affinity(受け手→話し手))` に割引（負の affinity なら 0.5 未満）。
- `confront(target, fact)`（II-5）: 自分の信念 `confidence ≥ act_threshold` の value が target を名指すとき候補に立つ。重み `0.15 + confidence × 1.2 + temper × 0.3`。真相と一致すれば `exposure`（target の stance→自分 −0.4、周囲の awareness +0.2）、外れれば `misjudged`（自分の reputation −0.1、target の affinity→自分 −0.3、信念の confidence を半減）。真相は world の `truth: {fact_id: value}`（Phase 1 は固定値。シード抽選は Phase 4）。

### 1.2 ミスリード `mislead(target, about, value)`（II-4）
- 候補: 同席する target ごと × 自分が `beliefs_about[about]` または valued fact の信念で偽の値を「作れる」対象（自分の真値と異なる値）。Phase 1 では 2 種: (a) `about = self` の `base_estimate` を真値より低く/高く見せる（`value = strength(self) × {0.6, 1.4}` の 2 候補）、(b) valued fact に偽 value を伝える。重み `0.1 + (1 − social) × 0.3 + curiosity × 0.2`。
- 効果: 受け手の `beliefs_about[about].base_estimate` を伝聞割引付きで更新（`new = cur + (value − cur) × (0.5 + 0.5 × affinity(受け手→話し手))`、受け手の stubbornness ゲート: `affinity < 0.2 × stubbornness` なら無視）。イベント `mislead`（`details.accurate: false`）。`observe` が成功すると真値に戻る（露見）。

### 1.3 `believed_strength` の利用範囲の拡大
Phase 0 は fight の `outmatched` 判定のみ。Phase 1 で `rest/withdraw` の `safe_under_threat` 判定と、敵役（policy=None）の fight 重みにも `believed_strength` を使う（敵役も騙される）。

---

## 2. 弱体化と犠牲

| verb | 候補条件 | 重み | 効果 |
|---|---|---|---|
| `neutralize(target, source)`（I-5） | 同席 target の active modifier のうち `known_modifiers ∋ source`（前提エッジ observe→neutralize） | `0.2 + curiosity × 0.4 + stubbornness × 0.3` | modifier `active=false`（`details.neutralized`）。`lootable` な道具由来なら自分の inventory へ移す（構想の拡張案「付け替え」。`modifier.source` の所有者変更） |
| `sabotage(target)`（I-4） | 同席 target、前提 observe 済み（`identity_seen` または known_modifiers 非空） | `0.1 + stubbornness × 0.3` | `target.base −= 6`、`target.stress += 1`、affinity target→self −0.4。露見: `awareness` +0.3 |
| `sacrifice(kind)`（I-6） | `kind ∈ {asset, bond}`。asset: 価値ある所持品（modifier 付き or 目的物以外の lootable）を 1 つ失う。bond: 最も affinity の高い味方の affinity→self を −0.5 | `0.05 + risk 由来（Policy が risky として扱う）` | 見返り: `base += 8`（asset）または `phase` に `決意` を追加＋`stress −3`（bond）。テンプレートで `sacrifice_rewards` を上書き可 |

---

## 3. 三原則の実装

### 3.1 行動グラフの前提エッジと permission（`templates/momotaro/action_graph.yaml`）
```yaml
edges:
  - {from: observe, to: neutralize, requires: known_modifier}   # 対象の modifier を known していること
  - {from: observe, to: sabotage,   requires: observed}          # identity_seen or known_modifiers 非空
  - {from: pledge,  to: betray,     requires: pledged}           # 分類用（betray は fight の subtype）
permission:                   # 都合主義回避レベル。verb ごと、対象役割ごと
  fight:      {hostile: allow, neutral: restricted, ally: restricted}
  neutralize: {hostile: allow, neutral: restricted, ally: deny}
  sabotage:   {hostile: allow, neutral: restricted, ally: restricted}
  give_item:  {hostile: allow, neutral: allow, ally: allow}
  share_knowledge: {hostile: allow, neutral: allow, ally: allow}
  mislead:    {hostile: allow, neutral: restricted, ally: restricted}
restricted_weight: 0.15
```
候補生成は `edges` の `requires` を満たさない候補を**列挙しない**（構築的保証）。`permission` は候補重みへの乗数（allow 1.0 / restricted `restricted_weight` / deny 0 → 列挙しない）。engine は `world.gapengine.action_graph`（パス）を読んで `World.permission(verb, role)` と `World.prerequisite_ok(verb, actor, target)` を提供する。テンプレートが無ければ全 allow・前提なし（Phase 0 と同じ挙動）。

### 3.2 対象開放と verb 総量の正規化
- fight / neutralize / sabotage / mislead / give_item / share_knowledge の対象を**同席者全員**に広げる（Phase 0 では fight は敵対者のみ）。
- 同一 verb の候補重みの合計を `基礎重み_single × (1 + open_bonus)`（`open_bonus` 既定 0.5）に正規化する。基礎重み_single は Phase 0 の単一候補の重み式。各候補の配分は `permission × role_prior`（従来の適格者＝敵対者への fight 等は 1.0、それ以外は 1.0 × permission）。

### 3.3 `negotiate` / `concede`（原則③）
- `negotiate(holder)`（claimant 側、III-2）: 目的物の holder が同席。重み `0.2 + social × 0.5`。効果: holder に `offer` を記録（`world.offers[(claimant, holder)] = {turn, assets: 自分の lootable 所持品}`）、affinity holder→claimant +0.05。
- `concede(claimant)`（holder 側）: `offers` に自分宛があり、`stance(holder→claimant) ≥ θ_n`（既定 0.3）**または** offer の assets に holder が持たない modifier 付き道具が含まれる（取引）。重み `0.1 + social × 0.4 + max(0, stance) × 0.6`。効果: 目的物を claimant へ移す（`objective holder` 交代）、取引なら assets を交換、affinity 双方 +0.2。イベント `concede`（`details.mode: goodwill|trade`）。
- 鬼（policy=None）にも `concede` を verbs に加える（テンプレートの主体定義）。

### 3.4 `pledge` / `persuade`
- `persuade(target)`（III-2）: 事実を伴わない stance 上昇。重み `0.15 + social × 0.6`。効果: affinity target→self `+0.08 × (1 + social)`、self→target +0.04。`stance(target→self) < 0` の相手にも可（懐柔）。
- `pledge(target)`（III-3）: `stance` 双方 ≥ 0.4 の同席者。重み `0.1 + stubbornness × 0.3`。効果: 双方の `phase` に `誓約:<相手>` を追加、affinity 双方 +0.15、`reputation` を担保として記録（`world.pledges[(a,b)]`）。破り（pledge 相手への fight / sabotage / neutralize）は禁止せず、実行時に `reputation −0.5`、周囲（同席者）の affinity→自分 −0.3、イベント `betrayal`（分類 subtype `betray`）。

---

## 4. gapengine 側の変更（D4）
- `classify`: `neutralize→I-5`, `sabotage→I-4`, `sacrifice→I-6 (risky)`, `mislead→II-4 (sign −1)`, `confront→II-5 (risky, sign −1)`, `negotiate→III-2 (sign +1)`, `concede→VI-2 (sign +1)`, `persuade→III-2 (+1)`, `pledge→III-3 (+1)`, `fight` に subtype `betray`（target が pledge 相手）。
- `quality`: 「完成した前提連鎖の数」（observe→neutralize、pledge→betray、negotiate→concede）を加点（scale 2）。`betrayal` と `concede(goodwill)` を起伏に計上。
- `precedent.act_key` は verb の追加だけで変更なし。canon.yaml に neutralize/negotiate を含めない（前例に無い＝新規扱いのまま）。
- テスト: 前提違反 0（全アーカイブの decision 行を走査し、neutralize の前に known_modifier が無い行が無い）、permission deny の verb が候補に立たない、concede の goodwill/trade 両モード、betrayal の reputation 減。

---

## 5. 乱数消費箇所の追加
Phase 1 で新たに乱数を消費する箇所: **なし**（confront の真偽判定は決定的、concede は条件判定のみ）。Phase 0 の列挙のまま。

## 6. 納品分割
- **D3（engine）**: §1〜§3 の engine 側（facts の values/implies/refutes、beliefs 更新、confront/mislead/neutralize/sabotage/sacrifice/negotiate/concede/persuade/pledge、行動グラフ読み込み、対象開放と正規化、offers/pledges、イベント）、`projects/momotaro` の更新（鬼に concede、valued fact の例を 1 つ: `oni_weakness: values: [金棒, 火, 塩]` と証拠 2 件）、`tests/test_engine.py` の追加。
- **D4（gapengine）**: §4 と `templates/momotaro/action_graph.yaml` の edges/permission、`tests/test_gapengine.py` の追加。
- D4 後に本番実験（N=100・G=20・K=3）で合格条件 1〜4 を判定。

## 7. D4 に同梱する Phase 0 の残指摘（2026-09-11、D2b 再レビュー後の設計役判断）
- C-5 `--keep reached` は到達 0 の世代で layers.jsonl を全消去する → **その世代の shaped 最上位個体の全シードのランを残す**（証跡用）。
- C-6 `Policy.reweight` の無変調条件は `annotate_only or (genome.is_neutral() and not rules)`（Phase 2 の修飾ルールが中立遺伝子で黙って無効化されないように）。
- C-7 `random_baseline.py` の `descriptor_distribution` は到達ランのみ → 全ラン分布 `all_descriptor_distribution` を併記。
- C-8 `random_baseline.py` に `--seed-base` を追加。
- C-9 `--seed-base` の負値を argparse で拒否。prune 後の空 seed ディレクトリは削除する。

## 8. D3 適用後の設計判断（2026-09-11、設計役）
- **permission テーブル（桃太郎）**: 敵対者への `give_item` / `share_knowledge` は `restricted`（§3.1 の表の `allow` を訂正。きびだんごで鬼を味方化する都合主義を抑える。懐柔は `persuade` / `negotiate` で行う）。permission セクションに無い verb/role は allow、action_graph 自体が無ければ Phase 0 どおり `restricted_weight`。
- **action_graph のパス解決**: world.yaml のディレクトリ相対 → プロジェクトルート相対 → 絶対パスの順。`Simulation` に `action_graph_path` の上書き引数を用意し、evolve / random_baseline は `--template` の action_graph.yaml を明示的に渡す（D4）。
- **固定ハッシュ**: 桃太郎 fixture は Phase 1 に opt-in したため seed 153 の固定ハッシュを更新する。あわせて「Phase 1 の設定を外した世界」が Phase 0 の旧ハッシュ `3e95ce80…` を再現するテストを追加し、opt-in の約束を検証する。
- Phase 0 レビューの持ち越し（B-1 全主体 capture、C-1 `_PREDICATE_NAMES` 一本化、C-2 zone 名との衝突検査、C-5 lru_cache）は D3b に同梱。

## 9. D3 レビュー後の設計判断（2026-09-11、設計役）→ D3c
- **正規化と permission の関係（A-1/C-2 の訂正）**: 同一 verb の候補重みは `w_i = M × prior_i / Σ_j prior_j × permission_i`。`M` は Phase 0 でその verb が単一候補だった verb（fight / neutralize / sabotage / mislead / persuade / confront）では `基礎重み_single × (1 + open_bonus)`、Phase 0 で既に多対象だった verb（give_item / share_knowledge）では **Phase 0 の候補ごとの基礎重みをそのまま使い正規化しない**（permission だけ掛ける）。これで候補間の比は `prior × permission` を保ち、restricted は総量を減らす。
- **exposure（A-2）**: `BeliefAbout` に `misled_by: str | None` を持たせ、mislead で書かれた推定が observe で真値に戻ったときだけ `exposure` を出す（初回 observe では出さない）。
- **confront が発火できる fixture（A-3）**: 桃太郎に valued fact `treasure_thief`（values: [鬼, 猿, 犬]、truth: 鬼）と証拠 2 件（村での聞き込み → implies 鬼 0.5、道中の噂 → implies 猿 0.4（decoy））を追加。`oni_weakness` はそのまま残す。
- **concede が発火できる fixture（A-4）**: 桃太郎の初期所持に `勾玉`（modifier +5、visible、lootable、鬼が持たない）を追加して trade 経路を開く。goodwill 経路は persuade / give_item（restricted）で鬼→桃太郎の stance を上げれば到達可能（GA の stance_shift_bias に委ねる）。
- **A-5**: persuade も正規化対象に含める（上記 M の規則で扱う）。
- **A-6**: pledge / negotiate / concede / confront / rescue / observe も候補生成時に `permission()` を参照し、deny なら列挙しない。
- **C-1**: 対象開放は action_graph の有無で切り替える現行のままとし、桃太郎テンプレートの permission（D4 で追加済み: fight の neutral/ally は restricted）で抑止する。
- **C-3**: `observe` は downed の相手にも可（候補と実行を一致）。
- **B-1**: `bind_subjects` の fact 衝突チェックの重複を削除。
- テスト: `test_hostile_permission_reduces_give_and_share_weights` は permission を明示注入した world で総量が下がること（と比が permission に一致すること）を検証する形に書き換える。
