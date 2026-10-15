# ブランチとリリースの運用

2026-10-13から、`main`と`develop`を常設する。機能変更はdevelopで統合・検証し、ユーザーが確認した内容をmainへ反映する。

## ブランチの役割

| ブランチ | 役割 | 維持・削除 |
| --- | --- | --- |
| `main` | 確認済みの安定版。公開ドキュメントの配信元でもある | 常設 |
| `develop` | 次のmain反映に向けた統合・動作確認 | 常設 |
| `codex/<作業名>`など | 機能・修正・文書の作業 | 取り込みと後続PRの参照先を確認して削除 |
| `gh-pages` | 公開ビューアとドキュメントの配信成果物 | 維持。機能開発の統合先にしない |

既存の`feat/`・`fix/`・`diag/`ブランチはそのまま扱ってよい。新しいCodex作業は`codex/`を標準とする。GitHubのデフォルトブランチは安定版の`main`を維持するため、作業PRではbaseを明示的に`develop`へ変更する。

## 通常の変更

1. `origin/develop`を取得し、そこから作業ブランチを作る。未コミット変更のある共有チェックアウトを切り替えず、独立したチェックアウトを使う。
2. 変更内容に合う試験と実動作確認を行い、PRを`develop`へ作る。依存PRがある場合は依存先・取り込む順序を記す。
3. 最新developと統合できる状態にし、必須CIが成功したこと、未解決レビューがないことを確認する。
4. **Create a merge commit**でマージする。継続ブランチの履歴と後続PRの依存関係を保つため、main・developではsquash/rebase mergeを使わない。
5. 後続PRがその作業ブランチをbaseにしていれば、差分を確認してdevelopへ付け替える。取り込まれた変更と未取り込みの変更を区別してから、不要なブランチを削除する。

```sh
git fetch origin
git switch -c codex/my-change origin/develop
# 変更・検証・commit
git push -u origin codex/my-change
gh pr create --base develop --head codex/my-change
```

機能の一部だけが実装されたPRは、対応範囲・未実装事項を本文に残す。試験の成功だけで、仕様全体や物語品質の受入完了とはしない。

## developからmainへの反映

1. `develop`から`main`へのPRを作る。通常の機能PRを直接mainへ入れない。
2. 統合元・重複整理・試験結果・実動作確認・既知の制約と、確認対象のdevelopのSHAをPR本文に記す。
3. ユーザーがその内容を確認してmain反映を指示するまで、Draftを維持する。確認後に追加commitが入った場合は、その差分を再確認する。
4. 最新mainに対する必須CIがすべて成功した後、確認済みのhead SHAを指定してmerge commitで取り込む。自動マージは使わない。
5. mainのツリーと確認したdevelopのツリーが一致することを確認する。mainのマージ履歴は、`main`から`develop`への同期PRで取り込む。保護を迂回する直接pushは行わない。
6. 取り込み済みPRと不要な作業ブランチを整理する。**developを削除しない。**

```sh
gh pr create --base main --head develop --draft
# ユーザー確認・CI成功後。<確認済みSHA>は実際の値に置き換える
gh pr ready <PR番号>
gh pr merge <PR番号> --merge --match-head-commit <確認済みSHA>
# mainのマージ履歴をdevelopへ同期するPR
gh pr create --base develop --head main --title "Sync main into develop"
```

## GitHubで強制する保護

mainは`main-pr-only`、developは`develop-integration`のアクティブなRulesetで保護する。両方でPR、merge commit方式、レビュー会話の解決、最新baseに対する以下のCI成功を必須にし、force pushとブランチ削除を禁止する。管理者を含めてbypass対象は設定しない。

- `regression (ubuntu-latest, 3.11)`
- `regression (ubuntu-latest, 3.13)`
- `regression (windows-latest, 3.11)`
- `regression (windows-latest, 3.13)`

Python最低対応は3.12、CIは3.12/3.13へ移行する。上の一覧と `.github/rulesets/` はGETで確認した現在の適用済み状態で、3.11の2チェックを3.12へ置換する案は未適用である。ルール変更は別途確認し、適用後に読み戻してから一覧・保存JSONを更新する。未適用の間はmain/developで3.11必須チェック待ちになるため、マージしない。詳細は[Python対応版の移行](python-support.md)。

チェックの発行元はGitHub Actions（integration ID `15368`）に固定する。パス条件で実行されないことがあるdocsの`build`は全PRの必須チェックには含めず、実行された場合の成否を確認する。回帰CIはPRとmain/developへのpushで動く。

現在の書き込み担当は1アカウントなので、他者によるGitHubの承認レビュー数は0件とする。**main反映前のユーザー確認は上記手順で必須とするが、GitHubがチャット上の承認を検証するわけではない。** また、Ruleset単体ではmain向けPRのheadをdevelopだけに限定していないため、マージ担当がhead・baseを確認する。レビュー担当が増えたら承認数を見直す。

設定の控えは[main.json](../.github/rulesets/main.json)と[develop.json](../.github/rulesets/develop.json)。このJSONを置くだけではGitHubに自動適用されない。管理者がAPIまたは設定画面で適用し、GitHubから読み戻して条件・ルール・bypass対象を照合する。変更時は設定控えとこの文書も更新する。

ブランチの自動削除は無効のままにする。常設developと、後続PRが依存する作業ブランチを誤って削除しないためである。

## 検証と失敗時の扱い

回帰コマンドは`python -B -m unittest discover -s tests -v`。エンジン・GAの変更では決定論と中立遺伝子の試験も確認する。UI変更は保存・再読込・操作導線などをブラウザで確認する。ラン出力と検証成果はリポジトリ外のローカル保存先に置く。

CIが失敗したら、失敗したSHA・試験・ログを保存し、実装の不具合と実行環境による変動を証拠に基づいて区別する。再実行の成功だけで原因が解消したとは扱わない。緑にするためにassertion・制限時間を緩めたり、保護ルールを無効化したりしない。結果と残る制約をPRに記す。

mainに反映済みの変更を戻す場合も、revertを含むPRをdevelopへ作り、確認後mainへ反映する。常設ブランチの履歴をforce pushで書き換えない。

## 参照

- [GitHub: Rulesetで利用できるルール](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)
- [開発者向けガイド](DEVELOPMENT.md)
