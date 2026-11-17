---
sources:
  - "scripts/evolve.py"
  - "scripts/synopsize.py"
  - "scripts/narrate.py"
  - "scripts/random_baseline.py"
  - "templates/"
reviewed: "2e70a9b216b7b2a80a4228a10a928148060e8fc8"
---

# CLI での作業手順

Studio 画面を使わず、コマンドラインだけで GA 実験からあらすじ・本文の生成まで一周する手順です。引数の一覧は[CLI](../reference/cli.md)を参照してください。

## 1. GA 実験を回す

```
python scripts/evolve.py --project projects/momotaro_plus3_new --template templates/momotaro_plus3 \
  --out <出力先>/exp1 --generations 20 --population 100 --seeds 3 --keep reached --processes 4
```

- 現行形式の同梱例は `--project projects/momotaro_plus3_new --template templates/momotaro_plus3` です。旧 `momotaro`・`momotaro_plus2`・`detective`・`romance` などはディスクに残っていますが、現行ローダーではそのまま実行できません。新規作成した世界では、その世界に対応する設定を使ってください。
- 出力先はリポジトリ外を推奨します（`.gitignore` は `runs/` を無視しますが、リポジトリ内に大量の実験出力を置くべきではありません）
- `--personality-growth` を付けると[性格の成長](../concepts/personality-growth.md)が有効になります（既定オフ）。有効かつジャンルの `qd.yaml` に `arc_bins` があれば、下記の `selection.json` のセルキーが3項（例 `I|low|small`）になることがあります
- 所要時間は世代数・個体数・seed数と世界の複雑さに依存します。まず小さい規模で動作を確認し、実測に応じて増やしてください。CLIの並列数は `--processes` で指定します。
- 完了すると `<出力先>/exp1/archive.json` に格子（アーカイブ）が書き出されます。`cells=<件数>` は結末に到達して格子に残った代表個体の数です

無作為な方針だけで結末に届く割合と多様性を測る対照実験も行えます。

```
python scripts/random_baseline.py --project projects/momotaro_plus3_new --template templates/momotaro_plus3 --out <出力先>/baseline
```

## 2. あらすじ・本文を生成する

```
python scripts/synopsize.py --archive <出力先>/exp1/archive.json --runs <出力先>/exp1 \
  --out <出力先>/exp1/synopses.json --backend ollama --project projects/momotaro_plus3_new --template templates/momotaro_plus3

python scripts/narrate.py --archive <出力先>/exp1/archive.json --selection <出力先>/exp1/selection.json \
  --out <出力先>/exp1/stories --backend ollama --project projects/momotaro_plus3_new --template templates/momotaro_plus3
```

- `--backend` は `claude-cli` / `codex-cli` / `anthropic` / `openai` / `ollama` / `llama-server` / `none` から選べます（[LLM バックエンド](llm-backends.md#model-defaults)を参照）。`none` を指定すると LLM を呼び出さず、プロンプトだけを保存します
- 上の例の `--backend ollama` は `settings.json` が無い（または `model` を書いていない）と、既定モデル `qwen3.6:35b` を要求します。[LLM バックエンド](llm-backends.md)の手順で `qwen3.5:9b-q4_K_M` などを pull 済みの場合は、`settings.json` の `output.ollama.model` にそのモデル名を書いてください
- `selection.json` は `{"selected": ["III|high"]}` の形式です（キーは格子のセル = `カテゴリ|volatility区分`。実際に確認したキー例: `I|mid`, `II|high`, `III|low` など）
- あらすじの確認・格子セルの選定は、`--control` 付きで起動したビューア画面からも行えます

## 3. 決定論・回帰テスト

```
python -m unittest discover -s tests -v
```

決定論・中立遺伝子の無変調・前提違反ゼロ・伏線の減点・vitality の遷移などを確認します。`engine/` `gapengine/` を変更したときは必ず実行してください。詳しくは[決定論](../concepts/determinism.md)を参照してください。

