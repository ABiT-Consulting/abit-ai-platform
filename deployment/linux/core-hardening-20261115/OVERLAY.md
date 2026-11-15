# Reproduce the Core source overlay

This preparation uses the exact committed runtime source and a hash-bound,
read-only inventory of the actual Linux runtime. It writes a private package
under the canonical repository's .git directory. It does not change a runtime
file, database, service, gate, browser, provider, ERP record or recovery receipt.

The code-only inventory cannot authorize activation. The lifecycle controller
still requires its separately reviewed plan, fresh authenticated administrator
browser proof, idle blocked gate, locked freshness checks, backups and exact
historical archive inputs.

## Required inputs

Use the canonical Linux checkout at /srv/abit-dev-linux/abit-ai-platform.

The reviewed source-pins JSON contains:

- source_commit: the full committed revision containing the runtime and the
  preparation tools.
- runtime_sha256: exactly the fourteen selected runtime paths and their committed
  SHA-256 values.
- additional_test_selection: any explicitly reviewed supplemental test names,
  including the retained legacy-history/preparer group when that source is selected.
- The runner additionally requires the v2 returned-format class and the three
  registered-command cases. These are never satisfied by live original data.
- historical_compatibility_pending: whether the source still lacks the final
  historical compatibility increment. A package with pending work is not final.

The live inventory must be a separately retained code-only JSON under .git with
activation_eligible=false. Its full hash is mandatory. It pins the original
147-file runtime cohort and process identities. Protected business state and
configuration bytes do not enter the source package.

## Prepare and verify

Run the committed preparer with the exact reviewed input hashes:

    python3 -B deployment/linux/core-hardening-20261115/prepare_core_overlay.py \
      --source-commit FULL_COMMIT \
      --source-pins .git/REVIEWED_SOURCE_PINS.json \
      --source-pins-sha256 SOURCE_PINS_SHA256 \
      --live-baseline .git/REVIEWED_CODE_ONLY_BASELINE.json \
      --live-baseline-sha256 BASELINE_SHA256


The package includes a separately hash-bound runtime_validation.py. Its
RuntimePython and RuntimeJSON validators enforce thirteen Python candidates and
one exact Draft 2020-12 schema; the JSON asset is not compiled as Python or added
to the application import graph. The original 147 files become 149 only through
the two new Python helpers, leaving all 135 unselected originals unchanged.

The shared replacement classifier accepts either all fourteen changes or the
exact complete developer_delivery.py/schema contract pair already at candidate
hashes. A partial pair or any unrelated no-op denies. The manifest records
replacement_count (14 or 12), existing_replacement_count (12 or 10),
unselected_original_count (135), already_current_selected_count (zero or two),
the exact already_current_selected_paths and preserved_original_count (135 or
137). The pair remains in the source snapshot, typed validation, private source
backups and pre/post freshness checks, while its runtime bytes, metadata and
Python cache receive no write or quarantine action.

The preparer prints the newly created package directory. Then run its pinned
isolated test runner:

    /srv/abit-dev-linux/abit-ai/venv/bin/python -B PACKAGE/run_overlay_tests.py

Retain the complete log and structured receipt. A failed or incomplete run does
not satisfy the controller's tested-package condition. The final package status
is updated only after actual successful isolated tests, hash stability checks and
independent review; source compilation alone does not establish functionality.

## Exact candidate adaptations

The repository contains a future repository-template feature that this increment
does not activate. The preparer removes only that feature's imports and restores
the exact currently deployed fixed-Project behavior in:

- agentic_gateway_operations._workspace and context's profile selection.
- agentic_gateway_delivery._repository_profile.

The original function AST hashes are retained. Missing, foreign and valid
template-only registrations must deny before a remote client is created.

The router's profile description is changed only to describe that selected
behavior accurately. Its functional AST must remain identical to the frozen
canonical router, retaining the actual native-control and delivery logic.

All other selected runtime behavior is copied exactly from the reviewed commit.
This includes the v2 recovery admission correction and both command-capacity
contract leaves. It does not upgrade any existing v1 capture or immutable
selection. That historical original remains an activation-sequence blocker until
Root establishes its supported disposition without editing business history.
The live result store, phase implementation, fixed policies and unselected
runtime files remain byte-identical in the fixture.

## Dependency checks

The preparer follows application imports through the supplied runtime cohort
without importing application code. It hashes preserved external module
boundaries and rejects missing new imports, imports through escaping symlinks,
the excluded project-profile template resolver, or a newly introduced external
application dependency outside the reviewed cohort. Namespace packages are
handled explicitly; a missing child cannot be mistaken for a package symbol.

The focused preparation suite uses only temporary files:

    /srv/abit-dev-linux/abit-ai/venv/bin/python -B \
      deployment/linux/core-hardening-20261115/test_prepare_core_overlay.py

The actual-overlay profile fixtures are copied from the same commit into the
temporary backend fixture and included in the combined run. The v2 test's exact
committed relay CJS detector is copied as a test-only asset, together with the
previous historical producer snapshots and archive preparer. All assets are
hashed before and after testing. The new registered-command test stays under
fixture-app/backend/tests; AGOV_FORMAT_BACKEND_FIXTURE points to that same
isolated backend, and ABIT_TEST_RECORDED_COMMAND_FILE is removed so only the
committed portable analogue is used. Module origins and source execution remain
confined to the fixture. All sockets and DNS
are denied, application source execution is confined to the fixture, and live
data paths are denied. The original service namespace test and excluded future
template-specific test remain explicitly excluded with reasons in the receipt.

## Evidence boundaries

Package manifests, code-only baselines, retained live source exports and runtime
proofs remain private preparation artifacts. Version control retains the
preparer, runner, focused fixtures and redacted validation evidence only.

The historical archive is prepared separately by its reviewed Root producer.
The lifecycle controller only verifies, preserves and privately backs up its
four exact existing inputs. Preparation does not settle or replay an original
run and does not claim first-flow closure.
