# Magic Hour MCP integration handoff

Use this when mounting the server into an existing FastAPI app.

For a numbered walkthrough, use `docs/detailed-step-by-step-integration.md`.
For instructions written for a coding agent, use `docs/ai-agent-go-live-instructions.md`.

## What to mount

- Import `app` from `mcp_magichour.server`
- Mount it at `/mcp`
- Final MCP endpoint: `/mcp/`

The internal FastMCP app is intentionally configured for `/`, not `/mcp`.

## What this server generates

At startup, the server reads `docs/openapi.json` and builds tools with `FastMCP.from_openapi()`.

OpenAPI `operationId` values are normalized to descriptive snake_case tool names. Examples:

- `video_assets_generate_presigned_url`
- `ai_image_generator_create_image`
- `image_projects_retrieve_details`
- `video_projects_retrieve_details`

`video_assets_generate_presigned_url` is the shared `/v1/files/upload-urls` endpoint. It accepts `video`, `audio`, and `image` asset items.

Do not hand-register Magic Hour endpoints in the host backend. Update
`docs/openapi.json` and restart the MCP server to pick up new endpoints.

The repo adds these custom helpers:

- `wait_for_video_project`
- `wait_for_image_project`
- `wait_for_audio_project`
- `fetch_image_download`
- `fetch_audio_download`
- `fetch_video_download`
- `upload_file_to_presigned_url`

OpenAPI policies add agent guidance by endpoint group rather than by individual endpoint.

## Required lifespan wiring

In FastAPI and Starlette, `lifespan` means app startup and shutdown logic.

Mounted ASGI sub-apps do not run their lifespan automatically. Mounting the MCP
app adds its routes but does not run startup code.

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

The bearer token is the user's Magic Hour API key. The MCP server does not look
up users or tenants, and the route does not inherit the host app's session or
JWT auth. Add rate limits, gateway auth, or analytics in front of `/mcp`.

Never log the raw `Authorization` header.

This setup supports developer clients. See `docs/future-oauth-support.md` for
connector authentication.

## Environment

By default, requests go to the production Magic Hour API.

Use an alternate API base for local or staging tests that should not spend credits:

```text
MAGIC_HOUR_API_BASE_URL=https://api.sideko.dev/v1/mock/magichour/magic-hour/0.66.0
```

Optional:

```text
MAGIC_HOUR_OPENAPI_PATH=docs/openapi.json
```

## Validation

Use MCP Inspector against `/mcp/` with the bearer header, then verify:

1. `ping` returns `pong`.
2. `video_assets_generate_presigned_url` returns `upload_url`, `expires_at`, and
   `file_path` for a valid key.
3. A create tool returns `{id, credits_charged}`.
4. Its `wait_for_*_project` helper reaches a terminal state.
5. A bad key returns `401` without affecting the host app.

Real create calls may spend credits. Use `exact_download_urls` exactly as
returned and never append expiration metadata.

## Checklist

- Mount the app at `/mcp` and merge its lifespan.
- Preserve `Authorization` through the proxy.
- Never log bearer tokens.
- Add host rate limits and request-size limits.
- Override `MAGIC_HOUR_API_BASE_URL` outside production if needed.
- Run the five validation checks above.
