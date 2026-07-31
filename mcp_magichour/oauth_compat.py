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
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from threading import Lock
from time import monotonic
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route


CODE_TTL_SECONDS = 300
MAX_FORM_BYTES = 16 * 1024
MAX_PENDING_CODES = 1_000
MAX_CODES_PER_API_KEY = 3
MAX_CONCURRENT_VALIDATIONS = 10
CLAUDE_CLIENT_ID = "magic-hour-mcp"
CLAUDE_REDIRECT_URIS = [
    "https://claude.ai/api/mcp/auth_callback",
]
PKCE_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
CHALLENGE_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
ApiKeyValidator = Callable[[str], Awaitable[bool]]
logger = logging.getLogger("uvicorn.error.mcp_oauth")


class OAuthCapacityError(Exception):
    pass


@dataclass(frozen=True)
class AuthorizationCode:
    api_key: str
    redirect_uri: str
    code_challenge: str
    resource: str | None
    expires_at: float


class AuthorizationCodeStore:
    """Small process-local store for short-lived, single-use codes."""

    def __init__(self, ttl_seconds: int = CODE_TTL_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._codes: dict[str, AuthorizationCode] = {}
        self._lock = Lock()

    def issue(
        self,
        *,
        api_key: str,
        redirect_uri: str,
        code_challenge: str,
        resource: str | None,
    ) -> str:
        code = secrets.token_urlsafe(32)
        now = monotonic()
        authorization_code = AuthorizationCode(
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

    def routes(self) -> list[Route]:
        return [
            Route("/authorize", self.authorize, methods=["GET", "POST"]),
            Route("/token", self.token, methods=["POST"]),
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
            return _authorization_page(page_params, "Invalid API key.", status_code=401)
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
            return _authorization_page(page_params, "Invalid API key.", status_code=401)

        try:
            code = self.codes.issue(
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

        if not hmac.compare_digest(params.get("client_id", ""), CLAUDE_CLIENT_ID):
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
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code"],
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none"],
            }
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
        if client_id != CLAUDE_CLIENT_ID or redirect_uri not in CLAUDE_REDIRECT_URIS:
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
    """Require bearer syntax before MCP; key validity remains downstream's job."""

    def __init__(self, app: Any, oauth_server: OAuthCompatibilityServer) -> None:
        self.app = app
        self.oauth_server = oauth_server

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return

        header = next(
            (value.decode("latin-1") for name, value in scope.get("headers", []) if name.lower() == b"authorization"),
            "",
        )
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
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
) -> Starlette:
    oauth = OAuthCompatibilityServer(
        settings=settings,
        api_key_validator=api_key_validator,
    )
    protected_mcp = MCPBearerChallengeMiddleware(mcp_app, oauth)
    return Starlette(
        routes=[*oauth.routes(), Mount("/", app=protected_mcp)],
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
    fields = "".join(
        f'<input type="hidden" name="{html.escape(name)}" value="{html.escape(value or "")}">'
        for name, value in authorization.items()
        if value is not None
    )
    error_html = (
        f'<div class="error" id="api-key-error" role="alert">'
        f'<span class="error-icon" aria-hidden="true">!</span>'
        f"<span>{html.escape(error)}</span></div>"
        if error
        else ""
    )
    error_attributes = ' aria-invalid="true"' if error else ""
    described_by = "api-key-hint api-key-error" if error else "api-key-hint"
    body = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>Connect Magic Hour</title>
<style>
  :root {{ color-scheme: light; font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; min-height: 100vh; min-height: 100dvh; display: grid; place-items: center;
    color: #17151c; background:
      radial-gradient(circle at 12% 16%, rgba(255, 178, 228, .42), transparent 28rem),
      radial-gradient(circle at 88% 82%, rgba(190, 180, 255, .45), transparent 31rem),
      #f8f6fb; padding: 32px 20px;
  }}
  .shell {{
    width: min(100%, 940px); min-height: 560px; display: grid; grid-template-columns: .92fr 1.08fr;
    overflow: hidden; background: rgba(255, 255, 255, .88); border: 1px solid rgba(42, 32, 54, .1);
    border-radius: 28px; box-shadow: 0 28px 80px rgba(43, 28, 60, .16), 0 2px 8px rgba(43, 28, 60, .06);
  }}
  .brand {{
    position: relative; overflow: hidden; display: flex; flex-direction: column; justify-content: space-between;
    padding: 40px; color: #fff; background: linear-gradient(145deg, #1c1724 0%, #31233d 58%, #5b315f 100%);
  }}
  .brand::before, .brand::after {{ content: ""; position: absolute; border-radius: 999px; filter: blur(1px); }}
  .brand::before {{ width: 310px; height: 310px; right: -155px; top: -90px; background: rgba(255, 114, 190, .28); }}
  .brand::after {{ width: 250px; height: 250px; left: -140px; bottom: -100px; background: rgba(131, 111, 255, .3); }}
  .wordmark, .brand-copy {{ position: relative; z-index: 1; }}
  .wordmark {{ display: flex; align-items: center; gap: 12px; font-size: 17px; font-weight: 720; letter-spacing: -.02em; }}
  .mark {{ position: relative; width: 30px; height: 30px; flex: 0 0 auto; transform: rotate(45deg); }}
  .mark span {{ position: absolute; width: 13px; height: 13px; border-radius: 4px; background: linear-gradient(135deg, #ff86cf, #bda8ff); }}
  .mark span:nth-child(1) {{ inset: 0 auto auto 0; }} .mark span:nth-child(2) {{ inset: auto 0 0 auto; }}
  .brand-copy h2 {{ max-width: 300px; margin: 0 0 14px; font-size: clamp(28px, 3.7vw, 40px); line-height: 1.08; letter-spacing: -.045em; }}
  .brand-copy p {{ max-width: 290px; margin: 0; color: rgba(255, 255, 255, .68); font-size: 14px; line-height: 1.65; }}
  .content {{ display: grid; align-content: center; padding: clamp(40px, 7vw, 72px); }}
  .eyebrow {{ margin: 0 0 14px; color: #7e477e; font-size: 12px; font-weight: 750; letter-spacing: .13em; text-transform: uppercase; }}
  h1 {{ margin: 0; font-size: clamp(30px, 4vw, 42px); line-height: 1.1; letter-spacing: -.045em; }}
  .intro {{ margin: 16px 0 30px; color: #69626f; font-size: 15px; line-height: 1.65; }}
  .error {{
    display: flex; align-items: center; gap: 10px; margin: -10px 0 20px; padding: 12px 14px;
    color: #862543; background: #fff1f5; border: 1px solid #f5cad7; border-radius: 12px; font-size: 13px; font-weight: 600;
  }}
  .error-icon {{ display: grid; place-items: center; width: 20px; height: 20px; flex: 0 0 auto; color: #fff; background: #bd365e; border-radius: 50%; font-size: 12px; }}
  label {{ display: block; margin-bottom: 9px; font-size: 13px; font-weight: 700; }}
  input[type="password"] {{
    width: 100%; height: 50px; padding: 0 15px; color: #201b25; background: #fff; border: 1px solid #d9d3df;
    border-radius: 12px; outline: none; font: inherit; box-shadow: 0 1px 2px rgba(28, 18, 35, .04); transition: border-color .16s, box-shadow .16s;
  }}
  input[type="password"]:hover {{ border-color: #bcb2c5; }}
  input[type="password"]:focus-visible {{ border-color: #8d4f91; box-shadow: 0 0 0 4px rgba(141, 79, 145, .14); }}
  input[aria-invalid="true"] {{ border-color: #bd365e; }}
  .hint {{ margin: 9px 0 24px; color: #817986; font-size: 12px; line-height: 1.5; }}
  button {{
    width: 100%; min-height: 50px; border: 0; border-radius: 12px; color: #fff; background: #201825;
    font: inherit; font-size: 14px; font-weight: 750; cursor: pointer; box-shadow: 0 8px 20px rgba(32, 24, 37, .18); transition: transform .16s, background .16s, box-shadow .16s;
  }}
  button:hover {{ background: #3b2941; box-shadow: 0 10px 24px rgba(32, 24, 37, .23); transform: translateY(-1px); }}
  button:active {{ transform: translateY(0); }}
  button:focus-visible {{ outline: 3px solid rgba(141, 79, 145, .35); outline-offset: 3px; }}
  .privacy {{ display: flex; align-items: flex-start; gap: 8px; margin: 22px 0 0; color: #817986; font-size: 11px; line-height: 1.55; }}
  .lock {{ width: 14px; height: 11px; flex: 0 0 auto; margin-top: 3px; border: 1.5px solid #8f8794; border-radius: 3px; position: relative; }}
  .lock::before {{ content: ""; position: absolute; width: 7px; height: 6px; left: 2px; top: -7px; border: 1.5px solid #8f8794; border-bottom: 0; border-radius: 6px 6px 0 0; }}
  @media (max-width: 720px) {{
    body {{ padding: 16px; place-items: start center; }}
    .shell {{ min-height: 0; grid-template-columns: 1fr; border-radius: 22px; }}
    .brand {{ min-height: 160px; padding: 26px; }}
    .brand-copy h2 {{ max-width: 420px; margin-top: 32px; font-size: 26px; }}
    .brand-copy p {{ display: none; }}
    .content {{ padding: 34px 26px 38px; }}
  }}
  @media (max-width: 380px) {{ body {{ padding: 0; }} .shell {{ min-height: 100dvh; border: 0; border-radius: 0; }} }}
  @media (prefers-reduced-motion: reduce) {{ *, *::before, *::after {{ scroll-behavior: auto !important; transition: none !important; }} }}
</style>
</head><body>
<main class="shell">
  <section class="brand" aria-label="Magic Hour">
    <div class="wordmark"><span class="mark" aria-hidden="true"><span></span><span></span></span>Magic Hour</div>
    <div class="brand-copy"><h2>Bring ideas to life.</h2><p>Create studio-quality video, image, and audio with generative AI.</p></div>
  </section>
  <section class="content">
    <p class="eyebrow">Secure connection</p>
    <h1>Connect Magic Hour</h1>
    <p class="intro">Enter your Magic Hour API key to continue. Your key is validated securely and is never shown on this page.</p>
    {error_html}
    <form method="post" action="">{fields}
      <label for="api-key">API key</label>
      <input id="api-key" name="api_key" type="password" required autocomplete="off" autocapitalize="none" spellcheck="false" autofocus aria-describedby="{described_by}"{error_attributes}>
      <p class="hint" id="api-key-hint">You can find your API key in your Magic Hour account.</p>
      <button type="submit">Connect securely</button>
    </form>
    <p class="privacy"><span class="lock" aria-hidden="true"></span><span>Your API key stays private and is only used to authorize this connection.</span></p>
  </section>
</main>
</body></html>"""
    return HTMLResponse(
        body,
        status_code=status_code,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"
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
