# Python support: minimum 3.12, regression on 3.12 and 3.13

The author selected Python 3.12 and 3.13 on 2026-10-15. The minimum supported
version is now 3.12. README, Japanese/English installation and troubleshooting
pages, the Studio isolated interpreter probe and its error message, CI matrices,
and determinism documentation use this policy. Historical investigation reports
still name the interpreters on which they actually ran.

Studio requires PyYAML under isolated `-I` execution as before. A 3.11 candidate
is rejected and the next PATH candidate is tested; 3.12 and 3.13 candidates are
accepted. Four tests cover the boundary, both supported versions, missing
PyYAML/broken aliases, and the error before workspace creation. No GUI or worker
is started by these tests.

## Exact determinism checks remain

The old pre-3.12 branches in two engine golden assertions and the new Melos
sequence golden were removed. Each original scenario and assertion remains;
the exact existing 3.12+ hash is unchanged. No tolerance, rounded float, changed
runtime formula, altered candidate ordering or RNG change is introduced. The
byte-pinned original NineTrial 24-choice comparison also remains mandatory.
Both 3.12 and 3.13 run the complete discovered suite on Ubuntu and Windows.
For historical byte-exact replay, also match the Python version recorded in
the original run; unsupported 3.11 had different floating-point summation bits.

## Required status checks: authorized migration applied

The owner authorized the two replacements per ruleset on 2026-10-15. Both
active rulesets were updated and read back before PR #21 was merged:

| Branch | Ruleset | Applied replacement |
| --- | --- | --- |
| main | main-pr-only, ID 24912881 | `regression (ubuntu-latest, 3.11)` -> `regression (ubuntu-latest, 3.12)`; same replacement for windows-latest |
| develop | develop-integration, ID 24965531 | `regression (ubuntu-latest, 3.11)` -> `regression (ubuntu-latest, 3.12)`; same replacement for windows-latest |

The two 3.13 checks remain. Readback preserved GitHub Actions integration ID 15368,
`strict_required_status_checks_policy=true`, `do_not_enforce_on_create=false`,
all PR/review/merge requirements, deletion/force-push protections and empty
bypass lists. The tag ruleset `release-tags-no-rewrite` (ID 25127354) was outside
this migration. Classic branch-protection GETs returned 404; repository rulesets
are the active protection mechanism, not an unprotected-branch conclusion.

Only the Ubuntu/Windows 3.11 context names changed to 3.12: two entries per
ruleset, with all other fields preserved apart from GitHub's update timestamp.
PR #21 also updated the checked-in `.github/rulesets/` JSON snapshots and the
current-check list in BRANCHING.md. This integration retains those applied
settings. New merges require Ubuntu/Windows on both Python 3.12 and 3.13,
with strict base freshness and no bypass. Main promotion remains subject to
the separate user review described in BRANCHING.md.
