"""Agent-facing MCP contracts against an in-memory API, never live generation."""

import base64
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastmcp import Client
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import McpError

from mcp_magichour.openapi_server import create_mcp


FIXTURES = Path(__file__).parent / "fixtures"
VIDEO = (FIXTURES / "agent-smoke.mp4").read_bytes()
IMAGE = (FIXTURES / "agent-smoke.png").read_bytes()
AUDIO = (FIXTURES / "agent-smoke.wav").read_bytes()
DOWNLOAD = "https://videos.magichour.ai/agent-smoke/output.mp4?signature=synthetic&expires=123"
ASYNC_CLIENT = httpx.AsyncClient


def data(result):
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(next(c.text for c in result.content if c.type == "text"))


class AgentWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.uploads = {}
        self.jobs = {}
        self.statuses = ["queued", "complete"]
        self.poll_error_once = False

        def upstream(request):
            self.requests.append((request.method, str(request.url)))
            if request.url.host == "storage.example.test":
                self.assertEqual(request.method, "PUT")
                self.assertNotIn("authorization", request.headers)
                self.uploads[request.url.path] = request.content
                return httpx.Response(200)
            if request.url.host == "videos.magichour.ai":
                self.assertEqual(str(request.url), DOWNLOAD)
                self.assertNotIn("authorization", request.headers)
                return httpx.Response(200, content=VIDEO, headers={"Content-Type": "video/mp4"})
            self.assertEqual(request.url.host, "api.example.test", "Unexpected network target")
            path = request.url.path
            if path == "/v1/account":
                return httpx.Response(200, json={"id": "test-account", "credits": 1000, "tier": "pro", "subscription": None, "email": None})
            if path == "/v1/files/upload-urls":
                items = json.loads(request.content)["items"]
                return httpx.Response(200, json={"items": [{
                    "upload_url": f"https://storage.example.test/{i}.{item['extension']}",
                    "file_path": f"api-assets/test/{i}.{item['extension']}",
                    "expires_at": "2030-01-01T00:00:00Z",
                } for i, item in enumerate(items)]})
            if request.method == "POST":
                payload = json.loads(request.content)
                for value in payload.get("assets", {}).values():
                    if isinstance(value, str) and value.startswith("api-assets/test/"):
                        self.assertIn("/" + value.rsplit("/", 1)[1], self.uploads)
                project_id = f"job-{len(self.jobs) + 1}"
                self.jobs[project_id] = {"path": path, "payload": payload, "polls": 0}
                result = {"id": project_id, "credits_charged": 1}
                if path == "/v1/ai-ugc-ad":
                    result["estimated_frame_cost"] = 1
                return httpx.Response(200, json=result)
            if path.startswith("/v1/video-projects/"):
                if self.poll_error_once:
                    self.poll_error_once = False
                    return httpx.Response(503, json={"message": "Synthetic temporary outage"})
                project_id = path.rsplit("/", 1)[1]
                job = self.jobs[project_id]
                status = self.statuses[min(job["polls"], len(self.statuses) - 1)]
                job["polls"] += 1
                result = {"id": project_id, "status": status, "credits_charged": 1}
                if status == "complete":
                    result["downloads"] = [{"url": DOWNLOAD, "expires_at": "2030-01-01T00:00:00Z"}]
                elif status == "error":
                    result["error"] = {"code": "TEST_ERROR", "message": "Synthetic provider failure"}
                return httpx.Response(200, json=result)
            raise AssertionError(f"Unhandled mock request: {request.method} {path}")

        self.transport = httpx.MockTransport(upstream)

        def client_factory(*args, **kwargs):
            kwargs["transport"] = self.transport
            return ASYNC_CLIENT(*args, **kwargs)

        self.patches = [
            patch("httpx.AsyncClient", side_effect=client_factory),
            patch("mcp_magichour.openapi_server.build_api_client", side_effect=lambda: ASYNC_CLIENT(base_url="https://api.example.test", transport=self.transport)),
            patch("mcp_magichour.openapi_server.current_authorization_header", return_value="Bearer synthetic-test-token"),
            patch("mcp_magichour.oauth_compat.current_authorization_header", return_value="Bearer synthetic-test-token"),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.mcp = create_mcp()
        self.caller = await self.enterAsyncContext(Client(self.mcp))

    async def upload(self, *kinds):
        extensions = {"image": "png", "audio": "wav", "video": "mp4"}
        content = {"image": IMAGE, "audio": AUDIO, "video": VIDEO}
        result = data(await self.caller.call_tool("get_upload_urls", {
            "items": [{"type": kind, "extension": extensions[kind]} for kind in kinds],
        }))
        async with ASYNC_CLIENT(transport=self.transport) as storage:
            for kind, item in zip(kinds, result["items"]):
                await storage.put(item["upload_url"], content=content[kind])
                self.assertEqual(self.uploads["/" + item["file_path"].rsplit("/", 1)[1]], content[kind])
        return [item["file_path"] for item in result["items"]]

    async def finish(self, project_id):
        result = data(await self.caller.call_tool("wait_for_video_project", {
            "id": project_id, "poll_interval_seconds": 0, "timeout_seconds": 3,
        }))
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["id"], project_id)
        self.assertEqual(result["exact_download_urls"], [DOWNLOAD])
        self.assertEqual(result["downloads"], [{"url": DOWNLOAD}])
        fetched = await self.caller.call_tool("fetch_video_download", {"download_url": DOWNLOAD})
        resource = next(c.resource for c in fetched.content if c.type == "resource")
        self.assertEqual(resource.mimeType, "video/mp4")
        self.assertEqual(str(resource.uri), DOWNLOAD)
        self.assertEqual(base64.b64decode(resource.blob), VIDEO)

    async def test_product_image_reaches_video_without_an_image_edit(self):
        self.assertEqual(data(await self.caller.call_tool("get_account_info", {}))["tier"], "pro")
        image, = await self.upload("image")
        result = data(await self.caller.call_tool("create_video_from_image", {
            "assets": {"image_file_path": image}, "end_seconds": 5,
        }))
        await self.finish(result["id"])
        self.assertEqual([j["path"] for j in self.jobs.values()], ["/v1/image-to-video"])
        self.assertEqual(self.jobs[result["id"]]["payload"]["assets"], {"image_file_path": image})

    async def test_talking_photo_uses_image_and_audio(self):
        image, audio = await self.upload("image", "audio")
        result = data(await self.caller.call_tool("create_talking_photo", {
            "assets": {"image_file_path": image, "audio_file_path": audio},
            "start_seconds": 0, "end_seconds": 5,
        }))
        await self.finish(result["id"])
        self.assertEqual(self.jobs[result["id"]]["payload"]["assets"], {"image_file_path": image, "audio_file_path": audio})

    async def test_lip_sync_uses_video_and_audio(self):
        video, audio = await self.upload("video", "audio")
        result = data(await self.caller.call_tool("create_lip_sync_video", {
            "assets": {"video_source": "file", "video_file_path": video, "audio_file_path": audio},
            "start_seconds": 0, "end_seconds": 5,
        }))
        await self.finish(result["id"])
        self.assertEqual(self.jobs[result["id"]]["payload"]["assets"], {"video_source": "file", "video_file_path": video, "audio_file_path": audio})

    async def test_three_ad_variants_keep_separate_project_ids(self):
        image, = await self.upload("image")
        ids = []
        for i in range(3):
            result = data(await self.caller.call_tool("create_spoken_ad_video", {
                "assets": {"image_file_path": image}, "script": f"Approved variant {i + 1}.", "end_seconds": 5,
            }))
            ids.append(result["id"])
            await self.finish(result["id"])
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual(len(self.jobs), 3)
        self.assertEqual([j["payload"]["script"] for j in self.jobs.values()], [f"Approved variant {i + 1}." for i in range(3)])

    async def test_application_resumes_a_timeout_without_recreating_the_job(self):
        image, = await self.upload("image")
        result = data(await self.caller.call_tool("create_video_from_image", {
            "assets": {"image_file_path": image}, "end_seconds": 5,
        }))
        timed_out = data(await self.caller.call_tool("wait_for_video_project", {
            "id": result["id"], "poll_interval_seconds": 0, "timeout_seconds": 0.02,
        }))
        self.assertEqual(timed_out["status"], "timeout")
        self.assertEqual(timed_out["id"], result["id"])
        await self.finish(result["id"])
        self.assertEqual(len(self.jobs), 1)

    async def test_missing_audio_and_wrong_source_type_never_reach_creation(self):
        cases = [
            ("create_talking_photo", {"assets": {"image_file_path": "photo.png"}, "start_seconds": 0, "end_seconds": 5}),
            ("create_lip_sync_video", {"assets": {"image_file_path": "photo.png", "audio_file_path": "speech.wav"}, "start_seconds": 0, "end_seconds": 5}),
        ]
        for name, arguments in cases:
            with self.subTest(tool=name), self.assertRaises(McpError):
                await self.caller.call_tool(name, arguments)
        self.assertEqual(self.jobs, {})

    async def test_polling_error_can_resume_the_original_job(self):
        image, = await self.upload("image")
        result = data(await self.caller.call_tool("create_video_from_image", {
            "assets": {"image_file_path": image}, "end_seconds": 5,
        }))
        self.poll_error_once = True
        with self.assertRaises(ToolError):
            await self.caller.call_tool("wait_for_video_project", {"id": result["id"]})
        await self.finish(result["id"])
        self.assertEqual(len(self.jobs), 1)

    async def test_error_and_canceled_jobs_stop_without_a_download(self):
        for status in ["error", "canceled"]:
            with self.subTest(status=status):
                self.statuses = [status]
                self.jobs["terminal-job"] = {"polls": 0}
                result = data(await self.caller.call_tool("wait_for_video_project", {"id": "terminal-job"}))
                self.assertEqual(result["status"], status)
                self.assertEqual(self.jobs["terminal-job"]["polls"], 1)
                self.assertFalse(result.get("exact_download_urls"))
                self.assertFalse(any(method == "GET" and "videos.magichour.ai" in url for method, url in self.requests))

    async def test_unrelated_download_hosts_are_rejected_without_fetching(self):
        with self.assertRaises(ToolError):
            await self.caller.call_tool("fetch_video_download", {"download_url": "https://example.test/other.mp4"})
        self.assertEqual(self.requests, [])
