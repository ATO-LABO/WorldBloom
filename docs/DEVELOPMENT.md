# 開発者向けガイド

このリポジトリのコードに変更を加える場合の手順です。GA 実験の回し方・LLM バックエンドの設定・CLI の使い方など、利用者向けの手順は[ドキュメントサイト](https://ato-labo.github.io/WorldBloom/docs/)にまとまっています。

- [CLI での作業手順](https://ato-labo.github.io/WorldBloom/docs/usage/cli-workflow/)（`scripts/evolve.py` / `synopsize.py` / `narrate.py` / `random_baseline.py` の使い方）
- [LLM バックエンド](https://ato-labo.github.io/WorldBloom/docs/usage/llm-backends/)（Ollama・Bonsai 2（llama-server）のセットアップ、`settings.json` の書き方）
- [GPU ガード](https://ato-labo.github.io/WorldBloom/docs/usage/gpu-guard/)（自動起動・調停・熱ガードの仕組みと設定）
- [決定論](https://ato-labo.github.io/WorldBloom/docs/concepts/determinism/)（決定論が成り立つ理由）

同梱サンプルをビューアで眺めるだけなら [README](../README.md) の「試し方」で足ります。

## 決定論の約束

同じ `(world, genome, seed, precedent, engine hash)` の組で、`layers.jsonl` はバイト単位で一致します。乱数は単一の `random.Random(seed)`（GA側は `random.Random(ga_seed)` と分離）、候補生成と Policy（遺伝子の変調）は乱数を消費せず、同点のタイブレークは名前順です。**この前提を崩す変更（乱数の消費順序が変わる変更など）は、設計書にその旨を明記してください。**

## 回帰テスト

```
python -m unittest discover -s tests -v
```

決定論・中立遺伝子の無変調・前提違反ゼロ・伏線の減点・vitality の遷移などを確認します。対象は `engine/` `gapengine/`（GA・分類器・前例表・7層のロジック）を変更したときで、`viewer/`（表示のみ）や一回きりの `scripts/` 実行など決定論に影響しない変更では省略してかまいません。

## 開発時の起動

ソースからビューアを起動する場合は、リポジトリ直下で次を実行します（`runs`・`control` はリポジトリの外に置いてください）。

```
python viewer/server.py --runs <runsの場所> --control <controlの場所> --port 5401
```

`--control` を付けない場合、画面から実験の実行・設定編集はできませんが、選定（採用チェックボックスなど）だけは書き込めます。この操作はジョブ管理を経由せず、`runs` 側の各実験フォルダに直接 `selection.json` を書き込みます（`viewer/data.py` の `toggle_selection()` が `directory_lock` を取って書く、`--control` 用の `JobStore` を介さない経路）。詳しくは[インストール](https://ato-labo.github.io/WorldBloom/docs/getting-started/install/)の「ソースから」を参照してください。

## GPU 調停の仕組みに外部コードから参加する

`gpu_guard` の仕組み（[GPU ガード](https://ato-labo.github.io/WorldBloom/docs/usage/gpu-guard/)）の外で GPU を使うコード（Jev の判定など）も、`gapengine.gpu_guard.gpu_lease(owner, wait_seconds=...)` を取ってから GPU を使えば同じ調停に参加できます（同一プロセス内での再入は待たずに通ります）。

## 配布用 exe のビルド

`viewer/app_desktop.py`（pywebview の起動エントリ）と `viewer/build_exe.cmd`（PyInstaller のビルドスクリプト）で、閲覧専用の `WorldBloom.exe` とフル機能の `WorldBloom-Studio.exe` の2本を作ります。

1. `viewer\build_exe.cmd` を実行すると、PyInstaller の dist フォルダに `WorldBloom.exe` と `WorldBloom-Studio.exe` が生成されます（`app_desktop.py` 1本から、実行時のファイル名で動作モードを切り替える設計です）
2. 配布フォルダを組み立てます。各 exe と同じ場所に `app/` フォルダを作り、次を丸ごとコピーします: `viewer/` `execution/` `gapengine/` `engine/` `scripts/` `templates/` `projects/` `requirements.txt`（閲覧専用の配布物には `samples/` も同梱します）
3. 自作パッケージ（`viewer`/`execution`/`gapengine`/`engine`/`scripts`）は exe に焼き込まず、配布フォルダの `app/` に生ファイルとして置いたものを実行時に動的 import（`importlib.import_module`）する設計です。再ビルド後は、PyInstaller の build フォルダにできる `PYZ-00.toc` を開き、`viewer.` `execution.` `gapengine.` `engine.` `scripts.` で始まるエントリが無いことを確認してください（あれば、古いコードが exe 内に焼き込まれてしまっています）
4. Studio 版は、配布前にテストで作られた `runs/`・`control/` フォルダを配布フォルダから削除してください（無ければ初回起動時に自動生成されます）

`requirements.txt` を `app/` に含め忘れると、実行時の来歴記録（`execution/provenance.py` の `code_snapshot()`）が失敗し、GA 実験が起動直後に止まります。`scripts/` を含め忘れると `ModuleNotFoundError: No module named 'scripts'` になります。どちらも起動はできてしまうため、再ビルドのたびに疑ってください。

## ドキュメントの更新

ドキュメントサイトの原稿は `docsite/{ja,en}/`（Material for MkDocs）です。

- **ビルド・プレビュー**: `mkdocs serve -f docsite/mkdocs.ja.yml`（英語版は `mkdocs.en.yml`）。CI と同じ厳密さで確認するなら `mkdocs build --strict -f docsite/mkdocs.ja.yml -d <出力先>`
- **CLI のヘルプを変えたら**: `python docsite/gen_cli.py` を実行して `docsite/snippets/cli/*.txt` を再生成してください（[CLI](https://ato-labo.github.io/WorldBloom/docs/reference/cli/)ページはこのスニペットを埋め込んでいるだけです）
- **`python docsite/check.py` の読み方**: ja/en のページ一覧が一致しない場合と、CLIスニペットが古い場合は exit 1（ビルドを壊す）。en ページの `ja_rev`（front matter）が対応する ja ページ本文（front matter を除く）の現在のハッシュと一致しない、または未設定の場合は「要追従/未翻訳」として警告表示のみ（翻訳を終えたら `python docsite/check.py --ja-rev ja/<ページ>.md` の値を ja_rev に書く）。ja ページの `reviewed:`（front matter、最後に内容を確認したコミットSHA）以降に `sources:` のファイルが変わっていれば「情報源が更新されたページ」として警告表示のみ。どちらも exit code には影響しないので、CI が通っていても一覧は確認してください
