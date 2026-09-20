# OAuth for web connectors

## Current support

| Surface | Static bearer token or custom header? |
|---|---|
| Claude Code CLI | Yes |
| Hand-edited Claude Desktop config | Yes |
| Claude Desktop Connectors UI | Yes, through OAuth |
| claude.ai web Custom Connector | Yes, through OAuth |
| ChatGPT Custom Connector | Yes, through OAuth |
| Cursor MCP | Yes, through OAuth |

OAuth clients are sent to the Magic Hour web app, which is the authorization
server (`https://magichour.ai/.well-known/oauth-authorization-server`). It
handles dynamic client registration, sign-in, consent, and Authorization Code +
PKCE. This server only publishes protected resource metadata and challenges
unauthenticated requests (`mcp_magichour/oauth_compat.py`).

## Why OAuth exists

This server uses bearer passthrough:

```text
Authorization: Bearer <magic_hour_api_key>
```

Claude and ChatGPT web connectors expect OAuth, not an arbitrary static bearer header.

The web app returns a newly created Magic Hour API key as the OAuth access
token, so OAuth clients and static-bearer clients authenticate the same way.
Users revoke a connection by deleting that key in the Developer Hub. There are
no refresh tokens.

## Deployment

Set `MCP_OAUTH_ISSUER_URL` and `MCP_OAUTH_RESOURCE_URL` to the canonical public
MCP URL. The web app only issues codes for `resource=https://mcp.magichour.ai`,
so OAuth cannot complete against preview deployment URLs. This server holds no
OAuth state, so any number of workers or serverless instances is fine.

OAuth does not handle file uploads. See `docs/future-chat-ui-handoff.md` for the
browser upload flow.
