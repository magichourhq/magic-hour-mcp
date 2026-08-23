import logging
import unittest

import mcp.types as mt
from fastmcp.server.middleware import MiddlewareContext

from mcp_magichour.tool_logging import ToolCallLoggingMiddleware


LOGGER_NAME = "uvicorn.error.mcp_tools"


class ToolCallLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_tool_logs_safe_diagnostic_arguments(self):
        middleware = ToolCallLoggingMiddleware()
        context = MiddlewareContext(
            message=mt.CallToolRequestParams(
                name="ai_image_generator_create_image",
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


if __name__ == "__main__":
    unittest.main()
