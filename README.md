# Magic Hour MCP Server

OpenAPI-backed MCP server for Magic Hour image, video, and audio generation.

At startup, this server reads `docs/openapi.json` and builds MCP tools with
`FastMCP.from_openapi()`. The OpenAPI spec supplies endpoint coverage, while
Magic Hour MCP policies add agent-facing guidance for async polling, uploads,
and project downloads.

Docs:

- [Magic Hour agent skills](https://github.com/magichourhq/skills) - media workflows, published examples, and recovery guidance
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

Public OAuth clients can use the stateless `POST /register` compatibility endpoint.

## Automated smoke checks

Build the UI, then run all tests (including the offline HTTP E2E checks):

```sh
npm --prefix web ci
npm --prefix web run build
python -m unittest discover -s tests -v
```

For only the E2E checks, run `python tests/test_e2e.py`. They start this
checkout's MCP app in a separate localhost process and exercise public discovery,
resources and assets, OAuth registration/PKCE/code replay rejection, generated
tools, image polling, signed media download, deletion, and failure responses.
All upstream HTTP requests are intercepted; unrecognized requests fail closed.
No API key, internet access, or generation credits are needed after dependencies
are installed. CI runs these checks on every PR and push to `main`.
This checks the MCP protocol and served UI assets, not browser rendering.

Live smoke testing is optional and spends credits. Put a dedicated test key in
local `.env.e2e` (gitignored) as `MAGIC_HOUR_API_KEY=...`, then explicitly opt in:

```sh
python tests/test_e2e.py --live --generate-one-image --env-file .env.e2e
```

The live check starts **this local checkout**, pins its upstream to the real
Magic Hour API, validates the key through OAuth, then creates exactly one
`flux-2-klein` image at `640px` and `1:1` (5 credits in the checked-in spec,
tied for the lowest listed price). It polls, downloads the image through MCP,
and attempts deletion in `finally` using the original creation ID. It never
loads `.env`, calls the deployed MCP endpoint, or retries image creation.
Subprocess logs are discarded to avoid retaining keys or signed media URLs.
If cleanup fails, the command exits unsuccessfully and prints the project ID
for manual deletion. Force-killing the process, or losing the creation response
before an ID arrives, can require manual cleanup in the test account.

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
reliable. Upload bytes from the caller or a dedicated upload bridge; the hosted
MCP server never reads caller-supplied local filesystem paths. Browser chat needs
a separate upload UI or bridge; see `docs/future-chat-ui-handoff.md`.
