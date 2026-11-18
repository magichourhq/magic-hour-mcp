"""Schemas describe real existing responses, without live account or media calls."""
import unittest
from unittest.mock import patch

import jsonschema
from fastmcp import Client

from mcp_magichour.helper_schemas import PROJECT_WAIT_OUTPUT_SCHEMA, WORKFLOW_GUIDE_OUTPUT_SCHEMA
from mcp_magichour.openapi_server import _project_to_tool_result, mcp
from mcp_magichour.workflow_guidance import WORKFLOWS


class HelperContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_helper_inputs_and_idempotency_are_explicit(self):
        tools = {tool.name: tool for tool in await mcp.list_tools()}
        for name, tool in tools.items():
            with self.subTest(name=name):
                self.assertIsInstance(tool.annotations.idempotentHint, bool)
        names = ["get_workflow_guide", "upload_chatgpt_image", "upload_chatgpt_media"]
        names += [f"wait_for_{kind}_project" for kind in ("image", "audio", "video")]
        names += [f"fetch_{kind}_download" for kind in ("image", "audio", "video")]
        for name in names:
            for field, schema in tools[name].parameters["properties"].items():
                with self.subTest(tool=name, field=field):
                    self.assertTrue(schema.get("description"))
        for name in ("upload_chatgpt_image", "upload_chatgpt_media", "generate_image"):
            self.assertFalse(tools[name].annotations.idempotentHint)
        for name in ("ping", "get_workflow_guide", "get_account_info", "wait_for_video_project"):
            self.assertTrue(tools[name].annotations.idempotentHint)
        # Native media results are not JSON objects; do not invent an output schema.
        self.assertIsNone(tools["fetch_video_download"].output_schema)

    async def test_every_workflow_response_conforms_through_mcp(self):
        with patch("mcp_magichour.oauth_compat.current_authorization_header", return_value="Bearer synthetic-test-token"):
            async with Client(mcp) as client:
                for workflow in WORKFLOWS:
                    with self.subTest(workflow=workflow):
                        result = await client.call_tool("get_workflow_guide", {"workflow": workflow})
                        jsonschema.validate(result.structured_content, WORKFLOW_GUIDE_OUTPUT_SCHEMA)
                        self.assertEqual(result.structured_content["workflow"], workflow)

    async def test_wait_schema_covers_terminal_and_timeout_responses(self):
        url = "https://videos.magichour.ai/output.png?signature=synthetic&expires=123"
        projects = [
            {"id": "job", "status": "complete", "downloads": [{"url": url, "expires_at": "2030-01-01T00:00:00Z"}]},
            {"id": "job", "status": "error", "error": {"code": "test"}},
            {"id": "job", "status": "canceled"},
            {"id": "job", "status": "timeout", "message": "Resume this ID", "last_project": {"status": "queued"}},
        ]
        for kind in ("image", "audio", "video"):
            for project in projects:
                with self.subTest(kind=kind, status=project["status"]):
                    result = await _project_to_tool_result(kind, project, include_inline_downloads=False, max_inline_downloads=0, max_bytes_per_download=1)
                    jsonschema.validate(result.structured_content, PROJECT_WAIT_OUTPUT_SCHEMA)
                    if project["status"] == "complete":
                        self.assertEqual(result.structured_content["exact_download_urls"], [url])
