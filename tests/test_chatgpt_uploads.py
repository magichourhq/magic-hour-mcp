import json
import unittest
from unittest.mock import patch

import httpx
from fastmcp import Client

from mcp_magichour import openapi_server
from mcp_magichour.openapi_server import create_mcp


class ChatGPTPresignedUploadGuardTests(unittest.IsolatedAsyncioTestCase):
    async def call_get_upload_urls(self, user_agent: str):
        def network(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"items": [{
                "expires_at": "2026-11-20T00:00:00Z",
                "upload_url": f"{openapi_server.MCP_APP_MEDIA_ORIGIN}/upload?sig=put",
                "file_path": "api-assets/id/uploaded.jpg",
            }]})

        async with httpx.AsyncClient(
            base_url="https://api.example.test", transport=httpx.MockTransport(network)
        ) as client:
            with patch.object(openapi_server, "build_api_client", return_value=client):
                server = create_mcp()
            with patch.object(openapi_server, "get_http_headers", return_value={"user-agent": user_agent}), \
                    patch("mcp_magichour.openapi_auth.current_authorization_header", return_value="Bearer test"), \
                    patch("mcp_magichour.oauth_compat.current_authorization_header", return_value="Bearer test"):
                async with Client(server) as caller:
                    return await caller.call_tool(
                        "get_upload_urls", {"items": [{"type": "image", "extension": "jpg"}]}, raise_on_error=False
                    )

    async def test_chatgpt_is_sent_to_the_attachment_field(self):
        result = await self.call_get_upload_urls("openai-mcp/1.0.0")
        self.assertTrue(result.is_error)
        self.assertIn("attachment field", result.content[0].text)

    async def test_other_clients_still_get_upload_urls(self):
        result = await self.call_get_upload_urls("claude-code/2.1.326 (sdk-cli)")
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(json.loads(result.content[0].text)["items"][0]["file_path"], "api-assets/id/uploaded.jpg")


if __name__ == "__main__":
    unittest.main()
