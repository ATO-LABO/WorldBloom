# GPU ガード計画: サーバー自動起動・GPU 調停・熱ガード（2026-09-18）

設計: Claude Fable 5.1 ／ 実装: Sonnet ／ レビュー: Opus

## 背景

Bonsai 2 27B（llama-server バックエンド、main bd8812d）を既定にした結果、運用上の注意が 3 つ残った。ユーザー指示（2026-09-18）:「これは仕組みで作って、仕組みで解決したい」。

1. 生成前に人が llama-server を起動しておく必要がある。
2. VRAM 8GB では Bonsai 2（6.8GB）と Ollama のモデルが同居できない。同居すると 0.9 tok/s まで落ち、計測・生成が静かに壊れる（2026-09-18 に 2 度発生）。
3. 連続生成で GPU が 86℃ に達し、約 1 時間の連続負荷で GPU lost（nvlddmkm 153）が起きた。

これを「人が気をつける」から「仕組みが保証する」へ変える。

## 設計判断（変更しないこと）

1. **有効化は settings.json の `output.gpu_guard` がある場合のみ**。キーが無ければ従来どおり何もしない（完全な no-op）。理由: (a) 既存・将来のテストが実 GPU リースや実 Ollama に触れないことを構造的に保証する、(b) 新規環境・配布 exe の挙動を変えない。
2. **GPU リースは OS レベルのファイルロック**（Windows: `msvcrt.locking` 非ブロッキング＋リトライ、POSIX: `fcntl.flock`）。プロセスが死ねば OS が解放するので、pid ファイル方式の「取りっぱなし」が起きない。`execution/provenance.py` の `directory_lock` と同じ技法だが、あちらは即時失敗・保存用なので流用せず、待機つきの専用実装にする。
3. **リースの場所はマシン全体で 1 つ**: 環境変数 `WORLDBLOOM_GPU_LEASE_DIR`、無ければ `%LOCALAPPDATA%\WorldBloom`（無ければ `tempfile.gettempdir()/worldbloom`）。worktree や control ディレクトリごとに分けない（GPU は 1 枚）。
4. **待つか失敗するか**: リースが取れない／Ollama が使用中のときは、呼び出し側が渡した待ち時間（バッチはジョブの残り時間、単発は `timeout`）まで待ち、それでも駄目なら `GpuBusy` で失敗する。無期限には待たない。
5. **Ollama の自動退避は「遊休と確認できたときだけ」**。`/api/ps` を `observe_seconds`（既定 15 秒）空けて 2 回読み、`expires_at` が進んでいれば使用中（誰かが呼んでいる）とみなして待つ。進んでいなければ遊休なので `keep_alive: 0` で退避する。リース未対応の利用者（手動の `ollama run`、Jev 側の未対応コード）の実験を壊さないため。
6. **止めるのは自分が起動したサーバーだけ**。既に到達可能な llama-server があれば、それは人が起動したものとして使うだけで止めない。
7. **孤児サーバーの自己修復**: 起動したサーバーの pid を保持者ファイルに記録する。次にリースを取った者は、記録された pid がまだ生きていれば（＝前の保持者が異常終了した）それを終了させてから進む。
8. **熱ガードのしきい値**: `pause_at` 78℃ 以上で待ち、`resume_at` 70℃ 以下で再開、`poll_seconds` 15、`max_wait_seconds` 600。温度が読めない（nvidia-smi 無し等）ときは**止めない**。生成 1 回の途中では止められないので、ガードは「各生成の前」に掛ける。（2026-09-18 追記: 当初は Jev 側実装の max_temp 82 に揃えたが、実測で 82℃ ガードが計 135 秒発動しても GPU が 86℃ まで上がったため、78/70 に引き下げ。Jev 側も同様に下げる）
9. `execution/output_worker.py` の `credentials()` 許可リストは変更しない。`launch`・`gpu_guard` は呼び出しリクエスト（来歴に封印される）に入れず、worker／generate_text が settings から直接読む。
10. engine/・決定論の対象経路には触れない。`viewer/pages.py` にも触れない（main に他セッションの未コミット変更あり）。UI への「GPU 待ち」表示は今回の範囲外（ジョブ progress にフィールドだけ書く）。

## settings.json の形（この機体での例。git 管理外）

```json
{"output": {
  "default_backend": "llama-server",
  "gpu_guard": {
    "thermal": {"pause_at": 78, "resume_at": 70, "poll_seconds": 15, "max_wait_seconds": 600},
    "ollama_base_url": "http://localhost:11434",
    "observe_seconds": 15
  },
  "llama-server": {
    "base_url": "http://127.0.0.1:8089", "model": "bonsai2-27b", "think": true,
    "options": {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0, "presence_penalty": 1.0, "max_tokens": 8192},
    "launch": ["C:/Projects/WorldBloom-local/bonsai/fork/llama-server.exe", "-m", "C:/Projects/WorldBloom-local/bonsai/Ternary-Bonsai-2-27B-PTQ1_0.gguf", "--alias", "bonsai2-27b", "-ngl", "99", "-np", "1", "-c", "10240", "-fa", "on", "--host", "127.0.0.1", "--port", "8089", "--reasoning-budget", "2048", "--reasoning-budget-message", "思考の上限に達した。ここで思考を終え、直ちに最終回答の本文だけを書く。"],
    "startup_seconds": 180
  }
}}
```

`gpu_guard` は空オブジェクト `{}` でも有効（全項目に既定値）。`launch` が無ければ自動起動はしない（到達不能なら従来どおり transport が失敗する）。

## 変更ファイルと内容

### 1. 新規 `gapengine/gpu_guard.py`（標準ライブラリのみ）

- `read_gpu_temperature() -> float | None`: `nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits`、timeout 5 秒、失敗はすべて None。モジュール関数にしてテストで差し替え可能にする。
- `wait_until_cool(thermal=None, *, read=read_gpu_temperature, sleep=time.sleep, clock=time.monotonic) -> float`: 温度 None または `< pause_at` なら即 0.0 を返す。`>= pause_at` なら `poll_seconds` ごとに読み、`<= resume_at`・None・`max_wait_seconds` 超過のいずれかで戻る。戻り値は待った秒数。
- `class GpuBusy(Exception)`: `.holder`（dict、`owner`/`pid`/`since` を持ちうる）を持つ。`str()` は人が読める 1 行。
- `lease_dir() -> Path`。
- `gpu_lease(owner, *, wait_seconds, poll_seconds=5.0, sleep=time.sleep, clock=time.monotonic)`: コンテキストマネージャ。`<lease_dir>/gpu.lock` を非ブロッキングで取りに行き、取れなければ `poll_seconds` ごとに再試行、`wait_seconds` 超過で `GpuBusy(holder=保持者ファイルの内容)`。取得後に `<lease_dir>/gpu.holder.json` へ `{"owner", "pid", "since"}` を書く（best effort、失敗しても続行）。**同一プロセス内では再入可能**（モジュール変数の深さカウンタ。内側の取得は何もしない）。解放時にロックを外す（保持者ファイルは残してよい——ロックが真実）。
- `record_managed_server(pid | None)` / `reap_orphan_server()`: 保持者ファイルの `managed_server_pid` の読み書きと、生存していれば終了させる処理（Windows: `taskkill /PID <pid> /T /F` を subprocess で、POSIX: `os.kill`）。pid の生存確認は `os.kill(pid, 0)` 相当（Windows では `tasklist /FI "PID eq ..."` か `OpenProcess`。実装しやすい方でよいが、自プロセスの pid を誤って殺さないこと）。
- `ollama_models(base_url, *, timeout=2.0) -> list[dict]`: `GET /api/ps` の `models`。到達不能・不正 JSON は `[]`。
- `ollama_is_active(base_url, *, observe_seconds, sleep=time.sleep) -> bool`: モデルが 1 つも載っていなければ False（観察せず即返す）。載っていれば `observe_seconds` 空けて再読し、いずれかのモデルの `expires_at` が変化していれば True。
- `unload_ollama(base_url, names)`: 各モデルに `POST /api/generate {"model": name, "keep_alive": 0}`。失敗は握りつぶす（best effort）。
- `local_gpu_session(backend, output_settings, *, owner, wait_seconds, on_status=None)`: コンテキストマネージャ。`output_settings` は settings の output 節（Mapping）。
  1. `backend not in ("ollama", "llama-server")` または `output_settings.get("gpu_guard")` が Mapping でなければ、**何もせず yield**。
  2. `gpu_lease(owner, wait_seconds=...)` を取る。以降の待ちも同じ締め切り（取得開始からの `wait_seconds`）を共有する。`on_status("gpu")` を待ち始めに 1 回呼ぶ（on_status が None でなければ）。
  3. `reap_orphan_server()`。
  4. `backend == "llama-server"` のとき: `ollama_is_active` が True の間は `poll_seconds` ごとに再判定して待ち、締め切り超過で `GpuBusy(holder={"owner": "ollama (in use)"})`。遊休でモデルが載っていれば `unload_ollama`。その後 `llama_server.managed_server(config, on_started=record_managed_server)` に入る（`config` は `output_settings["llama-server"]`）。`on_status("server_start")` を起動待ちの前に呼ぶ。
  5. `backend == "ollama"` のとき: `output_settings` に `llama-server` 節があり、その `base_url` の llama-server が到達可能なら（人が起動したサーバーが VRAM を持っている）`GpuBusy(holder={"owner": "llama-server (running)"})`。
  6. yield。finally でサーバー停止（自分が起動した場合のみ）→ `record_managed_server(None)` → リース解放。

### 2. `gapengine/llama_server.py`

- `is_ready(config, *, timeout=2.0) -> bool`: `GET {base_url}/health` が 200。
- `managed_server(config, *, on_started=None, sleep=time.sleep, clock=time.monotonic)`: コンテキストマネージャ。`is_ready` なら `yield False`（自分のものではない）。`launch`（空でない str の list）が無ければ `yield False`。あれば `subprocess.Popen(launch, stdin/stdout/stderr=DEVNULL, creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP（nt のみ）)`、`on_started(pid)`、`startup_seconds`（既定 180）まで 2 秒間隔で `is_ready` を待つ。途中でプロセスが終了した／時間切れなら terminate して `RuntimeError("llama-server failed to start")`。`yield True`。finally で `terminate()` → `wait(10)` → だめなら `kill()`。

### 3. `execution/output_worker.py` の `run()`

- settings を 1 回読み（`credentials()` と同じ読み方。`credentials()` 自体は無変更）、`output` 節を得る（`gapengine.synopsis` の既存の節解決と同じ優先順 `output`/`perform`/`llm`。小さな内部ヘルパーを synopsis 側に切り出してよいが、`_backend_config` の挙動は変えないこと）。
- 候補ループ全体を `local_gpu_session(request["backend"], output, owner=f"output:{output_id}", wait_seconds=max(0, 締め切り - 現在), on_status=...)` で包む。`GpuBusy`・`RuntimeError`（起動失敗）は既存の `preflight_error` 経路に載せる（各候補が `error` / `preflight_failed` / stage=preflight / `cause_type` = 例外クラス名 / `retry_policy="safe_new_request"` で終わる。**送信は 1 件も行わない**）。
- 各候補の `run_generation` の直前に `wait_until_cool(thermal)`（ガード有効時のみ）。
- `on_status` とクールダウン中は `change(jobs, folder, nonce, waiting=<"gpu"|"server_start"|"cooldown">)`、解消したら `waiting=None`。既存の `progress` の形は変えない（新しいトップレベルキー `waiting` を足すだけ）。
- ガード無効時（`gpu_guard` キー無し）は現状と 1 バイトも挙動が変わらないこと。

### 4. `gapengine/synopsis.py` の `generate_text()`（単発経路: CLI と読者向け要約）

`ollama` / `llama-server` の分岐を、`local_gpu_session(backend, output節, owner="generate_text", wait_seconds=timeout)` と、その内側の `wait_until_cool` で包む。`GpuBusy`・`RuntimeError` は `GenerationError` に変換。ガード無効時は現状どおり。`execution/generation.py`（UI バッチ経路）はここを通らないので二重にはならない。

### 5. `scripts/synopsize.py`・`scripts/narrate.py`

セルを回すループ全体を `local_gpu_session` で包み、セルごとにサーバーを立て直さないようにする（リースは再入可能なので内側の generate_text はそのまま通る）。ガード無効時は no-op。

### 6. `README.md`

「(b-2) Bonsai 2 27B」の節を更新: 手動起動コマンドの説明は残しつつ、`launch` と `gpu_guard` を書けば WorldBloom が自動で起動・調停・冷却待ち・停止を行うことを説明。各キーの意味と既定値、待っても取れないときは `GpuBusy` で失敗すること、Jev など他の GPU 利用コードは `gapengine.gpu_guard.gpu_lease(owner, wait_seconds=...)` を取れば調停に参加できることを 1 段落で。

### 7. 新規 `tests/test_gpu_guard.py`（実 GPU・実 Ollama・実 llama-server に一切依存しない）

全テストで `WORLDBLOOM_GPU_LEASE_DIR` を一時ディレクトリに向ける。

- 熱: 温度列を注入して (a) None は待たない、(b) 閾値未満は待たない、(c) 85→80→71 で再開し待ち秒数が `poll_seconds`×2、(d) `max_wait_seconds` で打ち切り。
- リース: (a) 別プロセス（`subprocess` で小さな Python を起動しリースを保持させる）が持っている間は `wait_seconds` 短めで `GpuBusy`、`.holder["owner"]` が相手の owner、(b) 相手の終了後は取得できる（**プロセスを kill しても取得できること**＝OS が解放することの確認）、(c) 同一プロセスの再入は待たずに通る。
- Ollama 判定: `ollama_models` を差し替えて (a) モデル無し→False かつ sleep しない、(b) `expires_at` 不変→False、(c) 変化→True。`unload_ollama` は遊休時のみ呼ばれ、使用中は呼ばれず `GpuBusy`。
- managed_server: (a) 到達可能なら Popen しない、(b) 到達不能＋`launch`→ 偽サーバー（`/health` に 200 を返す小さな `http.server` スクリプトを空きポートで起動）を立てて ready になり、コンテキストを抜けるとプロセスが終了している、(c) すぐ終了するコマンドなら `RuntimeError`、(d) `on_started` に pid が渡る。
- セッション: (a) `gpu_guard` キー無しなら何も呼ばれない（lease も ollama も触らない）、(b) 非ローカル backend は no-op、(c) 孤児 pid が記録されていれば reap が呼ばれる、(d) ollama backend で llama-server 到達可能なら `GpuBusy`。
- output_worker 統合: `GpuBusy` 時に全候補が `preflight_failed`（cause_type `GpuBusy`）で終わり送信 0 件。既存の output_worker／output_jobs 系テストの作り方（`tests/test_output_jobs.py`・`tests/test_generation_execution.py`）を手本にする。
- generate_text: ガード有効＋セッションが `GpuBusy` を投げると `GenerationError`。

## 合格条件

1. `python -m unittest discover -s tests -v` が全件成功。ただし既知の失敗 1 件（`test_output_jobs…test_stop_and_crash_confirm_descendant_exit_and_never_resend`、main でも再現・別タスクで対応中）は除く。それ以外の失敗・新規の不安定テストが無いこと。リースのプロセス間テストは 3 回連続で安定して通ること。
2. `gpu_guard` キーが無い設定では、既存テストの挙動・実行時間が変わらない（実 `/api/ps`・実ロックファイルに触れない）ことをテストで示す。
3. `execution/output_worker.py` の `credentials()`、`engine/`、`viewer/pages.py`、`DEFAULT_BACKEND` は無変更。新規依存なし。
4. ブランチ `feat/gpu-guard` にコミット。**push しない・main に合流しない・settings.json を作らない／触らない**。コミットメッセージ末尾に `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`。

## 範囲外（メインが後で行う）

この機体の settings.json への `gpu_guard`・`launch` の記入、実 GPU での実機検証（隣のセッションの GA 実験と調整のうえ）、Jev 側（`feat/jev-rationality`）へのリース適用の依頼、UI への「GPU 待ち」表示。
