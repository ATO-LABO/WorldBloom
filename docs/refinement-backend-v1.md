# Refinement backend checkpoint (2026-11-06)

This is isolated implementation, not merged, deployed or user UI accepted.
The original resume-20261106 and saved worlds remain untouched.

## Implemented

New-world drafts only. Explicit typed operations: person.update_profile,
place.update_description, place.add, route.add, item.add, item.set_holders.
Preset catalog: enrich_people, add_place, add_item. Free input can return
multiple dependent operations. Existing goals, initial positions, commitments,
name, overview, settings and roles are protected. New-place reachability is a
static incoming-path check, not a guarantee of successful simulation.

Plans and candidates are persisted outside worlds. Same request ID/input reads
the existing result without inference. Changed input with same ID conflicts.
Cancellation discards late results. Reads and apply perform zero model calls.
Apply uses the stored candidate ID/hash, exact base revision and hash, protected
context hashes and acknowledged warning IDs. Candidate is revalidated and the
whole draft is atomically replaced. A commit marker recovers a crash between
the draft write and request receipt. No automatic rebase or retry.

Existing configured generation transport/GPU guard is reused. Maximum two
reserved calls per request (structured plan and semantic audit), no download,
model change, external paid fallback, world publication or GA start.
Both calls use response schemas. A failed request remains failed.

## API

POST /api/world-drafts/{draft}/refine
Body: revision, request_id, mode (preset/prompt), prompt, preset (null for prompt), targets.
POST /api/world-drafts/{draft}/apply-refinement
Body: revision, request_id, candidate_id, candidate_hash, acknowledged_warnings.
POST /api/world-drafts/{draft}/cancel-refinement
Body: request_id.
GET /api/world-drafts/{draft}/refinements/{request_id}
GET /api/world-drafts/{draft}/refinements/capabilities

Uses existing same-origin/client POST boundary. POST is synchronous HTTP 200
with durable state; it is not a queued 202 job. During processing GET reads the
last saved state. Missing/extra fields are refused. The approved icon/modal UI is integrated in the isolated copy; actual browser acceptance is still pending.

## Verification

32 fixed service/routing tests passed, one actual HTTP integration test passed.
Existing revision 13, goal-plan 18, creation-generation 10 passed.
An attempted test_library_pages pattern matched zero tests; it is not a pass.

Real Gemma4 e4b-it-qat isolated fixture (no production adoption):
- Initial profile: invalid operation keys, 1 reserved call, 15.93s.
- Plan-schema fix: invalid audit shape, 2 reserved calls, 6.98s.
- Both schemas: profile ready, 2 reserved calls, 6.16s.
- Cross-category first: needs clarification plus wrong operation category,
  1 call, 8.34s. Never adopted.
- Unsupported arbitrary runtime law: unsupported, 1 call, 4.37s.
- Cross-category instruction fix: dropped new place/routes. Semantic audit
  detected omission and blocked adoption, 2 calls, 7.84s.

These are distinct requests after specific changes, not automatic replay.
Raw plans/reviews are retained after their capture was added. The first two
failure records retain errors, not complete raw responses. Model digest was
not verified. Response times are samples, not performance promises.

## Earlier v01/v02 limitations (superseded where specified below)

Cross-category real-model quality is NOT accepted. It can return incomplete
plans, currently blocked by audit. Consider explicit planning/decomposition
before value generation, within a separately reserved three-call budget.
Time/schedule/action constraints, new people, obstacle conditions, imported
native worlds, rename/delete, arbitrary rules and goal changes are unsupported.
Current schema supports named references; no generalized temporary-ID mapping.
No GA/sifting/writing comparison, actual browser interaction or user acceptance.
UI icon/modal implementation is integrated, with fake-DOM state tests and shared design contract passing. No product
merge or install has been performed. The existing legacy revision API has not
been migrated to the new service. Full repository suite was not run.


## Final review fixes and manual input synchronization

Independent code review findings were fixed and checked again: confirmed-plan
validation temporarily extends only places/items catalogs without changing goals
or premises; real model output from human-origin drafts also requires semantic
audit; pre-commit and post-commit interruptions recover under the shared lock;
invalid/unknown draft edit_revision is respected; holder/holders alias writes
conflict; nested candidate storage uses the existing contained path check.

POST /api/world-drafts/{draft}/save-refinement-input takes revision and draft.
It saves human edits with zero inference, validates confirmed objectives,
preserves existing context and invalidates old semantic review. UI saves the
workspace and source revision before asking for a proposal. Failure stops AI.

See MANUAL-QA.md and qa-manual-refinement/ for later user-run desktop/narrow/
keyboard checks. QA launcher is prepared but has not been started. Existing
servers and login/model settings were not changed. No real candidate was adopted.


## v03 continuation: temporal settings and complete-request validation

Contract refinement-v2 adds time.update (days/slots), schedule.add/update
(stable event IDs and direct supported effects), and action.set_constraints
(existing enabled non-rest verb, restrict actors/slots only). Existing goals,
entries, commitments, enabled verbs, mechanics and unrelated settings remain
protected. New item/place references require operation dependencies. Conflicting
simultaneous effects, permission expansion and invalidated references are rejected.
The icon/modal now enables time/schedule and action restrictions with required
supplementary instructions and explicit limitations.

Free input uses three bounded calls: requirement decomposition, values/operations,
independent audit against the ORIGINAL user request. Each dispatch is reserved
before invocation; cancellation/replay never starts another generation. Typed
output schemas and bipartite target/relation coverage reject omissions or extras.
A valid requirement ledger is not proof of complete user-intent coverage: the
model can omit a clause during decomposition, so the independent audit is retained.

Actual local gemma4:e4b-it-qat QA (no adoption, model digest not verified):
- Explicit four-line profile/new Harbor/outward+return-route request: ready,
  three reserved calls, 15.64 seconds; receipt 0b39a938500d4fa19d4a8708d3780b88.
- Time preset, days=8 only: ready, two reserved calls, 6.33 seconds;
  receipt 9c078bd8d0ce4e0da21d30a8ed8a7496.
- Natural prose cross-category request still sometimes loses the latter clause
  during decomposition. Latest receipt 2c5772c5fcb04813be294a5d3c26ff39 was rejected
  by independent audit; no partial candidate was adopted. This quality gap is OPEN.
- Earlier distinct diagnostic attempts and their errors remain in isolated QA
  history. They are not counted as successful generations. No automatic retries.

Not implemented: forced actions, arbitrary rules, calendar migration, recurrence,
minute-level time, new people, obstacle condition changes, native-world import,
goal changes, rename/delete and generic temporary IDs. Action restrictions need
an existing explicit action policy; the tool does not invent one. No production
merge/deploy, GA/sifting/writing comparison or browser acceptance. The existing
action-constraints suite cannot run in this source-only checkpoint because its
source/projects fixtures are absent (17 FileNotFoundError), not a passing suite.


v03 final verification: 144 focused Python tests passed, followed by 7 requirement
regressions after the final merged-instruction length hardening. Seven JavaScript
suites passed (modal/footer/navigation/model/generation/three-stage/shared design).
Independent review fixes include same-setting requirement aggregation, movement
versus forced-action collision rejection, and stable merged length validation.
Full repository and missing-fixture action-constraint tests remain unverified.


## v04: lossless source-span decomposition

Observed v03 failure: the requirement response represented only the first sentence;
subsequent generation faithfully implemented that incomplete ledger. The original-
request audit rejected it. This is an observed pipeline failure, not a proven
internal model cause. Repeated blind retries were not used.

v04 mechanically splits the original input at sentence/newline boundaries, retaining
all characters and order. Each span gets a mandatory response field. More than 12
spans are concatenated into the final field without dropping text. Each response
explicitly distinguishes requested changes, preservation, unsupported effects and
necessary clarification. All non-add updates with the same kind/target are merged,
retaining every instruction, to avoid repeated writes to one description or setting.
The existing maximum-three-call budget, immutable proposals and original-full-text
semantic audit remain. Span coverage is not a guarantee of semantic completeness.

New real-model representative checks, each performed once, not adopted:
- English natural prose: profile + Harbor + two directed routes, ready, 3 calls,
  30.42s, request d89fdfc9d278499e81d63cdf64e59ff1.
- Japanese natural prose, same four changes: ready, 3 calls, 26.30s,
  request 57d32453b45d4615a4b91424f48f7691.
- Japanese single sentence, existing person + existing place descriptions:
  ready, 3 calls, 13.18s, request 6b0fbb53703a44bd8e147b78a2cab14f.
- Profile plus arbitrary flying rule: blocked needs_clarification, 2 calls,
  18.91s, request aedf0596d22b4171906d64d9c203dba5. No partial adoption.

These samples establish representative successes and a rejection, not a success
rate or guarantee on arbitrary inputs. Actual UI acceptance remains for the user.


v04 focused verification: 148 Python tests passed before the final same-entity
span aggregation fix; the final changed subset then passed 12 tests. Modal and
shared UI design contracts also passed after that fix. Broad offline JS comparison:
71 scripts passed, 18 failed in both this checkpoint and its supplied baseline;
two browser helper scripts are excluded from offline results (missing arguments,
no browser launched). No extra offline JS failure was introduced. Full Python
snapshot results are recorded separately in regression-full-evidence.


Broad Python snapshot run: 3,828 tests, 1,737 passed, 35 failure invocations,
2,002 errors, 54 skipped (315.072s). All 34 unique assertion-failing tests also
failed on the untouched supplied baseline; one appears twice in discovery.
Errors were not individually baseline-compared. At least 1,571 traces are missing
projects fixtures, plus missing ci_plan from .github/ci. Other errors include
world/config failures and require a complete matching checkout for diagnosis.
This is NOT a full-suite pass. See the regression summary and logs for details.
Real-model checks used synthetic isolated draft fixtures, never user-world adoption.


## Complete local-WIP reconstruction and full regression

The exact historical local source was recovered at worldbloom-draft-editing:
all 931 historical source hashes and all 351 protected project-file hashes match.
Local HEAD is 15f3ea9d1509738d8eec7d24fd9a6f4e084dc441. The authenticated GitHub
connector can read ATO-LABO/WorldBloom, but this local SHA is not published there
(API 422), so no unrelated newer remote files were substituted. The tracked local
archive, latest isolated source, and historical project overlay reconstruct a
1,497-file WIP. This is a verified local lineage, not a published commit identity.
Settings, credentials, live runs/control and other untracked user data were excluded.

Full reconstructed run: 3,974 tests in 1,603.769 seconds, 71 failure invocations,
1,290 errors and 54 skips. Subtests mean no naive subtraction is reported as a
passed-test count. Most errors (1,255 ERROR records; another 21 FAIL records are legacy-related) are rejection of old-format test
worlds by the current new-format-only policy. Old worlds and that policy were
not modified to force success. Other diagnostics include old checkpoint schemas,
missing cfg['project'], packaging hash discrepancies, a sibling ../base fixture
expectation, frozen closure data, and old helper use of a removed World property.
The regression is completed but NOT green, and this work is not release-certified.

Focused modules in the full run passed: three-stage integration 25, settings UI 1,
generation execution 17, draft goal plan 18, refinement requirements 12, temporal
refinement 21, durable refinements 32. Generation pages errors are old-world-policy
rejections. Offline JS remains 71 passes and the same 18 baseline failures, with
two browser helpers excluded. Actual screen acceptance still belongs to the user.

After the run, all 1,497 snapshot files were hash-verified unchanged. A temporary
PC execution disconnect occurred only after the suite; connection recovered and
post-run verification continued. See the complete regression summary, provenance,
manifest and logs included with the complete-source checkpoint.


## v06 bounded obstacles, implicit action restrictions, and manual QA

Contract is refinement-v3. Previously ready v2 candidates must be regenerated,
not applied under the expanded contract. Source changes remain isolated.

- route.set_requirement uses canonical compact JSON [from,to] as its target,
  only a pre-existing directed route, and requires_item as its only value.
  Null removal, replacement of a different gate, topology/cost alteration and
  ambiguous names are rejected. New item references require explicit dependencies.
- The obstacles preset permits gate + item additions, not arbitrary rules.
  All actors must hold the key initially or acquire it in one actual isolated
  investigate action at their unchanged entry. Entry acquisition currently rejects
  stamina-management, scheduled/advanced settings, insufficient logical intervals,
  unavailable initial-slot actions and unknown sources. Prior initially reachable
  destinations remain reachable after the witnessed acquisition. No automatic
  action choice, goal success, deadline feasibility or later key retention is proven.
  Initial possession is displayed as zero steps, not an acquisition hurdle.
- Implicit action policies are materialized from the compiled current runtime,
  including its actual mechanics. Every actor/action/slot permission is compared
  before and after materialization. Restrictions must reduce effective permission;
  enabled-action drift, mechanics drift and simultaneous slot-catalog changes fail.
- Controller's stale expected 19 PASS lines was a test harness bug. Each JS case
  now emits a machine-readable CASE_RESULT; Python verifies every declared unique
  case completed exactly once. Product controller logic is unchanged.

Manual QA server: http://127.0.0.1:5493/worlds/new?method=ai
The server binds loopback only; its control/runs/temp and settings copy are in
qa-manual-refinement. Existing port5483 service was retained. Page and refinement
JS/CSS/workflow assets returned HTTP200 and matched current source hashes.
Startup did not call a model or manipulate user screens. The copied existing
configuration selects local gemma4:e4b-it-qat with max3 calls, 180s call/300s wall.
No credentials were copied. Actual user acceptance and the latest v06 model
outputs remain unverified. Do not publish or start GA as part of screen acceptance.

Full regression remains NOT GREEN; the v05 complete-source results are historical,
not a full rerun of v06. Residual classification identifies an existing stamina/
exact-energy checkpoint mismatch, stale tracked manifest versus authorized WIP,
and fixture/API/history issues. No old-world conversion or guard relaxation.

Final fixed v06 verification: 147 Python test runs passed in 53.530s; seven offline JavaScript suites passed (shared design, footer, navigation, model, generation, three-stage, refinement). Obstacle suite independently passed 23 tests. No full-suite rerun or additional model calls.
