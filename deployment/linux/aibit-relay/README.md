# ABiT Browser Relay — Linux deployment

Source of truth: `C:\Code\open-webui-custom` on branch `abit-projects`.
Target: `/srv/abit-dev-linux/relay-runtime` on ABIT-DEV-LINUX, as unprivileged `mcpdev`.

- Browser binary: `browser/chrome-linux64/chrome` (Chrome for Testing).
- Runtime libraries: `browser/ubuntu-libs/extracted/usr/lib/x86_64-linux-gnu`.
- JavaScript sources: `service/*.cjs`, copied from `deployment/windows/aibit-relay`.
- Launcher: `service/start-browser.sh` and `service/start-relay.sh`.
- Chrome CDP: `127.0.0.1:9228`; Linux relay: `127.0.0.1:8810`.
- Tokens, profiles and relay state are private runtime files; never commit them.

**Cutover gates — all required before replacing the Windows relay:**
1. Restore outbound AD-aware DNS resolution and HTTPS for the Linux VM.
2. Allow Chromium's *sandboxed* user-namespace execution through a reviewed, dedicated AppArmor profile or equivalent security configuration; do not use `--no-sandbox` for production.
3. Start browser and relay as supervised Linux systemd services with least privileges and restart-on-failure. The MCP runtime account cannot install system services.
4. Sign in to ChatGPT using the Linux browser's controlled UI; do not copy Windows browser cookies or authentication databases.
5. Securely transfer the existing API relay token to `secrets/relay.token` (mode 0600, never in Git), or rotate it consistently in all authorized clients.
6. Verify Linux `/health`: `ready=true`, `authenticated=true`, `target_ready=true`, and test project listing, one harmless completion, sandbox preview, no duplicate job, and completed response readback.
7. Route the Windows Open WebUI backend to the Linux relay over a private authenticated transport (for example, SSH forwarding), with the Windows service as rollback until in-flight jobs reconcile.
8. Verify `abit.ai`, port 8888 `/health` and `/api/config`, existing chats/projects, automations, models, and branding. Only then disable the old relay.

Do not restart port 8888, change Windows live data, or forward production traffic before all gates pass. The Windows Open WebUI database and automation schedules remain in place.
