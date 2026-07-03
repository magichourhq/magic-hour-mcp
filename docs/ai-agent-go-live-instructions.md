# AI Agent Go-Live Instructions

Give this file to the AI agent that will integrate Magic Hour MCP into the product backend.

The goal is to go live with the lightest useful integration first, then add only the small targeted pieces needed for chat UI and docs support.

## Operating rules for the AI agent

You are integrating the existing Magic Hour action MCP server into the host product.

Work in this order:

1. Integrate the current MCP server into the backend
2. Verify it works with developer MCP clients
3. Add only light chat UI support if the product already has a chat UI
4. Add docs support so users and internal agents know how to use it

Do not start with OAuth, marketplace submission, or a brand new chatbot unless the product owner explicitly asks for that.

Keep the work targeted:

- use the current MCP server
- keep bearer token passthrough for v1
- avoid major auth refactors
- avoid rebuilding the Magic Hour SDK wrapper
- avoid building a new chat product from scratch
- avoid changing tool schemas unless a test proves it is required

## What already exists

The MCP repo already provides:

- an HTTP MCP server
- Magic Hour API key passthrough via `Authorization: Bearer <magic_hour_api_key>`
- runtime OpenAPI tool generation from `docs/openapi.json`
- generated image, video, audio, and file tools named from OpenAPI `operationId`
- `videoAssets_generatePresignedUrl`, the generated shared upload-URL tool for `/v1/files/upload-urls`
- async create and poll flows
- custom `wait_for_video_project`, `wait_for_image_project`, and `wait_for_audio_project` helpers
- inline image and audio results where MCP clients support them
- sanitized image, audio, and video download URLs

The current MCP app exports:

```python
from mcp_magichour.server import app as mcp_app
from mcp_magichour.server import lifespan as mcp_lifespan
```

The current app is configured internally at `/`.

When mounted into the product backend, mount it at:

```text
/mcp/
```

## What does not exist yet

Do not assume these already exist:

- OAuth
- ChatGPT or Claude web connector auth
- a browser upload widget
- a chat popup
- a server side upload bridge
- platform marketplace submission assets

These can be added later, but they are not required for the first go-live path.

## Phase 0: Inspect the host product first

Before changing files, inspect the host product and answer these questions:

1. Is the backend FastAPI, Starlette, or another ASGI framework?
2. Where is the main `FastAPI()` app created?
3. Does the backend already use a lifespan function?
4. Does the backend already have middleware for auth, logging, rate limits, or request IDs?
5. Is there a reverse proxy, gateway, or ingress config?
6. Does the product already have a chat UI?
7. Does the product already have file upload endpoints?
8. Does the product already store Magic Hour API keys per user?
9. Does the product already have user docs or developer docs pages?

Write down the answers in your implementation notes before editing.

## Phase 1: Integrate the current MCP server into the backend

### Step 1: Add the MCP package to the backend

Install this MCP repo in the host backend the same way the product handles internal Python packages.

Examples:

```sh
pip install -e .
```

or add the package to the backend dependency file if the product uses one.

Do not vendor-copy random files unless the product has no package install path.

### Step 2: Import the MCP app and lifespan

In the backend file where the product creates the FastAPI app, add:

```python
from mcp_magichour.server import app as mcp_app
from mcp_magichour.server import lifespan as mcp_lifespan
```

### Step 3: Mount the MCP app at `/mcp`

Add:

```python
app.mount("/mcp", mcp_app)
```

The final MCP endpoint should be:

```text
https://<product-domain>/mcp/
```

Do not mount it at `/mcp/mcp`.

### Step 4: Merge the MCP lifespan

If the backend does not already have a lifespan function, use this pattern:

```python
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

from mcp_magichour.server import app as mcp_app
from mcp_magichour.server import lifespan as mcp_lifespan


@asynccontextmanager
async def combined_lifespan(app: FastAPI):
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(mcp_lifespan(app))
        yield


app = FastAPI(lifespan=combined_lifespan)
app.mount("/mcp", mcp_app)
```

If the backend already has a lifespan function, preserve it and enter it before `mcp_lifespan`:

```python
@asynccontextmanager
async def combined_lifespan(app: FastAPI):
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(existing_lifespan(app))
        await stack.enter_async_context(mcp_lifespan(app))
        yield
```

If this step is skipped, routes may exist but MCP tool calls can fail.

### Step 5: Preserve the `Authorization` header

The current MCP server expects:

```text
Authorization: Bearer <magic_hour_api_key>
```

The host backend, proxy, and gateway must preserve that header.

Do not replace it with the product session token unless you also build a mapping layer from product user to Magic Hour API key.

For v1, the simplest supported model is:

1. user gets their Magic Hour API key
2. MCP client sends it as `Authorization: Bearer <magic_hour_api_key>`
3. MCP server passes it to Magic Hour

### Step 6: Add basic host protections

Add these only if they already fit the product's backend patterns:

- route level rate limits
- request size limits
- structured logs without raw tokens
- request IDs
- gateway allowlists for staging

Required rule:

- never log the raw `Authorization` header

### Step 7: Verify proxy and gateway config

If the product uses a proxy or gateway, confirm:

1. `/mcp/` forwards unchanged
2. trailing slash behavior is stable
3. `Authorization` is preserved
4. streaming or long-lived HTTP is allowed
5. request bodies are not stripped

### Step 8: Add a backend smoke test if the product has tests

Add the lightest test that proves:

1. `/mcp/` is mounted
2. the app starts with the merged lifespan
3. requests with `Authorization` can reach the mounted app

Do not spend Magic Hour credits in automated tests.

Use mock mode for test environments if needed:

```text
MAGIC_HOUR_API_BASE_URL=https://api.sideko.dev/v1/mock/magichour/magic-hour/0.66.0
```

## Phase 2: Verify with MCP clients

### Step 1: Test with MCP Inspector

Start the host backend.

Run:

```sh
npx @modelcontextprotocol/inspector
```

Use:

- Transport: `Streamable HTTP`
- URL: `http://127.0.0.1:8000/mcp/`
- Header: `Authorization: Bearer <magic_hour_api_key>`

Call:

1. `ping`
2. `videoAssets_generatePresignedUrl` with image, audio, and video item types
3. one safe `imageProjects_getDetails`, `videoProjects_getDetails`, or `audioProjects_getDetails` call with a known id if available

Expected `ping` result:

```text
pong
```

### Step 2: Test real auth without spending generation credits

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

- `upload_url`
- `expires_at`
- `file_path`

This proves the bearer key reaches the real Magic Hour API.

### Step 3: Test one real generation only when approved

Only do this if credit spending is approved.

Call `aiImageGenerator_createImage`:

```json
{
  "image_count": 1,
  "style": {
    "prompt": "a bright sunset over a lake"
  }
}
```

Then pass the returned `id` to `wait_for_image_project` until status is:

- `complete`
- `error`
- `canceled`

Expected complete result:

- project status is `complete`
- `exact_download_urls` contains at least one full signed URL
- `download_expiration_metadata` is separate metadata and must not be appended to the URL
- image and audio wait helpers may also include inline MCP media content

### Step 4: Test Codex CLI if it is part of rollout

Register:

```sh
export MAGIC_HOUR_API_KEY="<magic_hour_api_key>"
codex mcp add magic-hour --url http://127.0.0.1:8000/mcp/ --bearer-token-env-var MAGIC_HOUR_API_KEY
codex mcp get magic-hour --json
```

Then launch Codex from the same shell:

```sh
codex
```

Ask:

```text
Call the magic-hour MCP ping tool once.
```

Important:

- Codex must inherit `MAGIC_HOUR_API_KEY`
- if Codex is launched from another shell, the server may not appear or authenticate

For local MCP repo testing without the host backend, use:

```text
http://127.0.0.1:8000/
```

For product backend testing, use:

```text
http://127.0.0.1:8000/mcp/
```

### Step 5: Test Claude Code if it is part of rollout

Register the MCP server with the product backend URL and bearer header.

Example:

```sh
claude mcp add --scope project --transport http magic-hour http://127.0.0.1:8000/mcp/ --header "Authorization: Bearer <magic_hour_api_key>"
```

Start a new Claude Code session and ask it to call:

```text
magic-hour ping
```

## Phase 3: Add light chat UI support only if the product already has chat

Do this phase only if the product already has a chat UI or an existing place where users upload files.

Do not build a brand new chatbot for this phase.

### Step 1: Identify the minimum upload path

Find the product's existing file upload pattern.

Prefer reusing:

- existing file picker
- existing upload endpoint
- existing storage bucket
- existing signed URL logic
- existing auth/session model

Avoid creating a separate upload system unless no existing path exists.

### Step 2: Add an upload bridge endpoint if needed

Add one small endpoint that accepts a file upload and returns a Magic Hour usable reference.

Recommended shape:

```text
POST /api/magic-hour/uploads
```

Input:

- authenticated product user
- multipart file
- file type such as `image`, `video`, or `audio`

Output:

```json
{
  "file_path": "<magic_hour_file_path>",
  "content_type": "image/png",
  "expires_at": "<timestamp>"
}
```

Backend flow:

1. validate product user
2. validate file type
3. validate file size
4. call Magic Hour `videoAssets_generatePresignedUrl` or the underlying `/v1/files/upload-urls` endpoint; this generated tool accepts image, audio, and video item types
5. upload raw bytes to returned `upload_url`
6. return `file_path` to the frontend

Use the user's Magic Hour API key if the product stores it.

If the product does not store Magic Hour API keys, require the user to provide the key in the client setup for v1.

### Step 3: Wire the chat UI to the upload bridge

When the user asks for a tool that needs a file:

1. show the existing file picker or upload control
2. upload the selected file to `/api/magic-hour/uploads`
3. get back `file_path`
4. pass that `file_path` into the MCP tool call

Example mapping:

```text
imageToVideo_createVideo.assets.image_file_path = file_path
```

Do not send base64 file bytes through the LLM prompt.

Do not ask the user to manually paste huge binary content.

### Step 4: Keep chat scope small

For the first version, support only one or two upload-heavy flows.

Recommended first flows:

1. image to video
2. image upscaler
3. background remover

Avoid trying to support every advanced file flow in the first pass.

### Step 5: Add chat UI error handling

Handle these cases:

- upload URL expired
- file too large
- unsupported file type
- Magic Hour API key missing
- Magic Hour API key rejected
- generation failed
- generation is still rendering

Show the final download URL when a job completes.

## Phase 4: Add docs support

Docs support should help users and AI agents understand how to use the integration.

Keep this phase lightweight.

### Step 1: Add an internal docs page

Create or update a product docs page named something like:

```text
Magic Hour MCP Setup
```

Include:

- what the MCP connector does
- how to get a Magic Hour API key
- MCP endpoint URL
- supported clients
- auth header format
- upload behavior
- known limitations

### Step 2: Add copy for developer clients

Include setup snippets for:

- MCP Inspector
- Codex CLI
- Claude Code

Required auth format:

```text
Authorization: Bearer <magic_hour_api_key>
```

### Step 3: Add a docs-only support note

Magic Hour has a documentation-only MCP endpoint:

```text
https://docs.magichour.ai/mcp
```

That endpoint is for docs lookup only.

It does not execute Magic Hour actions.

Do not confuse it with the action MCP server mounted at:

```text
https://<product-domain>/mcp/
```

### Step 4: Add an AI usage note

Document this behavior for AI clients:

1. after a generated create tool, call the matching `wait_for_*_project` helper
2. stop polling only when status is `complete`, `error`, or `canceled`
3. show `exact_download_urls` or exact `downloads[n].url` values to the user
4. never append `expires_at` or `download_expiration_metadata` to signed URLs
5. for uploads, call `videoAssets_generatePresignedUrl`, upload bytes outside MCP, then pass `file_path` to the create tool; despite the generated name, this is the shared file upload-URL tool

### Step 5: Add a user-facing limitations section

Include:

- real generation can spend credits
- upload URLs expire
- video output is returned as URLs
- local CLI clients can access local files
- web chat clients need an upload UI or upload bridge
- OAuth is not included in the v1 bearer-token path

## Phase 5: Decide whether OAuth is needed later

Do not build OAuth for the first go-live path unless it is required by the target platform.

Bearer passthrough works for:

- MCP Inspector
- Codex CLI
- Claude Code
- manual clients that support custom headers

OAuth or an auth adapter is likely needed for:

- one click web connector setup
- marketplace style setup
- web chat surfaces that do not allow custom bearer headers

If OAuth becomes required later, keep it separate from this v1 integration.

Recommended OAuth approach:

1. use the product's existing login if it already supports OAuth or OIDC
2. map the logged-in product user to a Magic Hour API key
3. mint or validate connector tokens at the product backend
4. keep the MCP tool implementation mostly unchanged

Do not rewrite the MCP tool layer just to add OAuth.

## Phase 6: Final verification checklist

Before marking this complete, verify:

1. `/mcp/` exists on the product backend
2. `mcp_lifespan` runs at startup
3. `Authorization` reaches the MCP app
4. raw bearer tokens are not logged
5. `ping` works through MCP Inspector
6. `videoAssets_generatePresignedUrl` works with a real Magic Hour key for image, audio, and video item types
7. a bad key returns a clean auth error
8. at least one generation flow was tested if approved
9. final download URLs are shown to the user
10. Codex CLI works if it is part of rollout
11. Claude Code works if it is part of rollout
12. docs explain the setup and limitations
13. upload behavior is documented
14. OAuth is clearly marked as future work unless implemented

## Expected final deliverables

When finished, report:

1. files changed
2. MCP endpoint URL
3. auth model used
4. test commands run
5. test results
6. whether real credits were spent
7. remaining limitations
8. whether chat UI upload support was added or deferred
9. whether docs support was added or deferred

## Stop conditions

Stop and ask the product owner before doing any of these:

1. building OAuth
2. replacing the product auth system
3. storing Magic Hour API keys in a new database table
4. building a new chatbot
5. adding a new storage provider
6. changing MCP tool schemas
7. changing billing or credit behavior
8. submitting to a marketplace

These are larger product decisions.

The v1 go-live path should stay light, targeted, and straightforward.
