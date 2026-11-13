"""Build an authenticated API catalog from the application's registered routes.

This module has no database or runtime configuration imports. Documentation is
a view of the existing API: it does not grant access or execute operations.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import copy, deepcopy
from dataclasses import dataclass
import inspect
import json
import re
from typing import Any
from urllib.parse import quote
import warnings

from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute, APIWebSocketRoute
from starlette.routing import Mount

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "options", "head", "trace")
GROUP_LABELS = {
    "auths": "Authentication", "users": "Users", "chats": "Chats",
    "folders": "Projects & folders", "automations": "Agents & automations",
    "agentic": "ERP Agentic Governance",
    "chatgpt-relay": "ChatGPT relay", "codex-handoff": "Code jobs",
    "calendars": "Calendars", "channels": "Team & channels",
    "models": "Models", "knowledge": "Knowledge", "retrieval": "Search & retrieval",
    "files": "Files", "prompts": "Prompts", "tools": "Tools & connectors",
    "skills": "Skills", "memories": "Memories", "notes": "Notes",
    "groups": "User groups", "notifications": "Notifications",
    "functions": "Functions", "evaluations": "Evaluations",
    "analytics": "Analytics", "pipelines": "Pipelines",
    "tasks": "AI task generation", "images": "Images", "audio": "Speech & audio",
    "configs": "System configuration", "utils": "Utilities",
    "terminals": "Terminals", "openai": "OpenAI compatible proxy",
    "ollama": "Ollama", "telegram": "Telegram", "telegram-mcp": "Telegram MCP",
    "telegram-oauth": "Telegram authorization", "scim": "Identity provisioning",
    "developer": "API & developers", "chat-completions": "Chat & generation",
    "events": "Events & webhooks", "runtime-tasks": "Active tasks",
    "system": "System & health", "oauth": "OAuth",
}
SECRET_FIELD = re.compile(r"password|secret|token$|api[_-]?key|authorization|cookie|private[_-]?key", re.I)
AUTH_GUIDANCE = (
    "Use Authorization: Bearer YOUR_API_KEY for personal API keys when enabled, "
    "or a valid application session token. API keys retain the account's role, "
    "feature permissions, resource ownership and configured endpoint restrictions. "
    "An operation appearing here is not a permission grant."
)


@dataclass(frozen=True)
class RouteAccess:
    access: str
    note: str
    security: str | None = None
    admin_docs_only: bool = False


def dependency_calls(route: APIRoute) -> set[tuple[str, str]]:
    calls: set[tuple[str, str]] = set()
    pending = [route.dependant]
    seen: set[int] = set()
    while pending:
        dependency = pending.pop()
        if id(dependency) in seen:
            continue
        seen.add(id(dependency))
        call = getattr(dependency, "call", None)
        if call:
            calls.add((getattr(call, "__module__", ""), getattr(call, "__name__", "")))
        pending.extend(getattr(dependency, "dependencies", ()))
    return calls


def route_access(route: APIRoute) -> RouteAccess:
    """Describe declared auth and the known inline checks in ABiT custom routes."""
    path = route.path_format
    calls = dependency_calls(route)
    names = {name for _, name in calls}
    is_admin = "get_admin_user" in names or (
        "open_webui.routers.telegram", "admin_user"
    ) in calls
    # These custom routers enforce administrator access inside their handlers.
    if path.startswith(("/api/v1/chatgpt-relay/", "/api/v1/codex-handoff/")):
        return RouteAccess(
            "admin",
            "Administrator authentication is required. The configured model/worker "
            "and the caller's resource ownership are also checked. Worker credentials "
            "stay on the server; do not supply them to these application endpoints.",
            "ABITBearer",
            True,
        )
    if path.startswith('/api/v1/agentic/') and path!='/api/v1/agentic/health':
        signed=path=='/api/v1/agentic/events'
        return RouteAccess(
            'custom',
            ('Requires signed ERP outbox headers X-ABIT-Timestamp, X-ABIT-Event-ID and X-ABIT-Signature. '
             'Timestamp and durable event identity are verified before acceptance.' if signed else
             'Requires the protected server-side X-ABIT-ERP-Control credential. Project opt-in, live ERP assignment, '
             'task revision and invocation ownership are checked separately.' )+
            ' Personal application API keys and browser sessions do not authenticate this operation.',
            'ABITERPSignature' if signed else 'ABITERPControl',True,
        )
    if path == "/api/v1/knowledge/reindex":
        is_admin = True
    if "get_scim_auth" in names:
        return RouteAccess(
            "custom",
            "Requires the separately configured SCIM provisioning bearer token and "
            "SCIM to be enabled. Personal application API keys are not accepted.",
            "SCIMBearer",
            True,
        )
    if path == "/api/v1/telegram/mcp":
        return RouteAccess(
            "custom",
            "Requires a dedicated, scoped Telegram OAuth access token obtained through "
            "the Telegram authorization flow, plus the MCP protocol headers and JSON-RPC "
            "body. Personal application API keys are not accepted.",
            "TelegramOAuthBearer",
        )
    if path == "/api/v1/telegram/webhook":
        return RouteAccess(
            "custom",
            "Telegram supplies the configured X-Telegram-Bot-Api-Secret-Token header. "
            "A personal application API key does not authenticate this webhook.",
            "TelegramWebhookSecret",
        )
    if path.startswith("/oauth/"):
        return RouteAccess(
            "custom",
            "This is a browser/OAuth protocol flow. Depending on the step it requires "
            "an authenticated browser session, provider credentials, signed state, "
            "or a provider logout token. A personal API key alone does not complete it.",
        )
    if path.startswith("/api/v1/telegram/oauth/"):
        if "_web_user" in names:
            return RouteAccess(
                "authenticated",
                "Requires an explicit Authorization: Bearer header for an active "
                "application account and the matching consent request. An ambient "
                "browser cookie alone is not accepted.",
                "ABITBearer",
            )
        if path.rsplit("/", 1)[-1] in {"token", "revoke", "authorize"}:
            return RouteAccess(
                "custom",
                "Part of the Telegram OAuth authorization-code/PKCE flow. Use its "
                "authorization code, code verifier or issued refresh/access token as "
                "required by this step. Personal API keys are not OAuth grants.",
            )
    if is_admin:
        return RouteAccess(
            "admin", "Administrator authentication is required. " + AUTH_GUIDANCE,
            "ABITBearer", True,
        )
    if names.intersection({"get_verified_user", "get_current_user", "get_optional_verified_user_from_request"}):
        return RouteAccess("authenticated", AUTH_GUIDANCE, "ABITBearer")
    return RouteAccess(
        "public",
        "No application authentication dependency is declared for this operation. "
        "Its input validation, feature configuration and any protocol-specific checks still apply.",
    )


def documented_routes(app: Any, role: str) -> list[APIRoute]:
    routes = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.include_in_schema:
            continue
        if role != "admin" and route_access(route).admin_docs_only:
            continue
        routes.append(route)
    return routes


def route_group(route: APIRoute) -> str:
    if route.tags:
        return str(route.tags[0])
    path = route.path_format
    if path.startswith(("/oauth/", "/.well-known/")):
        return "oauth"
    if path.startswith(("/api/chat/", "/api/v1/chat/", "/api/message", "/api/v1/messages", "/api/embeddings", "/api/v1/embeddings")):
        return "chat-completions"
    if path.startswith("/api/events"):
        return "events"
    if path.startswith("/api/tasks"):
        return "runtime-tasks"
    if path.startswith(("/api/models", "/api/v1/models")):
        return "models"
    return "system"


def _append_description(operation: dict[str, Any], note: str) -> None:
    description = str(operation.get("description") or "").strip()
    operation["description"] = f"{description}\n\n{note}".strip()


def _enrich_known_operations(path: str, method: str, operation: dict[str, Any]) -> None:
    if method == "post" and path in {"/api/chat/completions", "/api/v1/chat/completions"}:
        content = operation.setdefault("requestBody", {}).setdefault("content", {})
        content.setdefault("application/json", {"schema": {"type": "object"}})["example"] = {
            "model": "YOUR_MODEL_ID",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
        }
        _append_description(
            operation,
            "Replace YOUR_MODEL_ID with an accessible ID from GET /api/models. "
            "Set stream=true for a streaming response and consume it incrementally. "
            "Additional model, chat and feature permissions are enforced by the application.",
        )
        success = operation.setdefault("responses", {}).setdefault("200", {"description": "Successful response"})
        success.setdefault("content", {})["text/event-stream"] = {
            "schema": {"type": "string"},
        }
    if method == "post" and path in {"/api/embeddings", "/api/v1/embeddings"}:
        content = operation.setdefault("requestBody", {}).setdefault("content", {})
        content.setdefault("application/json", {"schema": {"type": "object"}})["example"] = {
            "model": "YOUR_MODEL_ID", "input": "Text to embed",
        }
    if method == "post" and path == "/api/v1/automations/{id}/toggle":
        _append_description(
            operation,
            "Toggles future scheduling. Disabling the schedule does not pause or cancel "
            "a run already executing; use the stop endpoint to request cancellation.",
        )
    if method == "post" and path == "/api/v1/automations/{id}/run":
        _append_description(
            operation,
            "Queues a run. An existing queued or active run returns HTTP 409. "
            "A successful response does not mean the execution has completed; "
            "inspect the automation's runs and current status.",
        )
    if method == "post" and path == "/api/v1/automations/{id}/stop":
        _append_description(
            operation,
            "Disables the schedule and requests cancellation of the caller's active "
            "application tasks. The response reports schedule_paused, cancellation_requested "
            "and relay_unconfirmed. An upstream relay may still be executing; cancellation "
            "is not confirmed by merely receiving a successful response.",
        )
    if "{" in path and path.startswith(("/openai/", "/api/v1/terminals/")):
        _append_description(
            operation,
            "This registered proxy route forwards a provider-specific path. "
            "The request and response body depend on that upstream service; "
            "an empty object schema does not describe all upstream fields.",
        )


def _scrub_secret_defaults(value: Any, name: str = "") -> None:
    """Avoid exporting static credential defaults/examples if a schema declares one."""
    if isinstance(value, dict):
        if SECRET_FIELD.search(name):
            for key in ("default", "example", "examples"):
                value.pop(key, None)
        for key, child in list(value.items()):
            if key == "properties" and isinstance(child, dict):
                for field, definition in child.items():
                    _scrub_secret_defaults(definition, field)
            elif key == "parameters" and isinstance(child, list):
                for parameter in child:
                    _scrub_secret_defaults(parameter, str(parameter.get("name", "")))
            elif key in ("example", "examples", "default", "const"):
                value[key] = _safe_example(child)
            else:
                _scrub_secret_defaults(child, name if key == "schema" else "")
    elif isinstance(value, list):
        for child in value:
            _scrub_secret_defaults(child)


def _schema_route(route: APIRoute) -> APIRoute:
    """Handle custom response wrappers without changing registered runtime routes.

    FastAPI inspects the response constructor for a status_code default. Some
    existing ABiT wrappers expose only *args/**kwargs, which otherwise makes
    get_openapi fail. Infer their inherited default on a shallow documentation copy.
    """
    if route.status_code is not None:
        return route
    response_class = getattr(route.response_class, "value", route.response_class)
    parameter = inspect.signature(response_class.__init__).parameters.get("status_code")
    if parameter is not None and isinstance(parameter.default, int):
        return route
    inferred = 200
    for parent in getattr(response_class, "__mro__", ())[1:]:
        parameter = inspect.signature(parent.__init__).parameters.get("status_code")
        if parameter is not None and isinstance(parameter.default, int):
            inferred = parameter.default
            break
    documented = copy(route)
    documented.status_code = inferred
    return documented


def _base_schema(app: Any, role: str, version: str, base_url: str) -> dict[str, Any]:
    routes = documented_routes(app, role)
    # Rebuild from the current contract: existing routes, models and security
    # declarations can change without changing a route identity or path.
    with warnings.catch_warnings():
        # FastAPI gives one operation ID to a multi-method proxy; repaired below.
        warnings.filterwarnings("ignore", message="Duplicate Operation ID.*", category=UserWarning)
        schema = get_openapi(
            title="ABIT.AI API",
            version=version,
            description=(
                "HTTP API reference generated from this application's registered routes. "
                + AUTH_GUIDANCE
                + " Socket.IO and WebSocket transports are listed separately in the developer "
                "module. Request examples contain placeholders; replace them before use."
            ),
            routes=[_schema_route(route) for route in routes],
            servers=[{"url": base_url, "description": "This application (same origin)"}],
        )
    schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
    schemes["ABITBearer"] = {
        "type": "http", "scheme": "bearer",
        "description": AUTH_GUIDANCE,
    }
    scheme_specs = {
        'ABITERPControl': {'type':'apiKey','in':'header','name':'X-ABIT-ERP-Control','description':'Protected ERP server credential; never place in browser or model prompts.'},
        'ABITERPSignature': {'type':'apiKey','in':'header','name':'X-ABIT-Signature','description':'HMAC SHA256 of timestamp, event ID and canonical body; requires matching timestamp and event ID headers.'},
        "SCIMBearer": {"type": "http", "scheme": "bearer", "description": "Separate configured SCIM provisioning token."},
        "TelegramOAuthBearer": {"type": "http", "scheme": "bearer", "description": "Scoped access token from the Telegram OAuth flow."},
        "TelegramWebhookSecret": {"type": "apiKey", "in": "header", "name": "X-Telegram-Bot-Api-Secret-Token"},
    }
    seen_ids: set[str] = set()
    for route in routes:
        access = route_access(route)
        path = route.path_format
        operations = schema.get("paths", {}).get(path, {})
        for method in sorted(route.methods or ()):
            method = method.lower()
            operation = operations.get(method)
            if not isinstance(operation, dict):
                continue
            operation["tags"] = [route_group(route)] + [
                str(tag) for tag in route.tags[1:]
            ]
            operation_access = access
            if access.access == "public" and operation.get("security"):
                operation_access = RouteAccess(
                    "custom",
                    "Authentication follows this operation's native OpenAPI security "
                    "schemes. Application API-key support and required roles are not "
                    "inferred; the endpoint's existing authentication and authorization "
                    "checks apply.",
                )
            operation["x-abit-access"] = operation_access.access
            operation["x-abit-auth-note"] = operation_access.note
            if access.security:
                operation["security"] = [{access.security: []}]
                if access.security in scheme_specs:
                    schemes[access.security] = scheme_specs[access.security]
            else:
                # Preserve unfamiliar security schemes instead of declaring the
                # operation public or claiming that an application key works.
                operation.setdefault("security", [])
            identifier = str(operation.get("operationId") or route.name)
            if identifier in seen_ids:
                identifier = f"{identifier}_{method}"
                suffix = 2
                while identifier in seen_ids:
                    identifier = f"{identifier}_{suffix}"
                    suffix += 1
                operation["operationId"] = identifier
            seen_ids.add(identifier)
            _enrich_known_operations(path, method, operation)
    _scrub_secret_defaults(schema)
    schema["tags"] = [
        {"name": group, "description": GROUP_LABELS.get(group, group.replace("-", " ").title())}
        for group in sorted({route_group(route) for route in routes})
    ]
    return schema


def api_key_access(path: str, access: str, authentication: dict[str, Any]) -> str:
    if access == "public":
        return "not-required"
    if access == "custom":
        return "custom"
    if not authentication.get("api_keys_enabled") or not authentication.get("can_manage_api_key"):
        return "disabled"
    if not authentication.get("endpoint_restrictions"):
        return "allowed"
    allowed_paths = authentication.get("allowed_endpoints") or []
    if any(path == allowed or path.startswith(allowed + "/") for allowed in allowed_paths):
        return "allowed"
    if "{" in path:
        # A concrete allowed resource ID may match only some values of a template.
        fixed_prefix = path.split("{", 1)[0].rstrip("/")
        if any(allowed == fixed_prefix or allowed.startswith(fixed_prefix + "/") for allowed in allowed_paths):
            return "conditional"
    return "restricted"


def build_schema(
    app: Any, role: str, version: str, authentication: dict[str, Any], base_url: str = "/"
) -> dict[str, Any]:
    schema = _base_schema(app, role, version, base_url)
    for path, operations in schema.get("paths", {}).items():
        for method in HTTP_METHODS:
            operation = operations.get(method)
            if isinstance(operation, dict):
                operation["x-abit-api-key-access"] = api_key_access(
                    path, operation["x-abit-access"], authentication
                )
    return schema


def catalog_counts(schema: dict[str, Any]) -> tuple[dict[str, int], list[dict[str, Any]]]:
    groups: Counter[str] = Counter()
    for operations in schema.get("paths", {}).values():
        for method in HTTP_METHODS:
            operation = operations.get(method)
            if isinstance(operation, dict):
                groups[(operation.get("tags") or ["system"])[0]] += 1
    return (
        {"operations": sum(groups.values()), "paths": len(schema.get("paths", {})), "groups": len(groups)},
        [
            {"id": group, "label": GROUP_LABELS.get(group, group.replace("-", " ").title()), "count": count}
            for group, count in sorted(groups.items(), key=lambda pair: GROUP_LABELS.get(pair[0], pair[0]).lower())
        ],
    )


def realtime_catalog(app: Any) -> dict[str, Any]:
    return {
        "socket_io_mounts": [route.path for route in app.routes if isinstance(route, Mount) and route.path == "/ws"],
        "websocket_paths": [route.path for route in app.routes if isinstance(route, APIWebSocketRoute)],
        "note": (
            "Socket.IO and WebSocket sessions are separate from the HTTP API reference. "
            "Use their protocol-specific authentication; personal API-key support must "
            "not be assumed. Terminal WebSockets require a verified session JWT in the "
            "first auth message. Streaming HTTP responses remain documented on their HTTP endpoints."
        ),
    }


def resolve_schema(value: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    reference = value.get("$ref", "")
    if not reference.startswith("#/"):
        return value
    resolved: Any = root
    for part in reference[2:].split("/"):
        if not isinstance(resolved, dict):
            return {}
        resolved = resolved.get(part.replace("~1", "/").replace("~0", "~"), {})
    return resolved if isinstance(resolved, dict) else {}


def example_value(
    value: dict[str, Any], root: dict[str, Any], name: str = "", depth: int = 0
) -> Any:
    if SECRET_FIELD.search(name):
        return "YOUR_" + re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")
    if depth > 5:
        return {}
    value = resolve_schema(value, root)
    for field in ("example", "default", "const"):
        if field in value and value[field] is not None:
            return deepcopy(value[field])
    if value.get("enum"):
        return value["enum"][0]
    for alternatives in ("anyOf", "oneOf"):
        if value.get(alternatives):
            candidates = [item for item in value[alternatives] if item.get("type") != "null"]
            return example_value(candidates[0], root, name, depth + 1) if candidates else None
    if value.get("allOf"):
        merged = {}
        for part in value["allOf"]:
            sample = example_value(part, root, name, depth + 1)
            if isinstance(sample, dict):
                merged.update(sample)
        return merged
    kind = value.get("type")
    if kind == "object" or "properties" in value:
        return {
            key: example_value(child, root, key, depth + 1)
            for key, child in list(value.get("properties", {}).items())[:30]
            if not child.get("readOnly")
        }
    if kind == "array":
        return [example_value(value.get("items", {}), root, name, depth + 1)]
    if kind == "boolean":
        return False
    if kind in {"integer", "number"}:
        return value.get("minimum", 0)
    if kind == "null":
        return None
    if kind == "string":
        return {"date-time": "2026-01-01T00:00:00Z", "date": "2026-01-01",
                "email": "user@example.com", "binary": "YOUR_FILE"}.get(value.get("format"), "string")
    return {}


def _safe_example(value: Any, name: str = "") -> Any:
    if SECRET_FIELD.search(name):
        return "YOUR_" + re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")
    if isinstance(value, dict):
        return {key: _safe_example(child, key) for key, child in value.items()}
    if isinstance(value, list):
        return [_safe_example(child) for child in value]
    return value


def _string_value(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _is_binary_schema(value: dict[str, Any], root: dict[str, Any], depth: int = 0) -> bool:
    if depth > 5:
        return False
    value = resolve_schema(value, root)
    if value.get("format") == "binary" or value.get("contentMediaType") == "application/octet-stream":
        return True
    if value.get("type") == "array":
        return _is_binary_schema(value.get("items", {}), root, depth + 1)
    return any(
        _is_binary_schema(child, root, depth + 1)
        for key in ("anyOf", "oneOf") for child in value.get(key, [])
    )


def build_postman(schema: dict[str, Any]) -> dict[str, Any]:
    """Export request templates only; credentials and the target host stay unset."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path, operations in schema.get("paths", {}).items():
        for method in HTTP_METHODS:
            operation = operations.get(method)
            if not isinstance(operation, dict):
                continue
            parameters = [
                resolve_schema(parameter, schema)
                for parameter in operations.get("parameters", []) + operation.get("parameters", [])
            ]
            variables = [
                {"key": parameter["name"], "value": "", "description": parameter.get("description", "Fill in this resource identifier.")}
                for parameter in parameters if parameter.get("in") == "path"
            ]
            query = [
                {
                    "key": parameter["name"],
                    "value": _string_value(example_value(parameter.get("schema", {}), schema, parameter["name"])),
                    "disabled": not parameter.get("required", False),
                    "description": parameter.get("description", ""),
                }
                for parameter in parameters if parameter.get("in") == "query"
            ]
            request_path = re.sub(r"\{([^}]+)\}", lambda match: ":" + match.group(1), path)
            raw_url = "{{base_url}}" + request_path
            required_query = [item for item in query if not item["disabled"]]
            if required_query:
                raw_url += "?" + "&".join(quote(item["key"], safe="") + "=" + quote(item["value"], safe="") for item in required_query)
            request: dict[str, Any] = {
                "method": method.upper(),
                "header": [],
                "url": {
                    "raw": raw_url, "host": ["{{base_url}}"],
                    "path": request_path.lstrip("/").split("/"),
                    "query": query, "variable": variables,
                },
                "description": "\n\n".join(filter(None, [
                    operation.get("description"),
                    operation.get("x-abit-auth-note"),
                    "API key access: " + str(operation.get("x-abit-api-key-access", "unknown")) + ".",
                    "Template only. Replace placeholders and review this operation before sending it.",
                ])),
            }
            security = operation.get("security") or []
            if not any("ABITBearer" in item for item in security):
                # Never inherit the personal API key for OAuth/service/webhook routes.
                request["auth"] = {"type": "noauth"}
            for parameter in parameters:
                if parameter.get("in") == "header" and parameter.get("name", "").lower() != "authorization":
                    request["header"].append({
                        "key": parameter["name"],
                        "value": _string_value(example_value(parameter.get("schema", {}), schema, parameter["name"])),
                        "disabled": not parameter.get("required", False),
                    })
            body = resolve_schema(operation.get("requestBody", {}), schema)
            content = body.get("content", {})
            if "application/json" in content:
                media = content["application/json"]
                sample = _safe_example(media["example"]) if "example" in media else example_value(media.get("schema", {}), schema)
                request["body"] = {"mode": "raw", "raw": json.dumps(sample, indent=2, ensure_ascii=False),
                                   "options": {"raw": {"language": "json"}}}
                request["header"].append({"key": "Content-Type", "value": "application/json"})
            else:
                form_type = next((kind for kind in ("multipart/form-data", "application/x-www-form-urlencoded") if kind in content), None)
                if form_type:
                    form_schema = resolve_schema(content[form_type].get("schema", {}), schema)
                    fields = []
                    for name, child in form_schema.get("properties", {}).items():
                        definition = resolve_schema(child, schema)
                        is_file = _is_binary_schema(definition, schema)
                        field = {"key": name, "type": "file" if is_file else "text",
                                 "disabled": name not in form_schema.get("required", [])}
                        if is_file:
                            field["src"] = []
                        else:
                            field["value"] = _string_value(example_value(child, schema, name))
                        fields.append(field)
                    mode = "formdata" if form_type == "multipart/form-data" else "urlencoded"
                    request["body"] = {"mode": mode, mode: fields}
            group = (operation.get("tags") or ["system"])[0]
            grouped[group].append({
                "name": f"{method.upper()} {path} - {operation.get('summary', '')}".rstrip(" -"),
                "request": request, "response": [],
            })
    return {
        "info": {
            "name": "ABIT.AI API",
            "description": (
                "Generated from registered application routes. Set base_url to your ABIT.AI "
                "origin (without a trailing slash) and api_key to your own personal key. "
                "Exports contain no credentials. Roles, ownership and API-key policy still apply. "
                "OAuth, SCIM and webhook requests require their separate authentication."
            ),
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "auth": {"type": "bearer", "bearer": [{"key": "token", "value": "{{api_key}}", "type": "string"}]},
        "variable": [
            {"key": "base_url", "value": "", "type": "string",
             "description": "Your ABIT.AI application origin; include any deployment path prefix; no trailing slash."},
            {"key": "api_key", "value": "", "type": "string",
             "description": "Your own personal API key, or a valid session bearer token when appropriate."},
        ],
        "item": [
            {"name": GROUP_LABELS.get(group, group.replace("-", " ").title()), "item": items}
            for group, items in sorted(grouped.items(), key=lambda pair: GROUP_LABELS.get(pair[0], pair[0]).lower())
        ],
    }
