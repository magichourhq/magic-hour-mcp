import json
import unittest
from unittest.mock import patch

import httpx
from starlette.middleware import Middleware

from mcp_magichour.oauth_compat import OAuthSettings, create_oauth_compatibility_app
from mcp_magichour.openapi_auth import (
    BearerPassthroughAuth,
    BearerPassthroughMiddleware,
)
from mcp_magichour.openapi_server import create_mcp


class GrokAuthenticationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.forwarded = []

        def upstream(request):
            self.forwarded.append(request)
            if request.headers.get("Authorization") not in {"Bearer mhk_live_mock_oauth_key", "Bearer mhk_live_mock_direct_key"}:
                return httpx.Response(401, json={"message": "Invalid API key"})
            return httpx.Response(200, json={"id": "mock-image-project", "credits_charged": 5})

        self.upstream = httpx.AsyncClient(
            base_url="https://api.example.test",
            auth=BearerPassthroughAuth(),
            transport=httpx.MockTransport(upstream),
        )
        with patch("mcp_magichour.openapi_server.build_api_client", return_value=self.upstream):
            server = create_mcp()
        mcp_app = server.http_app(
            path="/", stateless_http=True,
            middleware=[Middleware(BearerPassthroughMiddleware)],
        )
        self.app = create_oauth_compatibility_app(
            mcp_app,
            settings=OAuthSettings(issuer_url="https://mcp.example", resource_url="https://mcp.example"),
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="https://mcp.example",
            headers={"Accept": "application/json, text/event-stream"},
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.upstream.aclose()

    @staticmethod
    def payload(method, params):
        return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}

    @staticmethod
    def result(response):
        return json.loads(next(
            line.removeprefix("data: ") for line in response.text.splitlines()
            if line.startswith("data: ")
        ))["result"]

    async def call_generation(self, authorization=None):
        headers = {"Authorization": authorization} if authorization is not None else {}
        return await self.client.post("/", headers=headers, json=self.payload("tools/call", {
            "name": "ai_image_generator_create_image",
            "arguments": {"image_count": 1, "style": {"prompt": "Authentication regression"}},
        }))

    async def test_missing_and_malformed_bearer_challenge_before_upstream_generation(self):
        async with self.app.router.lifespan_context(self.app):
            for authorization in (None, "", "Bearer", "Bearer ", "Basic test"):
                with self.subTest(authorization=authorization):
                    response = await self.call_generation(authorization)
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response.headers["www-authenticate"],
                        'Bearer resource_metadata="https://mcp.example/.well-known/oauth-protected-resource"')
            self.assertEqual(self.forwarded, [])

    async def test_initialize_and_tool_discovery_remain_public(self):
        async with self.app.router.lifespan_context(self.app):
            initialized = await self.client.post("/", json=self.payload("initialize", {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "grok-auth-regression", "version": "1"},
            }))
            self.assertEqual(initialized.status_code, 200)
            self.assertNotIn("www-authenticate", initialized.headers)
            listed = await self.client.post("/", json=self.payload("tools/list", {}))
            self.assertEqual(listed.status_code, 200)
            self.assertIn("ai_image_generator_create_image", {tool["name"] for tool in self.result(listed)["tools"]})
            self.assertNotIn("www-authenticate", listed.headers)
            self.assertEqual(self.forwarded, [])

    async def test_oauth_format_and_direct_api_key_bearers_reach_generation_unchanged(self):
        async with self.app.router.lifespan_context(self.app):
            # Synthetic keys exercise passthrough, not real OAuth issuance or DB validation.
            for token in ("mhk_live_mock_oauth_key", "mhk_live_mock_direct_key"):
                with self.subTest(token=token):
                    response = await self.call_generation(f"bearer {token}  ")
                    self.assertEqual(response.status_code, 200)
                    result = self.result(response)
                    self.assertFalse(result.get("isError", False))
                    self.assertIn("mock-image-project", json.dumps(result))
                    request = self.forwarded[-1]
                    self.assertEqual(request.headers["Authorization"], f"Bearer {token}")
                    self.assertEqual(request.url.path, "/v1/ai-image-generator")
            self.assertEqual(len(self.forwarded), 2)

    async def test_revoked_key_upstream_rejection_never_becomes_generation_success(self):
        async with self.app.router.lifespan_context(self.app):
            response = await self.call_generation("Bearer revoked-test-key")
            self.assertEqual(len(self.forwarded), 1)
            # Upstream failures remain MCP tool errors rather than fabricated project success.
            result = self.result(response)
            self.assertTrue(result["isError"])
            self.assertIn("401", json.dumps(result))
            self.assertNotIn("mock-image-project", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
