# InterMarket PR237 original release registration

The protected broker profile registers one resource at `change_resources.deploy_release.intermarket-pr237`. Its complete resource record enters the prepared plan's policy digest. The Core project gateway must enable the same resource and `execute_infrastructure` for the scoped IT manager. ERP uses the existing original IT plan binding, human approval and consumption records, and the new read-only `abit_ai_assistant.agentic_approval_actions.release_phase_decision` RPC.

| Resource field | Value or owner-supplied evidence |
| --- | --- |
| operation, resource_id | deploy_release, intermarket-pr237 |
| enabled, exclusive_to_project | true, true |
| project_id, host_id, environment | PROJ-0196, abit-dev-linux, ABIT-DEV-LINUX |
| repository | https://github.com/ABiT-Consulting/sbodesk-sap-b1-enter-INTERMARKET |
| phase_authority_protocol, host_health_protocol | original-core-release-v1, original-release-v1 |
| source | 26432b2ef62bd8e61bd1a62016a874cef0347b11 |
| manifest_sha256 | 3af33454471443a9248a9a725ef951acf5f6ff5f967e4f1ecd2a1ee6c47a53d8 |
| controller_sha256 | Reviewed installed PR237 controller bytes; source reviewed at 113caacc2194229de691eefa30af55174147c945 hashes to 0ec819f60c89e088281121bc791b73c772f59c836d64c168314ced6ccb77e3bc |
| rollback_manifest_sha256 | 88cb910f1ee35bf257cd6b5875d2cdea840c06c2000d69e32eed6c79f27e4b29 |
| prior_dll_sha256 | 1f04563aaf3ca62e6a6399c9ad65795afca0bd10be2e3ded9007cdee192a59df |
| prior_index_sha256 | e903961191e2bbf69615fdd677c6b4a683bb8b34d7d1410d65f76602b34b4456 |
| rollback_policy_sha256 | SHA-256 of the actual protected application policy after the owner records physical directory identities |
| unit, unit_path | intermarket-sbodesk.service, /etc/systemd/system/intermarket-sbodesk.service |
| unit_sha256 | 13f26cf7612daeb6e8fdacdaddd987996fb2fd36dc9163c9c889397fcf606d3b |
| recovery_proof_id, recovery_receipt_sha256, recovery_probe_policy | Original authenticated RecoveryProofStore registration for this resource policy |
| capacity_path, minimum_free_bytes | Registered capacity directory and reviewed minimum bytes |

Retain the registered host, peer, transport, backup and state roots in the existing protected profile. The independent worker requires precisely its two private journal roots plus /var/lib/intermarket-pr237-activation and /etc/systemd/system/intermarket-sbodesk.service.d as writable mount points for this resource. Core selects those four even when the profile also serves other resources. The worker uses its existing private network namespace, AF_UNIX restriction and empty capability bounding set.

The installation has three code components. The existing Core runtime owns the authenticated broker client and original governed admission. The protected broker entry.py and its manifest-verified lib/open_webui/utils modules own the physical resource, journal, worker and relay. The ERP application owns the native phase decision and binding. The protected broker profile and code-manifest locations are relative to the existing registered entry point. The reviewed entry reads code-manifest.json alongside entry.py. Include agentic_intermarket_release.py in the protected module inventory.

Place the reviewed package_controller.py and pinned deploy_successor.py filesystem primitives in /usr/libexec/abit/intermarket-pr237/. The original-job producer is /usr/libexec/abit/intermarket-original-job/restart_job.py. Package inputs, including the read-only root-owned payload and runtime mirrors, reside in /var/lib/intermarket-pr237-input. The application package's PROMPT09_REGISTRATION.md defines exact modes, digests and rollback-policy fields. Record the installed mirror identities separately from the original staging identity. The final release directory is /var/lib/intermarket-pr237-activation/intermarket-pr237-26432b2.

The broker holds the original authenticated Core session and challenges it for phase entry, each filesystem or manager checkpoint and observed evidence. The host observer samples the actual index, bootstrap, main module and application health. A verified restore yields a rolled_back gateway receipt and a blocked IT result. Missing original responses or unattributable observations preserve uncertainty and allow observation only. The durable unit fence serializes cooperating clients of this broker journal. Original systemd JobRemoved evidence and subsequent generation observations are stored separately.
