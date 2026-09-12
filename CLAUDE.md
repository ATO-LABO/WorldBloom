# モデル使い分けルール

モデル使い分けの役割定義・委任フロー・委任放棄防止・設計役ゲート・並列分解の可否は、全プロジェクト共通のため `~/.claude/CLAUDE.md`（グローバル。ATO-LABO/Claude-Ops-Kit の global/CLAUDE.md が実体）に集約している。このプロジェクトで実際に起きた実例（委任放棄・設計役ゲートの発動など）があれば、ここに追記していく。

## このプロジェクトでの設計優先順位

- **構想（Notion「WorldBloom(StorySim×GA)」）が正本**。StorySim に対応物があっても「そのまま流用でOK」と判断しない。既存機構で代用する提案は「構想が意図した機能が失われない」ことを機構レベルで示せるときだけ。**工数削減より仕組みの実現を優先する**（ユーザー指示 2026-09-11）。
- WorldBloom は StorySim の延長ではなく別プロジェクト。StorySim 本体（`G:\マイドライブ\Projects\StorySim`）は**読むだけで変更しない**。要素の仕分けは `docs/2026-09-11_gapengine-detailed-design.md` §15.2。
- 設計判断を伴う変更（7層の意味・遺伝子の次元・QD 軸・結末判定の方式）は設計役ゲートの対象。実装役が自己判断で決めない。

# 委任時の共通規約（このファイルはサブエージェントにも読み込まれる。委任プロンプトへの転記は不要）

## 実行環境の定型（WorldBloom）
- Python の実行は PATH の `python`（3.11 以上）。依存は `requirements.txt`（engine/gapengine は PyYAML のみ。Parquet/DuckDB は `scripts/analyze.py` に閉じる）
- **ラン出力は `C:\Projects\WorldBloom-local\runs\<experiment>\`**（Google Drive 上のリポジトリには置かない。実行時は `--out` でローカルパスを明示する）
- `G:\マイドライブ` は Google Drive for Desktop のストリーミングマウント。大量の小ファイルを書かない。角括弧付きフォルダは PowerShell で `-LiteralPath` を使う
- 決定論が設計の前提: 同じ `(world, genome, seed, precedent)` で `layers.jsonl` がバイト一致すること。乱数は単一の `random.Random(seed)`、候補生成と Policy は乱数を消費しない、タイブレークは名前順

## 検証の共通規約
- 回帰テスト: `python -m unittest discover -s tests -v`（決定論・中立遺伝子の無変調・前提違反ゼロ・伏線減点・vitality 遷移）
- エンジンや候補生成を変えたら、決定論テストと中立遺伝子テストを必ず通す。乱数の消費順序が変わる変更は設計書に明記する
- 検証で変更した値は必ず原状復帰する。検証目的での破壊的操作（削除・移動）はしない

## 外部AI成果物の受け入れ
- 手順自体（適用前に静的レビュー → バックアップ → 適用 → 実機検証）はグローバル CLAUDE.md を参照。実装は AB テスト方式（設計役の計画 → Codex GPT-5.6 sol のテキスト納品 → Claude が適用 → Opus レビュー → Fable 最終確認）
- 適用時にコードを勝手に改良しない（測定対象は Codex のコード品質）。バグに気づいたら差し戻すか記録に残す

## Notion定型
- 正本（構想）: 「WorldBloom(StorySim×GA)」ページID `3d8e21ef1cac800293b9c7b109d9df8f`（ProjectLists 配下）。子ページ「📐 基本設計」ID `3d8e21ef1cac81db841cef966a6e96f1`・「📘 詳細設計」ID `3d8e21ef1cac81489541ef2c12fa6098` を持ち、関連タスク DB（`collection://d3448ca3-45cd-41f7-9fa4-8cb3b769a329`）は親ページに置く
- 作業ログページID: `3d8e21ef1cac818fb285e00eab9b29ad`（「📝 作業ログ」。作業の区切りごとに末尾へ追記。エントリ形式=日付/担当モデル/作業内容/成果物/決定事項/次アクション）
- Notion ページの新規作成・hub 構成変更・既存ページの書き直し時は Claude-Ops-Kit の `reference/notion-page-design.md` を参照する

# プロジェクト補足

設定と結末を固定し、その間の経緯を GA×シミュレーションで生成する物語エンジン（GapEngine）。設計の正本は `docs/2026-09-11_gapengine-detailed-design.md`。プロジェクト固有の経緯・決定事項は auto-memory（MEMORY.md）を参照。

## 協業タスクの受領窓口（2026-09-12）

- 協業ボード（状態の正本）: https://app.notion.com/p/3d9e21ef1cac8153aa56fbf23ca9fb7e
- 今回の実装依頼「選択・根拠・代償・転機」: https://app.notion.com/p/3d9e21ef1cac81ed9330d0f675ca4d1e
- 専用タスクデータソース: collection://90e88eeb-687e-435e-8cbc-2a0a3a1cb2c7
- 再開・受領時はボードと依頼をfetchし、データソースの最新スキーマを確認 →「次に取る（Claude Code）」ビュー（view://3d9e21ef-1cac-8190-a5b5-000c1ec5bd6a）をquery → 対象カード本文末尾と依存カードをfetchする。初回はWB-EXPLAIN-001を対象とする。
- 既存の実行担当・未コミット差分との競合を確認し、受領・状態・次の一手・最終活動を更新する。起票だけで受領済み／実装中としない。担当ごとに原則1枚、進行中の作業へ無断で割り込まない。
- 作業ログ・実装報告・レビュー結果は各カードへ残す。依存が解消したカードは指示済みに更新する。共通のモデル分担・設計ゲート・コミット運用は既存規約をそのまま適用する。
- この入口はWorldBloom専用。StorySimのカード・状態・自動実行を移さない。
