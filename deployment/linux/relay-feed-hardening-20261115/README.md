# Detector-only relay preparation

This controller prepares one Linux runtime leaf, aibit_dot_feed.cjs, from the
single shared source at deployment/windows/aibit-relay/aibit_dot_feed.cjs.
The historical directory name does not select a Windows runtime.

| Scope | Pinned SHA-256 |
| --- | --- |
| Current detector | 1ce594960acc19485d54db9d2be21e037992f7d55adacceffd1c8aab7fdf350e |
| Reviewed detector | ddf85a4edd9fc3e71ff41c28c76f670906cb4a7bf11c1185dfd0b9de07e51f53 |
| Unchanged Node main loop | b3cd84de552c487e4751986929cd0e1cb70abb0af60d366459ac791df739627d |
| Exact prepared baseline | 7eaa6db20c1c7e9ae9189fa64e3073652fcb0790e4d8a2dafbcc9b9560872531 |

Runtime target:

    /srv/abit-dev-linux/relay-runtime/service/aibit_dot_feed.cjs

The reviewed detector has 76 passing pure Node DOM tests and retained-reply
fixture evidence. Those checks cover the saved reply and reconstructed original
envelope; they do not establish full live rendered-DOM behavior. The main loop's
existing stable/streaming completion rules remain unchanged. Core continues to
own result schema, evidence, authority and publication.

## Exact preservation boundary

The frozen input is retained at:

    .git/relay-leaf-deploy-validation/frozen-settled-baseline.json

It records the complete 147-file Core cohort, 28 relay/launcher files,
configuration hashes and metadata, eight process identities, unit identity,
and the 16-target registry. Nanosecond timestamps remain Python integers from
the exact captured JSON bytes.

The controller pins Node PID 3593239/birth67645222. The existing watchdog
PID 2996798 alone may replace Node after its graceful termination. Chrome 2998209,
Main 923235, QA 923245, watchdog, Xvfb, egress and tunnel must retain their exact
identities. Main and QA were restarted by a separate operator at 08:42:52 UTC;
this controller preserves that new cohort and does not deploy a Core increment.

The exact preserved original is
agov-parent-bc60a4e72de7c6771af1cf25f317e8c19f517d36b6696ff1,
run 74685093-2067-561e-80b0-b51852f4e7ba. A separate operator resumed this
same original at 08:50:38 UTC. It remains one dispatch and now has a durable
start receipt plus a published blocked result, SHA
b185e94680c390b9c8422bfee9cb2fafbf6ab1ab6dbf946258c648e67cba8aa6.
The controller requires its exact execution_finished state, success AutomationRun,
one admitted gateway session, four completed gateway calls, one delegated Dot row,
one local result and the two finished original admission rows. Full row hashes
preserve the original packet, responses, native projection, receipts and identities.
The controller performs no acceptance, settlement, clearing, cancellation or rearming.

The actual gate must remain DRAINING epoch 20 with **zero active or unresolved
admissions, zero running AutomationRuns, and zero running/uncertain/cancel-requested
governance invocations**. The former pre-submit epoch-18 exception is removed.
Any other active work, changed gate, changed original, linked current relay work,
nonterminal ERP/development relay job or nonzero relay activity denies execution.
Historical unknown provider rows remain intact; this check does not claim to
establish the liveness of every historical provider job.

At 09:12:59 UTC the read-only capture verified all 147 Core files, 28 relay and
launcher files, protected configuration and eight processes against the preceding
baseline unchanged. Relay health reported ready/authenticated, zero active turns,
browser not busy and 16 tracked targets. Fifteen ERP relay journal files remained
terminal. Only the reviewed original/gate and current registry pins were refreshed.

The current registry is pinned byte-for-byte, with metadata and target count,
at SHA 9a8a9e68e99c942063b99042f7e1e30ae721caee17f336fa5ee43d8fa28e12ff.
Its last touch is 08:50:39 UTC. The preceding hash-only capture did not retain
registry mapping bytes, so this preparation does not claim the change was merely
a timestamp. An older retained private registry has different mappings.
The current exact mapping has its own digest in the new baseline. Source review
shows health stats do not persist the registry; scoped-turn acquire updates a
touch or creates/prunes a target. The timestamp correlates with the resumed run;
that correlation is not treated as proof of the caller that wrote the registry.

All pre-existing governed, automation and provider history must remain byte
equivalent at the logical-row level. Gate history may only append complete pairs
for the unchanged QA service's already settled historical receipt
TASK-2026-05624. Every prior gate row stays exact. Partial pairs, another receipt,
a different process or new governed admission deny completion.

## Read-only preflight and execution boundary

Run the pure guard fixtures without live state or network access:

    /srv/abit-dev-linux/abit-ai/venv/bin/python -B deployment/linux/relay-feed-hardening-20261115/test_apply_relay_feed.py

The reviewed root operator supplies the independently reviewed controller hash:

    sudo -n /srv/abit-dev-linux/abit-ai/venv/bin/python -B deployment/linux/relay-feed-hardening-20261115/apply_relay_feed.py --controller-sha256 REVIEWED_SHA256 --preflight-only

Preflight acquires the existing release lock, then the existing dispatch lock
as their established owners, using nonblocking acquisition. It performs only
read checks and prints a bounded receipt. It creates no operation directory,
backup, runtime replacement or signal. Busy locks remain a hold condition.

Execution additionally requires --execute and a new private operation path:

    /var/lib/abit-ai-erp-rollouts/relay-feed-hardening/relay-feed-<12 hex digits>

Its parent must be prestaged root:root 0700. An existing operation directory
cannot be reused. The source contains no default execution or automatic retry.

Execution first saves the reviewed baseline, original detector and metadata,
protected configurations, target registry and retained relay journals into the
private operation directory. It then creates **11 SQLite backup-API copies**
with quick_check, file hashes and fsync. Main/QA stay running. The copies are
consistent per database; they are not claimed as one cross-database transaction.
Selected business histories and all protected original rows are rechecked
around the operation.

Capacity must cover twice the estimated 11-database size plus 128 MiB, while
leaving at least 1 GiB free. Capacity is checked again before every backup. A
60-second backup deadline aborts before replacement if copying cannot complete.
No log, backup or business-data deletion is provided.

The sole source replacement preserves ownership, mode, mtime and extended
attributes. A pinned kernel pidfd receives exactly one SIGTERM after a durable
intent and fresh idle checks. The controller waits up to 55 seconds for the
existing watchdog's replacement. It never calls a launcher, unit Stop/Start,
SIGKILL, browser action, gateway action, provider message, ERP callback or
admission mutation. Chrome and Main therefore do not use this controller's
restart path. The separate Core controller's authenticated browser-task proof
requirement remains intact.

## Held outcomes and rollback

Any failed or uncertain check leaves a held receipt and retains the original
operation. A lost signal acknowledgment never triggers a second signal.
The existing watchdog may still finish its original replacement after a hold;
observe that original outcome before considering further action.

There is no automatic rollback or database restore. Private detector.before.cjs,
detector.after.cjs, detector-metadata.json and backups remain available. A
reviewed rollback must bind the then-current exact Node, recheck idleness, gate,
all preserved processes/configuration/history and the installed hash, restore
only the original detector metadata/bytes, then use a separately reviewed single
graceful Node transition. It cannot replay this controller or erase databases,
receipts, prompts, replies or admission evidence.

## Preparation evidence

Pure guard logs and read-only preflight receipts live under:

    .git/relay-leaf-deploy-validation/

An initial prepared manifest lost nanosecond precision through JavaScript
serialization; that denied preflight and its manifest are retained. The corrected
baseline uses the exact captured JSON.

The earlier read-only preflight denied on the dispatch lock held by Main 923235.
Root's subsequent 08:58:26 UTC preflight acquired both locks and correctly denied
TARGET_REGISTRY_CHANGED. Both denied receipts and the prior source/baseline are
retained. This new preparation passed 31 pure guard fixtures with socket connects
blocked and no live operations. Its read-only capture is not a lock-held execution
preflight. Independent review and a fresh successful root preflight against this
exact new source and baseline remain required. No deployment was performed here.
