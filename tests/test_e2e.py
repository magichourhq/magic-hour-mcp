"""HTTP smoke checks. Default is offline; --live requires an explicit paid-call opt-in."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import httpx
import jsonschema

ROOT = Path(__file__).resolve().parents[1]
VIEW_URI = "ui://magic-hour/project-result-v1.html"
IMAGE_URL = "https://videos.magichour.ai/e2e/output.png?sig=keep%2Bthis&part=1"
IMAGE_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII="
)
IMAGE_ARGUMENTS = {
    "name": "MCP E2E smoke test",
    "model": "flux-2-klein",
    "resolution": "640px",
    "aspect_ratio": "1:1",
    "image_count": 1,
    "style": {"prompt": "A blue circle on a white background."},
}


@contextmanager
def local_server(*, live=False):
    """Run this checkout on a reserved loopback socket, without loading any .env."""
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("MAGIC_HOUR_", "MCP_", "VERCEL", "UPSTASH_", "KV_", "POSTHOG_"))
    }
    env.update(
        PYTHONPATH=str(ROOT), POSTHOG_PROJECT_TOKEN="", POSTHOG_HOST="",
        DEBUG="false", ENVIRONMENT="review", MAGIC_HOUR_API_BASE_URL="https://api.magichour.ai",
    )
    with socket.socket() as listener, tempfile.TemporaryFile() as logs:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        env.update(MCP_OAUTH_ISSUER_URL=url, MCP_OAUTH_RESOURCE_URL=url)
        command = [sys.executable, str(Path(__file__).resolve()), "--serve", str(listener.fileno())]
        if live:
            command.append("--live-upstream")
        process = subprocess.Popen(
            command, cwd=ROOT, env=env, pass_fds=(listener.fileno(),), stdout=logs, stderr=logs,
        )
        try:
            with httpx.Client(base_url=url, timeout=1, trust_env=False) as probe:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("Local MCP subprocess exited during startup.")
                    try:
                        if probe.get("/.well-known/glama.json").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.05)
                else:
                    raise RuntimeError("Local MCP subprocess did not become ready within 30 seconds.")
            yield url
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


class MCPClient:
    def __init__(self, client):
        self.http = client
        self.request_id = 0

    def rpc(self, method, params=None):
        self.request_id += 1
        response = self.http.post("/", json={
            "jsonrpc": "2.0", "id": self.request_id, "method": method, "params": params or {},
        })
        if response.status_code != 200:
            raise RuntimeError(f"{method} returned HTTP {response.status_code}.")
        if session := response.headers.get("mcp-session-id"):
            self.http.headers["mcp-session-id"] = session
        if response.headers.get("content-type", "").startswith("application/json"):
            payload = response.json()
        else:
            messages = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            payload = next(message for message in messages if message.get("id") == self.request_id)
        if "error" in payload:
            raise RuntimeError(f"{method} returned JSON-RPC error {payload['error'].get('code')}.")
        return payload["result"]

    def initialize(self):
        result = self.rpc("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "magic-hour-e2e", "version": "1"},
        })
        self.http.headers["MCP-Protocol-Version"] = result["protocolVersion"]
        response = self.http.post("/", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        if response.status_code != 202:
            raise RuntimeError("MCP initialization notification was rejected.")
        return result

    def call(self, name, arguments=None):
        result = self.rpc("tools/call", {"name": name, "arguments": arguments or {}})
        if result.get("isError"):
            # Do not echo upstream error bodies: these can contain private signed URLs.
            raise RuntimeError(f"Tool {name} failed.")
        return result


def structured(result):
    if "structuredContent" in result:
        return result["structuredContent"]
    return json.loads(next(item["text"] for item in result["content"] if item["type"] == "text"))


def oauth_login(client, api_key):
    verifier = "e2e-verifier-" * 6
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    redirect = "http://localhost:8787/callback"
    registration = client.post("/register", json={
        "redirect_uris": [redirect], "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code"], "response_types": ["code"],
    })
    if registration.status_code != 201:
        raise RuntimeError("OAuth client registration failed.")
    params = {
        "response_type": "code", "client_id": registration.json()["client_id"],
        "redirect_uri": redirect, "resource": str(client.base_url).rstrip("/"),
        "code_challenge": challenge, "code_challenge_method": "S256", "state": "e2e-state",
    }
    page = client.get("/authorize", params=params)
    if page.status_code != 200 or 'name="api_key"' not in page.text:
        raise RuntimeError("OAuth authorization page is unavailable.")
    authorized = client.post("/authorize", data={**params, "api_key": api_key})
    if authorized.status_code != 303:
        raise RuntimeError("OAuth API-key validation failed.")
    query = parse_qs(urlsplit(authorized.headers["location"]).query)
    if query.get("state") != ["e2e-state"]:
        raise RuntimeError("OAuth state was not preserved.")
    exchange = {
        "grant_type": "authorization_code", "client_id": params["client_id"], "redirect_uri": redirect,
        "code": query["code"][0], "code_verifier": verifier, "resource": params["resource"],
    }
    token = client.post("/token", data=exchange)
    if token.status_code != 200 or token.json().get("access_token") != api_key:
        raise RuntimeError("OAuth code exchange failed.")
    replay = client.post("/token", data=exchange)
    if replay.status_code != 400 or replay.json().get("error") != "invalid_grant":
        raise RuntimeError("OAuth accepted an already consumed authorization code.")
    client.headers["Authorization"] = f"Bearer {api_key}"


def image_workflow(mcp):
    """Create exactly once; preserve the original ID for cleanup on any later failure."""
    created = structured(mcp.call("ai_image_generator_create_image", IMAGE_ARGUMENTS))
    project_id = created.get("id")
    if not isinstance(project_id, str) or not project_id:
        raise RuntimeError("Creation returned no project ID; inspect the test account for cleanup. Do not retry creation.")
    try:
        print(f"Created test project {project_id}; credits charged: {created.get('credits_charged', 'unknown')}", flush=True)
        project = structured(mcp.call("wait_for_image_project", {
            "id": project_id, "poll_interval_seconds": 1, "timeout_seconds": 180,
            "include_inline_downloads": False,
        }))
        if project.get("status") != "complete":
            raise RuntimeError(f"Test project finished with status {project.get('status')}.")
        urls = project.get("exact_download_urls", [])
        if len(urls) != 1:
            raise RuntimeError("Expected exactly one output image.")
        media = mcp.call("fetch_image_download", {"download_url": urls[0]})
        image = next(item for item in media["content"] if item["type"] == "image")
        downloaded = base64.b64decode(image["data"], validate=True)
        if not image["mimeType"].startswith("image/") or not downloaded:
            raise RuntimeError("Downloaded output is not a nonempty image.")
        print(f"Downloaded {len(downloaded)} image bytes through local MCP.", flush=True)
        return project_id, project, downloaded
    finally:
        try:
            mcp.call("image_projects_delete", {"id": project_id})
        except BaseException:
            print(f"CLEANUP FAILED: delete test image project {project_id} manually.", file=sys.stderr, flush=True)
            raise
        print(f"Deleted test project {project_id}.", flush=True)


class MCPEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = local_server()
        cls.url = cls.server.__enter__()
        cls.addClassCleanup(cls.server.__exit__, None, None, None)

    def setUp(self):
        self.http = httpx.Client(base_url=self.url, timeout=210, trust_env=False, headers={
            "Accept": "application/json, text/event-stream",
        })
        self.addCleanup(self.http.close)
        self.mcp = MCPClient(self.http)
        self.mcp.initialize()

    def test_public_discovery_resources_and_auth_challenge(self):
        tools = self.mcp.rpc("tools/list")["tools"]
        self.assertIn("ai_image_generator_create_image", {tool["name"] for tool in tools})
        self.assertTrue(all(tool["securitySchemes"] == [{"type": "oauth2", "scopes": []}] for tool in tools))
        resources = self.mcp.rpc("resources/list")["resources"]
        self.assertIn(VIEW_URI, {resource["uri"] for resource in resources})
        content = self.mcp.rpc("resources/read", {"uri": VIEW_URI})["contents"][0]
        self.assertEqual(content["mimeType"], "text/html;profile=mcp-app")
        self.assertIn('id="root"', content["text"])
        assets = re.findall(r'(?:src|href)="([^"]+/app/project-result-assets/[^"]+)"', content["text"])
        self.assertGreaterEqual(len(assets), 2)
        for asset in assets:
            self.assertEqual(self.http.get(urlsplit(asset).path).status_code, 200)
        self.assertIn("tools", self.http.get("/.well-known/mcp/server-card.json").json())
        self.assertEqual(self.http.get("/.well-known/oauth-authorization-server").status_code, 200)
        denied = self.mcp.rpc("tools/call", {"name": "ping", "arguments": {}})
        self.assertTrue(denied["isError"])
        self.assertIn("mcp/www_authenticate", denied["_meta"])

    def test_oauth_create_poll_download_delete(self):
        oauth_login(self.http, "sk_e2e")
        self.assertEqual(structured(self.mcp.call("ping")), {"result": "pong"})
        project_id, project, downloaded = image_workflow(self.mcp)
        self.assertEqual(project["exact_download_urls"], [IMAGE_URL])
        self.assertEqual(downloaded, IMAGE_BYTES)
        # The fixture counts polls and requires two before returning complete.
        deleted = structured(self.mcp.call("image_projects_retrieve_details", {"id": project_id}))
        self.assertFalse(deleted["enabled"])

    def test_upstream_failure_and_invalid_media_are_tool_errors(self):
        self.http.headers["Authorization"] = "Bearer sk_e2e"
        for name, arguments in (
            ("image_projects_retrieve_details", {"id": "upstream-error"}),
            ("fetch_image_download", {"download_url": "https://example.invalid/image.png"}),
        ):
            with self.subTest(tool=name):
                result = self.mcp.rpc("tools/call", {"name": name, "arguments": arguments})
                self.assertTrue(result["isError"])
        self.assertEqual(structured(self.mcp.call("ping")), {"result": "pong"})

    def test_failed_project_is_terminal_and_retains_error(self):
        self.http.headers["Authorization"] = "Bearer sk_e2e"
        result = structured(self.mcp.call("wait_for_image_project", {"id": "failed-project"}))
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"]["message"], "Fixture generation failed")


def serve(fd, live):
    import uvicorn

    projects = {}

    async def fake_upstream(transport, request):
        # Replacing the outbound transport makes every unrecognized URL fail closed.
        # MCP HTTP traffic still uses real sockets in the separate parent process.
        if request.url.host == "videos.magichour.ai" and str(request.url) == IMAGE_URL:
            return httpx.Response(200, content=IMAGE_BYTES, headers={"Content-Type": "image/png"})
        if request.url.host != "api.magichour.ai":
            raise RuntimeError("Offline E2E blocked unexpected outbound request.")
        if request.headers.get("Authorization") != "Bearer sk_e2e":
            return httpx.Response(401, json={"message": "Invalid API key"})
        path = request.url.path
        if path == "/v1/ai-image-generator" and request.method == "POST":
            body = json.loads(await request.aread())
            if not body:  # The real OAuth validator's nonbillable key probe.
                return httpx.Response(400, json={"message": "Missing required fields"})
            if body != IMAGE_ARGUMENTS:
                return httpx.Response(400, json={"message": "Unexpected creation arguments"})
            project_id = f"e2e-{len(projects) + 1}"
            projects[project_id] = {"polls": 0, "enabled": True}
            return httpx.Response(200, json={"id": project_id, "credits_charged": 5})
        if path.startswith("/v1/image-projects/"):
            project_id = path.rsplit("/", 1)[1]
            if project_id == "upstream-error":
                return httpx.Response(503, json={"message": "Fixture upstream unavailable"})
            if project_id == "failed-project":
                return httpx.Response(200, json={"id": project_id, "status": "error", "downloads": [],
                    "error": {"message": "Fixture generation failed", "code": "fixture_failure"}})
            if project_id not in projects:
                return httpx.Response(404, json={"message": "Unknown test project"})
            project = projects[project_id]
            if request.method == "DELETE":
                project["enabled"] = False
                return httpx.Response(204)
            if request.method == "GET":
                project["polls"] += 1
                complete = project["polls"] >= 2
                return httpx.Response(200, json={
                    "id": project_id, "name": "MCP E2E smoke test", "image_count": 1, "type": "AI_IMAGE",
                    "created_at": "2026-01-01T00:00:00Z", "enabled": project["enabled"], "credits_charged": 5,
                    "status": "complete" if complete else "rendering", "error": None,
                    "downloads": [{"url": IMAGE_URL, "expires_at": "2030-01-01T00:00:00Z"}] if complete else [],
                })
        raise RuntimeError("Offline E2E blocked unexpected upstream operation.")

    def run():
        uvicorn.run("mcp_magichour.openapi_server:app", fd=fd, log_level="error", access_log=False)

    if live:
        run()
    else:
        with patch("httpx.AsyncHTTPTransport.handle_async_request", new=fake_upstream):
            run()


def live_smoke(env_file):
    from dotenv import dotenv_values

    api_key = dotenv_values(env_file).get("MAGIC_HOUR_API_KEY")
    if not api_key or not api_key.strip():
        raise RuntimeError("Dedicated MAGIC_HOUR_API_KEY missing from the specified .env.e2e file.")
    api_key = api_key.strip()
    print("Live smoke: local checkout -> Magic Hour API; one flux-2-klein 640px image, then deletion.", flush=True)
    with local_server(live=True) as url, httpx.Client(
        base_url=url, timeout=210, trust_env=False, headers={"Accept": "application/json, text/event-stream"},
    ) as http:
        mcp = MCPClient(http)
        mcp.initialize()
        # Validate against the local server's actual schema, regardless of where its spec is packaged.
        image_tool = next(tool for tool in mcp.rpc("tools/list")["tools"]
                          if tool["name"] == "ai_image_generator_create_image")
        jsonschema.validate(IMAGE_ARGUMENTS, image_tool["inputSchema"])
        oauth_login(http, api_key)
        image_workflow(mcp)
    print("Live E2E passed.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--live-upstream", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--live", action="store_true", help="Use the real API through a local MCP subprocess.")
    parser.add_argument("--generate-one-image", action="store_true", help="Authorize one paid image and cleanup.")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.e2e")
    args = parser.parse_args()
    if args.serve is not None:
        serve(args.serve, args.live_upstream)
    elif args.live:
        if not args.generate_one_image:
            parser.error("--live requires --generate-one-image (spends credits).")
        try:
            live_smoke(args.env_file)
        except Exception as error:
            # A traceback or HTTP body could expose credentials or signed URLs.
            print(f"Live E2E failed: {type(error).__name__}: {error}", file=sys.stderr)
            sys.exit(1)
    else:
        if args.generate_one_image or args.live_upstream:
            parser.error("Live options require --live.")
        unittest.main(argv=[sys.argv[0]], verbosity=2)
