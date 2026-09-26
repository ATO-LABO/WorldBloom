# 世界インポート用ZIPの作り方（AI向け手引き）

このリポジトリ（WorldBloom）に新しい世界を、ZIPファイルとしてインポートするための仕様書です。
Codex・Claude Code・Gemini などのAIツールにこの文書をそのまま渡し、「この文書に従って
ID `<id>` の世界をZIPで作って」と依頼すれば、互換性のあるZIPを作れます。

外部チャット（このリポジトリにアクセスできないGemini等）にこの文書だけを渡す場合は、
**この文書に加えて、使いたいジャンルの `templates/<genre>/action_graph.yaml` と、同じ
ジャンルの既存 `world.yaml` を1本、一緒に貼ってください**。ジャンルの語彙（動詞・状況）は
この文書には複製していません（複製するとジャンル側の変更でこの文書が古くなるため）。
ジャンルをまだ決めない場合は、この2ファイルは不要です（後述）。

## 0. この文書の使い方

依頼文の例（ジャンルを指定する場合）:

> `docs/world-import-guide.md` の仕様に従い、ジャンル `momotaro`・ID `uchuu_kaizoku` の
> 世界を作ってください。参考として `templates/momotaro/action_graph.yaml` と
> `projects/momotaro/world.yaml` を添付します。`world.yaml` と `subjects/*.yaml` を
> このリポジトリと同じ構成でZIPにまとめてください。

依頼文の例（ジャンルを後で決める場合）:

> `docs/world-import-guide.md` の仕様に従い、ID `uchuu_kaizoku` の世界を作ってください。
> ジャンルはまだ決めないので `world.yaml` に `gapengine` は書かないでください。

## 1. 世界とジャンルの分担

- **書くもの**（このZIPに入れるもの）: `world.yaml`（この世界固有の設定・結末）、
  `subjects/*.yaml`（登場人物ごとのパラメータ）
- **書かないもの**（ジャンル側が持っており、書いても効かないもの）: `action_graph.yaml`・
  `canon.yaml`・`effects.yaml`・`qd.yaml`・`rules.yaml`。これらはZIPに含めても無視されます。
- **`name`**: WorldBloom側の「表示名」フォームが**空欄なら、ZIPの `world.yaml` の `name`
  を使います**。フォームに入力があれば、その値で上書きされます。
- **`gapengine`**（ジャンルの指定）: 次のどちらかを選べます。
  - **書く**: `action_graph`・`effects` のちょうど2キーで、両方とも同じジャンル
    （`templates/<genre>/action_graph.yaml` と `templates/<genre>/effects.yaml`）を
    指定してください。指定したジャンルがこのリポジトリに実在しないとインポートに
    失敗します（先にジャンルを作るか、次の「書かない」を選んでください）。
  - **書かない**（`gapengine` キーごと省略する）: **未設定の世界**として取り込まれます。
    取り込み後、世界設定画面の「ジャンル」から選べます。**実行前に必ず選んでください。**
    選ばなくても、実行設定でジャンルを指定すればGA実験自体は動きますが、この世界の
    `gapengine.effects`（伏線）だけは黙って空のまま実行されます
    （`engine/phase2.py` は `world.yaml` 自身の `gapengine.effects` しか読まず、
    実行設定側で選んだジャンルの `effects.yaml` にはフォールバックしません）。

## 2. ZIPレイアウト仕様

- `world.yaml` をZIPの直下（トップレベル）に置く。**必須**（無いとインポートに失敗します）。
- 人物ファイルは `world.yaml` と同じ階層の `subjects/` フォルダに置く（`subjects/01_foo.yaml` など）。
- ZIP全体を1つの共通フォルダで包んでも構いません（例: `uchuu_kaizoku/world.yaml`,
  `uchuu_kaizoku/subjects/01_foo.yaml`）。WorldBloom側がその共通フォルダを自動で
  読み飛ばします。共通フォルダの候補が複数（`world.yaml` が2箇所以上にある）場合は
  失敗します。
- 上記以外のファイル（README等）は無視されます。ただし `subjects/` 直下に
  規則外の名前（下記）のファイルを置くとエラーになります。
- ファイル名の規則: `world.yaml` は完全一致。人物ファイルは
  `subjects/[英数字・アンダースコア・ハイフンのみ、1〜64文字].yaml`
  （例: `subjects/01_kaizoku.yaml`。空白・日本語ファイル名は不可）。
  一覧の表示順はファイル名順なので、`01_`, `02_`, ... の連番接頭辞を推奨します。
- 同じファイルパスの重複は禁止です。大文字小文字だけが違うパス
  （`subjects/01_a.yaml` と `subjects/01_A.yaml` など）も重複として扱われます。
- 文字コードはUTF-8（BOMありでも自動で剥がされます）。
- 上限: 1ファイル256KB以内、ZIP本体は**47KB以内**、ファイル数は200件以内、
  展開後の合計サイズは2MB以内です（超えるとアップロードが拒否されます。
  テキストのYAMLであれば通常はこの範囲に十分収まります）。
- `.DS_Store` や `__MACOSX/` 配下は自動的に無視されます。
- パス・ファイル名は大文字小文字を区別します。`world.yaml`・`subjects/` は完全に小文字で書いてください（`World.yaml`や`Subjects/`は認識されません）。

## 3. `world.yaml` リファレンス

### 必須キー

| キー | 型 | 説明 |
|---|---|---|
| `name` | str | 表示名（フォームの表示名欄が空欄のときだけ使われます。1章参照） |
| `protagonist` | str | 主人公のID。`subjects/*.yaml` のいずれかの `id` と一致すること |
| `antagonist` | str | 敵役のID。同上 |
| `time.slots` | list[str] | 1日の時間帯（例: `[朝, 昼, 夕方, 夜]`）。空にしないこと |
| `ending` | list | 結末の定義。各要素は下記「ending の書式」参照 |
| `target_ending` | str または list[str] | 成立を目指す結末のID（`ending[].id` のいずれか）。重複不可 |

`gapengine` は任意キーです（1章参照）。書く場合は `action_graph`・`effects` の
ちょうど2キーで、両方とも `templates/<同じgenre>/action_graph.yaml` /
`templates/<同じgenre>/effects.yaml` の形式である必要があります。

**ending の書式**: 各要素は `id`（必須・一意）、`when`（必須。述語文字列、または
`{agent: <人物id>, goal: attained}` のちょうど2キーの糖衣構文）、`label`（任意）、
`deliver`（任意。指定する場合は `{subject, item, zone}` の3キーちょうど）。

### 任意キー（既定値）

| キー | 既定 | 説明 |
|---|---|---|
| `time.days` | 1 | 日数 |
| `zones` | なし | `[{name（必須・一意）, note}]`。実質必須（`routes`・`items`・`facts` から参照） |
| `routes` | `{}` | `{起点zone名: [{to（必須、zone名）, cost（既定1）, requires_item（items内に存在すること）}]}` |
| `movement` | `{action_weight:1, hop_decay:1, destination_weights:{}}` | 移動の重み |
| `stamina` | `{default_max:10, default_recover_per_slot:1, exhausted_ratio:0.2}` | 体力の既定値 |
| `companionship` | `{threshold:0.6, weight:1}` | 同行判定 |
| `permission.restricted_weight` | 0.15 | 制限区域の重み |
| `phase1` | `{open_bonus:0.5, negotiate_threshold:0.3, sacrifice_rewards:{asset_base:8, bond_stress:-3, bond_phase:決意}}` | 序盤の調整値 |
| `disguises` | `[]` | `[{subject, as, when（必須）, id}]` |
| `trials` | `[]` | `[{giver（必須）, requires:{stance, item}, grants:{fact\|item}（非空）}]` |
| `phase_rules` | `[]` | `[{id（必須・一意）, when（必須）, enable:[], disable:[]}]` |
| `thresholds` | `[]` | `[{id, when}]`。`phase`（下記の述語名前空間）で使えるフェーズ名の定義元 |
| `awareness_per_encounter` | 0 | 遭遇ごとの認知度上昇 |
| `pulls` | `{}` | 行動選好の重み |
| `items` | `[]` | 下記「items の書式」参照 |
| `facts` | `[]` | 下記「facts の書式」参照 |
| `truth` | `{}` | `{fact_id: 値}` または `{fact_id: {candidates:{値: 重み(≥0、和>0)}, known_by}}` |
| `default_strength_prior` | 50 | 強さの既定事前値 |
| `contest` | `{tau:10（>0）, epsilon:5}` | 対決判定の調整値 |
| `vitality` | `{revive_after:4, ally_speedup:1, revive_base_penalty:2, lethal_exempt:[]}` | 生死・復帰 |
| `grief` | `{stress:1, affinity_to_killer:-0.6}` | 死別の影響 |
| `scheduled_events` | `[]` | `[{id, day, slot, targets, label, grants_item:{name,count}, move_to, stress_delta}]` |
| `daily_events` | `{chance:0, events:[]}` | `events: [{id（必須）, label, weight, stress_delta}]` |

**items の書式**: 各要素は `name`（一意）、`sources:[{type, zone\|agent, count, max}]`、
`made_from:{}`（材料。循環不可）、`requires:{knowledge: <fact id>}`、`craft_zone`、
`lootable`、`objective`、`keepsake`、`vehicle`、`access:{mode, allow}`、
`give:{receiver_affinity, giver_affinity}`、
`modifier:{id, value, kind, visible, lethal, lethal_chance}`。

**facts の書式**: 各要素は `id`（一意）、`label`、`secrecy`、`share_min_affinity`、
`act_threshold`、`secret_of`、`values:[一意]`（`sources` とは排他）、`sources:[]`、
`implies`/`refutes:{fact（既存のfact idであること）, value, confidence}`。

**参照整合性**（`World` 初期化時に検査されます。壊れているとインポート後の「検証」で失敗します）:
`routes` の `requires_item`・`items` の `made_from` の材料名・`requires.knowledge`・
`craft_zone`・`sources.zone`・`facts` の `implies`/`refutes` の参照先は、すべて
このファイル内で定義済みであること。`zone`/`item`/`fact` の名前はそれぞれ一意であること。

**`when:` 述語の名前空間**（`ending`・`disguises`・`trials`・`phase_rules`・`thresholds` で使えます）:
`self`, `phase`（集合。例 `"越境" in phase"`）, `turn`, `day`, `stance(a, b)`, `bonds`,
`awareness`, `holds(actor, item)`, `holder(item)`, `zone(actor)`, `present(actor)`,
`vitality(actor)`, `known(actor, fact)`, `knows_modifier(actor, target, source)`,
`strength(actor)`, `believed_strength(actor, target)`, `hostile_present`,
`confront_success(actor, fact)`。人物・場所・品名は裸の識別子として書けます
（例: `zone(桃太郎) == '鬼ヶ島'`）。

## 4. `subjects/*.yaml` リファレンス

### 必須キー

| キー | 型 | 説明 |
|---|---|---|
| `id` | str | 全ファイルで一意。`world.yaml` の `protagonist`/`antagonist` や `relations` の相手キーはこの値を指す |
| `traits` | dict | 次の5キーが必須（超過分のキーがあっても無視されます）: `social, stubbornness, curiosity, diligence, temper` |
| `range.entry` | str | 開始する zone 名（`world.yaml` の `zones` に存在すること） |

ファイル名は `[A-Za-z0-9_-]{1,64}.yaml`。表示順はファイル名順なので `01_`〜の接頭辞を推奨。

### 任意キー（既定値）

| キー | 既定 | 説明 |
|---|---|---|
| `base` | 0 | 基礎の強さ（既存世界は50前後が目安） |
| `modifiers` | `[]` | `[{id, source, value, kind（必須）, visible:true, active:true, lethal:false, lethal_chance:0, affinity_cap, affinity_cap_targets}]` |
| `beliefs_about` | `{}` | `{相手id: {known_modifiers:[], base_estimate:50, identity_seen:false, observe_progress:0, misled_by}}` |
| `beliefs` | `{}` | `{fact: {value（必須）, confidence（0〜1）}}` |
| `knowledge` | `[]` | 知っている fact のID一覧 |
| `inventory` | `{}` | `{item名: 個数(≥0)}` |
| `reputation` | 0 | 評判 |
| `phase` | `[]` | 開始時点のフェーズ |
| `verbs` | `[]` | 使える動詞（下記参照） |
| `identity` | `{true: id, displayed: id}` | 変装等の見た目 |
| `goal` | `{}` | `{target（品名）, deliver_to（zone名）, obstacles:[fact id], outcome}` |
| `stamina` | `{max:0, current, recover_per_slot:0}` | 既存世界は全員明示している。明示を推奨 |
| `range.zones` | `[]` | 移動可能な zone の制限 |
| `range.exclude` | `[]` | `[{zones, until_item}]` |
| `companions` | `[]` | 同行者 |
| `ally_value` | 0 | 味方評価 |
| `objective_claimant` | true | 目的品の主張者になれるか |
| `relations` | `{}` | `{相手id: {affinity:0, awareness:0}}` |
| `stress` | 0 | ストレス（0〜10に丸められます） |
| `vitality` | `alive` | 生死状態 |

**`verbs` について**: エンジンは `action_graph.yaml` と自動で突き合わせません。書いた
動詞のうち、実際に効くのは `move, investigate, observe, neutralize, sabotage,
sacrifice, mislead, confront, rethink, share_knowledge, give_item, persuade, pledge,
negotiate, concede, craft, fight, train, rescue, withdraw, guard, plant, payoff,
disguise, grand_gesture, trial, donate, rest` の28語のみです。それ以外は黙って無視
されます。ジャンルを指定する場合は、同じジャンルの `templates/<genre>/action_graph.yaml`
の `nodes[].verb` を見て、そこにある語彙から選んでください（ジャンルを後で決める場合は、
決めた時点で見直してください）。

## 5. ジャンルから拾う情報（ジャンルを指定する場合）

world.yaml/subjects を書く前に、同じジャンルの以下のファイルを確認してください
（このZIPには含めません。読むだけです）:

- `templates/<genre>/action_graph.yaml`: 使える `verbs` と、行動の `permission` 条件。
- `templates/<genre>/canon.yaml`: このジャンルの「定石」が前提にしている `phase` 名
  （`world.yaml` の `thresholds` で同じ名前を定義する必要があります）と、目的品
  （`items` のどれかに `objective: true` を1つ設定する必要があります）。
- `templates/<genre>/qd.yaml`: QD軸の定義（world.yaml側で対応する必要はありません）。
- ジャンル別の固有前提: 既存の `projects/<同ジャンルの世界>/world.yaml` を1本、
  構成の参考にしてください（`momotaro` なら `projects/momotaro/world.yaml`）。

ジャンルを後で決める場合は、この章は取り込み後（ジャンルを選ぶとき）に読んでください。

## 6. 最小完全例

いずれも2人・2ゾーン・1結末の最小構成です。

### 6.1 ジャンルを指定する場合

`gapengine` のパスは実在するジャンル名に置き換えてください。

`world.yaml`:

```yaml
name: 仮の世界
time:
  days: 3
  slots: [朝, 夜]
protagonist: 主人公
antagonist: 敵役
zones:
  - name: 広場
  - name: 塔
routes:
  広場:
    - {to: 塔}
  塔:
    - {to: 広場}
gapengine:
  action_graph: templates/momotaro/action_graph.yaml
  effects: templates/momotaro/effects.yaml
items:
  - name: 鍵
    objective: true
    lootable: true
facts: []
ending:
  - id: reach_tower
    when: "holds(主人公, 鍵) and zone(主人公) == '塔'"
    label: 鍵を手に塔へたどり着いた
target_ending: reach_tower
```

### 6.2 ジャンルを後で決める場合

`gapengine` キーを丸ごと省略します。それ以外は6.1と同じです。

`world.yaml`:

```yaml
name: 仮の世界（ジャンル未定）
time:
  days: 3
  slots: [朝, 夜]
protagonist: 主人公
antagonist: 敵役
zones:
  - name: 広場
  - name: 塔
routes:
  広場:
    - {to: 塔}
  塔:
    - {to: 広場}
items:
  - name: 鍵
    objective: true
    lootable: true
facts: []
ending:
  - id: reach_tower
    when: "holds(主人公, 鍵) and zone(主人公) == '塔'"
    label: 鍵を手に塔へたどり着いた
target_ending: reach_tower
```

どちらの場合も `subjects/01_shujinkou.yaml`・`subjects/02_tekiyaku.yaml` は共通です:

```yaml
id: 主人公
traits: {social: 0.5, stubbornness: 0.5, curiosity: 0.5, diligence: 0.5, temper: 0.5}
range: {entry: 広場}
stamina: {max: 10, current: 10, recover_per_slot: 1}
```

```yaml
id: 敵役
traits: {social: 0.5, stubbornness: 0.5, curiosity: 0.5, diligence: 0.5, temper: 0.5}
range: {entry: 塔}
stamina: {max: 10, current: 10, recover_per_slot: 1}
```

## 7. 提出前チェックリスト

- [ ] `protagonist`/`antagonist` が `subjects/*.yaml` の `id` のどれかと一致している
- [ ] `zones`/`items`/`facts` への参照（`routes`・`requires_item`・`made_from`・
      `requires.knowledge`・`craft_zone`・`implies`/`refutes`）がすべて定義済み
- [ ] `target_ending` が `ending[].id` の集合に含まれる（重複なし）
- [ ] `gapengine` を書く場合、`action_graph`・`effects` が同じジャンルを指し、
      そのジャンルがこのリポジトリに実在する（`templates/<genre>/` フォルダがある）
- [ ] `gapengine` を書く場合、`subjects` の `verbs` が同ジャンルの `action_graph.yaml`
      の語彙に含まれる
- [ ] 人物ファイル名が `subjects/[A-Za-z0-9_-]{1,64}.yaml`（日本語・空白不可）
- [ ] 全ファイルUTF-8、1ファイル256KB以内、ZIP本体47KB以内、ファイル数200件以内、
      展開後の合計2MB以内
- [ ] `world.yaml` がZIP直下（または単一の共通フォルダの直下）にある

## 8. インポート後の検証とエラー対応表

インポート後、世界ページの「検証」ボタンを押すと以下のようなメッセージが出ます。
（`gapengine` を書かずに取り込んだ場合は、先に世界設定画面でジャンルを選んでください。）

| メッセージ | 原因 |
|---|---|
| `登場人物の入力がありません` | `subjects/` に人物ファイルが1つもない |
| `主人公または敵役の人物入力がありません` | `protagonist`/`antagonist` が `subjects` の `id` と一致しない |
| `人物IDが重複しています` | 複数の `subjects/*.yaml` が同じ `id` を持っている |
| `YAMLとして読めません` | YAML構文エラー |
| `世界・人物・テンプレートまたは結末の検証に失敗しました` | 上記以外の参照不整合（zone/item/fact名の誤りなど）。汎用メッセージなので、7章のチェックリストを見直してください |

アップロード時点のエラー:

| メッセージ | 原因 |
|---|---|
| `ZIPとして読めません` | ZIP形式として壊れている、またはBase64が不正 |
| `ZIPにworld.yamlがありません` | `world.yaml` が見つからない（階層・ファイル名を確認） |
| `world.yamlの場所が一意に決まりません` | `world.yaml` が複数の階層にある（包みフォルダを1つにしてください） |
| `<path>: 対象外のファイル名です` | `subjects/` 配下のファイル名が規則外 |
| `<path>: 256KB以内にしてください` | 1ファイルが大きすぎる |
| `<path>: ZIP内に重複しています` | 同じパス（大文字小文字違い含む）が複数ある |
| `<path>: UTF-8として読めません` | 文字コードがUTF-8でない |
| `ファイル数が多すぎます（200件以内）` | 人物ファイルなどの件数が多すぎる |
| `展開後の合計サイズが大きすぎます（2MB以内）` | ZIP全体の展開後サイズが大きすぎる |
| `ZIPは47KB以内にしてください` | アップロードしたZIP本体が大きすぎる |
| `YAMLとして読めません` | `world.yaml` または `subjects/*.yaml` のいずれかがYAML構文エラー |
| `空にできないファイルです` | `world.yaml` または `subjects/*.yaml` の中身が空 |
| `YAMLの形式が不正です` | `world.yaml`・`subjects/*.yaml` のいずれかが、項目と値の形式（マッピング）でも一覧（リスト）でもない値にパースされる |
| `world.yamlの形式が不正です` | `world.yaml` が一覧（リスト）や単一の値など、項目と値の形式（マッピング）以外にパースされる |
| `gapengineはaction_graphとeffectsの2項目で指定してください` | `gapengine` が dict でない、またはキーが `action_graph`・`effects` のちょうど2つでない |
| `gapengine.action_graphはtemplates/<ジャンル>/action_graph.yamlの形で指定してください（effectsも同じジャンル）` | `action_graph`/`effects` の値が文字列でない、パス形式が不正、または両者が別ジャンルを指している |
| `ZIPのジャンル「<id>」（templates/<id>/）がありません。先にジャンルを作るか、world.yamlのgapengineを削除して未設定で取り込んでください` | 指定したジャンルがこのリポジトリに存在しない |
| `名前を入力してください（ZIPのnameも空です）` | フォームの表示名欄も `world.yaml` の `name` も空 |

## 9. 変更履歴

スキーマの定義元は `engine/world.py`・`engine/subject.py` です。**エンジン側の変更と
同じコミットでこの文書も更新してください。**

- 2026-09-27: ジャンル指定を任意化（`gapengine` を省略すると未設定で取り込み、後から
  世界設定画面で選べる）。`name` はフォーム空欄ならZIPの値を使う方式に変更（以前は
  常にフォーム値で上書きされた）。ZIP本体の上限を47KBに、ファイル数200件・展開後
  合計2MBの上限を追加。
- 2026-09-15: 初版（WB-UI: 世界作成のZIPインポート機能。未リリース）
