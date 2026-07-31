# OAuth compatibility for Claude

For complete integration, deployment, testing, and troubleshooting instructions,
see the [OAuth integration guide](oauth-integration-guide.md).

Implemented as a small compatibility shim in `mcp_magichour/oauth_compat.py`.

## Current support

| Surface | Static bearer token or custom header? |
|---|---|
| Claude Code CLI | Yes |
| Hand-edited Claude Desktop config | Yes |
| Claude Desktop Connectors UI | Yes, through OAuth |
| claude.ai web Custom Connector | Yes, through OAuth |

The OAuth shim supports Authorization Code + PKCE for one fixed Claude client.
Claude client ID `magic-hour-mcp` is built in. Exact callback URLs live in the
`CLAUDE_REDIRECT_URIS` allowlist.

## Why

This server uses bearer passthrough:

```text
Authorization: Bearer <magic_hour_api_key>
```

claude.ai web Custom Connectors expect OAuth, not an arbitrary static bearer header.

The authorization page asks for a Magic Hour API key. The server validates it
against the existing API, stores it in a short-lived single-use authorization
code, then returns that same key from `/token` as the bearer access token. It
does not mint refresh tokens or introduce another token system.

## Deployment

Set `MCP_OAUTH_ISSUER_URL` and `MCP_OAUTH_RESOURCE_URL` to the canonical public
MCP URL. Serve production endpoints over HTTPS. Authorization codes are
process-local, so run one worker. Multi-worker deployment requires a shared
store. Rate-limit `/authorize` at the public edge.

This is intentionally a Claude compatibility layer, not a general-purpose
authorization server. Access tokens retain the lifetime and privileges of the
underlying Magic Hour API key.

## What the backend team would still need after OAuth

OAuth only solves auth.

If the product later wants web chat or connector style uploads, the team will still need:

- a browser upload UI
- a popup, modal, or embedded upload surface
- a frontend or backend upload bridge
- resume logic so chat continues after upload completes

See `docs/future-chat-ui-handoff.md`.
