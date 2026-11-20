"""Reopening a saved image or audio project uses the same card as video."""

import json
import unittest
from unittest.mock import patch
from urllib.parse import urlparse

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


def downloads_with_signature(downloads, signature):
    refreshed = []
    for item in downloads:
        refreshed.append({**item, "url": f"https://videos.magichour.ai{urlparse(item['url']).path}?sig={signature}"})
    return refreshed


def output_paths(project):
    return [urlparse(url).path for url in project["exact_download_urls"]]


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

    async def reopen_then_refresh(self, kind, project_id):
        seen = []
        reads = {"count": 0}
        source = IMAGE_DOWNLOADS if kind == "image" else AUDIO_DOWNLOADS

        def project(signature):
            body = image_project(project_id) if kind == "image" else audio_project(project_id)
            body["downloads"] = downloads_with_signature(source, signature)
            return body

        def upstream(request):
            seen.append((request.method, request.url.path, request.headers.get("authorization")))
            if request.method != "GET" or request.url.path != f"/v1/{kind}-projects/{project_id}":
                return httpx.Response(500, json={"message": "unexpected create or project route"})
            reads["count"] += 1
            signature = "first" if reads["count"] == 1 else "second"
            return httpx.Response(200, json=project(signature))

        token = _authorization_header.set("Bearer synthetic-test-token")
        try:
            transport = httpx.MockTransport(upstream)

            def api_client():
                return httpx.AsyncClient(
                    base_url="https://api.example.test",
                    auth=BearerPassthroughAuth(),
                    transport=transport,
                )

            async with api_client() as opened_client:
                def build_client():
                    # The project GET reuses the server's open client. The wait tool opens its own.
                    if not build_client.served_openapi:
                        build_client.served_openapi = True
                        return opened_client
                    return api_client()

                build_client.served_openapi = False
                with patch("mcp_magichour.openapi_server.build_api_client", side_effect=build_client):
                    server = create_mcp()
                    async with Client(server) as caller:
                        opened = await caller.call_tool(f"get_{kind}_project", {"id": project_id}, raise_on_error=False)
                        refreshed = await caller.call_tool(
                            f"wait_for_{kind}_project",
                            {
                                "id": project_id,
                                "timeout_seconds": 5,
                                "include_inline_downloads": False,
                                "max_inline_downloads": 0,
                            },
                            raise_on_error=False,
                        )
        finally:
            _authorization_header.reset(token)
        return opened, refreshed, seen

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

    async def test_image_refresh_keeps_the_second_output_selected(self):
        opened, refreshed, seen = await self.reopen_then_refresh("image", "image-demo-001")
        project = refreshed.structured_content
        paths = ["/demo/image-1.png", "/demo/image-2.png"]

        self.assertFalse(opened.is_error)
        self.assertFalse(refreshed.is_error)
        self.assertEqual(
            seen,
            [("GET", "/v1/image-projects/image-demo-001", "Bearer synthetic-test-token")] * 2,
        )
        self.assertTrue(all(url.endswith("?sig=first") for url in opened.structured_content["exact_download_urls"]))
        self.assertEqual(output_paths(opened.structured_content), paths)
        self.assertEqual(project.get("project_type"), "image")
        self.assertEqual(output_paths(project), paths)
        self.assertTrue(all(url.endswith("?sig=second") for url in project["exact_download_urls"]))
        self.assertEqual(
            [item["download_index"] for item in project["download_expiration_metadata"]],
            ["0", "1"],
        )
        self.assertEqual(urlparse(project["exact_download_urls"][1]).path, "/demo/image-2.png")

    async def test_audio_refresh_keeps_the_same_output(self):
        opened, refreshed, seen = await self.reopen_then_refresh("audio", "audio-demo-001")
        project = refreshed.structured_content

        self.assertFalse(opened.is_error)
        self.assertFalse(refreshed.is_error)
        self.assertEqual(
            seen,
            [("GET", "/v1/audio-projects/audio-demo-001", "Bearer synthetic-test-token")] * 2,
        )
        self.assertTrue(opened.structured_content["exact_download_urls"][0].endswith("?sig=first"))
        self.assertEqual(project.get("project_type"), "audio")
        self.assertEqual(output_paths(opened.structured_content), ["/demo/audio.wav"])
        self.assertEqual(output_paths(project), ["/demo/audio.wav"])
        self.assertTrue(project["exact_download_urls"][0].endswith("?sig=second"))
        self.assertEqual(project["download_expiration_metadata"][0]["download_index"], "0")
