import unittest

import httpx
from make_api_request import ApiError

from mcp_magichour.errors import translate_api_error, translate_http_error


def _api_error(status_code: int, body):
    request = httpx.Request("GET", "https://api.magichour.ai/v1/test")
    response = httpx.Response(status_code, json=body, request=request)
    return ApiError(response=response)


class TranslateApiErrorTests(unittest.TestCase):
    def test_401_gets_bad_key_guidance(self):
        error = translate_api_error(_api_error(401, {"message": "Unauthorized"}))
        self.assertIn("401 Unauthorized", str(error))
        self.assertIn("Authorization header", str(error))

    def test_402_mentions_credits_or_plan_access(self):
        error = translate_api_error(_api_error(402, {"message": "Payment Required"}))
        self.assertIn("402", str(error))
        self.assertIn("credits or plan access", str(error))

    def test_other_status_keeps_api_message(self):
        error = translate_api_error(_api_error(422, {"message": "Validation failed"}))
        self.assertEqual(str(error), "Magic Hour API request failed with 422: Validation failed")


class TranslateHttpErrorTests(unittest.TestCase):
    def test_timeout_is_actionable(self):
        error = translate_http_error(httpx.ReadTimeout("boom"), during="calling the Magic Hour API")
        self.assertEqual(str(error), "Timed out while calling the Magic Hour API. Please retry.")

    def test_http_status_error_mentions_upstream_status(self):
        request = httpx.Request("GET", "https://downloads.magichour.ai/file.png")
        response = httpx.Response(403, request=request)
        source_error = httpx.HTTPStatusError("forbidden", request=request, response=response)
        error = translate_http_error(source_error, during="downloading generated image output")
        self.assertEqual(
            str(error),
            "Failed while downloading generated image output: upstream file server returned 403.",
        )


if __name__ == "__main__":
    unittest.main()
