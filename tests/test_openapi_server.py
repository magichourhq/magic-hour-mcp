import unittest
from unittest.mock import AsyncMock, patch

from mcp_magichour.openapi_server import (
    _project_download_guidance_text,
    _project_status_text,
    _project_structured_content_for_agent,
    _project_to_tool_result,
    _resolve_media_mime_type,
    mcp,
)


class OpenApiServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_custom_media_fetch_tools_are_registered(self):
        tools = await mcp.list_tools()
        names = {tool.name for tool in tools}

        self.assertIn("fetch_image_download", names)
        self.assertIn("fetch_audio_download", names)
        self.assertIn("wait_for_video_project", names)
        self.assertIn("wait_for_image_project", names)
        self.assertIn("wait_for_audio_project", names)

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

        self.assertEqual(result.structured_content["exact_download_urls"], ["https://videos.magichour.ai/id/output.png?sig=123"])
        self.assertEqual(result.structured_content["downloads"], [{"url": "https://videos.magichour.ai/id/output.png?sig=123"}])
        self.assertNotIn("expires_at", result.structured_content["downloads"][0])
        self.assertEqual(result.content[0].text, "Image project img-123 completed with 1 download(s).")
        self.assertIn("EXACT_DOWNLOAD_URL[0] = https://videos.magichour.ai/id/output.png?sig=123", result.content[1].text)
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

        self.assertEqual(result.structured_content["exact_download_urls"], ["https://videos.magichour.ai/id/output.mp4?sig=123"])
        self.assertEqual(result.structured_content["downloads"], [{"url": "https://videos.magichour.ai/id/output.mp4?sig=123"}])
        self.assertEqual(result.structured_content["download_expiration_metadata"][0]["expires_at"], "2026-07-04T15:23:44.751Z")
        self.assertNotIn("expires_at", result.structured_content["downloads"][0])
        self.assertEqual(result.content[0].text, "Video project vid-123 completed with 1 download(s).")
        self.assertIn("EXACT_DOWNLOAD_URL[0] = https://videos.magichour.ai/id/output.mp4?sig=123", result.content[1].text)

    def test_project_status_text_uses_timeout_message(self):
        text = _project_status_text(
            "audio",
            {"id": "aud-123", "status": "timeout", "message": "Timed out waiting for audio project aud-123."},
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
        self.assertIn("EXACT_DOWNLOAD_URL[0] = https://videos.magichour.ai/id/output.wav?sig=123", text)
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

        self.assertEqual(structured_content["exact_download_urls"], ["https://videos.magichour.ai/id/output.wav?sig=123"])
        self.assertEqual(structured_content["downloads"], [{"url": "https://videos.magichour.ai/id/output.wav?sig=123"}])
        self.assertNotIn("expires_at", structured_content["downloads"][0])
        self.assertEqual(structured_content["download_expiration_metadata"][0]["expires_at"], "2026-07-04T15:23:44.751Z")


if __name__ == "__main__":
    unittest.main()
