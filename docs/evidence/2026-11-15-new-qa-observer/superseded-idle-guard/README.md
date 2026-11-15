# One observation of the submitted QA original

Prepared for Root review and execution only. The script has not called the relay or opened a live database during preparation.

- Invocation: agov-parent-bd0c8d3ad7437b0619f6c73ee16bd95e1a7e6df747d1fea8
- Run: 3df6d316-75cb-563b-b972-73fe4cdea33a
- Saved envelope SHA-256: 86cfbcc63cf01facc545cb4573cffafc9005acc499ff51e140c3306f2f1560d4
- Historical observations: the 17:46:35 UTC Root record pins the current run, Main process and five completed gateway calls; the 17:08:18 record supplies the relay process identity. Execution must recheck every identity and saved-state predicate. These times are not a claim of present eligibility.

## Supported call

The pinned live agentic_recovery.observe_original uses the saved envelope and current existing synchronized account, calls verified_dot_payload(admission=False), POSTs once to the approved local /dot/observe route and validates the exact original receipt with receipt_from_relay. This preparation reuses those exact pure payload/scope/receipt functions through the already-reviewed AST selector, without importing an application module or calling read_original/recover_original.

The script performs one GET of relay /health and at most one POST of /dot/observe. The POST has a 35-second socket and wall deadline, inside a maximum 45-second network window including health/prechecks. No redirects, retries, Send, new admission, replacement run, control action or result persistence are allowed. It reads the existing four SQLite files with mode=ro/query_only as the existing service identity. It checks the saved admission, original running state, unchanged packet/envelope, five immutable completed calls, durable start, OPEN30 and the exact existing active admission. Any changed prerequisite holds before observation; it does not repin or manufacture a replacement.

Before the POST, it rechecks the exact Main and relay Node births/argv and 16 source pins. The fixed local token and Node owner/account configuration are read privately and never printed. Existing thread mapping must exist. The supported existing-target resolver may prune a stale mapping; the script retains private before/after bytes and reports a change without restoring it.

## Root execution

Review the frozen source and preparation manifest first. Use the existing approved administrative execution route with this exact file:

`/usr/bin/python3 -B /srv/abit-dev-linux/abit-ai-platform/.git/new-qa-original-observation-20261115/observe_original_once.py`

Do not execute as the ordinary workspace user. The script requires Root, then drops to the established service uid/gid for each read-only database connection. It creates only its new private evidence directory:

`/var/lib/abit-ai-erp-rollouts/new-qa-original-observe-3df6d316-20261115`

The directory is create-only 0700 and files are 0600. Existing output denies another attempt. Partial response bytes, exact registry bytes and any verified receipt stay private outside Git. Captured response bytes are capped at 1 MiB; an oversized/incomplete response is not accepted. No capture/token/prompt is printed. Summary UI fields are closed booleans/counts and fixed button categories, with identity hashes only.

If normal execution advances during observation, the postcheck reports that fact; it does not change state. An accepted receipt is saved only as private observation evidence. Root must separately review the supported normal original recovery path before any persistence. UI indicators alone do not prove model completion, QA acceptance or full workflow closure.

## Preparation verification

In-memory compile and pure redaction/bound checks only. No observer execution, network call, process probe, live database read or Root evidence creation occurred during preparation. The historical proof, source pins and Root-only source checks remain explicit prerequisites at execution. This is a small adaptation of the earlier reviewed f33 observer, with current QA identities and bounded safe UI projection; it introduces no product API or framework.
