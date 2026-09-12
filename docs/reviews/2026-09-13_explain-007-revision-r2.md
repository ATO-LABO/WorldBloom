# WB-EXPLAIN-007 再提出 r2

2026-09-13 / Codex。Claude CodeレビューのA・Bと記録項目1〜7に対応。独立再レビューとコミットはClaude Code担当。コミット・pushは行っていない。

## 必須修正
### A: 未実行の後続行動を除外
build_packetで後続のresultがinvalidのときだけ除外。成功値のホワイトリストは導入していない。misjudgedもexposedも実行済みの後続として保持する。

実ログ exp10-detective/g0/ind-1/seed-4/layers.jsonl のL141/154/160/166が除外された。factsは9件から5件になった。fixtureではinvalidを2件含む複数リンクを本物の抽出器へ通し、misjudged/exposedが残ることを検証。

既存3本は保存応答・編集文・プロンプト・元ログを変更せず、そのまま照合を通過。新しいLLM呼び出しは0回。パケットが変わる別ランの古い要約は、既存のパケット一致検査によりフォールバックする。

### B: 格子の由来表示と四項目復帰
reader_ui.shortは「LLM要約・編集照合済み」の標識、色の付いた背景と左罫線を持つ見出しを描画し、直後に従来のexplanation_ui.shortを併記する。選択・根拠・代償・転機をすべて保持。

HTTP実測: 恋愛格子のLLM表記1件／代償1件、探偵格子のLLM表記2件／代償4件。恋愛格子の「B → A の好意 -0.2」も確認。

## 記録項目への対応
1. 境界線は既存の --border へ統一。
2. 生成関数名への無効なpatchを削除。実際に保存要約を読み込む格子・詳細・比較ページを通し、CLIプロセス起動・HTTP API要求・ソケット生成の境界に禁止モックと未呼び出しアサーションを設置。from-importされた生成関数からの実行もこれらの境界で検知する。
3. status/version/prompt不一致検査は変更後のartifactハッシュに署名し直してから確認し、ハッシュ不一致とは独立に失敗させる。サイズ上限は有効なJSONに空白を足して検証。モデル文字列のHTMLエスケープも明示確認。
4. 文数2/3/5/6、見出し60/61、本文200/201、応答6000/6001を他の条件を満たすJSONで確認。
5. 要約がない場合は告知を出さず、詳細ページのナビゲーションとeliteサマリを先に置き、「選択から後続へのつながり」とコアパネルを従来位置へ戻す。要約がある場合だけ読者向け文章を先頭に出す。
6. 「トークン数を取得できない」を資料2本で訂正。generate_textの戻り値には使用量を含めていないが、CLI rolloutにはtoken_countがある。Claude Code独立レビューによる合計64,620トークン（出力1,961）を出典付きで反映。今回Codexが全セッションを再集計したとは主張しない。円建て費用は未算出、事前のトークン上限を設定していたという意味でもない。
7. 読み取りをmockしない実ページ経路の試験を追加。_with_readerを無効にすれば必ず落ちる。スコープゲート、入力パケットの許可フィールド、プロンプトの情報追加禁止条項と資料JSON、editor_note必須、改変応答・変更ログのフォールバックを固定。

## 検証
- python -B -m unittest discover -s tests -v: **148件成功、69.590秒**。専用テスト17件（旧9件から1件置換・9件追加）、既存131件。
- 重要条件を意図的に無効化する14種類のミューテーションをメモリ上で適用し、14/14を検出。invalid混入、誤った成功ホワイトリスト、viewer接続停止、格子標識/代償削除、プロンプト/資料除去、スコープ/編集理由/statusゲート除去、サイズ・数量境界。元レビューの50件一式を再現した試験ではなく、今回の指摘に絞った14件。ソースファイル自体を書き換えて検証していない。
- 実ログのinvalid4件除外と、試作3本の保存要約の有効性・元ログSHA一致を確認。
- 恋愛・探偵格子、探偵比較、恋愛詳細でHTTP200。1440pxの格子2画面で標識と代償を目視確認。600pxの格子は既存仕様の横スクロールで、右端の候補は初期位置では全文表示されない。狭幅の全面対応を今回完了したとは扱わない。
- アプリ内ブラウザー接続がタイムアウトしたため、独立した一時プロファイルのheadless Chromeで撮影。読者操作による確認や実機モバイル試験は未実施。
- git diff --check成功。engine配下・gapengine/scenes.py・gapengine/synopsis.py、Claude Codeのレビュー原文、設定、元ランは変更なし。

## 所有ファイルと成果物
今回修正: gapengine/reader_summary.py、viewer/reader_ui.py、viewer/pages.py、viewer/static/app.css、tests/test_reader_summary.py、docs/2026-09-12_explain-007-reader-pilot.md、docs/reviews/2026-09-12_explain-007-reader-pilot-review.md。この再提出資料を新規作成。前回からあるviewer/data.py/scripts/readable.py等の差分は保持。

実行結果: C:/Projects/WorldBloom-local/runs/wb-explain-reader-007-r2-20260913
- regression.log: 全148件
- targeted-mutations.json / check_targeted_mutations.py: 意図的な不具合14件の検出結果と再実行スクリプト（実行時cwdはWorldBloom_v2）
- artifact-checks.json: 実ログ除外・保存3本・元ログSHA
- http-checks.json / 各HTML / PNG3枚: 表示検証
- code-provenance.json: 基準コミットと今回対象ファイルのSHA

localhost: port5322、PID46860。入力は C:/Projects/WorldBloom-local/runs/wb-explain-revision-20260912-r2。既存URLで再読み込みできる。

## 次の一手と評価の区別
Claude CodeにA/Bと記録項目の再確認を依頼する。意味の正しさは引き続き人手照合を含む設計で、自動保証へ変えたわけではない。
ユーザーからは「展開としてだいぶ理解しやすい」と評価を得ている。名前表示・背景の深掘りは別の改善案。面白さ・探索量による候補の幅は未評価。50世代探索、名前対応、創作補完の実装は今回の修正へ混ぜていない。
