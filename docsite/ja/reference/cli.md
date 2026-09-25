---
sources:
  - "scripts/evolve.py"
  - "scripts/synopsize.py"
  - "scripts/narrate.py"
  - "scripts/random_baseline.py"
  - "scripts/readable.py"
  - "scripts/world_patch.py"
  - "scripts/world_demand.py"
  - "scripts/export_static.py"
  - "scripts/pack_samples.py"
  - "viewer/server.py"
reviewed: "72aaeae8379271e99357be2f967fa0cff3901a8d"
---
# CLI

すべて `python <スクリプト> ...` の形でリポジトリ直下から実行します。ヘルプ全文は各スクリプトの `--help` から生成しています（`docsite/gen_cli.py`。スクリプト側の引数を変えたら再生成してください）。`jev_*.py`・`rationality_probe.py` は開発用の計測スクリプトなのでここには含めません。

## scripts/evolve.py — GA 実験を実行する

決定論的な MAP-Elites を1回走らせます。`--project`（世界）・`--template`（ジャンル）・`--out`（出力先）が必須です。

```
--8<-- "cli/evolve.txt"
```

例:

```powershell
python scripts/evolve.py --project projects/momotaro_plus2 --template templates/momotaro_plus2 `
  --out C:\WorldBloom-data\runs\my-exp --generations 20 --population 100 --seeds 3
```

出力先は Google Drive 上のリポジトリではなく、ローカルの `runs/<experiment>/` に置いてください（詳しくは[実行結果のファイル](run-outputs.md)）。

## scripts/synopsize.py — あらすじを生成する

`archive.json` の各セルからあらすじを生成します。`--cells` を省略すると全セル、指定すると該当セルだけを更新し他のエントリは保持します。

```
--8<-- "cli/synopsize.txt"
```

## scripts/narrate.py — 本文を生成する

`selection.json` で採用したセルだけを本文化します。`--synopses` を渡すとあらすじの内容をプロンプトに含めます。

```
--8<-- "cli/narrate.txt"
```

## scripts/random_baseline.py — 無作為基準を測る

方針（遺伝子）を持たない完全無作為な個体だけで、結末への到達率と多様性を測る対照実験です。

```
--8<-- "cli/random_baseline.txt"
```

## scripts/readable.py — 読者向け要約

`generate` でLLMによる短い要約を作り、`approve` でその成果物（sha256付き）を人が承認します。

```
--8<-- "cli/readable.txt"
```

## scripts/world_patch.py — 世界の自己拡張パッチ

`propose`（提案）・`check`（追試）・`approve`/`reject`（承認・却下）・`list`/`reopen`/`repair`（一覧・差し戻し・修復）のサブコマンドを持ちます。詳しくは[世界を作る](../usage/create-world.md#world-expansion)を参照してください。

```
--8<-- "cli/world_patch.txt"
```

## scripts/world_demand.py — 世界拡張の需要レポート（読み取り専用）

実験ディレクトリを読み、空振りが多い場所・行動から不足している要素を報告するだけのCLIです（`world_patch.py propose` の下敷き）。

```
--8<-- "cli/world_demand.txt"
```

## scripts/export_static.py — 公開用の静的HTML書き出し

実験結果を読み取り専用の静的サイトとして書き出します（一般公開用）。

```
--8<-- "cli/export_static.txt"
```

## scripts/pack_samples.py — ビューア用サンプルの抽出

実験の実行結果から、配布版ビューア（Viewer exe）が読める最小限のサンプル一式だけを別フォルダへコピーします。

```
--8<-- "cli/pack_samples.txt"
```

## viewer/server.py — 画面を起動する

`--control` を付けない既定状態では、書き込み系のAPIはすべて拒否される閲覧専用として動きます（詳しくは[HTTP API](http-api.md)）。

```
--8<-- "cli/server.txt"
```

例（フル機能で起動）:

```powershell
python viewer/server.py --runs C:\WorldBloom-data\runs --control C:\WorldBloom-data\control
```
