# Future: OAuth support for claude.ai web

Not implemented. Keep this doc only if the team later needs claude.ai web or Claude's Connectors UI.

## Current support

| Surface | Static bearer token or custom header? |
|---|---|
| Claude Code CLI | Yes |
| Hand-edited Claude Desktop config | Yes |
| Claude Desktop Connectors UI | No |
| claude.ai web Custom Connector | No. OAuth only |

Today this server fits developer workflows such as Claude Code. It does not fit the one-click web connector flow.

## Why

This server uses bearer passthrough:

```text
Authorization: Bearer <magic_hour_api_key>
```

claude.ai web Custom Connectors expect OAuth, not an arbitrary static bearer header.

## If OAuth becomes necessary

There are three realistic paths:

### 1. Self-contained OAuth server

Build an `OAuthAuthorizationServerProvider` in this service. The auth page could ask the user for a Magic Hour API key, then mint an access token mapped to that key.

Best when:

- the startup has no existing OAuth system
- the goal is a working claude.ai web connector with minimal outside dependencies

### 2. Validate the startup's existing auth

Implement only `TokenVerifier`, validate the startup's own OAuth or OIDC tokens, then map the authenticated user to their Magic Hour key.

Best when:

- the startup already has a real OAuth-capable login system
- they already store or can resolve a per-user Magic Hour key

### 3. Use a third-party IdP

Proxy auth through Auth0, WorkOS, GitHub, or a similar provider, then solve the same user-to-Magic-Hour-key mapping step.

Best when:

- the startup has no IdP
- they do not want to build one

## Open question

Does the startup already have an OAuth-capable login system?

- If yes, path 2 is likely best.
- If no, path 1 is usually the simplest default.
