import logging
import unittest
from types import SimpleNamespace

import mcp.types as mt
from fastmcp.server.middleware import MiddlewareContext

from mcp_magichour.tool_logging import ToolCallLoggingMiddleware


LOGGER_NAME = "fastmcp.mcp_tools"


class ToolCallLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_tools_list_logs_count_names_and_latency(self):
        middleware = ToolCallLoggingMiddleware()
        context = MiddlewareContext(
            message=mt.ListToolsRequest(
                params=mt.PaginatedRequestParams(cursor="opaque-cursor"),
            ),
            method="tools/list",
        )

        async def list_tools(_context):
            return [SimpleNamespace(name="ping"), SimpleNamespace(name="render")]

        with self.assertLogs(LOGGER_NAME, level=logging.INFO) as captured:
            result = await middleware.on_list_tools(context, list_tools)

        output = "\n".join(captured.output)
        self.assertEqual([tool.name for tool in result], ["ping", "render"])
        self.assertIn("tools_list_started cursor_present=true", output)
        self.assertIn("tools_list_completed", output)
        self.assertIn("tool_count=2", output)
        self.assertIn("tool_names=ping,render", output)
        self.assertRegex(output, r"latency_ms=\d+\.\d+")

    async def test_tools_list_logs_exception_details(self):
        middleware = ToolCallLoggingMiddleware()
        context = MiddlewareContext(
            message=mt.ListToolsRequest(),
            method="tools/list",
        )

        async def fail(_context):
            raise RuntimeError("schema registration failed")

        with self.assertLogs(LOGGER_NAME, level=logging.INFO) as captured:
            with self.assertRaisesRegex(RuntimeError, "schema registration failed"):
                await middleware.on_list_tools(context, fail)

        output = "\n".join(captured.output)
        self.assertIn("tools_list_failed cursor_present=false", output)
        self.assertIn("exception_type=RuntimeError", output)
        self.assertIn("schema registration failed", output)

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
