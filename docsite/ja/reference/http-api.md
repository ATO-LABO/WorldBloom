---
sources:
  - "viewer/job_api.py"
  - "viewer/server.py"
  - "viewer/app_desktop.py"
  - "viewer/output_pages.py"
  - "viewer/workbench_pages.py"
  - "viewer/library_pages.py"
  - "viewer/run_catalog.py"
reviewed: "72aaeae8379271e99357be2f967fa0cff3901a8d"
---
# HTTP API

!!! warning "内部向けAPIです"
    ここに載せる `/api/...` エンドポイントは WorldBloom の画面（`viewer/`）自身が使う内部APIで、予告なく変わります。外部からの利用を想定した安定APIではありません。サーバーはループバック（`127.0.0.1`/`localhost`）のみで待ち受け、`viewer/job_api.py` の `boundary()` が `Host`/`Origin`/`Sec-Fetch-Site` を検査して他オリジンからの呼び出しを拒否します。

## 有効になる条件

- `python viewer/server.py --runs <runs>` だけで起動すると、`--control` が無いため `job_store` が無く、`/api/...` は**書き込み系（POST）だけでなく GET も含めて `503 unavailable` になります**。例外は `GET /api/status/local` と `POST /api/status/local/preload`・`POST /api/status/local/unload` の3つだけで、これらは `job_store` を経由せず常に使えます。
- `--control <control>` を付けて起動すると、`ConfigStore`・`JobStore` が有効になり、下表の設定・ジョブ・生成APIが使えます。
- 配布版 **Viewer exe**（読み取り専用ビューア）は `--control` を渡していても、`viewer/app_desktop.py` の `_ReadOnlyHandler.do_POST` がすべての POST を **403 "read-only build"** で即座に拒否します（`samples/` への書き込みを防ぐため）。画面上の採用・選定チェックボックスはこのビルドでも見た目上クリックできますが、押すと403になるのは仕様です。**Studio exe** はこの制限を持たず、フル機能で動作します。

## 認証・境界

- 書き込み系（POST）は `X-WorldBloom-Client: 1` ヘッダーが必須です（無いと `403 forbidden`）。
- リクエストボディは `Content-Type: application/json` かつ `Content-Length` を1つだけ持つこと（`Transfer-Encoding` は不可）。

## 設定・ジョブ・生成 API（`viewer/job_api.py`）

| メソッド | パス | 用途 |
|---|---|---|
| GET | `/api/status/local` | GPU・生成の状態（トップバーのダイアログ用）。`job_store` 不要 |
| POST | `/api/status/local/preload` | 現在の設定のローカルLLMを事前起動 |
| POST | `/api/status/local/unload` | 指定バックエンド（`llama-server`/`ollama`）のモデルを解放 |
| GET | `/api/configs` | 保存済み実行設定の一覧 |
| GET | `/api/configs/<id>` | 実行設定1件の詳細 |
| POST | `/api/configs/preview` | 実行設定を保存せずプレビュー（規模・ETA等） |
| POST | `/api/configs` | 実行設定を保存 |
| POST | `/api/configs/<id>/duplicate` | 実行設定を複製（実装は `viewer/workbench_pages.py`） |
| GET | `/api/settings/output` | ⚙設定「文章生成」タブの現在値 |
| POST | `/api/settings/output` | 文章生成の設定を更新（backend/model/limits） |
| POST | `/api/settings/output/api-key` | anthropic/openai の APIキーを保存（書き込み専用、読み出しは不可） |
| GET | `/api/settings/output/models` | 選択可能なモデルの一覧（ollama/llama-serverは実サーバーへ問い合わせ） |
| POST | `/api/settings/output/test` | 疎通テスト（成功すると `verified_models` に記録） |
| GET | `/api/settings/evolution` | GA並列数の現在値 |
| POST | `/api/settings/evolution` | GA並列数を更新 |
| GET | `/api/outputs` | 生成ジョブ（あらすじ・本文）の一覧 |
| GET | `/api/outputs/<id>` | 生成ジョブ1件の詳細 |
| POST | `/api/outputs/<id>/recover` | 異常終了した生成ジョブを復旧 |
| GET | `/api/jobs` | GA実験ジョブの一覧 |
| GET | `/api/jobs/<id>` | GA実験ジョブ1件の詳細 |
| POST | `/api/jobs` | GA実験ジョブを投入 |
| POST | `/api/jobs/<id>/cancel` | GA実験ジョブを停止 |

## 世界・ジャンルの編集 API（`viewer/library_pages.py`）

世界・ジャンルの編集はGA実験そのものとは独立の機能ですが、各ハンドラは `_require_job_store` を通すため、動かすには `job_store`（＝`--control` 起動）が必要です。一覧取得の `GET /api/worlds`・`GET /api/genres` は存在しません（一覧は画面のHTMLに埋め込まれます）。

| メソッド | パス | 用途 |
|---|---|---|
| POST | `/api/worlds` | 世界の新規作成 |
| POST | `/api/worlds/<id>/edit` | 世界の編集（画面の「世界を編集」） |
| POST | `/api/worlds/<id>/basics` | 世界の基本情報のみ保存 |
| POST | `/api/worlds/<id>/files` | 世界配下のYAMLファイルを保存 |
| POST | `/api/worlds/<id>/validate` | 世界のYAMLを検証（保存はしない） |
| POST | `/api/genres` | ジャンルの新規作成 |
| POST | `/api/genres/<id>/files` | ジャンル配下のYAMLファイルを保存 |
| POST | `/api/genres/<id>/{save,parse,check}` | ジャンルエディタの保存・構文解析・検証 |
| POST | `/api/worlds/<id>/patches/<patch_id>/{approve,reject}` | 世界の自己拡張パッチの承認・却下 |
| POST | `/api/worlds/<id>/patches/reopen` | 承認済み拡張パッチを差し戻す |

## 実験結果・選定 API

| メソッド | パス | 用途 |
|---|---|---|
| POST | `/exp/<experiment>/selection` | 採用・保留・除外の選定状態を更新。`X-WorldBloom-Client` ヘッダーは不要で、`job_store` が無くても動く（実行中の実験には `assert_run_idle` で拒否、これは `job_store` がある場合のみ働く） |
| POST | `/exp/<experiment>/delete` | 実験（`runs/<experiment>/` 一式）を削除。`job_store` 必須 |
| POST | `/exp/<experiment>/cell/<cell>/reader-summary` | セル1件の読者向け要約を生成 |
| GET / POST | `/api/runs/<rid>/selection` | ラン1件の選定状態を取得 / 更新（`viewer/run_catalog.py`） |
| POST | `/api/runs/<rid>/world-patch` | 世界の自己拡張パッチの提案・確認（`propose`/`check`。`job_store` 必須、`viewer/run_catalog.py`） |

これ以外にも `viewer/run_catalog.py`（`/api/runs`・`/api/selected`）・`viewer/workbench_pages.py`（主に画面表示用のGET）がありますが、いずれも画面のUI操作に対応する内部APIです。正確な一覧は各ファイルの `dispatch()`/`_resolve()` を参照してください。
