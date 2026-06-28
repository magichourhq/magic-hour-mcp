import os
import unittest
from unittest.mock import patch

from magic_hour.environment import Environment

from mcp_magichour.client import API_TIMEOUT, build_http_client, _environment


class ClientConfigTests(unittest.TestCase):
    def test_environment_uses_mock_when_enabled(self):
        with patch.dict(os.environ, {"MAGIC_HOUR_ENVIRONMENT": "mock"}, clear=False):
            self.assertEqual(_environment(), Environment.MOCK_SERVER)

    def test_environment_uses_default_when_mock_not_enabled(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_environment(), Environment.ENVIRONMENT)

    def test_build_http_client_uses_shared_defaults(self):
        client = build_http_client(follow_redirects=True)
        try:
            self.assertTrue(client.follow_redirects)
            self.assertEqual(client.timeout.connect, API_TIMEOUT.connect)
            self.assertEqual(client.timeout.read, API_TIMEOUT.read)
            self.assertEqual(client.timeout.write, API_TIMEOUT.write)
            self.assertEqual(client.timeout.pool, API_TIMEOUT.pool)
        finally:
            import asyncio

            asyncio.run(client.aclose())


if __name__ == "__main__":
    unittest.main()
