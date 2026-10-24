# ABiT Telegram bridge for abitdot

## Purpose

The Telegram bot receives private text messages from a Telegram account linked to an authenticated ABiT Inhouse AI user. The application delivers each incoming message to a ChatGPT dot through an authenticated MCP Events subscription. The dot returns its answer using the bridge's scoped reply tool. The bridge sends that answer to the same linked Telegram account and keeps the exchange visible in the application.

This integration does not create a replacement assistant or route messages to a generic model. Configure the event subscription from the actual **abitdot** conversation.

## Setup after deployment

### 1. Configure the bot

An ABiT administrator opens **abitdot · Telegram** in the sidebar.

1. Enter the bot token obtained from Telegram BotFather into the password field.
2. Enter the public HTTPS origin that reaches **ABiT Inhouse AI on port 8888**.
3. Save the configuration. The backend verifies the token with Telegram and registers a webhook.
4. If the bot already points at a different webhook, explicitly choose to move it before saving again.

Use the Inhouse AI origin. The separate IIS website on port 8091 does not serve this feature.

The BotFather token is stored encrypted using the persistent application secret. It is never returned by status endpoints, included in a QR code, or stored in source control. Telegram request/response bodies are redacted in detailed audit logs; audit metadata is retained. Keep the existing application secret when deploying or restarting.

### 2. Link a Telegram account

1. Sign in to the ABiT account that should own the conversation.
2. Select **Generate QR code** on the Telegram page.
3. Scan the code with a phone and tap **Start** in Telegram.
4. Return to ABiT, verify the Telegram identity shown, and confirm the link.

Codes expire after five minutes. A code can be claimed only once. A replacement code invalidates an earlier one. Scanning a code does not grant access until the signed-in application user confirms it.

Only private, non-bot Telegram messages are accepted. One active Telegram identity can belong to only one application user for the configured bot.

### 3. Connect the real abitdot

1. Copy the MCP server URL shown on the Telegram page.
2. In ChatGPT, open **Plugins → + → Add custom MCP server**.
3. Enter a name such as **ABiT Telegram Bridge**, enter that URL, and choose OAuth authentication.
4. Complete the ABiT sign-in and consent flow. The grant permits reading this user's bridge messages and replying to them.
5. Install/enable the resulting plugin for the account that owns abitdot.
6. Open the actual **abitdot** conversation and send the setup prompt displayed in ABiT.

The prompt asks abitdot to subscribe to `telegram.message_received` for the connected account and answer using `reply_to_telegram_message`. Existing dot permissions and approval requirements still apply.

An active subscription proves a working subscribed callback, not the identity or name of the dot. Complete a real Telegram → abitdot → Telegram exchange to verify the chosen dot. A subscription made in another Work chat delivers events there instead.

No plugin creation or subscription happens merely by saving the bot token or scanning the QR code.

## Behavior

- Incoming updates and replies are durable and scoped to their owning application user.
- Duplicate Telegram updates do not repeat a message or action.
- The dot receives event data, including the application message ID, and can read recent bridge messages for context.
- The reply tool derives its Telegram destination from that saved message. It cannot select an arbitrary user or chat.
- Identical repeated reply calls return the existing state; a conflicting second reply is rejected.
- Text replies longer than Telegram's limit are split into ordered parts and sent as plain text.
- Telegram 429 responses are retried after the required delay.
- A timeout or interrupted send may have succeeded at Telegram. Those deliveries are marked uncertain and are not automatically resent.
- Each event subscription is bound to the exact OAuth grant that created or renewed it. Revoking that grant stops events even if another grant for the same account remains valid.
- Callback retries and Telegram sends run independently, so a slow callback does not block ready replies.
- Disconnecting Telegram cancels queued replies and prevents old messages from being sent to a newly linked account.
- `/unlink` also disconnects Telegram. `/help` and `/status` explain the available controls.
- While abitdot is not subscribed, messages remain queued in ABiT; the bridge does not substitute another model.
- This release handles text. Photos, files, voice notes, groups, and unsolicited proactive messages are not part of this transport.

## Source and runtime

Source remains in `C:\Code\open-webui-custom` on `abit-projects`.

The bridge uses a separate additive SQLite ledger, `abit-telegram.sqlite`, under the existing Open WebUI data directory. It does not replace the application database or modify existing chats, projects, or providers. Back up this ledger together with existing application data once the integration is deployed.

Relevant components:

- `backend/open_webui/utils/telegram_bridge.py`: pairing, encrypted bot configuration, inbox, outbox, worker.
- `backend/open_webui/routers/telegram.py`: authenticated account/UI routes and Telegram webhook.
- `backend/open_webui/routers/telegram_oauth.py`: scoped OAuth authorization and token lifecycle.
- `backend/open_webui/routers/telegram_mcp.py`: tools, event subscriptions, signed callback delivery.
- `src/routes/(app)/dot/telegram`: setup, QR linking, transcript, and OAuth consent.
- `backend/open_webui/main.py`: route registration and worker lifecycle.

The pinned `qrcode==8.2` dependency generates QR images locally. No QR content is sent to an image service.

## Deployment procedure

Deployment must be explicitly requested under the project's operational instructions.

1. Check the exact source commit and review the changed files.
2. Back up the existing database, WAL/SHM if present, and files being replaced.
3. Preserve the running process's environment, data directory, application secret, and known restart method.
4. Install the pinned QR dependency in the intended runtime.
5. Compare changed backend files with their live counterparts and preserve valid live-only differences.
6. Rebuild the frontend from the reviewed commit with an adequate Node heap. Deploy only the required backend files and that successful frontend build.
7. Restart with the preserved environment and confirm the same persistent data directory.
8. Verify the listener, health, configuration, authentication, existing chats/projects/providers, branding, and the new Telegram UI.
9. Configure the bot and connect the plugin through the UI.
10. Run one real exchange with the intended Telegram account and abitdot before calling the integration live verified.

No live bot token, production OAuth grant, or production subscription is embedded in this change.

## Verification

Run isolated suites with the source backend on PYTHONPATH and the pinned QR dependency available:

```powershell
python scripts/test_telegram_bridge.py
python scripts/test_telegram_oauth.py
python scripts/test_telegram_mcp.py
python scripts/test_telegram_runtime_isolated.py
python scripts/test_telegram_ui_browser.py
npm run check
npm run build
```

These suites use disposable state and simulated external transports. They cover code expiry/replay, identity confirmation, account isolation, disabled accounts, update deduplication, reply idempotency, disconnects, delivery uncertainty, OAuth/PKCE, event authentication, callback validation, and subscription delivery.

Browser tests compile the actual page and API client with isolated backend responses. Such checks do not establish live Telegram or abitdot delivery.

### Validation performed on 2026-10-24

- Transport suite: 15 tests passed.
- OAuth suite: 27 tests passed.
- MCP Events suite: 30 tests passed.
- Actual-auth integration passed against a disposable application database. It exercises real WebUI JWT authentication, OAuth registration/consent/PKCE, a grant-bound subscription, incoming Telegram webhook, signed callback generation, dot reply tool, idempotent Telegram outbox delivery, revocation, account isolation, and audit redaction. Only external callback and Telegram transports are simulated.
- Browser fixture: seven checks passed using the actual Svelte page and API client, including QR claim/confirmation, expiry/cancellation, admin settings, explicit webhook replacement, non-admin restrictions, mobile layout, escaped transcript, and polling cleanup.
- Desktop/mobile visual inspection completed. At 390 px and 320 px, the Refresh button stays on one line, the QR retains a square aspect ratio, and the layout has no horizontal overflow.
- New Telegram pages and API client have zero focused diagnostics. The repository-wide type check already failed before this UI change with 7,009 errors and 198 warnings in 344 files; imported existing modules still produce 196 errors in the focused check. This change does not claim a clean repository-wide type check.
- Production build passed with an 8 GiB Node heap; the static site output includes both Telegram routes.
- No bot token has been configured on the live application and no live Telegram-to-abitdot exchange has been performed.

The initial production build exceeded Node's default heap limit; the same production build completed successfully with an 8 GiB heap. Use an adequate build heap for this repository; the build can be run after asset preparation with `node --max-old-space-size=8192 node_modules/vite/bin/vite.js build`.

## Primary protocol references

- [Telegram deep linking](https://core.telegram.org/bots/features#deep-linking)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [OpenAI MCP Events](https://developers.openai.com/plugins/build/mcp-events)
- [ChatGPT plugin OAuth authentication](https://developers.openai.com/plugins/build/auth)
- [Connect a custom MCP server](https://developers.openai.com/api/docs/guides/custom-mcp-server)
- [MCP 2.0](https://modelcontextprotocol.io/specification/2026-07-28)
