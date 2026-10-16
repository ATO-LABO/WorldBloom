# Time, policy and review integration

This change depends on the fact-preserving generation implementation in
PR #21. It adds shared time/action execution, actor-scoped decision evidence,
communication completion and repeat control, and separate creation/review
workspaces. Independent trials bind their declared seeds, providers, inputs
and stopping rules to saved records, retaining failed or unfinished trials.
The opt-in Jev evidence boundary covers actor-context rendering. Legacy
candidate existence and base weights keep their previous behavior; this is
not a proof that the legacy candidate path is free of objective-state inputs.

The explicit neutral `melos_approved_ninegene_v1` profile uses the normal `Genome` and
`Policy` for Melos and fixed base weights for the other five actors. Its CFG
mapping and scenario rules are versioned verification assumptions, not a
general scene classifier. Physical court arrival, the fixed report, and the
completed wedding task are distinct observations. The older 192-quantile
baseline is a separate historical contract.

## Offline verification

Use the repository's existing Python dependencies and Node.js. Keep generated
outputs outside the checkout.

```sh
node scripts/test_javascript.cjs
python -B -m unittest discover -s tests -v
```

The JavaScript runner executes all 18 checked-in scripts, including the
explicit input argument required by the raw-record workspace test. Its purpose
workspace fixtures live under `tests/fixtures`; no parent-directory JSON files
or private Drive access are required.

For the approved Melos factory/equivalence tests, set
`MELOS_APPROVED_ADAPTER_DIR` to `<checkout>/execution/approved_melos_sources`
and `MELOS_INPUT_DIR` to `<checkout>/projects/melos-input`. CI supplies both.
The approved source and YAML catalogs are checked by received byte hashes.
The `.gitattributes` exceptions preserve the YAML's approved CRLF bytes on
Windows and Linux; normalizing them would invalidate the profile.

Before running a full suite on a personal computer, isolate model service and
process ownership boundaries. Four existing `test_local_status_api` cases
use real default service/lease state unless fixture-isolated: GET without a
client header, GET without a job store/settings, preload dispatch, and the
unload no-op case. Do not use them against unrelated running services. Hosted
CI is disposable and has no user model services or lease metadata.

## Evidence scope

The frozen cloud source passed a bounded 326-case Python selection and eight
JavaScript scripts. A later safety-bounded Linux run passed 2,732 Python cases,
skipped 22 Windows-only cases, and excluded five cases: four service/process
boundary cases plus one historical Git-object check. All 17 JavaScript scripts
passed. A separate historical-fixture substitution passed, but did not turn
the excluded original Git integration test into a pass.

The earlier local frozen-v3 full run failed; it is separate evidence and is
not a pass for this source. The PR records new local and CI results separately.

## Remaining limits

Internal minute-profile checkpoint resume requires fresh authorization from
the outer owner. Matching saved ownership alone is insufficient. A public
resume button/API and full slot/festival episode resume are not connected.
An actual local browser smoke check covers explicit profile saving, duplicate
seed rejection, CPU trials for seeds 0 and 63, mode changes and reload without
duplicate execution, saved-result copying, and cancellation with partial
results retained. The two completed trials report two physical arrivals, two
completed reports and one completed wedding; these observations are distinct.
Profile selection now persists the common world input and its creation link,
and cancellation text distinguishes a pending stop from confirmed termination.
Comprehensive browser layout and accessibility, real-model narration,
information leakage in free prose, reader quality and sensitivity across a
population remain unverified. Hashes establish consistency, not authenticity against an attacker
who can rewrite all related artifacts. The separate Sites viewer is outside
this repository change.
