"""Self-service Slack workspace/account connection using Slack OAuth v2."""
import json

from fastapi import APIRouter, Depends, Query, Request, Response, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from open_webui.utils.auth import get_verified_user
from open_webui.utils import slack_connection as connection
from open_webui.utils import slack_bridge as bridge


def no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


router = APIRouter(dependencies=[Depends(no_store)])


async def linked_user(user=Depends(get_verified_user)):
    if not await connection.active_user(user.id):
        raise HTTPException(403, "This application account is not active.")
    return user


class SlackWorkspace(BaseModel):
    id: str
    name: str


class SlackStatus(BaseModel):
    configured: bool
    linked: bool = Field(description="A locally stored connection exists; not a live Slack health check.")
    workspace: SlackWorkspace | None
    slack_user_id: str | None
    linked_at: int | None
    dot_connected: bool
    dot_subscription_active: bool
    mcp_url: str | None


class SlackConnect(BaseModel):
    authorization_url: str
    expires_at: int


@router.get("/status", response_model=SlackStatus, summary="Get your Slack connection")
async def status(user=Depends(linked_user)):
    """Return only the authenticated user's workspace identity. Never returns tokens."""
    return await bridge.public_status(user.id)


@router.post("/connect", response_model=SlackConnect, summary="Start connecting your Slack account")
async def connect(response: Response, user=Depends(linked_user)):
    """Create a ten-minute, one-use OAuth state bound to this account and browser.
    Navigate to authorization_url in the same browser. Requests bot chat:write and
    im:history. Only the linked member's direct messages to this app enter the dot relay.
    """
    result, browser = connection.begin(user.id)
    response.set_cookie(connection.COOKIE_NAME, browser, max_age=connection.STATE_TTL,
                        path=connection.CALLBACK_PATH, httponly=True, secure=True, samesite="lax")
    return result


@router.get("/callback", response_class=RedirectResponse, summary="Complete Slack authorization")
async def callback(
    request: Request,
    state: str = Query(default="", max_length=200),
    code: str = Query(default="", max_length=4096),
    error: str = Query(default="", max_length=200),
):
    """Slack browser callback. Authenticated by single-use state and secure browser cookie,
    not a bearer token. Redirects only to the fixed local Slack connection page.
    """
    result = "expired"
    entry = connection.consume(state, request.cookies.get(connection.COOKIE_NAME, ""))
    if entry:
        if error:
            result = "cancelled"
        elif code and await connection.active_user(entry["owner"]):
            try:
                cfg = connection.config()
                payload = await connection.exchange(code, cfg)
                # Recheck account after the external exchange.
                if await connection.active_user(entry["owner"]):
                    connection.save_connection(entry["owner"], payload, cfg, entry)
                    result = "connected"
                else:
                    result = "failed"
            except HTTPException:
                result = "failed"
        else:
            result = "failed"
    response = RedirectResponse("/dot/slack?slack=" + result, status_code=303)
    response.delete_cookie(connection.COOKIE_NAME, path=connection.CALLBACK_PATH,
                           secure=True, httponly=True, samesite="lax")
    no_store(response)
    return response


@router.delete("/link", response_model=SlackStatus, summary="Disconnect your Slack account")
async def disconnect(response: Response, user=Depends(linked_user)):
    """Forget this account's stored Slack credentials and cancel pending connections.
    Does not uninstall the shared Slack app or revoke other users' connections.
    Slack-side authorization can also be removed in Slack's app settings.
    """
    response.delete_cookie(connection.COOKIE_NAME, path=connection.CALLBACK_PATH,
                           secure=True, httponly=True, samesite="lax")
    connection.disconnect(user.id)
    return await bridge.public_status(user.id)


class SlackMessage(BaseModel):
    id: str
    text: str
    created_at: int
    status: str
    response_text: str | None
    error: str | None


class SlackMessages(BaseModel):
    messages: list[SlackMessage]


@router.get("/messages", response_model=SlackMessages, summary="Read your Slack DM relay messages")
async def messages(limit: int = Query(default=30, ge=1, le=100),
                   before: str | None = Query(default=None, max_length=128),
                   user=Depends(linked_user)):
    """List stored messages for this ABiT account only; does not read workspace history."""
    return {"messages": bridge.list_messages(user.id, limit, before)}


@router.post("/disconnect-dot", response_model=SlackStatus, summary="Disconnect dot from your Slack relay")
async def disconnect_dot(user=Depends(linked_user)):
    """Revoke this owner's dot authorization, subscriptions, and unsent replies."""
    from open_webui.routers import slack_oauth, slack_mcp
    slack_oauth.revoke_owner(user.id)
    slack_mcp.revoke_subscriptions(user.id)
    with bridge.db() as conn:
        connection.invalidate_relay(conn, user.id)
    return await bridge.public_status(user.id)


class SlackWebhookResult(BaseModel):
    ok: bool | None = None
    challenge: str | None = None


@router.post("/webhook", response_model=SlackWebhookResult, response_model_exclude_none=True,
             summary="Receive signed Slack direct-message events")
async def webhook(request: Request):
    """Slack Events API callback. Requires Slack v0 HMAC signature over raw body and
    a timestamp within five minutes. App/team/member identity and event IDs are checked.
    Personal application API keys do not authenticate this endpoint.
    """
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > 262144:
            raise HTTPException(413, "Slack event exceeds the size limit.")
        raw.extend(chunk)
    bridge.verify_signature(bytes(raw), request.headers.get("x-slack-request-timestamp", ""),
                            request.headers.get("x-slack-signature", ""))
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Invalid Slack event.") from None
    return await bridge.accept_update(payload)
