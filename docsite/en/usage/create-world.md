---
ja_rev: "e06506590d92"
---
# Create a World

A "world" is the full setup a GA experiment runs on: characters, places, time, and the target ending. Once you've built a world here, you run GA experiments against it from [Run Settings](run-settings.md).

## Create a new world

Open this from the home screen ("**世界を選ぶ**", Pick a world) via "**＋ 新しい世界を作る**" (+ Create a new world).

Choose one of two ways to create it:

- **新しく作る** (Create new): start from an empty world. The genre defaults to the shared base rules (`basic`) and can be changed later.
- **既存の世界から作る** (Create from an existing world): pick a source world from the list and copy its settings. The source world itself is unaffected. You can also pick a different genre (from a searchable list) at copy time.

Fields common to both:

- **世界の名前** (World name) — required, up to 120 characters
- **世界の概要** (World overview) — optional, up to 8000 characters (this field only appears for "Create new")
- Opening **詳細設定** (Advanced settings) lets you edit the **世界ID** (World ID): alphanumerics, hyphens and underscores only (1–96 characters); by default it's auto-filled as `world-` followed by 12 random characters

Creating the world takes you to that world's **world settings screen** (below). Characters, places, and the initial story start out empty and are filled in afterward.

## World settings screen (per-world tabs)

Opening a world (or clicking "世界を開く", Open world, from its card on the home screen) shows five tabs for viewing and editing its settings.

| Tab | Contents |
|---|---|
| 世界の概要 (World overview) | Edit the name and overview. Any approved or proposed changes from world self-expansion (below) are shown here |
| 登場人物 (Characters) | View and edit each character's parameters (9 stats), starting position, etc. |
| 場所 (Places) | The list of places (zones) and the connections between them |
| 初期物語 (Initial story) | Edit the opening text (the situation at the start) |
| 時間 (Time) | The run's day count and the day's time-of-day breakdown |

Each field can be saved individually. Saving **does not change results from runs already executed** against that world (the new settings apply starting from the next run).

The footer at the bottom of the screen shows "未設定の項目を確認 →" (Check what's not set up →) if any required item is missing (characters, places, protagonist, antagonist, target ending, or a character's starting position), or "実行条件を決める →" (Decide run conditions →, leading to [Run Settings](run-settings.md)) once everything is filled in.

In the read-only Viewer build, this screen is view-only and the save buttons do nothing (editing only works in Studio).

## Advanced settings and config-file editing { #advanced-settings }

From "詳細設定・設定ファイル ↗" (Advanced settings / config files) below the left-hand menu on the world settings screen, you can go further. It has four sections:

- **役割と結末** (Roles and endings): the protagonist/antagonist roles and the target ending settings
- **状況ごとの定石** (Situational conventions): the genre's conventions (what tends to happen in which situation), applied to this world's characters
- **行動図鑑** (Action catalog): the list of actions characters can choose from
- **設定ファイル** (Config files): direct editing of the YAML files inside the world's folder

To edit the template (genre) itself, use the "ジャンル" (Genre) tab on the home screen → open a genre, or "＋ 新しいジャンルを作る" (+ Create a new genre). A genre is the shared rule set — which actions exist and what they do — used across multiple worlds. An existing genre can only be changed after duplicating it via "複製して編集" (Duplicate and edit) (this doesn't affect other worlds still using the original genre).

## World self-expansion { #world-expansion }

While running GA experiments, if a character's actions keep coming up empty at the same place (e.g. investigating turns up nothing), WorldBloom automatically proposes elements the world seems to be missing (places, items, facts). These proposals never rewrite the world settings directly — **they take effect only once approved**.

Cards for each proposal appear on the world settings screen's "世界の概要" (World overview) tab, and on the relevant experiment's results screen. Each card shows:

- **何が増えるか** (What gets added): the place/item/fact being added (e.g. "Add place '△△' as part of '○○'")
- **きっかけ** (Trigger): which experiment, place, and action came up empty, and how many times out of how many attempts
- **検査の結果** (Check results): the outcome of the static gate (any rule violations) and the trial run (reproduction check on a holdout seed, contract check, change in reach rate)
- **書き手の説明** (Author's note): the explanation from whoever authored the proposal (reference only — judge by the check results above)

Only proposals that passed the static gate, were trial-run on a holdout seed, and are in the "人の確認待ち" (Awaiting human review) state can be approved. Approving requires entering a reason of at least 10 characters. Once approved, the proposal applies to that world's future runs. Rejecting it means it's never used.

Approved expansions are listed in application order under a "後から生まれたもの" (Added later) section. You can revert all approved expansions back to proposed at once via "承認済みの拡張をすべて提案中へ戻す" (Revert all approved expansions to proposed), but not just one at a time (later approvals are checked assuming the earlier approved expansions are already in place). Reverting means everything reverted has to go through the checks again from scratch.
