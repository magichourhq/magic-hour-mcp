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

The OAuth shim supports Authorization Code + PKCE for one fixed client. Client
ID `magic-hour-mcp` is built in. Exact callback URLs live in the
`ALLOWED_REDIRECT_URIS` allowlist.

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
process-local, so run one worker. Multi-worker deployment requires a shared
store. Rate-limit `/authorize` at the public edge.

This is a connector compatibility layer, not a general-purpose authorization
server. Access tokens retain the lifetime and privileges of the Magic Hour API
key.

OAuth does not handle file uploads. See `docs/future-chat-ui-handoff.md` for the
browser upload flow.
