import json
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastmcp import Client

from mcp_magichour.openapi_policies import apply_magic_hour_policies, image_model_resolutions
from mcp_magichour.openapi_server import create_mcp

SPEC = json.loads((Path(__file__).parent.parent / "docs/openapi.json").read_text())


class ImageResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, tool: str, arguments: dict):
        requests = []

        def upstream(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "proj_1", "credits_charged": 5})

        async with httpx.AsyncClient(
            base_url="https://api.example.test", transport=httpx.MockTransport(upstream),
        ) as client:
            with patch("mcp_magichour.openapi_server.build_api_client", return_value=client):
                server = create_mcp()
            with patch("mcp_magichour.oauth_compat.current_authorization_header", return_value="Bearer test-token"):
                async with Client(server) as caller:
                    result = await caller.call_tool(tool, arguments, raise_on_error=False)
        return result, requests

    def test_reads_per_model_resolutions_from_spec(self):
        rules = image_model_resolutions(SPEC)
        self.assertEqual(rules["generate_image"]["z-image-turbo"], {"640px", "1k", "2k"})
        self.assertEqual(rules["generate_image"]["gpt-image-2"], {"640px", "1k", "2k", "4k"})
        self.assertEqual(rules["edit_image"]["nano-banana-pro"], {"1k", "2k", "4k"})
        self.assertEqual(image_model_resolutions({"paths": {}}), {})

    async def test_unsupported_pair_is_rejected_before_the_api(self):
        style = {"prompt": "A colorful nebula"}
        result, requests = await self.call("generate_image", {
            "model": "z-image-turbo", "resolution": "4k", "image_count": 1,
            "aspect_ratio": "16:9", "style": style,
        })
        self.assertTrue(result.is_error)
        self.assertEqual(requests, [], "The API must not be called")
        error = result.structured_content["error"]
        self.assertEqual(error["code"], "unsupported_resolution")
        self.assertIn("z-image-turbo does not support 4k", error["message"])
        self.assertIn("Nothing was generated or charged", error["message"])
        self.assertIn("gpt-image-2", error["supported_models"])
        self.assertNotIn("z-image-turbo", error["supported_models"])
        self.assertEqual(error["closest_lower_resolution"], "2k")
        self.assertIsNone(error["closest_higher_resolution"])
        for choice in ("switch to a model that supports 4k", "go down to 2k", "Ask which they want"):
            self.assertIn(choice, error["next_action"])

    async def test_unsupported_low_resolution_offers_going_up(self):
        result, requests = await self.call("generate_image", {
            "model": "nano-banana-pro", "resolution": "640px", "image_count": 1,
            "aspect_ratio": "16:9", "style": {"prompt": "A colorful nebula"},
        })
        self.assertEqual(requests, [])
        error = result.structured_content["error"]
        self.assertIsNone(error["closest_lower_resolution"])
        self.assertEqual(error["closest_higher_resolution"], "1k")
        self.assertIn("go up to 1k", error["next_action"])
        self.assertNotIn("go down", error["next_action"])

    async def test_supported_default_and_auto_requests_reach_the_api(self):
        style = {"prompt": "A colorful nebula"}
        for arguments in (
            {"model": "gpt-image-2", "resolution": "4k"},
            {"model": "default", "resolution": "2k"},
            {"model": "z-image-turbo", "resolution": "auto"},
            {"model": "z-image-turbo"},
        ):
            with self.subTest(arguments=arguments):
                result, requests = await self.call("generate_image", {
                    **arguments, "image_count": 1, "aspect_ratio": "16:9", "style": style,
                })
                self.assertFalse(result.is_error)
                self.assertEqual(len(requests), 1)

    async def test_default_model_is_checked_as_the_model_it_uses(self):
        style = {"prompt": "A colorful nebula"}
        for arguments, message in (
            ({"model": "default", "resolution": "4k"}, "The default model (z-image-turbo) does not support 4k"),
            ({"resolution": "4k"}, "The default model (z-image-turbo) does not support 4k"),
        ):
            with self.subTest(arguments=arguments):
                result, requests = await self.call("generate_image", {
                    **arguments, "image_count": 1, "aspect_ratio": "16:9", "style": style,
                })
                self.assertTrue(result.is_error)
                self.assertEqual(requests, [])
                error = result.structured_content["error"]
                self.assertIn(message, error["message"])
                self.assertIn("gpt-image-2", error["supported_models"])
                self.assertNotIn("default", error["supported_models"])
        self.assertEqual(image_model_resolutions(SPEC)["edit_image"]["default"], {"640px", "1k"})

    def test_model_descriptions_say_to_match_resolution(self):
        patched = apply_magic_hour_policies(SPEC)
        for path in ("/v1/ai-image-generator", "/v1/ai-image-editor"):
            with self.subTest(path=path):
                model = patched["paths"][path]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["model"]
                self.assertIn("pick a model whose supported resolutions include it", model["description"])
                self.assertIn("`default` is", model["description"])
                self.assertEqual(model["description"].count("pick a model whose supported resolutions"), 1)
        self.assertEqual(apply_magic_hour_policies(patched), patched)


if __name__ == "__main__":
    unittest.main()
