# Core hardening verification evidence

The package built from committed source
004dbaac8f5df82a3d9fe35f3de8a3eb322345e1 recorded **219 passing tests**, zero
failures/errors/skips, and completed its final runtime, test-input and candidate
hash checks. The exact selection is the prior 197 cases plus 19 returned-format
authority cases and three command-capacity cases. See
[the complete validation record](actual-overlay-219-validation.json) for all
unique IDs, source/runtime/test hashes and retained private evidence.

The runtime fixture contains 149 files from the captured 147-file cohort and two
new helpers. Fourteen selected files were tested; all 135 unselected originals
were preserved. The separate command-capacity cutover has not been executed; both contract
leaves remain replacements in this captured fixture.

The supervisor exit is **null**. After the runner wrote its successful receipt,
the MCP job returned “Job not found or expired”; the exact owned runner process
was absent. The inspected supervisor keeps an in-memory job map and can evict the
first completed entry at its 64-entry limit, including during a read request.
This is a source-supported explanation; map occupancy and the terminal exit code
were not observed. No exit-zero claim or full-suite retry was made. Interpreter
shutdown diagnostics remain in the retained stderr.

The initial seven-error full run, incomplete 300-second focused attempt and
accepted 18-case fixture correction remain retained. The current evidence does
not replace those records.

**Activation remains ineligible.** This verification made no Core deployment,
original-run recovery, maintenance transition or model/ERP action. Fresh release
authority, the original f33a disposition and browser/product acceptance remain
separate requirements. Python guards cover file opens and stat/lstat metadata
paths; direct listdir/scandir confinement and OS-level sandboxing are not claimed.
