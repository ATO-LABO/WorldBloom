---
sources:
  - "README.md"
reviewed: "4394c7ef86af65bb0f995a9071da5f1744e3ef87"
---

# 変更履歴

利用者から見て意味のある主な変更を日付順にまとめます。個々のコミットの一覧ではありません。最新のリリース物は [GitHub Releases](https://github.com/ATO-LABO/WorldBloom/releases) から取得できます。

## 2026-09-25

- ドキュメントサイト（このサイト）を公開。日本語・英語のバイリンガル構成、はじめに・概念・使い方・リファレンス・ガイドの5章立て
- あらすじ・本文生成の既定の接続先を `codex-cli` から `llama-server`（ローカル LLM）へ変更

## 2026-09-22

- 世界の自己拡張機能が完成（実験結果から世界設定の拡張案を提案・承認/却下・比較まで）
- サイドバー・ボタン・見出し・トップバーの見た目をトークンで統一

## 2026-09-18

- ローカル LLM 生成バックエンド `llama-server`（Bonsai 2 27B）を追加
- GPU ガード（llama-server の自動起動・Ollama との GPU 調停・熱ガード）を追加

## 2026-09-15

- 第五回AIアートグランプリ D部門へ応募。Windows 配布版（[v1.0.0-viewer](https://github.com/ATO-LABO/WorldBloom/releases/tag/v1.0.0-viewer)、タグの日時は 09-15〜16 にまたがる）を公開: 閲覧専用の `WorldBloom.exe` と、GA実験の実行から文章生成まで行える `WorldBloom-Studio.exe` の2種類
- 公開ビューア（[https://ato-labo.github.io/WorldBloom/](https://ato-labo.github.io/WorldBloom/)）を公開。桃太郎・恋愛・探偵の3実験をインストール不要で閲覧可能に

## それ以前

上記より前の変更は、実装フェーズ（Phase 0〜4）としてコアエンジン・GA×QD 評価・共進化・出力段（あらすじ化）・ビューアを順に構築した期間にあたります。経緯は[設計資料](../reference/design-docs.md)を参照してください。
