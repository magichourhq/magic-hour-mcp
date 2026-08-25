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
CHATGPT_REDIRECT_URI = "https://chatgpt.com/connector/oauth/5swpyzyTpmje"
CURSOR_REDIRECT_URI = "http://localhost:8787/callback"
RESOURCE = "https://mcp.example/mcp"
VERIFIER = "v" * 64
CHALLENGE = _pkce_challenge(VERIFIER)


class AuthorizationPageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inputs = []
        self.images = []
        self.links = []
        self.alerts = []
        self.buttons = []
        self.spans = []
        self.scripts = []
        self.script_bodies = []
        self.tables = []
        self.table_headers = []
        self.table_cells = []
        self._script_body = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input":
            self.inputs.append(attributes)
        if tag == "img":
            self.images.append(attributes)
        if tag == "a":
            self.links.append(attributes)
        if tag == "button":
            self.buttons.append(attributes)
        if tag == "span":
            self.spans.append(attributes)
        if tag == "script":
            self.scripts.append(attributes)
            self._script_body = []
        if tag == "table":
            self.tables.append(attributes)
        if tag == "th":
            self.table_headers.append(attributes)
        if tag == "td":
            self.table_cells.append(attributes)
        if attributes.get("role") == "alert":
            self.alerts.append((tag, attributes))

    def handle_endtag(self, tag):
        if tag == "script" and self._script_body is not None:
            self.script_bodies.append("".join(self._script_body))
            self._script_body = None

    def handle_data(self, data):
        if self._script_body is not None:
            self._script_body.append(data)


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

    async def test_authorization_page_preserves_form_and_asset_contracts(self):
        page = await self.client.get("/authorize", params=self.authorization_params())
        parser = AuthorizationPageParser()
        parser.feed(page.text)

        api_key_input = next(field for field in parser.inputs if field.get("name") == "api_key")
        self.assertEqual(api_key_input["type"], "password")
        self.assertEqual(api_key_input["placeholder"], "mhk_live_…")
        self.assertIn("required", api_key_input)
        self.assertEqual(api_key_input["autocomplete"], "off")
        self.assertNotIn("aria-describedby", api_key_input)

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
        submit_button = next(button for button in parser.buttons if button.get("type") == "submit")
        self.assertEqual(submit_button.get("id"), "connect-button")
        self.assertEqual(submit_button.get("class"), "connect-button")
        self.assertNotIn("disabled", submit_button)
        self.assertNotIn("aria-busy", submit_button)
        visibility_button = next(button for button in parser.buttons if button.get("id") == "api-key-visibility")
        self.assertEqual(visibility_button.get("type"), "button")
        self.assertEqual(visibility_button.get("class"), "visibility-toggle")
        self.assertEqual(visibility_button.get("aria-label"), "Show API key")
        self.assertEqual(visibility_button.get("aria-pressed"), "false")
        loading = next(span for span in parser.spans if span.get("class") == "button-loading")
        spinner = next(span for span in parser.spans if span.get("class") == "spinner")
        self.assertIn("hidden", loading)
        self.assertEqual(spinner.get("aria-hidden"), "true")

        script = "".join(parser.script_bodies)
        self.assertIn('visibilityButton.addEventListener("click"', script)
        self.assertIn('apiKeyInput.type = revealing ? "text" : "password"', script)
        self.assertIn('visibilityButton.textContent = revealing ? "Hide" : "Show"', script)
        self.assertIn('visibilityButton.setAttribute("aria-label"', script)
        self.assertIn('visibilityButton.setAttribute("aria-pressed", String(revealing))', script)
        self.assertIn('form.addEventListener("submit"', script)
        self.assertIn("connectButton.disabled = true", script)
        self.assertIn('connectButton.setAttribute("aria-busy", "true")', script)
        self.assertIn("label.hidden = true", script)
        self.assertIn("loading.hidden = false", script)
        self.assertNotIn("apiKeyInput.value", script)
        self.assertNotIn("console.", script)

    async def test_authorization_page_security_headers_restrict_content(self):
        page = await self.client.get("/authorize", params=self.authorization_params())
        parser = AuthorizationPageParser()
        parser.feed(page.text)

        self.assertEqual(page.headers["cache-control"], "no-store")
        self.assertEqual(page.headers["referrer-policy"], "no-referrer")
        self.assertEqual(page.headers["x-content-type-options"], "nosniff")
        self.assertEqual(page.headers["x-frame-options"], "DENY")
        self.assertEqual(len(parser.scripts), 1)
        nonce = parser.scripts[0].get("nonce")
        self.assertTrue(nonce)
        csp = page.headers["content-security-policy"]
        self.assertIn("default-src 'none'", csp)
        self.assertIn("style-src 'unsafe-inline'", csp)
        self.assertIn("img-src 'self'", csp)
        self.assertIn(f"script-src 'nonce-{nonce}'", csp)
        script_directive = next(part for part in csp.split("; ") if part.startswith("script-src"))
        self.assertNotIn("'unsafe-inline'", script_directive)
        self.assertNotIn("src", parser.scripts[0])
        self.assertNotIn('src="http', page.text)

        second_page = await self.client.get("/authorize", params=self.authorization_params())
        second_parser = AuthorizationPageParser()
        second_parser.feed(second_page.text)
        self.assertNotEqual(nonce, second_parser.scripts[0].get("nonce"))

    async def test_authorization_error_is_accessible_and_never_reflects_key(self):
        response = await self.client.post(
            "/authorize",
            data={**self.authorization_params(), "api_key": "sk_bad"},
        )

        self.assertEqual(response.status_code, 401)
        parser = AuthorizationPageParser()
        parser.feed(response.text)
        self.assertIn(
            ("p", {"class": "error", "id": "api-key-error", "role": "alert"}),
            parser.alerts,
        )
        self.assertIn('aria-invalid="true"', response.text)
        self.assertIn('aria-describedby="api-key-error"', response.text)
        self.assertLess(response.text.index('id="api-key"'), response.text.index('id="api-key-error"'))
        self.assertNotIn("sk_bad", response.text)

    async def test_malformed_api_key_is_not_reflected_or_validated_upstream(self):
        response = await self.client.post(
            "/authorize",
            data={**self.authorization_params(), "api_key": "mhk live incomplete"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertNotIn("mhk live incomplete", response.text)
        self.assertEqual(self.validated_keys, [])

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
        redirect_uris = [
            "https://evil.example/callback",
            "https://chatgpt.com/connector/oauth/",
            "https://chatgpt.com/connector/oauth/replacement-connector-id",
            "https://chatgpt.com.evil.example/connector/oauth/id",
            "http://localhost:8788/callback",
            "http://localhost:8787/callback/extra",
            "http://localhost:8787/callback?next=evil",
            "http://localhost:8787/callback#fragment",
            "http://localhost.evil.example:8787/callback",
        ]

        for redirect_uri in redirect_uris:
            with self.subTest(redirect_uri=redirect_uri):
                response = await self.client.post(
                    "/authorize",
                    data={
                        **self.authorization_params(redirect_uri=redirect_uri),
                        "api_key": "sk_valid",
                    },
                )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"], "invalid_request")
        self.assertEqual(self.validated_keys, [])

    async def test_connector_redirect_uris_are_allowed(self):
        for redirect_uri in [CHATGPT_REDIRECT_URI, CURSOR_REDIRECT_URI]:
            with self.subTest(redirect_uri=redirect_uri):
                response = await self.client.post(
                    "/authorize",
                    data={
                        **self.authorization_params(redirect_uri=redirect_uri),
                        "api_key": "sk_valid",
                    },
                )

                self.assertEqual(response.status_code, 302)
                redirect = urlsplit(response.headers["location"])
                self.assertEqual(
                    f"{redirect.scheme}://{redirect.netloc}{redirect.path}",
                    redirect_uri,
                )
                redirect_query = parse_qs(redirect.query)
                self.assertEqual(redirect_query["state"], ["client-state"])

                token = await self.client.post(
                    "/token",
                    data={
                        "grant_type": "authorization_code",
                        "client_id": CLIENT_ID,
                        "redirect_uri": redirect_uri,
                        "code": redirect_query["code"][0],
                        "code_verifier": VERIFIER,
                        "resource": RESOURCE,
                    },
                )
                self.assertEqual(token.status_code, 200)
                self.assertEqual(
                    token.json(),
                    {"access_token": "sk_valid", "token_type": "Bearer"},
                )

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

    async def test_browser_get_to_root_redirects_to_setup_page(self):
        response = await self.client.get(
            "/",
            headers={"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "https://magichour.ai/mcp")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["vary"], "Accept")
        self.assertNotIn("www-authenticate", response.headers)

    async def test_non_post_machine_requests_still_receive_bearer_challenge(self):
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
        ):
            response = await self.client.request(method, "/", headers={"Accept": accept})
            self.assertEqual(response.status_code, 401, (method, accept))
            self.assertIn("resource_metadata=", response.headers["www-authenticate"])

        discovery = await self.client.post("/", headers={"Accept": "text/html"})
        self.assertNotEqual(discovery.status_code, 401)
        self.assertNotIn("www-authenticate", discovery.headers)

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
        self.assertEqual(authorization.json()["registration_endpoint"], "https://mcp.example/register")

        resource = await self.client.get("/.well-known/oauth-protected-resource")
        self.assertEqual(resource.json()["resource"], RESOURCE)
        self.assertEqual(resource.json()["authorization_servers"], ["https://mcp.example"])

    async def test_dynamic_client_registration_supports_custom_connector_callback(self):
        registration = await self.client.post(
            "/register",
            json={
                "client_name": "ChatGPT Business",
                "redirect_uris": ["https://chatgpt.com/connector/oauth/business-callback"],
            },
        )
        self.assertEqual(registration.status_code, 201)
        registered = registration.json()
        self.assertTrue(registered["client_id"].startswith("mcp_"))
        self.assertEqual(
            registered["redirect_uris"],
            ["https://chatgpt.com/connector/oauth/business-callback"],
        )

        params = self.authorization_params(
            client_id=registered["client_id"],
            redirect_uri=registered["redirect_uris"][0],
        )
        authorized = await self.client.post(
            "/authorize",
            data={**params, "api_key": "sk_valid"},
        )
        self.assertEqual(authorized.status_code, 302)
        code = parse_qs(urlsplit(authorized.headers["location"]).query)["code"][0]

        token = await self.client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "client_id": registered["client_id"],
                "redirect_uri": params["redirect_uri"],
                "code": code,
                "code_verifier": VERIFIER,
                "resource": RESOURCE,
            },
        )
        self.assertEqual(token.status_code, 200)
        self.assertEqual(token.json(), {"access_token": "sk_valid", "token_type": "Bearer"})

    async def test_dynamic_registration_rejects_insecure_or_query_callback(self):
        for redirect_uri in (
            "https://evil.example/callback?next=evil",
            "http://evil.example/callback",
            "https://chatgpt.com/connector/oauth/callback#fragment",
        ):
            with self.subTest(redirect_uri=redirect_uri):
                response = await self.client.post(
                    "/register",
                    json={"redirect_uris": [redirect_uri]},
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"], "invalid_redirect_uri")

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
