---
ja_rev: "a1cc08e22c99"
---
# Generate Text

Generating the events themselves never involves an LLM. The LLM only comes in at two stages: turning candidates adopted in [Sifting](../concepts/sifting.md) into a **synopsis**, and turning that synopsis into **prose**. Before generating, choose an LLM connection under ⚙ Settings (see [LLM Backends](llm-backends.md)).

## Kinds

| Kind | Contents |
|---|---|
| Synopsis generation (synopsize) | Pull out notable scenes from each run in the grid and have the LLM write a short synopsis |
| Narration generation (narrate) | Turn only the adopted candidates into full-length prose |

## Generation flow

1. From the candidate list/detail in [Read Results](read-results.md), pick the candidates to generate (synopsis generation targets every representative candidate in the grid; narration targets only adopted candidates)
2. On the confirmation screen, review the contents (target count, generation settings) and click "この内容で生成を開始" (Start generation with these settings) to start the generation job. **The LLM is not called until you do this** (just opening the list/detail does not trigger generation)
3. You can watch the generation job's progress by polling the list/detail screens. If the server can't be reached, it shows "サーバーに接続できません（再試行中）" (Can't reach the server (retrying))
4. Once generation finishes, you can review the result for each candidate

## Per-candidate status

| Status | Meaning |
|---|---|
| 未開始 (Not started) | Not generated yet |
| 生成中 (Generating) | In progress |
| 本文あり (Has text) | Generated successfully |
| プロンプトのみ (Prompt only) | Text was not generated, only the prompt was saved (when the LLM backend is set to "生成しない", no generation) |
| 失敗 (Failed) | Generation failed |
| 結果不明 (Result unknown) | The LLM was called but success/failure couldn't be determined (see below) |
| 上限で未開始 (Not started: limit reached) | The limit (e.g. generation count cap) was hit before the call |
| 停止で未開始 (Not started: stopped) | Not started because the job was stopped |
| 中断で未開始 (Not started: interrupted) | Not started because the job was interrupted |

The overall job status is shown as one of: "全件生成" (All generated), "プロンプト保存のみ（本文未生成）" (Prompts saved only, no text generated), "本文とプロンプトの混在" (Mix of text and prompt-only), "一部成功" (Partially succeeded), "一部成功（結果不明あり）" (Partially succeeded (some unknown)), "結果不明（失敗確定ではない）" (Unknown (not confirmed failed)), "呼出し前に上限到達" (Limit reached before calling), "失敗" (Failed), "停止" (Stopped), or "中断" (Interrupted).

## Retrying after a failure or unknown result { #retry }

The suggested retry method depends on the situation.

- **問題を解消してから『未生成・失敗分を生成』で新規要求できます** (Once the problem is fixed, you can re-request via "Generate ungenerated/failed items"): when the cause is clear (e.g. a config mistake) and it's safe to just redo it
- **結果不明です。二重生成の可能性を確認したうえで『結果不明の候補を確認して再生成』から明示的に再生成してください** (Result unknown. After checking for possible duplicate generation, explicitly regenerate via "Review unknown-result candidates and regenerate"): when success/failure couldn't be confirmed after the LLM call. Since the same content might get generated twice, this requires an explicit, confirmed action
- **保存障害。LLM は再送せず『復旧』で保存済み証跡から復元します** (Save failure. The LLM is not re-sent; use "Recover" to restore from the saved evidence): when generation itself succeeded but saving it failed. This restores from saved evidence without calling the LLM again
- **上限に達しました。上限を見直した新しい設定版で新規要求してください** (Limit reached. Re-request under a new settings version with a revised limit): when a limit such as generation count was hit. Here, "settings version" refers not to the GA run settings but to "生成の上限" (Generation limit) under the "文章生成" (Text generation) tab of ⚙ **全体設定** (Global settings) (see [Settings](settings.md#output-settings)). Saving a revised value creates a new settings version, which is used for generation requests from then on

## Reusing text-generation settings across experiments

Text-generation settings (backend, model name, limits, etc.) are stored separately from run settings (the GA config), under the "文章生成" (Text generation) section of ⚙ **全体設定** (Global settings) (see [Settings](settings.md)). There is no per-experiment generation config, so generating from a different experiment reuses the same settings as-is. To change them, open ⚙ Settings before generating.
