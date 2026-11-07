# Agent-card Play — 2026-11-07

Release: ABIT-AGENT-CARD-PLAY-20261107-01

The live Agentic Governance map still uses the legacy cards. This scoped enhancement adds a visible Play control to each scheduled-agent card using the existing owner-scoped POST /api/v1/automations/{id}/run endpoint. It does not change schedules or enable disabled schedules. The authoritative ContextMap.svelte already contains native controls; this enhancement automatically defers to button[data-testid=agent-play] when native controls are present.

## Deployment

Source and deployment scripts are retained here. The live copy is /srv/abit-dev-linux/abit-ai/deployment/agent-card-play-20261107 on ABIT-DEV-LINUX. deploy_agent_card_play.py verifies host and identity, checks JavaScript syntax, preserves the current frontend shell, adds one deferred asset, and verifies the served file and health. No backend replacement or service restart is required. The earlier broad agentic-graph release was not activated.

## Validation

- node --check agent-card-play-20261107.js passed.
- node test-agent-card-play-20261107.cjs: 25 passed, 0 failed, 0 skipped. These are isolated DOM/API tests; they do not launch agents.
- Live browser: 41 scheduled cards, 41 Play controls, 0 duplicate controls, 0 controls outside card bounds. Both agents observed running had disabled Play controls.
- Deployment HTTP asset/hash and application health checks passed.

The button reports a request as accepted until run/chat status confirms progress. Guards prevent repeat clicks within the current page, including ambiguous POST errors. This is not a server-wide idempotency mechanism. Run history and chat tasks are reconciled for completion; channel completions remain conservatively pending when the chat completion cannot be verified. No live operational agent was manually launched during validation.

## Rollback

Remove only the script inclusion for /agent-card-play-20261107.js from the current frontend index.html, preserving any subsequent changes. The original shell is saved as index.before.html beside the live deployment script; do not blindly replace the shell with this backup after another deployment. The unused asset can remain. A future complete frontend build with native controls supersedes this enhancement.
