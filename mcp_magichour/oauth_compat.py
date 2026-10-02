from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools.base import Tool, ToolResult
from mcp.types import TextContent
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import BaseRoute, Mount, Route

from .openapi_auth import AuthError, current_authorization_header


# The Magic Hour web app is the OAuth authorization server: users sign in and consent there, and
# it returns a Magic Hour API key as the access token. This app is only the protected resource.
AUTHORIZATION_SERVER_URL = "https://magichour.ai"
OAUTH_SECURITY_SCHEMES = [{"type": "oauth2", "scopes": []}]


@dataclass(frozen=True)
class OAuthSettings:
    issuer_url: str | None = None
    resource_url: str | None = None

    @classmethod
    def from_env(cls) -> "OAuthSettings":
        return cls(
            issuer_url=os.getenv("MCP_OAUTH_ISSUER_URL"),
            resource_url=os.getenv("MCP_OAUTH_RESOURCE_URL"),
        )


class OAuthCompatibilityServer:
    def __init__(self, *, settings: OAuthSettings | None = None) -> None:
        self.settings = settings or OAuthSettings.from_env()
        _validate_settings(self.settings)

    def routes(self) -> list[Route]:
        return [
            # Clients on the older MCP auth spec look for authorization server metadata here.
            Route("/.well-known/oauth-authorization-server", self.authorization_server_metadata),
            Route("/.well-known/oauth-protected-resource", self.protected_resource_metadata),
            Route("/.well-known/oauth-protected-resource/mcp", self.protected_resource_metadata),
        ]

    async def authorization_server_metadata(self, request: Request) -> Response:
        return RedirectResponse(
            f"{AUTHORIZATION_SERVER_URL}/.well-known/oauth-authorization-server", status_code=302
        )

    async def protected_resource_metadata(self, request: Request) -> Response:
        return JSONResponse(
            {
                "resource": self.resource(request),
                "authorization_servers": [AUTHORIZATION_SERVER_URL],
                "bearer_methods_supported": ["header"],
            }
        )

    def issuer(self, request: Request) -> str:
        return (self.settings.issuer_url or str(request.base_url)).rstrip("/")

    def resource(self, request: Request) -> str:
        return (self.settings.resource_url or self.issuer(request)).rstrip("/")


class MCPBearerChallengeMiddleware:
    """Preserve HTTP auth challenges while allowing public MCP discovery."""

    def __init__(self, app: Any, oauth_server: OAuthCompatibilityServer) -> None:
        self.app = app
        self.oauth_server = oauth_server

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if scope.get("method") in {"OPTIONS", "POST"}:
            await self.app(scope, receive, send)
            return

        authorization = next(
            (value.decode("latin-1") for name, value in scope.get("headers", []) if name.lower() == b"authorization"),
            None,
        )
        header = authorization or ""
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            if (
                authorization is None
                and scope.get("method") == "GET"
                and scope.get("path") == "/"
                and _accepts_html(scope)
            ):
                response = _setup_page_redirect()
                await response(scope, receive, send)
                return
            request = Request(scope)
            issuer = self.oauth_server.issuer(request)
            response = JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={
                    "WWW-Authenticate": (
                        f'Bearer resource_metadata="{issuer}/.well-known/oauth-protected-resource"'
                    )
                },
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


class _OAuthListedTool(Tool):
    def to_mcp_tool(self, **overrides: Any):
        tool = super().to_mcp_tool(**overrides)
        tool.securitySchemes = OAUTH_SECURITY_SCHEMES
        return tool


class MCPToolOAuthMiddleware(Middleware):
    """Advertise OAuth during discovery and challenge unauthenticated tool calls."""

    async def on_list_tools(self, context: MiddlewareContext, call_next: Any):
        tools = await call_next(context)
        return [
            _OAuthListedTool(**{name: getattr(tool, name) for name in Tool.model_fields})
            for tool in tools
        ]

    async def on_call_tool(self, context: MiddlewareContext, call_next: Any) -> ToolResult:
        try:
            authorization = current_authorization_header()
            return await call_next(context)
        except Exception as error:
            cause: BaseException | None = error
            while cause is not None:
                if isinstance(cause, AuthError) or (
                    isinstance(cause, httpx.HTTPStatusError)
                    and cause.response.status_code == 401
                    and cause.request.headers.get("Authorization") == authorization
                ):
                    break
                cause = cause.__cause__
            else:
                raise
            issuer = (
                OAuthSettings.from_env().issuer_url or str(get_http_request().base_url)
            ).rstrip("/")
            challenge = (
                f'Bearer resource_metadata="{issuer}/.well-known/oauth-protected-resource", '
                'error="invalid_token", error_description="Authentication required"'
            )
            return ToolResult(
                content=[TextContent(type="text", text="Authentication required.")],
                meta={"mcp/www_authenticate": [challenge]},
                is_error=True,
            )


def _accepts_html(scope: Mapping[str, Any]) -> bool:
    accept_values = (
        value.decode("latin-1")
        for name, value in scope.get("headers", [])
        if name.lower() == b"accept"
    )
    for item in ",".join(accept_values).split(","):
        media_type, *parameters = item.split(";")
        if media_type.strip().lower() != "text/html":
            continue
        quality = 1.0
        for parameter in parameters:
            name, separator, value = parameter.partition("=")
            if separator and name.strip().lower() == "q":
                try:
                    quality = float(value.strip())
                except ValueError:
                    quality = 0.0
        if quality > 0:
            return True
    return False


def _setup_page_redirect() -> RedirectResponse:
    return RedirectResponse(
        "https://magichour.ai/mcp",
        status_code=302,
        headers={"Cache-Control": "no-store", "Vary": "Accept"},
    )


def create_oauth_compatibility_app(
    mcp_app: Any,
    *,
    settings: OAuthSettings | None = None,
    public_routes: Sequence[BaseRoute] = (),
) -> Starlette:
    oauth = OAuthCompatibilityServer(settings=settings)
    protected_mcp = MCPBearerChallengeMiddleware(mcp_app, oauth)
    return Starlette(
        routes=[*oauth.routes(), *public_routes, Mount("/", app=protected_mcp)],
        lifespan=mcp_app.lifespan,
    )


def _valid_server_url(uri: str) -> bool:
    parts = urlsplit(uri)
    if not parts.scheme or not parts.netloc or parts.query or parts.fragment or parts.username or parts.password:
        return False
    if parts.scheme == "https":
        return True
    return parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"}


def _validate_settings(settings: OAuthSettings) -> None:
    for name, value in (
        ("MCP_OAUTH_ISSUER_URL", settings.issuer_url),
        ("MCP_OAUTH_RESOURCE_URL", settings.resource_url),
    ):
        if value and not _valid_server_url(value):
            raise RuntimeError(f"{name} must be an HTTPS URL (or localhost HTTP)")
