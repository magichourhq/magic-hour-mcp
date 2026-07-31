import unittest
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from mcp_magichour.oauth_compat import (
    AuthorizationCodeStore,
    MCPBearerChallengeMiddleware,
    OAuthCompatibilityServer,
    OAuthSettings,
    _pkce_challenge,
)


CLIENT_ID = "magic-hour-mcp"
REDIRECT_URI = "https://claude.ai/api/mcp/auth_callback"
RESOURCE = "https://mcp.example/mcp"
VERIFIER = "v" * 64
CHALLENGE = _pkce_challenge(VERIFIER)


class AuthorizationPageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inputs = []
        self.images = []
        self.links = []
        self.visible_text = []
        self._hidden_depth = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input":
            self.inputs.append(attributes)
        if tag == "img":
            self.images.append(attributes)
        if tag == "a":
            self.links.append(attributes)
        if tag in {"style", "script"}:
            self._hidden_depth += 1

    def handle_endtag(self, tag):
        if tag in {"style", "script"}:
            self._hidden_depth -= 1

    def handle_data(self, data):
        if not self._hidden_depth and data.strip():
            self.visible_text.append(data.strip())


class OAuthCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.validated_keys = []

        async def validate_api_key(api_key):
            self.validated_keys.append(api_key)
            return api_key == "sk_valid"

        self.oauth = OAuthCompatibilityServer(
            settings=OAuthSettings(
                issuer_url="https://mcp.example",
                resource_url=RESOURCE,
            ),
            api_key_validator=validate_api_key,
        )

        async def mcp_endpoint(request: Request):
            return JSONResponse({"authorization": request.headers.get("authorization")})

        mcp_app = Starlette(routes=[Route("/", mcp_endpoint)])
        protected_mcp = MCPBearerChallengeMiddleware(mcp_app, self.oauth)
        self.app = Starlette(routes=[*self.oauth.routes(), Mount("/", protected_mcp)])
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://mcp.example",
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    def authorization_params(self, **overrides):
        params = {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "code_challenge": CHALLENGE,
            "code_challenge_method": "S256",
            "resource": RESOURCE,
            "state": "client-state",
        }
        params.update(overrides)
        return params

    async def issue_code(self):
        response = await self.client.post(
            "/authorize",
            data={**self.authorization_params(), "api_key": "sk_valid"},
        )
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlsplit(response.headers["location"]).query)
        self.assertEqual(query["state"], ["client-state"])
        return query["code"][0]

    async def test_authorization_code_pkce_flow_returns_original_key_once(self):
        page = await self.client.get("/authorize", params=self.authorization_params())
        self.assertEqual(page.status_code, 200)
        self.assertIn('name="api_key"', page.text)
        self.assertIn('name="state" value="client-state"', page.text)
        self.assertEqual(page.headers["cache-control"], "no-store")
        self.assertNotIn("form-action", page.headers["content-security-policy"])

        code = await self.issue_code()
        self.assertEqual(self.validated_keys, ["sk_valid"])

        token_request = {
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "code": code,
            "code_verifier": VERIFIER,
            "resource": RESOURCE,
        }
        token = await self.client.post("/token", data=token_request)
        self.assertEqual(token.status_code, 200)
        self.assertEqual(token.json(), {"access_token": "sk_valid", "token_type": "Bearer"})
        self.assertEqual(token.headers["cache-control"], "no-store")

        replay = await self.client.post("/token", data=token_request)
        self.assertEqual(replay.status_code, 400)
        self.assertEqual(replay.json()["error"], "invalid_grant")

    async def test_authorization_page_has_branded_accessible_api_key_form(self):
        page = await self.client.get("/authorize", params=self.authorization_params())
        parser = AuthorizationPageParser()
        parser.feed(page.text)
        visible_text = " ".join(parser.visible_text)
        normalized_text = visible_text.casefold()

        self.assertIn("Magic Hour", visible_text)
        self.assertIn("API key", visible_text)
        self.assertIn("Create your API key", visible_text)
        self.assertNotIn("Find your API key in your Magic Hour account.", visible_text)
        self.assertNotIn("client id", normalized_text)
        self.assertNotIn("callback url", normalized_text)
        self.assertIn('<meta name="viewport"', page.text)
        self.assertIn("<main", page.text)
        self.assertIn("<h1>Connect Magic Hour</h1>", page.text)
        self.assertIn('<label for="api-key">API key</label>', page.text)
        self.assertNotIn("Your API key is validated securely and never displayed.", page.text)
        api_key_input = next(field for field in parser.inputs if field.get("name") == "api_key")
        self.assertEqual(api_key_input["type"], "password")
        self.assertEqual(api_key_input["placeholder"], "mhk_live_…")
        self.assertIn("required", api_key_input)
        self.assertEqual(api_key_input["autocomplete"], "off")

        hidden = {field["name"]: field["value"] for field in parser.inputs if field.get("type") == "hidden"}
        for name, value in self.authorization_params().items():
            self.assertEqual(hidden[name], value)

        self.assertIn(
            {"class": "brand-logo", "src": "/favicon.ico", "alt": "", "width": "24", "height": "24"},
            parser.images,
        )
        self.assertIn(
            {
                "href": "https://magichour.ai/developer?tab=api-keys",
                "target": "_blank",
                "rel": "noopener noreferrer",
            },
            parser.links,
        )

    async def test_authorization_page_security_headers_restrict_content(self):
        page = await self.client.get("/authorize", params=self.authorization_params())

        self.assertEqual(page.headers["cache-control"], "no-store")
        self.assertEqual(page.headers["referrer-policy"], "no-referrer")
        self.assertEqual(page.headers["x-content-type-options"], "nosniff")
        self.assertEqual(page.headers["x-frame-options"], "DENY")
        self.assertEqual(
            page.headers["content-security-policy"],
            "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; "
            "base-uri 'none'; frame-ancestors 'none'",
        )
        self.assertNotIn("<script", page.text)
        self.assertNotIn('src="http', page.text)

    async def test_authorization_page_uses_dark_theme_tokens(self):
        page = await self.client.get("/authorize", params=self.authorization_params())

        self.assertIn('<meta name="color-scheme" content="dark">', page.text)
        for declaration in (
            "--background: hsl(234.55 31.43% 6.86%)",
            "--foreground: white",
            "--card: hsl(235.71 25.93% 10.59%)",
            "--card-foreground: white",
            "--primary: hsl(259.29 100% 50%)",
            "--primary-foreground: white",
            "--muted: hsl(235.71 21.21% 12.94%)",
            "--muted-foreground: hsl(235 11.11% 57.65%)",
            "--border: hsl(232.17 22.77% 19.8%)",
            "--input: hsl(236 19% 15%)",
            "--ring: white",
            "--destructive: hsl(0 100% 68.24%)",
            "--radius: .625rem",
        ):
            with self.subTest(declaration=declaration):
                self.assertIn(declaration, page.text)

    async def test_authorization_error_is_accessible_and_never_reflects_key(self):
        response = await self.client.post(
            "/authorize",
            data={**self.authorization_params(), "api_key": "sk_bad"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertIn('<p class="error" id="api-key-error" role="alert">Invalid API key.</p>', response.text)
        self.assertIn('aria-invalid="true"', response.text)
        self.assertIn('aria-describedby="api-key-hint api-key-error"', response.text)
        self.assertLess(response.text.index('id="api-key"'), response.text.index('id="api-key-error"'))
        self.assertLess(response.text.index('id="api-key-error"'), response.text.index('id="api-key-hint"'))
        error_rule = response.text.split(".error {", 1)[1].split("}", 1)[0]
        self.assertNotIn("background", error_rule)
        self.assertNotIn("border", error_rule)
        self.assertNotIn("padding", error_rule)
        self.assertNotIn("sk_bad", response.text)

    async def test_claude_client_supports_request_without_resource(self):
        params = self.authorization_params()
        del params["resource"]

        page = await self.client.get("/authorize", params=params)
        self.assertEqual(page.status_code, 200)

        authorized = await self.client.post(
            "/authorize",
            data={**params, "api_key": "sk_valid"},
        )
        code = parse_qs(urlsplit(authorized.headers["location"]).query)["code"][0]

        token = await self.client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT_URI,
                "code": code,
                "code_verifier": VERIFIER,
                "resource": RESOURCE,
            },
        )
        self.assertEqual(token.status_code, 200)
        self.assertEqual(token.json()["access_token"], "sk_valid")

    async def test_invalid_redirect_uri_is_rejected_before_key_validation(self):
        response = await self.client.post(
            "/authorize",
            data={
                **self.authorization_params(redirect_uri="https://evil.example/callback"),
                "api_key": "sk_valid",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_request")
        self.assertEqual(self.validated_keys, [])

    async def test_pkce_failure_does_not_redeem_authorization_code(self):
        code = await self.issue_code()
        request = {
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "code": code,
            "resource": RESOURCE,
        }

        failed = await self.client.post("/token", data={**request, "code_verifier": "x" * 64})
        self.assertEqual(failed.json()["error"], "invalid_grant")

        retry = await self.client.post("/token", data={**request, "code_verifier": VERIFIER})
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json()["access_token"], "sk_valid")

    async def test_invalid_api_key_is_not_stored_or_reflected(self):
        response = await self.client.post(
            "/authorize",
            data={**self.authorization_params(), "api_key": "sk_bad"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertIn("Invalid API key", response.text)
        self.assertNotIn("sk_bad", response.text)
        self.assertEqual(self.validated_keys, ["sk_bad"])

    async def test_chunked_oversized_form_is_rejected_without_buffering_it_all(self):
        async def oversized_body():
            yield b"x" * 10_000
            yield b"x" * 10_000

        response = await self.client.post(
            "/token",
            content=oversized_body(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_request")

    async def test_browser_get_to_root_returns_landing_page(self):
        response = await self.client.get(
            "/",
            headers={"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/html; charset=utf-8")
        self.assertEqual(response.headers["vary"], "Accept")
        self.assertNotIn("www-authenticate", response.headers)
        self.assertIn("Magic Hour MCP", response.text)
        self.assertIn("Connect with Claude", response.text)
        self.assertIn("Settings &gt; Connectors &gt; Add custom connector", response.text)
        self.assertIn('href="https://claude.ai/new#settings/customize-connectors"', response.text)
        self.assertIn("https://mcp.magichour.ai/", response.text)
        self.assertIn("magic-hour-mcp", response.text)
        self.assertIn("paste your Magic Hour API key", response.text)
        self.assertIn('href="https://magichour.ai/developer?tab=api-keys"', response.text)
        self.assertIn("Get a Magic Hour API key", response.text)
        self.assertIn('target="_blank" rel="noopener noreferrer"', response.text)

    async def test_machine_requests_still_receive_bearer_challenge(self):
        unauthorized = await self.client.get("/")
        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(unauthorized.json(), {"error": "unauthorized"})
        self.assertEqual(
            unauthorized.headers["www-authenticate"],
            'Bearer resource_metadata="https://mcp.example/.well-known/oauth-protected-resource"',
        )

        for method, accept in (
            ("GET", "text/event-stream"),
            ("GET", "*/*"),
            ("GET", "text/html;q=0,*/*"),
            ("POST", "text/html"),
        ):
            response = await self.client.request(method, "/", headers={"Accept": accept})
            self.assertEqual(response.status_code, 401, (method, accept))
            self.assertIn("resource_metadata=", response.headers["www-authenticate"])

    async def test_browser_get_with_invalid_authorization_receives_bearer_challenge(self):
        for authorization in ("Basic dXNlcjpwYXNz", "Bearer"):
            response = await self.client.get(
                "/",
                headers={"Accept": "text/html", "Authorization": authorization},
            )

            self.assertEqual(response.status_code, 401, authorization)
            self.assertEqual(response.json(), {"error": "unauthorized"})
            self.assertEqual(
                response.headers["www-authenticate"],
                'Bearer resource_metadata="https://mcp.example/.well-known/oauth-protected-resource"',
            )

    async def test_mcp_preserves_existing_api_key_header(self):

        authorized = await self.client.get("/", headers={"Authorization": "Bearer sk_existing"})
        self.assertEqual(authorized.status_code, 200)
        self.assertEqual(authorized.json()["authorization"], "Bearer sk_existing")

        preflight = await self.client.options("/")
        self.assertNotEqual(preflight.status_code, 401)

    async def test_discovery_advertises_pkce_and_resource(self):
        authorization = await self.client.get("/.well-known/oauth-authorization-server")
        self.assertEqual(authorization.json()["code_challenge_methods_supported"], ["S256"])
        self.assertEqual(authorization.json()["token_endpoint"], "https://mcp.example/token")
        self.assertNotIn("registration_endpoint", authorization.json())

        resource = await self.client.get("/.well-known/oauth-protected-resource")
        self.assertEqual(resource.json()["resource"], RESOURCE)
        self.assertEqual(resource.json()["authorization_servers"], ["https://mcp.example"])

    async def test_default_key_validator_only_accepts_authenticated_validation_response(self):
        class FakeClient:
            def __init__(self, response):
                self.response = response
                self.headers = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return None

            async def post(self, path, headers, json):
                self.headers = headers
                self.json = json
                return self.response

        server = OAuthCompatibilityServer(
            settings=OAuthSettings(resource_url=RESOURCE),
        )
        for status_code, expected in ((400, True), (401, False), (403, False), (404, False)):
            response = httpx.Response(
                status_code,
                request=httpx.Request("POST", "https://api.magichour.ai/validation"),
            )
            fake_client = FakeClient(response)
            with patch("mcp_magichour.oauth_compat.httpx.AsyncClient", return_value=fake_client):
                self.assertEqual(await server._validate_api_key("sk_secret"), expected)
            self.assertEqual(fake_client.headers, {"Authorization": "Bearer sk_secret"})
            self.assertEqual(fake_client.json, {})

        bad_response = httpx.Response(
            500,
            request=httpx.Request("POST", "https://api.magichour.ai/validation"),
        )
        with patch(
            "mcp_magichour.oauth_compat.httpx.AsyncClient",
            return_value=FakeClient(bad_response),
        ):
            with self.assertRaises(httpx.HTTPStatusError):
                await server._validate_api_key("sk_secret")

    def test_expired_authorization_code_cannot_be_consumed(self):
        store = AuthorizationCodeStore(ttl_seconds=0)
        code = store.issue(
            api_key="sk_valid",
            redirect_uri=REDIRECT_URI,
            code_challenge=CHALLENGE,
            resource=RESOURCE,
        )

        self.assertIsNone(store.consume(code))


if __name__ == "__main__":
    unittest.main()
