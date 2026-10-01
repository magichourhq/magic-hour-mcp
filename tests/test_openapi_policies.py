import json
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import jsonschema

from mcp_magichour.openapi_policies import (
    apply_magic_hour_policies,
    customize_openapi_component,
    normalize_mcp_tool_name,
    VOICE_PRESET_DESCRIPTION,
)


class OpenApiPolicyTests(unittest.TestCase):
    def test_voice_policy_replaces_synced_enum_copy_and_examples(self):
        spec = json.loads((Path(__file__).parent.parent / "docs/openapi.json").read_text())
        operation = spec["paths"]["/v1/ai-voice-generator"]["post"]
        schema = operation["requestBody"]["content"]["application/json"]["schema"]
        voice = schema["properties"]["style"]["properties"]["voice_name"]
        # Synthetic approvals exercise policy plumbing, not real preset recommendations.
        approved = ("Test approved preset A", "Test approved preset B")
        unapproved = "Unapproved upstream preset"
        voice["enum"].extend([*approved, unapproved])
        voice["oneOf"] = [{"const": unapproved}]
        operation["description"] = unapproved
        operation["summary"] = unapproved
        operation["x-codeSamples"] = [{"source": unapproved}]
        schema["examples"] = [{"style": {"voice_name": unapproved}}]
        schema["properties"]["name"]["default"] = unapproved
        original = deepcopy(spec)
        baseline = apply_magic_hour_policies(original)

        with patch("mcp_magichour.openapi_policies.APPROVED_VOICE_NAMES", approved):
            patched = apply_magic_hour_policies(spec)

        safe_operation = patched["paths"]["/v1/ai-voice-generator"]["post"]
        safe_schema = safe_operation["requestBody"]["content"]["application/json"]["schema"]
        safe_voice = safe_schema["properties"]["style"]["properties"]["voice_name"]
        self.assertEqual(safe_voice, {
            "type": "string", "enum": list(approved), "description": VOICE_PRESET_DESCRIPTION,
        })
        self.assertNotIn(unapproved, json.dumps(safe_operation))
        self.assertEqual(spec, original)
        for name in approved:
            jsonschema.validate({"style": {"prompt": "Hello", "voice_name": name}}, safe_schema)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate({"style": {"prompt": "Hello", "voice_name": unapproved}}, safe_schema)
        for path in spec["paths"]:
            if path != "/v1/ai-voice-generator":
                self.assertEqual(patched["paths"][path], baseline["paths"][path])

    def test_empty_voice_allowlist_rejects_every_upstream_voice(self):
        spec = json.loads((Path(__file__).parent.parent / "docs/openapi.json").read_text())
        with patch("mcp_magichour.openapi_policies.APPROVED_VOICE_NAMES", ()):
            patched = apply_magic_hour_policies(spec)
        safe_schema = patched["paths"]["/v1/ai-voice-generator"]["post"]["requestBody"]["content"]["application/json"]["schema"]
        jsonschema.Draft202012Validator.check_schema(safe_schema)
        validator = jsonschema.Draft202012Validator(safe_schema)
        upstream_schema = spec["paths"]["/v1/ai-voice-generator"]["post"]["requestBody"]["content"]["application/json"]["schema"]
        for name in upstream_schema["properties"]["style"]["properties"]["voice_name"]["enum"]:
            self.assertFalse(validator.is_valid({"style": {"prompt": "Hello", "voice_name": name}}))
        self.assertEqual(safe_schema["properties"]["style"]["properties"]["voice_name"]["not"], {})

    def test_sync_cannot_make_voice_selection_optional(self):
        spec = json.loads((Path(__file__).parent.parent / "docs/openapi.json").read_text())
        schema = spec["paths"]["/v1/ai-voice-generator"]["post"]["requestBody"]["content"]["application/json"]["schema"]
        schema.pop("required")
        schema["properties"]["style"].pop("required")
        safe = apply_magic_hour_policies(spec)["paths"]["/v1/ai-voice-generator"]["post"]["requestBody"]["content"]["application/json"]["schema"]
        for arguments in ({}, {"style": {"prompt": "Hello"}}):
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(arguments, safe)

    def test_voice_policy_fails_closed_on_changed_upstream_input_shape(self):
        spec = json.loads((Path(__file__).parent.parent / "docs/openapi.json").read_text())
        operation = spec["paths"]["/v1/ai-voice-generator"]["post"]
        operation["requestBody"]["content"]["application/json"]["schema"] = {"$ref": "#/components/schemas/NewVoiceInput"}
        with self.assertRaises(KeyError):
            apply_magic_hour_policies(spec)

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

        self.assertEqual(operation_id, "ai_image_generator_create_image")
        self.assertIn("MCP guidance", description)
        self.assertIn("wait_for_image_project", description)
        self.assertIn("complete", description)

    def test_file_path_inputs_get_upload_guidance(self):
        spec = {
            "paths": {
                "/v1/image-to-video": {
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
        description = patched["paths"]["/v1/image-to-video"]["post"]["description"]

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
