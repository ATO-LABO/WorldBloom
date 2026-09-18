# llama-server バックエンド追加計画（Bonsai 2 27B 採用、2026-09-18）

設計: Claude Fable 5.1 ／ 実装: Sonnet ／ レビュー: Opus

## 背景と決定

PrismML の Ternary Bonsai 2 27B を文章生成（あらすじ・本文・読者向け要約）の既定モデルとして採用する（ユーザー決定 2026-09-18）。
Bonsai 2 は独自量子化形式のため Ollama では動かず、PrismML フォーク版 llama.cpp の `llama-server`（OpenAI 互換 API）でのみ動く。
よって WorldBloom に「ローカル llama-server」バックエンドを 1 つ追加する。Ollama バックエンド（`gapengine/ollama.py`、コミット c650506）と**同じ形・同じ粒度**で足すこと。新しい抽象化は作らない。

試し打ちで確定した動作条件（`C:\Projects\WorldBloom-local\bonsai\trial\`、auto-memory `worldbloom-bonsai2-trial`）:

- サーバー起動: `llama-server.exe -m Ternary-Bonsai-2-27B-PTQ1_0.gguf --alias bonsai2-27b -ngl 99 -np 1 -c 10240 -fa on --port 8089 --host 127.0.0.1 --reasoning-budget 2048 --reasoning-budget-message "思考の上限に達した。ここで思考を終え、直ちに最終回答の本文だけを書く。"`
- 思考トークン上限は**サーバー起動フラグでしか効かない**（リクエスト側の `reasoning_budget` は無視される）。コード側で上限を送ろうとしないこと。
- リクエスト: `POST {base_url}/v1/chat/completions`、`chat_template_kwargs: {"enable_thinking": true}`、サンプリングは temperature 0.7 / top_p 0.8 / top_k 20 / min_p 0 / presence_penalty 1.0、`max_tokens` 8192。
- 応答: `choices[0].message.content` が本文、`choices[0].finish_reason` が `"stop"` なら完了。思考は `reasoning_content` に分離されて返るので**保存も使用もしない**。上限フラグなしでサーバーを立てた場合は `finish_reason: "length"`・content 空になる → 「incomplete response」として失敗させる。

## 設計判断（変更しないこと）

1. **バックエンド名は `llama-server`**。`BACKENDS` では `"ollama"` の直後、`"none"` の前に置く。
2. **設定キーは Ollama と同じ 4 つだけを使う**: `base_url` / `options` / `think` / `seed`。`execution/output_worker.py` の `credentials()` 許可リストはこの 4 つを既に通すので**変更しない**。`options` は dict で、そのままリクエスト payload のトップレベルにマージする（サンプリング値と `max_tokens`）。
3. **既定値**（新モジュール内の定数）: `DEFAULT_MODEL = "bonsai2-27b"`、`DEFAULT_BASE_URL = "http://127.0.0.1:8089"`、`DEFAULT_OPTIONS = {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0, "presence_penalty": 1.0, "max_tokens": 8192}`、`think` の既定は **True**（Ollama は False だが、Bonsai 2 は思考オンが採用構成）。
4. **コード側の `DEFAULT_BACKEND`（`execution/output_settings.py`、現在 `"codex-cli"`）は変更しない**。「既定を Bonsai 2 にする」はこの機体の `settings.json`（git 管理外）の `output.default_backend` を書き換えることで行い、それは実装役の作業範囲外（メインが main 合流後に行う）。新規環境や配布 exe の初期動作を壊さないため。
5. **`viewer/pages.py` には触れない**。main の作業ツリーに他セッションの未コミット変更があり、合流時の衝突を避ける（476 行目の「例: ollama / qwen3.6:35b」は例示文なので放置でよい）。
6. engine / gapengine のシミュレーション経路（決定論の対象）には一切触れない。今回の変更は出力生成（LLM 呼び出し）層に閉じる。

## 変更ファイルと内容

### 1. 新規 `gapengine/llama_server.py`
`gapengine/ollama.py` を手本に、標準ライブラリのみで次の 4 関数を持つ。docstring・型注釈の粒度も合わせる。

- `build_request(config, prompt) -> tuple[str, dict]`: `base_url`（末尾スラッシュ除去）、`model`、`think`（既定 True）、`options`（`DEFAULT_OPTIONS` に config の `options` を上書きマージ）、`seed`（int のときだけ payload に `"seed"` として入れる）。payload は `{"model", "messages": [{"role": "user", "content": prompt}], "stream": False, "chat_template_kwargs": {"enable_thinking": think}, **options}`。URL は `{base_url}/v1/chat/completions`。
- `extract_text(data) -> str`: `choices` が空でないリストで先頭が Mapping であること、`finish_reason == "stop"` でなければ `ValueError("incomplete response")`、`message.content` が str でなければ `ValueError("invalid response content")`。
- `list_models(config, *, timeout=2.0) -> tuple[list[str], str | None]`: `GET {base_url}/v1/models` の `data[].id` をソートして返す。到達不能・JSON 不正は `([], "server_unreachable")`。
- `availability(config, *, timeout=2.0) -> dict`: ollama.py と同じ形（`available` / `reason` / `model`、`model_missing` / `server_unreachable`）。

### 2. `gapengine/synopsis.py`
- `from gapengine import llama_server` を追加、`BACKENDS` に `"llama-server"` を追加。
- `generate_text()` の `if backend == "ollama":` ブロックの直後に、同じ形の `if backend == "llama-server":` ブロック（`llama_server.build_request` → `_post_json(url, {}, payload, timeout=timeout)` → `llama_server.extract_text`、`ValueError` は `GenerationError` に変換、`_validate_response(text)`）。
- `backend in {"anthropic", "openai"}` の API キー必須判定に `llama-server` が**入らない**ことを確認（キー不要）。

### 3. `execution/generation.py`（3 か所、いずれも ollama 分岐の隣）
- プリフライト（現 69 行付近 `if backend == "ollama": return None`）: `llama-server` も同じくキー不要で `None` を返す。`if backend in ("ollama", "llama-server"):` にまとめてよい。
- リクエスト組み立て（現 154 行付近）: `llama-server` 分岐を追加し `llama_server.build_request({**request["credentials"], "model": request["model"]}, request["prompt"])`、`headers = {}`。**else 節（OpenAI API、Authorization ヘッダ付き）に落ちないこと**。
- `validate_response()`（現 184 行付近）: `llama-server` 分岐を追加し `llama_server.extract_text(data)`。その後段の空文字拒否などの共通検証は ollama と同じ経路を通ること。

### 4. `execution/configs.py`
`generation_availability()` の `if backend == "ollama":` の直後に `llama-server` 分岐（`llama_server.availability({**config, "model": model})`、`authentication: "not_required"`、生のエラー文字列は落とす）。

### 5. `execution/output_settings.py`
- `list_models()`: `llama-server` 分岐（`llama_server.list_models(section)`、`live: True`）。
- `default_limits()`: ローカルモデル用の長いタイムアウト（900 / 3600）を `llama-server` にも適用。`backend in ("ollama", "llama-server")` の形でよい。
- docstring の「ollama/anthropic/openai have a real list-models API」に llama-server を追記。

### 6. `viewer/workbench_pages.py`
`GENERATION_BACKEND_OPTIONS` の `("ollama", ...)` の直後に `("llama-server", "ローカル llama-server（OpenAI互換。Bonsai 2 など）")`。

### 7. その他の列挙箇所の確認
`grep -rn "ollama" --include=*.py --include=*.js viewer execution gapengine scripts` を実行し、バックエンド名で分岐・列挙している箇所がすべて `llama-server` にも対応していることを確認する（`scripts/rationality_probe.py` と `viewer/pages.py` は対象外）。見つけた追加箇所は報告に列挙する。

### 8. `README.md`
既存の Ollama セットアップ節の後に「Bonsai 2 27B（llama-server）」節を追加: フォーク版バイナリ（PrismML-Eng/llama.cpp releases、win-cuda-12.4 の `llama-...-bin-...zip` と `cudart-...zip` の両方）と GGUF（Hugging Face `prism-ml/Ternary-Bonsai-2-27B-gguf` の `PTQ1_0`）の入手、上記の起動コマンド（`--alias` と `--reasoning-budget` が必須である理由を 1 行ずつ）、`settings.json` の記述例、注意 2 点（VRAM 8GB では Ollama のモデルと同居できないので生成前に `ollama ps` で退避を確認／ノート GPU は連続生成で 86℃ に達するため長時間連続実行は休止を挟む）。

`settings.json` 記述例:
```json
"default_backend": "llama-server",
"llama-server": {
  "base_url": "http://127.0.0.1:8089",
  "model": "bonsai2-27b",
  "think": true,
  "options": {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0, "presence_penalty": 1.0, "max_tokens": 8192}
}
```

### 9. 新規 `tests/test_llama_server_backend.py`
`tests/test_ollama_backend.py` の各クラスに対応するテストを、同じ手法（`unittest`、`mock.patch` でネットワークを差し替え、実サーバー不要）で書く:
BuildRequest（既定 payload の形・`chat_template_kwargs`・options マージ・seed・base_url 末尾スラッシュ・`think: False` 指定時）、ExtractText（`length` で失敗・content 非 str で失敗・`stop` で成功・`choices` 空で失敗）、GenerateText（API キーなしで ok・incomplete は GenerationError）、Availability（present / missing / unreachable）、EmptyResponse（空 content は validate_response で拒否）、ConfigsAvailability、UiGenerationPath（プリフライトがキー不要・**`/v1/chat/completions` に Authorization ヘッダなしで POST**・validate_response が stop を受理し length を拒否・worker の credentials が 4 キーのみ通す）、OutputSettings（`list_models` が live=True、`default_limits` が 900/3600）。

## 合格条件

1. `python -m unittest discover -s tests -v` が worktree で全件成功（既存テストの退行なし）。新規テストが上記の観点を網羅。
2. `BACKENDS` に `llama-server` があり、Ollama の分岐がある全箇所に対応する分岐がある（§7 の grep 結果を報告）。
3. `execution/output_worker.py`・`viewer/pages.py`・engine/・`execution/output_settings.py` の `DEFAULT_BACKEND` は無変更。
4. 新規依存ライブラリなし（標準ライブラリのみ）。
5. ブランチ `feat/llama-server-backend` に 1〜2 コミット。**push しない・main に合流しない**。コミットメッセージ末尾に `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`。

## 範囲外（メインが後で行う）

実機検証（実サーバーに対する生成）、`settings.json` の既定切替、`.claude/launch.json` への llama-server 登録、main 合流、配布 exe の README 更新。
