# Reliability analytics

Uses existing `POSTHOG_PROJECT_TOKEN` and `POSTHOG_HOST` configuration. Rebuild
the result app when deploying; it sends fixed error codes through `/app/events`
without a browser SDK or PostHog token.

| What to investigate | Event and properties |
| --- | --- |
| Tool popularity, errors, latency | Existing `$mcp_tool_call`: `$mcp_tool_name`, `$mcp_is_error`, `$mcp_duration_ms` |
| Client/session, discovery, resource reads, exceptions | Existing native MCP events: `$mcp_initialize`, `$mcp_tools_list`, `$mcp_resource_read`, `$exception` |
| Rejected registration, authorization, or token exchange | `oauth_request_failed`: `stage`, fixed `reason`, `http_status` |
| Suspected cross-worker OAuth code loss | `oauth_authorization_code_issued`, `oauth_authorization_code_lookup_missed`, `oauth_connection_completed`; see [diagnosis](future-oauth-support.md#measuring-code-lookup-failures) |
| Failed/timed-out jobs or missing outputs | Existing `media_project_resolved`: `project_type`, `status`, added `download_count` |
| Partial inline downloads despite a successful tool result | `media_inline_download_failed`: `project_type`, exception class `error_type`, optional `http_status` |
| Browser bridge, missing/unusable results, previews, host actions, runtime/render errors | `mcp_app_error`: fixed `error` code |

Tool success does not imply project success. Check project `status` and look
for `status=complete` with `download_count=0`. Polling timeout does not stop the
upstream job. Native tool events already cover tool validation, authentication,
and API errors; browser events focus on failures only visible in the UI.

Break down `mcp_app_error` by `error`. These are anonymous failure counts, not
failure rates or unique users. The endpoint accepts only an allowlisted error
code with a bounded body; browser capture omits credentials/referrers and caps
reports at 100 per page. Public telemetry can be spoofed. Delivery is best
effort and does not block interactions.

New custom events omit prompts, keys, raw OAuth codes, project IDs, media URLs,
and free-text errors. Existing native MCP SDK capture is unchanged: sanitized
parameters, responses, intent, and errors can still contain prompts or URL paths.

Initial asset failures, outages before reaching the server, process termination,
and blocked analytics require hosting logs or availability checks. Downloads
accepted by a host cannot prove a file was saved. OAuth/UI captures run after
the HTTP response while the ASGI request remains active.
