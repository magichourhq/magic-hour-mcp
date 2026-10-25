import io
import json
import unittest
import urllib.error
from unittest.mock import Mock

from registry_preflight import check_unpublished


class Response(io.BytesIO):
    status = 200


class RegistryPreflightTests(unittest.TestCase):
    manifest = {"name": "io.github.magichourhq/magic-hour", "version": "0.1.0"}

    def test_published_version_requires_bump(self):
        opener = Mock(return_value=Response(json.dumps({"server": self.manifest}).encode()))
        with self.assertRaisesRegex(ValueError, "already published"):
            check_unpublished(self.manifest, opener)
        request = opener.call_args.args[0]
        self.assertIn("io.github.magichourhq%2Fmagic-hour/versions/0.1.0", request.full_url)
        self.assertEqual(request.get_method(), "GET")
        self.assertNotIn("Authorization", request.headers)

    def test_unpublished_exact_version_is_allowed(self):
        opener = Mock(side_effect=urllib.error.HTTPError("url", 404, "Not Found", {}, None))
        self.assertIn("/versions/0.1.0", check_unpublished(self.manifest, opener))

    def test_rate_limit_does_not_allow_publication(self):
        opener = Mock(side_effect=urllib.error.HTTPError("url", 429, "Rate limited", {}, None))
        with self.assertRaisesRegex(RuntimeError, "HTTP 429"):
            check_unpublished(self.manifest, opener)

    def test_network_failure_does_not_allow_publication(self):
        with self.assertRaisesRegex(RuntimeError, "verification failed"):
            check_unpublished(self.manifest, Mock(side_effect=urllib.error.URLError("offline")))

    def test_malformed_response_does_not_allow_publication(self):
        with self.assertRaisesRegex(RuntimeError, "verification failed"):
            check_unpublished(self.manifest, Mock(return_value=Response(b"<html>challenge</html>")))

    def test_wrong_record_does_not_allow_publication(self):
        with self.assertRaisesRegex(RuntimeError, "unexpected server/version"):
            check_unpublished(self.manifest, Mock(return_value=Response(b'{"server":{"name":"other","version":"0.1.0"}}')))

    def test_missing_version_never_queries_registry(self):
        opener = Mock()
        with self.assertRaisesRegex(ValueError, "version"):
            check_unpublished({"name": self.manifest["name"]}, opener)
        opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
