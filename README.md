# WorldBloom

設定と結末を先に固定し、その間の経緯（道のり）を遺伝的アルゴリズム × シミュレーションで生成する物語制作エンジン。「結末が既知の物語」（昔話、恋愛もの、探偵もの）は結末ではなく道中の経路にこそ面白さがある、という前提に立つ。経路生成を担う汎用レイヤーを **GapEngine** と呼ぶ。

[StorySim](https://github.com/ATO-LABO/StorySim)（世界と人物をシミュレートし、面白かったログを物語に書き起こす方式）から必要な要素を切り取り、7層構造の主体モデルと GA×Quality-Diversity の選抜を中心に再構築したもの。StorySim のフォークではなく別プロジェクト。

## 仕組み（要約）

- **主体は7層構造**: 能力層（真の強さ base/modifiers）／認識層（相手の強さ・事実についての信念）／資源層（関係資本・所持品・評判）／フェーズ層（通過した閾値と使える行動）／身分層（真の正体と見せている正体）／対象層（複数の主体が奪い合う目的物）／遅延効果層（伏線の設置と回収）＋ vitality（倒れる・起き上がる・死ぬ）
- **遺伝子は行動系列ではなく戦略ベクトル**（9 スカラー: 行動カテゴリ I〜VI の重み、risk_tolerance、stance_shift_bias、novelty_drive）。行動は方針と世界状態から都度選ばれる
- **GapEngine は外側ループ**: 個体ごとに固定シードでシミュレーションを走らせ、固定結末に到達したランだけを MAP-Elites アーカイブ（主導カテゴリ × volatility）に入れる
- **novelty_drive** は「正典プライア＋前世代アーカイブ＋自己履歴」の前例表に対する個体内の駆動力。選抜側の QD とは別の機構
- **出力**: アーカイブの全個体をあらすじ化 → 人間が選定 → 選ばれたものだけ LLM が本文化

## ドキュメント

- 構想（正本）: Notion「WorldBloom(StorySim×GA)」
- 詳細設計: [docs/2026-09-11_gapengine-detailed-design.md](docs/2026-09-11_gapengine-detailed-design.md)

## 構成（予定）

```
engine/      7層の主体・世界・動詞・勝負解決・述語評価・vitality・ログ
gapengine/   遺伝子・Policy・分類器・前例表・QD・進化ループ・あらすじ化
templates/   ジャンルテンプレート（行動グラフ・修飾ルール・正典プライア・QD軸・伏線ライブラリ）
projects/    世界と主体の初期状態（7層）
scripts/     進化の実行・分析（JSONL → Parquet）
tests/
```

ラン出力は `C:\Projects\WorldBloom-local\runs\` に置く（リポジトリ外）。

## 実行

```
python scripts/evolve.py --project momotaro --generations 20 --population 100 --seeds 3 --out C:\Projects\WorldBloom-local\runs\exp1
```
（Phase 0 完了後に確定）

### ローカル Ollama をあらすじ・本文生成のバックエンドに使う

```
ollama pull qwen3.5:9b-q4_K_M
```

`settings.json` に接続先を設定する:

```json
{"output": {"ollama": {"model": "qwen3.5:9b-q4_K_M", "base_url": "http://localhost:11434",
                        "think": false, "options": {"num_ctx": 16384, "num_predict": 4096}}}}
```

`options` は既定（`num_ctx` 16384・`num_predict` 4096）に上書きマージされるので、変えたいキーだけ書けばよい。`think` は Qwen3.5 の思考トークンを抑えるため既定で `false`。

`--backend ollama` を指定して実行する（API キー不要）。あらすじ化は `synopsize.py`、本文化は `narrate.py`:

```
python scripts/synopsize.py --archive <archive.json> --runs <runs_dir> --out <out.json> --backend ollama --project projects/<name> --template templates/<name>
python scripts/narrate.py --archive <archive.json> --selection <selection.json> --out <stories_dir> --backend ollama --project projects/<name> --template templates/<name>
```
