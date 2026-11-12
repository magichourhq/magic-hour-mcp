# Magic Hour MCP Server

OpenAPI-backed MCP server for Magic Hour image and video generation and editing,
music generation, uploads, completion waiting and retrieval of existing audio projects.

At startup, this server builds MCP tools from the canonical `docs/openapi.json`
with `FastMCP.from_openapi()`. Installed distributions bundle that snapshot inside
the Python package, so startup does not require a repository checkout. The spec
supplies endpoint coverage, while
Magic Hour MCP policies add agent-facing guidance for async polling, uploads,
and project downloads.

## Connect an agent

Hosted creation endpoint: **https://mcp.magichour.ai/**

Follow the [client setup guide](https://docs.magichour.ai/integration/model-context-protocol)
for Claude, Claude Code, Codex CLI or ChatGPT. After connecting, call
`get_account_info` to verify API access and inspect the account before generation;
`ping` alone does not validate the API key.

Try: “Use my portrait to create a cinematic space-pilot still, then animate it
with a slow camera push. Keep my face consistent, wait for the render and show
the finished video. Check account eligibility and cost before spending credits.”

Use `get_workflow_guide` for the ordered tool steps and output review.
After a tool-name update, refresh the client’s cached tool catalog or reconnect.
Use the names returned by `tools/list`; dated example receipts keep their original IDs.
For documentation search while coding, use the separate
[API Docs MCP](https://docs.magichour.ai/integration/api-docs-mcp).

Docs:

- [Magic Hour agent skills](https://github.com/magichourhq/skills) - media workflows, published examples, and recovery guidance
- `user.md` - hosted endpoint user guide
- `integration-handoff.md` - FastAPI mount checklist
- `docs/detailed-step-by-step-integration.md` - full backend integration guide
- `docs/api-reference.md` - generated API reference

## Evaluate a paid workflow

Use the [workload evaluation guide](https://docs.magichour.ai/integration/evaluating-media-apis)
to compare a finished image or video job, including its inputs, model, settings,
output and cost. Use the Markdown tables for [model limits and credit costs](https://docs.magichour.ai/api-reference/models.md)
and [billing options](https://docs.magichour.ai/billing/overview.md). The
[Kling 3.0 annual example](https://docs.magichour.ai/billing/subscription-pricing.md#kling-30-annual-pricing-example)
shows the upfront payment and effective cost when all credits are used.
Audio, higher resolutions, and video-reference inputs change credit usage.

The account read verifies API authorization and returns account information; it
is not a request-specific price or eligibility quote. The current MCP tool
contract has no such quote operation. A workflow guide does not validate the
requested model, source media or plan access. Resolve unclear costs or access
before creating a paid project.

For inspectable video examples, the [published image-to-video benchmark](https://github.com/magichourhq/ai-video-model-benchmark)
provides source images, requests, all attempts and downloadable results. It
compares models through Magic Hour and does not establish a provider-quality
winner or human acceptance of the clips.

## Upload files from a local agent

If your agent can run Python where the file is stored, call
`client.v1.files.upload_file("path/to/file.png")` from the
[Magic Hour Python SDK](https://docs.magichour.ai/integration/inputs-and-outputs#sdk-upload-simplified).
Use a client authenticated for the same Magic Hour account as the MCP connection.
Pass the returned `file_path` to the generation tool and save it for reuse.
Hosted MCP clients can use native attachment tools where available, or request a
presigned URL with `get_upload_urls` and upload the file bytes from the client.

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

Credential-free client connection configs are also public at `/mcp.json` and
`/.well-known/mcp.json`. These contain only the hosted endpoint, never an API key.
Tool calls still require OAuth or a bearer API key. The handshake advertises the
setup documentation and a packaged 256px icon; the server card also supplies its
human-readable title and description.

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

`get_workflow_guide` accepts `video-face-swap`, `image-editor`, `image-to-video`,
`video-generator`, `lip-sync`, `ugc-ads`, `talking-photo` or `media-apis`. It returns
inputs, product and API documentation URLs, exact creation-tool names, ordered
steps, example requests, follow-up edits and review criteria. It makes no API
request, spends no credits and does not verify account access. Like other tools, it requires the existing authentication flow.

Clients can also discover the public `magic-hour://workflows/index` resource and
read `magic-hour://workflows/{workflow}`. UGC uses `create_spoken_ad_video` with a
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
5. Call `get_upload_urls` or another generated tool.

Notes:

- FastMCP generates endpoint tools from OpenAPI at startup.
- Creation tools return `id` and the initial `credits_charged`. Record them together,
  then reconcile that entry with `credits_charged` from the terminal project
  response when available.
- OpenAPI `operationId` values are normalized to descriptive snake_case tool names.
- The shared `/v1/files/upload-urls` endpoint is named `get_upload_urls`. It accepts `video`, `audio`, and `image` items.
- Use `wait_for_*_project` to poll jobs. Use `exact_download_urls` exactly as
  returned; never append expiration metadata.
- Image and audio wait tools also return inline media when supported.

Rebuild and type-check the MCP App UI with `cd web && npm ci && npm run build`.

### Result delivery metrics

`media_project_resolved` includes `project_id`, `project_type`, `status` and
`download_count`. Count distinct projects with status `complete` and at least one
download to measure completed-project retrievals. Repeated reads remain raw
events; older events without a project ID cannot be deduplicated or backfilled.

The result card reports `mcp_app_result` events: `preview_loaded` when the main
media loads, `playback_started` when playback actually starts, and
`download_requested` when Download is clicked. Properties contain only the
project ID, media type and SHA-256 output-path hash. No prompts, filenames,
raw paths, signed URLs or credentials are sent. The widget suppresses repeat
events within its lifetime; reports must also deduplicate by
`(event, project_id, project_type, output_id)` across reloads and cards.

UI reports are anonymous and can be spoofed. Reconcile project IDs against
authenticated completion events and report unmatched coverage separately.
Loading does not prove visibility or quality; a download request does not prove
host acceptance or a completed download. These events do not establish unique
customers, public-plugin installs or paid activation.

## File uploads

Magic Hour does not accept raw file bytes inside tool arguments. The flow is:

1. Call the generated shared upload-URL tool, `get_upload_urls`
2. Upload the file bytes to the returned `upload_url`
3. Pass the returned `file_path` into the generated creation tool

Direct public media URLs may work, but uploaded `file_path` values are more
reliable. Upload bytes from the caller or a dedicated upload bridge; the hosted
MCP server never reads caller-supplied local filesystem paths. Browser chat needs
a separate upload UI or bridge; see `docs/future-chat-ui-handoff.md`.

## MCP voice and meme tools

Voice Generator uses the voice presets in the Magic Hour API schema. Voice Cloner
takes an audio sample and text. Use your own voice or a voice you have permission
to clone. Meme Generator accepts the templates listed in its tool schema.
