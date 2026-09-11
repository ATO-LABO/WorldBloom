# Phase 2 実装計画: 遅延効果・身分・儀礼・修飾ルール

作成: 2026-09-11（設計役 Fable）/ 親設計: `docs/2026-09-11_gapengine-detailed-design.md` §8・§9・§10・§3.8・§16 Phase 2 / 前提: Phase 1 合格 / ステータス: 実装指示（納品 D5 = engine、D6 = gapengine/テンプレート）

## 0. 狙いと合格条件（設計書 §16）
1. `chosen` 伏線の回収時機が個体間で異なる（同じ伏線を仕込んだ個体で payoff のターンが分布を持つ）。
2. 未回収伏線を持つ個体がアーカイブで淘汰される（同マスで未回収個体が回収個体に置換される事例）。
3. 変装→露見のあるランが高 volatility マスに現れる。
4. 修飾ルール（rules.yaml）が有効遺伝子を文脈で変え、ログの `policy.effective_genome` に反映される。

## 1. 遅延効果層（設計書 §8）

### 1.1 データ（世界側）
`world.pending_effects: list[PendingEffect]`、`PendingEffect = {id, library_id, planted_by, planted_turn, target, condition(Predicate), description, effect, mode ∈ {auto, chosen}, resolved: bool, resolved_turn}`。ライブラリは `templates/<genre>/effects.yaml`（設計書 §8 の形式）を `world.gapengine.effects`（パス）で読み込む。無ければ空。

### 1.2 設置（plant）
- **既存 verb による自動設置**: ライブラリの `plant.verb` が `give_item` / `observe` / `share_knowledge` 等の場合、その verb の実行後に `plant` 条件（`item`, `to_role`, `reveals` 等）を照合し、一致すれば `pending_effects` に積む（同じ `library_id` × 同じ target は 1 回）。派生イベント `planted`（マーカー）。
- **専用 verb `plant(effect_id)`**（II-2）: `plant.verb: plant` のライブラリ項目のうち、`plant.when`（述語、任意）を満たすものを候補に列挙。重み `0.15 + curiosity × 0.3`。効果は設置のみ（ターン消費）。
- 設置は乱数を消費しない。

### 1.3 回収（payoff）
- スロット末に全 pending の `condition` を評価（述語評価器を共有。名前空間に `target` と `planter` を束縛）。
  - `mode: auto` → 条件が真になった最初のスロット末に発火。`event` 行 `payoff`（`delta` に効果の差分）。
  - `mode: chosen` → 条件が真の間、planter の候補に `payoff(effect_id)`（II-3）が立つ。重み `0.3 + stubbornness × 0.3`。実行で発火。条件が偽に戻れば候補は消える（`resolved` は false のまま）。
- `effect` の語彙（v1）: `modifier: {target, source, value, kind}`（明示 modifier の追加）、`neutralize: {target, source}`、`stance: {a, b, delta}`、`reputation: {target, delta}`、`enable_verb: {verb, target?, source?}`（planter の `verbs` に追加。§7.1 の `oni_weakness_known` は「neutralize を解放する」ではなく、Phase 1 で neutralize が前提付きで常設になったため、効果は `modifier` 型（観測の記憶が味方の士気 +5 になる等）に置き換える）。未知の効果種別は読み込み時 `ValueError`。
- 結末時（ending / 日数満了）に `mode: chosen` かつ `resolved: false` の件数を `ending` 行 `details.dangling_effects` に記録。

### 1.4 桃太郎の伏線ライブラリ（v1、3 件）
```yaml
- id: kibidango_loyalty
  plant: {verb: give_item, item: きびだんご, to_role: [neutral, ally]}
  payoff:
    condition: "stance(target, planter) >= 0.5 and hostile_present(planter)"
    description: "きびだんごの恩に報いる仲間の奮戦"
    effect: {modifier: {target: planter, source: "$target", value: 10, kind: loyal}}
    mode: auto
- id: oni_gap
  plant: {verb: observe, reveals: {target: 鬼, source: 金棒}}
  payoff:
    condition: "present(鬼) and vitality(planter) != 'downed'"
    description: "金棒の隙を見切って仲間に合図を送る"
    effect: {modifier: {target: planter, source: 見切り, value: 15, kind: insight}}
    mode: chosen
- id: village_promise
  plant: {verb: plant, when: "zone(planter) == '村'"}
  payoff:
    condition: "holds(planter, 鬼ヶ島の宝物) and zone(planter) == '村'"
    description: "旅立ちの朝に交わした約束を果たす"
    effect: {reputation: {target: planter, delta: 0.3}}
    mode: chosen
```

## 2. 身分層（設計書 §9）
- `disguise(displayed)`（IV-1）: テンプレート `disguises: [{subject: 桃太郎, as: 旅の商人, when: "zone(self) == '海'"}]` に列挙された人格へ。候補重み `0.1 + (1 − social) × 0.3`。効果: `identity_displayed = as`、自分を知らない同席者（`awareness < 0.5`）の `beliefs_about[self].identity_seen = False`。
- `perceived_name(observer, target)`: `identity_seen` なら true、さもなくば displayed。**関係行列・敵対判定・役割プライア・permission・goal.obstacles の照合はすべてこの名前で引く**。テンプレートは displayed 人格に対する初期関係を `relations` に書ける（`鬼: {旅の商人: {affinity: 0.0, awareness: 0.0}}`）。
- 露見: `observe` の `identity_seen=True` 化、`confront` 正解、または `fight` 参加（戦えば正体が割れる）で派生イベント `exposure`。露見時に observer の関係を true 人格の値へ切替（displayed 向けの関係は破棄）。
- `identity` 乖離は層ベクトルの `identity 乖離 0/1` に既に入っている（Phase 0）。

## 3. 儀礼・還元
- `grand_gesture(target)`（III-4）: 前提: target の「傷」＝target を `secret_of` に持つ fact を planter が `known`（Phase 1 の valued fact 拡張で `facts[].secret_of` を追加）。代償: 自分の最大 assets 1 つ（modifier 付き優先）を失い、bonds（自分→味方の affinity）を −0.2。効果: `stance(target→self) += 0.6`、周囲の awareness +0.3、reputation +0.1。重み `0.05 + social × 0.2`（Policy の risky）。
- `trial(giver)`（III-1）: テンプレート `trials: [{giver: おじいさん, requires: {stance: 0.4, item: きびだんご}, grants: {fact: 造船術}}]`。候補: giver 同席かつ requires 充足。効果: grants（fact/item）、affinity 双方 +0.1。
- `donate`（VI-3）: 目的物を所持して `deliver_to` にいるとき候補。効果: 目的物を `world.delivered[item] = zone` に移し（holder はゾーン名）、reputation +0.5。ending 述語 `holder(鬼ヶ島の宝物) == '村'` で「還元」結末を書ける（桃太郎テンプレートに `ending: homecoming_shared` を追加し、`target_ending` は従来どおり）。

## 4. フェーズ層の切替と修飾ルール
- `phase_rules`（設計書 §10.1）: `[{id, when, enable: [...], disable: [...]}]`。`availableActions = verbs ∩ 前提充足 ∩ (∪enable) − (∪disable)`。候補生成はこの集合だけを列挙。
- `rules.yaml`（設計書 §3.8）: `Policy` が `scope: turn` の規則を各決定の冒頭で、`scope: candidate` を候補ごとに評価し、`g_eff = clip(genome + Σadjust)` を使う。`policy.effective_genome` に記録。述語名前空間に `target`（candidate scope）を束縛。桃太郎 v1: `hostile_lean`（stance(self,target) < −0.3 → I +0.2, risk +0.1）、`after_crossing`（'越境' in phase → risk +0.2）、`when_downed_ally`（味方が downed → III +0.3）。

## 5. gapengine 側（D6）
- classify: `plant→II-2`, `payoff→II-3`, `disguise→IV-1 (sign 0)`, `grand_gesture→III-4 (risky, +1)`, `trial→III-1`, `donate→VI-3 (+1)`。
- quality: 未回収 `chosen` 伏線 −0.05/件（`ending`/最終行の `dangling_effects`）。`exposure` と `payoff(chosen)` を起伏に加点。
- precedent の ctx に `disguised: bool` を追加（キーの互換性: 旧表は `disguised=False` として読む）。
- テスト: auto 伏線の自動発火、chosen 伏線の候補出現と回収、未回収の減点、disguise→exposure の関係切替、rules の effective_genome 反映、phase_rules の enable/disable。

## 6. 乱数消費
追加なし。

## 7. 納品分割
- **D5（engine）**: §1〜§4 の engine 側（pending_effects、plant/payoff、disguise/perceived_name/exposure、grand_gesture/trial/donate、phase_rules、`world.delivered` の一般化）、桃太郎テンプレートの effects.yaml / disguises / trials / phase_rules / ending 追加、tests。
- **D6（gapengine）**: §5、rules.yaml の適用、tests。
- D6 後に本番実験で合格条件 1〜4 を判定。
