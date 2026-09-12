# ビューア第 1 段「読める化」実装計画（2026-09-12、設計役 Fable）

設計の正本は `docs/2026-09-12_viewer-ux-design.md`（以下「設計」）。本計画は設計 §6 の第 1 段を、Codex GPT-5.6 sol にテキスト納品させるための実装指示として書く。実装役はこの文書だけで作業できることを前提に、参照すべき既存コードの関数名・シグネチャ・データのキーをすべて明記する。

---

## 0. 狙いと範囲

- 狙い: 実験一覧に「何の実験か」を出し、格子をジャンルの軸に絞り、エリート詳細を**生ログの表ではなく場面のタイムライン**として読めるようにする。
- 範囲: `viewer/` の再構成と `tests/` へのテスト追加のみ。**`engine/`・`gapengine/`・`scripts/`・`templates/`・`projects/`・`.claude/launch.json` は変更しない。**
- 依存: 追加なし（PyYAML は `gapengine.synopsis` 経由で既に必須）。CDN・外部 URL・ビルド工程を持ち込まない。
- 起動方法は不変: `python viewer/server.py --runs C:\Projects\WorldBloom-local\runs --port 5401`（`.claude/launch.json` の `worldbloom-viewer`）。

## 1. 制約（厳守）

1. **`gapengine/scenes.py` と `gapengine/synopsis.py` は変更しない。** 両者はあらすじ・本文プロンプトのバイト安定性（`tests/test_output.py`）を担う。ビューアは両者を呼ぶだけで、足りない情報（`rethink` の差分など）は `viewer/data.py` 側で補う。
2. `engine/`・`gapengine/` のいかなるファイルも変更しない（決定論・golden hash の対象）。
3. 既存テスト `tests/test_viewer.py` を変更せずに通すこと（§7）。
4. 乱数を使わない。ページ生成は同じ入力に対して同じ HTML を返す（テストで文字列検査するため）。
5. HTML に埋め込む値は `html.escape` を通す（既存 `server.py` の規律）。`<script>` の inline コードを廃止し、CSP の `script-src` から `'unsafe-inline'` を外す。JS に渡すデータは `data-*` 属性か `<script type="application/json" id="…">`（実行されないので CSP 対象外）で渡す。`style-src` は `'self' 'unsafe-inline'` のまま（SVG の属性描画を単純に保つため）。
6. パス安全性は既存 `RunRepository.safe_path` / `validate_segment` の規律を維持する。静的ファイルは `viewer/static/` 直下の**固定ファイル名の許可リスト**（`app.css`, `app.js`）だけを配信する。

## 2. 対象ファイル

| ファイル | 種別 | 内容 |
|---|---|---|
| `viewer/__init__.py` | 新規（空） | `tests` から `viewer.data` / `viewer.pages` を import するため |
| `viewer/data.py` | 新規 | `RunRepository`（`server.py` から移設、内容不変）＋ 実験メタ・ジャンル解決・場面ビュー・層系列（§4） |
| `viewer/pages.py` | 新規 | HTML/SVG 生成（§5）。`server.py` の `_document` / `_index_page` / `_experiment_page` / `_cell_page` / `_layers_svg` / `_turn_table` / `_synopsis_panel` / `_story_panel` の後継 |
| `viewer/static/app.css` | 新規 | 既存 `_document` の CSS を移設し、タイムライン・格子・チップ・スパークライン用を追加 |
| `viewer/static/app.js` | 新規 | 選定 POST（既存 inline script の移設）、7 層凡例トグル、グラフ⇄タイムラインの日ホバー連動 |
| `viewer/server.py` | **全文置換**（既存を上書き） | ルーティング・HTTP・静的配信のみ。ページ生成とデータ読み取りは `pages` / `data` に委譲。`ViewerServer` / `ViewerHandler` / `build_parser` / `main` の名前と CLI 引数は維持 |
| `tests/test_viewer_pages.py` | 新規 | サーバを起動せずに `data` / `pages` を検査する LLM 不要テスト（§7） |

`server.py` はスクリプトとして直接起動される（`python viewer/server.py`）ため、先頭で `ROOT = Path(__file__).resolve().parents[1]` を `sys.path` に挿入してから `from viewer import data, pages` する（`scripts/synopsize.py` と同じ作法）。`data.py` も同様に `ROOT` を挿入してから `gapengine` を import する。

## 3. `gapengine` から使う関数（シグネチャは実コードで確認済み）

| 関数 | 所在 | シグネチャ | 用途 |
|---|---|---|---|
| `read_rows` | `gapengine/qd.py` | `read_rows(path: str \| Path) -> list[dict[str, Any]]` | `layers.jsonl` の読み込み（既存 `_read_jsonl` の代替） |
| `extract_scenes` | `gapengine/scenes.py` | `extract_scenes(rows, world_meta, *, limit: int = 12) -> list[dict[str, Any]]` | 注目場面の抽出。既定表示は `limit=12`、「主人公の全決定」表示は `limit=len(rows)`（候補は全部残る。候補 = 主人公の effective 決定・`SPECIAL_PRIORITY` の動詞・目的物の所持者交代を含む turn） |
| `describe_row` | `gapengine/scenes.py` | `describe_row(row, world_meta) -> str` | 任意の decision / event 行を日本語 1 文にする。「NPC の行動も」表示と生ログの代替文に使う |
| `load_world_meta` | `gapengine/synopsis.py` | `load_world_meta(project_dir, template_dir) -> dict[str, Any]` | `display_names` / `ending_labels` / `effect_descriptions` / `protagonist` / `antagonist` / `target_ending_label` などを得る。ジャンルが解決できない実験には**空の dict に header の `protagonist` / `antagonist` / `world`（→ `name`）だけ入れた fallback** を渡す（`extract_scenes` / `describe_row` は `.get` で読むので落ちない） |
| `Archive` | `gapengine/qd.py` | `Archive.load(path) -> Archive`、`archive.cells: dict[tuple[str, str], Elite]`、`archive.volatility_thresholds` | 使わなくてもよい。既存どおり `archive.json` を dict で読む方が単純なので、**第 1 段では既存の dict 読み（`RunRepository.archive`）を維持**し、`Archive` は import しない |

`extract_scenes` の戻り値（1 場面）のキー: `turn`(int) / `day`(int) / `slot`(str|None) / `delta_l1`(float) / `priority`(int) / `reasons`(list[str]) / `events`(list[str]、`describe_row` の文) / `state`(dict: `strength` / `believed_strength` / `stance` / `holder`{item: holder} / `phase`[list] / `vitality` / `zone`) / `foreshadowing`(list[str])。

## 4. `viewer/data.py` の API

既存 `RunRepository`（`__init__` / `validate_segment` / `safe_path` / `experiment` / `experiments` / `archive` / `selection` / `write_selection`）と例外 `ForbiddenPath` / `MissingResource` / `BadRequest`、補助 `_inside` / `_read_json` / `_number` / `_json_text` / `_display` を `server.py` から**そのまま移設**する。加えて次を実装する。

```python
ROOT: Path                      # リポジトリルート
MINOR_GENERATIONS = 8           # これ未満の世代数のランは「その他の短いラン」に折り畳む
LAYER_SERIES: tuple[tuple[str, str], ...]   # 既存の 7 層ラベルと色（移設）

def resolve_genre(world_name: str) -> tuple[str, Path, Path] | None:
    """projects/*/world.yaml の name が world_name に一致する (genre, project_dir, template_dir) を返す。無ければ None。"""

def qd_axes(template_dir: Path | None) -> tuple[list[str], list[str]]:
    """templates/<g>/qd.yaml の categories / volatility_bins。None または欠損時は既定 (I..VI, low/mid/high)。"""

def world_meta_for(header: Mapping[str, Any]) -> tuple[str | None, dict[str, Any]]:
    """header.world からジャンルを解決し (genre, world_meta) を返す。解決できなければ (None, fallback)。"""

def experiment_meta(repository: RunRepository, experiment: Path) -> dict[str, Any]:
    """一覧カードと実験ページ上部に要る値をまとめて返す（§6.1 の表の左列がキー）。ファイルが無い項目は None。"""

def is_minor(meta: Mapping[str, Any]) -> bool:
    """generations が None または MINOR_GENERATIONS 未満なら True。"""

def grouped_experiments(repository: RunRepository) -> tuple[list[tuple[str, list[dict]]], list[dict]]:
    """(ジャンル名, メタの list) のリスト（ジャンル名順、各グループ内は archive_mtime 降順）と、その他（minor）のリスト。"""

def cell_hook(repository, experiment, cell_key, elite, world_meta, synopsis_entry) -> str:
    """格子セルの 1 行フック。あらすじ(status ok)があれば先頭 50 字＋'…'、無ければ extract_scenes(limit=12) の
    priority 降順で ending 以外の上位 2 場面の events[0] を ' → ' で連結。場面が無ければ ''。"""

def cell_view(repository, experiment, cell_key, *, view: str) -> dict[str, Any]:
    """エリートページ用の全データ。view は 'digest' | 'decisions' | 'all'。§6.3 の表を満たす。"""

def detail_line(row: Mapping[str, Any], world_meta: Mapping[str, Any]) -> str | None:
    """rethink / confront / learn_fact の補足行。他の動詞は None。"""

def layer_points(rows) -> list[dict[str, Any]]:
    """snapshot ごとに {day, turn, values[7]} を返す。values の合成規則は既存 _layer_values と同一
    （[0,1] 平均→能力, [7]→認識, [2..5] 平均→資源, [6]→フェーズ, [8]→身分, [9]→対象, pending 件数/最大→遅延）。"""

def vitality_markers(rows) -> list[dict[str, Any]]:
    """verb が downed / revived / ending の行から {day, turn, kind} を返す（x 軸マーカー用）。"""
```

`detail_line` の仕様（実データで確認済みのキー）:

- `verb == "rethink"`（decision、`details.before` / `details.after` は `{fact: {"value", "confidence"}}`、`details.evidence` は fact id の list）: 「考え直した — 犯人: 乙(0.72) → 保留。証拠: evidence_05_second_alibi, …」の形。`after` に無い fact は「保留」、値が変われば `旧(conf) → 新(conf)`。fact 名は `world_meta["display_names"]` に無ければそのまま。
- `verb == "confront"`（`details.correct: bool`, `details.confidence: float`, `details.fact`, `details.value`）: 「指摘は正解（確信 0.75）」／「指摘は不正解（確信 0.72）」。
- `verb == "learn_fact"`（event、`details.beliefs: list[{fact, outcome, before, after}]`, `details.fact`, `details.source`）: 各 belief について「犯人: 丙(0.35) → 乙(0.72)（adopted）」を「／」で連結。`before` が None なら「なし」。
- `learn_fact` / `encounters` / `rethink` は `scenes.VERB_LABELS` に無く `describe_row` が「rethinkという行動を取った」等の一般文を返す。タイムラインでは `describe_row` の文の直後に `detail_line` を併記する（`scenes.py` は変更しない）。

## 5. `viewer/pages.py` の API と HTML 要件

```python
def document(title: str, body: str, *, crumbs: list[tuple[str, str]] = ()) -> str
def index_page(repository) -> str
def experiment_page(repository, experiment_name: str) -> str
def cell_page(repository, experiment_name: str, cell_key: str, *, view: str) -> str
def sparkline(values: Sequence[float], *, width=120, height=28) -> str      # inline SVG
def layers_svg(points, markers) -> str                                      # 7 層、x = day
```

- `document` は `<link rel="stylesheet" href="/static/app.css">` と `<script src="/static/app.js" defer>` を出す。`<title>` は `{title} | WorldBloom`。ヘッダにパンくず（`実験一覧 › exp10-detective › II|high`）。
- **既存テストが検査する文字列を維持する**（§7.1）: 一覧に `WorldBloom 実験一覧`、格子に `III|high`・q の `0.7500`（`f"{q:.4f}"`）・到達率の `100.0%`（`f"{r:.1%}"`）、エリートに `7層の推移`・`<svg`・`模範ランのターン列`（生ログ `<details>` の `<summary>` 文言に使う）・あらすじ本文・本文テキスト。
- 生ログ表は `<details class="raw">`（`open` 属性なし）の中に既存 `_turn_table` 相当を置く。**既定で閉じていること**（第 1 段の合格条件の要）。
- 展開モードのリンク: `?view=digest`（既定）/`?view=decisions`/`?view=all`。現在のモードは `aria-current="page"`。
- タイムラインの構造（JS とテストが依存する）:
  ```html
  <section class="timeline" data-view="digest">
    <section class="day" data-day="4">
      <h3>第4日 <span class="chip">客室</span><span class="chip">alive</span><span class="chip">強さ 72</span>
          <span class="chip">犯人: 乙 (0.72)</span>…</h3>
      <article class="scene" data-turn="13" data-day="4">
        <span class="turn">T13 朝</span>
        <p>探偵がrethinkという行動を取った</p>
        <p class="detail">考え直した — 犯人: 乙(0.72) → 保留。証拠: …</p>
        <span class="tag">重要な派生イベント</span><span class="delta">Δ 0.31</span>
      </article>
    </section>
  </section>
  ```
  `view=all` のとき、各 turn の NPC 行は `<details class="npc"><summary>NPC の行動 (n)</summary>…</details>` に畳む。
- 7 層 SVG: 既存 `_layers_svg` の描画を流用し、x のラベルを `day` に変更、各 day の位置に `<g class="day-mark" data-day="N">`（透明な当たり判定矩形＋目盛り）を置く。凡例は `<g class="legend-item" data-series="0..6">`、各系列は `<polyline data-series="i">`。マーカーは `downed` を ▼、`revived` を ▲、`ending` を ● で x 軸上に描く。
- 遺伝子バー: `category_weight` I〜VI を横棒（幅 = 値 × 100%）、`risk_tolerance` / `stance_shift_bias` / `novelty_drive` を数値で。argmax のカテゴリに `class="lead"`。
- 一覧・実験ページの「生成コマンド」ブロック: `<pre class="cmd">` に次を表示（`runs`、実験名、ジャンルはサーバが埋める）。`settings.json`（`ROOT / "settings.json"`）が無ければ直前に注意文「settings.json が無いため backend は prompt_only になります。例: `{"output": {"codex-cli": {"model": "gpt-5.6-sol"}}}`」を出す。
  ```
  python scripts\synopsize.py --archive <runs>\<exp>\archive.json --runs <runs>\<exp> --out <runs>\<exp>\synopses.json --backend codex-cli --project projects\<genre> --template templates\<genre>
  python scripts\narrate.py --archive <runs>\<exp>\archive.json --selection <runs>\<exp>\selection.json --out <runs>\<exp>\stories --backend codex-cli --project projects\<genre> --template templates\<genre>
  ```
  （`synopsize.py` の `--runs` は `layers_path`（`g9/ind-2/seed-4/layers.jsonl`、実験ディレクトリ相対）の基点なので**実験ディレクトリ**を渡す。ジャンル未解決なら `<genre>` の代わりに `momotaro` を出さず「ジャンル不明」と表示する。）
- 選定: 格子セルの ★/☆ と、エリートページのチェックボックス。どちらも `data-endpoint="/exp/<name>/selection"` と `data-cell` を持つ要素を `app.js` が拾って POST する（既存 inline script の移設。POST の URL・JSON 形式・応答は不変）。
- 選定トレイ: 実験ページ末尾の固定バー（`<aside class="tray">`）に選定済みセルのリンクを列挙。0 件なら「選定なし」。

## 6. 画面ごとの情報と取得元（実データで存在確認済み）

### 6.1 実験一覧 `/`（`experiment_meta` のキー）

| 出す情報 | キー | 取得元 |
|---|---|---|
| 実験名 | `name` | ディレクトリ名 |
| 世界名／ジャンル | `world`, `genre` | exemplar の `layers.jsonl` 先頭行（`kind == "header"`）の `world`（`archive.cells` の名前順で最初のセルの `exemplar.layers_path` を読む。無ければ `synopses.json` の `world`）→ `resolve_genre` |
| 主人公・敵役 | `protagonist`, `antagonist` | 同 header |
| 真相の集合（探偵） | `truths` | 各 exemplar header の `truth`（dict。`{"culprit": "容疑者甲", "weapon": "…"}`）の `culprit` を集合に。無い世界は空 |
| engine_hash | `engine_hash` | header の `engine_hash`（`archive.cells[*].exemplar.engine_hash` と同値） |
| 日付 | `archive_mtime` | `archive.json` の `os.stat().st_mtime`（ローカル時刻 `YYYY-MM-DD HH:MM` で表示） |
| 世代数 | `generations` | `g<数字>` ディレクトリの個数（`summary.generations` の長さと一致するが、ディレクトリ数を正とする） |
| 個体数 | `population` | `g0/population.json`（list）の長さ。無ければ None |
| シード | `seeds` | `summary.json` の `seeds`（list）。無ければ None（exp1 は無い） |
| 結末 | `target_ending` | `summary.json` の `target_ending`（str または list）。無ければ world_meta の `target_ending_label` |
| keep | `keep` | `summary.json` の `keep`（`"reached"` 等）。無ければ None |
| 到達率推移 | `reach_series` | `summary.json` の `generations[].reach_rate` |
| 占有推移 | `occupied_series` | `summary.json` の `generations[].occupied_cells` |
| 平均 q 推移 | `quality_series` | `summary.json` の `generations[].average_archive_quality` |
| 相異度 | `dissimilarity` | `summary.json` の `final_archive_dissimilarity` |
| 占有 n/N | `cells`, `grid_size` | `len(archive.cells)`、`len(categories) × len(bins)`（`qd_axes`） |
| volatility 閾値 | `thresholds` | `archive.json` の `volatility_thresholds.low_max` / `mid_max` |
| あらすじ件数 | `synopsis_ok` | `synopses.json` の `entries[]` で `status == "ok"` の数（ファイル無しは 0） |
| 選定件数 | `selected` | `selection.json` の `selected`（list）の長さ（無しは 0） |
| 本文件数 | `story_ok` | `stories/index.json` の `entries[]` で `status == "ok"` の数（無しは 0） |

グループ化: `grouped_experiments` は `is_minor` が偽のものをジャンル（`world` 名。未解決は「不明」）でまとめ、真のものを「その他の短いラン」として `<details>` に畳む。実データでは exp1〜exp10 の 12 件（8〜20 世代）が本体、`accept1` / `review-acc` / `review-d2` / `review-d2c`（3 世代）が折り畳み。

### 6.2 実験ページ `/exp/<name>`

- 上部ストリップ: 6.1 の `reach_series` / `occupied_series` / `quality_series` を `sparkline` で描き、始点→終点の値（到達率は `%`）、`dissimilarity`、`thresholds`、`target_ending`、`keep` を併記。
- 格子: 行 = `qd_axes(template_dir)[0]` ＋ archive に現れた想定外カテゴリ（名前順）、列 = `qd_axes(...)[1]` ＋ 想定外 bin。セル: `quality`（`.4f`）、`reach_rate`（`.1%`）、`generation`、`genome.category_weight` の argmax、`cell_hook`、選定 ★/☆（`selection.json`）。空セルは `<td class="empty">空</td>`。
- 選定トレイ: `selection.json` の `selected` を格子順に並べたリンク。
- 生成コマンド（§5）。

### 6.3 エリートページ `/exp/<name>/cell/<cell>?view=…`（`cell_view` の戻り値）

| 出す情報 | キー | 取得元 |
|---|---|---|
| q / 到達率 / 世代 / seed / 親 | `quality`, `reach_rate`, `generation`, `seed`, `parents` | `archive.cells[cell]` の `quality` / `reach_rate` / `generation` / `exemplar.seed` / `parents`（list[str]、例 `["g7/archive/III-low", "g5/archive/II-high"]`） |
| 遺伝子 | `genome` | `archive.cells[cell].genome`（`category_weight{I..VI}`, `risk_tolerance`, `stance_shift_bias`, `novelty_drive`。`rule_bits` は表示しない） |
| 前後セル | `prev_cell`, `next_cell` | 格子順（行 = categories、列 = bins）で占有セルだけを辿る |
| 選定状態 | `selected` | `selection.json` |
| 場面（digest） | `scenes` | `extract_scenes(rows, world_meta, limit=12)` |
| 場面（decisions） | `scenes` | `extract_scenes(rows, world_meta, limit=len(rows))` |
| NPC 行（all） | `npc_by_turn` | `rows` のうち `kind in {decision, event}` で `subject != protagonist` を turn ごとに `describe_row` した文の list。`view == "all"` のときのみ計算 |
| 補足行 | 各 scene の `details` | 場面に含まれる turn の行のうち verb が `rethink` / `confront` / `learn_fact` のものを `detail_line` で文にした list（主人公の行のみ。`learn_fact` は主人公が subject のもの） |
| 日ごとの状態チップ | `day_states` `{day: {...}}` | 各 `kind == "snapshot"` 行（1 日 1 回、`subject` は主人公）: `zone` = `layers.zone`、`vitality` = `layers.vitality`、`strength` = `layers.ability.base` ＋ `layers.ability.modifiers[]` のうち `active` が真の `value` の合計、`beliefs` = `layers.valued_beliefs`（`{fact: {value, confidence}}`。無い世界は空）、`holders` = `layers.objective`（`{item: holder}`）、`phase` = `layers.phase`（list）、`pending` = `len(layers.pending)`、`stance_out` = `relations[]` で `observer == protagonist and target == antagonist` の `affinity`、`stance_in` = その逆向き。同じ day に snapshot が複数あれば**その day の最後の snapshot**を採る（本番ログは 1 日 1 回だがテストのフィクスチャは day 1 に 2 つある）。snapshot が無い day は直前の day の値を引き継ぐ |
| 7 層系列 | `layer_points` | `layer_points(rows)`（§4） |
| マーカー | `markers` | `vitality_markers(rows)` |
| 生ログ | `turn_rows` | `rows` のうち `kind not in {header, snapshot}` かつ `turn` を持つ行（既存 `_turn_table` と同じ選別） |
| あらすじ | `synopsis` | `synopses.json` の `entries[]` で `cell == cell_key` の要素（`status`, `synopsis`, `error`）。`archive` の `backend` も表示 |
| 本文 | `story` | `stories/index.json` の `entries[]` で `cell == cell_key` の `story_path` を `stories/` 基点で読む（既存 `_story_entry` と同じ） |

チップの表示規則: `beliefs` は `fact: value (confidence)`（fact 名は `display_names` を通す）、`holders` は `item: holder`、`stance_out` / `stance_in` は `主人公→敵役 +0.12 / 敵役→主人公 −0.27`。値が無い項目は出さない（ジャンルごとに自然に変わる。探偵は beliefs、恋愛は stance、桃太郎は holders と phase が主に出る）。

## 7. テスト

### 7.1 既存 `tests/test_viewer.py`（変更しない）が要求すること

サーバを `python viewer/server.py --runs <tmp> --port <p>` で起動し、フィクスチャ実験 `exp-viewer`（world `桃太郎`、セル `III|high` のみ、`summary.json` **無し**、`g0/population.json` **無し**、`synopses.json` あり、`stories/index.json` と `stories/III-high.md` あり）に対して:

- `/` に `WorldBloom 実験一覧` と `exp-viewer` が含まれる（`summary.json` が無いので `is_minor` は真 → 「その他の短いラン」の `<details>` 内に出る。**名前は必ず HTML に含めること**）。
- `/exp/exp-viewer` に `III|high`、`0.7500`、`100.0%` が含まれる（world `桃太郎` → `momotaro` に解決され格子は 6 行）。
- `/exp/exp-viewer/cell/III%7Chigh` に `7層の推移`、`<svg`、`模範ランのターン列`、あらすじ本文 `桃太郎は鬼を退け、宝物を村へ持ち帰った。`、本文 `桃太郎の凱旋` が含まれる。
- `POST /exp/exp-viewer/selection` に `{"cell": "III|high", "selected": true}` で 200、応答 JSON の `selected` が真、`selection.json` に `III|high` が書かれる。
- `GET /exp/../etc` 相当のトラバーサルは 403。
- 停止後にポートが解放される。

フィクスチャの header は `genome: null` で、decision 行に `choice_prob` / `policy` / `delta.actor` が無い場合がある。**欠損キーで落ちないこと**（`.get` で読む）。

### 7.2 新規 `tests/test_viewer_pages.py`（サーバ不要）

`from test_viewer import _create_experiment` でフィクスチャを再利用し（`unittest discover -s tests` は `tests/` を `sys.path` に入れる）、`tempfile.TemporaryDirectory` に実験を作って次を検査する:

1. `data.resolve_genre("桃太郎")` が `("momotaro", ROOT/"projects"/"momotaro", ROOT/"templates"/"momotaro")` を返し、`data.resolve_genre("存在しない世界")` が `None`。
2. `data.qd_axes(ROOT/"templates"/"detective")` が `(["I","II","III"], ["low","mid","high"])`。
3. `data.experiment_meta` がフィクスチャで `world == "桃太郎"`、`genre == "momotaro"`、`generations == 1`、`population is None`、`synopsis_ok == 1`、`story_ok == 1`、`selected == 0` を返し、`is_minor` が真。
4. `data.cell_view(..., view="digest")` の `scenes` が 1 件以上で、`events[0]` に `桃太郎` を含み、`day_states[1]["zone"] == "村"`（フィクスチャの最後の snapshot）、`markers` に `kind == "ending"` が 1 件。
5. `pages.cell_page(..., view="digest")` の HTML で、`<details class="raw"` が `open` を持たず、`class="timeline"` があり、`<script src="/static/app.js"` があり、`<script>` で始まる inline スクリプトが無い。
6. `pages.index_page` の HTML に `その他の短いラン` と `exp-viewer` が含まれる。
7. `data.detail_line` の単体: `rethink` 行（`details.before == {"culprit": {"value": "乙", "confidence": 0.72}}`, `after == {}`）→ 文字列に `乙` と `保留` を含む。`confront` 行（`details.correct == True`, `confidence == 0.75`）→ `正解` を含む。`learn_fact`（`beliefs: [{fact: "culprit", outcome: "adopted", before: None, after: {value: "乙", confidence: 0.72}}]`）→ `adopted` を含む。他の verb → None。
8. 静的配信の安全性: `server.ViewerHandler` を起動せずに検査できるよう、`server.py` に `static_path(name: str) -> Path` を置き、`"app.css"` は `viewer/static/app.css` を返し、`"../server.py"` と `"nope.css"` は `ForbiddenPath` / `MissingResource` を投げる。

実行: `python -m unittest discover -s tests -v`（既存 95 件＋新規が全部通ること）。

## 8. 合格条件と測り方

| 条件 | 測り方 |
|---|---|
| exp10-detective / II\|high の既定表示（`?view=digest`）でページ高 < 5,000px | ブラウザで開き、DevTools コンソールで `document.documentElement.scrollHeight`。参考: 現状 18,781px。あわせて `document.querySelector('details.raw').open === false` |
| 幅 1,000px 以上で横スクロールが無い | 同ページで `document.documentElement.scrollWidth <= window.innerWidth`（ウィンドウ幅 1,000 と 1,180 で確認）。生ログ `<details>` を開いたときだけ表内スクロール（`.grid-wrap { overflow-x: auto }`）が出るのは可 |
| 探偵・恋愛の格子が 3 行 | `/exp/exp10-detective` と `/exp/exp7-romance` で `document.querySelectorAll('table.archive-grid tbody tr').length === 3`、`/exp/exp1` で 6 |
| 一覧 16 件すべてにジャンル・日付・到達率 | `/` で各カード（`.card[data-experiment]`）に `.genre`・`.date`・`.reach` 要素があり空でない。本体 12 件がジャンル別、4 件が「その他の短いラン」 |
| 場面が読める | `/exp/exp10-detective/cell/II%7Chigh` の既定表示に `article.scene` が 12 件、`rethink` の場面に `p.detail` があり `→` を含む |
| 依存・外部参照なし | `pip freeze` に変化なし。`viewer/` 内を `grep -n "http://\|https://"` して自サーバ以外の URL が無い。レスポンスヘッダの CSP に `script-src 'self'` のみ |
| テスト | `python -m unittest discover -s tests -v` が全件通る |
| 起動不変 | `.claude/launch.json` の `worldbloom-viewer` でそのまま起動する |

## 9. 納品フォーマット（Codex への指示に転記する）

- 新規ファイル（`viewer/__init__.py`, `viewer/data.py`, `viewer/pages.py`, `viewer/static/app.css`, `viewer/static/app.js`, `tests/test_viewer_pages.py`）は `new-file` で全文。
- `viewer/server.py` は**全文置換**（`new-file` として納品し、先頭に「既存ファイルを上書き」と明記。既存の `RunRepository` 等は `data.py` へ移すので `server.py` からは消える）。
- それ以外の既存ファイルには触れない。`replace-function` が必要になる箇所は無い想定。もし既存ファイルの変更が必要だと判断したら `QUESTION:` で止まる。
- アンカーは一意な行のみ。diff / パッチ形式（`*** Begin Patch` 等）と `// ...既存のまま...` の省略は禁止。
- 最終便に実装報告: どのように分割したか、`extract_scenes` の `limit` の扱い、`detail_line` の表記の判断、フィクスチャ欠損キーへの対処、テストの実行結果（実行できない場合はその旨）。
- 納品後の適用は Claude が行い（`node` 不要、`python -m py_compile viewer/*.py tests/test_viewer_pages.py` で構文確認）、`python -m unittest discover -s tests -v` と実機（`http://localhost:5401`）で §8 を測る。
