import asyncio
import unittest
from unittest.mock import patch

import httpx

from mcp_magichour.errors import MagicHourToolError
from mcp_magichour.tools.audio_projects import _fetch_audio
from mcp_magichour.tools.image_projects import _fetch_image


class _ClientContext:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, _url):
        return self._response


class MediaFetchTests(unittest.TestCase):
    def test_fetch_image_uses_content_type_format(self):
        url = "https://downloads.magichour.ai/out"
        response = httpx.Response(
            200,
            content=b"image-bytes",
            headers={"content-type": "image/jpeg"},
            request=httpx.Request("GET", url),
        )

        with patch("mcp_magichour.tools.image_projects.build_http_client", return_value=_ClientContext(response)):
            image = asyncio.run(_fetch_image(url))

        self.assertEqual(image.to_image_content().mimeType, "image/jpeg")
        self.assertEqual(image.data, b"image-bytes")

    def test_fetch_image_falls_back_to_url_extension(self):
        url = "https://downloads.magichour.ai/out.png"
        response = httpx.Response(200, content=b"image-bytes", request=httpx.Request("GET", url))

        with patch("mcp_magichour.tools.image_projects.build_http_client", return_value=_ClientContext(response)):
            image = asyncio.run(_fetch_image(url))

        self.assertEqual(image.to_image_content().mimeType, "image/png")

    def test_fetch_image_translates_http_failures(self):
        url = "https://downloads.magichour.ai/out.png"
        response = httpx.Response(403, request=httpx.Request("GET", url))

        with patch("mcp_magichour.tools.image_projects.build_http_client", return_value=_ClientContext(response)):
            with self.assertRaises(MagicHourToolError) as context:
                asyncio.run(_fetch_image(url))

        self.assertIn("upstream file server returned 403", str(context.exception))

    def test_fetch_audio_uses_content_type_format(self):
        url = "https://downloads.magichour.ai/out"
        response = httpx.Response(
            200,
            content=b"audio-bytes",
            headers={"content-type": "audio/wav"},
            request=httpx.Request("GET", url),
        )

        with patch("mcp_magichour.tools.audio_projects.build_http_client", return_value=_ClientContext(response)):
            audio = asyncio.run(_fetch_audio(url))

        self.assertEqual(audio.to_audio_content().mimeType, "audio/wav")
        self.assertEqual(audio.data, b"audio-bytes")


if __name__ == "__main__":
    unittest.main()
