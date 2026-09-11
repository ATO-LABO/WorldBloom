# Phase 3 実装計画: 共進化・出力段（あらすじ化 → 人間選定 → 本文化）・ビューア

作成: 2026-09-11（設計役 Fable）/ 親設計: `docs/2026-09-11_gapengine-detailed-design.md` §13・§14・§16 Phase 3 / 前提: Phase 2（D5/D5b/D6）合格 / ステータス: 実装指示（納品 D7 = 共進化、D8 = 出力段、D9 = ビューア）

## 0. 狙いと合格条件（設計書 §16 Phase 3）
1. 共進化 ON/OFF の両方で到達エリートが得られる（同一 N/G/K で、ON でもアーカイブが空にならない）。
2. あらすじ → 人間選定 → 本文の 1 周が回る（アーカイブの全エリートのあらすじ一覧 → 選定ファイル → 選ばれたものだけ本文化）。
3. ビューアでアーカイブ格子（カテゴリ × volatility）と各エリートの物語ログ・あらすじ・本文が閲覧でき、Dev-Launcher に登録される。

## 1. 共進化（設計書 §13）— D7

### 1.1 構成
- `--coevolve` を `scripts/evolve.py` に追加（既定 OFF）。ON のとき敵役（`world.antagonist`）にも `Policy` を付け、敵役の母集団 `B` と敵役アーカイブ `archive_antagonist.json` を別に持つ。
- 遺伝子は主人公と同じ 9 スカラー（`Genome`）。敵役用テンプレート `templates/momotaro/action_graph.antagonist.yaml` は任意（無ければ主人公と同じ action_graph）。canon は敵役用 `canon.antagonist.yaml`（任意、無ければ空）。
- **評価は交互**: 世代 g で主人公母集団 P_g を評価するとき、対戦相手は敵役アーカイブ B から決定論的に選んだ標本（`b_sample = archive_antagonist の全エリートを id 順に並べ、個体 index mod |B| で割り当て`。B が空なら `policy=None`）。次に敵役母集団 Q_g を評価するとき、相手は主人公アーカイブ A のエリート標本（同じ規則）。1 世代 = 主人公評価 → 敵役評価 の順（決定論）。
- 各ランの header に `genome`（主人公）と `antagonist_genome` を記録。アーカイブの exemplar には両方の genome を保存。

### 1.2 敵役の適応度（非ゼロサム、設計書 §13）
- 敵役の「品質」= 主人公が**到達したラン**の中で道中を最も険しくした度合い: `q_ant = 0.4 × min(1, turns/max_turns) + 0.3 × min(1, volatility/vol_high) + 0.3 × min(1, reversals/2)`（reversals = 真の差分 `S(主人公)−S(敵役)` の符号反転回数）。主人公が到達しなかったランは敵役アーカイブに入れない（結末を封じる敵役を選ばない）。
- 敵役アーカイブの記述子は主人公と同じ 2 軸（敵役の effective 決定の主導カテゴリ × 主人公の volatility）。
- 敵役の整形適応度（親選択用）: 主人公の `shaped` の補数ではなく、「主人公の到達ランで q_ant が高い」＋「未到達ランは 0」。

### 1.3 実装
- `gapengine/evolve.py`: `evolve()` に `coevolve: bool` を追加。`run_individual` の job に `antagonist_genome`（None 可）を足し、`Simulation(..., policies={protagonist: P, antagonist: Q})` を組む。`precedent` は主人公・敵役で別表（`precedent.json` / `precedent.antagonist.json`）。
- `gapengine/qd.py`: `antagonist_quality(rows, world_meta)`、`Archive` は role 別にファイルを分ける。
- `summary.json` に敵役側の到達率・占有マス・相異度を併記。
- テスト: 共進化 ON の小規模 evolve（`reached` を monkeypatch）が完走し、両アーカイブが非空、processes 1/2 でバイト一致。

## 2. 出力段（設計書 §14）— D8

### 2.1 場面抽出 `gapengine/scenes.py`
- 入力: エリートの模範ラン `layers.jsonl`。出力: 注目ターンの列（最大 12 件）。
- 選抜規則（決定論）: (a) 主人公の `effective` 決定のうち `|δ_t|`（層ベクトル差分 L1）上位、(b) 派生イベント `downed / revived / ally_gained / exposure / betrayal / payoff / planted / concede / threshold_crossed / ending`、(c) 目的物の所持者交代。重複ターンは 1 つに統合、ターン順に並べる。各注目ターンに「その時点の 7 層の要約」（述語名前空間から `strength / believed_strength / stance(主人公, 敵役) / holder / phase / vitality`）を付ける。

### 2.2 あらすじ生成 `gapengine/synopsis.py`
- `build_synopsis_prompt(elite, scenes, world_meta) -> str`: 世界の設定（`world.yaml` の `name` と主体の `identity.displayed`）、固定結末のラベル、注目ターンの列（ターン・主体・行動・結果・層の要約・伏線の説明文 `description`）を箇条書きにし、「200〜300 字のあらすじを、結末を明かしたうえで道中の転機を中心に書け」という指示を付ける。LLM には**構造化述語ではなく自然言語説明**を渡す（設計書 §データフォーマット）。
- `scripts/synopsize.py --archive <archive.json> --runs <root> --out synopses.json --backend {claude-cli, codex-cli, anthropic, openai, none}`: アーカイブの全エリートについてプロンプトを生成し、backend で短いあらすじを得て保存。`--backend none` はプロンプトだけを保存（LLM を使わない検証用）。
- backend 実装は StorySim の上演層の設計を参考に**書き直す**（コピーしない）。CLI backend は `claude -p` / `codex exec` をサブプロセスで呼ぶ。API キーは `settings.json`（.gitignore 済み）から読む。

### 2.3 人間選定と本文化
- `synopses.json` を人間が読み、`selection.json`（`{"selected": ["III|high", "I|mid"]}`）を書く（ビューア §3 からも書ける）。
- `scripts/narrate.py --archive ... --selection selection.json --out stories/`: 選ばれたエリートだけ、注目ターン列と 7 層の推移から本文（2,000〜4,000 字）を生成。プロンプトは「あらすじ」より詳細（各注目ターンの前後関係、伏線の設置と回収の対応、露見・裏切りの心理）。
- 決定論: LLM 出力は非決定だが、プロンプトはバイト一致で再生成できる（`prompts/` に保存）。

## 3. ビューア — D9
- `viewer/server.py`（標準ライブラリ `http.server`、依存追加なし、ポート 5401）。エンドポイント: `/`（実験一覧）、`/exp/<name>`（アーカイブ格子: 行 = カテゴリ、列 = volatility 段階、セルに q / reach_rate / 世代）、`/exp/<name>/cell/<key>`（模範ランのターン列と 7 層の推移グラフ（SVG）、あらすじ、本文、選定チェックボックス）、`/exp/<name>/selection`（POST で `selection.json` を更新）。
- `.claude/launch.json` に `worldbloom-viewer`（`python viewer/server.py --runs C:\Projects\WorldBloom-local\runs`）を登録し、Dev-Launcher に載せる。
- テスト: サーバーを起動 → `/` と格子ページを取得 → 停止（ポート解放まで確認）。

## 4. 乱数消費
共進化で敵役に Policy を付けても候補重みの乗算だけなので、乱数消費箇所は Phase 0 の 5 箇所のまま。

## 5. 納品分割
- **D7**: §1（`--coevolve`、敵役アーカイブ、非ゼロサム適応度、精度テスト）。
- **D8**: §2（scenes / synopsis / synopsize.py / narrate.py、`--backend none` のテスト）。
- **D9**: §3（ビューア、launch.json、テスト）。
- 合格判定: D7 後に本番実験（ON/OFF 各 N=100・G=20・K=3）、D8/D9 後に 1 周の手動確認（あらすじ生成は `--backend claude-cli` で実施し、選定は設計役が代行して本文化まで通す）。

## 6. Phase 3 共進化実験（2026-09-11、コミット 70a5fc6 の固定ワークツリー、`runs\exp5-coevolve` / `runs\exp5-off`、N=100・G=8・K=3）

| | 共進化 OFF | 共進化 ON |
|---|---|---|
| 主人公の到達率（最終世代） | 0.3% | 4.0% |
| 主人公アーカイブの占有マス | 4（III×3・I×1） | **10**（I×3・II×3・III×3・V×1） |
| 主人公アーカイブの相異度 | 0.85 | 0.79 |
| 敵役アーカイブ | — | 3 マス（I\|low/mid/high、q_ant 0.71〜0.97）、敵役側の到達率 10〜20% |

合格条件 1（ON/OFF 両方で到達エリートが得られる）✓。ON の方が主人公アーカイブの充填が速い（敵役が多様化することで主人公の到達経路も多様化する＝構想の「軍拡レース」が観察できた）。20 世代では共進化の評価が 2 倍になり約 4 時間かかるため 8 世代で比較した。
