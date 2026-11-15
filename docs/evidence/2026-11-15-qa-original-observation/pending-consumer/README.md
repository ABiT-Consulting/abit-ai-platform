# Pending original QA event: source and bounded log diagnosis

The existing result event remains pending in the 16:13:25 readback. The signed event route stores the event before its asynchronous handler runs. That handler must re-read Native Project authority, assignment and ToDos before recording an original response.

The 15:59:41.830 log records only `HTTPException`, 27.83 seconds after the event receipt. This is consistent with the current transport's three 8-second GET attempts and 1+2-second backoff. The log contains no event ID, HTTP status, endpoint, cause or traceback, so exact timeout attribution is not established. The first broad log window hit its cap; the source-selected second window returned two exception-class records and no original-recovery-fence record. Both limitations are retained.

## Existing consumer and same-original effects

Startup creates the existing reconciliation loop. It checks pending events and independently recovers enrolled Projects every 1800 seconds. No separate authenticated wake/recovery endpoint was identified. Reposting the same event returns its existing receipt and does not spawn a duplicate handler.

The event path checks ERP authority first. The sweeper's direct per-Project recovery may verify and save the same original provider receipt before its later fresh ERP packet/authority checks. The ordinary reader may perform another read-only observation of the already submitted original if its receipt is still absent; it never sends another model request. It does not automatically ingest the retained offline observation JSON.

A valid blocked QA result does not rerun the uncertain verification call or claim tests passed. After exact Native publication, the current result-store code automatically derives the original governed receipt and settles the same old-owner admission, without a new lease or epoch change. Actual receipt/state readback remains necessary to verify completion.

This review invoked no consumer, endpoint, provider persistence, retry, gate or lifecycle action. Existing event and original identities remain untouched.

## Separate staged policy review

The three selected exports match Git commit7db30e. Recovery adds two functions; only the router recovery branch and workflow text change elsewhere. Seven distinct retained helper cases finish with OK. This reviewer ran none. Those fixtures do not prove the real router→executor→Native/model path; the general API receipts also do not supply that proof.

The policy is a bounded new QA unit after a published empty blocked checkpoint, fresh Pending Review readiness and newer acknowledged human Retry. It preserves the old uncertain call. Current prerequisites remain absent. No policy activation or future QA execution approval is implied.
