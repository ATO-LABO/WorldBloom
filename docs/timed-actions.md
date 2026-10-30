# Shared timed actions: first integration

Status: implementation contract for the logical-turn integration. The existing
TimeActionKernel/ActionWindow/ActionLifecycle remain the only clock and pending
action mechanism. They also serve the minute-based calendar runtime. This step
does not migrate the frozen Melos scenario or deliberation. The subsequent
calendar-goal integration connects native calendar tasks and received promises
to the same causal planner; see `calendar-goal-planning.md`.

## Configuration and compatibility

Worlds may opt in with `action_timing: {version: 1, rules: [...]}`. A rule has
`verb`, `duration`, and `interruptible`. Initially supported providers are move,
respond and rest; unregistered verbs retain their existing instant behavior.
Duration is an integer number of boundary intervals (1-32), or `work_steps` for
respond. Move durations multiply by actual route hops. Movement cannot yet be
interrupted: there is no intermediate map coordinate or return-travel rule.
Unknown/duplicate rules and unsupported combinations fail validation.

Without this section, candidate policy, decision rows, work progress and timing
are unchanged. Existing response `steps` retain their old meaning. In an enabled
rule, the same steps supply duration and the existing cost supplies total effort;
effects still come from the existing response definition, never a second copy.
When rest is timed, recovery occurs at rest completion; the ordinary additional
per-slot free recovery is disabled in that opted-in world to avoid double gain.

## Boundary contract

Display turn 1 maps to kernel offset 0. An action beginning on turn 1 with
duration 2 finishes on turn 3 (morning -> noon -> evening). It does not complete
when selected. Each boundary runs scheduled/environment events, accrues actual
effort, checks ongoing actions and completes due actions, records objective
states, then lets available actors select once. Other actors and events keep
advancing while someone is busy. Completion on a deadline boundary precedes the
closing deadline verdict; later completion never revises a terminal verdict.

Effort accrues proportionally to elapsed intervals, without double charging at
completion. Failure/interruption keeps time, effort and all earlier effects.
There are no partial success effects; interrupted work must restart. Movement
keeps its last endpoint until completion, is marked in transit and cannot observe
or interact as a stationary occupant of either endpoint while in transit.

Actual guards are tested on admission and again while work proceeds. Failure
reveals only the normal failed guard evidence, not a hidden remote value. A
change in actor-scoped observations can interrupt interruptible work when the
common planner no longer recommends that action. No additional random choice
is drawn for continuation. Death, external relocation and lost requirements
cancel pending actions; no stale completion is replayed.

## Planning and records

The planner uses the same normalized duration/remaining effort as execution.
Legacy steps complete at their final selection point; timed steps complete at
the following boundary after their last interval. These are explicit completion
delays, not a global off-by-one conversion. Starting/continuing is not success.
Admission cannot create an action beyond the observation range. An early stop
retains an unfinished record instead of fabricating completion.

Selection remains a decision row. Starts, progress, completion, failure and
interruption are separate activity events with source action, start, finish,
unit, elapsed effort and selected-at metadata. Snapshots include pending actions.
The read-only planning view can merge these execution records in source order,
without calling them fresh actor choices.

## Acceptance

Check exact completion/deadline boundaries, two concurrent actors, intermediate
failure and new evidence, no destination observation during travel, no early
resource acquisition/recovery, no double cost, terminal goal immutability,
observation-end rejection/early stop, rename invariance, real unforced episodes
on two worlds, and unchanged decisions/snapshots in non-opted-in worlds.
