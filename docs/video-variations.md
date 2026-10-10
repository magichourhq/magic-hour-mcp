# Budgeted product-photo video variations

This extends the existing hosted MCP server with a quote → authorize → execute →
poll workflow. No separate server, generation engine, dollar conversion, cropping
endpoint, or automatic credit purchase is introduced.

## Deployment prerequisite

Set `MCP_WORKFLOW_SIGNING_SECRET` to a server-only random secret of at least
32 bytes, stable across hosted instances. Never expose it to a client. Plans are
signed with this secret and bound to the authenticated API-key identity; a key
holder cannot rewrite a quote or authorize a different account's plan. Plans
expire after 24 hours. Secret rotation invalidates old plans; inspect saved
project IDs through existing project tools before requesting replacement work.

Deploy the matching API support before enabling paid use. `GET /v1/account` must
advertise `capabilities.image_to_video_budget_v1: true`. The existing
`POST /v1/image-to-video` then supports `dry_run`, `max_credits`, and
`idempotency_key` inside a `budgeted_request` envelope. Older API deployments
reject that envelope because it lacks the legacy top-level required fields;
they cannot silently discard `dry_run` and charge. The MCP also refuses submission
until the capability is advertised. A PR or passing
test alone does not prove this capability is deployed.

For production rollout, update and verify the async completion worker and existing
watchdog first, then the API, then configure the hosted MCP signing secret. All
worker instances must honor the stored `approved_credits` ceiling before the API
advertises readiness or accepts budgeted jobs. Do not enable the MCP against a
mixed worker rollout. Verify the quote, repeated submission, terminal URLs, and
net credit ledger through the real client after deployment; enabling configuration
alone is not an end-to-end pass.

## Tools

| Tool | Inputs and behavior |
| --- | --- |
| `plan_video_variations` | Image `file_path`, creative instructions, variation count, aspect ratio, duration, maximum credits, optional exact model/resolution and variation prompts. Quotes compatible supported models without generating videos. Returns `plan_token`, workflow ID, estimate, cap, variation metadata, and recovery instructions. |
| `execute_video_variations` | Saved `plan_token` and explicitly approved credit cap. Submits the quoted jobs using stable upstream idempotency keys. Reuse the same token after an interrupted submission. |
| `video_variations_status` | Saved plan token. Returns each job's status, completed URLs, generation metadata, and next action. Poll after reconnecting; do not submit a new plan to check progress. |

Budgets are **Magic Hour credits**, not USD. Ask users for a credit cap when they
provide a currency budget; no account-specific dollar exchange rate is assumed.
The cap covers this workflow, not unrelated activity on the account.

Image-to-video inherits the source image's aspect ratio. Supply an image already
cropped to the requested ratio; the API verifies its actual dimensions.
`aspect_ratio="input"` preserves the source. This workflow does not crop images or
guarantee perfect product/logo preservation or objectively better creative output.

Upload local photos through existing `video_assets_generate_presigned_url` with
`{"items":[{"type":"image","extension":"png"}]}`; PUT the bytes to its
`upload_url`, then pass its `file_path`. The hosted server cannot read a caller's
local filesystem. Chat attachments require the client's upload bridge; attachment
access is not implied by enabling MCP. Explicit ratios require owned uploaded
images so the backend can inspect dimensions; direct public URLs use
`aspect_ratio="input"` and must meet API input rules.

## Real MCP client demonstration

Use the repository's installed Python dependencies on a remote validation host.
The hosted endpoint is `https://mcp.magichour.ai/`; use `--endpoint` for a preview.
Set `MAGIC_HOUR_API_KEY` through your secret manager/environment; never include
it in command arguments, transcripts, or committed files.

```sh
python examples/video_variations.py --image-file-path "$UPLOADED_IMAGE_FILE_PATH" \
  --aspect-ratio 9:16 --max-credits 1000 --ledger /tmp/videos.private.json
```

Review the actual quote, model, resolution, prompts, duration, and cap. This
planning command does not generate videos. To approve the saved quote:

```sh
python examples/video_variations.py --resume --execute --approved-max-credits "$QUOTED_MAXIMUM_CREDITS" \
  --ledger /tmp/videos.private.json
```

To recover/poll without submitting additional jobs:

```sh
python examples/video_variations.py --resume --ledger /tmp/videos.private.json
```

The demo uses a real `fastmcp.Client`, real tool discovery/schema enforcement,
and a fresh connection for every call. Its private ledger contains the plan and
job identifiers; do not commit or share it. It is a scripted client demonstration,
not proof that a natural-language agent selected tools correctly. Completed state
requires real returned video URLs; queued jobs are not a completed demonstration.

## Natural-language agent evaluation

Connect this endpoint to Codex or a supported remote-MCP client, then request:

> Take this product photo and generate three different short vertical videos. Keep the total cost under my specified budget.

Provide the uploaded 9:16 image path and an explicit credit budget. Verify the
agent discovers `plan_video_variations`, presents the quote, obtains paid approval,
calls `execute_video_variations` once, polls `video_variations_status` after reconnecting,
then compares each result using its prompt/model/duration and exact returned URL.
Record actual tool selections and outcomes; do not label scripted calls an agent run.
Also exercise unsupported ratios/models, insufficient credits, denied approval,
expired plans, interrupted submission, failures, and cross-account access.

## Measurement

Existing `tools/list` discovery and tool-invocation instrumentation covers the
three workflow tools. `video_workflow_planned` and `video_workflow_observed` add
workflow ID and authenticated account ID without prompts, media URLs, or tokens.
Observed events are cumulative snapshots: deduplicate by workflow ID and status;
never sum repeated polling events as charges or revenue. Compare distinct
workflows per account for repeat usage; a quote alone is not activation.

Persisted video metadata links `agent_workflow_id`, project ID, and account ID.
Backend `agent-video-generation-result` logs committed terminal outcomes and net
credits; `agent-video-usage-metered` links successfully reported usage to its
native Stripe meter event identifier. Deduplicate by project ID, then join those
identifiers to billing records/invoices for attributable metered revenue.
Subscription or prepaid-credit consumption is usage attribution, not new cash
revenue. Settled revenue requires invoice/payment evidence; failed/refunded jobs
must not count as successful billable generation.

## Distribution and approval

Current [OpenAI plugin requirements](https://developers.openai.com/plugins/plugin-guidelines)
require accurate tool descriptions/annotations, disclosed data practices, and
server-side safeguards. Existing paid entitlements are supported; digital credit
sales, checkout, subscriptions, and upsells inside the integration are not.
[Public submission](https://developers.openai.com/plugins/deploy/submission)
needs publisher/domain verification, permissions, reviewer access, and approval;
the portal does not currently complete API-key authentication setup.
OAuth discovery/PKCE/audience binding must be independently validated against
[official authentication requirements](https://developers.openai.com/plugins/build/auth).
No public listing or OAuth readiness is claimed by these workflow changes.

The repository already contains `server.json` for the hosted Streamable HTTP
endpoint and a manual `publish-mcp-registry.yml` workflow using GitHub OIDC.
Keep that distribution route; publish metadata/version changes only after the
matching deployment is verified and the account owner approves publication.

[Claude custom connectors](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)
can connect supported remote servers. Its current
[directory policy](https://support.claude.com/en/articles/13145358-anthropic-software-directory-policy)
excludes standalone AI video generation without written permission. An approved
exception is required before claiming eligibility for Claude's public directory.
