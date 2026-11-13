# Captured Melos plan admission

`MelosPlanBinding` admits pristine captured sessions from `MelosSessionFactory`.
This document describes the memory-only admission layer used by the native
independent-job path. Admission itself does not start execution, create a CLI or
UI route, or save a RunRecord.

## Admission contract

- The caller retains the live `CalendarCapture` and original factory. Admission
  never copies, serializes, closes or removes that ownership binding.
- Actual captured YAML bytes, sizes, role names and manifests must match the
  built-in approved Melos profile and the declared stage input catalog. A cached
  hash or self-consistent stage configuration alone does not authorize inputs.
- The captured world, definition, provenance, execution configuration, runtime,
  simulation and subject aliases must agree. The approved lifecycle and relation
  callbacks must remain bound to their own runtime/world; substituted callbacks
  are inspected rather than invoked.
- A fresh session from the same factory reconstructs the captured scenario and
  subject/world configuration. Every live world field except the capture owner
  is included in semantic comparison. Diagnostic paths are compared lexically
  and never resolved, stated or read by captured admission.
- The live trial parameters, goals, genome, contract, compiled runtime settings,
  initial state, complete provider RNG/transcript, journal and session cursors
  must match that pristine reconstruction. Output writer ownership, previously
  started native persistence and dynamic execution states are rejected.
- Installed executable sources remain checked against current source bytes;
  the plan retains the existing conservative engine/gapengine/execution closure.
  Input namespace removal/replacement does not replace captured byte authority.
- Default uncaptured admission keeps its existing file-backed constructor and
  input checks. For unchanged approved inputs, captured and uncaptured seeds
  0/63 produce equal plan/manifest semantics and can bind the same semantic plan.

## Boundaries

Callers must exclude concurrent mutation while admitting or binding. This is not
an isolation boundary against arbitrary code execution in the same Python
process. The returned plan and detached provider catalog confer no output-parent
ownership, durable selection authority or release permission. Captured legacy
save writers remain unavailable; native persistence is a separately admitted
operation. Admission alone does not start execution or authorize resumption of a
cancelled or interrupted job.

## User-facing execution and saved results

The approved Melos profile supports saving a configuration, admitting a fixed
seed list and starting an initial independent CPU job through the existing UI
and API. The same job can be polled, cancellation can be requested, and its
retained complete or partial results can be read. Reconnecting by the retained
request/job identity resumes observing that job; it never reruns it.

Native session restoration is used between execution chunks and during fresh
result projection. This internal restoration and saved-result readback do not
provide a user-facing resume route for cancelled or interrupted Melos jobs.
Historical Melos results remain readable; they are not automatically migrated
or restarted.

The separate generic calendar CLI is explicit: use `scripts/calendar_sim.py`
with `--storage native --world WORLD_YAML --choices CHOICES_JSON
--out ABSOLUTE_OUTPUT_DIRECTORY`. Create the output directory separately, outside
the source and input trees. Native execution does not create it or scan it for a
restart head. The default remains `--storage legacy`; `--freeze-to` is a separate
legacy-mode operation and cannot be combined with native execution.

Both legacy and native CLI execution acquire choices through `capture_choices`.
Choices must be a single-linked regular file, read without following links,
at most 8 MiB, and decode as a UTF-8 JSON array. Invalid UTF-8, duplicate object
keys and the non-standard JSON constants `NaN`, `Infinity` and `-Infinity` are
rejected. This input-admission change also applies when `--storage` is omitted;
it does not switch the default storage mode or migrate legacy output files.

Generic native restore requires `--resume` together with all four independent
reference fields: `--expected-sha256`, `--expected-size`,
`--expected-stream-id` and `--expected-ordinal`. Retain the exact
`selected_generation.expected` reference separately when a confirmed selection
is returned, and supply its values explicitly. A bare or partial `--resume` is
rejected. This generic CLI restore is not a cancelled Melos job resume route.

## Native Windows publication limits

Native Windows publication is validity-based: the final name can be visible
during writing. An accepted receipt confirms verified bytes and data flush;
atomic namespace publication and namespace or power-loss durability are not
established. A file's presence alone is not a successful publication receipt.

## Verification scope

`tests/test_melos_capture_plan.py` covers pristine admission, removed/replaced
input namespaces under path-access guards, live and pre-stage tampering,
provider/journal/continuation mutations, borrowed callbacks and objects,
source/catalog tampering, closed/wrong captures and factories, semantic parity,
detached manifests and absence of run/write/borrow/publish/copy operations.
Platform-specific GUI, Windows, GPU/LLM and final release qualification remain
separate checks and are not claimed by this source-level test suite.
