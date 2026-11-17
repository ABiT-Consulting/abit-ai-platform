# Original be395 status reader

Source-only preparation. The reader has not been executed and no database was queried during preparation. Root may run the exact command below once after source review.

This is a separate stdlib reader. It reuses only the fixed original identities and the mode=ro / PRAGMA query_only / BEGIN boundary from the retained a746 observer source. It does not import or execute that controller, reuse stale status/process gates, read its auth file, or contain its HTTP, capture, Send, recovery, ownership-write or service-control actions.

The reader requires real/effective UID 999 and GID 988 and Python -I -S -B. Only three named SQLite paths under /srv/abit-dev-linux/abit-ai/data are opened: abitdot-primary.sqlite, agentic-governance.sqlite and webui.db. Each connection uses a separate read transaction, three-second busy timeout, query_only verification and total_changes==0 verification. The three DB snapshots are not cross-DB atomic. The observer has a 15-second deadline, bounded SQL row/group lengths, a 2MiB SQLite value limit and a 64KiB public output limit.

Projection: fixed-original provider/invocation/automation states, matching-identity booleans, timestamps, dispatch/fence metadata, optional task lease and gateway session, gateway call/control state counts, and local/final result presence/phase. Response, envelope, provider receipt, gateway scope/admission, result/event/ERP receipt and error fields are represented only by raw-byte SHA256, UTF-8 byte length and nullness. They are read internally to compute those fingerprints; no content is parsed into business instructions or exported. No auth file, lease token, gateway connection secret, user credential column or provider action is selected. Session/other lease-owner identifiers are hashed. Missing lease/session rows are valid observations. Missing rows or expired leases do not establish terminality, work quiescence or recovery authority.

The optional-row and grouped queries were reviewed against retained SQL and canonical schema source only. No full application module, DB initializer or migration was executed. Static AST/import/SQL review is the only preparation check; no fixture or test suite is needed or run.

## Reviewed execution command

```bash
set -euo pipefail
/usr/bin/sha256sum --check --status <<'BE395_STATUS_SOURCE'
845bfdacd130e3b2c5047b3c4d60d95917747bcd4875cf4e3313a041da655ea5  /srv/abit-dev-linux/abit-ai-platform/deployment/linux/original-be395-status-readonly-20261116-v1/read_status.py
BE395_STATUS_SOURCE
exec /usr/bin/sudo -n -u mcpdev -g mcpdev -- /usr/bin/timeout --signal=TERM --kill-after=2s 20s /usr/bin/python3 -I -S -B /srv/abit-dev-linux/abit-ai-platform/deployment/linux/original-be395-status-readonly-20261116-v1/read_status.py --read-only
```

The timeout affects this reader process only. No output file or persistent attempt state is written by the reader; Root retains its redacted stdout and actual operation result. There is no automatic retry. Schema/shape/deadline/read failure exits 2 with a fixed error code/class and no exception text or payload. A provider, invocation or automation identity mismatch is reported as identity_matches=false; it remains an observation and is not original-run acceptance. The reader UID/GID guard still fails closed. This tool does not establish current loaded source, process identity, provider terminal proof or a safe deployment gate.
