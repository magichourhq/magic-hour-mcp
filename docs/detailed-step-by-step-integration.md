# Detailed integration guide

Use this guide to mount the server in FastAPI, preserve bearer auth, and verify
it with MCP Inspector. For client setup, OAuth, or browser uploads, use the
related docs at the end.

## Step 1: Decide which integration path you want

Use bearer passthrough for:

- Codex CLI
- Claude Code
- MCP Inspector
- other developer style MCP clients that can send custom headers

Use the OAuth compatibility layer for:

- one click web connector setup
- ChatGPT style app auth
- Claude web chat auth

Claude connector auth is included. Chat-native uploads still require extra UX work outside this repo.

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

Both imports are required:

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

The internal FastMCP app uses `/`. The host adds `/mcp`, making the final
endpoint `/mcp/`.

## Step 5: Merge the MCP lifespan into the host app lifespan

Mounted ASGI apps do not run their startup logic automatically. Mount the app
and merge `mcp_lifespan` into the host lifespan.

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

The auth model:

- the bearer token is the user's Magic Hour API key
- this MCP server passes that key through to the Magic Hour API
- this MCP server does not look up users in a database
- this MCP server does not automatically reuse the host app's session auth

The host app must:

1. Accept the incoming `Authorization` header
2. Forward it unchanged to the mounted MCP route
3. Avoid logging the raw bearer token

## Step 7: Add host protections

This repo does not force gateway behavior on the host app.

If needed, add these in front of `/mcp`:

- rate limits
- request logging
- analytics
- allowlists
- API gateway rules

Never log the raw `Authorization` header.

## Step 8: Verify reverse proxy or ingress behavior

If the host app sits behind a proxy, ingress, or load balancer, confirm:

1. `/mcp/` forwards to the mounted app unchanged
2. the `Authorization` header is preserved
3. long-lived MCP HTTP traffic is not blocked

If any of those are broken, the MCP client may connect but tools will fail.

## Step 9: Start the host backend

Run the real FastAPI service that now includes the mounted MCP route.

The public MCP endpoint should look like:

```text
http://<host>/mcp/
```

For local testing:

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
2. Call `video_assets_generate_presigned_url`, the shared upload-URL tool for image/audio/video assets
3. Call one generated create tool, such as `ai_image_generator_create_image`
4. Poll with the matching custom `wait_for_*_project` helper

Expected `ping` result:

- `"pong"`

## Step 11: Verify an authenticated tool

Call `video_assets_generate_presigned_url` with representative image, audio, and video items:

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

This checks real authentication without starting a generation job.

## Step 12: Verify a real generation flow

Call `ai_image_generator_create_image` with:

```json
{
  "image_count": 1,
  "style": {
    "prompt": "a bright sunset over a lake"
  }
}
```

Then pass the returned `id` to `wait_for_image_project`.

You can also poll manually with `image_projects_retrieve_details`, but prefer the custom wait helper for AI clients.

Expected result:

1. `ai_image_generator_create_image` returns an `id`
2. `wait_for_image_project` waits until `complete`, `error`, `canceled`, or timeout
3. on `complete`, the response includes `exact_download_urls`
4. image and audio wait helpers also try to return inline MCP media content

Real `create_*` calls may spend credits. Use `exact_download_urls[n]` or the
exact `downloads[n].url` value as the link. Never append `expires_at` or
`download_expiration_metadata` to a signed URL.

## Step 13: Verify the bad key path once

Run this once against the real API:

```text
Authorization: Bearer not-a-real-magic-hour-key
```

Then call `video_assets_generate_presigned_url` again.

Expected result:

- the tool call fails cleanly
- the error shows the upstream auth failure
- the host backend stays healthy

## Step 14: Understand the upload flow

This MCP server does not accept raw file bytes directly inside tool arguments.

The upload flow is:

1. Call `video_assets_generate_presigned_url`, the generated shared upload-URL tool for `/v1/files/upload-urls`
2. Upload the file bytes to the returned `upload_url`
3. Pass the returned `file_path` into the generated create tool

Direct public media URLs can work when they are stable and return raw file bytes, but treat them as best-effort. For local files, user-uploaded files, and hotlinked assets, prefer uploading first and using the returned Magic Hour `file_path`.

Example:

1. call `video_assets_generate_presigned_url` for an image; the same tool also accepts audio and video items
2. upload the bytes outside MCP
3. pass the returned `file_path` into `image_to_video_create_video.assets.image_file_path`

`video_assets_generate_presigned_url` mints upload URLs for image, audio, and video.
The custom `upload_file_to_presigned_url` helper can upload local files during
CLI testing when the MCP server can read the path. Browser chat users still
need an upload UI or bridge.

## Step 15: Run the handoff checklist

Before telling another team the integration is done, confirm:

1. the MCP app is mounted at `/mcp`
2. `mcp_lifespan` is wired into the host app
3. the `Authorization` header is preserved
4. bearer tokens are not logged
5. MCP Inspector can call `ping`
6. MCP Inspector can call `video_assets_generate_presigned_url` with image, audio, and video item types
7. at least one real `create_*` flow was tested if credit-spending validation is required
8. rollout clients were tested using `user.md`

## Related docs

- `user.md` - Claude, Claude Code, and Codex setup
- `docs/future-chat-ui-handoff.md`
- `docs/future-oauth-support.md`
