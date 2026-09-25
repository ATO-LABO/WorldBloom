---
sources:
  - "docs/"
reviewed: 4394c7ef86af65bb0f995a9071da5f1744e3ef87
---
# 設計資料

このドキュメントサイトは「今の使い方・仕組み」を説明する場所です。それに対して、**設計の経緯・決定事項・実装計画**は `docs/` 配下の設計資料側にまとまっています。仕様の背景（なぜこの形になったか、どんな案を検討して却下したか）を知りたいときはこちらを参照してください。

## 詳細設計

- [`docs/2026-09-11_gapengine-detailed-design.md`](https://github.com/ATO-LABO/WorldBloom/blob/main/docs/2026-09-11_gapengine-detailed-design.md) — GapEngine の詳細設計。7層構造・遺伝子・行動タイプ・伏線・結末固定・QD 格子・共進化など、このサイトの[概念](../concepts/seven-layers.md)章の元になっている正本です。

## ビューア UI/UX 設計

- [`docs/2026-09-12_viewer-ux-design.md`](https://github.com/ATO-LABO/WorldBloom/blob/main/docs/2026-09-12_viewer-ux-design.md) — ビューアの画面構成・操作フローの設計。作成が2026-09-12と古く、その後の UI 刷新（世界画面の再設計、実行タブの再設計など）で記述の一部が現行の画面と異なります。実際の画面の使い方は[使い方](../usage/create-world.md)を参照してください。

## 開発者向け

- [`docs/DEVELOPMENT.md`](https://github.com/ATO-LABO/WorldBloom/blob/main/docs/DEVELOPMENT.md) — 回帰テストの実行方法、決定論の約束、exe ビルド手順、ドキュメントの更新手順など、開発者向けの情報です。

## 実装計画書・個別機能の設計記録

`docs/` にはこのほか、フェーズごとの実装計画書や個別機能の設計・検証記録が置かれています。日付が古いものほど、その後の変更で内容が現行と食い違っている可能性があります。

| ファイル | 内容 |
|---|---|
| `2026-09-11_phase0-implementation-plan.md` | Phase 0: コアエンジン再構築＋GA×QD 配管 |
| `2026-09-11_phase1-implementation-plan.md` | Phase 1: 認識層の完成・弱体化・三原則 |
| `2026-09-11_phase2-implementation-plan.md` | Phase 2: 遅延効果・身分・儀礼・修飾ルール |
| `2026-09-11_phase3-implementation-plan.md` | Phase 3: 共進化・出力段（あらすじ化→人間選定→本文化）・ビューア |
| `2026-09-11_phase4-implementation-plan.md` | Phase 4: 他ジャンルへの転用・転換（rethink）・メタ進化の準備 |
| `2026-09-12_viewer-phase1-implementation-plan.md` | ビューア第1段「読める化」実装計画 |
| `2026-09-12_explain-*.md`（4件） | 「選択・根拠・代償・転機」の表示契約・実装計画・引き継ぎ・読者確認の記録 |
| `2026-09-18_llama-server-backend-plan.md` | llama-server バックエンド（Bonsai 2 27B）追加計画 |
| `2026-09-18_gpu-guard-plan.md` | GPU ガード（サーバー自動起動・GPU 調停・熱ガード）計画 |
| `2026-09-20_screening-workspace-concept.md` / `-validation.md` | 上映画面の改善案と実装・検証記録 |
| `2026-09-20_sifting-workspace-design.md` / `-validation.md` | Sifting ワークスペースの設計と実装確認記録 |
| `2026-09-20_synopsis-modal-design.md` | あらすじモーダルと世界差分の設計 |

一覧は [GitHub の `docs/` ディレクトリ](https://github.com/ATO-LABO/WorldBloom/tree/main/docs)で常に最新のものを確認できます。
