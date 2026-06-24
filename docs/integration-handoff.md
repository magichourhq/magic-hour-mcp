# Magic Hour MCP Integration Handoff

This document is for the backend team mounting this MCP server into an existing FastAPI app.

## What gets mounted

- Import `app` from `mcp_magichour.server`.
- Mount it at `/mcp`.
- The resulting MCP endpoint is `/mcp/` on the host app.

Important: the internal FastMCP app is configured for `/`, not `/mcp`, on purpose. Do not remap it again inside the package.

## Required FastAPI wiring

Mounted ASGI sub-apps do not automatically run their own lifespan handlers. This MCP server needs its exported `lifespan` entered explicitly or every tool call will fail at runtime.

Use this pattern:

```python
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

from mcp_magichour.server import app as mcp_app
from mcp_magichour.server import lifespan as mcp_lifespan


@asynccontextmanager
async def combined_lifespan(app: FastAPI):
    async with AsyncExitStack() as stack:
        # Enter your existing lifespan here first, if you have one.
        # await stack.enter_async_context(existing_lifespan(app))
        await stack.enter_async_context(mcp_lifespan(app))
        yield


app = FastAPI(lifespan=combined_lifespan)
app.mount("/mcp", mcp_app)
```

If the host app already has a lifespan, merge both with `AsyncExitStack` rather than replacing the host's existing setup.

## Authentication behavior

`/mcp` authenticates entirely from the incoming header:

```text
Authorization: Bearer <magic_hour_api_key>
```

- The bearer token is the caller's Magic Hour API key.
- The MCP server does not look up users, tenants, or API keys in a database.
- The route does not automatically inherit the host app's normal session or JWT auth.
- If the host team wants rate limiting, abuse controls, analytics, or additional auth on `/mcp`, add them in the gateway or reverse proxy in front of this route.

Never log the raw `Authorization` header.

## Environment choice

By default this server talks to the real Magic Hour API and spends real credits.

To force the free mock backend instead:

```text
MAGIC_HOUR_ENVIRONMENT=mock
```

Recommendation:

- Local development: `mock` is the safest default.
- Staging: use `mock` if the goal is tool wiring and schema testing with no spend.
- Production: leave unset so the server uses the real Magic Hour environment.

This setting is process-wide for the mounted server.

## Post-integration smoke test

After mounting into the host app, verify the mounted route itself rather than `python main.py`.

### 1. Start the host app

Run the normal FastAPI service with the MCP route mounted.

### 2. Open MCP Inspector

```sh
npx @modelcontextprotocol/inspector
```

In the browser UI:

- Transport: `Streamable HTTP`
- URL: `http://127.0.0.1:8000/mcp/`
- Header: `Authorization: Bearer <magic_hour_api_key>`

Replace the base URL as needed for the host environment.

### 3. Verify the basic route

Call `ping`.

Expected result:

- Returns `"pong"`

### 4. Verify an authenticated tool

Call `generate_upload_urls` with:

```json
{
  "items": [
    {
      "type": "image",
      "extension": "png"
    }
  ]
}
```

Expected result:

- In real mode: a normal structured response containing `upload_url`, `expires_at`, and `file_path`
- In mock mode: a fake-but-successful structured response

### 5. Verify a create/poll flow

Recommended quick check in mock mode:

1. Call `create_ai_image_generator` with:
   ```json
   {
     "image_count": 1,
     "style": {
       "prompt": "a bright sunset over a lake"
     }
   }
   ```
2. Copy the returned `id`.
3. Call `get_image_project` with that `id`.

Expected result:

- The create call returns `{id, credits_charged}`
- The poll call returns a project status object
- In real mode, once complete, generated images are embedded inline
- In mock mode, the status shape is still verified, but mock download URLs are not fetchable media

### 6. Deliberate bad-token test

Run this once against the real Magic Hour environment, not mock mode.

Use an obviously invalid header such as:

```text
Authorization: Bearer not-a-real-magic-hour-key
```

Then call `generate_upload_urls` again.

Expected result:

- The tool call fails cleanly
- The visible error message says the Magic Hour API key was rejected with `401 Unauthorized`
- The host app should not crash

## Known behavior worth calling out

- `generate_upload_urls` only mints presigned URLs. File bytes must be uploaded outside MCP, then the returned `file_path` is passed into `create_*` tools.
- `get_image_project` embeds completed images inline.
- `get_audio_project` embeds completed audio inline.
- `get_video_project` returns status and download URLs only; MCP has no video content block type.

## Recommended handoff checklist

- Mount `mcp_magichour.server.app` at `/mcp`
- Merge `mcp_magichour.server.lifespan` into the host app lifespan
- Decide whether staging should set `MAGIC_HOUR_ENVIRONMENT=mock`
- Add any gateway-level auth, rate limiting, or analytics needed on `/mcp`
- Run the smoke test above, including the deliberate bad-token check
