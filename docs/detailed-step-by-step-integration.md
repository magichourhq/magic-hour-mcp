# Detailed Step-by-Step Integration

Use this guide when another team wants to integrate this MCP server into their product with the current lightweight approach.

This guide is for the path we support today:

- mount the MCP server into an existing FastAPI app
- pass through the user's Magic Hour API key as a bearer token
- test with MCP Inspector, Codex CLI, or Claude Code
- generate endpoint tools from `docs/openapi.json` at startup with `FastMCP.from_openapi()`
- keep only small custom helper tools for polling, upload bridging, and inline image/audio fetches

This guide is not for:

- OAuth
- web chat connectors
- browser upload UI
- custom popup or modal upload flows

## Step 1: Decide which integration path you want

Use the current path if the goal is:

- Codex CLI
- Claude Code
- MCP Inspector
- other developer style MCP clients that can send custom headers

Do not use this path if the goal is:

- one click web connector setup
- ChatGPT style app auth
- Claude web chat auth

Those require extra auth and upload UX work outside this repo.

## Step 2: Install this package in the host backend

From the backend repo or service that will expose the MCP route:

```sh
pip install -e .
```

If this MCP repo is used as a dependency from another repo, install it the way that backend normally installs internal Python packages.

## Step 3: Import the MCP app into the host backend

Import these two values:

```python
from mcp_magichour.server import app as mcp_app
from mcp_magichour.server import lifespan as mcp_lifespan
```

Why both are needed:

- `mcp_app` gives you the HTTP MCP routes
- `mcp_lifespan` starts the MCP session manager

## Step 4: Mount the MCP app at `/mcp`

The host backend should expose this server at:

```text
/mcp/
```

Example:

```python
from fastapi import FastAPI

from mcp_magichour.server import app as mcp_app


app = FastAPI()
app.mount("/mcp", mcp_app)
```

Important:

- the internal FastMCP app is configured for `/`
- the host app adds `/mcp`
- the final public MCP endpoint becomes `/mcp/`

## Step 5: Merge the MCP lifespan into the host app lifespan

Mounted ASGI apps do not run their own startup logic automatically.

That means mounting the app is not enough by itself.

You must also merge in `mcp_lifespan`.

Use this pattern:

```python
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

from mcp_magichour.server import app as mcp_app
from mcp_magichour.server import lifespan as mcp_lifespan


@asynccontextmanager
async def combined_lifespan(app: FastAPI):
    async with AsyncExitStack() as stack:
        # If the host app already has startup logic, enter it first.
        # await stack.enter_async_context(existing_lifespan(app))
        await stack.enter_async_context(mcp_lifespan(app))
        yield


app = FastAPI(lifespan=combined_lifespan)
app.mount("/mcp", mcp_app)
```

If this step is skipped, the route may exist but MCP tool calls can fail at runtime.

## Step 6: Preserve the incoming bearer token

This server expects the caller to send:

```text
Authorization: Bearer <magic_hour_api_key>
```

The current auth model is simple:

- the bearer token is the user's Magic Hour API key
- this MCP server passes that key through to the Magic Hour API
- this MCP server does not look up users in a database
- this MCP server does not automatically reuse the host app's session auth

What the host app must do:

1. Accept the incoming `Authorization` header
2. Forward it unchanged to the mounted MCP route
3. Avoid logging the raw bearer token

## Step 7: Add any host-level protections you want

This repo does not force gateway behavior on the host app.

If needed, add these in front of `/mcp`:

- rate limits
- request logging
- analytics
- allowlists
- API gateway rules

Keep one rule in place:

- never log the raw `Authorization` header

## Step 8: Verify reverse proxy or ingress behavior

If the host app sits behind a proxy, ingress, or load balancer, confirm:

1. `/mcp/` forwards to the mounted app unchanged
2. the `Authorization` header is preserved
3. long-lived MCP HTTP traffic is not blocked

If any of those are broken, the MCP client may connect but tools will fail.

## Step 9: Start the host backend

Run the real FastAPI service that now includes the mounted MCP route.

At this point, the public MCP endpoint should look like:

```text
http://<host>/mcp/
```

For purely local testing, it is often:

```text
http://127.0.0.1:8000/mcp/
```

## Step 10: Run a smoke test with MCP Inspector

Use MCP Inspector before handing the integration to another team.

Start Inspector:

```sh
npx @modelcontextprotocol/inspector
```

Use these settings:

- Transport: `Streamable HTTP`
- URL: `http://127.0.0.1:8000/mcp/`
- Header: `Authorization: Bearer <magic_hour_api_key>`

Then test in this order:

1. Call `ping`
2. Call `videoAssets_generatePresignedUrl`, the shared upload-URL tool for image/audio/video assets
3. Call one generated create tool, such as `aiImageGenerator_createImage`
4. Poll with the matching custom `wait_for_*_project` helper

Expected `ping` result:

- `"pong"`

## Step 11: Verify an authenticated tool

Call `videoAssets_generatePresignedUrl` with representative image, audio, and video items:

```json
{
  "items": [
    {
      "type": "image",
      "extension": "png"
    },
    {
      "type": "audio",
      "extension": "mp3"
    },
    {
      "type": "video",
      "extension": "mp4"
    }
  ]
}
```

Expected result in real mode:

- `upload_url`
- `expires_at`
- `file_path`

This is a good auth check because it exercises the real Magic Hour API path without starting a full generation job.

## Step 12: Verify a real generation flow

Call `aiImageGenerator_createImage` with:

```json
{
  "image_count": 1,
  "style": {
    "prompt": "a bright sunset over a lake"
  }
}
```

Then pass the returned `id` to `wait_for_image_project`.

You can also poll manually with `imageProjects_getDetails`, but prefer the custom wait helper for AI clients.

Expected result:

1. `aiImageGenerator_createImage` returns an `id`
2. `wait_for_image_project` waits until `complete`, `error`, `canceled`, or timeout
3. on `complete`, the response includes `exact_download_urls`
4. image and audio wait helpers also try to return inline MCP media content

Important:

- real `create_*` calls may spend credits
- use `exact_download_urls[n]` or the exact `downloads[n].url` value as the link
- never append `expires_at` or `download_expiration_metadata` to a signed URL

## Step 13: Verify the bad key path once

Run this once against the real API:

```text
Authorization: Bearer not-a-real-magic-hour-key
```

Then call `videoAssets_generatePresignedUrl` again.

Expected result:

- the tool call fails cleanly
- the error shows the upstream auth failure
- the host backend stays healthy

## Step 14: Understand the upload flow before building upload-based features

This MCP server does not accept raw file bytes directly inside tool arguments.

The upload flow is:

1. Call `videoAssets_generatePresignedUrl`, the generated shared upload-URL tool for `/v1/files/upload-urls`
2. Upload the file bytes to the returned `upload_url`
3. Pass the returned `file_path` into the generated create tool

Example:

1. call `videoAssets_generatePresignedUrl` for an image; the same tool also accepts audio and video items
2. upload the bytes outside MCP
3. pass the returned `file_path` into `imageToVideo_createVideo.assets.image_file_path`

Important:

- this repo mints upload URLs with `videoAssets_generatePresignedUrl`; despite the generated name, it is not video-only
- the custom `upload_file_to_presigned_url` helper can upload local files for CLI testing when the MCP server can read the path
- this repo does not upload the bytes for browser chat users
- CLI style clients can handle this more easily because they can access local files

## Step 15: Test with Codex CLI if needed

If the team wants to test from Codex CLI:

1. start the backend
2. set `MAGIC_HOUR_API_KEY` in the shell that will launch Codex
3. register the MCP server with `codex mcp add`
4. launch Codex from that same shell
5. ask Codex to call `ping`

Important:

- Codex must inherit the shell environment where `MAGIC_HOUR_API_KEY` is set
- if Codex is launched from another shell or another surface, the MCP server may not appear or authenticate correctly

## Step 16: Test with Claude Code if needed

If the team wants to test from Claude Code:

1. start the backend
2. register the MCP server in Claude Code
3. include `Authorization: Bearer <magic_hour_api_key>`
4. start a new Claude Code session
5. ask Claude Code to call `ping`

## Step 17: Know what is intentionally not included

This repo does not currently include:

- OAuth
- ChatGPT or Claude web connector auth
- chat-native file upload UI
- upload popup or modal
- a server-side upload bridge for web chat users

Those are future integration layers around this MCP server, not blockers for the current developer-first path.

## Step 18: Use this checklist before handoff

Before telling another team the integration is done, confirm:

1. the MCP app is mounted at `/mcp`
2. `mcp_lifespan` is wired into the host app
3. the `Authorization` header is preserved
4. bearer tokens are not logged
5. MCP Inspector can call `ping`
6. MCP Inspector can call `videoAssets_generatePresignedUrl` with image, audio, and video item types
7. at least one real `create_*` flow was tested if credit-spending validation is required
8. Codex CLI or Claude Code testing was verified if those clients are part of the rollout

## Related docs

- `integration-handoff.md`
- `docs/ai-agent-go-live-instructions.md`
- `docs/future-chat-ui-handoff.md`
- `docs/future-oauth-support.md`
