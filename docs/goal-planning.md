# Automatic prerequisites and replanning

This optional ordinary-instant runtime connects character-owned final purposes
to existing, authored causal operators. Intermediate tasks are not extra genes,
personality traits or saved commitments. Worlds without `planning` retain their
previous candidate policy and ledger shape.

```yaml
planning:
  version: 1
  actors: [通信士]
  max_depth: 10
  max_nodes: 512
```

The advanced world workspace has a shared-style editor under “目的の分解と再計画”.
Check validates an unsaved draft without writing; save uses the same revision
guard and durable world-editor transaction as other world settings. Existing
person files and unrelated world fields are preserved. Disabling planning is
rejected when doing so would orphan intermediate proactive operators that do
not have standalone objective contracts.

## What is derived

The actor starts from its own active `goal_rules` and personal commitments.
The planner traces the success/failure coordinates to authored response effects,
then to those operators' item, knowledge and state preconditions. This discovers
causal dependencies without author-written planning edges. A bounded forward
search assembles the corresponding operators and ordinary adjacent moves.

For example, the only valued final purpose can be sending a beacon. Its sending
operator requires a battery and consumes it. The planner can obtain the battery
through a retrieval operator or an existing negotiation, then return and send.
An inventory predicate describing the prerequisite remains a world definition;
the character does not have to value battery ownership independently.

Supported steps are ordinary `move`, proactive `respond`, and the existing
`negotiate` / later independent `concede` protocol. Intermediate response
operators retain their world `condition` label but, in an enabled world, need
not have independent temporal goal rules. Their capabilities, explicit boolean
knowledge requirements, resources, effort, actual state guards and authorization
gates still apply. Planner worlds currently require response version 2 with no
signals/channels and ordinary instant time. Existing reactive response worlds
and calendar/slot runtimes are not implicitly migrated.

## Epistemic boundary and failed attempts

Search reads the actor's own inventory/knowledge, actual observations at declared
sites, explicitly public condition coordinates, remembered observations, and
authored beliefs. It does not look at remote hidden state, future scheduled
events, another person's private preferences or author-level terminal history.
Observation sites and authored capabilities form the known problem description.

An unknown state guard may be considered as an explicitly conditional branch.
Its assumed value is propagated only inside that hypothetical search node;
incompatible assumptions cannot both hold without an intervening effect. This
never adds knowledge or items to the live world. Direct known contradictions
exclude a branch. Negative beliefs and actual failures are not treated as
evidence for a particular alternative hidden value.

Before execution, the selected plan is checked again from current actor-scoped
inputs. Actual operator guards are checked by the existing response engine.
When an attempt with one hidden state guard fails, the actor can learn that
the required value did not hold. It does not thereby learn the actual remote
value or a drifting object's location. A failed joint guard is remembered as a
failed conjunction; the runtime does not guess which clause failed. Repeated
attempts with that unchanged evidence are blocked. New actual observations can
justify different actions.

Visible information is remembered both before leaving a site and after an
action. Thus a character does not forget an observation merely by moving away.
If no justified sequence is found, it can travel to a reachable declared
observation site and re-evaluate on arrival. Sites are tried at most once per
root and own-failure epoch, preventing purposeless observation loops. Failed
search or a search-budget limit is never promoted to a final goal failure.

## Priorities, resources and consent

Every prefix must respect affected required conditions and conditions at least
as important as the current root. A later repair cannot excuse an earlier
required breach. Complete feasible plans are compared by the actor's ordinal
condition vector, priority 10 down to 1, before assumptions, duration and effort.
Within the search budget this preserves optional valued assets when another
plan can achieve the root without losing them. Higher-priority infeasible roots
do not prevent safe work on lower-priority feasible ones.

Effort and movement costs are accumulated conservatively against current
stamina. Work already performed on the current operator is retained. Actual
failed guards reset only that operator's incomplete work; prior spent effort,
time, facts, transfers and relationship changes are not undone. Rest can restore
resources in the live simulation, after which a new plan may become feasible;
search does not forecast future regeneration or invent a rest schedule.

An intermediate negotiation uses the old payment bundle, not price selection:
all eligible lootable items except the requested item. It forecasts a potential
one-unit acquisition conditional on the other person's consent. The old
negotiation verb records a real offer and transfers nothing. The recipient
independently chooses to accept or refuse through its own conditions and the
existing stance/attractive-item rule. Acceptance rechecks the original payment,
claimant constraints and the remaining causal continuation before transfer.
The claimant can wait through the next logical point. A refused or unanswered
unchanged proposal is not searched as a future guaranteed retry; new assets or
newly observed stock can justify another attempt. No counteroffers or subset
pricing are introduced. A refusal can therefore lead to an alternative physical
recovery plan while preserving the same final purpose.

## Bounds and outcome records

Depth is 1–12 operators/moves; generated nodes are 16–2048. Both generation and
expansion are bounded. State search retains nondominated time/effort/assumption
budgets, avoiding travel cycles that accomplish nothing. A returned feasible
plan may be marked `search_truncated`; it is not a proof of global optimality.
Unknown assumptions likewise do not guarantee real success.

The planner never overwrites world or actor goal-rule outcomes. A method can
fail while the final purpose stays pending. Final failure remains governed by
its authored irreversible failure expression or inclusive deadline. A valid
alternative can continue until then; no-path and search-limit states can wait
or gather information without fabricating success or terminal impossibility.

Headers include the opt-in configuration/hash; snapshots retain runtime memory
and plan history only for enabled worlds. Decision details contain `planning`:
the root, remaining causal steps, assumptions, bounded-search status and why
the plan was reconsidered. The explanation panel shows this separately from
real goal outcomes. Intermediate goals are never written back to person YAML.

## Reading a recorded plan

Candidates with recorded planning decisions have an optional "目的と計画" tab.
It groups records by actor and final objective, shows initial plans, failed
attempts, actual path changes, completed work and matched exchange replies.
Ordinary moves and partial work are hidden until "全行動を表示" is selected.
Replanning compares the remaining tasks before and after the change; movements
remain available in the complete-route disclosure and original record links.

The final world verdict is explicitly author-facing. Each selected historical
scene retains its own world verdict and action result, alongside the plan made
when selecting that action. Future success is never presented as that actor's
knowledge. A no-path or search-limit record is not an objective failure. Separate
objective-outcome events are retained, including deadlines after the last choice.
The display does not rerun planning or change a saved simulation or person file.

Routine progress is recognized by comparing the remaining verb/argument sequence
after a successful move or completed task. A broad `new_information` reason alone
does not create a path-change milestone. Exchange replies are attached only to
the matching claimant, partner and objective. The timeline summarizes recorded
events; it does not establish unrecorded causality or infer an opponent's mind.

## Examples

`projects/ship_planning`: a communicator values sending a beacon, ship safety
and retaining a tool. It first attempts to retrieve a stored battery. An
environmental accident removes that possibility during real work. The actor
learns of failure, searches for the drifting battery's location, physically
recovers it and consumes it to send. Giving the supply officer a public spare,
and removing the communicator's optional preference to retain the tool, permits
an alternative real exchange. A required stock condition can make that officer
refuse and cause the communicator to return to the recovery plan.

`projects/village_planning`: completing a defensive fence requires both timber
and rope. The actor prepares rope, attempts a timber retrieval, learns it failed,
searches downstream, hauls the timber back and consumes both resources in the
final construction. Neither resource is a separate personal commitment.

These examples use declared physical operators, not arbitrary natural-language
invention. New means, facts and object whereabouts must be represented by the
world's supported conditions and effects. Nested social commitments, general
multi-agent joint planning and unbounded long-term search remain outside this
first implementation.
