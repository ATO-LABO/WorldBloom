These two inputs preserve the original historical comparison bytes.
They were checked byte-for-byte against both the private pre-rewrite backup
and the corresponding rewritten Git objects. No commit metadata is included.
manifest.json records content SHA-256 and Git blob IDs, which are unaffected
by author-email changes. Loaders reject changed bytes rather than replacing
the original baseline with a current implementation.

pre_growth_s3_qd.yaml: template before the optional growth axis was introduced.
original_raw_workspace_ui.cjs: original assertion-bearing clipboard harness.
