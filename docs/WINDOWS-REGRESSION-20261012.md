# Windows の既存回帰2件の観測

PR #10 の `eb2b452be269d9a08bf4b92c65450f40c2e3c97f` を固定基点にした
独立の診断変更。既存 PR #8、main、develop、Choice 再開の作業枝は変更しない。

確認済みの未解決は、Windows 3.11 の epoch-chain `process_failed` と
Windows 3.13 の clipboard Node subprocess 15秒 timeout。前者は Choice
変更前の PR #6 でも同じ症状が先行し、後者は PR #8 の既存失敗と同じ
ソース・症状だった。内部原因はまだ確定していない。

## 観測範囲

`WB_REGRESSION_DIAGNOSTICS=1` で epoch-chain のテストコピー内だけに
子 stderr と準備例外の観測を入れる。入力・runtime を固定する前に
コピーへ適用し、同じコピーを実行する。本番 worker を変更しない。
テストの成否、580秒の driver 上限、550秒の chain 上限、実GA設定と
自動承認・2epochに関する assertions を維持する。

一時ディレクトリを消す前に、driver の段階・経過時間・終了コード、
job の phase/error/exit_code と runtime/launch/prepared/event 情報、
所有する子プロセス stderr を外部の `WB_REGRESSION_EVIDENCE_DIR` へ残す。
保存対象と抜粋には件数・byte上限があり、切り詰めたことも記録する。

clipboard は Node entry、runtime/executable、vm、各 await、assertions完了、
自然な beforeExit の段階と uptime/active resource名を stderr へ記録する。
Python は15秒上限を保ち、TimeoutExpired の捕捉出力を安全に復号して
失敗へ含める。timer、force exit、自動 retry は追加しない。

## CI

元の4構成・contents read権限・45分ジョブ上限を維持する。
Windowsでは全回帰の前に対象2件を各1回、後に元の対象2件を各1回観測する。
固定回数で単独／suite条件を比較し、観測probeの結果と全回帰の成否を
別々に記録する。全回帰の実際の終了コードをCIへ返す。

初回観測head e50b747の全4構成・固定probeは成功したが、元の原因は
未確定。stderrの出力先変更による影響を分けるため、次の固定比較では
全回帰と後段epoch probeのコピーworkerを元のbytes/DEVNULLのままにする。
前段だけは捕捉コピーを使う。後段clipboardは固定基点eb2b452の元harnessを
そのまま取得し、同じ15秒上限・assertionsで実行する。

全回帰ログと診断資料は always artifact に7日保存する。Node段階ログと
失敗時の子stderr抜粋はCI本文にも残す。診断を追加した後の成功だけで
元の不具合の原因が解決したとは扱わない。失敗段階と根拠に沿って次の
最小修正を判断する。

GPU・有料サービス・認証/権限変更・外部LLMは使わない。epoch-chainの
LLM生成は従来のテストと同じコピー内stubを使用する。Choice GUI全体、
新しいfitness/engine、30日モデルへの拡張はこの変更に含めない。
