# World-scoped review navigation

The home purpose choice opens `/worlds?mode=review`. Opening a world from this list enters `/worlds/<id>?view=review`; the ordinary world overview also links to the review. Production configuration and candidate comparison retain their existing routes. `/modes` is retired and redirects to world selection, retaining an explicit valid purpose.

Review rendering is read-only. A trial starts only through the explicit start button and the existing independent-job API. The world is fixed by the route; available configurations and recovered pending requests must belong to that world. Cancellation, idempotent retries, integrity verification and immutable receipts use the existing implementation.

Work notes use a world-specific browser-storage key, with session storage as a fallback. Matching legacy notes can be read without changing execution settings. Overview and review share the same note. Legacy pending requests migrate only when their configuration belongs to the selected world; the legacy storage is retained.

Receipt history remains in the existing session store, filtered for the current world without removing other worlds' records. Legacy world names are aliases only when unique. Imports for a different identified world are rejected. Common RunRecords lack world identity, so they remain readable with an explicit warning and are not treated as this world's independent-trial aggregate.

The real `/outputs` screening navigation already links to `/stories` for story comparison and evaluation. FlowStudio records that connection, including the route onward to batch comparison.
