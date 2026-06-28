import unittest

from mcp_magichour.auth import get_api_key
from mcp_magichour.errors import AuthError


class _Request:
    def __init__(self, headers):
        self.headers = headers


class _RequestContext:
    def __init__(self, request):
        self.request = request


class _Context:
    def __init__(self, request):
        self.request_context = _RequestContext(request)


class GetApiKeyTests(unittest.TestCase):
    def test_accepts_bearer_header(self):
        ctx = _Context(_Request({"authorization": "Bearer mhk_live_example"}))
        self.assertEqual(get_api_key(ctx), "mhk_live_example")

    def test_rejects_missing_request(self):
        ctx = _Context(None)

        with self.assertRaises(AuthError) as context:
            get_api_key(ctx)

        self.assertIn("Server must run over Streamable HTTP", str(context.exception))

    def test_rejects_malformed_header(self):
        ctx = _Context(_Request({"authorization": "Token nope"}))

        with self.assertRaises(AuthError) as context:
            get_api_key(ctx)

        self.assertIn("Missing or malformed Authorization header", str(context.exception))


if __name__ == "__main__":
    unittest.main()
