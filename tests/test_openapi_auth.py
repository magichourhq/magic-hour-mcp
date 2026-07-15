import logging
import unittest

from mcp_magichour.openapi_auth import BearerPassthroughMiddleware, current_authorization_header


LOGGER_NAME = "uvicorn.error.mcp_auth"


class OpenApiAuthLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_log_reports_bearer_presence_without_leaking_token(self):
        async def app(scope, receive, send):
            self.assertEqual(current_authorization_header(), "Bearer super-secret-token")
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = BearerPassthroughMiddleware(app)
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/mcp",
            "headers": [(b"authorization", b"Bearer super-secret-token")],
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            return None

        with self.assertLogs(LOGGER_NAME, level=logging.INFO) as captured:
            await middleware(scope, receive, send)

        output = "\n".join(captured.output)
        self.assertIn("auth_present=true", output)
        self.assertIn("auth_scheme=bearer", output)
        self.assertIn("status=200", output)
        self.assertNotIn("super-secret-token", output)

    async def test_missing_authorization_logs_rejection_reason(self):
        async def app(scope, receive, send):
            current_authorization_header()

        middleware = BearerPassthroughMiddleware(app)
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/mcp",
            "headers": [],
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            return None

        with self.assertLogs(LOGGER_NAME, level=logging.INFO) as captured:
            with self.assertRaisesRegex(Exception, "Missing Authorization header"):
                await middleware(scope, receive, send)

        output = "\n".join(captured.output)
        self.assertIn("auth_present=false", output)
        self.assertIn("auth_rejected", output)
        self.assertIn("reason=missing", output)
        self.assertIn("request_failed", output)


if __name__ == "__main__":
    unittest.main()
