import json
import unittest

import httpx

from mcp_magichour.openapi_server import app


class MCPErrorTests(unittest.IsolatedAsyncioTestCase):
    async def call_tool(self, name: str, arguments: dict) -> dict:
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://mcp.example",
            headers={
                "Accept": "application/json, text/event-stream",
                "Authorization": "Bearer test-token",
            },
        ) as client:
            response = await client.post(
                "/",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
            )

        payload = next(
            line.removeprefix("data: ")
            for line in response.text.splitlines()
            if line.startswith("data: ")
        )
        return json.loads(payload)

    async def test_unknown_tool_returns_structured_json_rpc_error(self):
        payload = await self.call_tool("tool_that_does_not_exist", {})

        self.assertEqual(payload["error"]["code"], -32602)
        self.assertEqual(payload["error"]["message"], "Unknown tool: 'tool_that_does_not_exist'")

    async def test_bad_arguments_return_structured_json_rpc_error(self):
        payload = await self.call_tool("ping", {"unexpected": True})

        self.assertEqual(payload["error"]["code"], -32602)
        self.assertIn("Invalid arguments for tool 'ping'", payload["error"]["message"])


if __name__ == "__main__":
    unittest.main()
