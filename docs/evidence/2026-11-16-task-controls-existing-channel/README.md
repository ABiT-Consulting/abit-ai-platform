# Existing ERP Task controls transport fix

This package contains one reviewed existing router replacement for the Task
sidebar. It uses the existing governed control channel. It creates no separate
runner or service and performs no model submission.

The activation controller preserves the exact 74-file Core transport Retry
cohort plus the existing legacy Task router as a 75-file baseline. It requires
all producers idle, exact Task-2026-06203 failed/original and finished/PM history,
a fresh authenticated empty Main task list, and OPEN54. It drains admission,
backs up databases, preserves all governance/provider/control tables, automation
history, QA rows and leases, installs only the existing router, and starts Main
and QA once each. Node, browser, relay, channel and configuration remain pinned.
No database restore, ERP document update, lease reset or model send is available
in this controller.

Source review and offline checks do not activate this package. The Root operator
must review the exact controller and manifest hashes before explicit activation.
The controller records attempted lifecycle calls before performing them and does
not repeat an unknown outcome.

Offline preservation checks:

```sh
python3 -B -m unittest discover -s docs/evidence/2026-11-16-task-controls-existing-channel -p test_activation.py -v
```

The actual activation initially held after both first Stop calls and the one-leaf
installation: cache backup used `os.rename` across `/srv` and `/var/lib` mount
boundaries and received EXDEV. The executed controller is retained unchanged.
`complete-existing-operation.py` is the exact reviewed continuation: it verified
the same operation, all 75 runtime files, metadata, configuration, complete
history/control/lease fingerprints and protected processes; backed up the owned
cache via exclusive copy, fsync, metadata preservation and source identity/hash
CAS before unlink; then performed only the first Start per unit and the original
OPEN55 command. It repeated no Stop, install or drain operation.

The final receipt confirms OPEN56, Main3931244/start78393289,
QA3931255/start78393304, 75 verified files, and preserved original state. No model
request, ERP write, configuration write or database restoration occurred during
this activation. The completion helper is historical evidence for this exact
operation, not a general replay command.
