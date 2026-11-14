# Existing-account ChatGPT Project and abitdot gateway

The user selected the existing ChatGPT account, its `abit.ai Inhouse ai` Project
(`g-p-6a9b28cd72f48191ab2ee2bd226d16f2`) and its existing abitdot on 14 November 2026.
Do not require a new ChatGPT account or substitute another model transport.

## Enforced boundary

Protocol v1 now names `governed_action_gateway`, with boundary
`gateway_operations_and_managed_erp`. Its explicit excluded surface is
`owner_account_plugins`. A personal dot inherits its owner's connected apps;
a browser tab or Project does not restrict all those apps. See the official
[dots security documentation](https://learn.chatgpt.com/docs/enterprise/dots-security).
The previous proposal to require an entirely restricted principal was broader
than the requested scoped credentials and has been superseded.

The real gateway enforces registered Project/Task/role/repository/environment
and operation grants. Production ERP, relay, GitHub and edge signing credentials are not included in
work packets, model prompts or gateway responses. Protected service files hold
them; deployment must verify direct ordinary-tool Unix-identity reads are denied.
The owner's existing root-administrator tools remain outside this attestation. The existing account's unrelated apps keep their existing
permissions; the proof makes no claim about their independent actions.

## Exact job binding and transport

1. The controller derives the scope from the immutable native ERP packet and
   persistent specialist registry. Preflight checks the current Project gates,
   account/abitdot/MCP connection profile and installed receiving ERP hooks.
   The native boundary RPC checks actual File MRO before filesystem effects.
2. A fresh scope proof is retained outside the model prompt. A second proof is
   requested inside the original submission slot. Both have the same exact scope
   and opaque gateway session capability, and become immutable original-run data.
3. The relay checks the selected owner/account and prepares its isolated tab.
   It awaits actual native admission immediately before Send. Failure does not
   send a model request. The accepted receipt binds the exact packet, job, run,
   session and both proof hashes. Original observation reads that same admission
   after expiry or Stop and never admits a replacement request.
4. The model receives only a narrow opaque job capability and the structured
   work packet. The existing authenticated ABIT-DEV-LINUX MCP edge supplies
   connection and conversation hashes outside tool arguments, then MAC-signs the
   exact request body and timestamp. Missing metadata or authentication denies
   the call. A model cannot select a different owner, role, repository or root.
5. The first authenticated capability use binds its MCP conversation metadata.
   This establishes possession by the admitted owner connection; it does not
   pretend the anonymous metadata is a signed ERP identity or independently
   verified browser conversation ID. Each new action checks current native
   authority, the exact original lease/fence and maintenance admission again.

A stable `call_id` identifies each logical operation. A SQLite intent precedes
I/O; repeated calls return the original result or unresolved state. They never
repeat an uncertain operation. A shared Task file lock serializes complete
operations across workers. The session has a bounded operation count.

## Repository and evidence operations

Only server-configured working copies, change paths and acceptance commands are
available. Model input cannot select an arbitrary command, URL, host or root.
Evidence is validated before creating immutable protected files. Read responses
are chunked to fit the edge envelope; full original log hashes are retained.

Tests use a sanitized copy of approved inputs, exclude Git/config/credential
paths and reject symbolic/hard links. Bubblewrap hides host files and network.
A service-delegated cgroup limits each job to 512 MiB and 64 processes; the process
budget does not depend on the unrelated host user's process count. No sudo or
arbitrary privileged command endpoint is provided. The production service must
have its reviewed delegation and existing `chatgpt` AppArmor profile configured;
absence fails closed. No global AppArmor policy is disabled.

Successful test receipts identify the actual command, exit code, original log
bytes and exact source snapshot. Developer delivery and QA must verify the same
repository, full commits and draft PR. A changed source snapshot invalidates old
successful tests. Infrastructure, production and sensitive operations continue
through fixed approved adapters and native one-use human approvals.

## Control and recovery semantics

Hold, project/PM checkbox revocation, assignment removal and Pause/Stop prevent
new action admission. An already-admitted atomic local action may finish at its
safe checkpoint; no immediate cross-host interruption halfway through filesystem
I/O is claimed. Native ERP result/evidence writes recheck receiving authority.
Original receipts remain readable after Stop without creating work.

A separate maintenance gate closes admission transactionally across scheduled,
manual, ERP and QA starts. Sealing requires zero unresolved leases in the same
transaction. Restart, lost acknowledgements and old unknown runs do not justify
blanket replay. See [maintenance operations](agentic-maintenance.md).

## Verification state

These contracts are implemented in the current source candidate. Focused tests
include actual SQLite/files, authenticated loopback HTTP/MAC transport, loaded
Frappe hooks/MRO and a real mcpdev namespace/cgroup test. Those checks do not
constitute a production deployment or a real provider Developer/QA acceptance.
The final release evidence must separately record activation, actual MCP metadata
and account binding, the original real provider turns, exact ERP readback and
zero duplicate runs. The hosted GitHub CI billing waiver remains in effect.
