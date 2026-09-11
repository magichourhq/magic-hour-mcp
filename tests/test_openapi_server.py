import re
import json
import unittest
import jsonschema
from base64 import b64encode
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from mcp_magichour.openapi_server import (
    _project_download_guidance_text,
    _fetch_media_bytes,
    _project_status_text,
    _project_structured_content_for_agent,
    _project_to_tool_result,
    _resolve_media_mime_type,
    app,
    mcp,
)


class OpenApiServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_favicon_is_public_and_serves_packaged_asset(self):
        favicon_path = Path(__file__).parent.parent / "mcp_magichour" / "favicon.ico"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://mcp.example.test",
        ) as client:
            response = await client.get("/favicon.ico")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/x-icon")
        self.assertEqual(response.content, favicon_path.read_bytes())
        self.assertNotIn("www-authenticate", response.headers)

    async def test_custom_media_fetch_tools_are_registered(self):
        tools = await mcp.list_tools()
        names = {tool.name for tool in tools}

        self.assertIn("fetch_image_download", names)
        self.assertIn("fetch_audio_download", names)
        self.assertIn("fetch_video_download", names)
        self.assertIn("wait_for_video_project", names)
        self.assertIn("wait_for_image_project", names)
        self.assertIn("wait_for_audio_project", names)

    async def test_every_exposed_tool_has_explicit_submission_hints(self):
        for tool in await mcp.list_tools():
            with self.subTest(tool=tool.name):
                self.assertIsNotNone(tool.annotations)
                for hint in ("readOnlyHint", "openWorldHint", "destructiveHint"):
                    self.assertIs(type(getattr(tool.annotations, hint)), bool)
                # Every call passes through the diagnostic logging middleware.
                self.assertFalse(tool.annotations.readOnlyHint)
                self.assertFalse(tool.annotations.openWorldHint)
                self.assertEqual(
                    tool.annotations.destructiveHint,
                    tool.name
                    in {
                        f"{asset}_projects_delete"
                        for asset in ("image", "video", "audio")
                    },
                )

    async def test_wait_schemas_accept_terminal_and_timeout_results(self):
        for asset in ("image", "video", "audio"):
            tool = await mcp.get_tool(f"wait_for_{asset}_project")
            self.assertIsNotNone(tool.output_schema)
            for project in (
                {
                    "id": "example",
                    "status": "complete",
                    "downloads": [
                        {
                            "url": "https://videos.magichour.ai/example",
                            "expires_at": "2026-09-07T00:00:00Z",
                        }
                    ],
                },
                {
                    "id": "example",
                    "status": "error",
                    "downloads": [],
                    "error": {"message": "failed", "code": "failed"},
                },
                {"id": "example", "status": "canceled", "downloads": []},
                {
                    "status": "timeout",
                    "message": "Timed out",
                    "last_project": {"status": "rendering"},
                },
            ):
                result = await _project_to_tool_result(
                    asset,
                    project,
                    include_inline_downloads=False,
                    max_inline_downloads=0,
                    max_bytes_per_download=1024,
                )
                jsonschema.validate(result.structured_content, tool.output_schema)

    async def test_primary_workflows_advertise_output_schemas(self):
        names = {
            "ai_image_generator_create_image",
            "ai_image_editor_create_image",
            "image_to_video_create_video",
            "video_assets_generate_presigned_url",
            "face_swap_create_video",
            "face_swap_photo_create_image",
            "ai_talking_photo_create_talking_photo",
            "lip_sync_create_video",
        }
        names.update(
            f"{asset}_projects_retrieve_details"
            for asset in ("image", "video", "audio")
        )
        for name in names:
            self.assertIsNotNone((await mcp.get_tool(name)).output_schema, name)

    async def test_submission_matches_exposed_tools(self):
        submission = json.loads(
            (Path(__file__).parents[1] / "chatgpt-app-submission.json").read_text()
        )
        tools = {tool.name: tool for tool in await mcp.list_tools()}
        self.assertEqual(set(submission["tools"]), set(tools))
        for name, entry in submission["tools"].items():
            self.assertEqual(
                entry["annotations"],
                tools[name].annotations.model_dump(exclude_none=True),
            )
        self.assertEqual(len(submission["test_cases"]), 5)
        self.assertEqual(len(submission["negative_test_cases"]), 3)
        for case in submission["test_cases"]:
            self.assertTrue(set(case["tools_triggered"].split(", ")) <= set(tools))

    async def test_server_does_not_expose_local_filesystem_upload_tool(self):
        names = {tool.name for tool in await mcp.list_tools()}

        self.assertNotIn("upload_file_to_presigned_url", names)

    async def test_video_download_is_returned_as_embedded_binary_resource(self):
        url = "https://videos.magichour.ai/id/output.mp4?sig=123"

        tool = await mcp.get_tool("fetch_video_download")
        with patch(
            "mcp_magichour.openapi_server._fetch_media_bytes",
            new=AsyncMock(return_value=(b"video-bytes", "video/mp4")),
        ):
            result = await tool.run({"download_url": url})

        content = result.content[0]
        self.assertEqual(content.type, "resource")
        self.assertEqual(str(content.resource.uri), url)
        self.assertEqual(content.resource.mimeType, "video/mp4")
        self.assertEqual(
            content.resource.blob, b64encode(b"video-bytes").decode("ascii")
        )

    async def test_media_fetch_rejects_non_magic_hour_url(self):
        with self.assertRaisesRegex(
            ValueError, "download_url must use https://videos.magichour.ai"
        ):
            await _fetch_media_bytes("https://example.test/output.mp4", "video/", 1024)

    async def test_video_wait_accepts_shared_inline_options(self):
        tool = next(
            tool
            for tool in await mcp.list_tools()
            if tool.name == "wait_for_video_project"
        )
        properties = tool.parameters["properties"]

        self.assertEqual(properties["include_inline_downloads"]["default"], False)
        self.assertEqual(properties["max_inline_downloads"]["default"], 0)

    async def test_all_tool_names_follow_descriptive_snake_case_convention(self):
        names = {tool.name for tool in await mcp.list_tools()}

        self.assertIn("ping", names)
        self.assertIn("ai_image_generator_create_image", names)
        self.assertIn("face_detection_retrieve_details", names)
        self.assertTrue(
            all(re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", name) for name in names)
        )
        self.assertTrue(all(len(name) >= 4 for name in names))
        self.assertTrue(
            all(
                not {"do", "get", "run"}.intersection(name.split("_")) for name in names
            )
        )

    def test_resolve_media_mime_type_prefers_matching_header(self):
        mime_type = _resolve_media_mime_type(
            "https://videos.magichour.ai/id/output.png",
            "image/png; charset=binary",
            "image/",
        )

        self.assertEqual(mime_type, "image/png")

    def test_resolve_media_mime_type_falls_back_to_url_extension(self):
        mime_type = _resolve_media_mime_type(
            "https://videos.magichour.ai/id/output.mp3",
            "application/octet-stream",
            "audio/",
        )

        self.assertEqual(mime_type, "audio/mpeg")

    def test_resolve_video_mime_type_has_safe_fallback(self):
        mime_type = _resolve_media_mime_type(
            "https://videos.magichour.ai/id/output",
            "application/octet-stream",
            "video/",
        )

        self.assertEqual(mime_type, "video/mp4")

    async def test_wait_result_can_include_inline_media_and_structured_content(self):
        project = {
            "id": "img-123",
            "status": "complete",
            "downloads": [
                {
                    "url": "https://videos.magichour.ai/id/output.png?sig=123",
                    "expires_at": "2026-07-04T15:23:44.751Z",
                }
            ],
        }

        with patch(
            "mcp_magichour.openapi_server._fetch_media_bytes",
            new=AsyncMock(return_value=(b"image-bytes", "image/png")),
        ):
            result = await _project_to_tool_result(
                "image",
                project,
                include_inline_downloads=True,
                max_inline_downloads=4,
                max_bytes_per_download=1024,
            )

        self.assertEqual(
            result.structured_content["exact_download_urls"],
            ["https://videos.magichour.ai/id/output.png?sig=123"],
        )
        self.assertEqual(
            result.structured_content["downloads"],
            [{"url": "https://videos.magichour.ai/id/output.png?sig=123"}],
        )
        self.assertEqual(result.structured_content["project_type"], "image")
        self.assertNotIn("expires_at", result.structured_content["downloads"][0])
        self.assertEqual(
            result.content[0].text,
            "Image project img-123 completed with 1 download(s).",
        )
        self.assertIn(
            "EXACT_DOWNLOAD_URL[0] = https://videos.magichour.ai/id/output.png?sig=123",
            result.content[1].text,
        )
        self.assertEqual(result.content[2].type, "image")

    async def test_video_wait_result_uses_sanitized_download_fields(self):
        project = {
            "id": "vid-123",
            "status": "complete",
            "downloads": [
                {
                    "url": "https://videos.magichour.ai/id/output.mp4?sig=123",
                    "expires_at": "2026-07-04T15:23:44.751Z",
                }
            ],
        }

        result = await _project_to_tool_result(
            "video",
            project,
            include_inline_downloads=False,
            max_inline_downloads=0,
            max_bytes_per_download=1024,
        )

        self.assertEqual(
            result.structured_content["exact_download_urls"],
            ["https://videos.magichour.ai/id/output.mp4?sig=123"],
        )
        self.assertEqual(
            result.structured_content["downloads"],
            [{"url": "https://videos.magichour.ai/id/output.mp4?sig=123"}],
        )
        self.assertEqual(result.structured_content["project_type"], "video")
        self.assertEqual(
            result.structured_content["download_expiration_metadata"][0]["expires_at"],
            "2026-07-04T15:23:44.751Z",
        )
        self.assertNotIn("expires_at", result.structured_content["downloads"][0])
        self.assertEqual(
            result.content[0].text,
            "Video project vid-123 completed with 1 download(s).",
        )
        self.assertIn(
            "EXACT_DOWNLOAD_URL[0] = https://videos.magichour.ai/id/output.mp4?sig=123",
            result.content[1].text,
        )

    def test_project_status_text_uses_timeout_message(self):
        text = _project_status_text(
            "audio",
            {
                "id": "aud-123",
                "status": "timeout",
                "message": "Timed out waiting for audio project aud-123.",
            },
        )

        self.assertEqual(text, "Timed out waiting for audio project aud-123.")

    def test_project_download_guidance_text_warns_against_mutating_urls(self):
        text = _project_download_guidance_text(
            "audio",
            {
                "downloads": [
                    {
                        "url": "https://videos.magichour.ai/id/output.wav?sig=123",
                        "expires_at": "2026-07-04T15:23:44.751Z",
                    }
                ]
            },
        )

        self.assertIn("Do not shorten the URL.", text)
        self.assertIn("Do not append `downloads[n].expires_at` to the URL.", text)
        self.assertIn(
            "EXACT_DOWNLOAD_URL[0] = https://videos.magichour.ai/id/output.wav?sig=123",
            text,
        )
        self.assertIn("EXPIRES_AT[0] = 2026-07-04T15:23:44.751Z", text)

    def test_structured_content_separates_download_urls_from_expiration_metadata(self):
        structured_content = _project_structured_content_for_agent(
            {
                "id": "aud-123",
                "status": "complete",
                "downloads": [
                    {
                        "url": "https://videos.magichour.ai/id/output.wav?sig=123",
                        "expires_at": "2026-07-04T15:23:44.751Z",
                    }
                ],
            }
        )

        self.assertEqual(
            structured_content["exact_download_urls"],
            ["https://videos.magichour.ai/id/output.wav?sig=123"],
        )
        self.assertEqual(
            structured_content["downloads"],
            [{"url": "https://videos.magichour.ai/id/output.wav?sig=123"}],
        )
        self.assertNotIn("expires_at", structured_content["downloads"][0])
        self.assertEqual(
            structured_content["download_expiration_metadata"][0]["expires_at"],
            "2026-07-04T15:23:44.751Z",
        )


if __name__ == "__main__":
    unittest.main()
