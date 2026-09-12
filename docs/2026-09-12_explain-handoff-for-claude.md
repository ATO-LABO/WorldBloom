# WB-EXPLAIN-005 独立受入用の引き継ぎ

2026-09-12 / 実装: Astra (Codex) / 対象: `G:\マイドライブ\Projects\WorldBloom_v2`

Claude Codeの005第1回受入で差し戻されたF1〜F5を修正し、002〜004を再提出する。**005は受領済み。独立再レビューと合否判定はClaude Code担当**。読者品質の合格や物語の幅の改善を意味しない。コミット・pushは行っていない。

## まず開くもの

- 協業ボード: https://app.notion.com/p/3d9e21ef1cac8153aa56fbf23ca9fb7e
- 005: https://app.notion.com/p/3d9e21ef1cac81eda217f2d966298f78
- 契約: `docs/2026-09-12_explain-four-items-contract.md`
- 指摘別の修正・再検証: `docs/reviews/2026-09-12_explain-revision-response.md`
- 第1回レビュー（原文を保持）: `docs/reviews/2026-09-12_explain-implementation-review.md`
- 着手前計画: `docs/2026-09-12_explain-implementation-plan.md`
- 最終検証出力: `C:\Projects\WorldBloom-local\runs\wb-explain-revision-20260912-r2`
- 新形式比較: http://127.0.0.1:5323/exp/new-detective/compare?cell=II%7Clow&cell=I%7Chigh
- 新形式恋愛: http://127.0.0.1:5323/exp/new-romance/cell/II%7Chigh

基準HEADは `445fa76`。開始時の作業ツリーはクリーン。旧WorldBloomとStorySimは変更していない。既存ランも上書きしていない。

## 変更の所在

| 工程 | 所有ファイル | 変更 |
|---|---|---|
| 001 | 契約・計画・本書 | 修訂R1〜R7を採用。契約の古い件数表記を訂正。過去scratchpadの成績を今回の再実行結果には数えない |
| 002 | `engine/decision_record.py`, `engine/sim.py`, `gapengine/evolve.py`, `scripts/evolve.py` | API既定OFF、CLI通常導線ON。抽選を一度行った後に重み上位8件＋選ばれた1件を記録。候補順/RNG/脇役方針は維持。F3(a): 全主体の実行直前stamina/resourcesを追加 |
| 003 | `gapengine/explanations.py`, `scripts/explain.py`, `scripts/verify_explanations.py` | 決定直前の知識と出典、即時代償、必要条件を成立させた選択と後続の連結。旧ログの代替候補は不明 |
| 004 | `viewer/data.py`, `viewer/pages.py`, `viewer/server.py`, `viewer/explanation_ui.py`, `viewer/static/app.css` | 可変格子の短い四項目、詳細と全主体の場面注釈、2〜4候補比較、原ログ行の前後表示と全文導線 |
| テスト | `tests/test_explanation_recording.py`, `tests/test_explanations.py`, `tests/test_explanation_viewer.py` | 記録の非干渉、欠損と知識境界、代償の向き、取消と再有効化、比較と原ログ参照 |

依存は viewer → gapengine.explanations → ログ。抽出器はviewerやLLMに依存しない。既存の転機種別判定は抽出器へ移した。あらすじ・本文の生成は変更していない。

## 実例3本の対応

| 実例 | 代表選択 | 即時代償 | 実際の後続 | 原ログ行 |
|---|---|---|---|---|
| exp10-detective / II\|low | T22 再考。犯人の見立てなし→容疑者乙、確信度0.75 | 確認範囲内ではなし | T28 容疑者乙を告発 | L157→L197 |
| exp10-detective / I\|high | T31 再考。容疑者丙0.18→容疑者甲0.75 | 確認範囲内ではなし | T32 容疑者甲を告発 | L222→L229 |
| exp7-romance / II\|high | T35 AがBの防衛を無効化しchosen伏線を追加 | B→Aの好意−0.2 | T36 その伏線を回収 | L207→L212 |

完全な出典・知識・代償・連結データは検証出力の `legacy-1.json`〜`legacy-3.json`。`verification.json` に原ログSHA256と変更前後の一致を保存した。探偵の別の決定で発生した信用低下は代表の再考に混ぜていない。恋愛の `resources.bonds` は関係値の集計なので好意−0.2と重複計上していない。

恋愛で確認済みにした直接の連結は「この選択で未回収のchosen伏線が追加され、後に実行されたpayoffの必要条件を満たした」。旧設定全体を使った好意上限と結末述語の反実仮想判定までは実装していない。これを独立レビューで確認してほしい。

## 検証結果を分けて報告

### 回帰・決定性

- 変更前113件成功。
- 最終修正版の全回帰131件成功（69.547秒、`regression.txt`）。説明系18件も成功。記録の実状態照合、数量不明の喪失、転機の三値、表示の境界を追加。
- 記録ON/OFFで3ジャンルの全既存行・RNG状態一致。非中立Policyと脇役を含む。
- 記録ONのprocesses=1/2で各layers.jsonlとarchive.jsonのバイト一致。
- 候補上位外の採用、同率、重複候補、1件、空集合、非正の総重み、フォールバックの古い状態残留、選択前の場所を検証。
- 固定ハッシュ2本、中立遺伝子、既存の共進化を含む回帰が成功。
- `scenes.py` SHA256: `b17e13b88085134826ab86f2ee3f553d99aed253fb7e9a3e47bb2ed3acde69c1`
- `synopsis.py` SHA256: `1fd0ca31a10fd8602fde5644d88bcf709e39029c2952dd927f98312fe69ace55`
- 上記2ファイルはHEADとバイト一致。

### ログ整合性

- 旧形式3例の代表と後続・代償を独立抽出し、原ログSHA256の不変を確認。
- 確信度だけの更新、他主体へのdelta、None削除、未知の前値、好意の方向、未来の観察情報、snapshotに含まれない取得済み事実の保持を検証。
- 状態が無効に戻って再有効化された場合に古い選択へ誤接続しない境界テストあり。
- 新形式3条件×ON/OFFの6ランで追加記録を除く全行一致。3条件とも結末到達。`new-detective` / `new-romance` 内にjob・入力ハッシュ・ログを保存。
- 新形式ランは**現在の入力による検証用候補**。旧ランの完全な実効設定の来歴を証明していないため、旧候補の説明補完として関連づけない。

### 画面

- 比較・詳細・原ログ、旧形式、3行/6行の格子のHTTP正常応答を確認。
- 最終版の `compare.png`、`compare-narrow.png`（600px）、`review-panels.png` を目視確認。指摘箇所の実ログパネルは `review-panels.html` で開ける。旧ログ・比較・詳細・原ログのHTTP5件は `http.json`。
- 組み込みブラウザの接続機能は応答しなかったため、一時プロファイルのheadless Chromeで検証した。長い原ログの直接アンカー描画は空になるケースがあり、前後3行を返す導線に修正して描画確認済み。
- 430pxでの撮影はウィンドウ最小幅の影響を切り分けられていないため合格証拠に数えない。モバイル実機の操作は未検証。
- 2〜4件比較はGETフォーム。選定トレイとは別で、比較するだけでは本文候補の選定を更新しない。

### 読者品質・候補の幅

- 独立した読み手による確認は未実施。005で実施者と結果を記録する。
- 2本の探偵候補はいずれも再考→告発で、中心的な筋は似ている。犯人の見立てと履歴の差を表示しただけで、物語の幅が改善したとは主張しない。
- 同じ主人公の行動・対象・結果の並びは同じ筋と表示する。語句の言い換えで差を作らない。

## 第1回指摘への修正

- F1: 旧恋愛ログL21/L58の喪失を数量不明のまま確定。L97の数量−1も保持。L58の評判は不明のまま、部分的な代償と明示。
- F2: 候補はconfirmed/candidate。全行走査・終端・構造確認のうえ、対応済みの無変化の決定で関連イベント・policy記録・未分類マーカーがない場合だけabsent。再考の前後欠損などはunknown。契約付録Eと境界テストを参照。候補イベントの出典へ直接リンク。
- F3: 記録ONの全主体に版付き直前値を追加。新3条件の体力減少56件（NPC36件）を原ログ直前値と照合。旧ログの無記録回復は復元しない。
- F4: 知識の一覧は記録された範囲に限るとパネルに明示。
- F5: 独立した短い語彙表を維持し、未知語は「未対応の行動（識別子）」としてHTMLエスケープ。辞書統合による文生成変更は行わない。

## 現在の判定範囲と既知の限界

- 確認済みの転機は、後に実行された告発の対象値条件が不成立→成立した変更と、実行された伏線回収の未解決chosen pendingを追加した選択。確認は必要条件についてのもの。世界全体の反実仮想探索はしない。
- 閾値だけを跨ぐ確信度変更、重みを介する影響、結末述語の反転、他の候補ゲートは未対応なら不明。これらを「転機なし」として扱わない。
- 結末述語への関連は未確認なら優先加点しない。確認可能な後続件数→遅いターン→状態キー→原ログ行の順で決定的に代表を選ぶ。
- 初期状態・同席者・staminaの無記録回復は旧ログで復元不能の場合がある。値をゼロで補わない。取得済みの知識は部分的な記録であり、知識の完全な一覧とは主張しない。
- 即時代償と状態変化を区別し、遅延した代償は常に未確認。未対応verbは無代償と断定しない。
- 直前値追加後の記録容量は3例で120,106→316,687、146,350→397,333、135,415→390,279 bytes（約2.64〜2.88倍）。計測時間は各1回でキャッシュの影響があり、速度比較の結論は出さない。

## Claude Codeの再実行手順

PowerShellで対象リポジトリへ移動して実行する。

```powershell
Set-Location -LiteralPath 'G:\マイドライブ\Projects\WorldBloom_v2'
$env:PYTHONUTF8 = '1'
python -B -m unittest discover -s tests -v
python -B scripts/verify_explanations.py --runs C:\Projects\WorldBloom-local\runs --out C:\Projects\WorldBloom-local\runs\wb-explain-claude-rereview-20260912
python -B -m viewer.server --runs C:\Projects\WorldBloom-local\runs\wb-explain-revision-20260912-r2 --port 5323
```

出力先が既に存在したら新しい名前を指定する。検証スクリプトは上書きを拒否する。5323は本セッションで起動済みの場合がある。既存のDev-Launcher登録は5401で同じviewer/server.pyを起動するため、継続利用可能。

通常の新規探索は `scripts/evolve.py` が候補記録を既定ONにする。大量探索で省略する場合は `--no-record-explanations`。Python APIの `Simulation` / `evolve(cfg)` は互換性のため既定OFF。コア説明のためのLLM呼び出しは不要。

## 005で行うこと

1. 差分と契約の適合をレビューする。上記の限定された転機判定が今回の受入範囲に適合するか明記する。
2. 3例について、原文行→直前知識→同じ選択の代償→後続を照合する。
3. 記録あり/なし・該当なし/不明・部分候補・同じ筋の表示を読む。
4. 読み手として違いが理解できるか、幅が不足するかを回帰結果と分けて記録する。
5. 問題は002〜004へ番号付きで差し戻し、合格した後にClaude Codeがコミット・pushする。
