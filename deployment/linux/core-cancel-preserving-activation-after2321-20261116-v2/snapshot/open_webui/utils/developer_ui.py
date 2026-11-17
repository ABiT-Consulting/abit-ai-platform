"""Optional standalone Developers UI, without importing application configuration.

The normal SvelteKit application remains the fallback when the standalone bundle
is absent. Only the frontend's root index receives the external navigation script.
"""
from __future__ import annotations

import html
import os
from pathlib import Path
import re
from typing import Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.types import Scope

_HEAD_END = re.compile(rb"</head\s*>", re.IGNORECASE)
_ROOT_STATE_KEY = "_abit_developer_ui_root"
_PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store",
    "Pragma": "no-cache",
    "Vary": "Authorization, Cookie",
    "X-Content-Type-Options": "nosniff",
}


def _bundle(frontend_build_dir: str | os.PathLike[str] | None):
    if frontend_build_dir is None:
        return None
    try:
        root = Path(frontend_build_dir).resolve()
        bundle_dir = root / "developer-ui"
        page = bundle_dir / "index.html"
        navigation = bundle_dir / "navigation.js"
        # Do not serve or inject a bundle redirected outside its frontend.
        if (
            bundle_dir.resolve() != bundle_dir
            or page.resolve().parent != bundle_dir
            or navigation.resolve().parent != bundle_dir
            or not page.is_file()
            or not navigation.is_file()
        ):
            return None
        return root, page, navigation
    except (OSError, RuntimeError, ValueError):
        return None


def _private_html(content: bytes, scope: Scope, status_code: int = 200) -> HTMLResponse:
    response = HTMLResponse(content, status_code=status_code, headers=_PRIVATE_HEADERS)
    if scope.get("method") == "HEAD":
        # Keep the GET Content-Length while omitting the HEAD response body.
        response.body = b""
    return response


def _root_prefix(scope: Scope) -> str | None:
    prefix = scope.get("root_path", "")
    if not isinstance(prefix, str):
        return None
    if (
        (prefix and not prefix.startswith("/"))
        or prefix.startswith("//")
        or any(character in prefix for character in ("\\", "?", "#"))
        or any(ord(character) < 32 or ord(character) == 127 for character in prefix)
        or any(segment in {".", ".."} for segment in prefix.split("/"))
    ):
        return None
    return prefix.rstrip("/")


def register_developer_ui(
    app: Any, frontend_build_dir: str | os.PathLike[str]
) -> bool:
    """Register GET/HEAD pages only when both standalone bundle files exist.

    Call before the catch-all frontend mount. No route is added without a bundle,
    so a regular SvelteKit Developers route continues to work in source builds.
    """
    bundle = _bundle(frontend_build_dir)
    if bundle is None:
        return False
    root, page, _navigation = bundle
    setattr(app.state, _ROOT_STATE_KEY, root)

    async def developers_page(request: Request) -> HTMLResponse:
        # A bundle removed after registration must not produce an application error.
        if _bundle(root) is None:
            raise HTTPException(status_code=404)
        try:
            content = page.read_bytes()
        except OSError as error:
            raise HTTPException(status_code=404) from error
        return _private_html(content, request.scope)

    for path in ("/developers", "/developers/"):
        app.add_api_route(
            path,
            developers_page,
            methods=["GET", "HEAD"],
            include_in_schema=False,
            name="abit_developers_ui",
        )
    return True


def render_developer_navigation(
    full_path: str | os.PathLike[str],
    scope: Scope,
    status_code: int = 200,
    *,
    frontend_build_dir: str | os.PathLike[str] | None = None,
) -> HTMLResponse | None:
    """Return an injected root index, or None to preserve StaticFiles behavior.

    Designed for the synchronous StaticFiles.file_response override. The caller
    must delegate to super().file_response whenever this returns None.
    """
    if (
        scope.get("type") != "http"
        or scope.get("method") not in {"GET", "HEAD"}
        or status_code != 200
    ):
        return None
    if frontend_build_dir is None:
        app = scope.get("app")
        frontend_build_dir = getattr(
            getattr(app, "state", None), _ROOT_STATE_KEY, None
        )
    bundle = _bundle(frontend_build_dir)
    prefix = _root_prefix(scope)
    if bundle is None or prefix is None:
        return None
    root, _page, navigation = bundle
    try:
        index = root / "index.html"
        if index.resolve() != index or Path(full_path).resolve() != index:
            return None
        content = index.read_bytes()
        head_end = _HEAD_END.search(content)
        if head_end is None:
            return None
        navigation_stat = navigation.stat()
        version = f"{navigation_stat.st_mtime_ns:x}-{navigation_stat.st_size:x}"
        source = html.escape(
            f"{prefix}/developer-ui/navigation.js?v={version}", quote=True
        )
        script = f'<script defer src="{source}"></script>'.encode("utf-8")
        content = content[: head_end.start()] + script + content[head_end.start() :]
        return _private_html(content, scope, status_code)
    except (OSError, RuntimeError, ValueError, UnicodeError):
        return None
