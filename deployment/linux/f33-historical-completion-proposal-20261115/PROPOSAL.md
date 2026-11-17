# Proposed historical provider completion proof

**Proposal only. No executable exclusion, policy decision, live action, or new provider receipt is supplied. The reviewed evidence lacks the required authoritative upstream terminal attestation, so the proposed proof must reject this case today.**

This concerns only the original f33 Developer run identified in proof-inputs.json. Its canonical result is already published and its original maintenance admission is finished. Its provider row remains unknown. A new QA run is separately active; neither that run nor its admission may be changed to resolve the old provider row.

## Existing behavior and actual blocker

The current e7b55 ownership exception in agentic_ownership._published_delivery validates a published Developer result, exact packet/actor/event/Native ACK, completed gateway calls and AutomationRun success. external_execution can therefore ignore that unknown provider row for another run on the same Task. It does not require selected-format provenance, an authoritative upstream terminal observation, or an append-only provider-resolution decision.

The current384812 primary.claim has a different scope: a prior dispatching/unknown record for the same owner and automation vetoes a new original. It currently selects only the latest such row. A same-key original still cannot be replayed because its state can move to dispatching only from prepared. That invariant must remain intact.

Installing a parser change or calling ordinary published-result recovery does not resolve this inconsistency. recover_original returns the saved publication and does not observe or synthesize a provider receipt.

## Why a parser repair is not justified

The exact retained visible6341-byte line fails strict Python and Node JSON parsing. The syntactic defect is confined to delivery.test_runs[0].command: an analysis-only null sentinel makes the surrounding object parse, and the other fields match the later selected result. A single opening-bracket hypothesis still fails. The contract requires a string containing the exact observed command; the malformed visible span is4123 characters, while the later selected serialized string is4549 characters.

The old observation is rendered DOM, not proven raw model text. The historical format-message intent explicitly says original_visible_is_raw_model_source=false. The release receipt describes the same limitation. Guessing escaping, inserting the later corrected command, accepting an array in place of the required string, or associating the later correction root with the original would change provenance. No such normalization is proposed.

## Inputs present and the missing proof

proof-inputs.json lists the exact sources, hashes and limits. Present evidence includes:

- The exact original provider envelope and immutable packet.
- The original stored gateway scope/admission and Native start record.
- The immutable selected later-root correction and its selection audit.
- The canonical published Developer result, exact durable Native Pending Review ACK and finished maintenance admission.
- The original visible reply identity and malformed visible bytes.
- The negative10:54 original observation.
- The historical local capture-release receipt and its linked format-message intent.

The capture release records one TERM and one conditional KILL of the old local Node, then an idle replacement. It records zero ChatGPT Stop calls. That is local-process evidence. It is not evidence that the upstream model completed or accepted a Stop. Its controller hash is declared in the retained intent; this proposal does not claim to have independently re-established that entire historical rollout.

**Still missing:** a trustworthy, exact-original upstream terminal attestation. A Native ACK, completed business result, local Node exit, capability expiry, idle replacement or visible DOM line must not substitute for it. There is also no existing historical-completion record or approved new policy authorizing the proposed exclusion. The saved chat/message are absent; the proposal must preserve that absence.

## Minimal policy and record shape for review

If authoritative terminal evidence becomes available and the policy is approved, use one new typed record: `historical_provider_completion.v1`. Its meaning is “this exact old original no longer blocks a different authorized original.” It is neither a normal relay receipt nor a new business result.

The record must bind:

| Group | Required binding |
|---|---|
| Original | Invocation, stable run, owner, automation, account, Dot/job, provider key, envelope digest, immutable provider projection |
| Governed identity | Project, Task, role, actor, revision, packet digest, original gateway session/scope/admission digests |
| Historical text | Original visible-input digest and identity, selected correction file/reply digest, distinct correction root/reply, immutable selection audit |
| Published business result | Local/canonical result digest, exact event digest, durable Native ACK digest and original Native start |
| Quiescence | Original finished maintenance admission, no unfinished original gateway calls, and authoritative exact-original upstream terminal evidence with identified producer/source/receipt |
| Decision | Explicit policy version, authorized operator/decision reference, immutable proof-bundle digest, creation time and deterministic event identity |

The producer must obtain all runtime identities and business records from trusted stored originals. Caller-supplied summaries, model text, a bare terminal=true flag, arbitrary evidence paths, or a later-root response cannot confer authority. Any missing, changed, ambiguous or conflicting binding denies the operation.

The upstream terminal evidence must establish completion or a positively acknowledged Stop of this exact provider original, with verified owner/account/job and original root/reply identity. It must name the producer and actual source of that assertion. The present artifacts do not satisfy this requirement.

## Smallest implementation boundary if approved

A shared verifier should serve both ownership and primary.claim. It should read one immutable typed record, rederive the exact bindings from current trusted records and reject by default. This replaces the broad e7 published-result exception for the new policy; it does not add another weaker fallback.

A separate, authenticated administrative producer would append the record only after all checks. This is a **proposed new operation**, not an endpoint available now. It would need the supported API authorization, request/response, documentation and validation work before use. It must not be exposed as a model gateway operation or inferred from an ordinary Retry. Its only mutation would be one append-only decision event; it must not update provider status, receipt, response, original chat, result, packet, ledger history, admission or dispatch counters.

Both consumers must retain these rules:

1. The exact old key stays non-replayable. A request for that same original returns its existing unresolved record; it never transitions unknown to prepared/dispatching.
2. Only a different, otherwise authorized original can benefit from a verified historical record.
3. Every unresolved peer must be checked. Extending primary.claim must not skip one proven latest row and accidentally overlook an older unresolved row. More rows than the reviewed bound must deny.
4. Dispatching peers, ungoverned originals, changed identities, incomplete publication, unfinished calls or missing upstream terminal proof keep the veto.
5. Current authority, task locks, maintenance admission, live data and approval checks remain unchanged. A resolution record does not itself submit a successor.
6. Repeat production of identical proof is idempotent; a conflicting second decision is rejected.

The producer’s cross-database lock order and readback must be designed against the existing task/dispatch locks before implementation. No transaction or concurrency guarantee is claimed by this proposal.

## Meaningful verification required

Before implementation is considered complete, fixtures should exercise the actual consumers and producer, using isolated databases and blocked external services:

- The current retained f33 evidence, without upstream terminal proof, still denies even when every published-result check passes.
- A synthetic fully bound terminal proof can exclude only its exact old peer for a distinct authorized original, without changing any original row or reply bytes.
- Reusing the original key never dispatches, with or without a historical record.
- A verified newest peer cannot hide an older unverified peer.
- Wrong owner/account/Project/Task/role/key/scope, changed raw/correction/result/Native ACK digests, later-root substitution and unfinished work all deny.
- Exact duplicate append is idempotent; altered duplicate proof fails and preserves the original event chain.
- An unknown acknowledgment of the producer operation is read back by its deterministic identity, never replayed as a new decision.

These tests are requirements, not passing-test claims. No product implementation or exclusion has been executed.

## Legitimate operator handoff

The next technical step, after the current QA is idle and under separately reviewed exact identities, could be one supported observation of the already-existing original. It must retain the actual result privately and must not send, regenerate, resume or relabel a response. The current /dot/observe route may still reject unchanged malformed visible text; that outcome cannot be upgraded to a terminal receipt.

Alternatively, the authorized operator can obtain an official, read-only provider record/export of this original’s raw response and final state, preserving its account/job/root/reply linkage. A screenshot or the existing DOM projection alone is not the missing attestation.

If no authorized technical route supplies the required evidence, hand the exact missing-proof question to the authorized owner: whether to accept a **separate, explicitly named uncertainty-release policy** for this historical original. Such a decision would be a different authority action with its own concrete limits and review. It must not be labeled proven completion, slipped into the typed proof above, or inferred from the user’s general repair request. Until then, no historical exclusion is eligible.
