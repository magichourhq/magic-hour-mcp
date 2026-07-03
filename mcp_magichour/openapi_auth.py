from __future__ import annotations

from contextvars import ContextVar
from typing import Any

import httpx
from starlette.datastructures import Headers


class AuthError(Exception):
    """Missing or malformed inbound MCP authorization."""


_authorization_header: ContextVar[str | None] = ContextVar("magic_hour_authorization_header", default=None)


def current_authorization_header() -> str:
    header = _authorization_header.get()
    if not header:
        raise AuthError("Missing Authorization header. Send 'Authorization: Bearer <magic_hour_api_key>'.")

    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError("Missing or malformed Authorization header. Send 'Authorization: Bearer <magic_hour_api_key>'.")

    return f"Bearer {token.strip()}"


class BearerPassthroughAuth(httpx.Auth):
    """Forward the inbound MCP bearer token to the upstream Magic Hour API."""

    def auth_flow(self, request: httpx.Request):
        request.headers["Authorization"] = current_authorization_header()
        yield request


class BearerPassthroughMiddleware:
    """Capture the incoming MCP Authorization header for outbound API calls."""

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        header = Headers(scope=scope).get("authorization")
        token = _authorization_header.set(header)
        try:
            await self.app(scope, receive, send)
        finally:
            _authorization_header.reset(token)
