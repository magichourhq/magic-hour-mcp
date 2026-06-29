# mcp-magichour

MCP server for Magic Hour image, video, and audio generation.

## Supported Today

- Local MCP Inspector
- Claude Code
- Codex CLI with remote HTTP MCP
- Manual Claude Desktop style configs that allow custom headers
- Backend mounted HTTP MCP at `/mcp`

## Not Included In This Repo

- OAuth or web connector auth
- Chat native file upload UI
- A server side upload bridge for web chat clients

Core docs:

- `integration-handoff.md` - FastAPI mount and smoke test guide

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

Use the free mock backend for local testing:

```sh
MAGIC_HOUR_ENVIRONMENT=mock python main.py
```

In mock mode, any bearer token string works and no real credits are spent.

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
5. Call a `create_*` tool, then the matching `get_*_project` tool.

Notes:

- Mock download URLs are not fetchable media. Use a real key if you need to verify inline image or audio rendering.
- Completed image and audio results include inline media plus direct download URLs.
- Completed video results include direct download URLs.

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
   MAGIC_HOUR_ENVIRONMENT=mock python main.py
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
- In this repo, mock mode accepts any bearer token string.
- Start Codex from the same shell where `MAGIC_HOUR_API_KEY` is set. Codex must inherit that environment for the MCP server to appear and authenticate correctly.

## File uploads

Magic Hour does not accept raw file bytes inside tool arguments. The flow is:

1. Call `generate_upload_urls`
2. Upload the file bytes to the returned `upload_url`
3. Pass the returned `file_path` into the `create_*` tool

Claude Code can handle this because it has shell access. Plain chat attachments do not map cleanly to this API.

## Upload Example For Future Chat UIs

For an upload based tool such as `create_image_to_video`:

1. Call `generate_upload_urls` for an `image`
2. Upload the local file bytes to the returned `upload_url`
3. Pass the returned `file_path` into `create_image_to_video.assets.image_file_path`

This repo stops at step 1. The caller or host app must do step 2.

For future web chat work, see `docs/future-chat-ui-handoff.md`.
