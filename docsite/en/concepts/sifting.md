---
ja_rev: "844edc9fcb05"
---
# Sifting

Sifting is the process where a human reads the candidates left in the [QD Map](qd-map.md) and decides which are worth turning into a synopsis or prose. In WorldBloom, judging "is this interesting" is always done by a human, never by the LLM (the LLM's only role is turning the chosen path into prose).

## Screen layout

Clicking a grid cell opens the candidate's detail across three tabs.

- **物語 (Story)**: this candidate's synopsis (if generated), the ending, and links to detailed rationale/generation status
- **選択とメモ (Choices & notes)**: the selection status and a free-text notes field
- **実験データ (Run data)**: run data such as candidate ID, generation, individual, and seed, plus a link to the raw log (`layers.jsonl`)

## Selection status

Each candidate carries one of four statuses.

| Status | Meaning |
|---|---|
| ✔ 採用 (Adopted) | Proceed to generation |
| ⏸ 保留 (Held) | Decide later |
| ✖ 除外 (Excluded) | Don't use |
| ○ 未分類 (Unsorted, default) | Not judged yet |

Only adopted candidates go on to [generate a synopsis/text](../usage/generate-text.md). A held candidate can be revisited later and switched to adopted. Selection status and notes are saved as a "selection version" — if another tab or run already saved a newer version, a later save is rejected (to prevent overwrites, you need to reload the latest state before saving).

## Comparison, lineage, and turning points { #compare-lineage-turning }

Selecting 2–4 representative candidates from different cells opens a comparison screen. You can read them side by side across three tabs: synopsis, "choice/rationale/cost/turning point", and run data (candidate ID, cell, generation, seed, quality, ending, raw log). From a candidate's detail you can also visualize the **lineage (main line)** — the single line traced back through parents from that individual, letting you see which decisions accumulated into this story across generations — and the **turning point** — re-running an adjacent parent/child pair on the lineage with the same random seed to show where the protagonist's decision first diverged. Use these when you want to trace "why this story turned out this way" as evolutionary history.
