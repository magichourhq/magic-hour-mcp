# Magic Hour MCP Server

OpenAPI-backed MCP server for Magic Hour image and video generation and editing,
music generation, uploads, completion waiting and retrieval of existing audio projects.

At startup, this server reads `docs/openapi.json` and builds MCP tools with
`FastMCP.from_openapi()`. The OpenAPI spec supplies endpoint coverage, while
Magic Hour MCP policies add agent-facing guidance for async polling, uploads,
and project downloads.

## Connect an agent

Hosted creation endpoint: **https://mcp.magichour.ai/**

Follow the [client setup guide](https://docs.magichour.ai/integration/model-context-protocol)
for Claude, Claude Code, Codex CLI or ChatGPT. After connecting, call
`account_retrieve` to verify API access and inspect the account before generation;
`ping` alone does not validate the API key.

Try: “Use my portrait to create a cinematic space-pilot still, then animate it
with a slow camera push. Keep my face consistent, wait for the render and show
the finished video. Check account eligibility and cost before spending credits.”

Use `workflow_guide_retrieve` for the ordered tool steps and output review.
For documentation search while coding, use the separate
[API Docs MCP](https://docs.magichour.ai/integration/api-docs-mcp).

Docs:

- [Magic Hour agent skills](https://github.com/magichourhq/skills) - media workflows, published examples, and recovery guidance
- `user.md` - hosted endpoint user guide
- `integration-handoff.md` - FastAPI mount checklist
- `docs/detailed-step-by-step-integration.md` - full backend integration guide
- `docs/api-reference.md` - generated API reference

## Credits and data

MCP generations use Magic Hour account credits. You can buy a [one-time credit pack](https://magichour.ai/pricing) without a subscription; unused credits never expire. Packs include applicable watermark-free exports and commercial use, subject to the [Terms](https://magichour.ai/terms-of-service) and rights in your source content. They do not unlock subscription-only model or resolution access.

Technical failures generally return credits after processing; completed but unsatisfactory results do not automatically return credits. A wait timeout is not proof of project failure—resume with the existing project ID. Payment refunds follow the separate [refund policy](https://magichour.ai/refund-policy).

[Trust & Data Use](https://magichour.ai/trust) explains Magic Hour's no-training policy for customer prompts, uploads and outputs, and feature-dependent retention.

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

Tool discovery is public. Tool calls require authentication. Hosted Claude and ChatGPT OAuth
connectors handle the token after you sign in and authorize access. For manual bearer clients,
include your Magic Hour API key:

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

## Read-only workflow guidance

`workflow_guide_retrieve` accepts `video-face-swap`, `image-editor`, `image-to-video`,
`video-generator`, `lip-sync`, `ugc-ads`, `talking-photo` or `media-apis`. It returns
inputs, product and API documentation URLs, exact creation-tool names, ordered
steps, example requests, follow-up edits and review criteria. It makes no API
request, spends no credits and does not verify account access. Like other tools, it requires the existing authentication flow.

Clients can also discover the public `magic-hour://workflows/index` resource and
read `magic-hour://workflows/{workflow}`. UGC uses `ai_ugc_ad_create_video` with a
prepared image, script and supported duration; image composition is a separate
Image Editor call. Keep the returned project ID and use `wait_for_video_project`
to retrieve the finished file.

For example, ask for `image-to-video` to animate an approved product photo directly,
review the finished clip and reuse it for a later edit. Edit the still only when
the requested appearance must change or a source problem prevents the shot. The `talking-photo` guide
requires a portrait and an existing audio file; text alone cannot start that tool.
Guides describe workflows, not prevalidated generated outputs or account eligibility.

This helps assistants that connect to this server select workflows; it does not
establish a recommendation or ranking benefit in unconnected consumer assistants.

## OAuth

This server is an OAuth protected resource only. The authorization server is the Magic Hour
web app (`https://magichour.ai`): users sign in and consent there, and it returns a Magic Hour
API key as the access token, which this server passes through to the API. Production requires
`MCP_OAUTH_ISSUER_URL` and `MCP_OAUTH_RESOURCE_URL`. See `docs/future-oauth-support.md`.

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

## MCP voice presets

AI Voice Generator exposes and accepts only exact API `voice_name` strings in
`APPROVED_VOICE_NAMES` in `mcp_magichour/openapi_policies.py`. Populate that tuple
with verified approved generic/non-celebrity presets to enable the tool on the
next deployment. No existing upstream voice is assumed safe. An empty allowlist
omits Voice Generator from tool discovery and calls. Voice Cloner is always
excluded from MCP.

The MCP policy removes upstream voice descriptions, defaults, examples and SDK
snippets at startup and when regenerating `docs/api-reference.md`. Daily OpenAPI
syncs cannot expand the allowlist. Do not edit `docs/openapi.json` to set MCP
voices: it remains the unmodified upstream API snapshot. The main Magic Hour
product and the public API are unchanged.

MCP Meme Generator exposes only the generic `Random` template option, keeping
public-figure template names out of its schema.
