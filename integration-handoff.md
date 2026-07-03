# Magic Hour MCP Integration Handoff

Use this when mounting the server into an existing FastAPI app.

If the receiving team wants a more explicit sequence, use `docs/detailed-step-by-step-integration.md`.

If they want to give instructions directly to an AI coding agent, use `docs/ai-agent-go-live-instructions.md`.

## What to mount

- Import `app` from `mcp_magichour.server`
- Mount it at `/mcp`
- Final MCP endpoint: `/mcp/`

The internal FastMCP app is intentionally configured for `/`, not `/mcp`.

## What this server generates

At startup, the server reads `docs/openapi.json` and builds tools with `FastMCP.from_openapi()`.

The generated tool names come from OpenAPI `operationId` values. Examples:

- `videoAssets_generatePresignedUrl`
- `aiImageGenerator_createImage`
- `imageProjects_getDetails`
- `videoProjects_getDetails`

`videoAssets_generatePresignedUrl` is the shared `/v1/files/upload-urls` endpoint even though the generated name says `videoAssets`. It accepts `video`, `audio`, and `image` asset items.

Do not hand-register every Magic Hour endpoint in the host backend. New endpoints should flow in by updating `docs/openapi.json`, then restarting the MCP server.

This repo only keeps a small custom layer for cross-cutting behavior:

- `wait_for_video_project`
- `wait_for_image_project`
- `wait_for_audio_project`
- `fetch_image_download`
- `fetch_audio_download`
- `upload_file_to_presigned_url`

The OpenAPI policy layer adds broad agent guidance by endpoint group, not one-off per-endpoint mappings.

## Required lifespan wiring

In FastAPI and Starlette, `lifespan` means app startup and shutdown logic.

Mounted ASGI sub-apps do not run their own lifespan automatically. That means mounting the MCP app adds its routes, but does not run its startup code by itself.

For this server, `mcp_magichour.server.lifespan` starts the MCP session manager. You must merge it into the host app lifespan or tool calls will fail at runtime.

```python
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

from mcp_magichour.server import app as mcp_app
from mcp_magichour.server import lifespan as mcp_lifespan


@asynccontextmanager
async def combined_lifespan(app: FastAPI):
    async with AsyncExitStack() as stack:
        # If the host app already has a lifespan, enter it first.
        # await stack.enter_async_context(existing_lifespan(app))
        await stack.enter_async_context(mcp_lifespan(app))
        yield


app = FastAPI(lifespan=combined_lifespan)
app.mount("/mcp", mcp_app)
```

## Router setup checklist

When wiring this into the real backend:

1. Mount the MCP app at `/mcp`
2. Merge `mcp_lifespan` into the host app lifespan
3. Make sure the public route keeps the incoming `Authorization` header
4. Put any gateway auth, rate limits, or logging in front of `/mcp`
5. Exclude raw bearer tokens from logs
6. Smoke test with MCP Inspector before handing it to other teams

If the host app uses a reverse proxy or ingress, make sure `/mcp/` forwards to the mounted app unchanged.

## Auth model

`/mcp` reads the incoming Magic Hour API key directly from:

```text
Authorization: Bearer <magic_hour_api_key>
```

Important:

- The bearer token is the user's Magic Hour API key
- The MCP server does not look up users or tenants in a database
- The route does not automatically inherit the host app's session or JWT auth
- If you want rate limits, gateway auth, or analytics on `/mcp`, add them in front of this route

Never log the raw `Authorization` header.

What this means:

- This repo supports developer style clients now
- Web connector style auth is out of scope here
- If the product later needs OAuth, add it in the host app or in a future auth layer

## Auth setup options for the host app

### Option 1: Keep current bearer passthrough

Use this now if the goal is:

- Codex CLI
- Claude Code
- MCP Inspector
- manual desktop or CLI setups

This is the lightest path.

For Codex CLI, launch Codex from the same shell where the bearer token env var is set so the MCP server appears and authenticates correctly.

### Option 2: Add an auth adapter later

Use this later if the goal is:

- web chat
- connector style flows
- one click user setup

The auth adapter should live outside this repo unless the team chooses to add OAuth directly here.

## Environment

Default behavior uses the real Magic Hour API.

Use an alternate API base for local or staging tests that should not spend credits:

```text
MAGIC_HOUR_API_BASE_URL=https://api.sideko.dev/v1/mock/magichour/magic-hour/0.66.0
```

Optional:

```text
MAGIC_HOUR_OPENAPI_PATH=docs/openapi.json
```

## Smoke test

Run these checks after mounting into the host app.

### 1. Start the host app

Start the real FastAPI service with the mounted `/mcp` route.

### 2. Connect with MCP Inspector

```sh
npx @modelcontextprotocol/inspector
```

Inspector settings:

- Transport: `Streamable HTTP`
- URL: `http://127.0.0.1:8000/mcp/`
- Header: `Authorization: Bearer <magic_hour_api_key>`

### 3. Verify basic connectivity

Call `ping`.

Expected result:

- `"pong"`

### 4. Verify an authenticated tool

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

Expected result:

- Real mode: returns `upload_url`, `expires_at`, and `file_path`
- Alternate API base or mock mode: returns a successful mock response

### 5. Verify create and poll

Call `aiImageGenerator_createImage`:

```json
{
  "image_count": 1,
  "style": {
    "prompt": "a bright sunset over a lake"
  }
}
```

Then pass the returned `id` to `wait_for_image_project`.

You can also poll with the generated `imageProjects_getDetails` tool, but the custom wait helper is easier for AI clients because it waits until the project reaches a terminal state and normalizes download links.

Expected result:

- Create returns `{id, credits_charged}`
- Wait returns project status
- On `complete`, the response includes `exact_download_urls`
- `download_expiration_metadata` is separate metadata and must not be appended to the URL
- Image projects also include inline image bytes when the client supports them

### 6. Verify the bad-token path

Run this once against the real API, not mock mode.

Use:

```text
Authorization: Bearer not-a-real-magic-hour-key
```

Call `videoAssets_generatePresignedUrl` again.

Expected result:

- The tool call fails cleanly
- The error clearly says the API key was rejected with `401 Unauthorized`
- The host app stays healthy

## Output behavior

- `wait_for_image_project` returns status, sanitized download fields, and inline image bytes when supported
- `wait_for_audio_project` returns status, sanitized download fields, and inline audio bytes when supported
- `wait_for_video_project` returns status and sanitized download fields
- For every completed project, use `exact_download_urls[n]` or the exact `downloads[n].url` value as the shareable link
- Never append `expires_at` or `download_expiration_metadata` values to a signed URL

## Upload behavior

`videoAssets_generatePresignedUrl` is the shared generated tool for `/v1/files/upload-urls`; despite the name, it mints presigned URLs for image, audio, and video assets. File bytes must be uploaded outside MCP. Then the returned `file_path` is passed into the generated create tool.

For local CLI testing, `upload_file_to_presigned_url` can upload a file that exists on the MCP server's filesystem.

This repo does not provide a browser upload UI or a chat upload bridge.

## Future popup or chat upload flow

If the team later wants ChatGPT, Claude Chat, or another web chat surface:

1. User asks for a tool that needs a file
2. Chat UI opens an upload popup, modal, or widget
3. User selects a file
4. Frontend or backend uploads the file
5. Chat flow resumes with a hosted URL or Magic Hour `file_path`

This is future phase work. See `docs/future-chat-ui-handoff.md`.

## Checklist

- Mount `mcp_magichour.server.app` at `/mcp`
- Merge `mcp_magichour.server.lifespan`
- Decide whether staging should override `MAGIC_HOUR_API_BASE_URL`
- Verify `videoAssets_generatePresignedUrl` with at least one `image`, `audio`, and `video` item
- Add any gateway-level auth, rate limits, or analytics needed on `/mcp`
- Run the smoke test above
