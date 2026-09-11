# Phase 4 実装計画: 他ジャンルへの転用・転換（rethink）・メタ進化の準備

作成: 2026-09-11（設計役 Fable）/ 親設計: `docs/2026-09-11_gapengine-detailed-design.md` §3.8・§7.1（II-6）・§11・§16 Phase 4 / 前提: Phase 3 合格 / ステータス: 実装指示（納品 D10 = engine/gapengine の汎用化、D11 = 恋愛テンプレート、D12 = 探偵テンプレート）

## 0. 狙いと合格条件（設計書 §16 Phase 4）
1. 恋愛テンプレートで `ending.when` 述語（相互 stance ≥ θ）に到達するアーカイブが得られる。
2. 探偵テンプレートで `confront` 正解を結末とするアーカイブが得られる。
3. 桃太郎テンプレートの結果（Phase 0〜3 の固定ハッシュ・到達率）が Phase 4 の汎用化で変わらない。
4. 修飾ルールの「有効ビット」を遺伝子に足すメタ進化が **設定で ON/OFF** でき、OFF では 9 スカラーの遺伝子と完全互換。

## 1. 汎用化（D10）

### 1.1 結末の汎用評価と整形適応度
- `shaped(rows, world)` を桃太郎特化から述語ベースへ: `target_ending` の述語を **連言肢**（トップレベル AND）に分解し、各連言肢の充足 0/1 と「距離」（`holds`: 所持 0/1、`zone`: 最短 hop の近さ、`stance(a,b) >= θ`: `(stance+1)/(θ+1)` の近さ、`known/knows_modifier`: 0/1、`confront` 成功はイベント有無）を平均する。分解できない述語（OR や関数呼び出しのみ）は「到達 1 / 未到達 0」に退避。`engine/predicate.py` に `conjuncts(pred) -> list[Predicate]` を追加。
- `--target-ending` を CLI で上書き可能に（テンプレートで複数の結末を持つ世界で、どれを固定するかを実験ごとに変える）。

### 1.2 真相のシード抽選（探偵ものの前提）
- `world.truth` に固定値の代わりに `{fact_id: {candidates: {value: weight}}}` を許し、**シードごとに専用 rng（`random.Random(f"{seed}:{fact_id}")`）で抽選**する（StorySim の where 型の問いと同じ流儀。主 rng を消費しない）。抽選結果は header 行に記録。`secret_of` / `known_by`（真相を最初から知る主体）に対応。

### 1.3 `rethink`（II-6 転換・気づき）
- 候補: 主体が valued fact の信念を 2 つ以上持ち、直近 N スロットで新事実を得ていない（停滞）とき。重み `0.1 + curiosity × 0.4`。
- 効果: 既存の証拠（`knowledge` に含まれる implies/refutes）を再走査して信念を再計算し（同じ証拠でも順序と現在の stubbornness で結論が変わりうる）、変化があれば `rethink` イベント（`details.before/after`）。乱数不消費。
- 分類 II-6、stance_sign 0、risk neutral。quality の起伏に「信念の反転」を計上（既存 `belief_shift_counts` 相当）。

### 1.4 メタ進化の準備（設計書 §3.8）
- `Genome` に任意フィールド `rule_bits: dict[str, bool]`（rules.yaml の各 `id` に対する有効ビット）を追加。`meta_evolution: false`（既定）では常に全 True で 9 スカラーと互換（`to_dict` は rule_bits を省略）。`true` のとき crossover は id ごとに一様、mutation はビット反転（確率 0.1）。
- `Policy` は `rule_bits[id]` が False の規則を無視する。

### 1.5 ジャンル枝刈り
- `templates/<genre>/action_graph.yaml` の `nodes[].genres` を engine の候補生成が参照し、テンプレートの `genre` に含まれない verb は候補に立てない（構築的枝刈り）。`qd.yaml` の `categories` で縦軸を絞る（既存）。

## 2. 恋愛テンプレート（D11）`projects/romance/` + `templates/romance/`
- 主体: 主人公 A、相手 B、ライバル C、友人 D、B の親 E（5 人）。ゾーン: 学校・カフェ・公園・B の家・駅（一本道ではなくハブ型）。
- 能力層の読み替え（設計書 §14 他ジャンルへの転用例）: B の「本心」= base、「表面的な態度」= modifier `防衛`（`visible: false`、トラウマ由来）。C の「優位性」= modifier `噂`。
- 結末: `ending: [{id: mutual, when: "stance(A, B) >= 0.6 and stance(B, A) >= 0.6"}]`、`target_ending: mutual`。lethal なし（`death` 無効）、downed は「気まずさ」（`vitality` は使うが `lethal` 属性の道具を置かない）。
- 行動グラフ: fight は `deny`（喧嘩は `confront` で表現）、`persuade`/`grand_gesture`/`pledge`/`share_knowledge` が主軸。`observe` = 本心を知る、`neutralize` = トラウマの原因（modifier `防衛`）を除く（前提 observe）。伏線ライブラリ: 「昔の約束」（chosen）、「C の噂」（auto: 露見で C の modifier が消える）。
- QD 縦軸: I・II・III・IV（V・VI は薄いので除外、設計書 §12 の再チューニング）。
- 合格: N=100・G=20 で `mutual` に到達するエリートが ≥3 マス。

## 3. 探偵テンプレート（D12）`projects/detective/` + `templates/detective/`
- 主体: 探偵、容疑者 3 人（うち真犯人はシード抽選）、証人 2 人。ゾーン: 屋敷の 5 室。
- valued fact `culprit`（values = 容疑者 3 人、truth はシード抽選、`known_by: [真犯人]`）。証拠 boolean fact 6 件（implies/refutes、うち 2 件は decoy）。容疑者の `mislead` が許可（真犯人は permission allow、他は restricted）。
- 結末: `ending: [{id: solved, when: "confronted(探偵, culprit) == truth"}]` → 述語名前空間に `confront_success(a, fact)` を追加（`confront` 正解イベントの有無）。
- 能力層の読み替え: 証拠力 = base（`investigate` で上がる）、アリバイ = modifier（`neutralize` で崩す。前提 observe）。
- 行動グラフ: fight/sabotage は deny、`investigate`/`observe`/`share_knowledge`/`confront`/`mislead`/`persuade` が主軸。QD 縦軸: I（証拠固め）・II（推理）・III（聞き込み・関係）・IV（変装・潜入）。
- 合格: N=100・G=20 で `solved` に到達するエリートが ≥3 マス、かつ「decoy に引っかかって misjudged した後に立て直す」エリートが ≥1。

## 4. 乱数消費
§1.2 の真相抽選は専用 rng（主 rng 不消費）。他は追加なし。桃太郎の固定ハッシュは D10 で変わらないこと（合格条件 3）。

## 5. 納品分割
- **D10**: §1（汎用化・rethink・メタ進化準備・枝刈り）+ tests。
- **D11**: §2 恋愛（world/subjects/templates、本番実験）。
- **D12**: §3 探偵（同上）。
- Phase 4 の完了をもって設計書 §16 の全フェーズ完了。残る保留（サブプロット差分トラック、QD 軸の追加、メタ進化の本格導入）は設計書の「未解決の論点」に戻す。
