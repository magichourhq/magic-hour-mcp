import unittest

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from mcp_magichour.oauth_compat import (
    AUTHORIZATION_SERVER_URL,
    MCPBearerChallengeMiddleware,
    OAuthCompatibilityServer,
    OAuthSettings,
)

RESOURCE = "https://mcp.example/mcp"


class OAuthCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.oauth = OAuthCompatibilityServer(
            settings=OAuthSettings(
                issuer_url="https://mcp.example",
                resource_url=RESOURCE,
            ),
        )

        async def mcp_endpoint(request: Request):
            body = await request.body()
            return JSONResponse(
                {
                    "authorization": request.headers.get("authorization"),
                    "body": body.decode("utf-8", errors="replace"),
                }
            )

        mcp_app = Starlette(routes=[Route("/", mcp_endpoint, methods=["GET", "POST"])])
        protected_mcp = MCPBearerChallengeMiddleware(mcp_app, self.oauth)
        self.app = Starlette(routes=[*self.oauth.routes(), Mount("/", protected_mcp)])
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="https://mcp.example",
        )

    async def asyncTearDown(self):
        await self.client.aclose()

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
            response = await self.client.request(
                method, "/", headers={"Accept": accept}
            )
            self.assertEqual(response.status_code, 401, (method, accept))
            self.assertIn("resource_metadata=", response.headers["www-authenticate"])

        discovery = await self.client.post("/", headers={"Accept": "text/html"})
        self.assertNotEqual(discovery.status_code, 401)
        self.assertNotIn("www-authenticate", discovery.headers)

    async def test_browser_get_with_invalid_authorization_receives_bearer_challenge(
        self,
    ):
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

        authorized = await self.client.get(
            "/", headers={"Authorization": "Bearer sk_existing"}
        )
        self.assertEqual(authorized.status_code, 200)
        self.assertEqual(authorized.json()["authorization"], "Bearer sk_existing")

        preflight = await self.client.options("/")
        self.assertNotEqual(preflight.status_code, 401)

    async def test_protected_post_without_usable_bearer_gets_http_challenge(self):
        for authorization in (None, "", "Basic fixture", "Bearer", "Bearer   "):
            with self.subTest(authorization=authorization):
                headers = (
                    {} if authorization is None else {"Authorization": authorization}
                )
                response = await self.client.post(
                    "/",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": "ping", "arguments": {}},
                    },
                )
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json(), {"error": "unauthorized"})
                self.assertEqual(
                    response.headers["www-authenticate"],
                    'Bearer resource_metadata="https://mcp.example/.well-known/oauth-protected-resource"',
                )

    async def test_public_posts_replay_body_unchanged(self):
        for body in (
            b'{"jsonrpc":"2.0","id":1,"method":"initialize"}',
            b'{"jsonrpc":"2.0","id":2,"method":"tools/list"}',
            b'{"jsonrpc":"2.0","method":"notifications/initialized"}',
            b"not-json",
            b"",
        ):
            with self.subTest(body=body):
                response = await self.client.post("/", content=body)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["body"], body.decode())
                self.assertNotIn("www-authenticate", response.headers)

    async def test_bearer_tool_call_preserves_body_and_opaque_credential(self):
        body = (
            b'{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"ping"}}'
        )
        for token in ("mhk_live_oauth_fixture", "mhk_live_api_key_fixture"):
            response = await self.client.post(
                "/",
                content=body,
                headers={"Authorization": f"Bearer {token}"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["body"], body.decode())
            self.assertEqual(response.json()["authorization"], f"Bearer {token}")

    async def test_discovery_points_at_the_web_app_authorization_server(self):
        resource = await self.client.get("/.well-known/oauth-protected-resource")
        self.assertEqual(resource.json()["resource"], RESOURCE)
        self.assertEqual(
            resource.json()["authorization_servers"], [AUTHORIZATION_SERVER_URL]
        )

        legacy = await self.client.get("/.well-known/oauth-authorization-server")
        self.assertEqual(legacy.status_code, 302)
        self.assertEqual(
            legacy.headers["location"],
            f"{AUTHORIZATION_SERVER_URL}/.well-known/oauth-authorization-server",
        )


if __name__ == "__main__":
    unittest.main()
