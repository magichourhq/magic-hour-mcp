"""Exercise real MCP HTTP/tool handling against a credential-checking API stub.

OAuth access tokens are opaque Magic Hour API keys. The web app, not MCP,
validates their hashes and resolves their owners. These tests verify that MCP
preserves that boundary; they do not replace live issuer/database validation.
"""

import asyncio
import json
import unittest
from contextlib import AsyncExitStack
from unittest.mock import patch

import httpx

from mcp_magichour.oauth_compat import create_oauth_compatibility_app
from mcp_magichour.openapi_auth import (
    AuthError,
    BearerPassthroughAuth,
    current_authorization_header,
)
from mcp_magichour.openapi_server import create_mcp, middleware

OAUTH_ACCESS_TOKEN = "mhk_live_" + "A" * 80
API_KEY = "mhk_live_" + "B" * 80


class AuthenticatedMCPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        stack = AsyncExitStack()
        self.addAsyncCleanup(stack.aclose)
        self.forwarded_headers = []
        # An upstream test double: only these exact credentials authorize an account.
        self.owners = {
            f"Bearer {OAUTH_ACCESS_TOKEN}": "oauth-owner",
            f"Bearer {API_KEY}": "api-key-owner",
        }

        async def upstream(request):
            self.assertEqual(str(request.url), "https://api.example/v1/account")
            self.forwarded_headers.append(request.headers)
            # Interleave concurrent callers to expose accidental shared credentials.
            await asyncio.sleep(0)
            owner = self.owners.get(request.headers.get("authorization"))
            if owner is None:
                return httpx.Response(
                    401, json={"code": "unauthorized", "message": "Unauthorized"}
                )
            return httpx.Response(
                200,
                json={
                    "id": owner,
                    "email": None,
                    "tier": "free",
                    "credits": 10,
                    "subscription": None,
                },
            )

        api = await stack.enter_async_context(
            httpx.AsyncClient(
                base_url="https://api.example",
                auth=BearerPassthroughAuth(),
                transport=httpx.MockTransport(upstream),
            )
        )
        with patch("mcp_magichour.openapi_server.build_api_client", return_value=api):
            mcp = create_mcp()
        app = create_oauth_compatibility_app(
            mcp.http_app(
                path="/",
                middleware=list(middleware),
                stateless_http=True,
            )
        )
        self.app = app
        self.client = await stack.enter_async_context(
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="https://mcp.example",
                headers={"Accept": "application/json, text/event-stream"},
            )
        )

    async def call_account(self, authorization):
        headers = {} if authorization is None else {"Authorization": authorization}
        return await self.client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "get_account_info", "arguments": {}},
            },
        )

    def result(self, response):
        self.assertEqual(response.status_code, 200)
        payload = next(
            line.removeprefix("data: ")
            for line in response.text.splitlines()
            if line.startswith("data: ")
        )
        return json.loads(payload)["result"]

    async def test_oauth_and_api_key_calls_resolve_their_own_accounts(self):
        async with self.app.router.lifespan_context(self.app):
            for token, owner in (
                (OAUTH_ACCESS_TOKEN, "oauth-owner"),
                (API_KEY, "api-key-owner"),
                (OAUTH_ACCESS_TOKEN, "oauth-owner"),
            ):
                result = self.result(await self.call_account(f"Bearer {token}"))
                self.assertFalse(result["isError"])
                self.assertEqual(result["structuredContent"]["id"], owner)
            self.assertEqual(
                [headers["authorization"] for headers in self.forwarded_headers],
                [
                    f"Bearer {OAUTH_ACCESS_TOKEN}",
                    f"Bearer {API_KEY}",
                    f"Bearer {OAUTH_ACCESS_TOKEN}",
                ],
            )

    async def test_concurrent_callers_do_not_share_credentials(self):
        async with self.app.router.lifespan_context(self.app):
            responses = await asyncio.gather(
                self.call_account(f"Bearer {OAUTH_ACCESS_TOKEN}"),
                self.call_account(f"Bearer {API_KEY}"),
            )
            self.assertEqual(
                [
                    self.result(response)["structuredContent"]["id"]
                    for response in responses
                ],
                ["oauth-owner", "api-key-owner"],
            )

    async def test_bearer_scheme_and_surrounding_spaces_are_normalized(self):
        async with self.app.router.lifespan_context(self.app):
            result = self.result(
                await self.call_account(f"bEaReR  {OAUTH_ACCESS_TOKEN} ")
            )
            self.assertFalse(result["isError"])
            self.assertEqual(result["structuredContent"]["id"], "oauth-owner")
            self.assertEqual(
                self.forwarded_headers[0]["authorization"],
                f"Bearer {OAUTH_ACCESS_TOKEN}",
            )

    async def test_invalid_tokens_cannot_return_account_data(self):
        async with self.app.router.lifespan_context(self.app):
            for token in ("mhk_live_unknown_fixture", "not-a-magic-hour-key"):
                result = self.result(await self.call_account(f"Bearer {token}"))
                self.assertTrue(result["isError"])
                self.assertNotIn("structuredContent", result)
                self.assertIn("401", result["content"][0]["text"])
                self.assertNotIn(token, json.dumps(result))

    async def test_revoked_oauth_token_is_not_cached(self):
        async with self.app.router.lifespan_context(self.app):
            authorization = f"Bearer {OAUTH_ACCESS_TOKEN}"
            self.assertFalse(
                self.result(await self.call_account(authorization))["isError"]
            )
            del self.owners[authorization]
            result = self.result(await self.call_account(authorization))
            self.assertTrue(result["isError"])
            self.assertNotIn("structuredContent", result)
            self.assertEqual(len(self.forwarded_headers), 2)

    async def test_missing_or_malformed_auth_is_challenged_before_upstream(self):
        async with self.app.router.lifespan_context(self.app):
            for authorization in (None, "Basic fixture", "Bearer   "):
                response = await self.call_account(authorization)
                self.assertEqual(response.status_code, 401)
                self.assertIn(
                    "resource_metadata=", response.headers["www-authenticate"]
                )
            self.assertEqual(self.forwarded_headers, [])

    async def test_authenticated_request_does_not_authorize_the_next_request(self):
        async with self.app.router.lifespan_context(self.app):
            self.assertFalse(
                self.result(await self.call_account(f"Bearer {OAUTH_ACCESS_TOKEN}"))[
                    "isError"
                ]
            )
            response = await self.call_account(None)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(len(self.forwarded_headers), 1)
            with self.assertRaises(AuthError):
                current_authorization_header()


if __name__ == "__main__":
    unittest.main()
