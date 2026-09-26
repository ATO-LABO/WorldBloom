---
ja_rev: "cd6f1570ad8b"
---
# Read Results

When a GA experiment finishes (or is stopped partway through), its results are shown on screen as a QD grid. This page explains how to read that screen; for how the mechanism itself works, see [QD Map](../concepts/qd-map.md) and [Sifting](../concepts/sifting.md).

## The QD map

A grid with **leading category** (I–VI) as rows and **volatility** (low/mid/high) as columns. The more cells are filled, the more different kinds of stories have been found for those combinations. Clicking a cell opens the detail of the representative candidate left in it (the run with the highest quality q).

Overall experiment metrics appear at the top of the screen.

| Metric | Meaning |
|---|---|
| 占有マス (Occupied cells) | Number of filled grid cells. More means more different kinds of stories found |
| 相異度 (Diversity) | Average of how different the paths taken by the runs left in the archive are from each other. Closer to 1 means more diverse |
| 到達率 (Reach rate) | Fraction of all runs in that generation (population × seeds) that reached the ending |
| q̄ | Average quality q across the whole archive |

## Candidate detail (Sifting's three tabs)

Opening a cell shows the candidate's detail across three tabs:

- **物語 (Story)**: the synopsis (if generated), the ending, and links to detailed rationale/generation status
- **選択とメモ (Choices & notes)**: the selection status (✔ Adopted / ⏸ Held / ✖ Excluded / ○ Unsorted) and a free-text notes field
- **実験データ (Run data)**: candidate ID, generation, individual, seed, reach, a link to the raw log (`layers.jsonl`), etc.

The candidate list screen shows these columns for each candidate:

| Column | Meaning |
|---|---|
| 傾向 (Tendency) | The action category that individual's genome weights most heavily (a different value from the leading category) |
| 到達 (Reached) | How many of this elite's evaluated seeds reached the ending |
| 原記録 (Raw log) | あり (present) / 剪定済み (pruned) / 不在 (absent) / 不一致 (mismatched). "あり" (present) allows text generation and viewing the raw log |
| 採用可 (Adoptable) | Whether it meets the requirements for text generation (reached the ending AND has a raw log) |
| 稿 (Drafts) | Number of texts already generated from this candidate |

## Choice, rationale, cost, and turning point

The candidate detail shows four fields — 選択 (Choice), 根拠 (Rationale), 代償 (Cost), 転機 (Turning point) — mechanically extracted from the log to explain what belief a given choice was made on, what it cost, and what it led to afterward. They're a way to read back a story's key decisions after the fact.

Right after 選択 (Choice), the four-field panel can show a **"道筋: (Route:)"** line. This appears when the [route layer](../concepts/route-layer.md) (an experiment run with ρ>0) classified that decision as 前進 (advance), 準備 (prepare), 寄り道〈身体／手探り／思い込み／a motive's name／理由なし〉(detour ⟨body/ignorance/belief/a motive's name/no reason⟩), or 見通しなし (lost), along with its reason text. It's absent for experiments that don't use the route layer (ρ=0, or a run predating the route layer).

## Reason badges in the action log

For experiments that use the route layer, the timeline (物語の流れ, story flow) also shows lines explaining "why" a decision was made.

- **Timeline reason line**: one line per route-bearing protagonist decision, formatted as `[classification label] event — reason`. For a detour ⟨no reason⟩, the reason part reads "no clear reason was recorded"
- **Breakdown at the top of the cell page**: opening a candidate's detail shows a count of that run's protagonist decisions by classification, "前進 n／準備 n／寄り道 n〈うち理由なし n〉／見通しなし n" (advance n / prepare n / detour n ⟨of which n are unreasoned⟩ / lost n)

Both are absent for experiments that don't use the route layer. See [Route Layer](../concepts/route-layer.md) for what each classification and reason means.

## Comparison, lineage, and turning-point visualization

Selecting 2–4 representative candidates from different cells in the candidate list opens the comparison screen. It also has three tabs — 物語 (Story), 選択・根拠・代償・転機 (Choice, rationale, cost, turning point), and 実験データ (Run data) — with the run-data tab listing candidate ID, cell, generation, seed, quality, ending, and raw log for each. From a candidate's detail you can also go on to visualize its lineage and turning points.

- **系譜（主系） (Lineage (main line))**: the single line traced back through parents from that elite. You can go back through generations and see which decisions accumulated into this story
- **転機 (Turning point)**: re-runs an adjacent parent/child pair on the lineage with the same random seed, and shows the point where the protagonist's decision first diverged. Lets you trace "why did this story turn out this way" as evolutionary history

If an ancestor couldn't be reproduced, the reason (a reproduction error) is shown.

## Run history and deletion

Once you start a run, it's recorded in "履歴" (History) for later reference. If an experiment is no longer needed, delete it from "削除" (Delete) in the experiment list. Deleting one cascades to remove its candidates, selection status, generated text, and world self-expansion proposals tied to that experiment (this cannot be undone).

## Epoch chain progress display

Running with "世界を育てる" (Grow the world) turned on in the run settings shows an epoch chain progress strip right below the heading on the run status screen, and on the run conditions screen too — epoch count, current stage, approval/retirement counts, and a stop button. A "次のエポックへ" (Next epoch) button also appears while awaiting approval. The demand tab likewise shows a hint at the top while awaiting approval: "承認または却下 →（必要なら）枯らす → 『次のエポックへ』" (Approve or reject → retire if needed → "Next epoch"). None of this appears on a screen with no chain.
