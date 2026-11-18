import json
import unittest
from unittest.mock import patch

import httpx
from fastmcp import Client

from mcp_magichour.openapi_server import app, create_mcp


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

        if not authorized:
            self.assertEqual(response.status_code, 401)
            self.assertIn("resource_metadata=", response.headers["www-authenticate"])
            return response.json()

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

    async def test_unauthenticated_invalid_calls_receive_http_auth_challenge(self):
        cases = [
            ("tool_that_does_not_exist", {}),
            ("ping", {"unexpected": True}),
        ]

        for tool, arguments in cases:
            with self.subTest(tool):
                payload = await self.call_tool(tool, arguments, authorized=False)
                self.assertEqual(payload, {"error": "unauthorized"})

    async def test_bad_arguments_return_structured_json_rpc_errors(self):
        cases = [
            (
                "unknown voice preset",
                "ai_voice_generator_create_audio",
                {"style": {"prompt": "Hello", "voice_name": "unknown-test-preset"}},
                "is not one of",
            ),
            (
                "missing voice sample",
                "ai_voice_cloner_create_audio",
                {"style": {"prompt": "Hello"}},
                "'assets' is a required property",
            ),
            ("missing required parameter", "wait_for_video_project", {}, "'id' is a required property"),
            ("wrong parameter type", "wait_for_video_project", {"id": 123}, "not of type 'string'"),
            ("unexpected parameter", "ping", {"unexpected": True}, "was unexpected"),
            (
                "wrong nested enum",
                "get_upload_urls",
                {"items": [{"type": "document", "extension": "pdf"}]},
                "is not one of",
            ),
            ("empty required array", "get_upload_urls", {"items": []}, "should be non-empty"),
            (
                "missing nested parameter",
                "get_upload_urls",
                {"items": [{"type": "video"}]},
                "'extension' is a required property",
            ),
            (
                "invalid extension pattern",
                "get_upload_urls",
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


class LocalAssetRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def rejected_call(self, tool, arguments, *, status=422, code="unprocessable_entity", message="Invalid asset path"):
        requests = []

        def upstream(request):
            requests.append(json.loads(request.content))
            return httpx.Response(status, json={"code": code, "message": message})

        async with httpx.AsyncClient(
            base_url="https://api.example.test", transport=httpx.MockTransport(upstream),
        ) as client:
            with patch("mcp_magichour.openapi_server.build_api_client", return_value=client):
                server = create_mcp()
            with patch("mcp_magichour.oauth_compat.current_authorization_header", return_value="Bearer test-token"):
                async with Client(server) as caller:
                    result = await caller.call_tool(tool, arguments, raise_on_error=False)

        self.assertTrue(result.is_error)
        self.assertEqual(requests, [arguments], "Rejected generation must not retry or change settings")
        self.assertEqual(json.loads(result.content[0].text), result.structured_content)
        error = result.structured_content["error"]
        self.assertEqual(error["message"], message)
        self.assertEqual(error["code"], code)
        self.assertEqual(error["http_status"], status)
        return error["next_action"]

    async def test_local_image_and_video_rejections_name_attachment_upload_helpers(self):
        cases = [
            ("create_video_from_image", {"image_file_path": "/mnt/data/image.png"}, {}),
            ("edit_video", {"video_file_path": "/mnt/data/video.mp4"}, {"style": {"prompt": "Keep identity"}}),
        ]
        for tool, assets, extra in cases:
            with self.subTest(tool=tool):
                action = await self.rejected_call(tool, {
                    "assets": assets, "end_seconds": 5, "model": "ltx-2.5", "resolution": "480p", **extra,
                })
                for instruction in (
                    "cannot read the client's sandbox", "upload_chatgpt_image(image=<attachment>)",
                    "upload_chatgpt_media", "media_type set to video or audio to match the attachment",
                    "returned file_path", "not its local path or a bare file ID", "file picker",
                    "Do not invent download URLs or file IDs", "Do not automatically retry generation",
                    "Preserve the user's explicit model, references and settings",
                ):
                    self.assertIn(instruction, action)

    async def test_local_reference_lists_and_audio_receive_recovery(self):
        cases = [
            ("edit_image", {"assets": {"image_file_paths": ["api-assets/existing.png", "sandbox:/mnt/data/reference.png"]},
                            "style": {"prompt": "Keep identity"}}),
            ("ai_voice_cloner_create_audio", {"assets": {"audio_file_path": "file:///mnt/data/voice.wav"},
                                             "style": {"prompt": "Hello"}}),
        ]
        for tool, arguments in cases:
            with self.subTest(tool=tool):
                action = await self.rejected_call(tool, arguments)
                self.assertIn("returned file_path", action)
                self.assertIn("upload_chatgpt_media", action)

    async def test_unrelated_422s_keep_generic_recovery(self):
        for path in ("api-assets/id/image.png", "https://example.test/image.png?sig=123"):
            with self.subTest(path=path):
                action = await self.rejected_call("create_video_from_image", {
                    "assets": {"image_file_path": path}, "end_seconds": 5,
                    "style": {"prompt": "A sign reading /mnt/data/image.png"},
                }, message="Unsupported duration")
                self.assertIn("Correct the values described by the API", action)
                self.assertNotIn("upload_chatgpt", action)
                self.assertNotIn("sandbox", action)

    async def test_other_api_errors_do_not_become_local_path_recovery(self):
        action = await self.rejected_call("create_video_from_image", {
            "assets": {"image_file_path": "/mnt/data/image.png"}, "end_seconds": 5,
        }, status=400, code="invalid_request", message="Unsupported setting")
        self.assertIn("Correct the fields described by the API", action)
        self.assertNotIn("upload_chatgpt", action)


if __name__ == "__main__":
    unittest.main()
