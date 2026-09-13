# WorldBloom 実験ビューア UI/UX 設計（2026-09-12、設計役 Fable、ユーザー承認済み）

**この文書がビューア（`viewer/`）の設計の正本である。** 設計書 `docs/2026-09-11_gapengine-detailed-design.md` §14（出力段: あらすじ化 → 人間選定 → 本文化）は、ビューアを「アーカイブ格子ビュー＋物語ログ表示」と一文で定義し、人間選定の担い手と位置づけている。本書はその §14 の 3.（人間があらすじ一覧から複数選定）と 4.（ビューア）を、実際に人間が日常的に操作できる UI/UX として具体化するものであり、§14 の役割分担（抽出は `gapengine/scenes.py`、プロンプトは `gapengine/synopsis.py`、生成は `scripts/synopsize.py` / `scripts/narrate.py`）は変えない。ビューアはそれらの**消費者**であり、出力段の機構そのものは持たない。Phase 3 計画 `docs/2026-09-11_phase3-implementation-plan.md` §3 の D9 ビューア要件は本書で置き換える。

---

## 0. 要約

- **主役は「エリートの吟味と選定」**。「実験の評価」は実験ページ上部のストリップ（数値＋スパークライン）に圧縮し、専用画面は作らない。
- **物語は生ログではなく `gapengine/scenes.py` の場面抽出で読ませる**。`synopsize.py` / `narrate.py` が使っている `extract_scenes` と `describe_row`（日本語文生成）をビューアからそのまま呼ぶ。生ログは折り畳みの裏に残す。
- **技術方針は「stdlib `http.server` 維持、依存追加ゼロ、CDN なし、ビルドなし」**。単一ファイルはやめ、`viewer/{server,data,pages}.py` ＋ `viewer/static/{app.css,app.js}` に分割し、`gapengine` を import する（PyYAML のみ、リポジトリ方針と整合）。
- **生成（あらすじ・本文）は UI から起動する**（第 3 段）。既定バックエンドは `codex-cli`。
- 4 段階で作る。第 1 段（一覧の情報付加・格子の軸絞り・場面タイムライン）だけで体感は変わる。第 1 段の実装計画は `docs/2026-09-12_viewer-phase1-implementation-plan.md`。

---

## 1. 現状の診断（根拠つき）

### 1.1 実測（exp10-detective / II|high のエリート詳細、ビューポート幅 598px、2026-09-12）

| 項目 | 実測 |
|---|---|
| ページ全高 | **18,781px** |
| うち「模範ランのターン列」セクション | **17,484px（93%）**、206 行 |
| ターン列テーブルの幅 | 1,750px（表示枠 513px の **3.4 倍**、横スクロール必須） |
| details セルの最大文字数 | 692 文字（生 JSON） |
| 7 層グラフ | 511×217px、データ点は **7 点**（snapshot は 1 日 1 回のため） |
| あらすじ／本文 | 各 166px、「該当項目がありません」のみ |

画面の 93% が読めない表であり、物語を読む・選ぶという目的に対して情報の置き方が逆転している。

### 1.2 ターン列の中身（同じログの集計）

- 232 行 = decision 186 ＋ event 38 ＋ snapshot 7 ＋ header 1。
- decision 186 のうち**主人公（探偵）は 29、`effective` なものは 20**。残り 157 は NPC の `investigate`（70）・`share_knowledge`（32）・`persuade`（18）など。
- 物語上の転機（`rethink` の `details.before/after`、`confront` の `details.correct`、`learn_fact` の `details.beliefs[].outcome`、`exposure`、`ending`）は `details` の生 JSON の中に埋まっている。
- 桃太郎の exemplar は 418〜581 行、探偵・恋愛は 160〜235 行。どのジャンルでも「主人公の effective 決定」は 15〜50 件に収まる。

生ログの 8〜9 割は NPC の探索ノイズで、物語として読むべき行は 1 割強。`extract_scenes(limit=12)` はこれを抽出しており、プロンプトでは既に日本語文になっている（`exp1/prompts/synopsis-I-high.txt`）。ビューアだけがこの資産を使っていない。

### 1.3 実験一覧

- `archive.json` を持つディレクトリ 16 件がフラットに並ぶ。うち `accept1` / `review-acc` / `review-d2` / `review-d2c` は受け入れ・レビュー用の短いラン（3 世代、個体 12〜20）で、本番実験（exp1〜exp10、20 世代・100 個体）と区別がつかない。
- 表示は占有マス数と volatility 閾値の生 JSON のみ。**ジャンル・日付・到達率・世代数はすべて既存ファイルから取れるのに出していない**:
  - ジャンル: exemplar の `layers.jsonl` 先頭 header の `world`（「桃太郎」「放課後の約束」「白椿館の密室」）→ `projects/*/world.yaml` の `name` と一致
  - 日付: `archive.json` の mtime
  - 世代数・到達率推移・占有マス推移・平均品質・相異度: `summary.json` の `generations[]`（16 件すべてにある。exp1 は旧形式で `keep`/`seeds`/`target_ending` を欠く）
  - 個体数: `g0/population.json` の長さ、シード: `summary.seeds`
  - **コミット: どこにも記録されていない**（header の `engine_hash` 12 桁のみ）。`scripts/evolve.py` 側の変更が必要（第 4 段、採用決定済み）。

### 1.4 格子

- 全実験で `DEFAULT_CATEGORIES`（I〜VI）× 3 を固定描画。恋愛・探偵は `templates/<genre>/qd.yaml` で `[I, II, III]` と宣言済みなので、IV〜VI の 3 行は常に空。
- セルには q / reach / generation の数値だけ。**どんな物語かの手掛かりがゼロ**なので、9 セルを 1 つずつ開いて戻る操作になる。

### 1.5 出力段の実態（2026-09-12 時点、最新）

- **あらすじ**: `exp7-romance`（6 件）と `exp10-detective`（9 件）は `codex-cli` で生成済み、**全 15 件 `status: ok`**（設計案提示後に実施）。`exp1` / `exp4` は `backend: none` の `prompt_only`。他は未生成。
- **選定**: `selection.json` は**全実験に存在しない**（0 件。ビューア操作で空ファイルが作られた事故が 1 回あり、削除済み）。
- **本文**: `stories/` は**どこにもない**（0 件）。
- 生成の失敗例（実運用で観測）: `claude-cli` は OAuth 期限切れ（`exp10-synopsize.log`: `OAuth access token has expired`）、`codex-cli` は `~/.codex/config.toml` の既定モデル `gpt-6-astra` が CLI 0.144.1 で未対応。いずれも CLI の stderr にしか出ないため、**失敗理由を画面に出す**設計が必要（§5）。
- ルート `settings.json`（gitignore 済み）には `{"output": {"codex-cli": {"model": "gpt-5.6-sol"}}}` を置いてある。無い環境では `prompt_only` に落ちるので、ビューアは**無い場合の案内**を出す。

設計書 §16 Phase 3 の合格条件②「あらすじ→選定→本文の 1 周が回る」は機構としては通ったが、**運用としては選定と本文が未完走**。ビューア再設計は「この 1 周を人間が実際に回せる状態にする」ことと不可分であり、生成の導線を第 3 段に置く理由である。

### 1.6 7 層グラフ

- snapshot が 1 日 1 回なので x 軸の実体は **turn ではなく day**（7〜16 点）。ラベルは turn 番号だが、イベントは turn 単位なので対応が取れない。
- vector の第 11 成分（vitality）は描かれていない。downed / revived は物語上の大きな転機なのに図に出ない。
- 凡例は 7 本を一列に並べた静的テキストで、線との対応を目で追うしかない。

---

## 2. UI の役割の再定義と優先順位

| 役割 | 位置づけ | 判断の根拠 |
|---|---|---|
| **2. エリートの吟味** | **主役** | 設計書 §14 でビューアが担うのは「人間選定」。選定は吟味の結果であり、吟味に必要な情報（場面・転機・状態・遺伝子）を最短で読める形にすることが価値の大半 |
| **3. 選定（＋本文化への接続）** | 主役の出口 | 選定済み 0 件という事実が、吟味から選定・本文化までの導線の断絶を示す。選定は「吟味中に 1 操作で印を付け、あとで俯瞰して本文化する」流れにする |
| **1. 実験の評価** | 脇役（圧縮） | GA が回ったかは `summary.json` の 4 系列（`reach_rate` / `occupied_cells` / `average_archive_quality` / `archive_dissimilarity`）で 1 行に要約できる。実験は使い捨て（16 件中 12 件は既に参考値）。深い分析は `scripts/analyze.py`（未作成、Parquet/DuckDB 側）の領分 |

**画面は役割ではなく対象の粒度で分ける**（実験一覧 → 実験＝格子 → エリート）。各画面の中で「評価情報は上部に薄く、吟味情報は中央に厚く、選定操作は常に手元に」という配置原則で統一する。

---

## 3. 画面ごとの情報設計

### 3.1 `/` 実験一覧

```
WorldBloom                                   [選定一覧 (n)]
────────────────────────────────────────────────────────────
▼ 探偵 白椿館の密室
 ┌────────────────────────────────────────────────────────┐
 │ exp10-detective     2026-09-12 10:06   engine b7f7c2d0 │
 │ 20世代 · 100個体 · 3シード · 結末 solved · 真相 甲/乙/丙 │
 │ 到達率 6% → 20%  ▁▂▂▃▃▄▄▅   占有 9/9   q̄ 0.56           │
 │ あらすじ 9/9 · 選定 0 · 本文 0                    [開く]│
 └────────────────────────────────────────────────────────┘
▼ 恋愛 放課後の約束     … exp7-romance (7%→33%, 6/9, あらすじ 6/6) …
▼ 桃太郎                … exp5-coevolve / exp5-off / exp4 / … …
▸ その他の短いラン（accept1, review-acc, review-d2, review-d2c）   ← 折り畳み
```

- 出す: 名前・ジャンル（world 名）・日付・世代/個体/シード・結末・到達率の始点→終点＋スパークライン・占有マス「n/N」（N は qd.yaml の格子サイズ）・平均 q・あらすじ/選定/本文の件数・engine_hash・（探偵）exemplar 群の真相の集合。
- 出さない: volatility 閾値の生 JSON（実験ページへ移す）。
- 分類: ジャンルごとにグループ、各グループ内は日付降順。**世代数 < 8 のランは「その他の短いラン」に自動で折り畳む**（規則は定数 1 か所。手動フラグは作らない＝決定 6）。

### 3.2 `/exp/<name>` 実験（格子＋選定トレイ）

```
exp10-detective  探偵 白椿館の密室  2026-09-12        [← 一覧] [選定一覧]
────────────────────────────────────────────────────────────────────
到達率 ▁▂▃▃▄▄▅ 6%→20% │ 占有 ▃▆▇▇▇▇▇ 9/9 │ q̄ ▂▄▅▅▆ 0.47→0.56 │ 相異度 0.67
volatility 閾値 low ≤ 0.204 < mid ≤ 0.242 < high  ·  結末 solved  ·  keep=reached
────────────────────────────────────────────────────────────────────
           low                 mid                 high
   ┌─────────────────┬─────────────────┬─────────────────┐
 I │ ☆ q .56 到達33% │ ☆ q .55 到達33% │ ★ q .57 到達33% │
   │ g6  ▮▮▮▮▯▯ II   │ g7             │ g2              │
   │ 「探偵が乙を疑い │ 「…」          │ 「…」           │
   │  →考え直し→甲」 │                │                 │
   │ □比較           │ □比較          │ □比較           │
 II│ …               │ …              │ …               │
III│ …               │ …              │ …               │
   └─────────────────┴─────────────────┴─────────────────┘
────────────────────────────────────────────────────────────────────
選定トレイ: ★ I|high  ★ II|mid       [比較 (2)]  [あらすじを生成 0/9]  [選定を本文化 (2)]
```

- 行は **qd.yaml の categories だけ**（探偵・恋愛は 3 行、桃太郎は 6 行）。アーカイブに現れた想定外カテゴリだけ追加。
- セルに出す: 選定 ★/☆（クリックで即保存）、q、到達率、世代、遺伝子の主導カテゴリ（`category_weight` の argmax を小さなバーで）、**1 行フック**（あらすじがあれば先頭 50 字、無ければ `extract_scenes` の優先度上位 2 場面の事象文を「→」で繋いだもの）、比較チェック（第 2 段）。
- 選定トレイは画面下部に固定。これが「選定の俯瞰」の最小形。生成ボタンは第 3 段、それまでは「コピー用コマンド」を出す。

### 3.3 `/exp/<name>/cell/<cell>` エリート（吟味の本体）

```
[← 格子]  exp10-detective / II|high     ★ 選定中 (s)     [◀ I|high] [III|low ▶]
────────────────────────────────────────────────────────────────────
q 0.571 · 到達 66.7% · g9 · seed 3 · 親 g7/III-low × g5/II-high
遺伝子  I ▮▮▯▯  II ▮▮▮▮  III ▮▮▯▯  IV ▮▮▮▯  V ▮▮▮▯  VI ▮▮▮▮ │ risk .79  stance +.58  novelty .00
────────────────────────────────────────────────────────────────────
7層の推移（x = 日）           [能力][認識][資源][フェーズ][身分][対象][遅延] ← クリックで表示切替
  ╭──────────────────────────────╮
  │   ／￣＼＿＿／￣￣            │   ▼ downed  ▲ revived  ● 結末  を x 軸上にマーカー
  ╰──────────────────────────────╯
   1   2   3   4   5   6   7   8   ← 日をホバー→下のタイムラインの該当日を強調（逆も）
────────────────────────────────────────────────────────────────────
物語（注目 12 場面）      [主人公の全決定] [NPC の行動も] [生ログ ▸]
第1日  客室 · alive · 強さ 72 / 甲の見積 45 · 犯人: 丙 (0.35)
  T1 朝  探偵が（書斎）調べた。…                    [有効な決定]
第4日  客室 · … · 犯人: 乙 (0.72)
  T13 朝 探偵が rethink — 犯人: 乙(0.72) → 保留。証拠: 第二のアリバイ, 凶器の痕跡, 泥の足跡   [転機 · Δ 0.31]
第8日  …
  T29 朝 探偵が（容疑者甲, culprit）問い詰めた。正解（確信 0.75）        [転機]
  T31 夜 探偵が「探偵は証拠を組み直し、白椿館の真犯人を指摘した」という結末に到達した ●
────────────────────────────────────────────────────────────────────
あらすじ  [ok · codex-cli]  …本文…            本文  [未生成 · 選定すると本文化できます]
```

- **場面タイムライン**が中心。`extract_scenes(rows, world_meta, limit=12)` の出力（`turn/day/slot/events[]/reasons[]/delta_l1/foreshadowing[]/state`）を日ごとにグループ化して縦に並べる。事象文は `describe_row` の日本語。理由タグ（主人公の有効な決定／重要な派生イベント／目的物の所持者交代）と Δ をバッジで。
- **日見出しの状態チップ**は snapshot（1 日 1 回）から直接取る: 場所・vitality・強さ（`ability.base`＋active な `modifiers[].value`）に加えて、**ジャンル依存の要点**を `snapshot.layers` から: 探偵は `valued_beliefs`（犯人・凶器の値と確信度）、恋愛は主人公↔敵役の affinity 両方向（`relations`）、桃太郎は目的物の所持者（`objective`）と通過した節目（`phase`）。**ビューア側の読み取りで済み、`scenes.py`（プロンプトのバイト安定性に関わる）は触らない。**
- **3 段階の展開（決定 1）**: 既定＝注目 12 場面／「主人公の全決定」＝主人公の全 decision（探偵 29、桃太郎 40〜50）／「NPC の行動も」＝turn ごとに `<details>` で畳んだ NPC 行。生ログ表は最下段の `<details>` に残す（削除しない）。
- **転機の補足行**（ビューア側で生成）: `rethink` は `details.before/after` の信念差分、`confront` は `details.correct` と `confidence`、`learn_fact` は `details.beliefs[].outcome`（adopted/reinforced/refuted）。`exposure`/`betrayal`/`payoff`/`ending` は `describe_row` の文で足りる。これが「設計意図どおりの経路か」（誤認→rethink→正解）を一目で確認する手段になる。
- 7 層グラフは既存 SVG を流用し、x を day に、凡例をクリック可能に、vitality をマーカーで追加、ホバーでタイムラインと相互強調。
- 前後セル（格子順）へのリンクを見出しに置く。キーボード（`,` `.` `s` `c` `g` `?`）は第 2 段。

### 3.4 `/exp/<name>/compare?cells=a,b,c`（第 2 段、最大 4 列、**テキスト主・軌跡従**＝決定 4）

```
            II|high            │ I|low              │ III|mid
q/到達/世代 .57 / 67% / g9     │ .56 / 33% / g6      │ …
あらすじ    …（あれば全文）    │ …                  │ …
場面 8      T1 … / T13 … / … │ …                  │ …
遺伝子      ▮▮▮▮ (II主導)      │ ▮▮▮▮ (I主導)        │ …
7層 小図    [能力][認識]…      ← 最下段。層ごとに 1 パネル、セルごとに 1 本の線
選定        ★ (s)              │ ☆                  │ ☆
```

- あらすじ → 場面 → 遺伝子 → 軌跡の順（テキストが上、曲線は下）。列ヘッダ固定、縦は同期スクロール。比較セットは格子のチェック＋エリートページの `c` で作り、sessionStorage に保持。

### 3.5 `/selected` 選定一覧（第 2 段、実験横断）

| 実験 | ジャンル | セル | q | 到達 | あらすじ | 本文 | |
|---|---|---|---|---|---|---|---|
| exp10-detective | 探偵 | II\|high | .57 | 67% | ok | 未 | [開く] [外す] |

- 実験ごとに「本文化（n 件）」ボタン（第 3 段）。「本文化したいものが揃っているか」を見る場所。

---

## 4. 技術方針の判断

### 4.1 判断: **stdlib 維持・分割・gapengine 再利用・vanilla JS・CDN なし・ビルドなし**

| 候補 | 判断 | 理由 |
|---|---|---|
| A. 現状維持（単一ファイル、全部 f-string） | × | 1,239 行に HTML/CSS/JS/データ処理が混在。場面・比較・ジョブを足すと破綻する |
| **B. stdlib `http.server` 維持＋モジュール分割＋静的ファイル＋JSON API** | **○ 採用** | 依存ゼロを守れる。必要な仕組み（場面抽出・比較・選定俯瞰・ジョブ起動）はすべて Python 側のデータ整形と数百行の vanilla JS で実現できる規模（16 実験 × ≤18 セル × ≤600 行ログ） |
| C. Flask/FastAPI＋Jinja | × | 得るのはルーティング糖衣だけ。`engine/gapengine は PyYAML のみ` の方針の外側に「ビューアのための依存」を作る理由が無い |
| D. SPA（React/Preact を CDN） | × | オフライン時に壊れる、CSP を緩める、状態管理が増える。単一利用者・ローカルで得るものが無い |
| E. 静的サイト生成 | × | 選定 POST とジョブ起動にサーバが要る |

「工数削減より仕組みの実現を優先」の原則に照らすと、**実現すべき仕組みは「場面構造化・状態チップ・比較・選定俯瞰・生成起動」であり、フレームワークの有無に依存しない**。B で仕組みは全部作れる。

### 4.2 構成

```
viewer/
  __init__.py  （空。tests から `viewer.data` を import するため）
  server.py    ルーティング・HTTP・静的配信・（第 3 段）ジョブ登録。ThreadingHTTPServer 維持
  data.py      RunRepository（既存を移設）＋ 実験メタ（summary/header/qd.yaml/world.yaml の読み取り）
               ＋ gapengine 呼び出し（read_rows / extract_scenes / describe_row / load_world_meta / Archive）
  pages.py     HTML 断片生成（html.escape 規律は現状どおり）、SVG（7 層・スパークライン）
  static/app.css, app.js   CSP は 'self'。inline script を廃止し script-src から 'unsafe-inline' を外す
```

- `gapengine` の import は `scripts/*.py` と同じ `sys.path` 挿入。ジャンル → `projects/<g>` / `templates/<g>` の解決は header.world と `world.yaml.name` の一致（一致が無ければ「不明」で場面文は落ちない）。
- サーバ側 HTML 生成を基本にし、JS は ①選定 POST ②ホバー連動 ③凡例トグル ④比較セット（第 2 段）⑤ジョブのポーリング（第 3 段）⑥キーボード（第 2 段）に限定。展開モードはクエリ文字列（`?view=digest|decisions|all`）で切り替え、JS を要さない。
- 長時間ジョブは `subprocess.Popen` ＋ プロセス内辞書（デーモンスレッド）。サーバ再起動で進行状態は消えるが、成果物ファイルの status から復元できる。SSE/WebSocket は不要、2 秒ポーリング。
- 既存 `tests/test_viewer.py`（起動→取得→POST→停止→ポート解放）は維持し、場面レンダリングとジョブ登録の LLM 不要テストを追加。

---

## 5. 生成の導線（決定 2・3: UI から起動、既定 `codex-cli`、第 3 段）

- `POST /api/exp/<name>/jobs {kind: "synopsize"|"narrate", backend, cells?}` → `python scripts/synopsize.py …` / `narrate.py …` を Popen。**1 実験につき同時 1 ジョブ**、超過は 409。backend の既定は `codex-cli`（`settings.json` の `output.codex-cli.model` で固定）。
- 進捗: `GET /api/jobs/<id>` が state（running/done/failed/cancelled）・経過秒・stderr 末尾・完了件数を返す。完了件数は成果物（`synopses.json` / `stories/index.json`）を読んで数える。
- **必要な小変更（`scripts/` 側。`engine`/`gapengine` には触れない）**: ①`synopsize.py` に `--cells` を追加し、指定セルだけ生成して既存 entries にマージ（現状は全件上書き・末尾一括書き込み）②両スクリプトを「1 件終わるごとに atomic replace で書き出す」に変更。CLI の既定挙動（引数なし＝全件）と `tests/test_output.py` は不変。
- 失敗: 終了コード≠0 または `entry.status == "error"` を赤帯で表示し、stderr 末尾（例: `OAuth access token has expired`、`gpt-6-astra` 未対応）と対処の案内を出す。`settings.json` が無い／backend=none のときは「prompt_only（LLM 未設定）」と明示し、生成ボタンの代わりにプロンプトファイルへのリンクと `settings.json` の例を出す。
- キャンセル: `terminate()`。部分的に書き出された成果物は残す。
- **CSRF ガード（必須）**: 状態変更系 API はカスタムヘッダ `X-WorldBloom-Client: 1` を要求（他オリジンのフォーム POST は付けられず、fetch は preflight で落ちる）。ジョブは LLM 費用を伴うので、選定 POST よりも防御を一段上げる。
- 是非の判断: 単一利用者・loopback 限定・実行対象はリポジトリ内の固定スクリプト 2 本・引数はサーバが組み立て（ユーザー入力は backend の列挙値とセル名のみ、セル名は archive に存在するものだけ許可）。この条件なら妥当。第 1 段では「コピー用のコマンド文字列」を表示する（第 3 段までの橋渡し）。

---

## 6. 段階分けと合格条件

| 段 | 内容 | 合格条件（測定可能なもの） |
|---|---|---|
| **1. 読める化**（体感が変わる最小） | ①一覧: ジャンル別グループ・日付・世代/個体/シード・到達率スパークライン・占有 n/N・件数、短いランの折り畳み ②格子: qd.yaml の行だけ、1 行フック、★選定、選定トレイ ③エリート: 遺伝子バー・場面タイムライン（日グループ＋状態チップ、3 段階展開、生ログは `<details>`）・7 層グラフの x=day 化と vitality マーカー・「生成コマンドをコピー」 ④モジュール分割・静的ファイル化・script の `unsafe-inline` 撤廃 | exp10 II\|high の既定表示で **ページ高 < 5,000px（現 18,781）**・幅 1,000px 以上で横スクロール無し／探偵・恋愛の格子が 3 行／一覧 16 件全てにジャンル・日付・到達率が出る／`test_viewer.py` 通過＋場面レンダリングのテスト追加／`pip` 追加なし・外部 URL 参照なし |
| **2. 比較と俯瞰** | 比較ページ（≤4 列、テキスト主）・`/selected`・キーボード操作・グラフ⇄タイムライン相互強調の仕上げ | 格子で 2〜4 セルを選び 1 クリックで比較が開く／`/selected` に実験横断の選定と生成状態が出る／`,` `.` `s` `c` が効く |
| **3. 生成の導線** | ジョブ API・進捗・失敗表示・CSRF ヘッダ・`synopsize.py --cells`＋逐次書き出し・`narrate.py` 逐次書き出し | UI から「あらすじ生成」→ 進捗 → `synopses.json` に反映 → ページに表示、まで再読み込みなしで完了／失敗時に stderr 末尾が画面に出る／CLI 単体の既定挙動と `test_output.py` は不変／1 実験 1 ジョブが強制される |
| **4. 由来の記録（採用）** | `scripts/evolve.py` が `summary.json` に git commit・argv・開始時刻を記録、一覧と詳細に表示 | 新規ランの一覧カードにコミット短縮ハッシュが出る（過去ランは「未記録」） |

各段は独立してユーザー確認 → 実装（AB テスト方式）→ Opus レビュー → Fable 最終確認の標準フローに乗せる。第 1 段は変更ファイルが `viewer/` と `tests/` に閉じ、`engine`/`gapengine`/`scripts` に触れない。

---

## 7. 決定事項（2026-09-12、ユーザー回答）

| # | 論点 | 決定 |
|---|---|---|
| 1 | 物語の既定の粒度 | **注目 12 場面**を既定。主人公の全決定と NPC の行動は展開式 |
| 2 | 生成の導線 | **UI から起動する**（第 3 段を採用） |
| 3 | 既定バックエンド | **`codex-cli`**。モデルは `settings.json` の `{"output": {"codex-cli": {"model": "gpt-5.6-sol"}}}` で固定（gitignore 済み）。無い場合の案内を出す |
| 4 | 比較の主役 | **物語のテキスト**（場面・あらすじ）。7 層の軌跡は従 |
| 5 | 選定の単位 | **セルごとに 1 つ（現状維持）**。`selection.json` の形式は変えない |
| 6 | 一覧の整理 | **自動で折り畳む**（世代数が少ないランを「その他」へ）。手動フラグは作らない |
| 7 | 由来の記録 | `scripts/evolve.py` に git commit・argv・開始時刻を `summary.json` へ記録する変更を**採用**（第 4 段） |

---

## 8. やらないこと

- 多人数共有・認証・リモート公開（loopback 固定のまま）
- 進化（`evolve.py`）の UI からの起動、遺伝子やテンプレートの編集
- あらすじ・本文のインライン編集（生成物はファイルが正本。編集はエディタで）
- Notion への自動転記
- 実験横断の統計分析（Parquet/DuckDB は `scripts/analyze.py` の領分）
- モバイル最適化（幅 1,000px 以上を前提。狭い幅では崩れないだけ）
- `gapengine/scenes.py` / `gapengine/synopsis.py` の変更（プロンプトのバイト安定性に関わる。状態チップの拡張が欲しくなったら別途設計役ゲート）
- 過去の世代でセルから押し出されたエリートの閲覧（`results.json` に残ってはいるが、アーカイブの設計を変える話）
- 同じ遺伝子の他シードの閲覧・選定（決定 5）

## 9. 承認済みWorkbench契約とUI-002（2026-09-13）

正本は [UI-001 設計契約 v1+r1](https://app.notion.com/p/3d9e21ef1cac81208910f78bb48c1509)、
[設計再レビュー合格](https://app.notion.com/p/3dae21ef1cac8190b633e263b1565b3a)。
この節は §4〜8 の旧計画と矛盾する場合に優先する。実装済みと設計受入済みを区別する。

- 新導線は設定セット → GA実行・進捗 → 結果一覧 → Sifting → 上映生成・作品一覧。
  stdlib HTTP、既存HTML/JS、既存URLを維持する。
- GA起動を対象に追加。プロセス内辞書は永続ジョブ台帳へ変更し、
  冪等request_id、停止・再接続・再起動照合を提供する（003/004）。
  厳密な途中再開は対象外で、再実行は新run_id。
- 過去世代・他seedの候補も不変candidate_idで扱う（004/005）。
  既知ログSHAは剪定後も保持し、残存状態を分ける。旧selectionは互換投影を維持。
  初期の選定・生成は終了済みrunに限定する。
- 生成は明示modelと上限を必須にする。設定ファイル不在だけでprompt_onlyと判断しない。
  UI用の型付きadapterと永続receiptを006で実装し、結果不明を自動再送しない。
  生成と読者要約の承認は別経路。scenes/synopsisのコード・プロンプトを保持。
- 開始・停止・エラー・戻り操作は1440/600pxとキーボードで受入する（007〜009）。
- 実装担当と合格後のコミットはCodex、レビューは別Codexタスク。

### UI-002のPythonサービス

所有: execution/configs.py、execution/provenance.py、execution/__init__.py、
tests/test_execution_configs.py。この節は承認設計の実装接続を記録する。
UI-002はHTTPルート、起動・停止、画面を追加しない。

ConfigStore(repo_root, control_root, runs_root)はrepo外に分離した管理・実行rootを受け取る。

| 呼出し | 効果 |
|---|---|
| preview(spec, settings_path=...) | 固定用バイトを一時領域で検証して実効設定を返す。管理領域への保存・GA/LLM起動なし |
| save(spec, config_id=..., settings_path=...) | 新しい不変設定版と入力manifestを保存 |
| get(config_id) / list() | 完成版をhash検証して返す。不完全な.pending版は列挙しない |
| duplicate(config_id, changes, new_id=...) | 元設定の固定入力を使って新しい版を作る。現在の原本へ切り替えない |
| check_generation(config_id, settings_path=...) | 保存済みmodelを維持しつつ現在の実行ファイル・資格情報有無を再確認。認証の有効性は未確認 |
| prepare_run(config_id, run_id=..., job_id=...) | 新しいrunへ入力と実行コードを固定しmanifest/argvを返す。プロセスは起動しない |
| verify_run(run_id) | 起動直前・再接続時に固定入力・コード・configのhashとファイル集合を照合 |
| legacy_settings(recorded_summary) | 保存された旧設定だけを返す。欠けた項目はnull、現行原本から補完しない |

specはlabel/project_id/template_id/evolution/execution_limits/generationを持つ。
GA値はCLI既定に一致させる。整数欄でbool・文字列・小数を拒否、説明記録は明示する。
既存World/Subject/Simulationの初期化と固定テンプレートで事前検証する。
必要canonと人物・結末を検査し、世界・人物の固定表示用データ、解決済み結末、
評価予定数、省略ファイルの実効fallback、編集不可の突然変異確率をpreviewへ返す。
画面はpreview.generationの保存時の可否を現在の可否として断定せず、
生成確認時にcheck_generationを呼ぶ。available=trueでも認証有効性はunverified。

設定版はcontrol/configs/<id>へ、runはruns/<id>へ保存。
入力のworld/subjects/テンプレートとworld側action_graph・effects参照を閉包に含める。
元worldの参照パスを固定コピー内の相対パスへ置換した場合、
source_sha256/source_bytesと保存後sha256/bytes、変換理由をmanifestへ併記する。
projects/templates以外への参照、リンク、任意path/command/資格情報の入力を拒否する。
設定ファイル・認証値はコピーせず、LLM可否には許可した情報だけを返す。

コード固定はengine/gapengine/scripts/executionのPythonソースとrequirements。
manifestにHEAD、dirty、実ファイルSHA、Python/PyYAML版、全実効argv、種、時刻を記録する。
準備済みargvは -I -B と固定runtimeのCLI入口を使い、multiprocessing子も同じruntimeを読む。
Python実行バイナリ・インストール済み依存ライブラリそのものの配布は本カードの対象外。
実行時のバージョンはmanifestへ記録し、003の起動で環境変更を検査する。

保存はOSファイルロック＋同一ボリュームの非公開staging→完成seal→公開rename。
既存IDは上書きせずconflict。不完全保存は履歴として公開しない。
ConfigErrorはcode/field_errors/retryableを返す。003/007のHTTP接続で
入力不正422、競合409、存在しないID404、OS権限/保存障害は適切なサーバーエラーへ変換する。
wall_secondsは003/004が強制する。本サービスだけで時間停止を実施したとは扱わない。

### 後続への受け渡し

002→003→004→005の順で基盤を受け渡す。006が生成、007が操作画面、
006/007後に008が上映画面、009が別担当の全導線受入。
候補台帳・公開revision・選定revisionと生成要求の型・hash規則は承認契約§13/15、
結果不明・保存障害・終端状態表は§14を正本とする。
