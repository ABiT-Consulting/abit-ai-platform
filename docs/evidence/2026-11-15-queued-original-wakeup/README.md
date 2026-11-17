# Existing queued governance recovery wakeup

A completed original execution can enqueue a successor while maintenance admission is closed. The first delivery attempt then preserves that successor in `queued` with no run or submission. Recovery previously inspected published/uncertain/approval/blocked executions but did not schedule this existing queued successor; it depended on the global reconciliation fallback.

Normal authenticated recovery now wakes an existing invocation only when it is queued, has no AutomationRun and has no submission timestamp. Current Project and Task controls must be enabled. It schedules the same existing ID through the normal delivery path; atomic maintenance admission, claim, fresh native packet and submission guards remain authoritative. It does not create/rearm an invocation or resend a submitted original. Signed native Retry, Start, Resume and normal recovery events use this path.

`agentic_governance.selected.py` is the exact reviewed runtime candidate composed from the actual live router plus this finite branch. Its deployment excludes concurrent Source-only gateway endpoint and future Architect-prompt changes. The canonical product router contains those preserved Source changes plus the same recovery function.

The existing event/control API schemas, authentication and permissions are unchanged. No endpoint, timer, schema, lease operation or generic execution control is added. Deployment and native operator controls are separate authorized actions.
