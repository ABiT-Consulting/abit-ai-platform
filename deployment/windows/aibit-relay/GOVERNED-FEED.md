The installed Dot relay feed reader captures a completed `AGOV RESULT:` JSON line from the reply after the uniquely bound original message. It retains the original root and reply IDs, stops at the next user message, and checks the result's common identity against the already approved envelope and WorkPacket. The native packet digest is distinct from the saved ledger packet hash.

Governed JSON is returned unchanged. Recognizing a terminal reply does not accept its business result: the existing Core result parser validates the complete schema, architecture plan, evidence and handoffs. Invalid results retain their actual validation errors. Legacy Developer and QA terminal markers keep their existing behavior.

This changes response recognition within the existing relay handoff and read-only observation contracts. It adds no endpoint, submission, retry, capability, or privileged action. The owner/account/tab checks remain in the caller.
