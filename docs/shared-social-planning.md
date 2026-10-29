# Shared social planning and scenario migration

Design contract for revisions 3-5. Keep the existing GoalPlanner search,
TimeActionKernel, actual Deliberation decisions and typed response operators.
No scenario names participate in these providers.

* A proposal is a protocol: submit, disclose, await reviews, amend/request,
  await the authority. Forecasts of other people's replies are conditional.
  Waiting never commits the forecast effect. Actual peers choose and execute
  their own reviews, acceptances and refusals.
* The optional social_planning declaration registers proposal prerequisites and
  explicit local cooperation requests. Prerequisites enter the same causal
  backward relevance trace as physical operators. No private peer preferences
  or unseen decisions enter the requester's search state.
* Each real action uses the same declared duration in planning and execution.
  A reply wait is a real action with a lower-bound duration and an explicit
  external-result assumption; deadline feasibility is conditional until heard.
  Failed/revised methods retain elapsed time, effort, older proposal revisions
  and terminal objective history.
* Existing signal-based delegation and worlds without social_planning retain
  their prior policy. New proactive requests identify an authored action and
  participant; they are not invented testimony. Acceptance alone does not
  produce work or transfer resources.
* Preserve the frozen Melos implementation as a comparison. A separate shared
  world describes travel, bridge/flood observations, wedding work, rest,
  return and the king's deliberation as data. The common run may differ from
  the old scripted choices; invariants, knowledge boundaries and outcomes,
  rather than identical prose or choices, are the comparison contract.
* All four data-only examples use the common goal/plan/action/result loop.
  Actual social replies and causal replanning reasons have source references.
  The existing read-only planning workspace displays these, including refusal,
  uncertainty and no-path results, without inferring motives from action names.

Acceptance: unforced physical and social choices; acceptance/refusal/amendment;
waiting without early success; hidden peer information; changed prerequisites;
exact time/cost; goal failure immutability; rename invariance; four topics;
legacy preservation; shared UI desktop/narrow, keyboard and raw-source checks.

## Configuration and execution

`social_planning.version: 1` is an explicit opt-in. Existing worlds keep their
policies. Example declarations:

```yaml
social_planning:
  version: 1
  proposals:
    - {proposal: review, requires: [documents_ready], reopen: true}
  requests:
    - {id: reserve_help, requester: sender, responder: technician, action: prepare_reserve}
```

Proposal IDs refer to existing deliberation definitions. Conditions in
`requires` are common concern IDs; their coordinates enter causal search and
are checked again against reality at submission/amendment. Reopening only
allows an unused authored alternative, with a new revision and new reviews.
Request IDs and proposal IDs are distinct. A request names a proactive response
operator and a capable responder. The requester forecasts its declared effects
conditionally. The responder checks its own knowledge, stamina, requirements,
priorities and protected conditions. A delivered local request, an acceptance,
and completed work are separate events. Failed accepted work is a separate
received result and no longer remains a promised success in search.

Time rules can cover the six proposal verbs, information sharing, requests,
replies and `await_response`. Use `protocol_steps` for reply waits, which derives
its lower-bound horizon from the actual declared response/work durations.
A forecast is a conditional timing estimate, not a reservation of the peer's
time. Peers can delay, refuse or change their assessment. Timed transactions
retain their offer while a timed recipient is answering; transfer happens only
at actual settlement. Previously paid movement/work is never charged or selected
again at completion. Real route/precondition/resource checks still apply then.

Routes optionally accept `requires_conditions: [condition_id]`. Unknown or false
conditions do not grant access. Search uses the actor's projected/received view;
execution checks the actual route, including again at completion. It does not
substitute another route after paying the selected route's duration. This is the
same mechanism for bridge availability, a secured corridor, or a permitted road.

Search remains bounded: at most 24 actions and 8192 queued candidates (each
world chooses lower bounds as needed). Exhausting search is not objective
failure. Intermediate tasks are not added as new personal values.

## Four common worlds

| Folder in projects | Purpose and intermediate work | Actual independent social result |
| --- | --- | --- |
| reform_shared_planning | Collect dossier, submit, amend, implement | Review and authority decision on the current proposal revision |
| ship_shared_planning | Recover/search battery, request preparation, transmit | Technician accepts/refuses using its own protected stock |
| village_shared_planning | Prepare materials, recover/search timber, request defense work | Local worker accepts/refuses using its own reserve |
| melos_shared_planning | Wedding, changed bridge access, crossing preparation, rest, return, disclose proof | King re-assesses received evidence and gives judgment |

The Melos world starts after the departure/hostage agreement, as declared initial
background. It uses three logical days with 16 authored slots per day. Its
common engine does not reproduce every literary scene or replace the archived
GA/connected Melos experiment. The frozen sources and old project remain intact
as a separate comparison. No canonical success is forced; insufficient time or
a king whose own threshold rejects the evidence produces a real failed purpose.

Run an example from the repository root (use a new output folder):

```powershell
$env:PYTHONUTF8='1'
python tools/run_planning_example.py --project projects/melos_shared_planning --out runs/melos-common-0 --seed 0
```

The same runner supports native `calendar_promises_v2` projects with common
planning and no explicit scripted choices. Native preparation tasks and proposal
protocols share `GoalPlanner.search`, minute accounting and checkpoint journal.
Native task-based cooperation requests are not yet a supported binding; the
cooperation binding here targets ordinary proactive response operators. Requests
are currently local to co-present participants. Existing delayed signal-based
delegation remains available under its original contract. No general simultaneous
multi-person scheduling or autonomous counter-offer search is introduced.

## Evidence and UI contract

Each actual received social event has a `receipt_id`, proposal revision/terms
hash when applicable, actual audience, and a link to the requester's personal
purpose. Selected plans retain the specific received events used in that
selection. Explanation extraction resolves those IDs to the actual source line
and ledger hash; it does not infer an actor's motive from an action name.
The common planning workspace shows these events alongside physical results.
Author-visible final outcomes, the actor's forecast, and received replies are
separate. Normal completion/progress is not classified as replanning merely
because the remaining list got shorter. An approval forecast in a waiting step
never appears as an actual approval. Existing saved logs remain readable.

Configurations can be maintained in the existing settings-file editor. This
change does not introduce a new dedicated form for the protocol declaration.
Validation rejects unknown references, unsupported native request bindings and
inconsistent checkpoints before applying runtime changes. The existing structured
planning editor exposes the expanded finite search bounds.

Validation includes ordinary and minute modes, refusal and changed priorities,
unknown remote replies, failed accepted work, re-review after disclosure, exact
checkpoint resume during a wait, deadline failure, four topic runs and four topic
identifier renaming. Reader quality is separate from these execution checks.
