---
sources:
  - "viewer/data.py"
  - "gapengine/evolve.py"
  - "gapengine/qd.py"
  - "scripts/synopsize.py"
  - "scripts/narrate.py"
  - "execution/configs.py"
  - "execution/jobs.py"
  - "execution/output_store.py"
reviewed: "72aaeae8379271e99357be2f967fa0cff3901a8d"
---
# 実行結果のファイル

`python scripts/evolve.py --out <実験フォルダ>` を実行すると、`<実験フォルダ>` 配下に以下が生成されます（実際に `momotaro_plus2` で1世代・2個体・1seedを回した実測、`C:\Projects\WorldBloom-local\runs\docs-phase3\` 配下）。

```
<実験フォルダ>/
  archive.json          # QD アーカイブ（到達したランの中で各セル最良の1本）
  summary.json          # 世代ごとの集計（到達率・占有マス数など）
  ga_state.json         # --resume 用のGA内部状態（乱数状態・完了世代数など）
  g<N>/
    population.json     # その世代の全個体（遺伝子・親）
    precedent.json       # その世代の前例表（文脈→行動の観測件数）
    results.json         # その世代の全ランの結果（個体×seed）
    ind-<i>/seed-<s>/layers.jsonl   # 1ラン分の生ログ（--keep all のときのみ全個体分）
  prompts/               # synopsize.py / narrate.py が保存した生成用プロンプト
  synopses.json          # synopsize.py の出力
  selection.json         # 画面で人が選んだ採用・保留・除外
  stories/               # narrate.py が書いた本文（採用したセルのみ）
```

## `archive.json`

QD（MAP-Elites）アーカイブ本体。

```json
{"cells": {"I|low": {...}}, "volatility_thresholds": {"low_max": 0.216, "mid_max": 0.226}}
```

`cells` のキーは `"<主導カテゴリ>|<起伏区分>"`（例 `I|low`）。空の実験では `{}`。各セルの値は到達したランの中でその区画に残った最良1本のエリート（遺伝子・quality・descriptor 等。`gapengine/qd.py` の `Elite`）です。`volatility_thresholds` は世代0の母集団の分散から決めた `low_max`/`mid_max` の境界値で、実験全体を通して固定されます（[QD 格子](../concepts/qd-map.md)を参照）。

## `summary.json`

```json
{"generations": [{"generation": 0, "reach_rate": 0.0, "occupied_cells": 0,
                   "action_share": {"I/craft/none": 0.5, ...}, "allies_mean_final": 2.0,
                   "archive_dissimilarity": null, "average_archive_quality": null}],
 "seed_base": 0, "seed_count": 1, "seeds": [0],
 "target_ending": ["homecoming", "homecoming_shared"], "keep": "reached"}
```

世代ごとに1エントリ。`reach_rate`（結末への到達率）・`occupied_cells`（アーカイブの占有マス数）・`average_archive_quality`（q̄）・`archive_dissimilarity`（相異度）などを追跡します。序盤の世代は到達ゼロのため多くの値が `null`。

## `g<N>/results.json`

その世代の全ラン（個体×seed）の結果配列。1件は概ね次の形です。

```json
{"index": 0, "generation": 0, "genome": {"category_weight": {"I": 0.18, "II": 0.86, "III": 0.78},
  "novelty_drive": 0.09, "risk_tolerance": 0.65, "stance_shift_bias": 0.58},
 "parents": [], "reach_rate": 0.0,
 "runs": [{"cell": null, "classification_status": "not_reached",
           "category": "III", "effective_sequence": [["I","investigate","none"], ["III","give_item","neutral"]],
           "allies_final": 2, "contest_turn": null, "allies_at_contest": null}]}
```

`classification_status` は到達したかどうか（`reached`/`not_reached`）。`effective_sequence` は実際に7層へ非零の影響を与えた行動だけを並べた列（`[カテゴリ, 動詞, 役割]`）です。

## `g<N>/ind-<i>/seed-<s>/layers.jsonl`

1ラン分の生ログ。1行=1イベント（JSON Lines）。先頭行は `kind: "header"`（遺伝子・エンジンハッシュ・前例表ハッシュ・世界名など）、以降は決定イベント（主体の行動）・派生イベント・世界イベント（`daily_event` 等）が続きます。実測例:

```json
{"kind":"header","genome":{"category_weight":{"I":0.18,"II":0.86}},"engine_hash":"6ba7089b2241","precedent_hash":"b032ca...","world":"桃太郎＋2","protagonist":"桃太郎","antagonist":"鬼","seed":0}
{"turn":1,"day":1,"slot":null,"subject":"桃太郎","verb":"daily_event","id":"sudden_storm","result":"applied","delta":{"actor":{"stress":0.7},"objective":null,"relations":[],"targets":{}}}
```

各行の `delta` はその行動前後の7層の差分です。決定論の検証は「同じ `(world, genome, seed, precedent)` なら `layers.jsonl` がバイト一致すること」で行います（[決定論](../concepts/determinism.md)）。分析用途では JSONL → Parquet（DuckDB）に変換します。

## `synopses.json` / `selection.json` / `stories/`

`synopsize.py`・`narrate.py`・画面の3点は次のように連携します。

- `synopses.json`: `{"entries": [{"cell": "I|high", "generation": 3, "seed": 0, "layers_path": "g3/ind-31/seed-0/layers.jsonl", "quality": 0.36, "reach_rate": 0.33, "synopsis": "…", "status": "ok"}], "backend": "ollama", "archive": "…/archive.json"}`
- `selection.json`: `{"selected": ["III|high", "I|low", "V|mid"]}`（セルキーの配列。画面の✔採用と対応）
- `stories/<cell>.md`: 採用したセルごとの本文。`stories/index.json` に一覧が入ります

## `runs/` と `control/` の違い

- **`runs/<experiment>/`**: `scripts/evolve.py`・`synopsize.py`・`narrate.py` が直接読み書きする生の実験データ（上記の構造）。CLI から直接実行しても、画面から実行しても同じ場所・同じ形式です。
- **`control/`**（`viewer/server.py --control` で有効化）: 画面がジョブ管理のために持つ状態。`control/configs/<config_id>/` に画面で保存した実行設定、`control/jobs/` にジョブ（実行中・完了）の状態、`control/outputs/<output_id>/` に生成ジョブのリクエスト・プロンプト・応答（`request.json`・`item.json`・`prompt.txt`・`quota.json`）が入ります。`--control` を付けない閲覧専用モードにはこのフォルダ自体が存在しません。
