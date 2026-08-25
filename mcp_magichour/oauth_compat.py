from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import html
import logging
import os
import re
import secrets
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from threading import Lock
from time import monotonic
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import httpx
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools.base import Tool, ToolResult
from mcp.types import TextContent
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import BaseRoute, Mount, Route

from .openapi_auth import AuthError, current_authorization_header


CODE_TTL_SECONDS = 300
MAX_FORM_BYTES = 16 * 1024
MAX_PENDING_CODES = 1_000
MAX_CODES_PER_API_KEY = 3
MAX_CONCURRENT_VALIDATIONS = 10
API_KEY_VERIFICATION_ERROR = (
    "We couldn't verify this API key. Check that you copied the full key and try again."
)
OAUTH_CLIENT_ID = "magic-hour-mcp"
ALLOWED_REDIRECT_URIS = [
    "https://claude.ai/api/mcp/auth_callback",
    "https://chatgpt.com/connector/oauth/5swpyzyTpmje",
    "http://localhost:8787/callback",
]
PKCE_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
CHALLENGE_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
ApiKeyValidator = Callable[[str], Awaitable[bool]]
logger = logging.getLogger("uvicorn.error.mcp_oauth")
OAUTH_SECURITY_SCHEMES = [{"type": "oauth2", "scopes": []}]


class OAuthCapacityError(Exception):
    pass


@dataclass(frozen=True)
class AuthorizationCode:
    client_id: str
    api_key: str
    redirect_uri: str
    code_challenge: str
    resource: str | None
    expires_at: float


@dataclass(frozen=True)
class RegisteredClient:
    client_id: str
    redirect_uris: tuple[str, ...]


class AuthorizationCodeStore:
    """Small process-local store for short-lived, single-use codes."""

    def __init__(self, ttl_seconds: int = CODE_TTL_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._codes: dict[str, AuthorizationCode] = {}
        self._lock = Lock()

    def issue(
        self,
        *,
        client_id: str = OAUTH_CLIENT_ID,
        api_key: str,
        redirect_uri: str,
        code_challenge: str,
        resource: str | None,
    ) -> str:
        code = secrets.token_urlsafe(32)
        now = monotonic()
        authorization_code = AuthorizationCode(
            client_id=client_id,
            api_key=api_key,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            resource=resource,
            expires_at=now + self.ttl_seconds,
        )
        with self._lock:
            self._remove_expired(now)
            if sum(value.api_key == api_key for value in self._codes.values()) >= MAX_CODES_PER_API_KEY:
                raise OAuthCapacityError
            if len(self._codes) >= MAX_PENDING_CODES:
                raise OAuthCapacityError
            self._codes[code] = authorization_code
        return code

    def consume(self, code: str) -> AuthorizationCode | None:
        now = monotonic()
        with self._lock:
            authorization_code = self._codes.pop(code, None)
            self._remove_expired(now)
        if authorization_code is None or authorization_code.expires_at <= now:
            return None
        return authorization_code

    def get(self, code: str) -> AuthorizationCode | None:
        now = monotonic()
        with self._lock:
            self._remove_expired(now)
            return self._codes.get(code)

    def has_capacity(self, api_key: str) -> bool:
        now = monotonic()
        with self._lock:
            self._remove_expired(now)
            return (
                len(self._codes) < MAX_PENDING_CODES
                and sum(value.api_key == api_key for value in self._codes.values()) < MAX_CODES_PER_API_KEY
            )

    def _remove_expired(self, now: float) -> None:
        for code, value in list(self._codes.items()):
            if value.expires_at <= now:
                del self._codes[code]


@dataclass(frozen=True)
class OAuthSettings:
    issuer_url: str | None = None
    resource_url: str | None = None
    api_base_url: str = "https://api.magichour.ai"
    validation_path: str = "/v1/ai-image-generator"

    @classmethod
    def from_env(cls) -> "OAuthSettings":
        return cls(
            issuer_url=os.getenv("MCP_OAUTH_ISSUER_URL"),
            resource_url=os.getenv("MCP_OAUTH_RESOURCE_URL"),
            api_base_url=os.getenv("MAGIC_HOUR_API_BASE_URL", "https://api.magichour.ai"),
            validation_path=os.getenv(
                "MAGIC_HOUR_OAUTH_VALIDATION_PATH",
                "/v1/ai-image-generator",
            ),
        )


class OAuthCompatibilityServer:
    def __init__(
        self,
        *,
        settings: OAuthSettings | None = None,
        api_key_validator: ApiKeyValidator | None = None,
        code_store: AuthorizationCodeStore | None = None,
    ) -> None:
        self.settings = settings or OAuthSettings.from_env()
        _validate_settings(self.settings)
        self.codes = code_store or AuthorizationCodeStore()
        self.validate_api_key = api_key_validator or self._validate_api_key
        self._validation_slots = asyncio.Semaphore(MAX_CONCURRENT_VALIDATIONS)
        self._registered_clients: dict[str, RegisteredClient] = {}

    def routes(self) -> list[Route]:
        return [
            Route("/authorize", self.authorize, methods=["GET", "POST"]),
            Route("/token", self.token, methods=["POST"]),
            Route("/register", self.register, methods=["POST"]),
            Route("/.well-known/oauth-authorization-server", self.authorization_server_metadata),
            Route("/.well-known/oauth-protected-resource", self.protected_resource_metadata),
            Route("/.well-known/oauth-protected-resource/mcp", self.protected_resource_metadata),
        ]

    async def authorize(self, request: Request) -> Response:
        try:
            params = request.query_params if request.method == "GET" else await _read_form(request)
            authorization = self._validate_authorization_request(params, self.resource(request))
        except OAuthRequestError as error:
            return _oauth_error(error.error, error.description)

        page_params = {
            **authorization,
            "response_type": "code",
            "code_challenge_method": "S256",
            "state": params.get("state"),
        }
        if request.method == "GET":
            return _authorization_page(page_params)

        api_key = params.get("api_key", "").strip()
        if not api_key:
            return _authorization_page(page_params, "API key is required.", status_code=400)
        if len(api_key) > 512 or any(character.isspace() for character in api_key):
            return _authorization_page(page_params, API_KEY_VERIFICATION_ERROR, status_code=401)
        if not self.codes.has_capacity(api_key):
            return _authorization_page(page_params, "Server is busy. Try again.", status_code=503)

        try:
            await asyncio.wait_for(self._validation_slots.acquire(), timeout=0.1)
        except TimeoutError:
            return _authorization_page(page_params, "Server is busy. Try again.", status_code=503)
        try:
            try:
                valid = await self.validate_api_key(api_key)
            finally:
                self._validation_slots.release()
        except httpx.HTTPError:
            return _authorization_page(
                page_params,
                "Could not validate API key. Try again.",
                status_code=503,
            )
        if not valid:
            return _authorization_page(page_params, API_KEY_VERIFICATION_ERROR, status_code=401)

        try:
            code = self.codes.issue(
                client_id=authorization["client_id"],
                api_key=api_key,
                redirect_uri=authorization["redirect_uri"],
                code_challenge=authorization["code_challenge"],
                resource=authorization["resource"],
            )
        except OAuthCapacityError:
            return _authorization_page(page_params, "Server is busy. Try again.", status_code=503)
        location = _add_query(authorization["redirect_uri"], {"code": code, "state": params.get("state")})
        return RedirectResponse(location, status_code=302, headers={"Cache-Control": "no-store"})

    async def token(self, request: Request) -> Response:
        try:
            params = await _read_form(request)
        except OAuthRequestError as error:
            return _token_rejection("malformed_form", error.error, error.description)

        if params.get("grant_type") != "authorization_code":
            return _token_rejection(
                "unsupported_grant_type",
                "unsupported_grant_type",
                "grant_type must be authorization_code",
            )

        code = params.get("code", "")
        authorization = self.codes.get(code)
        if authorization is None:
            return _token_rejection(
                "code_invalid_or_expired",
                "invalid_grant",
                "Authorization code is invalid or expired",
            )

        client_id = params.get("client_id", "")
        client = self._client(client_id)
        if client is None:
            return _token_rejection(
                "client_mismatch",
                "invalid_grant",
                "Authorization code does not match client",
            )
        if not hmac.compare_digest(client_id, authorization.client_id):
            return _token_rejection(
                "client_mismatch",
                "invalid_grant",
                "Authorization code does not match client",
            )
        if not hmac.compare_digest(params.get("redirect_uri", ""), authorization.redirect_uri):
            return _token_rejection(
                "redirect_uri_mismatch",
                "invalid_grant",
                "Authorization code does not match redirect_uri",
            )
        token_resource = params.get("resource")
        if authorization.resource:
            resource_mismatch = not token_resource or not _same_resource(
                token_resource,
                authorization.resource,
            )
        else:
            resource_mismatch = bool(token_resource) and not _same_resource(
                token_resource,
                self.resource(request),
            )
        if resource_mismatch:
            return _token_rejection(
                "resource_mismatch",
                "invalid_grant",
                "Authorization code does not match resource",
            )

        verifier = params.get("code_verifier", "")
        if not PKCE_RE.fullmatch(verifier) or not hmac.compare_digest(
            _pkce_challenge(verifier), authorization.code_challenge
        ):
            return _token_rejection("pkce_failed", "invalid_grant", "PKCE verification failed")

        if self.codes.consume(code) is not authorization:
            return _token_rejection(
                "code_already_consumed",
                "invalid_grant",
                "Authorization code is invalid or expired",
            )

        return JSONResponse(
            {"access_token": authorization.api_key, "token_type": "Bearer"},
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )

    async def authorization_server_metadata(self, request: Request) -> Response:
        issuer = self.issuer(request)
        return JSONResponse(
            {
                "issuer": issuer,
                "authorization_endpoint": f"{issuer}/authorize",
                "token_endpoint": f"{issuer}/token",
                "registration_endpoint": f"{issuer}/register",
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code"],
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none"],
            }
        )

    async def register(self, request: Request) -> Response:
        try:
            payload = await request.json()
        except (ValueError, UnicodeDecodeError):
            return _oauth_error("invalid_client_metadata", "Request body must be JSON")

        redirect_uris = payload.get("redirect_uris") if isinstance(payload, dict) else None
        if (
            not isinstance(redirect_uris, list)
            or not redirect_uris
            or any(not isinstance(uri, str) or not _valid_redirect_uri(uri) for uri in redirect_uris)
            or len(set(redirect_uris)) != len(redirect_uris)
        ):
            return _oauth_error(
                "invalid_redirect_uri",
                "redirect_uris must contain unique HTTPS or localhost callback URLs",
            )

        client_id = f"mcp_{secrets.token_urlsafe(24)}"
        self._registered_clients[client_id] = RegisteredClient(
            client_id=client_id,
            redirect_uris=tuple(redirect_uris),
        )
        return JSONResponse(
            {
                "client_id": client_id,
                "client_name": payload.get("client_name"),
                "redirect_uris": redirect_uris,
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
            status_code=201,
        )

    async def protected_resource_metadata(self, request: Request) -> Response:
        issuer = self.issuer(request)
        return JSONResponse(
            {
                "resource": self.resource(request),
                "authorization_servers": [issuer],
                "bearer_methods_supported": ["header"],
            }
        )

    def issuer(self, request: Request) -> str:
        return (self.settings.issuer_url or str(request.base_url)).rstrip("/")

    def resource(self, request: Request) -> str:
        return (self.settings.resource_url or self.issuer(request)).rstrip("/")

    def _validate_authorization_request(
        self,
        params: Mapping[str, str],
        expected_resource: str,
    ) -> dict[str, str | None]:
        client_id = params.get("client_id", "")
        redirect_uri = params.get("redirect_uri", "")
        challenge = params.get("code_challenge", "")
        resource = params.get("resource")

        if params.get("response_type") != "code":
            raise OAuthRequestError("unsupported_response_type", "response_type must be code")
        client = self._client(client_id)
        if client is None or redirect_uri not in client.redirect_uris:
            raise OAuthRequestError("invalid_request", "Unknown client or redirect_uri")
        if params.get("code_challenge_method") != "S256" or not CHALLENGE_RE.fullmatch(challenge):
            raise OAuthRequestError("invalid_request", "PKCE S256 code_challenge is required")
        if resource and not _same_resource(resource, expected_resource):
            raise OAuthRequestError("invalid_target", "Unknown resource")

        return {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": challenge,
            "resource": resource,
        }

    def _client(self, client_id: str) -> RegisteredClient | None:
        if client_id == OAUTH_CLIENT_ID:
            return RegisteredClient(client_id=client_id, redirect_uris=tuple(ALLOWED_REDIRECT_URIS))
        return self._registered_clients.get(client_id)

    async def _validate_api_key(self, api_key: str) -> bool:
        async with httpx.AsyncClient(base_url=self.settings.api_base_url, timeout=10.0) as client:
            response = await client.post(
                self.settings.validation_path,
                headers={"Authorization": f"Bearer {api_key}"},
                json={},
            )
        # Empty body cannot create a project or spend credits. The documented
        # endpoint returns 400 only after bearer authentication succeeds.
        if response.status_code == 400:
            return True
        if response.status_code in {401, 403, 404}:
            return False
        response.raise_for_status()
        return False


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
            current_authorization_header()
        except AuthError:
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
        return await call_next(context)


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


class OAuthRequestError(Exception):
    def __init__(self, error: str, description: str) -> None:
        self.error = error
        self.description = description
        super().__init__(description)


def create_oauth_compatibility_app(
    mcp_app: Any,
    *,
    settings: OAuthSettings | None = None,
    api_key_validator: ApiKeyValidator | None = None,
    public_routes: Sequence[BaseRoute] = (),
) -> Starlette:
    oauth = OAuthCompatibilityServer(
        settings=settings,
        api_key_validator=api_key_validator,
    )
    protected_mcp = MCPBearerChallengeMiddleware(mcp_app, oauth)
    return Starlette(
        routes=[*oauth.routes(), *public_routes, Mount("/", app=protected_mcp)],
        lifespan=mcp_app.lifespan,
    )


async def _read_form(request: Request) -> dict[str, str]:
    content_length = request.headers.get("content-length")
    if content_length and (not content_length.isdigit() or int(content_length) > MAX_FORM_BYTES):
        raise OAuthRequestError("invalid_request", "Request body is too large")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_FORM_BYTES:
            raise OAuthRequestError("invalid_request", "Request body is too large")
        body.extend(chunk)
    try:
        parsed = parse_qs(bytes(body).decode("utf-8"), keep_blank_values=True, max_num_fields=20)
    except (UnicodeDecodeError, ValueError):
        raise OAuthRequestError("invalid_request", "Malformed form body") from None
    if any(len(values) != 1 for values in parsed.values()):
        raise OAuthRequestError("invalid_request", "OAuth parameters must not be repeated")
    return {name: values[0] for name, values in parsed.items()}


def _authorization_page(
    authorization: Mapping[str, str | None],
    error: str | None = None,
    *,
    status_code: int = 200,
) -> HTMLResponse:
    script_nonce = secrets.token_urlsafe(18)
    fields = "".join(
        f'<input type="hidden" name="{html.escape(name)}" value="{html.escape(value or "")}">'
        for name, value in authorization.items()
        if value is not None
    )
    error_html = (
        f'<p class="error" id="api-key-error" role="alert">'
        f"{html.escape(error)}</p>"
        if error
        else ""
    )
    error_attributes = ' aria-invalid="true" aria-describedby="api-key-error"' if error else ""
    body = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>Connect to Magic Hour MCP</title>
<style>
  :root {{
    color-scheme: dark;
    --background: hsl(234.55 31.43% 6.86%);
    --foreground: white;
    --card: hsl(235.71 25.93% 10.59%);
    --card-foreground: white;
    --primary: hsl(259.29 100% 50%);
    --primary-foreground: white;
    --muted: hsl(235.71 21.21% 12.94%);
    --muted-foreground: hsl(235 11.11% 57.65%);
    --border: hsl(232.17 22.77% 19.8%);
    --input: hsl(236 19% 15%);
    --ring: white;
    --destructive: hsl(0 100% 68.24%);
    --radius: .625rem;
    font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; min-height: 100vh; min-height: 100dvh; display: grid; place-items: center;
    padding: 24px; color: var(--foreground); background: var(--background);
  }}
  .card {{
    width: min(100%, 440px); padding: 36px; color: var(--card-foreground); background: var(--card);
    border: 1px solid var(--border); border-radius: var(--radius); box-shadow: 0 12px 32px rgba(0, 0, 0, .24);
  }}
  .brand {{
    display: flex; align-items: center; gap: 9px; margin-bottom: 30px;
    color: var(--card-foreground); font-size: 14px; font-weight: 650;
  }}
  .brand-logo {{ width: 24px; height: 24px; flex: 0 0 auto; border-radius: calc(var(--radius) - .1875rem); }}
  h1 {{ margin: 0; font-size: 24px; line-height: 1.2; letter-spacing: -.025em; }}
  .intro {{ margin: 12px 0 26px; color: var(--muted-foreground); font-size: 14px; line-height: 1.55; }}
  .error {{ margin: 8px 0 0; color: var(--destructive); font-size: 12px; line-height: 1.5; }}
  .field-header {{ display: flex; align-items: baseline; justify-content: space-between; flex-wrap: wrap; gap: 6px 16px; margin-bottom: 8px; }}
  label {{ font-size: 13px; font-weight: 650; }}
  .field-header a {{ color: var(--muted-foreground); font-size: 12px; text-underline-offset: 3px; }}
  .field-header a:hover {{ color: var(--foreground); }}
  .field-header a:focus-visible {{ outline: 2px solid var(--ring); outline-offset: 2px; border-radius: 2px; }}
  .api-key-control {{ position: relative; }}
  #api-key {{
    width: 100%; height: 46px; padding: 0 68px 0 13px; color: var(--foreground); background: var(--input);
    border: 1px solid var(--border); border-radius: var(--radius); outline: none; font: inherit;
  }}
  #api-key::placeholder {{ color: var(--muted-foreground); opacity: 1; }}
  #api-key:focus-visible {{ border-color: var(--ring); box-shadow: 0 0 0 2px var(--ring); }}
  input[aria-invalid="true"] {{ border-color: var(--destructive); }}
  .visibility-toggle {{
    position: absolute; top: 7px; right: 7px; width: auto; min-height: 32px; padding: 0 9px;
    border: 0; border-radius: calc(var(--radius) - .1875rem); color: var(--muted-foreground);
    background: transparent; font: inherit; font-size: 12px; font-weight: 650; cursor: pointer;
  }}
  .visibility-toggle:hover {{ color: var(--foreground); background: var(--muted); }}
  .visibility-toggle:focus-visible {{ outline: 2px solid var(--ring); outline-offset: 1px; }}
  .connect-button {{
    width: 100%; min-height: 46px; margin-top: 22px; display: inline-flex; align-items: center;
    justify-content: center; border: 0; border-radius: var(--radius);
    color: var(--primary-foreground); background: var(--primary);
    font-family: inherit; font-size: 14px; font-weight: 650; line-height: 1.25; cursor: pointer;
  }}
  .connect-button:not(:disabled):hover {{ box-shadow: inset 0 0 0 1px var(--primary-foreground); }}
  .connect-button:focus-visible {{ outline: 2px solid var(--ring); outline-offset: 3px; }}
  .connect-button:disabled {{ cursor: wait; opacity: .72; }}
  .button-label, .button-loading {{
    font-family: inherit; font-size: inherit; font-weight: inherit; line-height: inherit;
  }}
  .button-label {{ display: inline-flex; align-items: center; justify-content: center; }}
  .button-loading {{ display: inline-flex; align-items: center; justify-content: center; gap: 8px; }}
  .button-label[hidden], .button-loading[hidden] {{ display: none; }}
  .spinner {{
    width: 14px; height: 14px; border: 2px solid currentColor; border-right-color: transparent;
    border-radius: 50%; animation: spin .7s linear infinite;
  }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
  @media (prefers-reduced-motion: reduce) {{ .spinner {{ animation: none; }} }}
  @media (max-width: 480px) {{ body {{ padding: 16px; }} .card {{ padding: 28px 24px; }} }}
</style>
</head><body>
<main class="card">
  <div class="brand"><img class="brand-logo" src="/favicon.ico" alt="" width="24" height="24">Magic Hour</div>
  <h1>Connect to Magic Hour MCP</h1>
  <p class="intro">Enter your API key to use Magic Hour tools.</p>
  <form id="authorization-form" method="post" action="" autocomplete="off">{fields}
    <div class="field-header">
      <label for="api-key">API key</label>
      <a href="https://magichour.ai/developer?tab=api-keys" target="_blank" rel="noopener noreferrer">Create your API key</a>
    </div>
    <div class="api-key-control">
    <input id="api-key" name="api_key" type="password" placeholder="mhk_live_…" required autocomplete="off" autocapitalize="none" spellcheck="false" data-1p-ignore="true" data-lpignore="true" data-bwignore="true" autofocus{error_attributes}>
      <button id="api-key-visibility" class="visibility-toggle" type="button" aria-label="Show API key" aria-pressed="false">Show</button>
    </div>
    {error_html}
    <button id="connect-button" class="connect-button" type="submit">
      <span class="button-label">Connect</span>
      <span class="button-loading" hidden><span class="spinner" aria-hidden="true"></span><span>Connecting…</span></span>
    </button>
  </form>
</main>
<script nonce="{script_nonce}">
  const form = document.getElementById("authorization-form");
  const apiKeyInput = document.getElementById("api-key");
  const visibilityButton = document.getElementById("api-key-visibility");
  const connectButton = document.getElementById("connect-button");
  const label = connectButton.querySelector(".button-label");
  const loading = connectButton.querySelector(".button-loading");
  visibilityButton.addEventListener("click", () => {{
    const revealing = apiKeyInput.type === "password";
    apiKeyInput.type = revealing ? "text" : "password";
    visibilityButton.textContent = revealing ? "Hide" : "Show";
    visibilityButton.setAttribute("aria-label", revealing ? "Hide API key" : "Show API key");
    visibilityButton.setAttribute("aria-pressed", String(revealing));
  }});
  form.addEventListener("submit", () => {{
    connectButton.disabled = true;
    connectButton.setAttribute("aria-busy", "true");
    label.hidden = true;
    loading.hidden = false;
  }});
</script>
</body></html>"""
    return HTMLResponse(
        body,
        status_code=status_code,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; "
                f"script-src 'nonce-{script_nonce}'; base-uri 'none'; frame-ancestors 'none'"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
        },
    )


def _token_error(error: str, description: str) -> JSONResponse:
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=400,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


def _token_rejection(reason: str, error: str, description: str) -> JSONResponse:
    logger.warning("token_rejected reason=%s", reason)
    return _token_error(error, description)


def _oauth_error(error: str, description: str) -> JSONResponse:
    return JSONResponse({"error": error, "error_description": description}, status_code=400)


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _add_query(uri: str, values: Mapping[str, str | None]) -> str:
    parts = urlsplit(uri)
    query = parse_qs(parts.query, keep_blank_values=True)
    for name, value in values.items():
        if value is not None:
            query[name] = [value]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), ""))


def _same_resource(left: str, right: str) -> bool:
    left_parts = urlsplit(left)
    right_parts = urlsplit(right)
    return (
        left_parts.scheme.lower(),
        left_parts.netloc.lower(),
        left_parts.path.rstrip("/"),
        left_parts.query,
    ) == (
        right_parts.scheme.lower(),
        right_parts.netloc.lower(),
        right_parts.path.rstrip("/"),
        right_parts.query,
    )


def _valid_server_url(uri: str) -> bool:
    parts = urlsplit(uri)
    if not parts.scheme or not parts.netloc or parts.query or parts.fragment or parts.username or parts.password:
        return False
    if parts.scheme == "https":
        return True
    return parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"}


def _valid_redirect_uri(uri: str) -> bool:
    return _valid_server_url(uri)


def _validate_settings(settings: OAuthSettings) -> None:
    for name, value in (
        ("MCP_OAUTH_ISSUER_URL", settings.issuer_url),
        ("MCP_OAUTH_RESOURCE_URL", settings.resource_url),
        ("MAGIC_HOUR_API_BASE_URL", settings.api_base_url),
    ):
        if value and not _valid_server_url(value):
            raise RuntimeError(f"{name} must be an HTTPS URL (or localhost HTTP)")
    if not settings.validation_path.startswith("/") or settings.validation_path.startswith("//"):
        raise RuntimeError("MAGIC_HOUR_OAUTH_VALIDATION_PATH must be a relative absolute-path")
