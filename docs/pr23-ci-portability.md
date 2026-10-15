# PR 23 CI portability and full-suite coverage

The bounded diagnostics at `e7815f4` compare the exact received
`ninegene_melos.py` (SHA256
`ad6733142979e48655f7dcaa1d389a7d3a2c9737ea1109f663ecdd6b14a9d324`)
with the provider for six actors and four decisions each, including six
singleton offers. The original `NineTrial.choose` AST and projections execute
unchanged, with the scenario constructor replaced by synthetic observable
inputs. Every calculation field, selected candidate, RNG state, count and
history agreed under the then-tested Python 3.11/3.13 pair on both operating systems.

Only 25 `normalized_weight` values differ between Python 3.11 and 3.13.
Each value exactly matches its version's `sum` behavior (left-fold addition
before 3.12, improved floating-point summation from 3.12). Raw weights,
selection, RNG and history are unchanged. The test compares the full original
reference on every execution. The subsequent author decision raises minimum
support to Python 3.12 and tests 3.12/3.13. Unsupported 3.11-specific golden
branches were removed; all scenarios, strict supported-version hashes and
reference comparisons remain. Runtime formulas, candidate order, CFG and
tolerances are unchanged. See [the migration record](python-support.md).

On hosted Windows, `TemporaryDirectory` returned a `RUNNER~1` path while the
factory resolved the same directory to `runneradmin`. Factory assertions now
use a canonical expected root and still require independent module/root
isolation and unchanged source bytes. The HTTP fixture had the same alias issue
in snapshot metadata: the initial capability used `ConfigStore.repo` (resolved)
but the frozen snapshot used the unresolved fixture root, producing a false
revision conflict (HTTP 409 instead of 202). It now snapshots the same canonical
repository and explicitly checks the engine revision and capability equality.
CSRF 403, launch 202, identical retry 200, identity and conflicting payload 409
assertions all remain.

`1593b7b` repeats only the three bounded target tests, which pass on all four
environments. These temporary diagnostic runs are not full regression evidence.
The final workflow restores complete discovery, divided into two isolated
jobs for each Windows/Linux and Python 3.12/3.13 pair. Neither shard shares a
filesystem or process with the other. Placement keeps a test module together,
uses deterministic weights from the previous Windows timings and preserves
discovery order within each shard. Newly discovered modules are automatically
included. No test is excluded. Product/lifecycle deadlines remain unchanged;
each shard retains the existing 45-minute CI job maximum.

The four existing `regression (os, python)` check names are aggregate gates.
The aggregator can run on Ubuntu while verifying evidence from the actual
requested OS/Python runners. It requires two successful shard reports for the
same head, matrix environment and actual interpreter/OS. Both must have the
same full discovery census, no import errors and every assigned invocation
completed. The multiset union of assigned and completed cases must equal full
discovery exactly. Required JavaScript and observation step outcomes are also
checked, so a Python-only success cannot hide a failed JavaScript gate.

Before the minimum-version migration, discovery contained 2764 invocations and
2762 distinct test IDs: the previous 2759 plus five census/result tests. Four
Studio interpreter-boundary tests now bring this to 2768 invocations and 2766
distinct IDs. Two duplicate
invocations were already present in the original discovery, from inherited
`EvolutionHttpPersonalityGrowthTests`; their multiplicity is preserved, not
silently deduplicated. JSON census artifacts record the actual shard sizes and totals at each head. Failures and
per-test durations are flushed immediately to `test-events.jsonl` so a later
cancellation cannot erase earlier assertion reports.

The normal local command remains `python -B -m unittest discover -s tests -v`.
Integration with PRs #21, #22, #24 and #25 discovers 2,801 invocations and
2,799 distinct IDs, partitioned into 1,378 and 1,423 invocations. Discovery has
no loader errors. These inventory numbers alone do not establish a suite pass;
the required aggregate gates verify actual execution against this census.

`tests/regression_suite_capture.py` without arguments retains its original full
suite behavior. `--shard 0` or `--shard 1` selects the CI partition and requires
`WB_REGRESSION_EVIDENCE_DIR`. The five harness tests verify missing/extra work,
incorrect heads and environments, partial/failed runs, required step failures,
preserved duplicate invocations, module isolation and immediate assertion logs.
