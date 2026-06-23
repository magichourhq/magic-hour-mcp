# Future: OAuth support for claude.ai web

Not implemented. Researched 2026-06-20 because bearer-passthrough (our current auth model) doesn't cover every Claude surface. Revisit this doc if/when that becomes a real requirement.

## Why this might matter later

Confirmed against Anthropic's own docs/support articles (not guessed):

| Surface | Static bearer token / custom header? |
|---|---|
| Claude Code (CLI, `--header` flag or `.mcp.json` `headers` field) | ✅ Yes — this is what we've been testing with |
| Claude Desktop, added by hand-editing `.mcp.json`/`~/.claude.json` | ✅ Yes — same config files as CLI |
| Claude Desktop's point-and-click **Connectors UI** | ❌ No header field exposed |
| Claude.ai web **Custom Connector** setup | ❌ OAuth only (Advanced settings exposes Client ID/Secret, nothing else) |

So today's design covers developer-facing usage (Claude Code, hand-edited config) but not the polished "click to connect" flow in claude.ai web or Desktop's Connectors UI. Whether that gap matters depends on who actually uses this — developers comfortable with config files, or general users who'd want the one-click flow.

## What the MCP spec requires for OAuth

From `modelcontextprotocol.io`'s authorization spec:

- The MCP server (resource server) **must** implement **OAuth 2.0 Protected Resource Metadata** ([RFC 9728](https://datatracker.ietf.org/doc/html/rfc9728)) at `/.well-known/oauth-protected-resource`, advertising where its authorization server lives.
- The authorization server **must** implement **OAuth 2.1** with **PKCE** (mandatory, prevents auth-code interception).
- Authorization servers and clients **should** support **Dynamic Client Registration** ([RFC 7591](https://datatracker.ietf.org/doc/html/rfc7591)) — this is what lets claude.ai auto-register itself with no manual client ID/secret entry. Without DCR, the user has to manually create an OAuth app and paste a client ID/secret into claude.ai's "Advanced settings" — which is the fallback the existing UI exposes today, but a worse experience.
- Tokens **must** be validated for audience (RFC 8707 resource indicators) — a server must reject tokens not issued for it specifically.

## What the official `mcp` SDK gives us

Two distinct primitives (`mcp.server.auth.provider`), selectable via `FastMCP(auth=AuthSettings(...), auth_server_provider=..., token_verifier=...)`:

- **`TokenVerifier`** — minimal: just `async def verify_token(token) -> AccessToken | None`. Used when an *external* authorization server already exists and we only need to validate the tokens it issues.
- **`OAuthAuthorizationServerProvider`** — the full thing: `register_client` (DCR), `authorize`, `exchange_authorization_code`, `load_access_token`, etc. Used when *we* are the authorization server.

Both are real protocols already present in the installed `mcp` package — no new dependency needed to use either.

## Three implementation paths

### Path 1 — Self-contained authorization server (recommended default)

We implement `OAuthAuthorizationServerProvider` ourselves. The `/authorize` step doesn't need real identity infrastructure — it can just be a form asking the user to paste their Magic Hour API key, and we mint an opaque access token mapped to that key (needs a small persistent store: token → key, e.g. a simple table with expiry). claude.ai auto-discovers us via Protected Resource Metadata and auto-registers via DCR — no manual client ID ever shown to the user.

- **Depends on:** nothing external. Self-sufficient.
- **New work:** `register_client`, `authorize` (+ a basic HTML form), `exchange_authorization_code`, `load_access_token`, a token↔key store.
- **Why default:** doesn't require knowing anything about the startup's infrastructure that we don't already know.

### Path 2 — Resource-server-only, validating the startup's *own* existing auth

We implement only `TokenVerifier`. claude.ai redirects users to log in with their actual startup account; `verify_token` validates that token (e.g. JWT signature check or an introspection call) and looks up the corresponding Magic Hour key from wherever the startup stores per-user keys.

- **Depends on:** the startup already having (or building) a real OAuth/OIDC-compliant authorization server with the right discovery endpoints, *and* exposing an internal way for us to resolve "this validated user → their Magic Hour key."
- **New work on our side:** comparatively little (just `verify_token` + a lookup call).
- **New work on their side:** potentially a lot, if this auth server doesn't already exist.
- **Why consider it:** this is the "real" multi-tenant model or actual per-user accounts, not just "anyone holding a Magic Hour key." It's the per-tenant-lookup idea from the original auth discussion, resurfacing here specifically for the claude.ai-web case — it didn't fit bearer-passthrough, but it does fit this path.

### Path 3 — OAuth proxy in front of a third-party IdP (Auth0, WorkOS, GitHub, etc.)

If the startup has no IdP of their own and doesn't want to build one. Our server proxies the OAuth dance to an off-the-shelf identity provider. The standalone `fastmcp` package (evaluated and declined earlier — see `reference_fastmcp_packages` memory) has pre-built one-liner provider classes for several of these (`GitHubProvider`, etc.) that would meaningfully cut code here — this is the one place where that package's auth utilities would actually pay for themselves, unlike the rest of this project's scope.

- **Depends on:** picking and paying for a third-party IdP.
- **New work:** still need the token→Magic-Hour-key lookup (same problem as Path 2), plus IdP integration (less if using `fastmcp`'s pre-built classes, more if hand-rolled against the official SDK's `OAuthAuthorizationServerProvider`).

## Open question before starting any of this

Does the startup already have an OAuth-capable login system of their own? That fact alone decides between Path 1 (no, build it self-contained) and Path 2 (yes, just validate against it). Path 3 only makes sense if the answer is "no, and we don't want to build one either."
