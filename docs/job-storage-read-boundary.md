# ジョブ監視と保存ロック

`worker.change`は共通のジョブ保存ロック内で`job.json`をfsync・原子的に置換し、`events.jsonl`を追記・fsyncする。旧実装のGET監視も同じ書込みロックを必要とし、保存中に2秒以内に取得できなければHTTP 409を返した。

回帰試験では別プロセスの実際の`worker.change`のfsyncを停止し、取得待ちのPIDと保存側PID、同じロック識別子、保持期間を計測した。ジョブ本体のfsync中と履歴のfsync中の両方で旧GETの409を再現した。過去のCIにはこの計測がなく、当時のロック所有者やOS側の遅延原因まで特定した証拠とは扱わない。

現在のGETは、一度の原子的な読取りから完全な保存版を返す。キャッシュや409への代替応答は使わない。通常の稼働中、状態確認が不明なジョブ、確定した終了状態の読取りには書込みロックを取らない。Windowsでは`FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE`を付けて読取専用ハンドルを開く。`job.json`の保存側には`FileRenameInfoEx`の`REPLACE_IF_EXISTS | POSIX_SEMANTICS`を使い、読取側が開いた旧版を保持していても置換できるようにする。閉じたときにファイルを削除する設定は使わない。他の保存ファイルの置換方式は変えない。

このWindows APIは対応するWindowsとファイルシステムを必要とする。動作環境での実API試験とWindows CIで確認し、未対応や権限のエラーは明示的に失敗させる。APIの動作はMicrosoftの[FILE_RENAME_INFORMATION](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntifs/ns-ntifs-_file_rename_information)と[FILE_RENAME_INFO](https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_rename_info)を参照。ファイルを置換できないときは旧版を保持し、一時ファイルを片付ける。親側の既存6回の有限再試行は維持し、制限時間は延ばさない。

停止・期限切れ・未起動・消失など、整合処理が保存や検証済みプロセスの停止を必要とする場合は、従来の書込みロックを取得する。その内側で最新版を読み直す。観測後にworkerが受付を確定した場合、古い未起動状態で上書きしない。キャンセル、新しい要求の受付、出力復旧の書込み経路はロックを維持する。ロック競合や権限エラーを一律に無視せず、入力・workerソースの整合性検証も省略しない。

計測は`WB_REGRESSION_DIAGNOSTICS=1`かつ`WB_REGRESSION_EVIDENCE_DIR`がある場合だけ有効。`storage-locks/<pid>.jsonl`へPID、スレッド、時刻、操作名、ロック取得待ち・保持時間、OSエラー番号を記録する。パスはハッシュで照合し、設定・要求・本文・認証情報は出力しない。計測先への書込み失敗はジョブ本体の成否を変えない。

再現・確認は`python -B -m unittest discover -s tests -p test_job_storage_reads.py -v`。実書込みのfsync中に旧版／新版の完全なJSONを200で読めること、解除後の履歴保存、Windowsで開いた旧版の置換、遅い受付の再読込、未起動の保存、改ざん拒否を確認する。全体試験の成否は対象コミットのCI結果と別途保存した検証記録を参照する。
