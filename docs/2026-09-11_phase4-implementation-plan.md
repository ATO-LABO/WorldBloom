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

## 6. D11 適用時の設計決定（2026-09-11、設計役 Fable）

- **D10 の逸脱（Codex 自己申告、受理）**: `--target-ending` は D6b で実装済みのため変更なし。汎用 `shaped` は `deliver` を持つ結末に従来式（0.4×所持＋0.3×距離＋0.3×到達）を残し、それ以外の結末を連言肢平均で評価する。複数 target は各結末の最大値。桃太郎テンプレートには `genre`・`rethink` を追加しない（opt-in は D11/D12 の各テンプレートで行う）。`summary.json` に `meta_evolution` は書かない。`quality` の「信念の反転」計上は `rethink` イベントで値が変わった fact 数を `dramatic_turns` に加算する方式（最大寄与 1/7）。
- **恋愛テンプレートの初回納品は結末が易しすぎた**: 方針なし無作為 30 シードで 30/30 が `mutual` に到達（中央値 5 ターン）、`neutralize` 0 回。B の不可視 modifier `防衛` が関係層に一切効いていなかった。
- **決定: 能力層の修飾子が関係層を縛る汎用機構 `affinity_cap` を engine に追加する**。`Modifier.affinity_cap: float | None`（既定 None）と `affinity_cap_targets: tuple[str, ...]`（空なら全対象）。主体が active な cap 付き modifier を持つ間、その主体を observer とする affinity は `Relations.change` と初期 bind で上限に clamp される。`neutralize` 等で `active=False` になると上限は消える。実装は `Relations(affinity_cap_resolver=...)`（`target_resolver` と同じ流儀）、`World.bind_subjects` が subjects の active modifier から解決関数を渡す。乱数不使用、cap を持つ modifier が無い世界（桃太郎）では従来と完全に同じ結果（固定ハッシュ不変）。
- 恋愛テンプレートでは B の `防衛` に `affinity_cap: 0.35` を付け、`observe → neutralize(B, '防衛')` を経ないと B→A が 0.6 に届かないようにする（構想の主経路が必須になる）。初期値は無作為 30 シードの到達率が概ね 10〜30% になるよう調整する（0% では GA が学習できず、100% では選択圧が無い）。
- rules.yaml の述語で修飾子名は文字列リテラル（`'防衛'`）で書く（初回納品は裸の名前で `Unknown predicate name` になった）。

## 7. D11 実測（2026-09-11、コミット 1701d15）

| 項目 | affinity_cap 導入前（初回納品） | 導入後（1701d15） |
|---|---|---|
| 無作為 30 シード（方針なし）の `mutual` 到達 | 30/30（中央値 5 ターン） | **4/30**（到達ターン 17 / 24 / 25 / 32） |
| 到達ランの `neutralize(B, 防衛)` | 0/30 | 4/4（全到達ランが observe→neutralize→「昔の約束」payoff→mutual） |
| 桃太郎の小規模 evolve byte-match（ce11ed7 比、engine_hash 除く） | — | 83/83 一致 |
| テスト | — | 86 件通過 |

設計上の主経路が必須になり、無作為到達率は狙い（10〜30%）の範囲に入った。本番実験（N=100・G=20・K=3、`runs\exp6-romance`）の結果は後述。

### 恋愛テンプレート本番実験（exp6、コミット 1701d15 の固定ワークツリー、`runs\exp6-romance`、N=100・G=20・K=3・`--keep reached`）

| 項目 | 結果 |
|---|---|
| 到達率（母集団 100×3 シード） | 第 0 世代 23%（69/300）→ 第 10 世代 52% → 第 19 世代 47% |
| アーカイブ占有マス | **8 / 12**（II×2・III×3・IV×3。I は未占有） |
| アーカイブ相異度 | 0.65 |
| エリート品質 | 0.33〜0.43 |

合格条件 1（`mutual` に到達するエリートが ≥3 マス）✓。到達エリートの主導カテゴリが II（観察・再考）・III（対話・誓約）・IV（噂への対峙）に分かれ、同じ結末に至る経路の多様性が得られた。

## 8. D11 レビュー後の設計決定（2026-09-11、設計役 Fable）

- **受理**: 恋愛では valued fact が無く `rethink` は発火しない（II-6 の実証は D12 探偵で行う。ノードは残す）。A→B は雑談で早期に飽和し `mutual` の門は実質 B→A のみ＝「A は最初から B を想っており、物語は B が心を開く過程」という設計として受理。`affinity_cap_targets: [A, C]` は受理。
- **作り直し（差し戻し 3 回目）**: C（ライバル）に阻害チャネルが無かったため、「Cの噂」を **B が持つ cap 付き modifier**（`affinity_cap: 0.5`、対象 A）に改め、C を observe して噂を暴く auto 伏線の payoff で解除する。これで `防衛` と `噂` の 2 つの cap 解除が `mutual` の必要条件になる。C には B へ働きかける規則を与えて競争相手として機能させる。`move` は `category: null`（IV は「噂への対峙」）。`investigate` の `when: gather` を外し縦軸 I を到達可能にする。
- **engine の意味変更**: `affinity_cap` は「上昇を止める」上限とする（上限 = `max(cap, 現在値)`。ラン途中で cap が有効化されても既存値を一気に落とさない）。桃太郎（cap なし）は不変。
- D11 差し戻し 3 回目の実測: 90 テスト通過、桃太郎 byte-match 83/83、無作為 30 シードの `mutual` 到達 **2/30**（両到達ランとも「Cの噂の露見」と「防衛の neutralize → 昔の約束」の両方を経由）。狙い 10〜30% をやや下回るが、合格判定は本番実験（exp7）で行う。

### 恋愛テンプレート本番実験（exp7、作り直し版 0b67e7c、`runs\exp7-romance`、N=100・G=20・K=3・`--keep reached`）

| 項目 | 結果 |
|---|---|
| 到達率 | 第 0 世代 7%（21/300）→ 第 5 世代 32% → 第 15 世代 37% → 第 19 世代 33% |
| アーカイブ占有マス | **6 / 12**（II×3・III×3。I・IV は未占有） |
| アーカイブ相異度 | 0.64 |
| エリート品質 | 0.43〜0.50 |

合格条件 1（`mutual` 到達エリート ≥3 マス）✓。2 つの cap（防衛・噂）を要求する作り直し版でも GA は第 5 世代までに到達率を 4〜5 倍に上げた。縦軸 I（investigate）と IV（噂への対峙）は到達エリートの主導カテゴリにはならなかった（既知の制限として記録）。
- exp7 のエリート経路: 6 件中 5 件が「C を observe → 噂の露見（auto payoff）→ B を observe → neutralize 防衛 → 昔の約束 payoff → mutual」。1 件（III|low）は噂の露見を経ず、B の `噂` modifier を `neutralize` で直接解除する別経路（cap 付き modifier は observe で知れば neutralize できるため。設計上許容）。`old_promise` の payoff 行が同一ターンに 2 行出る点は要確認（Opus レビューへ）。

### 探偵テンプレート本番実験（exp8、コミット a21a97c、`runs\exp8-detective`、N=100・G=20・K=3・`--keep reached`）

| 項目 | 結果 |
|---|---|
| 無作為 30 シード（方針なし） | `solved` 3/30、真相分布 甲 11 / 乙 10 / 丙 9、misjudged 9 回・rethink 15 回 |
| 到達率 | 第 0 世代 22%（67/300）→ 第 10 世代 31% → 第 19 世代 32% |
| アーカイブ占有マス | **9 / 12**（I×3・III×3・IV×3。II は未占有） |
| アーカイブ相異度 | 0.64 |
| エリート品質 | 0.41〜0.57 |
| 「decoy で misjudged → rethink で culprit 反転 → 正解 confront → solved」 | **IV|mid のエリートで成立**（turn 22 誤認 → 26 rethink 不変 → 31 rethink で反転 → 32 正解 confront）。misjudged 無しの rethink 反転→正解も 3 件（IV|high・IV|low・I|low） |

合格条件 2（`solved` 到達エリート ≥3 マス、かつ誤認後に立て直すエリート ≥1）✓。真相がシードごとに異なる世界でも、GA は証拠収集→信念収束→正解 confront の経路を学習した。

## 9. Phase 4 合格判定（2026-09-11）
1. 恋愛: exp6（8 マス）・exp7（6 マス）✓　2. 探偵: exp8（9 マス、誤認→rethink→正解 1 件以上）✓　3. 桃太郎不変: 各コミットで固定ハッシュ 2 本・小規模 evolve の byte-match 83/83（engine_hash 除く）✓　4. メタ進化 ON/OFF: `--meta-evolution` で `rule_bits` が母集団に乗り、OFF は archive.json バイト一致 ✓。
設計書 §16 の全フェーズ完了。保留（サブプロット差分トラック、QD 軸の追加、メタ進化の本格導入、恋愛での rethink 実証、deliver 結末の個別到達判定、縦軸 I/IV が恋愛で未占有）は設計書「未解決の論点」へ。

## 10. レビュー後の是正と設計決定（2026-09-12、Opus レビュー D11r3＋D12／Fable 最終確認「条件付き承認」）

- **§9 の訂正**: 恋愛の縦軸 IV は「未占有」ではなく**到達不能**だった（`confront` は valued fact の信念がないと候補に立たず、恋愛には valued fact が無い。exp7 全 1714 ランで confront 0 回）。探偵では `observe`→IV・`move`→IV が設計書 §12.1（move の反復は雑音）に反していた。→ **両ジャンルとも縦軸 `[I, II, III]`**、探偵の `observe` は II、`move` は null（コミット e0c2358）。exp7 は confront 0 回のため IV 除外で結果不変（再実行不要）。exp8 は D12 差し戻し 2 回目の後に再実行して合格条件 2 を再判定する。
- **恋愛の C 規則 `rival_courts_b`**: NPC には Policy が無く（`--coevolve` 時の敵役のみ）規則は評価されない→削除。C の競争は噂 cap で表現する。`question_rival_advantage` も IV 廃止に伴い削除。
- **探偵の推理が真相と無相関**（Opus B-1）: 証拠 6 件が毎回同一で丙だけ否定証拠を持たず、rethink は真相に関係なく丙に収束（有効 confront 正解率 25%）。→ **設計決定**: `implies/refutes.value` に `$truth` / `$innocent:1` / `$innocent:2` トークンを許し（`bind_subjects` で抽選後に解決、固定値の世界では無変更）、無実の 2 人には必ずアリバイ反証を置く。真犯人の自白漏れは `share_min_affinity: 0.6` で抑制。
- **rethink の再設計**（Fable 項目 3）: 保持証拠が触れる valued 信念を派生・非派生を問わず再導出し、保護は真相所有者の直接知（confidence 1.0）のみ。再走査順は refutes → implies、confidence 降順、fact id。候補ゲートは「証拠 2 件以上」（`weapon` 埋め草の解消）。
- **受理**: 汎用 shaped の二系統（deliver＝従来式）は互換分岐として確定。`affinity_cap` は ally modifier の逆向きの層間作用として正当、「上昇のみ阻止」も妥当。恋愛で rethink 不発は受理。`old_promise` の 2 行は決定行＋派生イベント行で重複ではない。
- **未解決の論点へ**: `_mislead_candidates` が全主体で `world.truth` を参照する漏洩（Phase 1 由来、golden を変えるため系列境界で）／裏切り判定を verb 固定から分類器の `stance_sign` へ（恋愛の neutralize sign +1 と不整合）／`confront_success` 型結末は shaped に勾配が無い（確信度マージンの導入）／cap 専用 modifier（value 0）の関係層への移設／探偵の能力層（証拠力 base・アリバイ）が結末に効かない／恋愛で A→B が雑談で飽和する。
