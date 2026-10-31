# ABiT AI: Dev-02 to Linux

## Status — 2026-11-01 (Asia/Dubai)

**The public application remains on Dev-02. Production has not been cut over.**

| Component | Verified state |
|---|---|
| Authoritative source | C:\Code\open-webui-custom, branch abit-projects; baseline b2e892d1bf1f76a6efa645d970b38f1ec26fc7b2 |
| Application | Exact live 0.11.4 runtime and frontend preserved independently of the divergent working source |
| Data | Four SQLite stores passed integrity and every-table-count checks: 7 users, 3,186 chats, 659 folders, 27 files, 17 automations |
| Linux smoke app | Loopback 18888; health, branding, normal password login, authenticated APIs, model catalog and frontend assets checked; all 27 upload files exist |
| Background work | Automations, calendar, Telegram and chat timers suppressed only in the smoke copy; temporary test users removed |
| Models | Ollama 0.40.13; seven definitions and 19 unique blobs (17,405,491,414 bytes) transferred over authenticated TLS and verified by SHA-256 |
| Local inference | qwen3.5:2b and the configured bit-2.5:7b model generated responses on Linux |
| Catalog policy | Existing Ollama allowlist exposes bit-2.5:7b; the app returned 12 total models. The other stored models remain preserved without changing that policy |
| Cloud model | kimi-k3:cloud returned HTTP 402 on both Linux and Dev-02; this is an existing provider response |
| Browser relay | Existing Linux relay on 8810 is authenticated; original profile and matching token preserved. A bounded generation probe expired and the app relay-status probe returned HTTP 504, so end-to-end relay verification remains open |
| Service definitions | Application, dedicated tunnel, relay watchdog and Ollama units pass systemd validation; none installed or activated by this migration |

The snapshot is **online-staging-not-final-cutover**. Its counts describe that capture, not the continually changing source. Never promote the worker-disabled smoke database.

## Paths and provenance

- Permanent application root: /srv/abit-dev-linux/abit-ai
- Prepared release: releases/20261031-live-0.11.4; current points to it.
- Python environment: venv under the permanent root. The original migration venv path is a compatibility symlink.
- Private staged configuration: config. **No production data directory or final-snapshot marker has been installed.**
- Immutable transferred payload and test evidence: /srv/abit-dev-linux/abit-ai-migration-20261031/staging.
- Windows backup: C:\ProgramData\ABiT\OpenWebUIBackups\migration-linux-20261031.
- Existing relay runtime/profile: /srv/abit-dev-linux/relay-runtime.
- Ollama: permanent root /ollama. The existing cloud key pair is under its home/.ollama; the mcpdev home .ollama symlink points there. OLLAMA_MODELS points to /ollama/models. Neither service binds publicly.

The exact snapshot archive SHA-256 is ee4c91968f87aef4ac01b5e13d708a594a72bb710632ae8891d6823498634d5a.
The official Ollama amd64 archive SHA-256 is c9490e0f520be21271d969e6703f012268b1d39f8b70dd58854e5bfef7a75bb6.

Source runtime and installed dependencies differ from current source pins. requirements-live.txt captures the effective Windows application requirements; requirements-linux-lock.txt records all 266 installed Linux distributions. Use the CPU PyTorch index specified there.

Runtime edits are limited to Linux AD validation dispatch and the relay token-file environment override. Windows defaults remain intact. Existing unrelated automation changes are outside this migration commit.

## Administrative prerequisites

### Connector sudo repair — rechecked 2026-11-01 Asia/Dubai

The user authorized sudo access for the connector. The actual running MCP process still has NoNewPrivs: 1; its unit has NoNewPrivileges=yes, RestrictSUIDSGID=yes and ProtectSystem=strict. Sudo therefore cannot grant privileges from the existing connector. The normal ubuntu administrator SSH key was rejected. The Hyper-V host administrator connection works, but it does not provide a Linux guest administrator shell.

The reviewed repair-mcp-sudo.sh must run from an **existing unrestricted administrator session inside ABIT-DEV-LINUX**, after active connector jobs have finished:

```sh
sudo bash /srv/abit-dev-linux/abit-ai/deployment/repair-mcp-sudo.sh --apply
```

This grants **mcpdev passwordless sudo to root for all commands**. It keeps the connector running as mcpdev and removes the unit restrictions that block the requested sudo and host configuration writes. ProtectHome, PrivateTmp and ProtectControlGroups remain in place. The script refuses execution inside the MCP service or a shell with inherited NoNewPrivileges.

The relay, Chrome and egress processes currently share the connector cgroup. A normal service restart would stop them. The repair temporarily uses KillMode=process while restarting the connector, checks the identities of the existing detached relay/browser processes, then restores the original KillMode. Active tracked connector jobs may be cancelled by the MCP shutdown handler. The repair does not move existing children into independent services; their original restrictions and future cgroup shutdown exposure remain until the planned service adoption.

The script validates sudoers and the unit, records private administrator-only audit state under /var/lib/abit-mcp-sudo-repair-v1, and automatically restores only its managed configuration if application fails. Its two persistent managed files are:

- /etc/systemd/system/abit-dev-linux-mcp.service.d/90-abit-mcp-sudo-managed.conf
- /etc/sudoers.d/90-abit-mcp-root-managed

Explicit rollback from the same administrator console:

```sh
sudo bash /srv/abit-dev-linux/abit-ai/deployment/repair-mcp-sudo.sh --rollback
```

Rollback refuses to remove managed files that have changed. The script is staged and syntax/guard checked; **it has not been applied or validated under a guest administrator session**. After successful application, reconnect the connector and verify sudo -n id through it before performing system installation.

### Remaining prerequisites

1. AD connectivity now succeeds from Linux to 192.168.1.198 on TCP 53/88/389/445/636; LDAP and Kerberos DNS SRV lookups also succeed. This task did not change the network. Recheck these transports at cutover and verify actual online SSSD authentication.
2. SSSD and pamtester are already present, but cached identity lookup does not establish online authentication. Install the reviewed root-owned pam-abit-ai policy. Review SSSD pam_trusted_users and pam_public_domains for mcpdev and abitcons.com; preserve other domain policies.
3. Test permitted login, rejected password, denied/expired account, and unavailable AD under the actual application service identity. Missing PAM policy already returns HTTP 503. Opaque pamtester rejection returns generic HTTP 401; it is not proof of an incorrect password.
4. Install native document/media dependencies required by enabled features. ffmpeg was absent during staging; review pandoc and tesseract requirements against actual feature use.
5. Review external provider and tool connections. API provider settings remain intact, including omniroute.abit.ai. Recovery tool-server entries on Windows loopback ports 3888/3889 need explicit review before recovery use.
6. Install reviewed definitions, without enabling or starting them:

    sudo bash /srv/abit-dev-linux/abit-ai/deployment/install-services.sh

The installer validates prerequisites and refuses conflicting existing definitions. run-production.py refuses startup without the verified final-snapshot marker, existing production database and dedicated PAM policy.

## Final cutover: one writer

1. Verify AD, local inference, relay response capture, provider access and persistent service prerequisites before scheduling the final transfer. Recheck current runtime/configuration and preserve any changes since staging.
2. Block new public writes and drain active chats, relay requests, automations, calendar and Telegram work. Record in-flight work and existing startup settings.
3. Stop the specific Windows service ABiTOpenWebUI and prevent its automatic restart. Disable/stop only the dedicated ABiT-OpenWebUI-Tunnel task and verify its identified cloudflared process exited. Leave other tunnels, IIS sites and legacy relay processes untouched.
4. Verify relevant writers are stopped. Create a fresh quiesced backup of all four SQLite stores, uploads, vector data, runtime and private configuration. Record quiescence evidence, integrity, table counts and file hashes in a new manifest. The online snapshot helper is not a final-cutover workflow.
5. Transfer and verify the final snapshot into a new private location. Apply only reviewed portability changes and translate embedded upload paths while retaining originals. Install final data at the permanent root. Preserve encryption/session keys and enabled production scheduling values.
6. Only after those checks, derive config/final-snapshot.json from the verified final manifest, with snapshotType quiesced-final-cutover and recorded source-writer fencing. Never relabel the online snapshot to satisfy the startup gate.
7. Adopt the existing relay processes under the reviewed watchdog after draining work and releasing the old watchdog lock. Preserve Chrome and its authenticated profile. The new definition disables the Windows reverse bridge; confirm no remaining consumer needs it.
8. Start dependencies and application, verify the expected process, final manifest and local behavior, then start the dedicated Linux tunnel. Never run the Windows and Linux production writers/connectors concurrently. Enable persistent services after successful cutover.

## Acceptance and rollback

Verify existing sessions/AD login, chats, projects/instructions, uploads, streaming, all configured providers, a captured relay answer, a controlled stored automation result, and enabled calendar/Telegram delivery without duplicates. Check service restart/reboot recovery and authenticated browser persistence. Reconcile data against the **final** manifest.

Before Linux accepts writes, rollback may stop Linux services/tunnel and restore recorded Windows startup/routing. After Linux accepts writes, fence both hosts, preserve the new Linux data, and reconcile or reverse-migrate it before restoring Windows. Starting the old Windows database would discard later changes.

Keep credentials, databases, browser profiles, model files and backups out of Git. Commit only reviewed migration files to abit-origin.

## Primary references

- https://manpages.ubuntu.com/manpages/noble/man1/pamtester.1.html
- https://manpages.ubuntu.com/manpages/noble/man8/pam_sss.8.html
- https://docs.ollama.com/linux
- https://docs.ollama.com/faq
- https://github.com/ollama/ollama/releases/tag/v0.40.13
