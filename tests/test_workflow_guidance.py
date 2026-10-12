import json
import unittest
from unittest.mock import patch

import httpx
from fastmcp import Client

from mcp_magichour.openapi_server import app, create_mcp
from mcp_magichour.workflow_guidance import WORKFLOWS, workflow_guide


class WorkflowGuidanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []

        def upstream(request):
            self.requests.append(request)
            return httpx.Response(500)

        self.api = httpx.AsyncClient(
            base_url="https://api.example.test", transport=httpx.MockTransport(upstream)
        )
        with patch(
            "mcp_magichour.openapi_server.build_api_client", return_value=self.api
        ):
            self.server = create_mcp()

    async def asyncTearDown(self):
        await self.api.aclose()

    async def test_discovery_resource_and_tool_agree_without_api_requests(self):
        async with Client(self.server) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            tool = tools["workflow_guide_retrieve"]
            self.assertTrue(tool.annotations.readOnlyHint)
            self.assertFalse(tool.annotations.destructiveHint)
            self.assertEqual(
                set(tool.inputSchema["properties"]["workflow"]["enum"]), set(WORKFLOWS)
            )
            self.assertIn(
                "magic-hour://workflows/index",
                {str(r.uri) for r in await client.list_resources()},
            )
            self.assertIn(
                "magic-hour://workflows/{workflow}",
                {r.uriTemplate for r in await client.list_resource_templates()},
            )
            index = json.loads(
                (await client.read_resource("magic-hour://workflows/index"))[0].text
            )
            self.assertEqual(
                {item["workflow"] for item in index["workflows"]}, set(WORKFLOWS)
            )
            for workflow in WORKFLOWS:
                resource = json.loads(
                    (await client.read_resource(f"magic-hour://workflows/{workflow}"))[
                        0
                    ].text
                )
                with patch(
                    "mcp_magichour.oauth_compat.current_authorization_header",
                    return_value="Bearer test-token",
                ):
                    result = await client.call_tool(
                        "workflow_guide_retrieve", {"workflow": workflow}
                    )
                self.assertEqual(result.data, resource)
                creation_tool = resource["creation_tool"]
                if creation_tool:
                    self.assertIn(creation_tool, tools)
            self.assertEqual(self.requests, [])

    async def test_tool_call_keeps_existing_auth_challenge(self):
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="https://mcp.example",
                headers={"Accept": "application/json, text/event-stream"},
            ) as client,
        ):
            response = await client.post(
                "/",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "workflow_guide_retrieve",
                        "arguments": {"workflow": "lip-sync"},
                    },
                },
            )
        result = json.loads(
            next(
                line.removeprefix("data: ")
                for line in response.text.splitlines()
                if line.startswith("data: ")
            )
        )["result"]
        self.assertTrue(result["isError"])
        self.assertIn("mcp/www_authenticate", result["_meta"])
        self.assertEqual(self.requests, [])

    async def test_invalid_resource_is_rejected_and_guides_are_isolated(self):
        from mcp.shared.exceptions import McpError

        async with Client(self.server) as client:
            with self.assertRaises(McpError):
                await client.read_resource("magic-hour://workflows/not-a-workflow")
        copy = workflow_guide("lip-sync")
        copy["guidance"].clear()
        self.assertTrue(workflow_guide("lip-sync")["guidance"])
        self.assertIsNone(workflow_guide("ugc-ads")["creation_tool"])
        self.assertIsNone(workflow_guide("ugc-ads")["api_docs_url"])
        self.assertEqual(self.requests, [])
