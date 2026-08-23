import json
import unittest

import httpx

from mcp_magichour.openapi_server import app


class MCPErrorTests(unittest.IsolatedAsyncioTestCase):
    async def call_tool(self, name: str, arguments: dict, *, authorized: bool = True) -> dict:
        headers = {"Accept": "application/json, text/event-stream"}
        if authorized:
            headers["Authorization"] = "Bearer test-token"

        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://mcp.example",
            headers=headers,
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

    async def test_unauthenticated_invalid_calls_return_structured_json_rpc_errors(self):
        cases = [
            ("tool_that_does_not_exist", {}, "Unknown tool"),
            ("ping", {"unexpected": True}, "Invalid arguments"),
        ]

        for tool, arguments, expected_message in cases:
            with self.subTest(tool):
                payload = await self.call_tool(tool, arguments, authorized=False)
                self.assertEqual(payload["error"]["code"], -32602)
                self.assertIn(expected_message, payload["error"]["message"])

    async def test_bad_arguments_return_structured_json_rpc_errors(self):
        cases = [
            ("missing required parameter", "wait_for_video_project", {}, "'id' is a required property"),
            ("wrong parameter type", "wait_for_video_project", {"id": 123}, "not of type 'string'"),
            ("unexpected parameter", "ping", {"unexpected": True}, "was unexpected"),
            (
                "wrong nested enum",
                "video_assets_generate_presigned_url",
                {"items": [{"type": "document", "extension": "pdf"}]},
                "is not one of",
            ),
            ("empty required array", "video_assets_generate_presigned_url", {"items": []}, "should be non-empty"),
            (
                "missing nested parameter",
                "video_assets_generate_presigned_url",
                {"items": [{"type": "video"}]},
                "'extension' is a required property",
            ),
            (
                "invalid extension pattern",
                "video_assets_generate_presigned_url",
                {"items": [{"type": "video", "extension": ".mp4"}]},
                "does not match",
            ),
        ]

        for label, tool, arguments, expected_message in cases:
            with self.subTest(label):
                payload = await self.call_tool(tool, arguments)
                self.assertEqual(payload["error"]["code"], -32602)
                self.assertIn(f"Invalid arguments for tool {tool!r}", payload["error"]["message"])
                self.assertIn(expected_message, payload["error"]["message"])


if __name__ == "__main__":
    unittest.main()
