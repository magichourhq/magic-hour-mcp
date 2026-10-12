import unittest

from mcp_magichour.openapi_policies import (
    apply_magic_hour_policies,
    customize_openapi_component,
    normalize_mcp_tool_name,
)


class OpenApiPolicyTests(unittest.TestCase):
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
