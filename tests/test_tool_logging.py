import logging
import unittest
from types import SimpleNamespace

import mcp.types as mt
from fastmcp.server.middleware import MiddlewareContext

from mcp_magichour.tool_logging import ToolCallLoggingMiddleware


LOGGER_NAME = "uvicorn.error.mcp_tools"


class ToolCallLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_tool_logs_safe_diagnostic_arguments(self):
        middleware = ToolCallLoggingMiddleware()
        context = MiddlewareContext(
            message=mt.CallToolRequestParams(
                name="generate_image",
                arguments={
                    "model": "default",
                    "resolution": "640px",
                    "style": {"prompt": "private user prompt", "tool": "general"},
                    "source_url": "https://example.test/private?signature=secret",
                    "api_key": "sk_secret",
                },
            ),
            method="tools/call",
        )

        async def fail(_context):
            raise ValueError("upstream failed")

        with self.assertLogs(LOGGER_NAME, level=logging.INFO) as captured:
            with self.assertRaisesRegex(ValueError, "upstream failed"):
                await middleware.on_call_tool(context, fail)

        output = "\n".join(captured.output)
        self.assertIn("tool_call_started", output)
        self.assertIn("tool_call_failed", output)
        self.assertIn('"model":"default"', output)
        self.assertIn('"tool":"general"', output)
        self.assertIn('"prompt":"[redacted]"', output)
        self.assertIn('"source_url":"[redacted]"', output)
        self.assertIn('"api_key":"[redacted]"', output)
        self.assertNotIn("private user prompt", output)
        self.assertNotIn("signature=secret", output)
        self.assertNotIn("sk_secret", output)


    async def test_media_links_and_the_users_words_in_the_video_tools_are_redacted(self):
        """The video tools name their links media, video or made, and the user's words request or story."""
        middleware = ToolCallLoggingMiddleware()
        context = MiddlewareContext(
            message=mt.CallToolRequestParams(
                name="join_production_clips",
                arguments={
                    "media": "https://cdn.example.test/clip.mp4?sig=secret1",
                    "made": {"1": "https://blob.example.test/films/a.mp4"},
                    "tracks": [{"media": "api-assets/acct/private.wav", "at": 2}],
                    "request": "a film about my private life",
                    "story": {"title": "private title"},
                    "aspect": "9:16",
                },
            ),
            method="tools/call",
        )

        async def ok(_context):
            return SimpleNamespace(is_error=False)

        with self.assertLogs(LOGGER_NAME, level=logging.INFO) as captured:
            await middleware.on_call_tool(context, ok)
        output = "\n".join(captured.output)
        for secret in ("secret1", "blob.example.test", "api-assets/", "private life", "private title"):
            self.assertNotIn(secret, output)
        self.assertIn('"aspect":"9:16"', output)
        self.assertIn('"at":2', output)


if __name__ == "__main__":
    unittest.main()
