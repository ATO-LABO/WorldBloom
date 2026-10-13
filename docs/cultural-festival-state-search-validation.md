> この報告は状態seed試走の段階記録です。最新GA実装と最終再実行は `cultural-festival-ga-adapter-validation.md` を参照してください。

# 文化祭：最終コード再実行・状態に基づく少数試走

確認済み: **基準パターン0成立。候補07の未回答を不合格へ修正。状態に基づく4 seed の試走・352選択の監査を完了。**
GA evolution は実施していない。Jev API 呼び出し0件。広い行動空間の探索完了・文章品質の完成とは扱わない。

## 前回成果の再現性修正

前回 Library version 1 は、最終コードの厳密な `declines_to_perform is False` 条件を追加した後に候補を再実行しなかった。
そのため candidate-07/run.json の null を合格扱いした旧結果が残った。今回、全コード変更後に基準、再実行、8候補、原稿生成を再実施した。
候補07は15時在場・出演回答未了(null)・pattern0_pass=false。未回答を了承や拒否と同一視しない。
8本の在場/遅刻は7在場・1遅刻、厳密な基準条件の合格は6本。不合格は04（遅刻）、07（回答未了）。
基準パターン0は衣装 true、練習3、音響 true、蓮在場、出演了承、実行失敗なしで成立。CPU約0.0259秒。

## 探索方法の区別

| 方法 | 今回の扱い | 変更するもの | 確認範囲 |
| --- | --- | --- | --- |
| 手書き期待列/基準 PLANS | 検証用として保持 | 手書きの実行可能な選択 | native の基準可行性 |
| 前回の限定乱択 | 最終コードで8 seed 再実行 | 蒼の13:30〜14:30の展示室内選択。15時移動固定も残る | 比較用の段階試走。広い探索の証拠にしない |
| 今回の状態に応じた選択 | 4 seed + seed0再実行 | 全4人・全22行動時点の合法な選択と相互作用 | 目的・知識・状態に応じた固定重みと native RNG による少数試走 |
| GA evolution | 未実施 | 将来は人格ではなく行動選択変数を進化 | 接続上の不足を source/ログで確認 |
| Jev | 未使用 | 将来は本人が知る文化祭文脈を評価 | API呼出なし。現行文脈の不足のみ確認 |

`gapengine/festival_policy.py` の `FestivalStatePolicy` は既存 Simulation の reweight/record 接続を使う。別のシミュレーションエンジンを作っていない。
候補4本の筋や行動列を先に書いて再生しない。候補は seed 0..3 の native 実行結果をそのまま保存する。
15時の移動固定は新 Policy にない。移動・待機・展示協力などが同時に正の重みを持つことをテストした。
予定は既知の目標位置への重み項。行動強制ではない。欠席を出すための逆方向の固定もない。

## 実際の判断と理由の記録

目的語句からこの世界用の purpose_flags を構成し、role・固定 traits と本人の状態を使う。任意の自然言語目標を理解する汎用モデルではない。
行動重みは、公開済み予定までの余裕、隣接経路からの距離改善、本人の衣装/練習、見聞きした音響確認、受信依頼、了承/断り、変更後の練習、了承後の協力を計算に使う。
同じ固定係数を全 seed で使い、世界・人物の性格・目的・初期条件を変えない。

短い reason は計算項の識別ラベル。実際に使った値は各人の `decision-audit-人物.json` の basis/scores/components に保存し、
選択イベントの decision_basis から audit_index と SHA-256 で対応付ける。定型文を後付けして判断の証拠にしない。
重みは明示的な実装ヒューリスティックで、Jev の確率や人間の心理測定値とは表記しない。練習3枠は Policy の柔らかい準備目安で、世界の必須条件を変更するものではない。

初回4 seed の品質点検で、準備不足が0なのに断りの重みが残る・了承済みの同一依頼を繰り返す問題を検出。
規模を増やさず修正し、完成した版で同じ4 seed を再実行した。台詞変更の了承を実際に知った蒼/蓮は、その後の練習履歴を参照する。
他者の秘匿状態を変えても本人の判断入力が同じなら重みが変わらないことをテストした。

## 最終4 seed の結果

| seed | 15時 primary | 出演の回答 | 衣装 | 練習 | 最初の開演後到着 |
| --- | --- | --- | --- | --- | --- |
| 0 | 在場 | 了承あり | False | 4 | — |
| 1 | 在場 | 未回答 | True | 4 | — |
| 2 | 不在 | 了承あり | True | 5 | 15:30 |
| 3 | 不在 | 了承あり | False | 3 | 16:00 |

各 run 約0.039〜0.044 CPU秒。全4人×22行動時点×4 seed =352選択と352完了を監査し、違反0・実行失敗0。
同時点で seed 間の選択が異なる点は、蒼21・紗季18・蓮17・灯19（各22点中）。
灯への相談・了承・断りも実際に選択された。相互作用の件数は seed ごとに12/13/15/16。
在場だけで準備完了や出演の回答済みとはしない。seed0は衣装未了、seed1は出演未回答。これらを基準パターン0合格と表示しない。
遅刻2本は15時30分、16時到着。終日欠席や3種類の理由分類は保証しない。

本人の knowledge と実ログの同時点スナップショットを比較し、受信宛先・練習/協力/了承の履歴・音響の観測範囲・選択確率と計算項の和を検証した。
各 seed の個人観測から4視点の記録初稿を生成し、4 seed の16視点を `WorldBloom-state-search-sixteen-views.md` にまとめた。
未観測の内心や他室の行動を補っていない。計算項ラベルを含む機械的な初稿で、文学的推敲は未実施。

## GA/Jev 接続点と必要な設計変更

現行 `gapengine/evolve.py:run_individual` は protagonist と任意の antagonist の Policy を作る。全4人の主観的 Policy factory 接続が必要。
現行 Genome の変異/交叉は category/risk/stance/novelty/plasticity を変えるため、人格を共通にする方針へそのまま使わない。
必要なのは固定人格と目的重みを維持し、合法候補からの選択変数だけを探索する genotype/codec の分離。既成の4筋を保存するための行動列にはしない。

現行 QD は effective な decision を使うが、文化祭の start は未完了。完了した action_completed と開始を対応付けて評価する必要がある。
実ログで既存 descriptor/effective_sequence/reached を読み取り試験し、sequence は空、reached は false。これを成功した GA fitness に偽装しない。
opening_check と準備/回答を分ける達成評価、次枠完了に沿う多様性軸、native 活動状態の volatility が必要。

現行 Jev 文脈は、この人物の native Goal が配送型でないため「目的: なし」と描写し、時点・活動準備・了承履歴がない。
文化祭の本人が知る目標・時計・未知の準備状態・相互作用を描写する formatter と、文脈/動作/エンジンを固定する cache schema 更新が必要。
Jev の外部 API や LLM を呼んでいない。既存の判断表を適合確認なしに流用しない。接続調査は `ga-jev-connection-audit.json` に記録した。

展示完成に必要な作業量は共通世界v1で定義されていないため、協力回数を完成判定へ置き換えない。所要量・完了観測の仕様が必要。
共同運搬2枠・中断、公演内部の15分の演技は未実装として維持。GUI/Windowsジョブ管理への接続も今回の実行経路では使用していない。
現段階は4 seed の可行性・知識整合点検。全行動空間の被覆、長期計画、統計的品質、GA/Jev による選定は未検証で、増量していない。

## 最終コードとの一致・検証

実行前後の Source SHA-256: `0d93cb5a979b599431ab0aea158820ef55d9a54ce1fa3f97726655108bcd99cb`。
基準、8限定候補、4状態候補すべての run.json の source_manifest は保存する実コードと一致。設定/人物/loaded 初期状態の hash も全方式で一致。
candidate07 の null/false を回帰テストへ追加し、同 seed の再実行一致も確認。関連20テスト成功。ネットワーク接続と旧 PLANS Policy を禁止した状態でも新試走が通る。
旧4世界のログは基点と engine source hash を除いて一致。前段の全体2210テストの8失敗・6エラーは基点でも再現するWindows/ポート問題。今回全件の再実行はしていない。
既存 GA/route/Linux/job/UI のファイルを変更・マージしていない。追加 module と文化祭 Native の監査情報のみ。

```sh
python3 scripts/cultural_festival_trial.py --out /absolute/new-baseline-output --candidates 8
python3 scripts/cultural_festival_state_search.py --out /absolute/new-state-output --seeds 4
python3 -m unittest discover -s tests -p test_festival_state_policy.py -v
```

CPUのみ。課金API/LLM、GPU、認証/権限変更、push、PR、マージ、公開はない。
Library の同一 ID を更新し、旧 version 1 の不整合を履歴として残しつつ、現版は再実行済みデータへ置き換える。
