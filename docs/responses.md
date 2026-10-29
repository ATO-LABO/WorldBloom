# Concern driven actions

The opt-in `responses` block connects a valued report to ordinary action
selection, adjacent movement, local work and delayed requests. Old worlds
without this block have no added choices or runtime snapshots. Version 1
explicitly rejects slot/calendar execution rather than silently skipping it.

## Authored contract

See `projects/village_response/world.yaml`, `ship_response` and `reform_response`
for complete editable examples. All blocks use version 1 and reject unknown
fields. State values are strings. Each response state coordinate has an explicit
observation location; it does not share keys with proposal state.

Signals map existing valued facts to a condition and a state coordinate.
Danger values, evidence threshold, maximum information age and an optional
absolute logical-step completion deadline are explicit inputs. Signal truth
must agree with initial response state. Observing the site reads current state.
Existing Subject beliefs remain the actor's belief store; runtime observations
only retain provenance and the original observation time. Ordinary successful
information sharing preserves that time. Rejected testimony cannot refresh it.
Initial authored beliefs are dated at logical step zero. Remote hidden changes
do not revise a person's belief or candidate weights.

Actions explicitly declare capable actors, location, boolean evidence, minimum
inventory, stamina cost per work action, number of work actions, actual state
preconditions and typed effects. Vitality assignments keep the ordinary downed/recovery timestamp
consistent; they do not infer combat loot or a killer. Effects reuse the concern projection validator.
They may change response state, vitality, knowledge, inventory or relationship;
they cannot teleport people. Changing existing deliberation world-state coordinates
requires an explicit authorization gate; proposal decision records cannot be assigned. These are authored causal
rules, not automatic physical or social consequence inference. Optional
`authorization: {proposal: main, status: approved}` gates implementation on an
actual deliberation decision. Approval alone never applies these effects.

Candidate planning is bounded to a registered local operator with a shortest
route to its site (one adjacent move per actor turn), followed by its work steps.
The `reform_implementation` example continues a real deliberation through approval
and subsequent local implementation, retaining the old qualification until work
completes. No arbitrary operator composition or natural-language planning is asserted.
Absent commitments, untrusted/stale information, missing evidence, unavailable
verbs, unreachable locations and impossible deadlines exclude a response.
Required conditions affected by the forecast cannot be false or unknown.
Harming an affected value with equal or higher priority also excludes it;
lower priority optional harm reduces its weight. Highest eligible concern
priority wins between response operators. Remaining weights consider confidence,
travel/work time, diligence and ongoing-plan continuity. Stubbornness already
affects existing belief adoption; social temperament affects requests. No new
genes or duplicate personal goal/compromise parameters are added.

Normal ordinary choices and existing Policy reweighting still run, so the
selected response is not guaranteed on every step. Movement topology is treated
as the existing public route model. At execution, location, capabilities,
authorization, evidence, resources and actual preconditions are rechecked.
Effects occur only after the declared work is done. Precondition failures have
no predicted effect. Required inventory is a reusable minimum, not consumed;
consumption, if needed, is an explicit inventory effect. Stamina is charged for
each work step. Current physical preconditions outside the actor's observable
locations are not used to filter their candidates: a mistaken plan can fail.

## Requests and actual outcomes

Directed channels have a minimum one-step delay. A request transmits the
sender's dated report, not hidden truth. A receiver's existing relationship
trust weights evidence, using the ordinary share-knowledge convention. This is
a provisional compatibility choice, not a separate calibrated credibility
model. The receiver evaluates its own commitments and competing higher priority
responses, and can refuse. A pending request does not move the recipient.
Acceptance, physical execution and completion are separate records. Replies
and completion notices require a return channel and incur its delay. Sender
candidate generation never examines private receiver goals, stamina or position.
Identical requests based on the same observation are not sent repeatedly.

Environment events are explicit logical-step typed assignments, optionally
prevented by an actual state conjunction. They run before choices at that step.
A deadline is inclusive for work; model an ensuing impact on the following
step. This environmental rule must represent the intended protection mechanism.
Returning or requesting assistance never sets the protected condition itself.
The village example models preventing damage by completing local defensive work;
it does not simulate an army, infer attacks, or implement military command.
The reform example applies a declared individual-eligibility transition; its
capable actor list provisionally provides authority. Add an authorization gate
when formal proposal approval is required.

Temporal windows are either continuous maintenance or achievement by an inclusive
deadline. Actual condition history is sampled before choices, after each action,
and after normal automatic effects. A continuous violation remains a violation
after repair. Deadline achievement remains recorded after a later loss.
Current state, temporal history and actor perception are distinct. No hidden
temporal truth is fed back into choice. Windows are explicit, fixed intervals;
dynamic groups and infinitely continuous time are outside this version.

## Configuration and evidence

The shared advanced world workspace has a structured response editor at
`#responses`; its validation/save operations validate actual people and the
whole resulting world, honor revisions and atomically replace only world.yaml.
Condition editing includes response state coordinates. Explanations display
report source/time, forecast, excluded alternatives, work progress, replies and
actual outcomes separately. Ordinary snapshots retain state, active plans,
requests, queued messages, observations and temporal results.

`gapengine.response_episode.run_batch` predeclares seeds, snapshots every input,
hashes engine sources and ledgers, and retains all independent outcomes including
execution failures. It does not run evolution, a model, or a GPU. The scheduled
first report in the examples forces only the messenger's sharing action;
the protagonist's response is chosen by the ordinary simulation.

## Nine implementation stages

Stages 1 through 7 follow `concern-driven-actions-plan.md`. Stage 8 is actual
independent runs and counterfactual variants across the three example domains.
Stage 9 is regression, browser save/reload checks and packaged evidence. Tests
and evidence are required at every stage, not just at stage 9.

The following author decisions remain explicit provisional inputs: what state
means safety, the causal defense/repair/implementation rule, capable actors,
evidence age/threshold, absolute deadlines, directed communication delays, and
temporal observation periods. They are editable; the engine does not invent
their real-world meaning. Autonomous message routing without declared channels,
arbitrary natural-language goals, unrestricted multi-operator planning, dynamic
group conditions and calendar-mode execution remain future work.
