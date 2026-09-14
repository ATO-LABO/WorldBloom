# WorldBloom

遺伝的アルゴリズム×LLM による結末固定型の物語生成エンジン

## これは何か

物語の**設定と結末を先に固定**し、そのあいだの道のりだけを GA（遺伝的アルゴリズム）×シミュレーションで探すツールです。「結末が既知の物語」（昔話、恋愛もの、探偵もの）は、結末そのものではなく道中の経路にこそ面白さがある、という前提に立っています。

主体は7層構造（力・認識・資源・段階・身分・目的物・伏線）＋ vitality で表現し、遺伝子は行動系列ではなく「戦略ベクトル」（9スカラー）です。固定シードで走らせたシミュレーションのうち、固定結末に到達したものだけを MAP-Elites 格子（主導カテゴリ I〜VI × volatility）に残します。**出来事の生成に LLM は関与しません。**格子に残ったあらすじは人が読んで選び、選ばれた道のりだけを LLM が本文化します。

StorySim（同作者の別プロジェクト。世界と人物をシミュレートし、面白かったログを物語に書き起こす方式）から要素を切り取って再構築したもので、フォークではありません。

## 必要なもの

- Python 3.11 以上（開発は 3.13）
- `pip install -r requirements.txt`（PyYAML のみ）

あらすじ・本文の生成に LLM を使う場合（任意）。既定はローカルの Ollama＋Qwen3.5:

1. [Ollama](https://ollama.com) をインストール
2. `ollama pull qwen3.5:9b-q4_K_M`（約6.6GB。VRAM 8GBで動作確認済み）

代替として `claude-cli` / `codex-cli` / Anthropic API / OpenAI API のいずれかも選べます（`--backend` で切り替え）。

## 5分で試す

### (a) clone

```
git clone https://github.com/ATO-LABO/WorldBloom.git
cd WorldBloom
pip install -r requirements.txt
```

### (b) settings.json を作る（Ollama を使う場合）

```json
{
  "output": {
    "ollama": {
      "model": "qwen3.5:9b-q4_K_M",
      "base_url": "http://localhost:11434",
      "think": false,
      "options": {"num_ctx": 16384, "num_predict": 4096}
    }
  }
}
```

`options` は既定（`num_ctx` 16384・`num_predict` 4096）に上書きマージされるので、変えたいキーだけ書けばよい。`think` は Qwen3.5 の思考トークンを抑えるため既定で `false`。`settings.json` は `.gitignore` 対象（APIキーを含み得るため）。

### (c) サンプルをビューアで見る

同梱の `samples/`（桃太郎・探偵・恋愛の3実験、格子・あらすじ・本文入り）をブラウザで見る:

```
python viewer/server.py --runs samples --port 5401
```

`http://127.0.0.1:5401/` を開く。新しく進化を回す・選定をビューアから操作する場合は `--control <空フォルダ>` を追加する。その場合は `--runs` もリポジトリ外のディレクトリ（自分の実験出力先）にする。リポジトリ内の `samples` は閲覧専用で、`--control` とは併用できない。

### (d) 自分で進化を回す

```
python scripts/evolve.py --project projects/momotaro --template templates/momotaro \
  --out <出力先>/exp1 --generations 20 --population 100 --seeds 3 --keep reached --processes 4
```

所要時間の目安: 20コアで約1時間（実測: exp12 が6プロセスで約70分）。出力先はリポジトリ外を推奨（`.gitignore` は `runs/` を無視するが、リポジトリ内に大量の実験出力を置くべきではない）。`--project` / `--template` は `momotaro` / `detective` / `romance` の3ジャンルを同梱。

### (e) あらすじ化・本文化

```
python scripts/synopsize.py --archive <出力先>/exp1/archive.json --runs <出力先>/exp1 \
  --out <出力先>/exp1/synopses.json --backend ollama --project projects/momotaro --template templates/momotaro

python scripts/narrate.py --archive <出力先>/exp1/archive.json --selection <出力先>/exp1/selection.json \
  --out <出力先>/exp1/stories --backend ollama --project projects/momotaro --template templates/momotaro
```

`selection.json` は `{"selected": ["III|high"]}` の形（格子のセルキー = カテゴリ|volatility_bin）。あらすじの確認・格子セルの選定はビューア（`--control` 付きで起動時）からも行える。

### (f) 無作為基準との比較

```
python scripts/random_baseline.py --project projects/momotaro --template templates/momotaro --out <出力先>/baseline
```

方針を持たない完全無作為なシードだけで結末に届く経路の多様性を測る、GAの効果を測るための対照実験。

## 決定論

同じ `(world, genome, seed, precedent)` の組で `layers.jsonl` はバイト一致します。回帰テスト:

```
python -m unittest discover -s tests
```

## リポジトリ構成

```
engine/      7層の主体・世界・動詞・勝負解決・述語評価・vitality・ログ
gapengine/   遺伝子・Policy・分類器・前例表・QD・進化ループ・あらすじ化・説明抽出
execution/   ビューアから進化・出力ジョブを操作するための設定/ジョブ/権限境界
viewer/      HTTP ビューア（標準ライブラリのみ。格子・あらすじ・本文・実行管理画面）
scripts/     進化・あらすじ化・本文化・無作為基準・静的公開・サンプル抽出などの実行スクリプト
templates/   ジャンルテンプレート（行動グラフ・修飾ルール・正典プライア・QD軸・伏線ライブラリ）
projects/    世界と主体の初期状態（7層）。momotaro / detective / romance
samples/     ビューアで開ける最小サンプル（3実験。scripts/pack_samples.py で生成）
tests/       回帰テスト（unittest）
docs/        設計書・実装計画・開示文書
```

ラン出力は既定でリポジトリ外に置く（`--out` で明示する）。`samples/` は例外で、配布用にリポジトリへ含めている。

## ドキュメント

- 詳細設計: [docs/2026-09-11_gapengine-detailed-design.md](docs/2026-09-11_gapengine-detailed-design.md)
- ビューア UI/UX 設計: [docs/2026-09-12_viewer-ux-design.md](docs/2026-09-12_viewer-ux-design.md)
- 生成プロセス開示文書（第五回AIアートグランプリ D部門向け）: [docs/2026-09-14_grandprix-disclosure.md](docs/2026-09-14_grandprix-disclosure.md)
- 公開ページ（格子・あらすじ・本文を読むだけの静的サイト）: https://ato-labo.github.io/WorldBloom/

## 第五回AIアートグランプリ

本プロジェクトは第五回AIアートグランプリ D部門への応募作品『WorldBloom ── 遺伝的アルゴリズム×LLM による結末固定型の物語生成エンジン』です。使用したAIモデル・シード値・プロンプト等の開示は [docs/2026-09-14_grandprix-disclosure.md](docs/2026-09-14_grandprix-disclosure.md) を参照してください。

## ライセンス

[Apache License 2.0](LICENSE)。Copyright 2026 ATO-LABO.
