# External completion of a submitted AI task

When the native Task is marked Completed or Cancelled by a non-AI author,
ordinary original-run recovery can retire its finished technical execution
without reopening or reassigning the Task. This path runs inside the existing
coordinator before the fresh work-packet authorization check. Normal recovery
continues unchanged for AI-authored and nonterminal tasks.

Retirement requires the exact original invocation, run, registered owner,
packet digest, native Task project/active-run identity, and verified saved
terminal provider receipt. Native revision and provider bytes are rechecked.
Every gateway call must have completed with a supported bounded operation;
repository tests need an actual process exit receipt. Privileged action
journals, external delivery journals, pending HTTP results, and unresolved
successor/child work deny this path and retain the original fence.

A durable internal journal coordinates three systems: exact outer AutomationRun
closure as cancelled, original governed maintenance-admission settlement, and
matching technical task-fence release. Crash recovery reuses those original
identities. Unrelated admissions and provider evidence stay unchanged.
The coordinator records external human completion explicitly and creates no
AI result, QA verdict, verification record, model submission or ERP write.

Manual completion of a managed Task does not become AI QA evidence for a
dependent task. Existing independent-work scheduling can proceed after technical
retirement; dependency verification keeps its existing proof requirements.

No public business endpoint or new scheduler is introduced.
