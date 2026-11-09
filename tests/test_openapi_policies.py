import json
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from mcp_magichour.openapi_policies import (
    API_TOOL_METADATA,
    apply_magic_hour_policies,
    customize_openapi_component,
    normalize_mcp_tool_name,
)


class OpenApiPolicyTests(unittest.TestCase):
    def test_task_metadata_preserves_api_contracts(self):
        spec = json.loads((Path(__file__).parent.parent / "docs/openapi.json").read_text())
        with patch("mcp_magichour.openapi_policies.API_TOOL_METADATA", {}):
            baseline = apply_magic_hour_policies(spec)
        updated = apply_magic_hour_policies(spec)
        # Tool metadata may change names and copy, while input/output schemas,
        # API parameters and unrelated operations remain unchanged.
        for path in baseline["paths"]:
            for method, operation in baseline["paths"][path].items():
                actual = deepcopy(updated["paths"][path][method])
                if (method.upper(), path) in API_TOOL_METADATA:
                    for field in ("operationId", "summary", "description"):
                        if field in operation:
                            actual[field] = operation[field]
                        else:
                            actual.pop(field, None)
                self.assertEqual(actual, operation, f"{method} {path}")
        self.assertEqual(apply_magic_hour_policies(updated), updated)

    def test_likeness_guidance_only_appends_to_target_tool_descriptions(self):
        spec = json.loads((Path(__file__).parent.parent / "docs/openapi.json").read_text())
        original = deepcopy(spec)
        expected_tools = {
            "/v1/face-swap": "swap_face_in_video",
            "/v1/face-swap-photo": "swap_face_in_photo",
            "/v1/head-swap": "swap_head_in_photo",
            "/v1/body-swap": "place_person_in_scene",
            "/v1/lip-sync": "create_lip_sync_video",
            "/v1/ai-talking-photo": "create_talking_photo",
            "/v1/character-replace": "replace_character_in_video",
            "/v1/ai-clothes-changer": "change_clothes_in_photo",
        }
        suffix = (
            "Use only with the user's own likeness or content they are authorized to use. "
            "Do not use for impersonation, deception, sexual content, or content involving minors."
        )
        with patch("mcp_magichour.openapi_policies.LIKENESS_GENERATION_PATHS", set()):
            baseline = apply_magic_hour_policies(spec)
        patched = apply_magic_hour_policies(spec)
        self.assertEqual(apply_magic_hour_policies(patched), patched)
        descriptions_restored = deepcopy(patched)
        for path, tool_name in expected_tools.items():
            operation = patched["paths"][path]["post"]
            description = baseline["paths"][path]["post"]["description"]
            self.assertEqual(operation["operationId"], tool_name)
            self.assertEqual(operation["description"], f"{suffix}\n\n{description}")
            descriptions_restored["paths"][path]["post"]["description"] = description
        self.assertEqual(descriptions_restored, baseline)
        self.assertEqual(spec, original)

    def test_restored_tools_preserve_api_input_schemas(self):
        spec = json.loads((Path(__file__).parent.parent / "docs/openapi.json").read_text())
        updated = apply_magic_hour_policies(spec)
        for path in ("/v1/ai-voice-generator", "/v1/ai-voice-cloner", "/v1/ai-meme-generator"):
            with self.subTest(path=path):
                self.assertEqual(
                    updated["paths"][path]["post"]["requestBody"],
                    spec["paths"][path]["post"]["requestBody"],
                )
        cloner = updated["paths"]["/v1/ai-voice-cloner"]["post"]
        self.assertIn("a voice you have permission to clone", cloner["description"])

    def test_generation_post_gets_polling_guidance(self):
        spec = {
            "paths": {
                "/v1/ai-image-generator": {
                    "post": {
                        "tags": ["Image Projects"],
                        "operationId": "aiImageGenerator.createImage",
                        "description": "Create an image.",
                    }
                }
            }
        }

        patched = apply_magic_hour_policies(spec)
        operation_id = patched["paths"]["/v1/ai-image-generator"]["post"]["operationId"]
        description = patched["paths"]["/v1/ai-image-generator"]["post"]["description"]

        self.assertEqual(operation_id, "generate_image")
        self.assertIn("wait_for_image_project", description)

    def test_file_path_inputs_get_upload_guidance(self):
        spec = {
            "paths": {
                "/v1/new-video-tool": {
                    "post": {
                        "tags": ["Video Projects"],
                        "description": "Animate an image.",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {"properties": {"image_file_path": {"type": "string"}}}
                                }
                            }
                        },
                    }
                }
            }
        }

        patched = apply_magic_hour_policies(spec)
        description = patched["paths"]["/v1/new-video-tool"]["post"]["description"]

        self.assertIn("upload-URL endpoint", description)
        self.assertIn("file_path", description)
        self.assertIn("Direct public media URLs may work", description)
        self.assertIn("hotlinked URLs can fail", description)

    def test_custom_component_adds_group_tags(self):
        class Route:
            method = "POST"
            path = "/v1/text-to-video"
            tags = ["Video Projects"]

        class Component:
            tags = set()

        component = Component()
        customize_openapi_component(Route(), component)

        self.assertIn("magic-hour", component.tags)
        self.assertIn("write-operation", component.tags)
        self.assertIn("generation", component.tags)

    def test_unknown_project_post_gets_group_policy_without_endpoint_specific_config(self):
        spec = {
            "paths": {
                "/v1/new-video-tool": {
                    "post": {
                        "tags": ["Video Projects"],
                        "operationId": "newVideoTool.createVideo",
                        "description": "Create a new kind of video.",
                    }
                }
            }
        }

        patched = apply_magic_hour_policies(spec)
        description = patched["paths"]["/v1/new-video-tool"]["post"]["description"]

        self.assertIn("wait_for_video_project", description)
        self.assertIn("GET /v1/video-projects/{id}", description)

    def test_tool_names_are_snake_case_and_replace_generic_actions(self):
        self.assertEqual(normalize_mcp_tool_name("faceDetection.getDetails"), "face_detection_retrieve_details")
        self.assertEqual(normalize_mcp_tool_name("videoAssets.generatePresignedUrl"), "video_assets_generate_presigned_url")

    def test_tool_names_must_have_at_least_four_characters(self):
        with self.assertRaisesRegex(ValueError, "at least 4 characters"):
            normalize_mcp_tool_name("id")


if __name__ == "__main__":
    unittest.main()
