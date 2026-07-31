import logging
import unittest

import httpx

from mcp_magichour.openapi_auth import (
    BearerPassthroughAuth,
    BearerPassthroughMiddleware,
    current_authorization_header,
)


LOGGER_NAME = "uvicorn.error.mcp_auth"


class OpenApiAuthLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_upstream_auth_strips_hosting_headers_and_preserves_api_headers(self):
        async def app(scope, receive, send):
            request = httpx.Request(
                "POST",
                "https://api.magichour.ai/v1/ai-image-generator",
                headers={
                    "Content-Type": "application/json",
                    "X-Forwarded-Host": "preview.vercel.app",
                    "X-Forwarded-Proto": "https",
                    "X-Vercel-Id": "iad1::secret",
                    "Origin": "https://claude.ai",
                    "Cookie": "session=secret",
                },
            )
            forwarded = next(BearerPassthroughAuth().auth_flow(request))

            self.assertEqual(forwarded.headers["Authorization"], "Bearer sk_test")
            self.assertEqual(forwarded.headers["Content-Type"], "application/json")
            self.assertNotIn("x-forwarded-host", forwarded.headers)
            self.assertNotIn("x-forwarded-proto", forwarded.headers)
            self.assertNotIn("x-vercel-id", forwarded.headers)
            self.assertNotIn("origin", forwarded.headers)
            self.assertNotIn("cookie", forwarded.headers)

            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = BearerPassthroughMiddleware(app)
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"authorization", b"Bearer sk_test")],
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            return None

        await middleware(scope, receive, send)

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
