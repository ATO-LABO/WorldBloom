# 公開前の情報確認

## コード・履歴・CI

Secret scanning、Push protection、Dependabot alerts/security updatesを有効にする。CodeQLはPython・JavaScript/TypeScript・Actionsを対象とする。通常の変更はdevelopへPRを作り、main反映は[既存の確認手順](BRANCHING.md)に従う。

Actionsは必要なアクションだけを許可し、ワークフローの参照は完全なコミットSHAに固定する。更新はDependabotのPRで確認する。SHA固定のリポジトリ強制設定は、main/developと進行中PRのワークフローを移行した後に有効化する。検査の導入前に強制して既存CIを止めない。

`security`ワークフローはGitleaksで到達可能な履歴と作業ツリーを検査する。検出値はログに出さない。Gitleaks本体はバージョンと配布アーカイブのSHA-256を固定して取得する。`security`の導入後、チェックがmain/developの全PRで動くことを確認してから`secrets`を必須チェックに加える。CODEOWNERSは担当者へのレビュー依頼先であり、単独運用で他者承認を強制する設定ではない。

`.env`や秘密鍵、実行用の`settings.json`はコミットしない。すでに追跡しているファイルは`.gitignore`だけでは除外できない。キーが公開された場合は、まず失効・再発行し、続いて履歴・PR・CIログ・成果物・Releaseを調べる。

コミットにはGitHubの非公開用`noreply`アドレスを使う。GitHubのSettings → Emailsでメールアドレスの非公開化と、通常メールを公開するコマンドラインPushの拒否を設定する。ローカル設定の例は次のとおり。

```sh
git config --local user.email '<GitHubが表示するnoreplyアドレス>'
```

過去の作者・コミッターメールを消す場合は、Git履歴を書き換える必要がある。作業中のPRとローカル変更を保全し、影響する全ブランチ・タグを確認してから実施する。元のメールを含むmailmapやバックアップを公開しない。書き換え後も既存clone、PR参照、キャッシュには旧履歴が残る可能性がある。通常のforce push禁止を一時的に例外扱いする操作は、対象SHAと手順を示して利用者の承認を得る。

## サンプル・公開サイト

`pack_samples.py`は生プロンプトを既定で含めない。公開する内容を確認済みの場合に限り`--include-prompts`を付ける。サンプル化と静的HTML出力は、認証情報・メール・非公開ファイルを検出した場合、既存出力の削除前に停止する。

```sh
python scripts/publication_guard.py samples
python scripts/publication_guard.py <静的サイトの出力先>
gitleaks dir <公開フォルダ> --redact=100 --no-banner
```

検査は、すべての秘密形式や個人情報を判別するものではない。本文・あらすじ・プロンプトに未公開資料、実在人物の情報、社内情報が入っていないか、人が内容を確認する。自動検査で止まった値を安易に除外設定へ追加しない。公開する必要がある連絡先は、その目的と公開範囲を確認してから検査ルールを変更する。

ドキュメントCIは生成サイトを検査してからアップロードする。成果物の保存期間は7日とする。CIログにも機密情報を出さず、実物のAPIキーをテストfixtureへ入れない。

MkDocs Material 9.7.7が同梱する3つの外部ライブラリファイルには、元作者の公開連絡先が著作権表記として含まれる。確認済みのファイル内容のSHA-256に限りメール検査の例外とし、認証情報の検査は継続する。依存更新で内容が変わった場合は再確認する。ディレクトリ全体やドメイン全体を除外しない。

## Windows配布とRelease

1. 公開用のチェックアウトとビルド環境を用意し、対象SHA、Python・PyInstaller・pywebview・PyYAMLの実際のバージョンを記録する。
2. 配布フォルダへ必要なファイルだけを組み立てる。`runs/`・`control/`・`.env`・秘密鍵を入れない。`settings.json`は公開用の既定値から作り、利用中の設定ファイルをコピーしない。
3. 信頼されたコード署名証明書が用意できた場合にexeへ署名し、`Get-AuthenticodeSignature`が`Valid`であることを確認する。証明書がない場合は未署名であることを明記する。秘密鍵や証明書パスワードを配布物へ含めない。
4. 完成したフォルダを検査し、各ファイルのハッシュを記録する。ハッシュはコード署名やビルド元の証明の代用にはならない。

```sh
python scripts/publication_guard.py <配布フォルダ> --checksums
gitleaks dir <配布フォルダ> --redact=100 --no-banner
python scripts/publication_guard.py <完成した配布ZIP>
```

5. Draft Releaseへ全成果物とZIPのSHA-256を揃えてから公開する。不変Releaseは今後公開するReleaseに適用される。既存Releaseの差し替えではなく、新しいバージョンを作る。

`v*`タグは`release-tags-no-rewrite`で更新・削除を禁止する。新規タグの作成は許可する。設定控えは[releases.json](../.github/rulesets/releases.json)。履歴のプライバシー修正で既存タグを移す場合も、通常の禁止設定を無断で迂回しない。
