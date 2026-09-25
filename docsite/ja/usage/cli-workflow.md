---
sources:
  - "scripts/evolve.py"
  - "scripts/synopsize.py"
  - "scripts/narrate.py"
  - "scripts/random_baseline.py"
  - "templates/"
reviewed: 72aaeae8379271e99357be2f967fa0cff3901a8d
---

# CLI での作業手順

Studio 画面を使わず、コマンドラインだけで GA 実験からあらすじ・本文の生成まで一周する手順です。引数の一覧は[CLI](../reference/cli.md)を参照してください。

## 1. GA 実験を回す

```
python scripts/evolve.py --project projects/momotaro --template templates/momotaro \
  --out <出力先>/exp1 --generations 20 --population 100 --seeds 3 --keep reached --processes 4
```

- `--project` / `--template` は同梱の `basic` / `momotaro` / `momotaro_plus` / `momotaro_plus2` / `detective` / `romance` が使えます（`templates/` 配下の一覧。`basic` は共通の基本ルールで、それ単体をジャンルとして指定することはあまりありません）
- 出力先はリポジトリ外を推奨します（`.gitignore` は `runs/` を無視しますが、リポジトリ内に大量の実験出力を置くべきではありません）
- 所要時間の目安: 世代数×個体数×seed数にほぼ比例します。既定の規模（20世代×100個体×3seed）は20コアで約1時間が目安です（並列数は[設定](settings.md)の「計算」で指定します）
- 完了すると `<出力先>/exp1/archive.json` に格子（アーカイブ）が書き出されます。`cells=<件数>` は結末に到達して格子に残った代表個体の数です

無作為な方針だけで結末に届く割合と多様性を測る対照実験も行えます。

```
python scripts/random_baseline.py --project projects/momotaro --template templates/momotaro --out <出力先>/baseline
```

## 2. あらすじ・本文を生成する

```
python scripts/synopsize.py --archive <出力先>/exp1/archive.json --runs <出力先>/exp1 \
  --out <出力先>/exp1/synopses.json --backend ollama --project projects/momotaro --template templates/momotaro

python scripts/narrate.py --archive <出力先>/exp1/archive.json --selection <出力先>/exp1/selection.json \
  --out <出力先>/exp1/stories --backend ollama --project projects/momotaro --template templates/momotaro
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

