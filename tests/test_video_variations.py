"""Actual MCP-client contract tests; upstream HTTP is simulated, never live proof."""

import base64
import hashlib
import hmac
import json
import os
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import httpx
from fastmcp import Client
from mcp.shared.exceptions import McpError

from mcp_magichour.openapi_auth import BearerPassthroughAuth, _authorization_header
from mcp_magichour.openapi_server import create_mcp


PLAN_ARGUMENTS = {
    "image_file_path": "api-assets/product-photo.png",
    "creative_instructions": "Create three distinct vertical product advertisements.",
    "variations": 3,
    "aspect_ratio": "9:16",
    "duration_seconds": 5,
    "max_credits": 300,
}


class VideoVariationsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        signing_environment = patch.dict(os.environ, {"MCP_WORKFLOW_SIGNING_SECRET": "test-server-only-signing-secret-32bytes"})
        signing_environment.start()
        self.addCleanup(signing_environment.stop)
        self.requests = []
        self.jobs = {}
        self.charges = {}
        self.capability = True
        self.account_credits = 10000
        self.quote_maximum = 60
        self.account_status = 200
        self.quote_status = 200
        self.quote_model = None
        self.submit_status = 200
        self.timeout_after_submit = False
        self.project_statuses = {}
        self.missing_download_ids = set()

    def upstream(self, request):
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, request.url.path, body, request.headers.get("authorization")))
        if request.url.path == "/v1/account":
            return httpx.Response(self.account_status, json={
                "id": "test-account",
                "credits": self.account_credits,
                "capabilities": {"image_to_video_budget_v1": self.capability},
            })
        if request.url.path == "/v1/image-to-video":
            self.assertEqual(set(body), {"budgeted_request"})
            body = body["budgeted_request"]
            key = body["idempotency_key"]
            project_id = body.get("project_id") or "video-" + key.rsplit(":", 1)[-1]
            if body.get("dry_run"):
                return httpx.Response(self.quote_status, json={
                    "dry_run": True,
                    "project_id": project_id,
                    "model": self.quote_model or body.get("model") or "ltx-2.5",
                    "resolution": body.get("resolution") or "720p",
                    "end_seconds": 5,
                    "credits_estimated": self.quote_maximum,
                    "credits_maximum": self.quote_maximum,
                    "input_width": 900,
                    "input_height": 1600,
                })
            if self.submit_status != 200:
                return httpx.Response(self.submit_status, json={"message": "Upstream rejected submission"}, headers={"Retry-After": "7"})
            self.jobs[key] = project_id
            self.charges.setdefault(key, 60)
            if self.timeout_after_submit:
                self.timeout_after_submit = False
                raise httpx.ReadTimeout("Response lost after accepted submission", request=request)
            return httpx.Response(200, json={"id": project_id, "credits_charged": self.charges[key]})
        if request.url.path.startswith("/v1/video-projects/"):
            project_id = request.url.path.rsplit("/", 1)[-1]
            if project_id not in self.jobs.values():
                return httpx.Response(404, json={"message": "Project not found"})
            status = self.project_statuses.get(project_id, "complete")
            return httpx.Response(200, json={
                "id": project_id,
                "status": status,
                "credits_charged": 60,
                "width": 720,
                "height": 1280,
                "downloads": [{
                    "url": f"https://videos.magichour.ai/{project_id}/output.mp4?signature=A%2FB%3D&expires=123",
                    "expires_at": "2026-10-11T20:00:00Z",
                }] if status == "complete" and project_id not in self.missing_download_ids else [],
                "error": {"message": "Generation failed"} if status == "error" else None,
            })
        raise AssertionError(f"Unexpected upstream request: {request.method} {request.url.path}")

    @asynccontextmanager
    async def caller(self, authorization="Bearer test-api-key"):
        clients = []

        def build_client():
            client = httpx.AsyncClient(base_url="https://api.example.test", auth=BearerPassthroughAuth(), transport=httpx.MockTransport(self.upstream))
            clients.append(client)
            return client

        auth_token = _authorization_header.set(authorization)
        try:
            with patch("mcp_magichour.openapi_server.build_api_client", side_effect=build_client), patch.dict(os.environ, {"MCP_OAUTH_ISSUER_URL": "https://mcp.example.test"}):
                async with Client(create_mcp()) as caller:
                    yield caller
        finally:
            _authorization_header.reset(auth_token)
            for client in clients:
                await client.aclose()

    async def plan(self, caller, **overrides):
        result = await caller.call_tool("plan_video_variations", {**PLAN_ARGUMENTS, **overrides})
        self.assertFalse(result.is_error, result)
        return result.structured_content

    async def execute(self, caller, plan, **overrides):
        return await caller.call_tool("execute_video_variations", {
            "plan_token": plan["plan_token"],
            "approved_max_credits": plan["maximum_credits"],
            **overrides,
        }, raise_on_error=False)

    def paid_requests(self):
        return [body["budgeted_request"] for method, path, body, _ in self.requests if method == "POST" and path == "/v1/image-to-video" and not body["budgeted_request"].get("dry_run")]

    async def test_discovery_exposes_workflow_schema_and_paid_annotations(self):
        async with self.caller() as caller:
            tools = {tool.name: tool for tool in await caller.list_tools()}
        plan = tools["plan_video_variations"]
        execute = tools["execute_video_variations"]
        self.assertIn("video_variations_status", tools)
        self.assertTrue(plan.annotations.readOnlyHint)
        self.assertFalse(execute.annotations.readOnlyHint)
        self.assertIn("max_credits", plan.inputSchema["required"])
        self.assertIn("approved_max_credits", execute.inputSchema["required"])
        self.assertIn("9:16", plan.inputSchema["properties"]["aspect_ratio"]["enum"])
        for field in ("image_file_path", "creative_instructions", "variations", "duration_seconds", "model", "resolution", "variation_prompts"):
            self.assertIn(field, plan.inputSchema["properties"])
        self.assertIn("budget", plan.description.lower())
        self.assertIn("approv", execute.description.lower())
        self.assertEqual(self.requests, [])

    async def test_actual_client_plans_executes_and_returns_exact_video_urls(self):
        async with self.caller() as caller:
            plan = await self.plan(caller)
            self.assertEqual(self.paid_requests(), [])
            self.assertLessEqual(plan["maximum_credits"], PLAN_ARGUMENTS["max_credits"])
            submitted = await self.execute(caller, plan)
            self.assertFalse(submitted.is_error, submitted)
            result = await caller.call_tool("video_variations_status", {"plan_token": plan["plan_token"]})
        data = result.structured_content
        self.assertEqual(data["status"], "complete")
        self.assertTrue(data["billing_final"])
        self.assertEqual(data["credits_charged"], 180)
        self.assertEqual(len(data["projects"]), 3)
        for project in data["projects"]:
            self.assertEqual(project["exact_download_urls"], [f"https://videos.magichour.ai/{project['id']}/output.mp4?signature=A%2FB%3D&expires=123"])
            self.assertEqual(project["download_expiration_metadata"][0]["expires_at"], "2026-10-11T20:00:00Z")
            self.assertEqual((project["width"], project["height"]), (720, 1280))
            self.assertEqual(project["model"], "ltx-2.5")
            self.assertEqual(project["resolution"], "720p")
            self.assertEqual(project["duration_seconds"], 5)
        self.assertEqual(len(self.charges), 3)
        self.assertTrue(all(header == "Bearer test-api-key" for _, _, _, header in self.requests))
        self.assertTrue(all(body["model"] == "ltx-2.5" and body["resolution"] == "720p" for body in self.paid_requests()))
        self.assertLessEqual(sum(body["max_credits"] for body in self.paid_requests()), 300)

    async def test_old_api_without_capability_never_receives_generation_post(self):
        self.capability = False
        async with self.caller() as caller:
            result = await caller.call_tool("plan_video_variations", PLAN_ARGUMENTS, raise_on_error=False)
        self.assertTrue(result.is_error)
        self.assertFalse(any(method == "POST" for method, _, _, _ in self.requests))
        self.assertEqual(result.structured_content["code"], "unsupported_api")

    async def test_all_generation_posts_fail_closed_on_old_api_payload_schema(self):
        async with self.caller() as caller:
            plan = await self.plan(caller)
            submitted = await self.execute(caller, plan)
            self.assertFalse(submitted.is_error)
        posts = [body for method, path, body, _ in self.requests if method == "POST" and path == "/v1/image-to-video"]
        self.assertEqual(len(posts), 6)
        for body in posts:
            self.assertEqual(set(body), {"budgeted_request"})
            self.assertNotIn("assets", body)
            self.assertNotIn("end_seconds", body)

    async def test_invalid_authorization_stops_before_upstream(self):
        for authorization in (None, "Basic private-token", "Bearer "):
            with self.subTest(authorization=authorization):
                async with self.caller(authorization) as caller:
                    result = await caller.call_tool("plan_video_variations", PLAN_ARGUMENTS, raise_on_error=False)
                self.assertTrue(result.is_error)
                self.assertEqual(self.requests, [])

    async def test_api_auth_failure_never_quotes_or_submits(self):
        self.account_status = 401
        async with self.caller() as caller:
            result = await caller.call_tool("plan_video_variations", PLAN_ARGUMENTS, raise_on_error=False)
        self.assertTrue(result.is_error)
        self.assertEqual([method for method, _, _, _ in self.requests], ["GET"])

    async def test_invalid_input_does_not_make_paid_calls(self):
        for overrides in ({"variations": 0}, {"max_credits": 0}, {"duration_seconds": 0}, {"variation_prompts": ["only one"]}):
            with self.subTest(overrides=overrides):
                async with self.caller() as caller:
                    try:
                        result = await caller.call_tool("plan_video_variations", {**PLAN_ARGUMENTS, **overrides}, raise_on_error=False)
                    except McpError:
                        pass
                    else:
                        self.assertTrue(result.is_error)
                self.assertEqual(self.paid_requests(), [])

    async def test_quote_exceeding_per_variation_budget_never_submits(self):
        self.quote_maximum = 101
        async with self.caller() as caller:
            result = await caller.call_tool("plan_video_variations", PLAN_ARGUMENTS, raise_on_error=False)
        self.assertTrue(result.is_error)
        self.assertEqual(self.paid_requests(), [])

    async def test_account_balance_must_cover_all_quoted_variations(self):
        self.account_credits = 179
        async with self.caller() as caller:
            result = await caller.call_tool("plan_video_variations", PLAN_ARGUMENTS, raise_on_error=False)
        self.assertTrue(result.is_error)
        self.assertEqual(result.structured_content["code"], "insufficient_credits")
        self.assertEqual(self.paid_requests(), [])

    async def test_explicit_incompatible_model_is_not_silently_changed(self):
        self.quote_status = 422
        async with self.caller() as caller:
            result = await caller.call_tool("plan_video_variations", {**PLAN_ARGUMENTS, "model": "explicit-incompatible-model"}, raise_on_error=False)
        self.assertTrue(result.is_error)
        self.assertEqual(self.paid_requests(), [])
        quotes = [body["budgeted_request"] for _, path, body, _ in self.requests if path == "/v1/image-to-video"]
        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0]["model"], "explicit-incompatible-model")

    async def test_api_cannot_replace_explicit_model_in_quote(self):
        self.quote_model = "default"
        async with self.caller() as caller:
            result = await caller.call_tool("plan_video_variations", {**PLAN_ARGUMENTS, "model": "ltx-2.5"}, raise_on_error=False)
        self.assertTrue(result.is_error)
        self.assertEqual(result.structured_content["code"], "model_replaced")
        self.assertEqual(self.paid_requests(), [])

    async def test_analytics_failure_cannot_abort_generation_or_hide_outputs(self):
        with patch("mcp_magichour.video_variations.analytics.capture_mcp", new=AsyncMock(side_effect=RuntimeError("Analytics unavailable"))):
            async with self.caller() as caller:
                plan = await self.plan(caller)
                submitted = await self.execute(caller, plan)
                self.assertFalse(submitted.is_error)
                result = await caller.call_tool("video_variations_status", {"plan_token": plan["plan_token"]})
        self.assertEqual(result.structured_content["status"], "complete")
        self.assertEqual(len(self.charges), 3)

    async def test_execution_requires_exact_approved_maximum(self):
        async with self.caller() as caller:
            plan = await self.plan(caller)
            for approval in (plan["maximum_credits"] - 1, plan["maximum_credits"] + 1):
                with self.subTest(approval=approval):
                    result = await self.execute(caller, plan, approved_max_credits=approval)
                    self.assertTrue(result.is_error)
            with self.assertRaises(McpError):
                await self.execute(caller, plan, approved_max_credits=0)
            with self.assertRaises(McpError):
                await caller.call_tool("execute_video_variations", {"plan_token": plan["plan_token"]})
        self.assertEqual(self.paid_requests(), [])

    async def test_tampered_token_and_different_tenant_cannot_execute(self):
        async with self.caller() as caller:
            plan = await self.plan(caller)
            token = plan["plan_token"]
            tampered = ("A" if token[0] != "A" else "B") + token[1:]
            result = await self.execute(caller, plan, plan_token=tampered)
            self.assertTrue(result.is_error)
        async with self.caller("Bearer other-tenant-key") as caller:
            result = await self.execute(caller, plan)
            self.assertTrue(result.is_error)
            status = await caller.call_tool("video_variations_status", {"plan_token": plan["plan_token"]}, raise_on_error=False)
            self.assertTrue(status.is_error)
        self.assertEqual(self.paid_requests(), [])

    async def test_caller_api_key_cannot_sign_forged_budget_authorization(self):
        async with self.caller() as caller:
            plan = await self.plan(caller)
            encoded = plan["plan_token"].split(".")[0]
            payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            payload["maximum_credits"] = 1
            for request in payload["requests"]:
                request["max_credits"] = 1000
            forged_payload = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
            original_request_count = len(self.requests)
            for caller_known_key in (b"test-api-key", b"Bearer test-api-key"):
                signature = hmac.new(caller_known_key, forged_payload.encode(), hashlib.sha256).hexdigest()
                result = await self.execute(caller, plan, plan_token=f"{forged_payload}.{signature}", approved_max_credits=1)
                self.assertTrue(result.is_error)
                self.assertEqual(result.structured_content["code"], "invalid_plan")
            self.assertEqual(len(self.requests), original_request_count)
        self.assertEqual(self.paid_requests(), [])

    async def test_missing_signing_secret_fails_before_any_upstream_request(self):
        async with self.caller() as caller:
            plan = await self.plan(caller)
            self.requests.clear()
            for missing_secret in ("", "too-short"):
                with self.subTest(secret=missing_secret), patch.dict(os.environ, {"MCP_WORKFLOW_SIGNING_SECRET": missing_secret}):
                    planned = await caller.call_tool("plan_video_variations", PLAN_ARGUMENTS, raise_on_error=False)
                    executed = await self.execute(caller, plan)
                    for result in (planned, executed):
                        self.assertTrue(result.is_error)
                        self.assertEqual(result.structured_content["code"], "workflow_not_configured")
                    self.assertEqual(self.requests, [])

    async def test_reconnect_and_replay_use_same_ids_without_double_charge(self):
        async with self.caller() as caller:
            plan = await self.plan(caller)
            first = await self.execute(caller, plan)
            self.assertFalse(first.is_error)
        first_ids = list(self.jobs.values())
        async with self.caller() as caller:
            replay = await self.execute(caller, plan)
            self.assertFalse(replay.is_error)
            result = await caller.call_tool("video_variations_status", {"plan_token": plan["plan_token"]})
        self.assertEqual(list(self.jobs.values()), first_ids)
        self.assertEqual(len(self.charges), 3)
        self.assertEqual(sum(self.charges.values()), 180)
        self.assertEqual(result.structured_content["status"], "complete")

    async def test_lost_submission_response_stops_and_recovers_same_plan(self):
        self.timeout_after_submit = True
        async with self.caller() as caller:
            plan = await self.plan(caller)
            uncertain = await self.execute(caller, plan)
            self.assertNotEqual(uncertain.structured_content["status"], "submitted")
            self.assertTrue(uncertain.structured_content["recovery"])
            self.assertEqual(len(self.paid_requests()), 1)
            status = await caller.call_tool("video_variations_status", {"plan_token": plan["plan_token"]})
            self.assertNotEqual(status.structured_content["status"], "complete")
            replay = await self.execute(caller, plan)
            self.assertFalse(replay.is_error)
        self.assertEqual(len(self.charges), 3)
        self.assertEqual(sum(self.charges.values()), 180)

    async def test_rate_limit_stops_batch_and_reports_retry_delay(self):
        self.submit_status = 429
        async with self.caller() as caller:
            plan = await self.plan(caller)
            result = await self.execute(caller, plan)
        self.assertNotEqual(result.structured_content["status"], "submitted")
        self.assertEqual(len(self.paid_requests()), 1)
        self.assertTrue(result.structured_content["recovery"])
        self.assertEqual(self.charges, {})
        self.assertIn("7", json.dumps(result.structured_content))

    async def test_failed_variation_preserves_successful_outputs(self):
        async with self.caller() as caller:
            plan = await self.plan(caller)
            await self.execute(caller, plan)
            failed_id = list(self.jobs.values())[1]
            self.project_statuses[failed_id] = "error"
            result = await caller.call_tool("video_variations_status", {"plan_token": plan["plan_token"]}, raise_on_error=False)
        data = result.structured_content
        self.assertNotEqual(data["status"], "complete")
        self.assertEqual(sum(bool(project["exact_download_urls"]) for project in data["projects"]), 2)
        failed = next(project for project in data["projects"] if project["id"] == failed_id)
        self.assertEqual(failed["status"], "error")
        self.assertIn("Generation failed", json.dumps(failed))
        self.assertTrue(data["recovery"])
        self.assertEqual(len(self.paid_requests()), 3)

    async def test_unsubmitted_plan_status_never_starts_generation(self):
        async with self.caller() as caller:
            plan = await self.plan(caller)
            result = await caller.call_tool("video_variations_status", {"plan_token": plan["plan_token"]})
        data = result.structured_content
        self.assertEqual(data["status"], "pending")
        self.assertFalse(data["billing_final"])
        self.assertEqual([project["status"] for project in data["projects"]], ["not_submitted"] * 3)
        self.assertEqual(self.paid_requests(), [])

    async def test_completed_job_without_download_is_not_workflow_success(self):
        async with self.caller() as caller:
            plan = await self.plan(caller)
            await self.execute(caller, plan)
            self.missing_download_ids.add(list(self.jobs.values())[0])
            result = await caller.call_tool("video_variations_status", {"plan_token": plan["plan_token"]}, raise_on_error=False)
        self.assertTrue(result.is_error)
        self.assertEqual(result.structured_content["status"], "partial")
        self.assertEqual(sum(bool(project["exact_download_urls"]) for project in result.structured_content["projects"]), 2)

    async def test_expired_token_cannot_start_paid_execution(self):
        async with self.caller() as caller:
            with patch("mcp_magichour.video_variations.time.time", return_value=1000):
                plan = await self.plan(caller)
            with patch("mcp_magichour.video_variations.time.time", return_value=1000 + 86401):
                result = await self.execute(caller, plan)
        self.assertTrue(result.is_error)
        self.assertEqual(self.paid_requests(), [])


if __name__ == "__main__":
    unittest.main()
