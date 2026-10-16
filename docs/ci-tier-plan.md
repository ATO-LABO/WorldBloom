# CI分担案（未適用の準備資料）

この差分はdevelop `1f645b4de39e06b02727e3d85f464053056cf6da`を基点に準備した。
GitHubへのpush、PR作成、CI実行、Ruleset変更、mergeは行っていない。
PR #28（計測追加）と#29（パス検証の最小修正）は含めない。

## 分類と実行範囲

| 変更・イベント | 有効化後の検査 |
| --- | --- |
| 既存のREADME、docs/docsiteのMarkdown・表示資産の変更 | Windows/Python 3.13の基本検査 |
| `execution/worker.py`の監査対象Windows専用関数の本体だけの変更、既存`viewer/build_exe.cmd`の変更 | 基本検査＋Windows/Python 3.12・3.13の関連検査 |
| 共有処理・試験・CI・依存定義・テンプレート・プロジェクトデータ・未知ファイル | 基本検査＋Ubuntu/Windows × Python 3.12/3.13の全件 |
| 追加、削除、rename、copy、ファイル型変更、差分の取得失敗・上限超過・不完全入力 | 全4環境・全件 |
| main向けPR（develop→mainを含む） | 全4環境・全件を常に実行 |
| main/developへのpush | 検証済みPR receiptとmerge commitの親・treeが一致した場合は基本確認。証明できなければ全4環境・全件 |

初版の縮小対象は意図的に狭い。依存関係が未監査の機能変更を短い検査だけに振り分けない。
共通のパス検証、ロック、保存、プロセス管理の変更は全4環境へ倒す。
Windows専用と扱えるのは`kernel`、`identity_from_handle`、`_WindowsProcessTree`の既存本体だけ。
関数のsignature/decorator、クラスの基底・class属性、import時の処理、共有関数の変更を含めたら全件にする。
`tests/**`の変更も全件なので、変更した試験自身が選択から外れることはない。

差分は固定base/headのローカルGit objectからNUL区切りで取得する。
RESTの変更ファイル一覧の300件切詰めに依存しない。5,000ファイル・2 MiBの取得上限、rename両側、UTF-8、末尾NUL、pathを検証し、不明な場合は全件にする。
checkoutした実SHA/tree、PRのbase/head、merge-base、run/attempt、分類根拠、policy digestを記録する。
PR仮mergeの親がeventのbase/headと一致しないなど、入力の固定自体ができない場合は分類が失敗し最終gateも失敗する。

基本検査はPython構文・policy/workflow構造、既存の設定/実行計画/公開内容/HTML境界/行動カタログ/episode契約/census契約と新しい分類・gate試験、全18 JavaScriptスクリプト。
具体的な全moduleリストは`.github/ci/policy.json`に置く。
基本検査だけでは実GA、HTTPの実プロセス、全OSのロック/パス、全機能の統合、実GPU/LLM、署名付きexeのビルドは確認しない。
Windows関連検査は同JSONの12 module全件を両Python版で実行し、ID順序・継承による重複を保持する。
既存のassertion、GA 45秒、HTTP 5秒、lock/fsync、保存・整合性・終了保証は変えない。

## 最終gateと保護

固定名`ci-policy`が分類、基本検査、secrets、要求されたWindows関連/全4環境の検査を集約する。
`if: always()`と明示的なneedsを使い、failure/cancelled/想定外skipped/未起動/報告欠落/重複/件数不足/誤SHAで成功しない。
分類jobが失敗した場合も旧4gateをskipさせず、明示的に失敗させる。main向けPRは分類outputsだけに依存せず全件と4gateの起動を要求する。
要求しなかった追加検査だけをskippedとして認める。テスト単位の正規OS skipは既存census契約に従い、テストが実行されたID/件数に含める。
全件では既存2 shardのunion検証を維持し、さらに最終gateの独立discoveryと全4環境の件数・digestを照合する。
古いattemptの成果物を混ぜないようartifact名・download patternにattemptを含め、receiptのrun/attemptも検証する。

mainの4つのrequired context名は維持する。

- `regression (ubuntu-latest, 3.12)`
- `regression (ubuntu-latest, 3.13)`
- `regression (windows-latest, 3.12)`
- `regression (windows-latest, 3.13)`

developの切替先候補は`ci-policy`（GitHub Actions integration ID 15368）。strict、PR必須、merge方式、レビュー会話解決、force/delete禁止、bypassなしは維持する。
secretsは既存`security.yml`の検査を同じ`secrets` jobとして`tests.yml`へ移し、最終gateと4環境gateの依存にする。
Gitleaksの履歴/working tree検査と公開sample検査は削除しない。CodeQLの3言語PR/push/週次解析は既存workflowのまま維持する。
設定控えJSONを配置するだけではGitHub設定は変わらない。実GETを基準にしたbefore/intermediate/after候補を別途レビューする。
`.github/rulesets/develop.json`の将来用控えも新gate名へ整合させた。この控えには以前から未適用のsecrets/CodeQL保護案が含まれる。
実GETの4gateだけを基にしたCI切替候補と、追加のsecurity保護を混同して一括適用しない。後者はmainへのsecurity/CodeQL workflow導入を確認して別途承認する。

## PR/pushの重複

成功PRのhead SHAだけでは省略しない。pushの実merge commit、2つの親、対象branch、merged PR番号、成功したtests workflow、実際に成功した`ci-policy` job、未失効のreceiptを正式GETで照合する。
receiptのbase/head/試験treeと実merge treeが一致し、mainでは全4環境の検証済み記録が必要。
API権限不足、エラー、pagination/byte上限、receipt失効・欠落、複数PR、squash/rebase、直接push、PR外の変更などは全件へ倒す。
受け取ったarchiveは128 KiB以下、既知member一つ、展開64 KiB以下。外部artifact hostへのredirectにAuthorizationを転送しない。
最終gateもpushの証明を再検証する。

post-mergeでも実commitの構文、設定、基本単体、JS、全discovery、secretsを確認し、CodeQLは実commitで再解析する。
docsは実main commitで再buildし、既存のGitleaks/公開内容検査とgh-pages公開処理を維持する。今回docsの公開タイミング・権限は変更しない。
PR/runのconcurrency取消は追加しない。別PR・main・develop・実行中の製品ジョブをこの変更で停止しない。

## 移行（ここでは実行しない）

1. 初期差分は`develop_mode: transition-full`。develop向けPRも従来の全4環境・全件を保ち、新gateを並行導入する。
2. 別途承認を得てこの段階のPR/CIを実行し、全4つの既存contextと新gateの発行元・成功を確認する。欠落/重要job skip/cancel/failureが新gateを確実に失敗させることも、承認された小規模CIの制御試験で確認する。ローカル試験をGitHub実証と扱わない。
3. developのRulesetに`ci-policy`を追加し、旧4＋新gateを一時的に必須にする。strict/app15368は維持する。既存PRは新developを取り込み、新base/headの入力で再検査する。
4. 新gateが成功/失敗を確実に伝える実証後、Rulesetを一回の更新で新gateへ切り替える。この時点のworkflowはまだtransition-fullなので、全4環境は引き続き実行される。required checkがない窓を作らない。
5. 別途承認したactivation patchでpolicyだけを`tiered`へ変更する。この変更自体はCI基盤変更なので全4環境・全件で検査し、新gateの下でmergeする。
6. 次の文書/Windows専用/共有変更のPRで予定job数と拒否動作を確認する。main向けPRは常に全4環境。mainへの導入は通常のユーザー確認付きdevelop→main PRで行う。

strictによりbaseが進んだら新しい仮merge入力で検査する。古いmerge SHAでのrerunは最新baseの代替にならない。
failed jobsだけのrerunでclassifier/成果物のattemptが混ざる場合もgateは拒否する。同一入力の再試行が必要なら承認した全jobの新attempt/新runで全報告を揃える。

## Rollback（ここでは実行しない）

1. 新gateを必須のままpolicyを`transition-full`へ戻すPRを作り、全4環境・全件と新gateを通してmergeする。
2. 旧4つのcontextが実際に再発行・成功していることを確認し、Rulesetに旧4を追加する（新gateをまだ外さない）。
3. 旧4の保護が有効なことを読み戻してから、必要なら旧Rulesetへ戻す。
4. 旧4が必須の状態で、元のworkflow/独立security workflowを復元するPRを検証してmergeする。新gateが必須のままそれを消す順序にしない。
5. 進行中PRはbase更新で古い検査が無効になる。新入力でのCIが必要。既存PRのclose/merge/自動取消は行わない。

## 費用・保持と未実行事項

tests workflowは基本/post-merge証明済みで4 job、Windows関連で6、全件/移行段階で16。CodeQL 3 jobとpath依存のdocs jobは別。
新しいplan/選択census/receiptはmetadataだけを1日保持する。既存全件の診断・census artifactは7日を維持する。
receipt失効時は全件に戻るため、省略率はmergeまでの時間にも依存する。新しいActions cache、Packages、custom image、larger runnerは追加しない。
artifact storageはActionsのストレージ枠を使うので、将来CIの有効化前にその時点の使用量・保存枠を確認する。本準備ではCIを起動せず、課金/容量/認証/権限設定を変更しない。
GitHubでの実行時間・merge時のreceipt取得・Ubuntu実機・新required checkの運用は未検証。ローカル結果だけで適用済み/CI成功とは記載しない。

PR #28は同じ`tests.yml`の全件stepに計測envを足すため競合確認が必要。今回その4 helperと`regression_suite_capture.py`の変更は取り込まない。
後日採用する場合、full shardへの計測適用範囲、Windows関連runnerが別入口であること、census形式・負荷・artifact量を再レビューする。

参考: [required checksのskip](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks)、
[needs/always](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-jobs)、
[PR/pushのevent入力](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)。
