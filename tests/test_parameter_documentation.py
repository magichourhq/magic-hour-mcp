"""Metadata changes must not alter validation, defaults or upstream payloads."""
import json
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import httpx
from fastmcp import Client

from mcp_magichour.openapi_policies import apply_magic_hour_policies
from mcp_magichour.openapi_server import create_mcp


def without_descriptions(value):
    if isinstance(value, dict):
        return {key: without_descriptions(item) for key, item in value.items() if key != "description"}
    if isinstance(value, list):
        return [without_descriptions(item) for item in value]
    return value


class ParameterDocumentationTests(unittest.TestCase):
    def setUp(self):
        self.spec = json.loads((Path(__file__).parent.parent / "docs/openapi.json").read_text())

    def test_all_api_contracts_unchanged_except_descriptions(self):
        original = deepcopy(self.spec)
        updated = apply_magic_hour_policies(self.spec)
        for path, methods in self.spec["paths"].items():
            for method, operation in methods.items():
                for field in ("requestBody", "parameters", "responses", "security"):
                    with self.subTest(path=path, method=method, field=field):
                        self.assertEqual(
                            without_descriptions(updated["paths"][path][method].get(field)),
                            without_descriptions(operation.get(field)),
                        )
        self.assertEqual(self.spec, original)
        self.assertEqual(apply_magic_hour_policies(updated), updated)

    def test_all_current_request_properties_have_descriptions(self):
        def check(schema, location):
            for name, parameter in schema.get("properties", {}).items():
                with self.subTest(location=location, parameter=name):
                    self.assertTrue(parameter.get("description"))
                check(parameter, f"{location}.{name}")
            if "items" in schema:
                check(schema["items"], f"{location}[]")

        for path, methods in apply_magic_hour_policies(self.spec)["paths"].items():
            for method, operation in methods.items():
                schema = operation.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
                check(schema, f"{method} {path}")

    def test_default_name_guidance_does_not_invent_unknown_defaults(self):
        operation = self.spec["paths"]["/v1/photo-colorizer"]["post"]
        schema = operation["requestBody"]["content"]["application/json"]["schema"]
        updated = apply_magic_hour_policies(self.spec)["paths"]["/v1/photo-colorizer"]["post"]
        self.assertIn("Omit to use", updated["requestBody"]["content"]["application/json"]["schema"]["properties"]["name"]["description"])
        del schema["properties"]["name"]["default"]
        updated = apply_magic_hour_policies(self.spec)["paths"]["/v1/photo-colorizer"]["post"]
        self.assertEqual(updated["requestBody"], operation["requestBody"])
        schema["properties"]["name"]["default"] = "Example"
        schema["required"].append("name")
        updated = apply_magic_hour_policies(self.spec)["paths"]["/v1/photo-colorizer"]["post"]
        self.assertEqual(updated["requestBody"], operation["requestBody"])

    def test_restyle_guidance_preserves_canonical_version_and_model_descriptions(self):
        operation = self.spec["paths"]["/v1/video-to-video"]["post"]
        updated = apply_magic_hour_policies(self.spec)["paths"]["/v1/video-to-video"]["post"]
        original_style = operation["requestBody"]["content"]["application/json"]["schema"]["properties"]["style"]
        updated_style = updated["requestBody"]["content"]["application/json"]["schema"]["properties"]["style"]
        for parameter in ("version", "model"):
            self.assertEqual(updated_style["properties"][parameter], original_style["properties"][parameter])
        self.assertIn("v3 requires a supplied reference image", updated["description"])
        self.assertNotIn("not v3", updated["description"])


class ParameterWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_omitted_and_explicit_names_and_references_are_forwarded_unchanged(self):
        cases = [
            ("colorize_photo", "/v1/photo-colorizer", {"assets": {"image_file_path": "uploaded/photo.png"}}),
            ("create_video_from_text", "/v1/text-to-video", {"end_seconds": 5, "style": {"prompt": "Animate @hero"}, "references": [{"name": "hero", "file_path": "uploaded/hero.png"}]}),
            ("restyle_video", "/v1/video-to-video", {
                "start_seconds": 0, "end_seconds": 2, "width": 1024, "height": 704,
                "style": {"art_style": "Studio Ghibli", "version": "v3", "model": "default"},
                "assets": {"video_source": "file", "video_file_path": "uploaded/video.mp4", "initial_image_path": "uploaded/reference.png"},
            }),
            ("ai_voice_generator_create_audio", "/v1/ai-voice-generator", {"style": {"prompt": "Hello"}}),
        ]
        # Use the canonical current voice value rather than embedding a drifting preset.
        spec = json.loads((Path(__file__).parent.parent / "docs/openapi.json").read_text())
        cases[-1][2]["style"]["voice_name"] = spec["paths"]["/v1/ai-voice-generator"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["style"]["properties"]["voice_name"]["enum"][0]
        forwarded = []

        def upstream(request):
            forwarded.append((request.url.path, json.loads(request.content)))
            return httpx.Response(200, json={"id": "test-project", "credits_charged": 1})

        async with httpx.AsyncClient(base_url="https://api.example.test", transport=httpx.MockTransport(upstream)) as api:
            with patch("mcp_magichour.openapi_server.build_api_client", return_value=api):
                server = create_mcp()
            with patch("mcp_magichour.oauth_compat.current_authorization_header", return_value="Bearer synthetic-test-token"):
                async with Client(server) as client:
                    for name, path, arguments in cases:
                        for named in (False, True):
                            payload = dict(arguments, name="User's exact label") if named else arguments
                            with self.subTest(tool=name, named=named):
                                self.assertFalse((await client.call_tool(name, payload)).is_error)
                                self.assertEqual(forwarded[-1], (path, payload))
        self.assertEqual(len(forwarded), 8)

    async def test_ping_preserves_native_text_and_structured_result(self):
        def unexpected_request(request):
            self.fail("ping must not contact the upstream API")

        async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected_request)) as api:
            with patch("mcp_magichour.openapi_server.build_api_client", return_value=api):
                server = create_mcp()
            with patch("mcp_magichour.oauth_compat.current_authorization_header", return_value="Bearer synthetic-test-token"):
                async with Client(server) as client:
                    ping = next(tool for tool in await client.list_tools() if tool.name == "ping")
                    result = await client.call_tool("ping", {})
        self.assertIn("not API credentials", ping.outputSchema["properties"]["result"]["description"])
        self.assertEqual(result.structured_content, {"result": "pong"})
        self.assertEqual([item.text for item in result.content], ["pong"])
        self.assertFalse(result.is_error)
