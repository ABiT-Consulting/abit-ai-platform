"""Disposable-DB integration with real ABiT JWT auth; never starts the live application."""
import asyncio
import base64
import hashlib
import json
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
DATA = Path(tempfile.mkdtemp(prefix="abit-telegram-runtime-check-"))
os.environ["DATA_DIR"] = str(DATA)
os.environ["STATIC_DIR"] = str(DATA / "static")
os.environ["FRONTEND_BUILD_DIR"] = str(DATA / "frontend-unused")
(DATA / "static").mkdir()
os.environ["DATABASE_URL"] = (DATA / "webui-isolated.db").as_uri().replace("file:///", "sqlite:///")
os.environ["WEBUI_SECRET_KEY"] = "isolated-telegram-runtime-test-only-secret"
os.environ["OFFLINE_MODE"] = "true"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["DO_NOT_TRACK"] = "true"
os.environ["ANONYMIZED_TELEMETRY"] = "false"

from fastapi import FastAPI, Request
import httpx
from open_webui.internal.db import get_async_db
from open_webui.models.auths import Auth
from open_webui.models.users import User
from open_webui.routers import telegram, telegram_oauth, telegram_mcp
from open_webui.utils import telegram_bridge as bridge
from open_webui.utils.auth import create_token

async def main():
    stamp = int(time.time())
    async with get_async_db() as session:
        for owner, role, active in (
            ("telegram-test-admin","admin",True),
            ("telegram-test-user","user",True),
            ("telegram-test-other","user",True),
            ("telegram-test-disabled","user",False),
            ("telegram-test-pending","pending",True),
        ):
            session.add(Auth(id=owner,email=owner+"@example.invalid",password="unusable-test-password",active=active))
            session.add(User(id=owner,email=owner+"@example.invalid",name=owner,role=role,
                             profile_image_url="/user.png",created_at=stamp,updated_at=stamp,last_active_at=stamp))
        await session.commit()
    assert str(bridge.LEDGER).startswith(str(DATA))
    assert await bridge.active_user("telegram-test-user")
    assert await bridge.active_user("telegram-test-disabled") is None
    assert await bridge.active_user("telegram-test-pending") is None
    assert await bridge.active_user("nonexistent") is None
    with bridge.db() as conn:
        conn.execute(
            "INSERT INTO tg_config(id,token,bot_id,bot_username,public_url,webhook_secret,configured_by,updated_at)"
            " VALUES(1,?,?,?,?,?,?,?)",
            (bridge.encrypt_secret("123456:synthetic_never_sent_to_telegram"),
             "123456","AbitSyntheticBot","https://ai.example.invalid",bridge.encrypt_secret("synthetic-hook"),"telegram-test-admin",stamp)
        )
        conn.commit()
    app=FastAPI()
    app.state.redis=None
    app.include_router(telegram.router,prefix="/api/v1/telegram")
    app.include_router(telegram_oauth.router,prefix="/api/v1/telegram/oauth")
    app.include_router(telegram_mcp.router,prefix="/api/v1/telegram")
    @app.get("/api/v1/telegram/.well-known/oauth-protected-resource")
    async def resource():
        return telegram_oauth.protected_resource_metadata()
    @app.get("/.well-known/oauth-authorization-server/api/v1/telegram/oauth")
    async def issuer():
        return telegram_oauth.authorization_server_metadata()
    def headers(owner):
        return {"Authorization":"Bearer "+create_token({"id":owner})}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="https://ai.example.invalid") as client:
        base="/api/v1/telegram"
        assert (await client.get(base+"/status")).status_code==401
        response=await client.get(base+"/status",headers=headers("telegram-test-user"))
        assert response.status_code==200, response.text
        assert response.json()["configured"] and not response.json()["linked"]
        assert (await client.get(base+"/status",headers=headers("telegram-test-disabled"))).status_code==403
        assert (await client.get(base+"/status",headers=headers("telegram-test-pending"))).status_code in (401,403)
        pair=await client.post(base+"/pairings",headers=headers("telegram-test-user"))
        assert pair.status_code==200, pair.text
        pair=pair.json()
        token=pair["deep_link"].split("?start=",1)[1]
        webhook=await client.post(base+"/webhook",headers={"X-Telegram-Bot-Api-Secret-Token":"synthetic-hook"},
            json={"update_id":1,"message":{"message_id":1,"text":"/start "+token,
                "from":{"id":101,"is_bot":False,"first_name":"Isolated"},"chat":{"id":101,"type":"private"}}})
        assert webhook.status_code==200, webhook.text
        denied=await client.post(base+"/pairings/"+pair["id"]+"/confirm",headers=headers("telegram-test-other"))
        assert denied.status_code in (404,409), denied.text
        confirmed=await client.post(base+"/pairings/"+pair["id"]+"/confirm",headers=headers("telegram-test-user"))
        assert confirmed.status_code==200 and confirmed.json()["linked"],confirmed.text
        wrong=(await client.get(base+"/status",headers=headers("telegram-test-other"))).json()
        assert not wrong["linked"] and wrong["pairing"] is None
        metadata=await client.get(base+"/.well-known/oauth-protected-resource")
        assert metadata.status_code==200 and metadata.json()["resource"].endswith("/api/v1/telegram/mcp")
        discovery=await client.get("/.well-known/oauth-authorization-server/api/v1/telegram/oauth")
        assert discovery.status_code==200 and "S256" in discovery.json()["code_challenge_methods_supported"]
        # Application login JWT is not a valid MCP OAuth bearer.
        forbidden=await client.post(base+"/mcp",headers=headers("telegram-test-user"),
                                  json={"jsonrpc":"2.0","id":1,"method":"server/discover","params":{}})
        assert forbidden.status_code in (400,401),forbidden.text
        # Complete the same OAuth -> event -> reply path used by a dot, with
        # only the two external transports replaced by deterministic fixtures.
        redirect="https://chatgpt.com/connector_platform_oauth_redirect"
        registration=await client.post(base+"/oauth/register",json={"redirect_uris":[redirect],"client_name":"Isolated ChatGPT fixture"})
        assert registration.status_code==201,registration.text
        client_id=registration.json()["client_id"]
        resource_url="https://ai.example.invalid"+base+"/mcp"
        verifier="v"*64
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        authorization=await client.get(base+"/oauth/authorize",params={
            "client_id":client_id,"redirect_uri":redirect,"response_type":"code",
            "resource":resource_url,"code_challenge":challenge,"code_challenge_method":"S256","state":"isolated-state"})
        assert authorization.status_code==303,authorization.text
        consent_id=parse_qs(urlparse(authorization.headers["location"]).query)["request"][0]
        details=await client.get(base+"/oauth/requests/"+consent_id,headers=headers("telegram-test-user"))
        assert details.status_code==200,details.text
        consent=await client.post(base+"/oauth/approve",headers=headers("telegram-test-user"),json={"request_id":consent_id,"approve":True})
        assert consent.status_code==200,consent.text
        code=parse_qs(urlparse(consent.json()["redirect_url"]).query)["code"][0]
        exchanged=await client.post(base+"/oauth/token",data={"grant_type":"authorization_code","client_id":client_id,
            "code":code,"redirect_uri":redirect,"code_verifier":verifier,"resource":resource_url})
        assert exchanged.status_code==200,exchanged.text
        oauth_token=exchanged.json()["access_token"]
        async def mcp_call(method,params=None):
            params=dict(params or {})
            params["_meta"]={"io.modelcontextprotocol/protocolVersion":"2026-07-28",
                             "io.modelcontextprotocol/clientCapabilities":{}}
            request_headers={"Authorization":"Bearer "+oauth_token,"MCP-Protocol-Version":"2026-07-28","MCP-Method":method}
            if method=="tools/call": request_headers["MCP-Name"]=params["name"]
            return await client.post(base+"/mcp",headers=request_headers,
                                     json={"jsonrpc":"2.0","id":1,"method":method,"params":params})
        discovered=await mcp_call("server/discover")
        assert discovered.status_code==200 and "events" in discovered.json()["result"]["capabilities"],discovered.text
        delivered_events=[]
        async def callback(url,raw,request_headers):
            body=json.loads(raw)
            if body.get("type")=="verification": return 200,json.dumps({"challenge":body["challenge"]}).encode(),None
            delivered_events.append(body)
            return 202,b"",None
        with patch.object(telegram_mcp,"_callback_post",side_effect=callback):
            subscribed=await mcp_call("events/subscribe",{"name":"telegram.message_received","arguments":{},
                "delivery":{"mode":"webhook","url":"https://callbacks.example.com/isolated-dot",
                            "secret":"whsec_"+base64.b64encode(b"s"*32).decode()},"ttlMs":3600000})
            assert subscribed.status_code==200 and "result" in subscribed.json(),subscribed.text
            incoming=await client.post(base+"/webhook",headers={"X-Telegram-Bot-Api-Secret-Token":"synthetic-hook"},
                json={"update_id":2,"message":{"message_id":2,"text":"A question for the subscribed dot",
                    "from":{"id":101,"is_bot":False,"first_name":"Isolated"},"chat":{"id":101,"type":"private"}}})
            assert incoming.status_code==200,incoming.text
            await telegram_mcp.deliver_pending_events()
        assert len(delivered_events)==1,delivered_events
        message_id=delivered_events[0]["data"]["message_id"]
        answer="Reply from the isolated dot fixture"
        reply=await mcp_call("tools/call",{"name":"reply_to_telegram_message","arguments":{"message_id":message_id,"text":answer}})
        assert reply.status_code==200 and not reply.json()["result"]["isError"],reply.text
        with bridge.db() as conn:
            conn.execute("DELETE FROM tg_outbox WHERE message_id IS NULL")
            conn.commit()
        with patch.object(bridge,"telegram_call",AsyncMock(return_value={"message_id":777})) as send:
            assert await bridge.deliver_outbox_once()
            assert send.call_count==1
            assert send.call_args.args[0]=="sendMessage"
            assert send.call_args.args[1]=={"chat_id":"101","text":answer}
            again=await mcp_call("tools/call",{"name":"reply_to_telegram_message","arguments":{"message_id":message_id,"text":answer}})
            assert again.json()["result"]["structuredContent"]["duplicate"],again.text
            assert not await bridge.deliver_outbox_once()
            assert send.call_count==1
        saved=bridge.list_messages("telegram-test-user")[0]
        assert saved["status"]=="replied" and saved["response_text"]==answer,saved
        revoked=await client.post(base+"/disconnect-dot",headers=headers("telegram-test-user"))
        assert revoked.status_code==200 and not revoked.json()["dot_connected"] and not revoked.json()["dot_subscription_active"],revoked.text
        assert (await mcp_call("server/discover")).status_code==401
        disconnected=await client.delete(base+"/link",headers=headers("telegram-test-user"))
        assert disconnected.status_code==200 and not disconnected.json()["linked"]
    # Production audit mode must retain metadata without logging pairing,
    # OAuth, bot credentials, or private message bodies.
    from open_webui.utils.audit import AuditContext, AuditLevel, AuditLoggingMiddleware
    auditor=AuditLoggingMiddleware(app,audit_level=AuditLevel.REQUEST_RESPONSE)
    auditor._get_authenticated_user=AsyncMock(return_value=None)
    recorded=[]
    auditor.audit_logger.write=recorded.append
    context=AuditContext()
    context.add_request_chunk(b'{"token":"synthetic-sensitive-value"}')
    context.add_response_chunk(b'{"access_token":"synthetic-sensitive-value"}')
    request=Request({"type":"http","method":"POST","path":"/api/v1/telegram/config",
                     "query_string":b"","headers":[],"scheme":"https","server":("ai.example.invalid",443)})
    await auditor._log_audit_entry(request,context)
    assert recorded and recorded[0].request_object=="[redacted Telegram payload]"
    assert recorded[0].response_object=="[redacted Telegram payload]"
    assert recorded[0].verb=="POST"
    await asyncio.sleep(0.05)
    print("PASS: real JWT auth, active/disabled roles, owner-scoped QR confirmation, metadata, dedicated MCP auth, OAuth consent, exact-grant subscription, event, reply, idempotent Telegram send, revocation, disconnect.")
    print("All state was isolated; no live database or external messaging was used.")

if __name__=="__main__":
    asyncio.run(main())
