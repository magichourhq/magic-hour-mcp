import json
import unittest

import httpx

from mcp_magichour.openapi_server import (
    MCP_APP_VIEW_PATH,
    MCP_APP_VIEW_URI,
    MCP_APP_VIEW_URL,
    MCP_SERVER_CARD_PATH,
    MCP_SERVER_INSTRUCTIONS,
    MCP_SERVER_NAME,
    MCP_SERVER_VERSION,
    app,
)


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

    async def test_mcp_app_http_view_is_public_with_scoped_csp(self):
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://mcp.example",
        ) as client:
            response = await client.get(MCP_APP_VIEW_PATH)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/html"))
        self.assertEqual(
            response.headers["content-security-policy"],
            "default-src 'none'; "
            "connect-src https://mcp.magichour.ai; "
            "frame-ancestors https://chatgpt.com https://claude.ai; "
            "form-action 'none'; "
            "img-src https://mcp.magichour.ai; "
            "script-src https://mcp.magichour.ai; "
            "style-src https://mcp.magichour.ai; "
            "base-uri https://mcp.magichour.ai",
        )
        self.assertTrue(response.text.startswith("<!DOCTYPE html>"))
        self.assertIn(f'<base href="{MCP_APP_VIEW_URL}">', response.text)
        self.assertIn('<meta name="color-scheme" content="light dark">', response.text)
        self.assertNotIn("<form", response.text.lower())
        self.assertNotIn('type="password"', response.text.lower())

    async def test_server_card_publicly_advertises_mcp_endpoint(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://mcp.example",
        ) as client:
            response = await client.get(MCP_SERVER_CARD_PATH)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["transport"], {"type": "streamable-http", "endpoint": "/mcp/"})
        self.assertEqual(response.headers["access-control-allow-origin"], "*")
        self.assertEqual(response.headers["access-control-allow-methods"], "GET")
        self.assertNotIn("www-authenticate", response.headers)

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
        initialize_result = self.result(initialized)
        self.assertEqual(
            initialize_result["serverInfo"],
            {"name": MCP_SERVER_NAME, "version": MCP_SERVER_VERSION},
        )
        self.assertEqual(initialize_result["instructions"], MCP_SERVER_INSTRUCTIONS)

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
        ping = next(tool for tool in tools if tool["name"] == "ping")
        self.assertEqual(ping["_meta"]["ui"]["resourceUri"], MCP_APP_VIEW_URI)

        listed_resources = await client.post(
            "/",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 3, "method": "resources/list", "params": {}},
        )
        resources = self.result(listed_resources)["resources"]
        view = next(resource for resource in resources if resource["uri"] == MCP_APP_VIEW_URI)
        self.assertEqual(view["mimeType"], "text/html;profile=mcp-app")

        read_view = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "resources/read",
                "params": {"uri": MCP_APP_VIEW_URI},
            },
        )
        view_content = self.result(read_view)["contents"][0]
        self.assertEqual(view_content["mimeType"], "text/html;profile=mcp-app")
        self.assertEqual(
            view_content["_meta"]["ui"]["csp"],
            {
                "connectDomains": ["https://mcp.magichour.ai"],
                "resourceDomains": ["https://mcp.magichour.ai"],
                "baseUriDomains": ["https://mcp.magichour.ai"],
            },
        )
        self.assertTrue(view_content["text"].startswith("<!DOCTYPE html>"))
        self.assertIn(f'<base href="{MCP_APP_VIEW_URL}">', view_content["text"])
        self.assertIn('<meta name="color-scheme" content="light dark">', view_content["text"])
        self.assertNotIn("<form", view_content["text"].lower())
        self.assertNotIn('type="password"', view_content["text"].lower())

        called = await client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 5,
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
                "id": 6,
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
