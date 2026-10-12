# Choice GA の世代単位checkpointと再開（AIT-135）

正式公開ソース [PR #6](https://github.com/ATO-LABO/WorldBloom/pull/6) の
`78117c84d0059d533d43a9a202a2961cc216313c` を固定基点にした独立変更。
元の統合復元ZIPはこの環境では転送403で未取得。この変更はZIPの復元や
代替取得ではなく、取得できた正式ソースへの実装であり、未取得資料との
バイト同一性は主張しない。既存PR #4/#5/#6、develop/PR #8、mainは変更しない。

## 対応範囲

世界・4人物・初期条件・既存fitnessを固定し、88個のuint32行動選択quantile
`festival_choices_v1` だけを進化させる既存CPU adapterを再開できる。
ChoiceGenome、重み付き合法行動へのquantile写像、交叉/変異の乱数消費順は
変更しない。旧人格GAのcheckpoint処理は変更しない。

populationは2〜4、seedsは重複なしの1〜2整数、processesは1。
一括と2世代＋2世代の比較のため、合計世代数の範囲を従来の1〜2から1〜4に
広げた。Jev、人格成長、別fitness、任意の計算設定は受け付けない。
Choice GUI全体、系譜UI、時間設定UIの実行版・変動時間・約束・設計版復元UIは
この変更の範囲外。AIT-135全体のDoneを意味しない。

## 実行方法

Pythonとソースを固定し、出力はcheckoutの外の新しい空パスに置く。

```sh
python scripts/cultural_festival_ga.py --out /tmp/choice-run --generations 2 --ga-seed 3
python scripts/cultural_festival_ga.py --out /tmp/choice-run --generations 4 --ga-seed 3 --resume
```

`--generations` は追加する世代数ではなく合計目標。population/ga-seedも
最初と同じ値を指定する。同じ完了世代数へ再開すると評価も乱数消費も行わない。
既存出力へのfresh実行、checkpointのない出力へのresume、保存済み世代より小さい
目標は拒否する。実行中にCtrl+Cやプロセス停止があれば、最後にcommitした世代から
再開し、未commit世代だけ再評価する。既存の世代は書き換えない。
PR #6以前のChoice `ga_state.json` は集団/前世代結果/全履歴を持たないため
この再開形式へ移行しない。旧結果は保持し、新しい出力で新形式の実験を開始する。

## checkpointと世代のcommit

`ga_state.json` が唯一のcommit marker。formatは
`worldbloom.choice-ga-checkpoint`、versionは1、adapterとcodecを明示する。
初期集団生成直後・評価前に世代0のcheckpointを保存する。
各checkpointには以下を同時に記録する。

- 完了世代、現在集団、前世代結果、全評価履歴、Archive、GA RNG。
- 合計世代目標と出力パスを除いた計算設定。
- Python実装/3要素版、ソース集合・内容、固定projectとtemplatesの全ファイル集合・内容。
- commit済み世代の全成果ファイルのsize/SHA-256とcheckpoint checksum。

評価は出力内の `.choice-pending/gN` に書く。各ファイルをfsyncし、ディレクトリを
`gN` にrenameした後、checkpointを一時ファイルへの書込・flush・fsync・replaceで
保存する。POSIXではディレクトリもfsyncする。Windowsではdirectory fsyncを
利用できないため行わない。rename後・checkpoint前で止まった `gN` は未commit扱い。
checkpoint・全commit済み成果・所有markerを検証してから、該当する未commit世代と
stagingだけを除き、同じ集団/RNGから再評価する。未知の世代や所有markerのない
staging、symlinkは削除せず拒否する。最初のcheckpoint以前の作成失敗や、staging
作成直後でmarkerが保存される前の停止は安全側で拒否し、手動確認を必要とする。

checkpointは評価後・次世代breed前のRNGを保存する。再開も一括も同じ境界で
次世代集団を一度だけ生成する。候補IDは `gN/ind-I`、seed評価IDは
`gN/ind-I/seed-S`。既存Archive親ラベルは、同じArchive exemplarの実候補IDへ
正規化する。選択や乱数には影響せず、親子参照を実候補へ結び付ける。

`archive.json` / `summary.json` / `evaluations.jsonl` はcheckpointから再構築可能な
表示用export。評価ログはseed評価IDにつき1行。append再送ではなくcommit済み
履歴からatomicに生成し、同じ内容なら書き込まない。export途中の停止は再開で
修復できる。世代/候補/seedの実ログはcommit後に再生成しない。
非決定的なCPU/wall計測をChoice評価結果から除外し、評価・結果ログも一括/再開で
バイト一致させる。元の非GA state searchの計測は変更しない。

## 再開の拒否と排他

JSONの重複key/非有限値/破損、checksum、schema/adapter/codec版、Python版、
source/input/world/template集合と内容、計算設定の不一致を拒否する。ソース変更を
許可するfallbackはない。履歴から元の乱数順・集団・候補/親ID・Archiveを再構築し、
checkpointの状態と照合する。完了世代の成果に追加・削除・変更があっても拒否する。
これらの検証は出力整理・評価・exportより先に行う。checksumは破損検知であり、
第三者による意図的改竄の認証署名ではない。

出力の隣の `.名前.choice-ga.lock` でOSの非blocking単一writerロックを取得する。
同じoutへの2本目は拒否。プロセス終了時にOSがロックを解放するので、残った
lockファイルだけで再開を妨げない。出力内の他ファイルは自動削除しない。
出力や同じ内容のprojectはパス移動できる。ソース・Python・入力内容も一致させる。
Python/OSをまたぐlayers同一性はこの検証の主張に含めない。

## 検証

`tests/test_festival_ga_resume.py` が実CPUで4個体×4世代×2seedの一括と
2世代＋再開を比較し、全layers/Archive/RNG/候補・親・評価・結果ログを照合する。
no-op、評価途中/rename後/checkpoint/export失敗、atomic書込故障、破損・入力・設定・
codec・source不一致、2writerも確認する。既存Choice試験と旧人格resume回帰は別途実行する。
最終全回帰・clean復元の数値と生ログは成果パッケージへ保存する。
