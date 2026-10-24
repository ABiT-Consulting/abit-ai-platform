"""Telegram QR linking and webhook endpoints; existing app auth owns every link."""
from __future__ import annotations

import hmac
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from open_webui.utils.auth import get_verified_user
from open_webui.utils import telegram_bridge as bridge


def no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


router = APIRouter(dependencies=[Depends(no_store)])


async def linked_user(user=Depends(get_verified_user)):
    if not await bridge.active_user(user.id):
        raise HTTPException(403, "This application account is not active.")
    return user


async def admin_user(user=Depends(linked_user)):
    if user.role != "admin":
        raise HTTPException(403, "Only an administrator can configure the Telegram bot.")
    return user


class BotConfigForm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr
    public_url: str = Field(min_length=8, max_length=300)
    replace_existing_webhook: bool = False


@router.get("/status")
async def status(user=Depends(linked_user)):
    return await bridge.public_status(user.id)


@router.post("/config")
async def configure(form: BotConfigForm, user=Depends(admin_user)):
    return await bridge.configure_bot(
        user.id, form.token.get_secret_value(), form.public_url,
        form.replace_existing_webhook
    )


@router.post("/pairings")
async def create_pairing(user=Depends(linked_user)):
    return bridge.create_pairing(user.id)


@router.post("/pairings/{identifier}/confirm")
async def confirm_pairing(identifier: str, user=Depends(linked_user)):
    bridge.confirm_pairing(user.id, identifier)
    return await bridge.public_status(user.id)


@router.delete("/pairings/{identifier}")
async def cancel_pairing(identifier: str, user=Depends(linked_user)):
    bridge.cancel_pairing(user.id, identifier)
    return await bridge.public_status(user.id)


@router.delete("/link")
async def disconnect(user=Depends(linked_user)):
    bridge.unlink(user.id)
    return await bridge.public_status(user.id)


@router.post("/disconnect-dot")
async def disconnect_dot(user=Depends(linked_user)):
    from open_webui.routers import telegram_oauth, telegram_mcp
    telegram_oauth.revoke_owner(user.id)
    telegram_mcp.revoke_subscriptions(user.id)
    return await bridge.public_status(user.id)


@router.get("/messages")
async def messages(
    limit: int = Query(default=30, ge=1, le=100),
    before: str | None = Query(default=None, max_length=100),
    user=Depends(linked_user)
):
    return {"messages": bridge.list_messages(user.id, limit, before)}


@router.post("/webhook")
async def webhook(request: Request):
    config = bridge.get_config()
    if not config or not config["enabled"]:
        raise HTTPException(503, "Telegram is not configured.")
    supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    expected = bridge.decrypt_secret(config["webhook_secret"])
    if not supplied or not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(403, "Invalid Telegram webhook authentication.")
    body = bytearray()
    async for part in request.stream():
        if len(body) + len(part) > 262144:
            raise HTTPException(413, "Telegram update exceeds the size limit.")
        body.extend(part)
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Invalid Telegram update.") from None
    await bridge.accept_update(payload)
    return {"ok": True}
