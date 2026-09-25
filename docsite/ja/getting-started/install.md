# インストール

## 必要環境

- Windows 10/11、WebView2（多くの環境に標準搭載済み）
- Studio には **Python 3.11 以上**が PATH に必要（`pip install pyyaml`）。Viewer は不要
- git は無くても動く（無い場合、実行の来歴に一部情報が記録されないだけ）

!!! tip "文章生成（LLM）は最初に ⚙ 設定で選ぶ"
    GA 実験そのものは LLM 無しで動きます。LLM が必要になるのは、あらすじ・本文の生成だけです。生成を使う場合は、最初にアプリの **⚙ 設定** 画面で接続先（Ollama / llama-server / claude-cli / codex-cli / Anthropic API / OpenAI API / 生成しない（プロンプト保存のみ））とモデル名を選んでください。何も設定していないときの既定の接続先は llama-server です（配布版 v1.0.0-viewer では codex-cli）。モデル名は claude-cli だけ未入力でも動きます（`claude-sonnet-5` が使われます）が、それ以外の接続先は必ず選ぶ必要があります。Ollama や llama-server 本体は WorldBloom に含まれていないので、別途インストールしてください。詳しくは [LLM バックエンド](../usage/llm-backends.md#model-defaults) を参照してください。

=== "Studio（フル機能）"

    [WorldBloom-Studio-portable.zip](https://github.com/ATO-LABO/WorldBloom/releases/latest/download/WorldBloom-Studio-portable.zip) を展開して `WorldBloom-Studio.exe` をダブルクリックします。

    - 世界を選んで GA 実験を実行、Sifting（吟味・選定）、あらすじ・本文の生成まで、この画面から行えます
    - 初回起動時に、exe と同じフォルダに `runs/`・`control/` フォルダが自動生成されます
    - 展開したフォルダの `app/` フォルダは動かさないでください（アプリ本体が入っています）
    - Python 3.11 以上（`pip install pyyaml` 済み）が PATH に無いと、Studio はエラー画面を出して起動しません

=== "Viewer（閲覧のみ）"

    [WorldBloom-portable.zip](https://github.com/ATO-LABO/WorldBloom/releases/latest/download/WorldBloom-portable.zip) を展開して `WorldBloom.exe` をダブルクリックします。

    - 桃太郎・恋愛・探偵の3実験（あらかじめ生成済みの格子・あらすじ・本文）を、ブラウザ操作なしで見られます
    - Python・Ollama 等のインストールは不要です
    - 閲覧専用ビルドのため、画面上の採用・選定チェックボックス等をクリックすると 403 エラーになります（仕様どおりの挙動です）

=== "ソースから"

    必要なもの:

    - Python 3.11 以上（開発は 3.13）
    - `pip install -r requirements.txt`（PyYAML のみ）

    ```
    git clone https://github.com/ATO-LABO/WorldBloom.git
    cd WorldBloom
    pip install -r requirements.txt
    ```

    同梱の `samples/`（桃太郎・探偵・恋愛の3実験）を閲覧専用で開く:

    ```
    python viewer/server.py --runs samples --port 5401
    ```

    `http://127.0.0.1:5401/` を開きます。この起動方法では画面から実験の実行・設定編集はできませんが、選定（採用チェックボックスなど）だけは書き込めます（`runs` 側の各実験フォルダに直接 `selection.json` として保存されます。`--control` を付けていないため）。

    自分で GA 実験を実行したりあらすじ・本文を生成し直したりする場合は、`--control` を付けて起動します（`runs`・`control` はリポジトリの外に置いてください）。

    ```
    python viewer/server.py --runs <runsの場所> --control <controlの場所> --port 5401
    ```

    実験の実行・あらすじ/本文の生成をコマンドラインから直接行う手順は [CLI での作業手順](../usage/cli-workflow.md) を参照してください。

## SmartScreen の警告が出た場合

配布版 exe は署名されていないため、初回起動時に Windows SmartScreen の警告が出ることがあります。「詳細情報」→「実行」の順にクリックしてください。

## 実際のダウンロード先

配布版は [GitHub Releases](https://github.com/ATO-LABO/WorldBloom/releases) から入手できます。現時点の最新版はタグ `v1.0.0-viewer` で、`WorldBloom-portable.zip`（Viewer）と `WorldBloom-Studio-portable.zip`（Studio）の2つが添付されています。
