# Quality scoring log contract

`gapengine.qd.quality(rows, world_meta) -> float` remains the scoring entry point
used by existing callers. `quality_breakdown(rows, world_meta)` exposes the same
calculation as a named, read-only `QualityBreakdown`. It retains no references to
the inputs and does not mutate them. Its nested `QualityCounts`, `QualityMetrics`
and `QualityPenalties` are also immutable named tuples containing only numbers.
This is a structural score, not a judgment of prose or reader satisfaction.

```python
from gapengine.qd import quality, quality_breakdown, read_rows

rows = read_rows("<run>/layers.jsonl")
breakdown = quality_breakdown(rows, {})
assert breakdown.total == quality(rows, {})
print(breakdown.counts.learning, breakdown.metrics.learning)
print(breakdown.aggregate, breakdown.penalties, breakdown.total)
```

## Supported interpretation

The input is an ordered sequence of WorldBloom layer-log mappings, normally
`layers.jsonl` decoded as JSON objects. Row order matters. `world_meta` is a
mapping. No validation or new rejection of previously accepted inputs is added.
Missing supported observations contribute zero; a returned number alone does
not establish that an arbitrary input format carries the intended meaning.
Existing numeric conversions still apply: malformed numeric values can raise
the same conversion errors as before.

For the main counters and decision penalties, the protagonist is
`str(world_meta.get("protagonist") or first_header_protagonist or "")`.
The first row with `kind == "header"` supplies the header protagonist, converted
to a string if present. A later header does not override it. **Prerequisite
chains use only that first header protagonist**, even if `world_meta` names
someone else. A missing/`None` header protagonist disables chain counting.

`counts` exposes these unnormalized observations. The seven `metrics` use the
same field names, in the table's order, each calculated as `min(1.0, raw / divisor)`.

| Field | Raw observation | Divisor |
| --- | --- | --- |
| `objective_changes` | Sum of the number of entries in each mapping-valued `delta.objective`, on any row; values need not differ or be truthy | 2.0 |
| `affinity_change` | Sum of absolute numeric `affinity` values in list-valued `delta.relations`, when the protagonist matches the string-converted observer or target; missing affinity is 0 | 3.0 |
| `revived` | Protagonist events with `verb == "revived"` | 2.0 |
| `strength_reversals` | Sign flips across all `details.strength_diff` values in row order, irrespective of kind or subject; zero is skipped | 2.0 |
| `learning` | Protagonist `learn_fact` events plus protagonist `observe` decisions | 3.0 |
| `dramatic_turns` | All betrayal/exposure events; chosen payoff events; non-invalid goodwill concede decisions; each changed belief value in protagonist rethink events | 2.0 |
| `completed_chains` | Distinct completed prerequisite chains described below | 2.0 |

Only mapping-valued `delta`/`details` are interpreted. Non-mapping relation
entries are skipped. Rethink compares `before` and `after` mappings over the
sorted union of fact IDs; a missing belief or non-mapping belief supplies a
`None` value. One rethink can count several dramatic turns. Betrayal, exposure,
chosen payoff and goodwill concede do not require the protagonist as subject.

There is deliberately no common effective/invalid filter. Learning includes
invalid and ineffective observe decisions. Delta and strength observations are
not filtered by result. Goodwill concede excludes only `result == "invalid"`.
Repetition and stalls include every decision of the selected protagonist,
including invalid or ineffective decisions. `descriptor()` has its own
effective-decision filter; it is not the quality filter.

### Prerequisite chains

Decisions with `result == "invalid"` are skipped; `effective` is not checked.
Targets come from the first element of a nonempty **list** `args`, converted to
string; otherwise the target is `"-"`. A tuple or string is not a target list.
For the header protagonist, the following ordered chains each count once per
pair, with no expiry or adjacency requirement:

- `observe(target)` then `neutralize(target)`, deduplicated by directed pair.
- `pledge(target)` then a protagonist betrayal event with string-valued
  `details.target`, deduplicated by the unordered pair.
- `negotiate(target)` then that target's `concede(protagonist)`, deduplicated by
  the directed negotiation pair.

The betrayal event is not subject to the decision invalid filter. Different
chain types can count separately for the same pair. No header means no chains,
even when `world_meta` supplies a protagonist.

### Penalties and arithmetic

`aggregate` is `sum(metrics) / len(metrics)` in the seven-field order above.
The implementation then subtracts each penalty separately in this order:

1. `penalties.repetition = 0.05 * counts.repetition_runs`. Scan protagonist
   decisions in order, ignoring intervening non-protagonist rows. A run key is
   string-converted verb plus the first-argument target rule above. A run counts
   **only when it reaches length three**; its fourth and later decisions add no
   penalty. A changed key starts a new run. Other arguments do not affect it.
2. `penalties.stalls = (counts.stalls / counts.protagonist_decisions) * 0.3`
   when there are protagonist decisions, otherwise 0.0. Stalls are `rest` or
   `withdraw` decisions.
3. `penalties.dangling_effects = 0.05 * counts.dangling_effects`. Search rows
   backwards. On each row prefer a non-`None` `details.dangling_effects` over a
   non-`None` top-level `dangling_effects`. The first found value wins after
   `max(0, int(value))`; a negative value becomes zero and still stops the search.

`penalized_total` is the unrounded result after those subtractions. `total` is
`round(min(1.0, max(0.0, penalized_total)), 12)`. Penalties must not be summed
first: regrouping floating-point arithmetic can change the historical result.
No intermediate rounding is added. The wrapper returns exactly `total`.

## Event-only Melos inputs

Melos event-only logs are not a supported equivalent source for these seven
WorldBloom metrics. Their narrative events do not supply all layer deltas,
protagonist decisions, belief changes and prerequisite chains with the semantics
above. Passing such rows may still produce a number under the existing permissive
function; that number is not an equivalent Melos quality assessment. This change
does not convert, infer, repair or reject those logs. Any future adapter needs a
separate specification and validation; no adapter or score change is included.

## Compatibility and verification

The characterization oracle in `tests/test_quality_breakdown.py` freezes the old
implementation and its helpers from develop commit
`e2f0ecc5cf62577d8bdc93a00da572e68083b2f2`. Exact differential checks cover the
checked-in layer logs and seeded synthetic inputs, including input non-mutation.
The new API is opt-in: existing archive, selection, checkpoint and viewer output
schemas do not gain fields.

Determinism and resume guarantees remain within the same code and supported
Python version. Editing `qd.py` changes `gapengine_hash`; the existing hash guard
may correctly reject a checkpoint created by older code. This refactor does not
weaken that guard or promise cross-version checkpoint resume. Preserve the old
code together with old experiments when continuing them.
