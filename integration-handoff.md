# Magic Hour MCP Integration Handoff

Use this when mounting the server into an existing FastAPI app.

## What to mount

- Import `app` from `mcp_magichour.server`
- Mount it at `/mcp`
- Final MCP endpoint: `/mcp/`

The internal FastMCP app is intentionally configured for `/`, not `/mcp`.

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

## Environment

Default behavior uses the real Magic Hour API.

Use mock mode for local or staging tests that should not spend credits:

```text
MAGIC_HOUR_ENVIRONMENT=mock
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

- Real mode: returns `upload_url`, `expires_at`, and `file_path`
- Mock mode: returns a successful mock response

### 5. Verify create and poll

Call:

```json
{
  "image_count": 1,
  "style": {
    "prompt": "a bright sunset over a lake"
  }
}
```

with `create_ai_image_generator`, then poll with `get_image_project`.

Expected result:

- Create returns `{id, credits_charged}`
- Poll returns project status
- On `complete`, the response includes direct download URLs
- Image projects also include inline image bytes when the client supports them

### 6. Verify the bad-token path

Run this once against the real API, not mock mode.

Use:

```text
Authorization: Bearer not-a-real-magic-hour-key
```

Call `generate_upload_urls` again.

Expected result:

- The tool call fails cleanly
- The error clearly says the API key was rejected with `401 Unauthorized`
- The host app stays healthy

## Output behavior

- `get_image_project` returns status, direct download URLs, and inline image bytes
- `get_audio_project` returns status, direct download URLs, and inline audio bytes
- `get_video_project` returns status and direct download URLs

## Upload behavior

`generate_upload_urls` only mints presigned URLs. File bytes must be uploaded outside MCP. Then the returned `file_path` is passed into the `create_*` tool.

## Checklist

- Mount `mcp_magichour.server.app` at `/mcp`
- Merge `mcp_magichour.server.lifespan`
- Decide whether staging should use `MAGIC_HOUR_ENVIRONMENT=mock`
- Add any gateway-level auth, rate limits, or analytics needed on `/mcp`
- Run the smoke test above
