# Stored gateway admission compatibility

The actual gateway session stores the original admission envelope, while scope_receipt validates the derived accepted receipt. The correction reader now requires the stored envelope to equal the original provider governance_scope exactly, hashes that unchanged envelope, and uses the existing pure gateway_sessions.receipt function before scope_receipt. It never re-admits or changes the saved record.

Six affected real-ledger cases passed against the actual stored-envelope shape. The earlier receipt-shaped fixture and its failing original guard are retained as evidence. The prompt/feed escaped-JSON test is unchanged from the prior passing proof. Public fixture IDs are opaque sanitized values; the URL alias remains distinct from the Calpico room namespace.

The exact captured correction and all protected authorization files remain private and are excluded from Git. Preparation verifies literal captured UTF-8, retained intent lineage, unchanged recorded delivery/evidence/tests, and read-only server bindings. Root owns staging and activation. All ordinary result, artifact, delivery, Native and independent QA checks remain mandatory.

Only these two selected runtime exports are deployable. Earlier exports and manifests are retained.
