# One-shot Windows HTTP progress observation

This dedicated diagnostic branch observes the original 1,363-invocation Windows
shard from run 38759786374, using fixed PR29 source 581292187f1ff76f110f17a895133a2fd1dc3274.
It is not a product change or a regression-gate replacement. Do not merge it.

Only this branch's push starts the one standard `windows-2025` job. No new PR,
artifact, Actions cache, Packages upload, custom image, larger runner, new
authentication or billing/permission change is involved. Attempt numbers above
one are skipped. Do not rerun or push another diagnostic attempt.

The isolated source copy receives observation bootstraps only. Original product
function/class ASTs, test assertions, GA 45-second and HTTP 5-second deadlines,
locking, fsync, integrity checks, publication and process-exit guarantees stay
unchanged. The observer adds a synthetic HTTP correlation header and bounded
numeric/hash metadata. Raw test output and native paths are not published.

The test tree has a maximum 28-minute outer bound within a 34-minute job bound.
The approved end-to-end collection/save/verification bound is 45 minutes from
the GitHub job start. Queue time and local preparation are recorded separately.
Cleanup observes and terminates only the owned Windows Job Object.

`public_log.py` checks metadata before emitting compressed, SHA-256-framed
ordinary logs. `selfcheck.py` exercises privacy and missing/duplicate-chunk
rejection without running product tests. Log transport completeness, producer
tail loss (including Pool `os._exit`) and test outcomes are distinct findings.
Supervisor liveness alone does not establish child progress.
