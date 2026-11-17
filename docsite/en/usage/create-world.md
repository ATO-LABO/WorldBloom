---
ja_rev: "b3379ec4e99f"
---
# Create a World

A "world" is the full setup a GA experiment runs on: characters, places, time, and the target ending. Once you've built a world here, you run GA experiments against it from [Run Settings](run-settings.md).

## Create a new world

Start with “新しい世界を作成” (Create a new world) from the world list. Cards show saved overviews, character/place counts, duration, protagonist and antagonist. Search by world or character name.

There are four entry choices:

- **既存の世界から始める** (Start from an existing world): select a source and make a separate draft, preserving related files and fields not supported by the forms. The original world is unchanged.
- **ZIPから始める** (Start from ZIP): make a draft from a ZIP. See [Import a World](import-world.md).
- **保存した下書きを再開** (Resume a saved draft): restore its content and editing stage. Opening it does not run AI, validation or adoption.
- **新規作成** (Create new): start with a name and overview. Incomplete inputs can be saved.

The editing workflow has three stages:

1. **① 下書きを用意** (Prepare a draft): enter a name (up to 120 characters) and overview (up to 8000). Optional genre and “世界の脅威への対応” (Respond to a world threat) are creative cues. Selecting them does not generate anything or configure runtime conditions.
2. **② 内容を整える** (Refine the content): edit people, relationships, places, items, time and goals manually. AI consultation is optional. Request a targeted change, review its diff, then choose “この案を下書きに採用” (Adopt this proposal into the draft). New drafts also support requesting an alternative whole-world outline, preserving current manual edits in a separate saved draft.
3. **③ 確認して採用** (Review and adopt): run “設定と実行入力を確認” (Check settings and execution inputs), review missing fields, defaults and unsupported details, then check the confirmation box and save as a new world. Adoption neither overwrites the source nor starts GA. Passing validation does not guarantee reaching an ending or producing an interesting story.

### Using AI

Check [AI settings](settings.md#output-settings) first. AI input starts only on explicit request. The three-stage generation strategy interprets purposes, generates world settings, then reviews them, showing up to three model calls. This is separate from the three editing stages above.

Manual editing, saving and adoption work while AI is waiting. For real AI in a source installation, review the provider, model and cost before starting the server with `--control` and `--world-draft-live`. Saving a model setting alone does not leave waiting mode.

After a lost connection or uncertain outcome, use “AI入力の状態を確認” (Check AI input status) to inspect the existing request. Requests are not automatically resent. Returning to manual input or another draft prevents an old result from overwriting current input.

## World settings screen { #world-settings }

Open a saved world with “世界を開く” (Open world). The shared categories are:

| Category | Contents |
|---|---|
| 概要と物語の前提 (Overview and premise) | Name, overview, roles, ending and expansion proposals |
| 話の説明 (Story description) | Initial story: opening text and starting state |
| 登場人物と関係 (Characters and relationships) | Character settings, initial positions, stats, graph and relationship table |
| 場所と移動 (Places and movement) | Places and connections |
| 持ち物と情報 (Items and information) | Initial ownership, knowledge and acquisition methods |
| 時間と出来事 (Time and events) | Duration, time slots and scheduled events |
| 行動設定 (Action settings) | Action types and allowed conditions |
| 世界のルール (World rules) | Conditions, objectives, responses, planning and money |
| 確認・調査 (Checks and surveys) | Overview, routes to the ending, survey conditions and world review |
| 設定ファイル (Config files) | Direct YAML editing |

Choose “話の説明” (Story description) to open “初期物語” (Initial story) and inspect the opening text or starting state. The “初期物語” card and “導入文を編集” (Edit opening text) in “概要と物語の前提” (Overview and story premise) remain available. Existing `#screen=story` URLs are also supported.

“結末までの経路” (Routes to the ending) under checks is a read-only survey of up to three example walkthroughs. Some route layers or goal shapes are unsupported. See [Route Layer](../concepts/route-layer.md#route-paths-survey).

Save individual edits. **Past run results are unchanged.** The footer offers “未設定の項目を確認 →” (Check missing settings) or, when complete, “実行条件を決める →” (Decide run conditions), opening [Run Settings](run-settings.md). The read-only Viewer cannot edit settings.

### Stamina and starting money

Open stamina management through action settings. When enabled, configure maximum and initial stamina, recovery per rest/sleep, and per-action cost adjustments. Disabling stamina management preserves those settings; time, location, injury and death conditions still apply.

Once a currency is configured, edit each character's “開始所持金” (Starting money). If none is configured, follow the link to money settings first. These values apply as initial settings for future runs.

## Advanced settings and config-file editing { #advanced-settings }

Use “世界のルール” (World rules) or “設定ファイル” (Config files) in the left menu for advanced editing: roles/endings, conditions, success/failure/evaluation periods, purposes/responses, decomposition/replanning, money, conventions and the action catalog.

Genres (templates) are shared rules for actions and effects. In genre management, duplicate an existing genre with “複製して編集” (Duplicate and edit) before changing it, to avoid unintended changes to worlds using the original.

## Relationship graph { #relationship-workspace }

The "登場人物と関係" (Characters and relationships) category has three views: "一覧" (List), "相関図" (Relationship graph), and "関係表" (Relationship table).

- **相関図 (Relationship graph)**: shows characters' relationships as a directed graph. Characters are colored by affiliation (`affiliation`; falls back to their starting place if unset), with groups laid out around the protagonist's own affiliation. Toggle between "全体" (Everyone) and "人物フォーカス" (Focus on one character — pick one, then narrow to direct relationships / two hops / everyone), and filter by relationship kind ("好意"/positive, "反感"/negative, "中立"/neutral) or affiliation. Clicking a character opens a detail panel: affiliation, number of direct relationships, a summary, and their top relationships (up to 6). "関係を数値で確認" (Check relationships as numbers, a collapsible section) switches to a list with `affinity` and `awareness` values
- **関係表 (Relationship table)**: a separate tab from the graph, showing every character's relationships at once as a person-by-person matrix
- In either the graph or the table, an editable world shows a "ラベルを編集" (Edit label) button on relationship rows — you can set or clear a text label (up to 120 characters) for one direction (source→target) of a relationship at a time (the numeric `affinity`/`awareness` themselves can't be edited here)
- A character's **affiliation** (`affiliation`, up to 120 characters, optional) is set from the "一覧" (List) view, via that character's "人物を編集" (Edit character) dialog. This value drives the graph's coloring and grouping

In the graph, any configuration problems (e.g. malformed relationship data) are listed, with a count, under a "設定の確認" (Check configuration, collapsible) section.

## Scheduled events and force actions { #scheduled-events }

The "時間と出来事" (Time and events) category, alongside the run's day count and time-of-day breakdown, also lets you check and edit the world's **scheduled events** (`scheduled_events`) — events (target, effect, label) that fire automatically on a given day/time-slot.

An event carrying a **force action** (`force_action`) replaces the target's own action for that slot outright, with a given verb/args (e.g. force the protagonist to move to "道中" on the morning of day 3). The on-screen form guides you through which argument kinds and how many each verb takes. See [Templates](../reference/templates.md#scheduled-events) for the full spec (validation rules, etc.).

## World self-expansion { #world-expansion }

While running GA experiments, if a character's actions keep coming up empty at the same place (e.g. investigating turns up nothing), WorldBloom automatically proposes elements the world seems to be missing (places, items, facts). These proposals never rewrite the world settings directly — **they take effect only once approved**.

Cards for each proposal appear on the world settings screen's "概要と物語の前提" (Overview and premise) category, and on the relevant experiment's results screen. Each card shows:

- **何が増えるか** (What gets added): the place/item/fact being added (e.g. "Add place '△△' as part of '○○'")
- **きっかけ** (Trigger): which experiment, place, and action came up empty, and how many times out of how many attempts
- **検査の結果** (Check results): the outcome of the static gate (any rule violations) and the trial run (reproduction check on a holdout seed, contract check, change in reach rate)
- **書き手の説明** (Author's note): the explanation from whoever authored the proposal (reference only — judge by the check results above)

Only proposals that passed the static gate, were trial-run on a holdout seed, and are in the "人の確認待ち" (Awaiting human review) state can be approved. Approving requires entering a reason of at least 10 characters. Once approved, the proposal applies to that world's future runs. Rejecting it means it's never used.

Approved expansions are listed in application order under a "後から生まれたもの" (Added later) section. You can revert all approved expansions back to proposed at once via "承認済みの拡張をすべて提案中へ戻す" (Revert all approved expansions to proposed), but not just one at a time (later approvals are checked assuming the earlier approved expansions are already in place). Reverting means everything reverted has to go through the checks again from scratch.
