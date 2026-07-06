# mcp-magichour

OpenAPI-backed MCP server for Magic Hour image, video, and audio generation.

At startup, this server reads `docs/openapi.json` and builds MCP tools with
`FastMCP.from_openapi()`. The OpenAPI spec supplies endpoint coverage, while
Magic Hour MCP policies add agent-facing guidance for async polling, uploads,
and project downloads.

User facing guide is detailed here: `user.md`

## Supported Today

- Local MCP Inspector
- Claude Code
- Codex CLI with remote HTTP MCP
- Manual Claude Desktop style configs that allow custom headers
- Backend mounted HTTP MCP at `/mcp`
- Runtime OpenAPI tool generation via standalone `fastmcp`

## Not Included In This Repo

- OAuth or web connector auth
- Chat native file upload UI
- A server side upload bridge for web chat clients

Core docs:

- `user.md` - hosted endpoint user guide
- `integration-handoff.md` - FastAPI mount and smoke test guide
- `docs/detailed-step-by-step-integration.md` - step-by-step backend integration guide
- `docs/ai-agent-go-live-instructions.md` - instructions to give another AI agent for product integration

## Setup

```sh
pip install -e .
```

## Run locally

```sh
python main.py
```

Local MCP endpoint:

```text
http://127.0.0.1:8000/
```

This local dev server runs at `/`, not `/mcp`. The host app adds `/mcp` when it mounts the server.

By default this calls the real Magic Hour API at:

```text
https://api.magichour.ai
```

Every MCP request must include your Magic Hour API key:

```text
Authorization: Bearer <magic_hour_api_key>
```

Optional environment variables:

```sh
MAGIC_HOUR_API_BASE_URL=https://api.magichour.ai
MAGIC_HOUR_OPENAPI_PATH=docs/openapi.json
```

You can still point at a mock or alternate API base by overriding `MAGIC_HOUR_API_BASE_URL`:

```sh
MAGIC_HOUR_API_BASE_URL=https://api.sideko.dev/v1/mock/magichour/magic-hour/0.66.0 python main.py
```

## Test with MCP Inspector

1. Start the server.
2. Run:
   ```sh
   npx @modelcontextprotocol/inspector
   ```
3. In Inspector:
   - Transport: `Streamable HTTP`
   - URL: `http://127.0.0.1:8000/`
   - Header: `Authorization: Bearer <magic_hour_api_key>`
4. Call `ping`.
5. Call a generated OpenAPI tool such as `videoAssets_generatePresignedUrl` or `textToVideo_createVideo`, or one of the custom `wait_for_*_project` tools.

Notes:

- The runtime OpenAPI server no longer hand-registers every endpoint.
- Generated creation tools return immediately with `id` and `credits_charged`.
- We intentionally do not maintain per-endpoint friendly aliases. FastMCP derives tool names from OpenAPI `operationId`, for example `textToVideo_createVideo` and `imageProjects_getDetails`.
- The shared `/v1/files/upload-urls` endpoint is currently exposed as `videoAssets_generatePresignedUrl` because that is the upstream OpenAPI `operationId`; it accepts `video`, `audio`, and `image` items.
- Use the matching generated project details tool or custom `wait_for_*_project` helper to retrieve finished `downloads`.
- `wait_for_video_project`, `wait_for_image_project`, and `wait_for_audio_project` return sanitized download fields so signed URLs stay separate from expiration metadata.
- `wait_for_image_project` and `wait_for_audio_project` also try to inline completed media in the same response while still returning the full project JSON.
- `fetch_image_download` and `fetch_audio_download` remain available as fallback tools if you want to retry a specific `downloads[n].url`.
- For custom wait tools, use `exact_download_urls[n]` as the clickable/downloadable URL. Expiration timestamps are returned separately in `download_expiration_metadata` and must never be appended to URLs.

## Test with Claude Code

Register the local HTTP MCP server for the current repo/project:

```sh
claude mcp add --scope project --transport http magic-hour http://127.0.0.1:8000/ --header "Authorization: Bearer API_KEY"
```

Then start a new Claude Code session in this repo.

## Test with Codex CLI

Codex supports streamable HTTP MCP servers and bearer token auth.

1. Start the server:
   ```sh
   python main.py
   ```
2. Set a temporary API key env var:
   ```sh
   export MAGIC_HOUR_API_KEY=test-key
   ```
   On PowerShell:
   ```powershell
   $env:MAGIC_HOUR_API_KEY = "test-key"
   ```
3. Register the MCP server:
   ```sh
   codex mcp add magic-hour --url http://127.0.0.1:8000/ --bearer-token-env-var MAGIC_HOUR_API_KEY
   ```
4. Verify the config:
   ```sh
   codex mcp get magic-hour --json
   ```
5. Start a new Codex session in this repo and ask it to call `ping`.

Notes:

- This works with the current API key passthrough model. OAuth is not required for Codex CLI.
- Real API calls may spend Magic Hour credits.
- Start Codex from the same shell where `MAGIC_HOUR_API_KEY` is set. Codex must inherit that environment for the MCP server to appear and authenticate correctly.

## File uploads

Magic Hour does not accept raw file bytes inside tool arguments. The flow is:

1. Call the generated shared upload-URL tool, currently `videoAssets_generatePresignedUrl`
2. Upload the file bytes to the returned `upload_url`
3. Pass the returned `file_path` into the generated creation tool

Direct public media URLs may work when they are stable and fetchable, but the upload flow is the reliable path for local files, hotlinked assets, or anything that may not return raw file bytes consistently.

Claude Code can handle this because it has shell access. Plain chat attachments do not map cleanly to this API.

## Upload Example For Future Chat UIs

For an upload based tool such as `imageToVideo_createVideo`:

1. Call `videoAssets_generatePresignedUrl` for an `image`; the same tool also accepts `video` and `audio`
2. Upload the local file bytes to the returned `upload_url`
3. Pass the returned `file_path` into `imageToVideo_createVideo.assets.image_file_path`

The generated upload-URL tool stops at step 1. For local CLI testing,
the custom `upload_file_to_presigned_url` helper can perform step 2 if the MCP
server can read the local file path. For remote/web chat, the caller or host app
still needs to handle the upload bridge.

## Inline Media And Signed URLs

For Inspector testing after a project is complete:

1. Call `wait_for_video_project`, `wait_for_image_project`, or `wait_for_audio_project`
2. Use `exact_download_urls[n]` as the exact link to share or open
3. For image/audio projects, look at the same response for inline media blocks
4. If a particular image/audio download did not inline cleanly, retry it with `fetch_image_download` or `fetch_audio_download`

The image/audio wait helpers now try to download and return generated media as MCP image/audio content instead of making you juggle a second tool in the happy path.
When sharing links, use only `exact_download_urls[n]` or the exact `downloads[n].url` value. Do not append `expires_at` or `download_expiration_metadata` values to the URL.

For future web chat work, see `docs/future-chat-ui-handoff.md`.
