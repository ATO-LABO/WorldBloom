# 文化祭・可変時間・具体的約束：段階1〜3レビュー版

基点は PR #11 の `3be1f75eb56540cb6c57f0ed7db72302dd6e2a4e`。
既存 PR #4/#5/#8/#9/#10/#11、main、develop は変更・統合していない。
この差分は PR #11 上の独立 draft としてレビューする。PR #9 の画面/backend
をこの枝へ取り込まず、その保存 JSON を明示的な別 family の実行版へ渡す。
本人のローカル Codex による既存 PR 統合は前提作業として残る。

## 実装と受入の対応

| 段階 | 実装した範囲 | 境界 |
|---|---|---|
| 1 | 元4人の YAML と性格・目的・初期知識/所持品を保持。悠（実行委員）、実（放送）の2人、学校の授業・担当工程・場所・材料・依存工程を明示 | 追加2人の性格、工数、P1下地完成済み等は本人レビュー前の仮定 |
| 2 | PR9 schema1 設計 JSON→新しい固定 v2 project→実 Simulation。整数分の移動/作業、複数特別日、日跨ぎ状態/知識/関係保持、31暦日 | 旧 v1 を移行しない。UI での一体操作は既存 PR 統合後の残件 |
| 3 | 誰が誰へ・何単位・どこで・いつまでの約束、同一 terms hash 全員受諾、実作業/手渡しによる部分/履行/違反、変更案への全員受諾、受益者の選択した依存計画と実負担 | 追加前提節/外的障害の因果分類は unsupported。公演内部、共同運搬/速度合成は未実装 |
| 4 | 未着手 | GA policy/codec を v2 に渡すと明示拒否。探索をこの受入 witness と混同しない |
| 5 | 未着手 | 作品本文・文学的内面生成は含めない |

新モードは既存の World・Subject・Relations を共有し、実位置・知識・関係を
別オブジェクトへ複製しない。新しい物理作業と約束の ledger のみ追加する。
初期4人の原 YAML の hash も実行入力に含める。仮定は contract と layers header に保存する。

## 実行

Python 3.11以上、既存 requirements.txt（PyYAML）のみ。GPU・LLM・外部 API は使わない。
出力は checkout 外の新しい空ディレクトリを指定する。

```sh
python scripts/cultural_festival_v2.py --case fulfilled --out /tmp/wb-full
python scripts/cultural_festival_v2.py --case partial --out /tmp/wb-partial
python scripts/cultural_festival_v2.py --case renegotiated --out /tmp/wb-revised
```

この3ケースは、同じ世界/人物/初期条件に対する **明示行動入力の受入 witness**。
探索・自律的に選ばれた物語・欠席率を保証した作品ではない。
既定は準備2暦日+文化祭1日（2026-10-13〜15）。fixture は準備1〜30暦日を作成できる。
汎用 calendar/runtime は31暦日を実際に進行する。付属の学校 witness の時刻表は
既定の平日初日/最終準備日に合わせた確認用で、任意の休日境界向け行動自動生成は残件。

PR9で保存した JSON を明示的に新しい実行版へ渡す場合：

```sh
python scripts/cultural_festival_v2.py --design /tmp/calendar_design.json \
  --freeze-to /tmp/wb-fixed-project --case fulfilled --out /tmp/wb-fixed-run
```

元 design の engine_supported/execution_ready flags を書き換えず、calendar_design.json
へ保持する。新しい execution-version.json は別 family の実行可能性、設計 id/世界版、
compiled calendar、全入力ファイル/元4人の hash、明示した文化祭予定移動を固定する。
既存 project/出力の上書き、固定版 sidecar 欠落、hash違い、重複 JSON key は拒否する。
設計の保存は開始ではない。CLI 実行は新しい明示的な開始操作。

```sh
python scripts/cultural_festival_v2.py --case fulfilled --out /tmp/wb-split --until-minute 2403
python scripts/cultural_festival_v2.py --case fulfilled --out /tmp/wb-split --resume
```

`calendar-state.json` が唯一の正本。atomic replace 前に fsync、実行全体で writer lock。
layers.jsonl/summary.json は正本から再生成する。source（engine、CLI、保存 helper）、
world/contract/calendar/人物/初期関係、seed、choices、Python版、OS の一致を要する。
異なるOS間の再開は拒否する。RNGを新モードで消費せず既存 RNG state を保存する。
追加停止点の浮動小数点結合差を避け、staminaの経過時間演算は有理数の正本を持つ。
破損/不整合の検証完了後にのみ既存人物へ復元する。

## 実際の比較

| ケース | 蒼が実際に行う作業 | 約束結果 | 灯の代替作業 | 灯の受付札残り |
|---|---|---|---:|---:|
| fulfilled | 見出し20分、カード40分、写真2分、手渡し2分 | 原版 fulfilled | 0分 | 0分 |
| partial | 見出し20分、カード20分、その後部室へ移動 | 原版 breached、蒼のカード寄与2/4 | 20分 | 30分 |
| renegotiated | 昼に新条件へ双方受諾、見出し20分、カード20分 | 旧版 renegotiated、新版 fulfilled | 20分 | 0分 |

再相談によって過去の履歴や灯の負担を消さない。灯は自分の prior_plan と新版 plan を保存する。
約束は作業を自動生成せず、資源も予約しない。実作業は同じ机を排他的に使い、材料を一度だけ
消費し、工程依存を検査する。写真の約束は写真生成だけでは履行せず灯への実際の手渡しを要する。
蒼の決定行には既知の情報・候補・選択入力、行動/物理効果の因果 id と未完了分が残る。

14:50出発・10分移動は15:00到着として開演時の在席に間に合う。15:00出発は15:10着。
到着/実作業完了→施設/予定→期限評価→約束操作→再評価→開演観測→既読→次の選択の順。
在席、相手役の在席、準備工程、公演への同意を区別し、在席だけで公演を成功扱いしない。

夜間の宛先通知は受信と翌6時既読を分ける。睡眠中の未観測違反で関係を変えない。
伝聞は観測した元 event の snapshot と伝達経路を渡し、現在の隠れた進捗を漏らさない。
依存計画は offer の認識だけでは足りず、全員受諾の観測を要する。
信頼に対応する既存有向 affinity は受益者が履行/違反を観測したときだけ固定±.05。
理由は choice_or_unconfirmed の範囲であり、悪意や外的障害を推測しない。

## 検証と残件の扱い

```sh
python -m unittest tests.test_calendar_v2 tests.test_promises_v2 tests.test_calendar_runtime tests.test_slot_runtime
python -m unittest discover -s tests -v
python docsite/check.py
```

対象試験は real minutes、登録時刻/優先規則、不正入力、at/by 境界、同時行動、締切丁度、
条件変更、受信/既読、旧snapshotの伝聞、資源競合、夜間の関係非更新、任意分での分割再開、
writer競合、atomic失敗、破損の無変更拒否、31日進行、ネットワーク禁止を含む。
最終 head の全回帰/4 CI/clean ZIP 復元の結果は成果資料で別途記録する。
PR11以前のWindows内部原因は引き続き未確認。今回の成功から原因解消を推定しない。

段階1〜3のこの機構をレビューに提示した時点で停止する。段階4/5、AIT136の外部Jev/fitness、
AIT137共同作業、一般GUI全体/系譜は混ぜず、今回のDoneに含めない。
