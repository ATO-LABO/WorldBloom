# Character objectives: achievement, irreversible failure and period

For existing negotiation and transfer verbs, see
[objective transactions](objective-transactions.md).

Character YAML continues to own `commitments` (condition ID, priority and
requiredness). World YAML owns instantaneous `concerns` predicates, causal
`responses` operators, and this optional shared `goal_rules` block:

```yaml
goal_rules:
  version: 1
  definitions:
    - condition: food_taken
      success: {condition: food_taken}
      failure:
        all:
          - any:
              - {condition: raid_repelled}
              - {condition: raid_completed}
          - not: {condition: food_taken}
      period: {start: 1, end: 12}
```

Each named predicate is registered in `concerns.conditions`. Expressions read
its instantaneous test, never another objective's terminal history; therefore
there is no recursive dependency on the goal's own result. Supported nodes are
`condition`, boolean `constant`, `all`, `any`, and `not`. There is no authored
code execution. References, exact fields, booleans, integer periods, nonempty
groups, maximum depth 4 and maximum 64 nodes per expression are validated.
An achievement expression must include its valued condition. Each condition has
at most one rule, and cannot also have a legacy `responses.windows` judgment.
The period must fit in the world's declared logical time range.

## Timing and terminal meaning

The condition's existing `mode` applies: `achieve` can finish as soon as success
is true and failure false; `maintain` must preserve success throughout the
period, and cannot succeed before its end. Explicit failure is checked first;
a maintenance breach is also terminal. If success and failure become true in
the same observation, failure wins. A previously terminal achievement/failure
retains its first cause and time, even if a later repair changes current state.
Repeatable/reopened missions need a distinct authored rule/episode; no implicit
reset is performed.

Both endpoints are inclusive. The existing ordinary clock gives every living
actor one choice at each logical time point. Rules are observed after each
action and around system changes so a breach repaired within the same time
point is retained. Deadline failure is evaluated only after all choices and
system effects at the ending point, allowing a final action to complete there.
Unmet success before that boundary remains `pending`. A skipped/unobserved
period remains `unknown`, not silently successful. The statuses are
`not_started`, `pending`, `achieved`, `failed`, `unknown`.

World reports and each actor's recognition are separate. The actor evaluates
the same expressions through existing visibility and beliefs; three-valued
logic propagates unknown information. For example, `not unknown` is unknown,
while `false AND unknown` is false. Neither author-level terminal records nor
another person's private state/history is read to score choices. A character
can continue toward a remotely failed objective until they learn its failure.
Hearsay-based tentative judgments can be revised when their evidence changes;
directly witnessed terminal history is retained. Unavailable actors do not
acquire new outcome knowledge. Uncertainty is retained in actor records and continuous observation gaps cannot
be retroactively asserted as confirmed maintenance. Continuous recognition retains
the lowest confidence and evidence sources observed during its whole period;
a final direct observation does not upgrade earlier hearsay to certainty.
Current-state changes after the period do not rewrite historical recognition. Authored public predicates
remain intentionally public.

Rule changes appear as `objective_outcome` markers, including at deadline
closure before an ending stops the run. Decision details retain actual outcomes
and actor-scoped forecasts separately; daily snapshots retain both histories.
The rule configuration and hash are included in the optional run header.

## Proactive actions

`responses.version: 2` adds explicit `signal: null` for an action motivated by
its actor's selected objective. It requires the corresponding goal rule; it
does not invent a danger report or force an actor at a scheduled time. The
existing action candidates, priority ordering, nine-gene Policy, ordinary
adjacent movement, work costs/steps and runtime causal checks still apply.
A candidate must forecast achievement and avoidance of failure using its own
evidence, respect the period, and not defeat an affected required/higher-priority
value. It cannot teleport, acquire loot before work, bypass an actual guard or
inspect an opponent's private values. Proactive delegation is not added; the
existing report-based requests/replies retain their epistemic contract.

Version 2 can declare up to eight observation zones per state key. This models
one power state visible both at a breaker and at a communications indicator;
it does not create two copies of that state. Version-1 single-zone declarations
and old response behavior are unchanged. `goal_rules` is deliberately distinct
from the existing `World.objectives` dictionary for item ownership.

The new contract supports ordinary instant execution only. Slot/calendar
combinations fail explicitly. Planning remains one registered operator plus
its adjacent route/work steps; arbitrary causal inference, multi-operator
planning and automatic interpretation of natural-language goals are not claimed.

## Examples and verification

- `village_objectives`: both Momotaro and the ogre choose their own routes and
  defense/raid work. Default world events are empty. Only a completed ogre raid
  damages the village and transfers its one food bag. The messenger's initial
  share is the only scheduled action; neither lead character is forced.
- `ship_objectives`: a communicator carries a battery to the radio while an
  engineer shuts down dangerous power. Their work can race; a cutoff before
  transmission explicitly fails the communicator without waiting for deadline.
- `reform_objectives`: a real proposal deliberation must approve reform before
  local implementation changes qualification. A closed implementation window
  without individual eligibility is an explicit failure. Approval remains
  separate from implemented results.

The shared editor exposes the triple in 世界の詳細設定 → 達成・失敗・判定期間.
Checking does not write, saves preserve unrelated fields/people, stale revisions
are rejected, and deleting referenced predicates or shortening the world below
a registered period is blocked. Existing v1/v2 concern inputs and old saved
records need no migration for this optional feature. Batch evidence retains all
predeclared seeds and the outcomes of losing characters; it does not report a
single global success metric for mutually competing objectives.
