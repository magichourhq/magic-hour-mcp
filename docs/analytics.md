# User-facing reliability events

Server events use the existing `POSTHOG_PROJECT_TOKEN` and `POSTHOG_HOST`
configuration. The result app sends anonymous, bounded metadata to
`POST /app/events`; the server forwards it to PostHog. No browser SDK or
browser-visible PostHog token is required. Rebuild the app when deploying.

## Coverage

| User action or issue | Event | Useful properties |
| --- | --- | --- |
| Tool usage, validation failures, API errors, polling request failures | `$mcp_tool_call` (existing SDK) | `$mcp_tool_name`, `$mcp_is_error`, `$mcp_duration_ms` |
| Tool discovery and result-app resource retrieval | `$mcp_tools_list`, `$mcp_resources_list`, `$mcp_resource_read` (existing SDK) | Error flag, duration, resource/tool names |
| MCP session/client attribution | `$mcp_initialize` and native MCP events (existing SDK) | `$session_id`, `$mcp_client_name`, `$mcp_client_version`, `$mcp_vendor_client` when available |
| Exceptions during MCP operations | `$exception` (existing SDK) | Exception details |
| HTTP requests challenged before reaching MCP | `mcp_authentication_challenged` | `reason`: `missing_bearer` or `malformed_bearer` |
| Valid connection form served | `oauth_authorization_viewed` | Count of served forms, not unique users |
| Registration, API-key validation, or token exchange rejected | `oauth_request_failed` | `stage`: `register`, `authorize`, `token`; bounded `reason`; `http_status` |
| OAuth code lost between issuance and redemption | `oauth_authorization_code_issued`, `oauth_authorization_code_lookup_missed`, `oauth_connection_completed` | Code hash, store ID, TTL, occurrence time; see [OAuth diagnosis](future-oauth-support.md#measuring-code-lookup-failures) |
| Wait tool returns a final project outcome or polling timeout | `media_project_resolved` (existing, enriched) | `project_type`, `status`, `download_count` |
| Inline media fetch fails while tool still returns useful results | `media_inline_download_failed` | `project_type`, exception class as `error_type`, optional `http_status` |
| Result app starts; bridge connects or fails | `mcp_app_event` | `stage`: `boot` or `bridge`; `outcome`, optional `reason` |
| Tool result missing/failed, canceled, or has unusable download links | `mcp_app_event` | `stage=tool_result`, `outcome`, `reason`, `media_type`, `output_count` |
| Image fails to load; audio/video has network, decode, or unsupported-format errors | `mcp_app_event` | `stage=media_preview`, `outcome`, `reason`, `media_type` |
| Download or fullscreen request accepted/rejected by host | `mcp_app_event` | `stage`: `download` or `fullscreen`; `outcome`, optional `reason` |
| JavaScript error, unhandled promise rejection, or React render error | `mcp_app_event` | `stage`: `runtime` or `render`; fixed `reason` |

Use native `$mcp_tool_call` for tool popularity and failure rates; custom tool-use
events would double-count those calls. Handler success is distinct from project
success: inspect `media_project_resolved.status` for failed or timed-out jobs,
and `status=complete` with `download_count=0` for missing output. Polling timeout
does not mean the upstream job stopped; it can finish later.

OAuth authorization failures distinguish `api_key_missing`, `api_key_rejected`,
`code_capacity`, `validation_capacity`, and `validation_unavailable`. Request
validation uses the fixed OAuth error code. Token failures retain the existing
rejection reasons, including `pkce_failed`, client/resource/redirect mismatches,
and `code_invalid_or_expired`. Missing-bearer challenges can be an expected first
step in OAuth discovery; do not treat every challenge as a broken connection.
The HTTP challenge middleware lets POST/OPTIONS through for public discovery;
tool-call authentication failures appear in native MCP events instead.

## Interpreting UI events

Filter `mcp_app_event` by `outcome=failed` and break down by `stage` and `reason`.
Success events provide context: bridge connected, result delivered, preview
metadata loaded, or host accepted an action. A host accepting a download or link
request does not prove the file was saved. Native anchor downloads have only
`outcome=requested` because the browser exposes no completion signal. Audio/video
metadata loading does not prove uninterrupted playback.

UI payloads allow only fixed enums and an output count from 0 to 1000. The
endpoint caps bodies at 4 KiB and rejects unknown fields. Browser capture sends
no credentials or referrer and caps events at 100 per page to bound error loops.
Events are anonymous counts, not unique-user funnels or authenticated evidence;
the public endpoint can receive spoofed traffic. Apply edge rate limits if abuse
appears. Capture failures are best effort and do not block user interactions.

## Data and limits

New custom events include no prompts, API keys, raw authorization codes, project
IDs, media URLs, client IDs, PKCE values, or free-text error messages. OAuth code
hashes correlate only short-lived code operations. This does **not** change the
existing native MCP SDK's capture: it includes sanitized parameters, responses,
intent, and error details, which can still contain prompts or media URL paths.

Telemetry cannot report code that never runs: blocked or failed initial app
assets, DNS/network outages before reaching the server, process termination,
and blocked analytics delivery need Vercel request/deployment logs or external
availability checks. Browser uploads and playback/download completion outside
this app are outside its visibility. A missing event alone does not prove an
application failure. OAuth and UI captures run after the HTTP response while
the ASGI request remains active; delivery is still best effort.
