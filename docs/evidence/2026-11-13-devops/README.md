# DevOps verification — 13 November 2026

The user waived hosted GitHub Actions for this release after the account billing lock prevented jobs from starting. Actual ABIT-DEV-LINUX tests and the authorized Azure DevOps route supply verification; no hosted CI success is claimed.

## Actual executed read-only checks

One new manual-only read-only definition (682, revision 1) executed exactly one release (7374), which succeeded. The existing shared release definitions were not edited or triggered. The script hash is recorded in both receipts. `inventory.sh` contains the exact fixed script: explicit host/local-Docker/container/project/site fences, isolated Python, bounded output, no source/DB/config/service mutation. The Bash task disabled profiles/rc files and supplied `/dev/null` for BASH_ENV; task and phase timeouts were two minutes with a shorter internal timeout. No service connection, access token, artifact, trigger, variable group or notification was enabled.

The current host UBUNTU-CODEX, the running erpnext-abit/backend container identity and the exact erp.abit.ai site directory were verified. Six potential site directories share the bench. This does not prove that six active sites share every installed app. No ingress label match was found, and public routing remains unverified.

Native compatibility inspection then used two distinct new manual definitions, one queue attempt each:

- Definition 683/release 7375 failed safely during guarded framework initialization with `AttributeError`, before any SQL query. The original negative receipt is preserved byte-for-byte in `compatibility-7375-negative.receipt.json`. Canary diagnosis identified an incomplete stdout sink interface and incorrect controller import in the inspection harness. This was a harness failure, not evidence of an ERP business or installation failure.
- Definition 684/revision 1/release 7376 succeeded using the separately reviewed REV2 script. `compatibility-7376.receipt.json` records a verified read-only database session, 118 allowlisted queries, zero SQL/file-write/non-DB-network guard denials, and zero model requests. The fixed script and native program are `compat-rev2-inventory.sh` and `compat-rev2-native.py`. No source, site configuration, role mapping, Task state, service or production application was changed.

The effective fresh-process File MRO was stock `File → Document → BaseDocument → object`, with no File override or extension hook. Versions were Frappe 16.17.2, ERPNext 16.16.0 and abit_ai_assistant 0.2.0. No active Project/Task/ToDo workflow was found. Task status metadata includes the QA states, but the new governance/PM-permission/SRS fields and managed Task fields are absent. All six configured-role checks and the service/bridge/control configuration checks were false. The receipt identifies the loaded package path and inspected file paths/hashes; absent files are not a claim about every possible module path or an old worker's cached imports.

Exactly TASK-2026-05811 and TASK-2026-06073 were inspected through bounded native scalar reads. Both report Task and relay status `Completed`; their audit scalar reports `Pending Review`, and the allowlisted independent-QA status/run scalars are null. This confirms those current status fields, not a complete independent QA flow or absence of historical duplicates. Descriptions, replies and comments were not read.

A successful metadata job is not a production deployment. Shared-code isolation across the six potential site directories and the public ingress route remain unverified.

## Source and release state

`devops-validation.json` records final platform code `d6d9b97498fa02ea05a59eeeef815749af7cab75`, the staged 37-file candidate and its manifest hash. The earlier 32-file candidate, original test counts and timestamped read-only health observations remain as historical evidence. Final source validation passed platform 340, ERP 246 and canonical-role 101 checks; API verification passed 601 declared operations, 13 contract tests, 39 developer API tests, 37 frontend tests and zero Svelte errors/warnings; isolated full-app API integration passed 91 checks. The runtime was not overwritten by this work. Reviewed migration/configuration, shared-code isolation, public route verification, source review/required production approval, consistent backups and verified drain remain prerequisites. Existing active/uncertain jobs must not be reset or replayed.

The real Dot relay still lacks per-task tool enforcement (#19). Controlled-model ERP canary evidence does not establish a real provider turn. `it-change-verification.json` records 27 passing IT-preparation tests and 19 passing controlled executor/integration tests, including persisted unavailable-plan checkpoints and deduplicated repair children. These tests do not install software, restart a service or extend a volume. Physical mutating executors, reviewed host profiles, trusted safety/signature readers and storage connectors remain prerequisites in #22. GitHub Actions billing follow-up #20 is waived as a release gate.

## Reproduction and disclosure

Run the exact reviewed script only through the existing authorized target after refreshing host/agent identity and scope review. Do not reuse old shared release definitions or grant additional access. The Azure definition request/auth transport is retained privately in the deployment stage; no credential or raw pipeline log is published here. Receipts contain allowlisted metadata only. `SHA256SUMS` verifies the evidence bytes.

The separately authorized shared-source inspection, definition 685/release 7377,
matched all three source hash pins and stopped at `hooks.py:AST` with
`HOOK_GLOBAL_DUPLICATE_OR_CONFLICT`. The parser rejected a repeated global or
reserved prefix. No candidate/source text was exported, no patch applied and no
guard relaxed or retry queued. Its negative receipt is preserved separately.

## Finite read-only integration proposal completed

New diagnostic definition 686/release 7378 identified only the observed duplicate:
after_migrate has two static literal assignments, at lines 12 and 47; Python's
last assignment is effective. It found no reserved _agov binding. The diagnostic
made no candidate, patch, application execution, SQL or production mutation.

Root then reviewed a distinct byte-preserving V2 proposal. It allows only that
exact duplicate; all others/dynamic hooks remain denied. Version-neutral AST
fingerprints matched local 3.12.3 and owned-canary 3.14.2 on 20 cases each. Original
and candidate bytes preserve UTF8 BOM and uniform LF/CRLF, and all complete
function signatures and insertion prefixes remain pinned to reviewed Git main.

New definition 687/revision 1/release 7379 succeeded once against the same three
7376 source hash pins. It returned bounded added-code-only context-zero diffs
and these in-memory candidate hashes:

| File | Candidate SHA256 | Original byte format retained |
| --- | --- | --- |
| hooks.py | 98cd1dcf75149bcc4b78d2969728be0bdaf57fdfe633ac1739b6f5a6d64b46f7 | LF |
| project_task_scheduler.py | 59329d50427ec5b37045fb641d4867b83c72f1a36069d6c492c08bb1a786208f | CRLF |
| scheduler.py | a7350eec5f9dbcd8165dc56f79a8a785fe4f897624332f025e16718f65f72d71 | UTF8 BOM + CRLF |

See shared-source-patch-v2-7379.receipt.json. Every original byte and
callback relative order remains intact in each proposed candidate. The earlier
scheduler.py text-parser failure was the BOM handling in the inspection harness;
original/candidate byte parsing succeeded. No live source was replaced and no
whole live source/candidate was exported. There were zero SQL/model requests,
source/config writes, guard denials, patch applications or service operations.
Azure's optional parallelExecutionType normalization to null was separately
accepted only for 687/rev1 plus its verified singleton 242 target; no definition
was edited/recreated and only one release was queued.

These are proposals rather than deployed integration. Review the exact added
code and candidate hashes, then prove effective native hook/MRO behavior and
shared image/service/site scope before any separately authorized release.
The full live source baseline is still not inventoried; Git main remains a
reference, and current configuration/default-OFF migrations are still absent.
Six potential shared site directories and public ERP ingress remain unresolved.

The exact owned instrumentation is shared-source-patch-v2-native.py and shared-source-patch-v2.sh; fixture cross-version and independent review receipts accompany it. No production application source is published here.
