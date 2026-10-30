# Calendar purpose migration

This change connects the festival's personal purposes to the existing common
`GoalPlanner`, native task ledger and `PromiseRegistry`. No festival policy is
loaded by the new project. The original one-day slot world, three-day calendar
world, authored witnesses, search codecs and saved histories remain unchanged.

## Ownership and scope

`projects/cultural_festival_shared_planning` is a separate, opt-in migration of
the festival-day morning through 17:00. Its six people keep their existing
traits, inventory, initial knowledge, relations and starting locations. Their
`commitments` select purpose IDs, priorities and required conditions; the world
defines state predicates, compound success/failure expressions and dated periods.
Task roles, quantities, dependencies, resources and work windows remain world
facts. Neither the engine nor a natural-language keyword parser assigns purposes.

The source world's school-day attendance witnesses are not silently reinterpreted
as autonomous behavior. This input explicitly scopes out the two preceding days.
The new task ownership, priority numbers and definition of adequate preparation
are provisional authoring choices, recorded in the contract and project README.

Only the offer and both acceptances of four initial agreements are explicit
commands. They complete before purpose selection starts at 08:45. No move,
production, handoff, rehearsal, consultation, decision or wait is scripted after
that point. The native registry, not a forecast, determines promise fulfillment.
Removing acceptance does not create a promise through a personal commitment.

## Shared additions

### Work windows

Native task definitions may supply `windows`, a bounded ordered list of
nonoverlapping `{start: {date, time}, end: {date, time}}` intervals. Start is
inclusive; completion at end is allowed. Every work or handoff action must fit
entirely within one interval. Absent windows retain existing behavior.

The same compiler and containment check serve declaration validation, actual
admission, bounded search and wake-up boundaries. Rejected work never reserves
materials or consumes energy. Work quantities and promise requirements are still
updated only by actual completions. A window does not teleport participants,
grant consent or certify performance quality.

Search can wait until an authored relevant opening. A search-local boundary
marker prevents temporal waiting from being pruned as an unchanged state; it is
not applied to the physical world or the character's knowledge. Wait completion
has its own result and is treated as progress in the common planning view.

### Other purposes during a wait

When a higher-priority feasible plan includes waiting, the same planner may select
a lower-priority plan that fits the available slack. It projects its work, travel,
energy and recovery, then re-runs the displaced higher-priority plans from that
future state and time. This is bounded local scheduling, not a global optimizer.
It does not assume unreceived cooperation. Presence-guarded handoff is allowed
because it has bounded duration and actual execution still checks the recipient.
Inspection may use the gap without treating its unseen result as completed work.

### Observe before assuming a dependency is done

The calendar adapter can return a plan to visit a declared dependency's location
or agreed handoff place. It uses public routes and known access conditions, and
does not inspect remote task quantities. Actual arrival exposes local progress
through the existing task view. A worker may then find a prerequisite completed,
unfinished, or impossible; another person's future work is never forecast as a
guaranteed effect. Inspection is not a request to that person to work.

### Compound purposes

Personal assessment now uses a declared objective's whole success expression,
rather than scoring only its anchor predicate. The original anchor state is
retained as `predicate_state` when it differs. For example, costume AND rehearsal
must both be ready; an actual accepted OR rejected OR deferred consultation is a
received reply. A rejection cannot be mislabeled as absence of consultation, nor
does hearing a reply imply agreement. Existing single-predicate purposes retain
their previous assessment.

## Limits

Stage promises concern prepared presence during the declared interval, not the
internal performance or literary quality. The operational preparation definition
does not invalidate an earlier rehearsal after an accepted line change. That
versioned preparation dependency is still a later extension. The non-sabotage
intention remains descriptive and is not invented as a measurable achievement.

Task-based cooperation requests, counter-offer search, joint carrying, combined
worker speed, three-day school planning, the old GA fitness migration and general
narration are outside this change. The ordinary social consultation protocol is
reused; no native task-based cooperation binding is introduced.

## Validation

`tests/test_calendar_purpose_migration.py` covers autonomous six-person festival
choices, actual fulfillment, timing, preserved character inputs and common
explanations. A separate workshop world covers waits, spare-time work, priority
changes, impossible deadlines, handoff admission, compound predicates, remote
information limits, exact resume and actor/place/task renaming. Existing calendar,
concern, objective, shared planning, festival and presentation tests are run too.
