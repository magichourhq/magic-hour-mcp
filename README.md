# Magic Hour MCP Server

[![smithery badge](https://smithery.ai/badge/magichourhq/magic-hour)](https://smithery.ai/servers/magichourhq/magic-hour)

OpenAPI-backed MCP server for Magic Hour image, video, and audio generation.

At startup, this server reads `docs/openapi.json` and builds MCP tools with
`FastMCP.from_openapi()`. The OpenAPI spec supplies endpoint coverage, while
Magic Hour MCP policies add agent-facing guidance for async polling, uploads,
and project downloads.

Docs:

- `user.md` - hosted endpoint user guide
- `integration-handoff.md` - FastAPI mount checklist
- `docs/detailed-step-by-step-integration.md` - full backend integration guide
- `docs/api-reference.md` - generated API reference

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

By default, requests go to the production Magic Hour API:

```text
https://api.magichour.ai
```

Tool discovery is public. Tool calls must include your Magic Hour API key:

```text
Authorization: Bearer <magic_hour_api_key>
```

Agents can discover the hosted server card at:

```text
https://mcp.magichour.ai/.well-known/mcp/server-card.json
```

Environment variables:

```sh
MAGIC_HOUR_API_BASE_URL=https://api.magichour.ai
MAGIC_HOUR_OPENAPI_PATH=docs/openapi.json
MCP_OAUTH_ISSUER_URL=https://mcp.magichour.ai
MCP_OAUTH_RESOURCE_URL=https://mcp.magichour.ai
```

Override `MAGIC_HOUR_API_BASE_URL` to use a mock or another API base:

```sh
MAGIC_HOUR_API_BASE_URL=https://api.sideko.dev/v1/mock/magichour/magic-hour/latest python main.py
```

## OAuth compatibility

The optional OAuth shim validates a Magic Hour API key and uses that key as the
access token. Production requires `MCP_OAUTH_ISSUER_URL` and
`MCP_OAUTH_RESOURCE_URL`. See `docs/future-oauth-support.md` for deployment
limits.

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
5. Call `video_assets_generate_presigned_url` or another generated tool.

Notes:

- FastMCP generates endpoint tools from OpenAPI at startup.
- Creation tools return `id` and `credits_charged` immediately.
- OpenAPI `operationId` values are normalized to descriptive snake_case tool names.
- The shared `/v1/files/upload-urls` endpoint is named `video_assets_generate_presigned_url`. It accepts `video`, `audio`, and `image` items.
- Use `wait_for_*_project` to poll jobs. Use `exact_download_urls` exactly as
  returned; never append expiration metadata.
- Image and audio wait tools also return inline media when supported.

Rebuild and type-check the MCP App UI with `cd web && npm ci && npm run build`.

## File uploads

Magic Hour does not accept raw file bytes inside tool arguments. The flow is:

1. Call the generated shared upload-URL tool, `video_assets_generate_presigned_url`
2. Upload the file bytes to the returned `upload_url`
3. Pass the returned `file_path` into the generated creation tool

Direct public media URLs may work, but uploaded `file_path` values are more
reliable. `upload_file_to_presigned_url` handles local files when the server can
read them. Browser chat needs a separate upload UI or bridge; see
`docs/future-chat-ui-handoff.md`.
