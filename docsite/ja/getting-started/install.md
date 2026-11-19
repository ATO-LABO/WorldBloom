---
reviewed: "2e70a9b216b7b2a80a4228a10a928148060e8fc8"
---

# インストール

## 必要環境

- Windows 10/11、WebView2（多くの環境に標準搭載済み）
- Studio には **Python 3.12 以上**が PATH に必要（`pip install pyyaml`）。Viewer は不要
- git は無くても動く（無い場合、実行の来歴に一部情報が記録されないだけ）

!!! tip "AI機能は接続先の設定が必要"
    基本のGAシミュレーションはLLMなしで動きます（任意の合理性層を有効にする場合を除きます）。AIを使った世界の新規作成・精緻化、あらすじ・本文の生成には接続先とモデルの設定が必要です。現行画面の **⚙ 全体設定** と[LLM バックエンド](../usage/llm-backends.md)を確認してください。OllamaやモデルはWorldBloomには同梱されません。外部サービスを使う設定では、そのサービスへ生成対象の情報が送られます。

=== "Studio（フル機能）"

    [WorldBloom-Studio-portable.zip](https://github.com/ATO-LABO/WorldBloom/releases/latest/download/WorldBloom-Studio-portable.zip) を展開して `WorldBloom-Studio.exe` をダブルクリックします。

    - 世界を選んで GA 実験を実行、Sifting（吟味・選定）、あらすじ・本文の生成まで、この画面から行えます
    - 初回起動時に、exe と同じフォルダに `runs/`・`control/` フォルダが自動生成されます
    - 展開したフォルダの `app/` フォルダは動かさないでください（アプリ本体が入っています）
    - Python 3.12 以上（`pip install pyyaml` 済み）が PATH に無いと、Studio はエラー画面を出して起動しません

=== "Viewer（閲覧のみ）"

    [WorldBloom-portable.zip](https://github.com/ATO-LABO/WorldBloom/releases/latest/download/WorldBloom-portable.zip) を展開して `WorldBloom.exe` をダブルクリックします。

    - 桃太郎・恋愛・探偵の3実験（あらかじめ生成済みの格子・あらすじ・本文）を、ブラウザ操作なしで見られます
    - Python・Ollama 等のインストールは不要です
    - 閲覧専用ビルドのため、画面上の採用・選定チェックボックス等をクリックすると 403 エラーになります（仕様どおりの挙動です）

=== "ソースから"

    必要なもの:

    - Python 3.12 以上（開発は 3.13）
    - `pip install -r requirements.txt`（PyYAML のみ）

    ```
    git clone https://github.com/ATO-LABO/WorldBloom.git
    cd WorldBloom
    pip install -r requirements.txt
    ```

    同梱の `samples/`（桃太郎・探偵・恋愛の3実験）を開く（選定を書き込む可能性があるため、元データを保管する場合はコピーを使ってください）:

    ```
    python viewer/server.py --runs samples --port 5401
    ```

    `http://127.0.0.1:5401/` を開きます。この起動方法では画面から実験の実行・設定編集はできませんが、選定（採用チェックボックスなど）だけは書き込めます（`runs` 側の各実験フォルダに直接 `selection.json` として保存されます。`--control` を付けていないため）。

    自分で GA 実験を実行したりあらすじ・本文を生成し直したりする場合は、`--control` を付けて起動します（`runs`・`control` はリポジトリの外に置いてください）。

    ```
    python viewer/server.py --runs <runsの場所> --control <controlの場所> --port 5401
    ```

    実験の実行・あらすじ/本文の生成をコマンドラインから直接行う手順は [CLI での作業手順](../usage/cli-workflow.md) を参照してください。

## ソース版のOSと保存データ

WindowsとLinuxで実行管理を利用できます。LinuxではPython 3.12以上に加え、Linux 5.3以上のpidfd、子プロセス監視機能、同じPID名前空間の`/proc`が必要です。利用できない環境ではジョブを受け付けません。Windows配布EXEとLinuxのソース起動は別の導入方法です。

現行ソースで編集・実行できる世界は`world_format_version: 1`を含む新形式だけです。同梱の「桃太郎＋3」（`projects/momotaro_plus3_new`）または新規作成画面から確定した世界を使ってください。旧世界のファイルや実行結果を上書きせず、必要なら元の版のコードとPython環境を別に保持してください。形式番号だけを手書きで足しても変換にはなりません。

## SmartScreen の警告が出た場合

未署名の配布版では、初回起動時にWindows SmartScreenの警告が出ることがあります。入手元がこのリポジトリの公式Releaseであることと、公開されている場合はチェックサムを確認してください。不明な配布元や一致しないファイルは実行せず、警告を無条件に回避しないでください。

## 実際のダウンロード先

配布版は [GitHub Releases](https://github.com/ATO-LABO/WorldBloom/releases) から入手できます。添付ZIPの対応バージョン・変更内容・動作条件は各Releaseで確認してください。ソース版の更新だけではEXEは更新されません。
