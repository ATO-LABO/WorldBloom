# Public Pages signboard: prepare, inspect, then stop

This source is a public static signboard, not the application viewer. The
existing public snapshot supplies the actual synopsis; fictional mock text is
never presented as generated output. Copy comes from the public ATOM-BOX
WorldBloom introduction. The public sample is `stories/momotaro-I-low.html`.

## Safety and layout

- `/` becomes the signboard; `/viewer/` contains the former experiment index.
- All existing `/stories/*.html` URLs remain. Only their exact `../index.html`
  list back-link becomes `../viewer/`; prose and attribution stay unchanged.
- `/docs/` and every other existing asset remain byte-for-byte unchanged.
- The builder does not invoke the destructive static exporter, delete anything,
  run git, call a model, contact a network, or publish. It requires a new output
  path and a clean public snapshot without `.git` or private files.
- Unexpected existing viewer/assets, links, inputs or output collisions fail
  closed before writes. A partial failed output is left for inspection, never
  automatically removed or overwritten.

## Prepare a candidate

1. Pin main and gh-pages SHA and confirm no competing publication is running.
2. Materialize the whole pinned gh-pages public tree into a clean directory.
   Verify each path and Git blob hash against that commit; do not use a partial
   checkout or a folder containing credentials.
3. Use a new sibling path (outside the source repository and input snapshot):

   `python -B scripts/prepare_pages.py --published ../published --out ../candidate`

   Save its JSON manifest outside the candidate. It proves the planned and
   read-back hashes, not deployment. Re-run with another new path, never erase
   an existing output merely to make the command work.
4. Run `python -B -m unittest tests.test_prepare_pages tests.test_export_static
   tests.test_ui_design`, `node tests/ui_design_contract.cjs`, and
   `node scripts/test_javascript.cjs`. Check publication guard, PC/narrow actual
   browser screenshots, no page-level overflow, focus/skip link, story/list and
   Back/Forward. Enumerate all href/src links on the candidate signboard, moved
   index and legacy stories; check local targets/fragments and public external
   destinations. Docs internals are unchanged and are not silently rewritten.
5. Inspect the diff: root index replacement; viewer index addition with relative
   story links rebased; one back-link per story; CSS addition; no other changes.

## Publication boundary

Open a draft PR for these source changes. Do not merge or write gh-pages until
owner final approval. Current docs workflow deploys only on main push/dispatch;
PR builds do not deploy. Merging this source PR changes scripts and therefore
can trigger docs publication: that merge itself belongs after final approval.
There is intentionally no new automated deploy workflow in this change.

Immediately before an approved publication, re-read main/gh-pages and all
competing docs/publication activity. If any SHA changed, rebuild and reverify
from the latest snapshot; never overwrite newer docs or force-push. Publish
only the reviewed paths via the approved repository process, verify exact
remote tree and live pages, and preserve the before tree as rollback reference.
Do not rollback by resetting all gh-pages when docs has since changed.

## Later viewer regeneration

`export_static.py --out` erases its output; it must stay a separate disposable
export path, never gh-pages, candidate root, docs, or the repository. The initial
assembler rejects an existing `viewer/` so it cannot quietly replace a migrated
site. A later regeneration needs a separate reviewed incremental procedure:
preserve the current signboard/docs and old story URLs, compare new story names
and hashes, then change only approved viewer/story paths. Do not rerun this
initial migration against an already migrated site.
