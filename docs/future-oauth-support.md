# OAuth compatibility for web connectors

Implemented as a small compatibility shim in `mcp_magichour/oauth_compat.py`.

## Current support

| Surface | Static bearer token or custom header? |
|---|---|
| Claude Code CLI | Yes |
| Hand-edited Claude Desktop config | Yes |
| Claude Desktop Connectors UI | Yes, through OAuth |
| claude.ai web Custom Connector | Yes, through OAuth |
| ChatGPT Custom Connector | Yes, through OAuth |
| Cursor MCP | Yes, through OAuth |

The OAuth shim supports Authorization Code + PKCE for public clients. Its RFC
7591-compatible `POST /register` endpoint validates callback metadata and
returns a client ID without keeping a client registry. Authorization requests
require an exact match from the built-in callback allowlist and bind it to the
short-lived code.

## Why OAuth exists

This server uses bearer passthrough:

```text
Authorization: Bearer <magic_hour_api_key>
```

Claude and ChatGPT web connectors expect OAuth, not an arbitrary static bearer header.

The authorization page asks for a Magic Hour API key. The server validates it
against the existing API, stores it in a short-lived single-use authorization
code, then returns that same key from `/token` as the bearer access token. It
does not mint refresh tokens or introduce another token system.

## Deployment

Set `MCP_OAUTH_ISSUER_URL` and `MCP_OAUTH_RESOURCE_URL` to the canonical public
MCP URL. Serve production endpoints over HTTPS. Authorization codes are
process-local, so run one worker. Multi-worker or serverless deployment requires
a shared code store or encrypted stateless codes. Rate-limit `/register` and
`/authorize` at the public edge.

## Measuring code lookup failures

When PostHog is configured, OAuth emits `oauth_authorization_code_issued`,
`oauth_authorization_code_lookup_missed`, and the existing
`oauth_connection_completed` event. Match them by `authorization_code_hash`
(SHA-256). No raw authorization codes, API keys, client IDs, redirect URLs, or
PKCE values are included. `code_store_id` identifies the in-memory store and
process; it changes across instances or restarts. OAuth response formats and
authorization codes are unchanged.

For a suspected cross-instance failure, find a lookup miss with a matching
issuance on a different `code_store_id`, within the issuance's
`code_ttl_seconds`, and without a matching completion. Use `occurred_at` (Unix
seconds) for ordering: events are captured after responses and may arrive out
of order. Count distinct code hashes rather than misses, since retries repeat
the same failure. A completion indicates redemption/replay; a miss after the
TTL indicates expiry. Misses without an issuance are inconclusive.

This is diagnostic evidence, not proof: missing telemetry, clock skew, and
concurrent redemption can affect classification. Capture runs after the OAuth
response in a background thread so PostHog latency does not delay the redirect
or token response. No shared storage is required to collect these events.

This is a connector compatibility layer, not a general-purpose authorization
server. Access tokens retain the lifetime and privileges of the Magic Hour API
key.

OAuth does not handle file uploads. See `docs/future-chat-ui-handoff.md` for the
browser upload flow.
