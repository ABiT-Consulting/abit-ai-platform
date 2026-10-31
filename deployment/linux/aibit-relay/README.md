# ABiT ChatGPT Browser Relay — Linux Production

Canonical source: `C:\Code\open-webui-custom`, branch `abit-projects`, ABiT remote `abit-origin`.

## Runtime and boundaries

- Linux VM: ABIT-DEV-LINUX (`192.168.253.20`), unprivileged `mcpdev`.
- Runtime: `/srv/abit-dev-linux/relay-runtime`; source scripts: `service/`.
- Chrome for Testing 156.0.8078.74 and isolated Ubuntu shared libraries under `browser/`.
- Headful Chrome: Xvfb display `:88`, AppArmor `aa-exec -p chrome`, Chrome renderer sandbox **enabled**. No `--no-sandbox`.
- Linux browser CDP `127.0.0.1:9228`; Linux Node relay `127.0.0.1:8810`.
- Isolated outbound HTTPS CONNECT proxy `127.0.0.1:8812`. It resolves DNS through explicit public resolvers without changing the VM's AD DNS, and rejects internal IP destinations and non-443 CONNECT requests. The proxy has no external listener.
- Dedicated restricted SSH *reverse* tunnel: Linux `mcpdev` dials Dev-02 `192.168.1.100:2226`, Windows non-admin `abit_relay_tunnel`, pinned host key, key-only auth, no shell; forwards Linux `127.0.0.1:8810` to Dev-02 **loopback-only** `127.0.0.1:8819`.
- The Open WebUI backend on Dev-02 defaults `ABIT_CHATGPT_RELAY_URL` to `http://127.0.0.1:8819`. No frontend or IIS website modifications.
- Legacy Windows relay `127.0.0.1:8808` stays running and is the rollback option. Other applications must not be silently redirected.

## Processes and startup

All five processes and the watchdog run as unprivileged `mcpdev`, with process IDs and logs under `state/`.

```sh
# Starts missing components in dependency order, preserving existing processes:
nohup setsid /srv/abit-dev-linux/relay-runtime/service/watchdog.sh \
  >/srv/abit-dev-linux/relay-runtime/state/watchdog.log 2>&1 </dev/null &
```

The watchdog checks/restarts `egress`, `xvfb`, `chrome`, `relay`, and `tunnel` when their processes exit, without restarting healthy jobs. These processes currently survive SSH/MCP disconnection.

**Reboot persistence is still pending**: `mcpdev` has no sudo and `systemctl --user` is unavailable. An authorized Linux administrator must install and enable the reviewed `aibit-linux-relay-watchdog.service` system unit; do not bypass host access controls or weaken the Chrome sandbox. The Windows `ABiTLinuxRelaySSH` service already autostarts. Until the Linux unit is installed, a Linux VM reboot requires manually starting `watchdog.sh`.

## Secrets and operational safety

Private Chrome profile, relay token, and SSH private key remain under `/srv/abit-dev-linux/relay-runtime/state` and `secrets` (mode 0600/0700); never commit or print these. Authenticated browser cookies and the relay token were transferred using an ephemeral encrypted envelope; the one-time private key and transfer files were destroyed. The one-time session-transfer script is not part of the deployed production startup or committed source.

Windows SSH installation scripts: `deployment/windows/aibit-relay/install-linux-reverse-tunnel-user.ps1` and `install-linux-tunnel-sshd.ps1`. The isolated Windows listener is deliberately separate from the existing port 22 SSHD.

## Health and validation

From Linux:

```sh
curl -fsS http://127.0.0.1:8810/health
ss -lnt | grep -E ':(8810|8812|9228) '
```

From Dev-02:

```powershell
(Invoke-WebRequest http://127.0.0.1:8819/health -UseBasicParsing).Content
Invoke-WebRequest http://127.0.0.1:8888/health -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:8888/api/config -UseBasicParsing
```

Require Linux `ready=true`, `authenticated=true`, `target_ready=true`, and successful account identity and project catalog verification. A verified catalog currently contains 131 projects. Check that Open WebUI, existing chats, projects, users, models, and scheduled automations continue to function. ChatGPT browser generation can exceed its response window; transport health alone does **not** establish successful automation outputs.

## Rollback

A protected pre-cutover backup was created on Dev-02 at `C:\ProgramData\ABiT\OpenWebUIBackups\relay-linux-20261031-181609`: prior single backend module, exact live SQLite and WAL/SHM copies, and consistent SQLite snapshot (quick_check=ok). Its path is also recorded in `C:\ProgramData\ABiT\ChatRelayRuntime\linux-cutover-backup-path.txt`.

If route rollback is needed, restore **only** `chatgpt_relay.py` from that backup to `C:\Program Files\Python311\Lib\site-packages\open_webui\routers\chatgpt_relay.py`, restart only the `ABiTOpenWebUI` service in a controlled manner, and verify 8888 `/health` and `/api/config`. The original Python module points to Windows port 8808. Do not restore older database files over live business data unless explicitly recovering from data corruption.
