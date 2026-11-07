# Calendar objectives, promises and recovery

This opt-in integration uses the same `GoalPlanner.search`, priority/safety
assessment and bounded state search as ordinary turn worlds. A calendar domain
adapter exposes native minute movement, task work, handoff and rest. It does not
migrate the frozen Melos scenario or invent social consent.

Calendar worlds declare ordinary `planning` and `concerns` plus `goal_rules`
version 2. Each objective period point is `{date: YYYY-MM-DD, time: HH:MM}` in
the contract calendar. Authored dates compile to the same absolute minute axis
that executes actions and checks promises. Completion precedes deadline verdicts.
Display checkpoints never change duration, energy, priorities or choices.

Conditions may read native `task` quantities or a `promise` with an exact version
and allowed statuses. Promise conditions never create an agreement. Terms,
acceptance and status must have reached the actor through normal observations;
even a public condition cannot expose unreceived private agreement state. The
actor's commitments own importance and required constraints. Registry history
remains authoritative, including cancellation and renegotiation. Forecast work
counts only matching place, actor, recipient, version and fulfillment window.

The optional contract `energy` defines nonnegative move/work costs per minute,
positive recovery per minute and a bounded rest duration. Both admission and
search use these rates. Recovery occurs at rest completion and cannot exceed
the character's capacity. Search keeps energy alongside time, so a useful rest
may be inserted before or between physical steps. Ordinary opted-in timed
worlds also search rest using their existing character recovery and duration.
No stamina or history is changed by a hypothetical search.

Native explicit commands may establish a consensual initial situation. Later
planner actions are selected at runtime, journaled separately, and labelled
`bounded_goal_search`; explicit witness commands retain their original label.
Only idle living actors choose. Existing role, location, resource, dependency,
material and handoff checks remain authoritative at execution. A delivery is
conditional on the recipient remaining present. Search cannot promise another
actor's future cooperation. Unknown remote work is not treated as completed.

Acceptance covers dates and exact deadlines, rest-required feasibility versus
insufficient time/capacity, actor priorities, unreceived/changed agreements,
retained work and spent energy, two renamed worlds, checkpoint continuation,
and unchanged legacy worlds without this opt-in configuration.

## Declaration example

```yaml
planning: {version: 1, actors: [worker], max_depth: 12, max_nodes: 2048}
concerns:
  version: 2
  conditions:
  - id: order_kept
    label: Keep the accepted order
    mode: achieve
    visibility: observed
    test: {kind: promise, promise: order-1, version: 1, statuses: [fulfilled]}
goal_rules:
  version: 2
  definitions:
  - condition: order_kept
    success: {condition: order_kept}
    failure: {constant: false}
    period:
      start: {date: '2026-11-01', time: '08:00'}
      end: {date: '2026-11-01', time: '09:08'}
```

The worker's subject file supplies its `commitments`. The native agreement must
actually be offered and accepted; the condition does not create it. Examples
`calendar_promise_workshop` and `calendar_promise_rescue` also declare irreversible
agreement failure and supply complete initial-situation commands and energy.
These declarations currently use YAML; no separate date/promise form is added.
Native materials are one shared public stock; hidden remote material ledgers
are not invented by this adapter.

No-path assessments are separate source events with `action_started: false`,
not fictitious rests or new physical choices. They remain readable together with
actual world verdicts even if no action could start. Maintaining a location is
checked again after departures at that same minute, before closing its period.
The old frozen Melos workflow and deliberation planning remain separate tasks.

Optional dated task windows and wait/slack planning are described in
`festival-purpose-migration.md`. The separate `cultural_festival_shared_planning`
project connects six people's purposes to actual work and accepted promises;
the old cultural festival inputs and authored witnesses are preserved.
