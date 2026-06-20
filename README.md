# mcp-magichour

MCP server wrapping the Magic Hour API. See `docs/implementation-plan.md` for the build plan and `docs/api-reference.md` for the full endpoint reference.

## Setup

```sh
pip install -e .
```

## Running locally

```sh
python main.py
```

Serves the MCP endpoint at `http://127.0.0.1:8000/` (root, not `/mcp` — that prefix gets added when a host mounts it later, see `mcp_magichour/instance.py`).

By default this talks to the real Magic Hour API and spends real credits. To use the free mock backend instead (instant fake responses, any string works as the token):

```sh
MAGIC_HOUR_ENVIRONMENT=mock python main.py
```

## Testing

Two complementary ways to test, depending on what you want to check:

### `requests.http` — raw Magic Hour API

One request per endpoint with realistic example bodies, defaulting to the mock server. Open in VS Code with the REST Client extension (or equivalent). Needs a `.env` with `MAGIC_HOUR_API_KEY` (copy `.env.example`) when pointed at production.

This tests the Magic Hour API directly — it does **not** go through our MCP server or tool layer.

### MCP Inspector — the actual MCP tools

This is what actually exercises our tools (auth, param schemas, image embedding, etc.), not just the underlying API.

1. Start the server. For anything involving real generated output (e.g. checking that `get_image_project` actually returns a viewable image), use a real key — mock mode's example download URLs are fake and 403 on fetch:
   ```sh
   python main.py
   ```
2. In another terminal:
   ```sh
   npx @modelcontextprotocol/inspector
   ```
   Opens a browser UI at `http://localhost:6274` (auto-authenticated via the URL it prints).
3. In the Inspector UI:
   - Transport: **Streamable HTTP**
   - URL: `http://127.0.0.1:8000/`
   - Add a custom header: `Authorization: Bearer <your real magic hour api key>`
   - Connect
4. Call a `create_*` tool (e.g. `create_ai_image_generator` with `image_count: 1, style: {"prompt": "a cool sunset"}`) — note the returned `id`. This spends real credits.
5. Call `get_image_project` with that `id`. If `status` isn't `complete` yet, call it again after a few seconds. Once complete, Inspector renders the returned image(s) inline alongside the status JSON — that's the actual thing to verify, not just that the call succeeded.
