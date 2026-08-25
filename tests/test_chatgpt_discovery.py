import json
import re
import unittest
from html import escape
from pathlib import Path
from urllib.parse import urlparse

import httpx

from mcp_magichour.openapi_server import (
    GLAMA_VERIFICATION_PATH,
    MCP_APP_ASSET_PATH,
    MCP_APP_MEDIA_ORIGIN,
    MCP_APP_MIME_TYPE,
    MCP_APP_ORIGIN,
    MCP_APP_SERVER_ORIGIN,
    MCP_APP_VIEW_CSP,
    MCP_APP_VIEW_PATH,
    MCP_APP_VIEW_URI,
    MCP_SERVER_CARD_PATH,
    MCP_SERVER_DESCRIPTION,
    MCP_SERVER_INSTRUCTIONS,
    MCP_SERVER_NAME,
    MCP_SERVER_URL,
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
            script_url = re.search(r'<script[^>]+src="([^"]+)"', response.text)
            style_url = re.search(r'<link[^>]+href="([^"]+)"', response.text)
            self.assertIsNotNone(script_url)
            self.assertIsNotNone(style_url)
            asset_headers = {"Origin": "https://chatgpt.com"}
            script_response = await client.get(urlparse(script_url.group(1)).path, headers=asset_headers)
            style_response = await client.get(urlparse(style_url.group(1)).path, headers=asset_headers)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith(MCP_APP_MIME_TYPE))
        self.assertEqual(response.headers["content-security-policy"], MCP_APP_VIEW_CSP)
        self.assertIn(
            f'<meta http-equiv="Content-Security-Policy" content="{escape(MCP_APP_VIEW_CSP, quote=False)}">',
            response.text,
        )
        self.assertIn(f"connect-src {MCP_APP_SERVER_ORIGIN} {MCP_APP_ORIGIN}", MCP_APP_VIEW_CSP)
        self.assertIn("frame-ancestors https://chatgpt.com https://claude.ai", MCP_APP_VIEW_CSP)
        self.assertIn("form-action 'none'", MCP_APP_VIEW_CSP)
        for directive in ("img-src", "script-src", "style-src"):
            self.assertNotIn(f"{directive} *", MCP_APP_VIEW_CSP)
        self.assertTrue(response.text.startswith("<!DOCTYPE html>"))
        self.assertNotIn("<base", response.text.lower())
        self.assertIn('<meta name="color-scheme" content="light dark">', response.text)
        self.assertIn('id="root"', response.text)
        self.assertIn(MCP_APP_ASSET_PATH, response.text)
        self.assertEqual(script_response.status_code, 200)
        self.assertEqual(style_response.status_code, 200)
        self.assertEqual(script_response.headers["access-control-allow-origin"], "*")
        self.assertEqual(style_response.headers["access-control-allow-origin"], "*")
        self.assertIn("ui/notifications/tool-result", script_response.text)
        self.assertIn("ui/download-file", script_response.text)
        self.assertIn("ui/request-display-mode", script_response.text)
        self.assertIn("/app/observability", script_response.text)
        self.assertNotIn("Generated media is ready.", script_response.text)
        self.assertIn("prefers-color-scheme:dark", style_response.text)
        self.assertNotIn("<form", response.text.lower())
        self.assertNotIn('type="password"', response.text.lower())

    def test_vercel_observability_relay_allows_mcp_iframes(self):
        config = json.loads((Path(__file__).parents[1] / "vercel.json").read_text())

        self.assertEqual(
            config["rewrites"],
            [
                {
                    "source": "/app/observability/:path*",
                    "destination": "https://mcp.magichour.ai/_vercel/:path*",
                }
            ],
        )
        self.assertEqual(
            config["headers"][0],
            {
                "source": "/app/observability/:path*",
                "headers": [
                    {"key": "Access-Control-Allow-Origin", "value": "*"},
                    {"key": "Access-Control-Allow-Methods", "value": "GET, POST, OPTIONS"},
                    {"key": "Access-Control-Allow-Headers", "value": "Content-Type"},
                ],
            },
        )

    async def test_server_card_publicly_advertises_mcp_endpoint(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://mcp.example",
        ) as client:
            response = await client.get(MCP_SERVER_CARD_PATH)

        self.assertEqual(response.status_code, 200)
        card = response.json()
        self.assertEqual(card["name"], MCP_SERVER_NAME)
        self.assertEqual(card["description"], MCP_SERVER_DESCRIPTION)
        self.assertEqual(card["version"], MCP_SERVER_VERSION)
        self.assertEqual(MCP_SERVER_URL, "https://mcp.magichour.ai/")
        self.assertEqual(card["serverUrl"], MCP_SERVER_URL)
        self.assertGreater(len(card["tools"]), 0)
        self.assertTrue(
            all(
                {"name", "description"} <= tool.keys()
                and ("inputSchema" in tool or "parameters" in tool)
                for tool in card["tools"]
            )
        )
        self.assertIn("ping", {tool["name"] for tool in card["tools"]})
        self.assertEqual(response.headers["access-control-allow-origin"], "*")
        self.assertEqual(response.headers["access-control-allow-methods"], "GET")
        self.assertNotIn("www-authenticate", response.headers)

    async def test_glama_verification_is_public(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://mcp.example",
        ) as client:
            response = await client.get(GLAMA_VERIFICATION_PATH)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "$schema": "https://glama.ai/mcp/schemas/connector.json",
                "maintainers": [{"email": "support@magichour.ai"}],
            },
        )
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
        self.assertNotIn("ui", ping.get("_meta", {}))
        for name in ("wait_for_video_project", "wait_for_image_project", "wait_for_audio_project"):
            tool = next(tool for tool in tools if tool["name"] == name)
            self.assertEqual(tool["_meta"]["ui"]["resourceUri"], MCP_APP_VIEW_URI)

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
                "connectDomains": [MCP_APP_SERVER_ORIGIN, MCP_APP_ORIGIN],
                "resourceDomains": [MCP_APP_ORIGIN, MCP_APP_MEDIA_ORIGIN],
            },
        )
        self.assertTrue(view_content["_meta"]["ui"]["prefersBorder"])
        self.assertTrue(view_content["text"].startswith("<!DOCTYPE html>"))
        self.assertIn(
            f'<meta http-equiv="Content-Security-Policy" content="{escape(MCP_APP_VIEW_CSP, quote=False)}">',
            view_content["text"],
        )
        self.assertNotIn("<base", view_content["text"].lower())
        self.assertIn('<meta name="color-scheme" content="light dark">', view_content["text"])
        self.assertIn('id="root"', view_content["text"])
        self.assertIn(MCP_APP_ASSET_PATH, view_content["text"])
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
