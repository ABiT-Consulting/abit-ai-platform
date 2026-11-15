# Closing PM registration scope

The independent QA completed its original work and published a verified PASS. The canonical result hash is `1050b2ef3a6544795ad5ab160c447b2cb40269231e96f43582622f08677481fa`; the durable native acknowledgement hash is `3e40cd808a0366f68b5b1b79c86ca95569938bf672278c70df5fd8a4d7b09b6c`, with status **QA APPROVED**. The raw provider reply, its exact original receipt, registered acceptance command, protected evidence bytes and reviewed PR24/headcc4afa2 were independently rederived read-only. No fresh native Task or GitHub readback is claimed.

The closing PM invocation `agov-pm-review-9ac3cfd3cc1969734e889cec5e0e4c978dd19ab86738fa5c` was then blocked before any run or submission. Its exact 94-byte reason is:

> The owner relay has no verified job-bound action gateway; governed gateway rollout is required

## Reproduced cause and narrow source correction

The old `require_relay_scope` passes only the target PM registration to the strict WorkPacket validator. Closing PM packets also contain the independent QA actor. The validator therefore returns `Agent is not registered for this project` before any Dot/account lookup, credential read or HTTP request. The unchanged saved packet validates with the existing complete same-Project roster.

Only `backend/open_webui/utils/agentic_relay_scope.py` changes. It reads that server-owned Project roster once, preserves the target owner/agent/role/ERP binding checks, and supplies the full registered role map to the unchanged validator. Transport identity, declared capabilities, proof checks and external write allowlist stay bound to the target PM. There are no new imports, dependencies, endpoints or caller-selected registrations.

Canonical and observed live prechange bytes both hash to `50fc65add1abe1e5b50e1ccf3ae2bcafa33ab8ff26a39fca168ff7301be40055`. The candidate is `1907f2010399336b0ae9053f66e0ecb3f1d9a560805f7ae07d0a3cc7d83f698f`. The full candidate module was imported with exact pure live bridge/tool-gateway/validator dependencies; the actual saved PM packet passed a controlled in-memory protocol peer without new grants or packet changes. That is an import/protocol proof, not live gateway admission.

## Verification and retained corrections

- The baseline positive regression reached the actual `DotAdapter.preflight` and produced one expected `CapabilityUnavailable` error.
- The first corrected run passed 29 of 30 cases. Its only error was a new negative fixture attempting to delete an agent referenced by a foreign key before product execution.
- That fixture alone was corrected to move the referenced registration outside this Project, preserving the agent ID, and its affected case passed. Together these are **30 distinct successful cases**, not a clean single 30-case run. Product bytes did not change.
- The seven new cases cover actual PM preflight, no grant expansion, foreign/missing/unregistered QA, mismatched QA ERP binding, unchanged target checks and revocation before the next preflight. The existing 23 relay cases also passed.
- `npm run api:verify` completed with 13 + 39 Python cases, 37 frontend cases and zero Svelte errors/warnings. The isolated integration gate passed 91 cases with zero failures. Both process exits were zero and all bound sources stayed unchanged.
- The focused runner denied network, DNS, subprocesses, full application/live configuration imports and live data reads; it used private 0700 temporary fixtures. Routine asyncio slow-task diagnostics remain in observed stderr.

The first read-only QA report used a diagnostic SQLite facade that omitted `Row`. This made the separate historical f33 recognizer return false through its caught `AttributeError`. The exact trace and first report are retained. The corrected read-only facade returns **true** for f33 in state `verified`; no runtime state-transition failure is inferred. The f33 raw provider remains `unknown` with null reply/receipt/scope receipt, unchanged. Its published result is not treated as a replacement raw provider response.

The initial report's `reported_pass_counts` array is not used as a nested test count. The evidence proves one registered acceptance command and its exact immutable result/artifact, rather than inferring an additional test total.

## Remaining release and flow gates

This package is source/test evidence and an offline one-leaf candidate. It has not deployed the leaf, restarted Main, altered maintenance, rearmed PM or submitted any model turn. At the last authorized local read, PM was blocked with dispatch0/no run, no gateway session/local result/canonical result, Project enabled, no Task owner, OPEN30 and zero active/unresolved admissions. These are timestamped facts, not a perpetual release authorization.

The supported explicit recovery path is the existing router `_recover_project` branch and `agentic_unsubmitted.rearm`: it requires a fresh identical original packet, current Project/Task gates, no prior run/submission and the bounded retry count, then reacquires the same invocation fence and records its audit. Fresh native equality and gates were not queried here. Root owns deployment, fresh release proof and any later supported same-original recovery.

The retained reader files document the exact bounded observation and reproduction performed. They are not new operational APIs or general scripts to rerun against production. Private reply, account, credential, configuration and evidence bodies remain outside this package.
