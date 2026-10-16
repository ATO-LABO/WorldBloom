# Purpose workspace fixtures

`melos-independent100-review-v1.json` is a UI compatibility fixture derived
from the historical 100 independent trials using 192 selection quantiles.
It retains every seed, outcome, physical-return/wedding classification and
evidence ID. Reasons and stories are shortened, and the private evidence link
is removed. It is not a new experiment or evidence of a nine-gene comparison.
The original input hashes are retained in `fixture_origin`.

`melos-independent100-execution-plan-v1.json` retains the historical plan's
JSON values with ordinary UTF-8/LF formatting. The receipt references the hash
of these checked-in bytes. The original plan hash is in `fixture_origin`.
Neither file requires a file outside the checkout or access to private storage.

The other `purpose_*` fixtures exercise saved-record and worker-result
contracts. Their hashes check internal consistency; they are not proof of
reader-facing quality or of broad model performance.
