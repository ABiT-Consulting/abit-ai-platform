"""Send one owner-authorized ABIT.AI Dot-primary run through the existing relay.

ABIT.AI's localhost /dot/handoff API is an internal application route, not an
OpenAI public Dot API. Only a verified final reply counts as a returned Dot
turn; a browser timeout never proves submission failed or justifies replay.
There is no timer, browser-driving code, or Work-mode execution here.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from typing import Any

import aiohttp

log = logging.getLogger(__name__)
ACCOUNT_ID = re.compile(r"^[0-9a-f]{64}$")
DOT_JOB_ID = re.compile(r"^aibit_[0-9a-f]{48}$")
MAX_MESSAGE = 160000
MAX_REPLY = 250000
ALLOWED_RELAY_HOSTS = {"127.0.0.1", "::1", "localhost"}


class RelayPreflightError(Exception):
    """An owner/account/project validation failure, before any submission."""


def verified_dot_payload(envelope: dict, account_id: str) -> dict:
    """Construct the original prompt and context for the existing relay API."""
    if not isinstance(envelope, dict) or not envelope.get("execution_policy", {}).get("dot_primary"):
        raise RelayPreflightError("The request is not an owner-authorized Dot-primary run.")
    owner = envelope.get("owner_id")
    expected = envelope.get("idempotency_key")
    if not isinstance(owner, str) or not owner or not isinstance(expected, str):
        raise RelayPreflightError("The originating owner and run key are required.")
    if not ACCOUNT_ID.fullmatch(account_id or ""):
        raise RelayPreflightError("Verified ChatGPT account fingerprint missing.")
    if len(expected) != 64 or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise RelayPreflightError("Invalid idempotency digest.")
    governed = (envelope.get('task', {}).get('meta') or {}).get('governance_managed') is True
    message = (
        "abitdot — PRIMARY ABIT.AI AGENT, not fallback. You are the execution "
        "coordinator for the exact originating owner and project below. "
        "Use the related ChatGPT Project and its instructions, then select "
        "the authorized tools, models or agents needed to complete the "
        "original user prompt autonomously. Reconcile existing work and "
        "ERPNext status/owners before executing; prevent duplicate jobs, "
        "commits or postings. Do not use ChatGPT Work mode or Dot polling. "
        + ("Use only the exact governance packet scope. ERP changes, approvals, handoffs and "
           "privileged infrastructure operations are server-controlled. Do not message others "
           "without an explicit scoped ERP authorization. " if governed else
           "Escalate via Slack only for a genuine showstopper or a required approval. ")
        + "The ABIT.AI run and ERPNext task are NOT complete merely "
        "because this Dot turn replies. Return real task IDs, code/test "
        "evidence, blockers and next action. Respond only within this "
        "authenticated owner's execution scope.\n\n"
        "Original project, instructions, prompt, prior chat and exact run "
        "idempotency key (JSON):\n"
        + json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), default=str)
    )
    if len(message) > MAX_MESSAGE:
        raise RelayPreflightError("Related project context exceeds the Dot relay limit.")
    job_id = "aibit_" + hashlib.sha256(("primary:" + expected).encode()).hexdigest()[:48]
    assert DOT_JOB_ID.fullmatch(job_id)
    return {
        "owner": owner,
        "job_id": job_id,
        "account_id": account_id,
        "channel": "abitdot",
        "message": message,
        # Project is delivered IN the prompt; do not supply project_name to
        # the legacy ChatGPT Project chat branch as a navigation target.
    }


def validated_local_relay_url(url: str) -> str:
    try:
        parts = urlsplit(url.rstrip("/"))
        if (parts.scheme != "http" or parts.hostname not in ALLOWED_RELAY_HOSTS
                or parts.username or parts.password or parts.query or parts.fragment
                or parts.path not in ("", "/") or not parts.port):
            raise RelayPreflightError("Only the configured local relay is authorized.")
        return url.rstrip("/")
    except (TypeError, ValueError) as exc:
        raise RelayPreflightError("Relay endpoint configuration is invalid.") from exc


def receipt_from_relay(envelope: dict, provider_result: dict, account_id: str) -> dict:
    if (not isinstance(provider_result, dict)
            or provider_result.get("dot_handoff") is not True
            or provider_result.get("account_id") != account_id
            or not isinstance(provider_result.get("response"), str)
            or not provider_result["response"].strip()
            or len(provider_result["response"]) > MAX_REPLY):
        return {"status": "outcome_unknown"}
    url = str(provider_result.get("conversation_url") or "")
    # The response is from the verified owner-account relay, not an
    # independently authenticated Dot task-run/ERP completion receipt.
    digest = hashlib.sha256(json.dumps([
        envelope["idempotency_key"], account_id,
        provider_result["response"], url,
    ], ensure_ascii=False).encode()).hexdigest()
    return {
        "owner_id": envelope["owner_id"],
        "automation_id": envelope["automation_id"],
        "run_id": envelope["run_id"],
        "dot_id": envelope["dot_id"],
        "idempotency_key": envelope["idempotency_key"],
        "status": "accepted",
        "receipt_id": digest,
        "dot_turn_result": provider_result["response"][:MAX_REPLY],
        "dot_conversation_url": url if url.startswith("https://chatgpt.com/") else "",
    }


async def relay_account_for_owner(owner: str) -> str:
    """Read the existing verified ABIT.AI->ChatGPT account binding; never guess."""
    from open_webui.routers.chatgpt_relay import _db
    with _db() as db:
        rows = db.execute(
            "SELECT account FROM sync_state WHERE owner=? "
            "ORDER BY updated_at DESC LIMIT 2", (owner,),
        ).fetchall()
    if not rows or not ACCOUNT_ID.fullmatch(rows[0]["account"] or ""):
        raise RelayPreflightError("No verified ChatGPT account sync for this owner.")
    if len(rows) > 1 and rows[0]["account"] != rows[1]["account"]:
        # The relay validates its live fingerprint as well; ambiguous owner
        # accounts require explicit confirmation before using a personal Dot.
        raise RelayPreflightError("Multiple synced ChatGPT accounts for the owner.")
    return rows[0]["account"]


async def send_via_existing_relay(
    envelope: dict,
    *,
    base_url: str | None = None,
    token: str | None = None,
    account_id: str | None = None,
    timeout_seconds: int = 235,
    stream: bool = False,
) -> dict:
    """One direct authenticated POST; never replay on timeout or lost response.

    Injection arguments are for isolated contract tests only. In production
    the token and URL come from the existing ABIT.AI relay configuration.
    """
    from open_webui.utils.abitdot_primary import primary_dot_for_owner
    if primary_dot_for_owner(envelope.get("owner_id")) != envelope.get("dot_id"):
        raise RelayPreflightError("Private Dot owner was not verified.")
    # The existing Linux Node relay uses a different port/token file from
    # the legacy Windows ChatGPT Project relay. Both are owner-authenticated;
    # never assume the legacy browser relay's URL/credentials apply here.
    if base_url is None:
        from open_webui.routers.chatgpt_relay import MESSAGE_URL
        base_url = os.getenv("ABIT_DOT_PRIMARY_RELAY_URL") or MESSAGE_URL
    if token is None:
        from open_webui.routers.chatgpt_relay import MESSAGE_TOKEN_FILE, _credentials
        token_path = os.getenv("ABIT_DOT_PRIMARY_RELAY_TOKEN_FILE")
        if token_path:
            authorized_path = Path("/srv/abit-dev-linux/relay-runtime/secrets/relay.token").resolve()
            try:
                selected_path = Path(token_path).resolve(strict=True)
            except OSError as exc:
                raise RelayPreflightError("Authorized local relay token file missing.") from exc
            if selected_path != authorized_path or not selected_path.is_file():
                raise RelayPreflightError("Dot primary relay token file is outside the approved Linux secret.")
        else:
            selected_path = MESSAGE_TOKEN_FILE
        token, _ = _credentials(selected_path)
    validated = validated_local_relay_url(base_url)
    if not isinstance(token, str) or not token:
        raise RelayPreflightError("The ABIT.AI relay credentials are unavailable.")
    if account_id is None:
        account_id = await relay_account_for_owner(envelope["owner_id"])
    payload = verified_dot_payload(envelope, account_id)
    if stream:
        payload["stream"] = True
    # No forwarding of private project context to an arbitrary host: local only.
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout_seconds),
            trust_env=False,
        ) as client:
            async with client.post(
                validated + "/dot/handoff",
                json=payload,
                headers={"Authorization": "Bearer " + token},
                allow_redirects=False,
            ) as response:
                if response.status == 200:
                    if stream and 'application/x-ndjson' in response.headers.get('Content-Type', ''):
                        pending = bytearray()
                        async for chunk in response.content.iter_chunked(65536):
                            pending.extend(chunk)
                            if len(pending) > MAX_REPLY * 3:
                                return {"status": "outcome_unknown"}
                            while b'\n' in pending:
                                raw, _, tail = pending.partition(b'\n'); pending = bytearray(tail)
                                event = json.loads(raw)
                                if event.get('type') == 'complete':
                                    return receipt_from_relay(envelope, event.get('result'), account_id)
                                if event.get('type') == 'error':
                                    if event.get('safe_to_fallback') is True:
                                        return {**{key: envelope[key] for key in ('owner_id', 'automation_id', 'run_id', 'dot_id', 'idempotency_key')}, 'status': 'rejected_before_submit'}
                                    return {"status": "outcome_unknown"}
                        return {"status": "outcome_unknown"}
                    body = await response.json(content_type=None)
                    return receipt_from_relay(envelope, body, account_id)
                if response.status in (400, 401, 403, 404, 424):
                    # 424 must explicitly confirm the message never crossed
                    # the submission boundary. Other denial statuses from the
                    # owner-authenticated gateway also precede submission.
                    body = await response.json(content_type=None)
                    if response.status in (400, 401, 403, 404) or (
                        isinstance(body, dict)
                        and body.get("safe_to_fallback") is True
                    ):
                        return {
                            "owner_id": envelope["owner_id"],
                            "automation_id": envelope["automation_id"],
                            "run_id": envelope["run_id"],
                            "dot_id": envelope["dot_id"],
                            "idempotency_key": envelope["idempotency_key"],
                            "status": "retryable_before_submit" if isinstance(body, dict) and body.get("code") in
                                {"DOT_BROWSER_BUSY", "DOT_RATE_LIMIT"} else "rejected_before_submit",
                        }
                return {"status": "outcome_unknown"}
    except aiohttp.ClientConnectorError:
        # A refused localhost connection proves this *particular request*
        # could not have reached the relay.
        return {
            "owner_id": envelope["owner_id"],
            "automation_id": envelope["automation_id"],
            "run_id": envelope["run_id"],
            "dot_id": envelope["dot_id"],
            "idempotency_key": envelope["idempotency_key"],
            "status": "rejected_before_submit",
        }
    except Exception:
        log.warning("abitdot primary relay result unverified; original run remains claimed")
        return {"status": "outcome_unknown"}
