from __future__ import annotations

from contextvars import ContextVar
import logging
from time import perf_counter
from typing import Any
from uuid import uuid4

import httpx
from starlette.datastructures import Headers


# Inherit Uvicorn's configured INFO handler in standalone and mounted deployments.
logger = logging.getLogger("uvicorn.error.mcp_auth")


class AuthError(Exception):
    """Missing or malformed inbound MCP authorization."""


_authorization_header: ContextVar[str | None] = ContextVar("magic_hour_authorization_header", default=None)
_request_id: ContextVar[str] = ContextVar("magic_hour_request_id", default="-")


def current_authorization_header() -> str:
    header = _authorization_header.get()
    if not header:
        logger.warning("auth_rejected request_id=%s reason=missing", _request_id.get())
        raise AuthError("Missing Authorization header. Send 'Authorization: Bearer <magic_hour_api_key>'.")

    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        logger.warning("auth_rejected request_id=%s reason=malformed scheme=%s", _request_id.get(), _safe_auth_scheme(header))
        raise AuthError("Missing or malformed Authorization header. Send 'Authorization: Bearer <magic_hour_api_key>'.")

    return f"Bearer {token.strip()}"


def _safe_auth_scheme(header: str | None) -> str:
    """Classify authorization without logging any user-supplied credential text."""
    if not header:
        return "none"
    scheme, _, _ = header.partition(" ")
    return "bearer" if scheme.lower() == "bearer" else "other"


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
        request_id = uuid4().hex
        method = scope.get("method", "UNKNOWN")
        path = scope.get("path", "")
        started_at = perf_counter()
        status_code: int | None = None

        authorization_token = _authorization_header.set(header)
        request_id_token = _request_id.set(request_id)

        logger.info(
            "request_started request_id=%s method=%s path=%s auth_present=%s auth_scheme=%s",
            request_id,
            method,
            path,
            str(bool(header)).lower(),
            _safe_auth_scheme(header),
        )

        async def send_with_status(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_with_status)
        except Exception as error:
            logger.error(
                "request_failed request_id=%s method=%s path=%s status=%s exception_type=%s latency_ms=%.1f",
                request_id,
                method,
                path,
                status_code if status_code is not None else "none",
                type(error).__name__,
                (perf_counter() - started_at) * 1000,
            )
            raise
        else:
            logger.info(
                "request_completed request_id=%s method=%s path=%s status=%s latency_ms=%.1f",
                request_id,
                method,
                path,
                status_code if status_code is not None else "none",
                (perf_counter() - started_at) * 1000,
            )
        finally:
            _request_id.reset(request_id_token)
            _authorization_header.reset(authorization_token)
