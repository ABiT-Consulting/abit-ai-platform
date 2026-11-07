"""Authenticated API documentation for ABIT.AI; no operation execution or new grants."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from open_webui.env import VERSION
from open_webui.models.config import Config
from open_webui.utils.access_control import has_permission
from open_webui.utils.auth import get_verified_user
from open_webui.utils.developer_api import (
    build_postman,
    build_schema,
    catalog_counts,
    realtime_catalog,
)


class PrivateDocumentationResponse(JSONResponse):
    def __init__(self, *args, status_code: int = 200, **kwargs):
        kwargs["headers"] = {
            **(kwargs.get("headers") or {}),
            "Cache-Control": "private, no-store",
            "Pragma": "no-cache",
            "Vary": "Authorization, Cookie",
            "X-Content-Type-Options": "nosniff",
        }
        super().__init__(*args, status_code=status_code, **kwargs)


router = APIRouter(default_response_class=PrivateDocumentationResponse)


class AuthenticationMetadata(BaseModel):
    api_keys_enabled: bool
    can_manage_api_key: bool
    endpoint_restrictions: bool
    allowed_endpoints: list[str]
    api_key_path: str
    header: str
    note: str


class CatalogCounts(BaseModel):
    operations: int
    paths: int
    groups: int


class CatalogGroup(BaseModel):
    id: str
    label: str
    count: int


class RealtimeMetadata(BaseModel):
    socket_io_mounts: list[str]
    websocket_paths: list[str]
    note: str


class DeveloperMetadata(BaseModel):
    title: str
    version: str
    role: Literal["user", "admin"]
    base_url: str
    authentication: AuthenticationMetadata
    counts: CatalogCounts
    groups: list[CatalogGroup]
    realtime: RealtimeMetadata
    coverage_note: str


def _relative_base_url(request: Request) -> str:
    """Use only the configured ASGI path prefix, never a request Host header."""
    root = str(request.scope.get("root_path") or "").rstrip("/")
    if not root or not root.startswith("/") or root.startswith("//") or any(
        character in root for character in ("?", "#", "\\", "\r", "\n")
    ):
        return "/"
    return root + "/"


async def _authentication_metadata(user) -> dict[str, Any]:
    values = await Config.get_many(
        "auth.enable_api_keys",
        "user.permissions",
        "auth.api_key.endpoint_restrictions",
        "auth.api_key.allowed_endpoints",
    )
    enabled = bool(values.get("auth.enable_api_keys"))
    permitted = user.role == "admin" or (
        enabled
        and await has_permission(
            user.id, "features.api_keys", values.get("user.permissions")
        )
    )
    return {
        "api_keys_enabled": enabled,
        "can_manage_api_key": enabled and bool(permitted),
        "endpoint_restrictions": bool(values.get("auth.api_key.endpoint_restrictions")),
        "allowed_endpoints": [
            path.strip()
            for path in str(values.get("auth.api_key.allowed_endpoints") or "").split(",")
            if path.strip()
        ],
        "api_key_path": "/api/v1/auths/api_key",
        "header": "Authorization: Bearer YOUR_API_KEY",
        "note": (
            "Keys inherit this account's role and resource permissions. Creating a new "
            "key replaces the account's existing key; deleting it revokes API-key access. "
            "Changing global API-key settings is an administrator action. Endpoint restrictions "
            "match an exact configured path or its child paths."
        ),
    }


async def _schema(request: Request, user) -> tuple[dict[str, Any], dict[str, Any]]:
    authentication = await _authentication_metadata(user)
    schema = build_schema(
        request.app, user.role, str(VERSION), authentication, _relative_base_url(request)
    )
    return schema, authentication


@router.get("/", response_model=DeveloperMetadata, summary="Get the API catalog and current account policy")
async def get_developer_catalog(request: Request, user=Depends(get_verified_user)):
    """Get live endpoint counts, feature groups, API-key policy and realtime coverage."""
    schema, authentication = await _schema(request, user)
    counts, groups = catalog_counts(schema)
    return {
        "title": "ABIT.AI API",
        "version": str(VERSION),
        "role": user.role,
        "base_url": _relative_base_url(request),
        "authentication": authentication,
        "counts": counts,
        "groups": groups,
        "realtime": realtime_catalog(request.app),
        "coverage_note": (
            "Generated from registered HTTP routes, including custom ABiT features. "
            "Administrator-only operations are omitted for regular users. Feature flags, "
            "resource ownership and protocol-specific permissions are still enforced at execution. "
            "Provider proxy bodies depend on their upstream service. Browser-only behavior "
            "and realtime protocols are not additional REST operations."
        ),
    }


@router.get("/openapi.json", summary="Download the current account's OpenAPI schema")
async def get_developer_openapi(request: Request, user=Depends(get_verified_user)) -> dict[str, Any]:
    """Get OpenAPI with request/response schemas and operation authentication notes."""
    schema, _ = await _schema(request, user)
    return PrivateDocumentationResponse(
        schema, headers={"Content-Disposition": 'attachment; filename="abit-ai-openapi.json"'}
    )


@router.get("/postman.json", summary="Download a Postman collection")
async def get_developer_postman(request: Request, user=Depends(get_verified_user)) -> dict[str, Any]:
    """Get credential-free request templates for the same documented routes."""
    schema, _ = await _schema(request, user)
    return PrivateDocumentationResponse(
        build_postman(schema),
        headers={"Content-Disposition": 'attachment; filename="abit-ai-postman.json"'},
    )
