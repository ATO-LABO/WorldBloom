# 文化祭 行動選択codec GAアダプター 検証報告

**最小CPUアダプターを実装し、4個体×2世代×1評価seedを試走。8個体で8つの異なる行動列。**
同じGA seedで独立再実行し、全8 native layersとArchiveがバイト一致した。世界・人格・目的・初期状態のhashは全個体同一。
GAは実際の親選択・交叉/変異を行う。4本の筋を手書きして再生するものではない。Jev呼出は0件。

## 既存GAの再利用と変更範囲

`gapengine/evolve.py` の `_evolve` / `run_individual` に明示的な search_adapter 分岐を追加。
個体評価 `_evaluate_jobs`、親プール `_parent_pool`、親選択 `_choose_parents`、世代生成 `_next_population`、
`qd.Archive/Elite`、RNG状態・atomic checkpoint writerを再利用する。
既存の人格 Genome を使う呼出はデフォルトのまま。codec指定は新adapterだけ。
`qd.Elite/Archive.from_dict` は明示的なchoice codecでround tripでき、既存のGenome読み込みを保つ。

追加module: `festival_choices.py`（choice codec/RNG/Policy wrapper）、`festival_ga.py`（完了評価/既存GA adapter）。
全4人の既存主観Policyをfactoryで作り、同じ native Simulation/SlotRuntime を実行。別エンジンへ置き換えていない。
run_individualは既存のworld_path/subjects_dirを使い、同じ凍結projectのcopyを評価できる。期待入力hashが違う場合は失敗する。
Linux/経路/UI/job管理のファイルは変更・マージしていない。CLIの単一CPUプロセスで実行。

## genotypeと人格の分離

codecは88個のuint32（4人×22時点）の選択quantile。語彙はactor/tickと数値のみで、literal actionや物語の筋を保存しない。
各時点で本人の実状態・知識・固定目的から合法候補と同一のutility重みを計算し、そのCDFへgeneと評価seedを写像する。
同じgeneでも到達状態/知識が異なれば選べる行動が変わる。人物traits・目的・utility係数を変異させない。
codecは人格/rule/growthパラメータを拒否する。全runでtraits不変と初期パラメータhashを検証。

各runの `choice-draws.json` にgene、seed shift、quantile、選択index、候補数、固定utilityの正規化重みを保存。
本実験と再実行の計1,408選択について、indexから実ログのactionへ対応付け、CDF区間と選択を確認した。
ログのchoice_probは固定utilityの正規化重みであり、固定されたgenotype下のランダム確率とは区別する。

## 次境界の実完了を読む評価

`(subject,start_t)` で開始行とaction_completedを1対1対応。verb/args/完了tick/row.turn/重複/未完了を検証する。
startをeffectiveと書き換えない。実完了したnative delta/活動deltaがあるものから新effective_sequenceを生成。
QDは完了した行動の主要群（coordination/preparation/exhibition/movement）と、tickごとの実効果変動を使う。
既存のArchive保存・セル比較を使うが、旧decision-only descriptorを無理に合格扱いしない。

fitnessは監査/目的進捗の明示的heuristic。重みは、15時在場0.10、衣装0.20、練習0.15、音響0.15、
蓮在場0.05、出演了承0.10、台詞変更了承0.10、蒼の展示協力0.05、実学習0.10。
各項を別に記録し、出演未回答(null)を了承(False)にしない。協力回数をパネル完成にしない。
欠席への加点は0。その他を固定して在場をfalse→trueにした評価は0.10だけ増えることをテストした。
`reach_rate`は15時primary在場のみ。準備/回答/失敗はopeningとfitness_componentsへ別記する。
Jevのplausibility、演技実行成功、文学品質の認定ではない。

## 少数試走の実測

| 世代 | 個体 | fitness | 15時分類 | 親数 |
| --- | --- | --- | --- | --- |
| 0 | 0 | 0.833333 | present | 0 |
| 0 | 1 | 0.783333 | present | 0 |
| 0 | 2 | 0.833333 | late | 0 |
| 0 | 3 | 0.933333 | present | 0 |
| 1 | 0 | 0.683333 | present | 2 |
| 1 | 1 | 0.883333 | present | 2 |
| 1 | 2 | 0.833333 | present | 2 |
| 1 | 3 | 0.783333 | present | 2 |

在場7本・遅刻1本。3種の欠席理由や欠席数を要求していない。全8runで88開始/88完了・知識監査違反0。
次世代4個体には実際の親由来ラベルがある。Archive4セル。全8行動列は異なる。
最良fitnessは初代の0.933333がArchiveに残る。2世代で品質向上を証明したとは扱わない。

Source SHA-256: `e2e638c1022b17fc1c2f0fcb406ca81f83c2c69df608e5331f29572a1d97325e`。対象55本を全て同梱する。
基準パターン0・限定8・状態4も同じ最終ソースで再実行。candidate07は在場/未回答/null、pattern0_pass=false。
既存4世界のログも再比較し、engine source hashを除いて基点と一致。
最終codeで文化祭/状態/GA codecの24テストと既存GA50・再開28、計102テストを実行し、生stdout/stderr/exit codeを保存。
クリーン再構成で同じ102件、Source/Input hash、GA全8log/Archiveを再確認し、その結果を復元証拠へ保存する。

## 再現方法

```sh
python3 scripts/cultural_festival_ga.py --out /absolute/new-output --population 4 --generations 2
python3 -m unittest discover -s tests -p test_festival_ga.py -v
```

少数上限はpopulation2..4、generations1..2、評価seed1..2、processes1。外部judge/growthは拒否する。
既存の人格GAは通常codecを維持。Choice Archiveの復元は `Archive.from_dict(..., genome_codec=ChoiceGenome)` を明示する。

## 未対応・設計に留めた範囲

Choice実験のCLI resume/GUI/lineage表示は未実装（checkpointのcodec round tripは検証済み）。Windows job管理も未統合。
Jevは本人の目的・時計・準備/了承の既知/未知を扱うformatter/cache schemaが必要だが、この段階では設計のみ。
実Jev/有料API/LLM/GPUは使わない。
展示完成に必要な作業量は追加せず、協力の観測だけ。共同運搬2枠/安全中断と公演内部の演技は未実装のまま。
このadapterは凍結世界の変更を必要としない。制御/codec/評価層だけを変更する。
大量探索・統計的品質・Jevによる選定は未実施。4個体2世代を超えて増やしていない。
ローカルstaged変更のみ。push/PR/merge/公開なし。
