# 暦・約束の汎用入力への再設計（2026-10-13）

PR12 `3d3397c28874dfeb0165784101e4f8ee8bfd6b58` を基点とする独立修正。
main/develop、既存PR、本人のPC・作業中ブランチ・旧runを変更しない。
行動探索（段階4）と文学作品生成（段階5）は範囲外。
作業枝は`codex/calendar-project-inputs-20261013`。依存差分のレビュー先は
PR12の枝とし、developへの統合・依存PRの取り込み順は別の承認済み作業で扱う。
2026-10-13 02:46 UTCのremote確認で、mainはPR8 mergeの`aba1aa4`、
developはPR13 branch workflow追加の`180c549`へ進んでいる。
当修正へは未統合。PR12はdraft/open、head `3d3397c`のまま。
最新の`docs/BRANCHING.md`を参照し、新規作業はcodex枝とするが、今回の
PR12固定基点とmerge禁止の承認範囲を変えない。

## 固定名が残った原因

PR12は文化祭の機構受入確認を先に実装した際、fixtureの固定版検証を
共通runtimeへ持ち込んだ。固定5ファイルと元人物ディレクトリの照合、
必須play、主役・相手役の観測、文化祭CLIのsource hashが共通層に残った。
CLIの`--project`も元4人のglobal directoryと追加2人のglobを読むため、
任意の世界・人数を表していなかった。時計・約束の中核は人物IDを受け取るが、
入出力の境界が文化祭fixtureと結合していた。

## 新しい境界

`engine/calendar_inputs.py`が明示したworldファイルからprojectを読む。
`slot_execution`にはmode、契約への相対path、`subjects:[{id,path}]`を記す。
人物名・人数・配置・traitsは宣言したYAMLが正本。人物の暗黙追加、glob、
元directory参照、loaderでの行動範囲の拡張は行わない。
共通Simulationへ別人物objectを渡しても、宣言した正本との矛盾を拒否する。
汎用loaderは検証したYAML正本からWorldとSubjectを直接構築する。
同size・同mtimeの更新でも古いlegacy YAML cacheを再利用せず、契約と
宣言graphもstrict parserで読む。初回全回帰はこの問題の再現後にSIGINTで
中断し、その途中記録を保持する。修正後の最終ソースで全回帰を実行する。

契約version3はplayを受け付けず、任意の`observers`を省略できる。
観測は登録済みscheduled eventに結び、`at`／`task_complete`の事実、
グループ、明記したliteralだけを出力する。上演・意思・動機・物語結果を
自動推論しない。観測ID・項目・参照・深さ・件数を検証する。
結果は`observations`辞書で保存し、checkpoint再開時は emitted eventと照合する。

固定版manifest schema3は、実際のworld・契約・全宣言人物・宣言した
project内action graphの入力closureをsize/SHA-256付きで記録する。
追加・欠落・改変、重複ID/path、参照逸脱、symlink・衝突、旧schemaを拒否する。
新規保存先だけに作り、manifestを最後にatomic公開し、部分作成の失敗を
未完成固定版として実行しない。元入力は変更しない。
manifestには元worldのUTF-8正本も含め、元size/hashと、fixed_versionだけを
加えたsnapshotの定義が一致することを検証する。外部designの存在を示す
任意metadataは提供者の来歴メモであり、その外部内容の実在は保証しない。

checkpoint互換schemaも3へ進める。共通engineと汎用CLIのsource hash、
入力closure、選択列、seed、Python/OSを照合する。
文化祭CLIを変更したこと自体を全世界の再開条件にしない。
旧schema2のcheckpoint・固定版を黙って移行せず、旧checkoutで保持する。
JSONL event codec schema2は継続するため文化祭の旧実ログとのbyte比較が可能。

## 利用

```sh
python scripts/calendar_sim.py --world projects/calendar_workshop_example/world.yaml \
  --freeze-to /tmp/new-workshop-fixed
python scripts/calendar_sim.py --world /tmp/new-workshop-fixed/world.yaml \
  --choices projects/calendar_workshop_example/choices.json --out /tmp/new-workshop-run \
  --until-minute 1442
python scripts/calendar_sim.py --world /tmp/new-workshop-fixed/world.yaml \
  --choices projects/calendar_workshop_example/choices.json --out /tmp/new-workshop-run --resume
```

freezeは保存だけで停止する。run/resumeは別の明示操作。
文化祭CLIは文化祭専用のadapter/sampleとして残り、fixture生成、PR9日付設計の
school fixtureへの明示transfer、3つの手入力witnessを担う。
元4人の新project内コピーで追加zoneを明記するが、元YAMLは保持する。
共通層に学校・体育館・主役・作品の条件を戻さない。
手渡しもtaskのrolesとminutes_per_unitに従う。材料消費付きの手渡しは
未対応として拒否し、材料を消費する制作工程はworkで定義する。

## 検証と残件

検証対象は、公演なしの2人の夜間工房、別名・別場所の3人world、
数量・手渡し・約束の評価、日跨ぎ作業途中checkpoint、連続実行との
全出力・人物・関係・RNG一致、不正設定と固定版改変の拒否、文化祭3例の
既存実ログbyte回帰、旧ChoiceGA、全Linuxテスト、復元checkoutでの再開。
実施結果と最終commit/CI/保存IDは完了時に追記する。
最終実装の暦関連136件は成功（Linux Python3.12.14、41.693秒、skip0）。
文化祭3例はPR12の実ログと全byte一致、summaryもopeningをobservationsへ
移す明示的schema対応後に全項目一致した。工房の25件とloaderの19件は
別世界・宣言入力・夜間再開・改変拒否・同size/mtimeキャッシュ回帰を含む。
全Linux、4構成CI、復元、外部保存の最終結果はこのcommit時点では未確定。

PR12のWindows3.11 HTTP進捗テスト409は原因・ロック所有者未確認の残件。
新修正で解決済みと扱わない。timeout延長、assert緩和、再実行で隠さない。
統合GUI、任意人物の自律行動選択、探索、作品本文、共同運搬、公演内部は
今回の汎用入出力修正によって完成したとは扱わない。
