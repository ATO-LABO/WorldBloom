# Overview-based world objectives

The AI world draft contract now keeps each person's narrative objective as
`goal.description`. It no longer requires an item or a delivery destination.
The input name and overview remain authoritative. People, places, relations,
optional items, optional discoverable facts and an opening scene are generated
from that overview. The prompt uses a neutral schema skeleton, not a delivery
story to imitate. The fixed mock remains an explicit offline fixture.

## Goal input

New goals have exactly `description` (up to 2000 characters) and `conditions`
(zero to eight conditions). The protagonist needs a nonempty description.
Conditions use these fields:

| type | Additional fields | Current-state success condition |
| --- | --- | --- |
| reach | place | Actor is at that place |
| acquire | item | Actor possesses that item |
| deliver | item, place | Actor possesses the item at the place; no handover |
| meet | person | Both people are living and at the same place |
| befriend | person, threshold | Both people are living; both directed affinities meet the threshold (0 < value <= 1) |
| defeat | person | Other person is downed or dead |
| survive | day | Actor is living at or after the last slot of that day (1..7) |
| discover | fact | Actor knows the named Boolean fact |

All conditions must hold simultaneously and the actor must be alive or revived.
These are state conditions, not an event history or a plan. They do not encode
order, continuous survival, an earlier meeting, confession, rebuilding a town,
or any other unrepresented action. A complex objective must keep its description
and use an empty condition list rather than substitute an unrelated objective.
The UI explicitly labels an empty condition list as requiring human review.

Optional root `facts` contains zero to eight objects with exactly `name`,
`description`, and `place`. Each becomes an engine Boolean fact with an
investigation source at the defined place (one investigation). No arbitrary
predicate text, code, YAML, paths, rules, or events are accepted from the model.
Every person/place/item/fact reference is checked before publication.

The compiler generates quoted, finite predicates for the protagonist's ending.
For a narrative-only protagonist, the existing engine's required target ending
is `manual_review`, with a constant false predicate. This preserves loadability
without claiming automatic success; its automatic success score stays zero.
Other people's objectives are retained as character intent, not global endings.
Item acquisition/delivery also populate the old item fields for existing policy
compatibility. Narrative descriptions are loaded into Subject.goal and exposed
in actor context and both saved-world views.

Legacy `{target, deliver_to}` inputs remain readable. No existing world or stored
draft is migrated. New validation versions invalidate stale review revisions;
old live drafts require explicit revalidation before adoption. Adoption remains
revision-checked, explicit, new-world-only and idempotent.

## Generation and limits

For Ollama world-draft requests only, effective context and output allowances
are at least 8192 and 3000 tokens respectively, to fit the expanded contract.
Saved settings, story-generation options, provider/model selection and the
64KB response bound remain in place. The dedicated local 27B launcher uses a
900-second per-call cap and 1500-second total budget for world drafts because
CPU offloading on an 8GB GPU can exceed five minutes. This scoped runtime
profile does not rewrite saved settings or change generic generation limits.
A live draft now has two explicit stages:
one generation call and one assessment call to the same configured provider and
model, each admitted only when calls are enabled. The UI discloses a maximum of
two calls per new draft. No stage is retried automatically. The assessment uses
the remaining shared wall-time budget; an expired or uncertain result cannot be
adopted. An explicit JSON edit requires one fresh assessment; adoption itself
does not call a model. The larger context may use more memory than before.

The separate assessment checks overview fidelity and every person's description
against its finite conditions. It is a model judgment, not a proof of meaning.
Malformed, missing, negative or incomplete assessments fail closed. The receipt
is stored against the exact compiled revision and checked again on adoption.
Cancellation and concurrent edits cannot reuse a stale receipt. The original
human name and overview cannot be erased through the JSON editor.

Rejected or uncertain drafts retain their parsed proposal when available. A
derived `edit_revision` permits explicit repair, never direct adoption. The goal
editor changes descriptions and typed references using shared UI components;
advanced JSON editing remains available. Neither editing nor reopening a draft
calls a model. The footer recheck performs validation and, for the original live
mode, one new assessment. An unavailable live reviewer cannot downgrade the
record to an offline approval. Cancellation supersedes late recheck responses.
Receipts from an older alignment-check version require explicit revalidation.
The `draft` URL parameter resumes via an explicit state read, without generation.

Each person and place also has an edit action matching the normal world editor:
a native dialog with save/cancel actions. Person introduction and personality,
and place descriptions (up to 2000 characters), update the proposal and visible
content. Saving a dialog clears approval and requires revalidation; cancellation
keeps the previous proposal. Names, references, goals and relations are retained.
Read-only person/place summary tables are omitted when this editor is active so
there is only one displayed copy of each editable description.

The opening story is edited in the same shared dialog (up to 8000 characters).
It does not change initial locations or relation values. The relation section
supports each directed pair's label (120 characters), affinity (-1..1), and
awareness (0..1), plus explicit creation and confirmed removal. Self-relations
and duplicate pairs are excluded; reverse directions are never inferred or
overwritten. Cancellation leaves the proposal untouched. Text/number edits and
removals invalidate approval, and initial-goal checks still apply on recheck.

Compound goals require all stated outcomes (for example, reunion plus mutual
friendship). A guide's own arrival does not establish the traveler's arrival;
retain escorting as a narrative-only goal. For automatic evaluation, explicitly
describe arriving together and use arrival plus meeting at the destination;
that conjunction does not prove the process or act of guiding someone.
The internal `antagonist` field denotes the counterpart, which may be a friend
or cooperative guide; the assessment must judge the actual story content.

`draft_alignment.py` also rejects clear objective substitutions independently of
the model (for example mystery/discovery replaced by arrival, or reconciliation
replaced by delivery). These finite Japanese/English patterns are a supplement,
not a full natural-language parser. Complex goals can remain narrative-only.
Validation loads the actual engine state and evaluates each automatic goal at
turn 1 before accepting it. Only a full conjunction that is already true is
rejected; an initially true component of an otherwise unfinished goal is valid.

On Windows, NVIDIA XML process types distinguish desktop C+G contexts. Only the
installed Codex path, Windows Explorer path and a verified protected Windows DWM
compositor are treated as desktop contexts. Arbitrary similarly named binaries,
foreign compute processes, unknown identities and other loaded Ollama models
still block. The protected compositor check reads one PID's identity without
elevation or process control. With no approved model already resident, at least
2 GiB of free GPU memory is required. No process is killed or model unloaded by
the guard.

Structural validation and predicate tests are not evidence of model quality,
reachability, or effective agent planning. The simulation's generic action policy
has not been extended into a planner for these objectives. Model generation must
still be assessed with varied summaries when the local GPU is available.

## Validation

`test_world_draft_goals.py` covers publication/reloading, finite success and
failure states, multiple AND conditions, narrative-only persistence, unsafe and
unknown references, quoted names, and overview-based prompting.
`test_world_drafts.py` includes HTTP generation/adoption for discovery without
items. The controller tests cover new goal text, facts, and manual review labels.
Existing engine, actor-context, world-view, creation, editing, launcher and
navigation tests are included in the targeted regression run.

Items and directed routes are editable in the same shared proposal dialogs.
Items support name/initial holder edits, creation (up to 8), and confirmed
removal. Renaming updates typed/legacy objective references and route item
requirements, but never rewrites prose. Referenced items cannot be removed
until those references are explicitly changed. All objective selectors refresh.
Routes support explicit directed endpoint pairs, creation (up to 40), confirmed
removal, positive finite movement cost (up to 10000), and an optional registered
item requirement. Self/duplicate routes are excluded; return paths are separate.
The route schema accepts the two original fields unchanged, plus optional cost
and requires_item, which are preserved in compiled and adopted worlds.
All edits remain local until explicit validation, clear approval, and keep the
existing initial-goal check. Dialog cancellation and Escape preserve values.
