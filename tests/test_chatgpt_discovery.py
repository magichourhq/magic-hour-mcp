import json
import unittest

import httpx

from mcp_magichour.openapi_server import app


class ChatGPTDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def result(response: httpx.Response) -> dict:
        payload = next(
            line.removeprefix("data: ")
            for line in response.text.splitlines()
            if line.startswith("data: ")
        )
        return json.loads(payload)["result"]

    async def test_discovery_is_public_and_tool_calls_trigger_oauth(self):
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://mcp.example",
            headers={"Accept": "application/json, text/event-stream"},
        ) as client:
            await self.assert_discovery_and_auth(client)

    async def assert_discovery_and_auth(self, client: httpx.AsyncClient):
        initialized = await client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "chatgpt-probe", "version": "1"},
                },
            },
        )
        self.assertEqual(initialized.status_code, 200)

        headers = {}
        if session_id := initialized.headers.get("mcp-session-id"):
            headers["mcp-session-id"] = session_id
        await client.post(
            "/",
            headers=headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        listed = await client.post(
            "/",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        tools = self.result(listed)["tools"]
        self.assertGreater(len(tools), 0)
        self.assertTrue(
            all(tool["securitySchemes"] == [{"type": "oauth2", "scopes": []}] for tool in tools)
        )

        called = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "ping", "arguments": {}},
            },
        )
        result = self.result(called)
        self.assertTrue(result["isError"])
        self.assertIn("mcp/www_authenticate", result["_meta"])
        self.assertIn("resource_metadata=", result["_meta"]["mcp/www_authenticate"][0])

        authorized = await client.post(
            "/",
            headers={**headers, "Authorization": "Bearer sk_test"},
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "ping", "arguments": {}},
            },
        )
        authorized_result = self.result(authorized)
        self.assertFalse(authorized_result["isError"])
        self.assertEqual(authorized_result["structuredContent"], {"result": "pong"})

        if session_id:
            await client.delete("/", headers=headers)


if __name__ == "__main__":
    unittest.main()
