## 変更の目的と結果

何が問題で、利用者にどのような変化があるか。

## 統合先・依存関係

- 通常の変更はbaseを`develop`にする。main反映は`develop → main`。
- 依存PR、重複する変更、取り込み順序：
- 対応範囲と未実装事項：

## 検証

- 試験コマンド・結果・対象SHA：
- 実動作確認の内容と証拠：
- 既知の失敗・制約・未確認事項：

## マージ前

- [ ] 最新baseに対する必須CIが成功している
- [ ] 未解決レビューがない
- [ ] merge commitで取り込む
- [ ] main向けの場合：headがdevelopで、ユーザーが確認したSHAと一致する
- [ ] 削除する場合：後続PRが作業ブランチを参照していない

運用手順：[docs/BRANCHING.md](https://github.com/ATO-LABO/WorldBloom/blob/develop/docs/BRANCHING.md)
