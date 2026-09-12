# WB-EXPLAIN 第1回指摘への修正・再提出

担当: Astra (Codex)。基準 HEAD: 445fa76。Claude Code の第1回レビュー本文は保持する。
ユーザー指示: 修正1〜4を実施し、5でClaude Codeが再レビューできる状態まで進める。

## 修正方針と受入条件

- F3 / 002: 推奨(a)を採用。既存の実行直前キャプチャから explanation.cost_baseline を記録する。version=1、timing=before_execute、stamina/resources。候補分布のversion=1は維持する。記録OFFは従来どおり。主人公・NPC、受動回復後、既存全行・RNG・並列一致を検証する。
- F1 / 003: assetsのnullは数量不明でも確定した喪失。数量を捏造しない。評判などの未知部分と完全性を分ける。
- F2 / 003: candidateはstatus=confirmed。全行走査と終端・必要な行構造を確認し、その決定に関連する候補イベントも状態変化もない場合だけabsent。重みなどの未対応影響・欠損はunknown。探索の範囲と理由を出力し、後続イベントの取りこぼしをテストする。
- F2/F4/F5 / 004: 転機の確認状態でタグを分ける。知識の部分性を表に明記。短い行動名は独立辞書とし、未知語は「未対応の行動（識別子）」としてエスケープ表示する。scenes/synopsisは変更しない。
- 検証: 回帰一式、旧3ログの不変・元行との照合、新3条件の記録ON/OFF一致・容量、実画面。専用の新しいCドライブ出力に保存する。
- 005: 本文書と証拠をNotionへ再提出。独立受入・読者品質の判断・コミット・pushはClaude Code担当。

## 結果

F1〜F5の修正・自己検証を完了。独立再レビューに提出する。005の合格は未宣言。

最終証拠: `C:\Projects\WorldBloom-local\runs\wb-explain-revision-20260912-r2`。
r1は途中確認の記録として保持する。最終提出はr2。

| 指摘 | 変更ファイル | 確認できた結果 |
|---|---|---|
| F1 | gapengine/explanations.py、tests/test_explanations.py | 旧恋愛L21/L58/L97の所持品喪失を確定。最初の2件のamountはnull。L58のreputation不明、complete=falseを保持。verification.jsonのF1_legacy |
| F2 | 抽出器、表示、両テスト、契約付録E | candidate→confirmed/candidate。終端・必要構造・全行走査、無変化、関連イベントなしでのみabsent。未知verb/未知marker/前後値の欠けた再考/中断ログ/policyや状態変化はunknown。候補のイベントは直接出典リンクを表示 |
| F3 | engine/sim.py、抽出器、記録・抽出テスト、実ログ検証スクリプト | 推奨(a)採用。version=1の独立cost_baseline、候補分布v1は維持。snapshotと同じ丸めの値を再利用。実際の受動回復を起こしたテストで全主体の実行直前値を照合。新3条件の56件（NPC36件）の移動損失が記録差と一致 |
| F4 | viewer/explanation_ui.py、表示テスト | 知識の一覧の部分性を表示。心理的な動機とは分ける |
| F5 | 同上、契約付録E | 未知のverbは識別子を保ち「未対応の行動」と表示。選択・要約・代替候補表でHTMLエスケープの試験成功。scenes/synopsisはHEADとバイト一致 |

## 検証と出典

- 最終コードの全回帰: 131件 / OK / 69.547秒。regression.txt。
- 説明系18件成功。全行・RNG非干渉は3ジャンルの非中立Policyで照合。processes=1/2のlayers/archiveバイト一致も含む。
- 旧3ログのSHA256不変、代表と後続はL157→197、L222→229、L207→212を保持。
- 新3条件はON/OFF全既存行一致、全3本が結末到達。現在入力の検証用ランであり、入力来歴未確定の旧候補へ説明を移植しない。
- 移動損失: 探偵case0 23件（NPC12）、探偵case1 13件（NPC5）、恋愛case2 20件（NPC19）。各caseのmove-costs.jsonに原行と前後値を保存。
- 転機の全決定分布: turning-counts.json。旧/新の各3本で同じ判定。確認済みの因果とイベント候補を合算しない。
- HTTP5件: 比較・新詳細・新格子・旧詳細・原ログ。http.json。
- 画面: compare.png（1440px）、compare-narrow.png（600px）、review-panels.pngを目視確認。review-panels.htmlは実ログを本番のpanel関数で描画したレビュー用資料。単独HTMLの出典リンクは起動中の5323/5324へ向けてある。
- 接続機能のタイムアウトのため、個人プロファイルを使わないheadless Chromeで撮影。モバイル実機の操作は未検証。
- 記録容量OFF→ON: 120106→316687、146350→397333、135415→390279 bytes（約2.64〜2.88倍）。前回の記録形式より約6.5〜7.3%増。速度は単発・キャッシュ混在で優劣を主張しない。
- 差分の所在と再実行コマンド: docs/2026-09-12_explain-handoff-for-claude.md。

## 005が再確認する点

1. 本表のF1〜F5と契約付録Eに沿って、修正差分と原ログを独立に照合する。
2. absentは「この選択の転機候補イベントなし」の限定判定であり、重みや未知機構まで因果が無いとは主張しない。同主体・同ターンのイベントを候補として扱う範囲の妥当性を確認する。
3. 三値とconfirmation、部分的知識、数量不明の喪失、未知語の表示を実際に読む。
4. 読者品質と候補の幅は未評価のまま。探偵2本の中心は再考→告発。表示の改善を物語品質・幅の改善とはしない。
5. 不備は番号付きで差戻し。合格後のコミット・pushはClaude Code。Astraは未コミットのまま提出する。

