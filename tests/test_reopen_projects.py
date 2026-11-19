"""Reopening a saved image or audio project uses the same card as video."""

import json
import unittest
from unittest.mock import patch

import httpx
from fastmcp import Client

from mcp_magichour.openapi_auth import BearerPassthroughAuth, _authorization_header
from mcp_magichour.openapi_server import MCP_APP_VIEW_URI, create_mcp, mcp

IMAGE_DOWNLOADS = [
    {
        "url": "https://videos.magichour.ai/demo/image-1.png?sig=first",
        "expires_at": "2026-11-19T18:00:00.000Z",
    },
    {
        "url": "https://videos.magichour.ai/demo/image-2.png?sig=first",
        "expires_at": "2026-11-19T18:00:00.000Z",
    },
]
AUDIO_DOWNLOADS = [
    {
        "url": "https://videos.magichour.ai/demo/audio.wav?sig=first",
        "expires_at": "2026-11-19T18:00:00.000Z",
    },
]


def image_project(project_id="image-demo-001"):
    return {
        "id": project_id,
        "name": "Example Name",
        "status": "complete",
        "image_count": 2,
        "type": "AI_IMAGE",
        "created_at": "2024-10-19T04:16:19.027Z",
        "enabled": True,
        "credits_charged": 5,
        "downloads": IMAGE_DOWNLOADS,
        "error": None,
    }


def audio_project(project_id="audio-demo-001"):
    return {
        "id": project_id,
        "name": "Example Name",
        "status": "complete",
        "type": "VOICE_GENERATOR",
        "created_at": "2024-10-19T04:16:19.027Z",
        "enabled": True,
        "credits_charged": 2,
        "downloads": AUDIO_DOWNLOADS,
        "error": None,
    }


class ProjectReopenTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, tool, arguments):
        seen = []

        def upstream(request):
            seen.append((request.method, request.url.path, request.headers.get("authorization")))
            project_id = request.url.path.rsplit("/", 1)[-1]
            if request.url.path == f"/v1/image-projects/{project_id}" and project_id == "image-demo-001":
                return httpx.Response(200, json=image_project(project_id))
            if request.url.path == f"/v1/audio-projects/{project_id}" and project_id == "audio-demo-001":
                return httpx.Response(200, json=audio_project(project_id))
            return httpx.Response(404, json={"code": "not_found", "message": "Project not found"})

        token = _authorization_header.set("Bearer synthetic-test-token")
        try:
            async with httpx.AsyncClient(
                base_url="https://api.example.test",
                auth=BearerPassthroughAuth(),
                transport=httpx.MockTransport(upstream),
            ) as api:
                with patch("mcp_magichour.openapi_server.build_api_client", return_value=api):
                    server = create_mcp()
                async with Client(server) as caller:
                    result = await caller.call_tool(tool, arguments, raise_on_error=False)
        finally:
            _authorization_header.reset(token)
        return result, seen

    async def test_image_and_audio_reopen_tools_advertise_the_result_card(self):
        tools = {tool.name: tool for tool in await mcp.list_tools()}
        expected = {
            "get_image_project": ("Opening your image project…", "Image project opened"),
            "get_audio_project": ("Opening your audio project…", "Audio project opened"),
        }

        for name, (invoking, invoked) in expected.items():
            with self.subTest(tool=name):
                tool = tools[name]
                meta = tool.meta or {}
                self.assertEqual(meta.get("ui", {}).get("resourceUri"), MCP_APP_VIEW_URI)
                self.assertEqual(meta.get("openai/toolInvocation", {}).get("invoking"), invoking)
                self.assertEqual(meta.get("openai/toolInvocation", {}).get("invoked"), invoked)
                self.assertLessEqual(len(invoking), 64)
                self.assertLessEqual(len(invoked), 64)
                self.assertTrue(tool.annotations.readOnlyHint)
                self.assertIn("result card", tool.description)
                self.assertIn("Read-only", tool.description)
                self.assertIn("existing project ID", tool.description)

    async def test_image_reopen_keeps_every_output_and_the_original_downloads(self):
        result, seen = await self.call("get_image_project", {"id": "image-demo-001"})

        self.assertFalse(result.is_error)
        self.assertEqual(
            seen,
            [("GET", "/v1/image-projects/image-demo-001", "Bearer synthetic-test-token")],
        )
        project = result.structured_content
        self.assertEqual(project["id"], "image-demo-001")
        self.assertEqual(project.get("project_type"), "image")
        self.assertEqual(project["downloads"], IMAGE_DOWNLOADS)
        self.assertEqual(
            project["exact_download_urls"],
            [item["url"] for item in IMAGE_DOWNLOADS],
        )
        self.assertEqual(
            [item["expires_at"] for item in project["download_expiration_metadata"]],
            [item["expires_at"] for item in IMAGE_DOWNLOADS],
        )
        self.assertEqual(
            [item["download_index"] for item in project["download_expiration_metadata"]],
            ["0", "1"],
        )

    async def test_audio_reopen_keeps_the_original_download(self):
        result, seen = await self.call("get_audio_project", {"id": "audio-demo-001"})

        self.assertFalse(result.is_error)
        self.assertEqual(
            seen,
            [("GET", "/v1/audio-projects/audio-demo-001", "Bearer synthetic-test-token")],
        )
        project = result.structured_content
        self.assertEqual(project["id"], "audio-demo-001")
        self.assertEqual(project.get("project_type"), "audio")
        self.assertEqual(project["downloads"], AUDIO_DOWNLOADS)
        self.assertEqual(project["exact_download_urls"], [AUDIO_DOWNLOADS[0]["url"]])
        self.assertEqual(
            project["download_expiration_metadata"][0]["expires_at"],
            AUDIO_DOWNLOADS[0]["expires_at"],
        )
        self.assertEqual(project["download_expiration_metadata"][0]["download_index"], "0")

    async def test_inaccessible_project_returns_no_media(self):
        for tool, project_id in (
            ("get_image_project", "missing-image"),
            ("get_audio_project", "missing-audio"),
        ):
            with self.subTest(tool=tool):
                result, seen = await self.call(tool, {"id": project_id})
                rendered = json.dumps(
                    {
                        "structured": result.structured_content,
                        "content": [getattr(block, "text", "") for block in result.content],
                    }
                )
                self.assertTrue(result.is_error)
                kind = "image" if tool == "get_image_project" else "audio"
                self.assertEqual(
                    seen,
                    [("GET", f"/v1/{kind}-projects/{project_id}", "Bearer synthetic-test-token")],
                )
                self.assertNotIn("videos.magichour.ai", rendered)
                self.assertNotIn("exact_download_urls", rendered)
                self.assertIn("Do not create a replacement generation", rendered)
