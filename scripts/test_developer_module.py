#!/usr/bin/env python3
"""Verify the developer module against the real app in an isolated subprocess.

Run from the source checkout:
    python scripts/test_developer_module.py

The launcher uses an OS-only environment, fresh data/static directories under
.git, the source backend, and an in-process ASGI client. It never enters the
normal application lifespan, starts schedulers, or contacts configured providers.

Optional browser-test backend (start explicitly, after verification):
    python scripts/test_developer_module.py --serve --port 18888

The optional server binds loopback by default, uses freshly generated synthetic
credentials, and disables providers. Create the printed stop.request file to
stop it cleanly. Its temporary database is removed when the child process exits.
No production process, database, settings, or credentials are read or changed.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from copy import deepcopy
from datetime import timedelta
import importlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import traceback
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[1]
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
MODULE_PATHS = (
    "/api/v1/developer/",
    "/api/v1/developer/openapi.json",
    "/api/v1/developer/postman.json",
)
EXPECTED_GROUPS = {
    "auths", "users", "chats", "folders", "automations", "chatgpt-relay",
    "codex-handoff", "calendars", "channels", "files", "models", "knowledge",
    "retrieval", "prompts", "tools", "skills", "memories", "notes", "groups",
    "notifications", "functions", "evaluations", "pipelines", "tasks",
    "images", "audio", "configs", "utils", "terminals", "openai", "ollama",
    "telegram", "telegram-mcp", "telegram-oauth", "developer",
    "chat-completions", "runtime-tasks", "events", "system", "oauth",
}


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18888)
    return parser.parse_args()


def child_environment(directory: Path) -> dict[str, str]:
    # Do not inherit service credentials, .env settings, or live data locations.
    allowed = {
        "SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "PROGRAMFILES",
        "PROGRAMFILES(X86)", "PROGRAMW6432", "COMMONPROGRAMFILES",
        "COMMONPROGRAMFILES(X86)", "PROCESSOR_ARCHITECTURE",
        "PROCESSOR_IDENTIFIER", "NUMBER_OF_PROCESSORS",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    for name in ("data", "static", "frontend", "tmp", "profile", "hf", "torch"):
        (directory / name).mkdir(parents=True, exist_ok=True)
    (directory / "frontend" / "index.html").write_text(
        "<!doctype html><title>Isolated API test backend</title>", encoding="utf-8"
    )
    env.update({
        "PYTHONPATH": str(REPOSITORY / "backend"),
        "PYTHON_DOTENV_DISABLED": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TEMP": str(directory / "tmp"),
        "TMP": str(directory / "tmp"),
        "USERPROFILE": str(directory / "profile"),
        "APPDATA": str(directory / "profile"),
        "LOCALAPPDATA": str(directory / "profile"),
        "DATA_DIR": str(directory / "data"),
        "DATABASE_URL": "sqlite:///" + (directory / "data" / "webui.db").as_posix(),
        "STATIC_DIR": str(directory / "static"),
        "FRONTEND_BUILD_DIR": str(directory / "frontend"),
        "FROM_INIT_PY": "False",
        "ENV": "prod",
        "ENABLE_DB_MIGRATIONS": "True",
        "ENABLE_PERSISTENT_CONFIG": "True",
        "WEBUI_AUTH": "True",
        "WEBUI_SECRET_KEY": secrets.token_urlsafe(48),
        "WEBUI_URL": "http://127.0.0.1:18888",
        "WEBUI_NAME": "ABIT.AI API isolated test",
        "OFFLINE_MODE": "True",
        "HF_HUB_OFFLINE": "1",
        "HF_HOME": str(directory / "hf"),
        "TORCH_HOME": str(directory / "torch"),
        "ENABLE_VERSION_UPDATE_CHECK": "False",
        "RAG_EMBEDDING_MODEL_AUTO_UPDATE": "False",
        "RAG_RERANKING_MODEL_AUTO_UPDATE": "False",
        "WHISPER_MODEL_AUTO_UPDATE": "False",
        "RAG_EMBEDDING_ENGINE": "openai",
        "VECTOR_DB": "chroma",
        "CHROMA_HTTP_HOST": "",
        "CHROMA_CLIENT_AUTH_PROVIDER": "",
        "CHROMA_CLIENT_AUTH_CREDENTIALS": "",
        "ANONYMIZED_TELEMETRY": "False",
        "REDIS_URL": "",
        "WEBSOCKET_REDIS_URL": "",
        "WEBSOCKET_MANAGER": "",
        "STORAGE_PROVIDER": "local",
        "ENABLE_OLLAMA_API": "False",
        "ENABLE_OPENAI_API": "False",
        "ENABLE_EVALUATION_ARENA_MODELS": "False",
        "ENABLE_API_KEYS": "True",
        "ENABLE_LOGIN_FORM": "True",
        "ENABLE_SIGNUP": "False",
        "ENABLE_INITIAL_ADMIN_SIGNUP": "False",
        "ENABLE_OAUTH_SIGNUP": "False",
        "ENABLE_SCIM": "False",
        "ENABLE_OTEL": "False",
        "LICENSE_KEY": "",
        "AUDIT_LOG_LEVEL": "NONE",
        "GLOBAL_LOG_LEVEL": "ERROR",
        "LOG_FORMAT": "json",
        "ABIT_CHATGPT_RELAY_TOKEN_FILE": str(directory / "no-relay-token"),
        "ABIT_CODEX_CLI_CONFIG": str(directory / "no-code-worker.json"),
        "ABIT_API_MODULE_TEST_DIR": str(directory),
    })
    return env


def prevent_external_connections(event: str, values: tuple[Any, ...]) -> None:
    # Windows asyncio can use loopback socket pairs; permit those, reject external
    # connect attempts even if an import or an accidental provider call tries one.
    if event != "socket.connect" or len(values) < 2:
        return
    address = values[1]
    if not isinstance(address, tuple) or not address:
        return
    host = str(address[0]).split("%", 1)[0]
    if host.lower() == "localhost":
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise RuntimeError("External network connections are disabled in API integration tests")


class Checks:
    def __init__(self) -> None:
        self.results: list[dict[str, str]] = []

    def require(self, name: str, condition: Any) -> None:
        self.results.append({"name": name, "status": "passed" if condition else "failed"})
        if not condition:
            raise AssertionError(name)

    def status(self, name: str, response: Any, expected: int | set[int]) -> None:
        allowed = expected if isinstance(expected, set) else {expected}
        self.require(f"{name} (HTTP {response.status_code})", response.status_code in allowed)


def operations(schema: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (path, method): operation
        for path, item in schema.get("paths", {}).items()
        for method, operation in item.items()
        if method in HTTP_METHODS and isinstance(operation, dict)
    }


def references_are_valid(schema: dict[str, Any]) -> bool:
    stack: list[Any] = [schema]
    while stack:
        item = stack.pop()
        if isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, dict):
            ref = item.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/"):
                current: Any = schema
                try:
                    for part in ref[2:].split("/"):
                        current = current[part.replace("~1", "/").replace("~0", "~")]
                except (KeyError, TypeError):
                    return False
            stack.extend(item.values())
    return True


def postman_requests(collection: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    queue = list(collection.get("item", []))
    while queue:
        item = queue.pop()
        if isinstance(item.get("request"), dict):
            found.append(item["request"])
        queue.extend(item.get("item", []))
    return found


async def setup_isolated_app(directory: Path, checks: Checks) -> dict[str, Any]:
    import httpx
    import open_webui
    main = importlib.import_module("open_webui.main")
    from open_webui.config import DEFAULT_CONFIG, DEFAULT_USER_PERMISSIONS
    from open_webui.env import DATA_DIR, DATABASE_URL, STATIC_DIR
    from open_webui.models.auths import Auths
    from open_webui.models.config import Config
    from open_webui.utils.auth import create_token, get_password_hash

    checks.require(
        "actual source application imported",
        Path(open_webui.__file__).resolve().is_relative_to(REPOSITORY / "backend"),
    )
    checks.require(
        "database and static files confined to isolated directory",
        Path(DATA_DIR).resolve().is_relative_to(directory)
        and Path(STATIC_DIR).resolve().is_relative_to(directory)
        and (directory / "data" / "webui.db").as_posix() in DATABASE_URL.replace("\\", "/"),
    )
    checks.require("production-style built-in documentation disabled", main.app.openapi_url is None)
    checks.require("normal startup lifespan was not executed", not main.app.state.startup_complete)
    checks.require("no authentication dependency overrides", not main.app.dependency_overrides)
    await Config.seed_defaults(DEFAULT_CONFIG)
    permissions = deepcopy(DEFAULT_USER_PERMISSIONS)
    permissions.setdefault("features", {}).update({"api_keys": True, "folders": True, "automations": True})
    await Config.upsert({
        "auth.enable_api_keys": True,
        "auth.api_key.endpoint_restrictions": False,
        "auth.api_key.allowed_endpoints": "",
        "user.permissions": permissions,
        "openai.enable": False,
        "ollama.enable": False,
        "evaluation.arena.enable": False,
        "tool_server.connections": [],
        "terminal.connections": [],
        "ui.enable_signup": False,
        "ui.enable_login_form": True,
        "ui.webhook_url": "",
    })
    password = "API-local-" + secrets.token_urlsafe(24)
    users: dict[str, Any] = {}
    tokens: dict[str, str] = {}
    for role, email in (
        ("admin", "api-admin@example.test"),
        ("user", "api-user@example.test"),
        ("other", "api-other@example.test"),
        ("pending", "api-pending@example.test"),
    ):
        user = await Auths.insert_new_auth(
            email=email, password=await get_password_hash(password),
            name=f"API isolated {role}", role="user" if role == "other" else role,
        )
        checks.require(f"synthetic {role} account created in isolated database", user is not None)
        users[role] = user
        tokens[role] = create_token({"id": user.id}, timedelta(minutes=45))
    return {
        "app": main.app, "httpx": httpx, "Config": Config, "permissions": permissions,
        "users": users, "tokens": tokens, "password": password,
    }


async def verify_app(context: dict[str, Any], checks: Checks) -> dict[str, Any]:
    from fastapi.routing import APIRoute, APIWebSocketRoute
    from open_webui.utils.auth import create_token

    app = context["app"]
    Config = context["Config"]
    tokens = context["tokens"]
    users = context["users"]
    credentials = [context["password"], *tokens.values()]

    def bearer(kind: str) -> dict[str, str]:
        return {"Authorization": "Bearer " + tokens[kind]}

    transport = context["httpx"].ASGITransport(app=app)
    async with context["httpx"].AsyncClient(
        transport=transport, base_url="http://api-module.test", follow_redirects=False
    ) as client:
        for path in MODULE_PATHS:
            checks.status("anonymous request denied " + path, await client.get(path), 401)
        checks.status(
            "pending user cannot access documentation",
            await client.get(MODULE_PATHS[0], headers=bearer("pending")), 401,
        )
        expired = create_token({"id": users["user"].id}, timedelta(seconds=-10))
        checks.status(
            "expired session rejected",
            await client.get(MODULE_PATHS[0], headers={"Authorization": "Bearer " + expired}), 401,
        )
        checks.status("health endpoint", await client.get("/health"), 200)
        checks.status(
            "application config with real session",
            await client.get("/api/config", headers=bearer("admin")), 200,
        )
        login = await client.post("/api/v1/auths/signin", json={
            "email": users["admin"].email, "password": context["password"],
        })
        checks.status("real login using isolated auth record", login, 200)
        checks.require("login returns a session token", bool(login.json().get("token")))
        credentials.append(login.json().get("token", ""))
        checks.status("session cookie remains usable", await client.get("/api/v1/auths/"), 200)
        client.cookies.clear()

        schemas: dict[str, dict[str, Any]] = {}
        metadata: dict[str, dict[str, Any]] = {}
        role_counts: dict[str, dict[str, int]] = {}
        for role in ("admin", "user"):
            responses = [await client.get(path, headers=bearer(role)) for path in MODULE_PATHS]
            for path, response in zip(MODULE_PATHS, responses):
                checks.status(role + " documentation response " + path, response, 200)
                checks.require(
                    role + " private JSON documentation headers " + path,
                    response.headers.get("content-type", "").startswith("application/json")
                    and "no-store" in response.headers.get("cache-control", "")
                    and "authorization" in response.headers.get("vary", "").lower()
                    and "cookie" in response.headers.get("vary", "").lower()
                    and response.headers.get("x-content-type-options") == "nosniff",
                )
            metadata[role], schemas[role], collection = [response.json() for response in responses]
            current = operations(schemas[role])
            groups = Counter((operation.get("tags") or ["system"])[0] for operation in current.values())
            computed = {"operations": len(current), "paths": len(schemas[role]["paths"]), "groups": len(groups)}
            role_counts[role] = computed
            checks.require(role + " catalog counts match OpenAPI", metadata[role]["counts"] == computed)
            checks.require(
                role + " group counts match OpenAPI",
                {group["id"]: group["count"] for group in metadata[role]["groups"]} == dict(groups),
            )
            checks.require(role + " all local schema references resolve", references_are_valid(schemas[role]))
            identifiers = [operation.get("operationId") for operation in current.values()]
            checks.require(
                role + " operation IDs are unique",
                all(identifiers) and len(set(identifiers)) == len(identifiers),
            )
            exported = postman_requests(collection)
            checks.require(role + " Postman covers every operation", len(exported) == len(current))
            exported_pairs = {
                ("/" + "/".join(request["url"]["path"]), request["method"].lower())
                for request in exported
            }
            expected_pairs = {
                (path.replace("{", ":").replace("}", ""), method) for path, method in current
            }
            checks.require(role + " Postman methods and paths match schema", exported_pairs == expected_pairs)
            checks.require(
                role + " export variables contain no credentials",
                all(not value.get("value") for value in collection.get("variable", [])),
            )
            combined = "".join(response.text for response in responses)
            checks.require(
                role + " no synthetic session or secret leaked in documentation",
                all(not credential or credential not in combined for credential in credentials),
            )

        registered = {
            (route.path_format, method.lower())
            for route in app.routes
            if isinstance(route, APIRoute) and route.include_in_schema
            and (
                route.path_format.startswith(("/api/", "/openai", "/ollama", "/oauth/", "/.well-known/", "/cache/"))
                or route.path_format in {"/health", "/health/db", "/ready"}
            )
            for method in route.methods or ()
            if method.lower() in HTTP_METHODS
        }
        admin_operations = operations(schemas["admin"])
        user_operations = operations(schemas["user"])
        checks.require("admin schema covers every registered HTTP API operation", set(admin_operations) == registered)
        admin_groups = {group["id"] for group in metadata["admin"]["groups"]}
        checks.require("all expected application categories documented", EXPECTED_GROUPS <= admin_groups)
        checks.require("ordinary schema is a strict subset of administrator schema", set(user_operations) < set(admin_operations))
        protected = {
            ("/api/v1/knowledge/reindex", "post"),
            ("/api/v1/telegram/config", "post"),
            ("/api/v1/telegram/bot-health", "get"),
            ("/api/v1/chatgpt-relay/status", "get"),
            ("/api/v1/codex-handoff/jobs", "post"),
        }
        checks.require("declared and inline administrative operations filtered", protected <= set(admin_operations) and not protected.intersection(user_operations))
        checks.require(
            "custom capabilities present in ordinary schema",
            {
                ("/api/v1/automations/context-map", "get"),
                ("/api/v1/automations/context-map/messages", "post"),
                ("/api/v1/automations/{id}/run", "post"),
                ("/api/v1/automations/{id}/stop", "post"),
                ("/api/v1/folders/{id}/update", "post"),
                ("/api/chat/completions", "post"),
            } <= set(user_operations),
        )
        checks.require("Socket.IO transport reported separately", "/ws" in metadata["admin"]["realtime"]["socket_io_mounts"])
        expected_websockets = {route.path for route in app.routes if isinstance(route, APIWebSocketRoute)}
        checks.require(
            "terminal WebSocket catalog matches registered routes",
            set(metadata["admin"]["realtime"]["websocket_paths"]) == expected_websockets and bool(expected_websockets),
        )

        poisoned = await client.get(MODULE_PATHS[1], headers={
            **bearer("admin"), "Host": "untrusted-origin.invalid",
            "X-Forwarded-Host": "untrusted-origin.invalid", "X-Forwarded-Proto": "https",
        })
        checks.status("schema remains available with untrusted request origin", poisoned, 200)
        checks.require(
            "request host cannot enter OpenAPI server URLs",
            poisoned.json()["servers"][0]["url"] == "/"
            and "untrusted-origin.invalid" not in poisoned.text,
        )

        generated = await client.post("/api/v1/auths/api_key", headers=bearer("user"))
        checks.status("existing API-key creation endpoint", generated, 200)
        key = generated.json()["api_key"]
        credentials.append(key)
        key_headers = {"Authorization": "Bearer " + key}
        checks.status("real API-key documentation authentication", await client.get(MODULE_PATHS[0], headers=key_headers), 200)
        checks.status(
            "user API key cannot call administrative reindex",
            await client.post("/api/v1/knowledge/reindex", headers=key_headers), {401, 403},
        )

        await Config.upsert({
            "auth.api_key.endpoint_restrictions": True,
            "auth.api_key.allowed_endpoints": "/api/v1/developer",
        })
        allowed = await client.get(MODULE_PATHS[1], headers=key_headers)
        checks.status("API-key allowlist accepts developer child path", allowed, 200)
        checks.status(
            "API-key allowlist rejects another registered resource",
            await client.get("/api/v1/chats/", headers=key_headers), 403,
        )
        checks.require(
            "documentation reflects current endpoint restrictions",
            operations(allowed.json())[("/api/v1/chats/", "get")]["x-abit-api-key-access"] == "restricted"
            and operations(allowed.json())[(MODULE_PATHS[0], "get")]["x-abit-api-key-access"] == "allowed",
        )
        await Config.upsert({"auth.api_key.endpoint_restrictions": False})

        denied_permissions = deepcopy(context["permissions"])
        denied_permissions["features"]["api_keys"] = False
        await Config.upsert({"user.permissions": denied_permissions})
        checks.status(
            "revoked API-key feature permission enforced",
            await client.get(MODULE_PATHS[0], headers=key_headers), 403,
        )
        restricted_metadata = await client.get(MODULE_PATHS[0], headers=bearer("user"))
        checks.status("session documentation remains readable without API-key feature", restricted_metadata, 200)
        checks.require("API-key management permission accurately reported", not restricted_metadata.json()["authentication"]["can_manage_api_key"])
        await Config.upsert({"user.permissions": context["permissions"]})

        rotated = await client.post("/api/v1/auths/api_key", headers=bearer("user"))
        checks.status("API-key rotation using existing endpoint", rotated, 200)
        rotated_key = rotated.json()["api_key"]
        checks.require("rotated API key is different", rotated_key != key)
        checks.status("rotated-away key rejected", await client.get(MODULE_PATHS[0], headers=key_headers), 401)
        rotated_headers = {"Authorization": "Bearer " + rotated_key}
        checks.status("replacement API key accepted", await client.get(MODULE_PATHS[0], headers=rotated_headers), 200)
        await Config.upsert({"auth.enable_api_keys": False})
        checks.status("global API-key disable enforced", await client.get(MODULE_PATHS[0], headers=rotated_headers), 403)
        disabled = await client.get(MODULE_PATHS[0], headers=bearer("admin"))
        checks.require("disabled API-key policy reported to administrator", not disabled.json()["authentication"]["api_keys_enabled"])
        await Config.upsert({"auth.enable_api_keys": True})
        checks.status("API-key revocation endpoint", await client.delete("/api/v1/auths/api_key", headers=bearer("user")), 200)
        checks.status("revoked API key rejected", await client.get(MODULE_PATHS[0], headers=rotated_headers), 401)

        folder = await client.post("/api/v1/folders/", headers=bearer("user"), json={
            "name": "API integration project", "data": {"system_prompt": "Isolated project instructions"},
        })
        checks.status("isolated project creation", folder, 200)
        folder_id = folder.json()["id"]
        own_folder = await client.get(f"/api/v1/folders/{folder_id}", headers=bearer("user"))
        checks.status("existing project instructions readable", own_folder, 200)
        checks.require("project instructions preserved", own_folder.json()["data"]["system_prompt"] == "Isolated project instructions")
        checks.status(
            "another ordinary user cannot read private project",
            await client.get(f"/api/v1/folders/{folder_id}", headers=bearer("other")), {401, 403, 404},
        )
        chat = await client.post("/api/v1/chats/new", headers=bearer("user"), json={
            "folder_id": folder_id,
            "chat": {
                "title": "API integration chat", "models": [],
                "messages": [], "history": {"messages": {}, "currentId": None},
            },
        })
        checks.status("isolated new chat creation", chat, 200)
        chat_id = chat.json()["id"]
        checks.status("existing chat opens", await client.get(f"/api/v1/chats/{chat_id}", headers=bearer("user")), 200)
        checks.status(
            "another ordinary user cannot read private chat",
            await client.get(f"/api/v1/chats/{chat_id}", headers=bearer("other")), {401, 403, 404},
        )
        checks.status("project list remains available", await client.get("/api/v1/folders/", headers=bearer("user")), 200)
        checks.status("chat list remains available", await client.get("/api/v1/chats/", headers=bearer("user")), 200)
        checks.status("test chat removed", await client.delete(f"/api/v1/chats/{chat_id}", headers=bearer("user")), 200)
        checks.status("test project removed", await client.delete(f"/api/v1/folders/{folder_id}", headers=bearer("user")), 200)

    # JWT verification schedules last-active writes; await only this isolated
    # process's remaining short-lived tasks before disposing its SQLite engine.
    pending = [task for task in asyncio.all_tasks()
               if task is not asyncio.current_task()
               and "update_last_active_by_id" in getattr(task.get_coro(), "__qualname__", "")]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    return {
        "role_counts": role_counts,
        "registered_http_api_operations": len(registered),
        "administrator_groups": sorted(admin_groups),
        "authentication": "Real isolated database users, JWT sessions, API-key issuance/rotation/revocation; no dependency overrides",
        "application": "Actual source app and middleware, production ENV, ASGITransport without normal lifespan",
        "external_services": "No provider requests; external outbound socket connections blocked",
        "optional_modules": {"scim": False},
    }


async def serve_app(context: dict[str, Any], directory: Path, host: str, port: int) -> None:
    import uvicorn

    app = context["app"]
    app.state.startup_complete = True
    # These normally get initialized with model services during lifespan. Empty
    # values keep the isolated UI usable without loading any models or providers.
    for name in ("EMBEDDING_FUNCTION", "RERANKING_FUNCTION", "ef", "rf", "YOUTUBE_LOADER_TRANSLATION"):
        setattr(app.state, name, None)
    stop_file = directory / "stop.request"
    server_info = {
        "url": f"http://{host}:{port}",
        "test_only": True,
        "admin_email": context["users"]["admin"].email,
        "user_email": context["users"]["user"].email,
        "password": context["password"],
        "stop_file": str(stop_file),
        "data_directory": str(directory / "data"),
        "providers_disabled": True,
        "normal_lifespan_disabled": True,
    }
    (directory / "server.json").write_text(json.dumps(server_info, indent=2), encoding="utf-8")
    print("ISOLATED_API_SERVER=" + json.dumps(server_info), flush=True)
    server = uvicorn.Server(uvicorn.Config(
        app, host=host, port=port, lifespan="off", access_log=False, log_level="warning",
        proxy_headers=False,
    ))

    async def watch_stop() -> None:
        while not stop_file.exists() and not server.should_exit:
            await asyncio.sleep(0.25)
        server.should_exit = True

    stop_task = asyncio.create_task(watch_stop())
    try:
        await server.serve()
    finally:
        stop_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)


async def child_main(args: argparse.Namespace, directory: Path) -> int:
    checks = Checks()
    report: dict[str, Any] = {
        "result": "failed", "source": str(REPOSITORY),
        "scope": "Source integration verification only; no production deployment or restart",
    }
    try:
        context = await setup_isolated_app(directory, checks)
        report.update(await verify_app(context, checks))
        report["result"] = "passed"
        report["checks"] = checks.results
        report["passed"] = sum(item["status"] == "passed" for item in checks.results)
        report["failed"] = 0
        (directory / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("API_MODULE_INTEGRATION=" + json.dumps({
            key: report[key] for key in ("result", "passed", "failed", "role_counts", "registered_http_api_operations")
        }), flush=True)
        if args.serve:
            await serve_app(context, directory, args.host, args.port)
        return 0
    except Exception as error:
        report["checks"] = checks.results
        report["passed"] = sum(item["status"] == "passed" for item in checks.results)
        report["failed"] = max(1, sum(item["status"] == "failed" for item in checks.results))
        report["error"] = f"{type(error).__name__}: {error}"
        (directory / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        traceback.print_exc()
        print("API_MODULE_INTEGRATION=" + json.dumps({
            key: report[key] for key in ("result", "passed", "failed", "error")
        }), flush=True)
        return 1
    finally:
        module = sys.modules.get("open_webui.internal.db")
        if module is not None:
            await module.async_engine.dispose()
            module.engine.dispose()


def main() -> int:
    args = arguments()
    if args.child is not None:
        directory = args.child.resolve()
        if (
            not directory.is_relative_to(REPOSITORY / ".git")
            or not directory.name.startswith("api-module-")
            or os.environ.get("ABIT_API_MODULE_TEST_DIR") != str(directory)
        ):
            raise SystemExit("Child mode requires an isolated directory created by this launcher")
        sys.addaudithook(prevent_external_connections)
        return asyncio.run(child_main(args, directory))

    if not (REPOSITORY / ".git").is_dir():
        raise SystemExit("Run this script inside the authoritative Git checkout")
    directory = Path(tempfile.mkdtemp(prefix="api-module-", dir=REPOSITORY / ".git")).resolve()
    command = [sys.executable, str(Path(__file__).resolve()), "--child", str(directory)]
    if args.serve:
        command += ["--serve", "--host", args.host, "--port", str(args.port)]
    print("ISOLATED_TEST_DIRECTORY=" + str(directory), flush=True)
    try:
        child = subprocess.Popen(command, cwd=REPOSITORY, env=child_environment(directory))
        try:
            return child.wait(timeout=None if args.serve else 300)
        except (KeyboardInterrupt, subprocess.TimeoutExpired):
            (directory / "stop.request").touch()
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=15)
            return 130
    finally:
        # Preserve only the credential-free verification report; dispose all test
        # users, API keys, temporary static data and synthetic server credentials.
        for item in directory.iterdir():
            if item.name == "report.json":
                continue
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink(missing_ok=True)
        print("API_MODULE_REPORT=" + str(directory / "report.json"), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
