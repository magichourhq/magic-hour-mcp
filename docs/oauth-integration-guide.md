# OAuth integration guide

This guide covers the OAuth compatibility layer used by Claude connectors. The
production MCP URL is:

```text
https://mcp.magichour.ai
```

Use that URL as the connector URL. The MCP transport and OAuth routes are served
from the same origin.

## Architecture

Magic Hour MCP uses OAuth Authorization Code with PKCE as a compatibility layer
over its existing API-key authentication:

1. Claude discovers the authorization and token endpoints.
2. `/authorize` asks the user for a Magic Hour API key.
3. The server validates the key against the Magic Hour API with an empty request
   that cannot create a project or spend credits.
4. The server puts the API key in a short-lived, single-use authorization code.
5. `/token` verifies the client, redirect URI, resource, and PKCE verifier, then
   returns the original API key as the bearer access token.
6. Claude sends `Authorization: Bearer <magic_hour_api_key>` to the MCP endpoint.

The server does not mint a second credential or a refresh token. Access-token
lifetime, revocation, permissions, and account scope are therefore those of the
underlying Magic Hour API key.

This is a compatibility server for one known client, not a general-purpose OAuth
provider. Dynamic client registration is not exposed.

## Client and callback allowlist

The fixed client ID is:

```text
magic-hour-mcp
```

The exact callback allowlist is defined by `CLAUDE_REDIRECT_URIS` in
`mcp_magichour/oauth_compat.py`. It currently contains:

```text
https://claude.ai/api/mcp/auth_callback
```

Client IDs or redirect URIs outside this allowlist are rejected before API-key
validation. Add a callback only after verifying an official client URL; do not
allow wildcard or user-supplied redirects.

## Endpoints and discovery

For the production origin, the routes are:

| Route | Purpose |
|---|---|
| `GET/POST https://mcp.magichour.ai/authorize` | Authorization form and submission |
| `POST https://mcp.magichour.ai/token` | Authorization-code exchange |
| `GET https://mcp.magichour.ai/.well-known/oauth-authorization-server` | Authorization-server metadata |
| `GET https://mcp.magichour.ai/.well-known/oauth-protected-resource` | Protected-resource metadata |
| `GET https://mcp.magichour.ai/.well-known/oauth-protected-resource/mcp` | Alternate MCP discovery path |
| `https://mcp.magichour.ai/` | Streamable HTTP MCP endpoint |

An unauthenticated MCP request returns `401` with a `WWW-Authenticate` header
pointing to protected-resource metadata. Metadata advertises Authorization Code,
PKCE `S256`, a public client (`token_endpoint_auth_methods_supported: ["none"]`),
and bearer authentication in the request header.

Route prefixes matter. The repository's standalone app serves the MCP endpoint
at `/`, not `/mcp`. If a host mounts the entire app under a prefix, that prefix
also applies to `/authorize`, `/token`, and both discovery endpoints. Prefer the
dedicated production origin at its root; it keeps advertised and reachable URLs
identical.

## Environment and server configuration

Production configuration:

```sh
MCP_OAUTH_ISSUER_URL=https://mcp.magichour.ai
MCP_OAUTH_RESOURCE_URL=https://mcp.magichour.ai
MAGIC_HOUR_API_BASE_URL=https://api.magichour.ai
MAGIC_HOUR_OPENAPI_PATH=docs/openapi.json
```

`MCP_OAUTH_ISSUER_URL` controls the issuer and advertised OAuth endpoint base.
`MCP_OAUTH_RESOURCE_URL` controls the protected-resource identifier and resource
validation. Set both explicitly in production instead of deriving them from
proxy request headers. Values must use HTTPS; HTTP is accepted only for
`localhost`, `127.0.0.1`, or `::1`.

`MAGIC_HOUR_API_BASE_URL` controls both tool requests and API-key validation.
`MAGIC_HOUR_OAUTH_VALIDATION_PATH` optionally overrides the validation endpoint;
its default is `/v1/ai-image-generator`. Do not change it unless the upstream API
route changes: the validator intentionally treats that endpoint's authenticated
`400` response as success and `401`, `403`, or `404` as an invalid key.

Run the app with its lifespan enabled. `python main.py` already does this. If the
app is embedded in another ASGI service, preserve the exported app's lifespan as
described in the [integration handoff](../integration-handoff.md).

### Vercel Deployment Protection

Claude must reach the MCP endpoint, discovery documents, authorization form, and
token endpoint without first authenticating to Vercel. Vercel Deployment
Protection in front of these routes can return a Vercel login/challenge instead
of the OAuth response and break discovery or token exchange. Disable Deployment
Protection for the production MCP domain or exempt all connector routes before
testing OAuth. An application-level API key or OAuth prompt is not a bypass for
Vercel's edge protection.

### Single-worker limitation

Authorization codes are stored in process memory. They expire after five
minutes, are single-use, and disappear on restart or deployment. Run one worker
and avoid load balancing the authorization and token requests across instances.
Horizontal scaling requires replacing the process-local store with a shared,
atomic, expiring code store before adding workers.

## Test locally

Local tests use direct bearer authentication. They verify the same access token
that OAuth ultimately returns without requiring Claude to reach localhost.

```sh
python main.py
```

Check discovery:

```sh
curl http://127.0.0.1:8000/.well-known/oauth-authorization-server
curl http://127.0.0.1:8000/.well-known/oauth-protected-resource
curl -i http://127.0.0.1:8000/
```

The last command should return `401` and a `WWW-Authenticate` discovery link.

### MCP Inspector

```sh
npx @modelcontextprotocol/inspector
```

Use:

- Transport: `Streamable HTTP`
- URL: `http://127.0.0.1:8000/`
- Header: `Authorization: Bearer <magic_hour_api_key>`

Call `ping`, then an authenticated tool if needed. Real generation calls can
spend credits.

### Claude Code

```sh
claude mcp add --scope project --transport http magic-hour http://127.0.0.1:8000/ --header "Authorization: Bearer API_KEY"
```

Start a new Claude Code session and call `ping`. This tests local MCP and bearer
passthrough, not the browser OAuth redirect.

To test the full Claude connector flow, deploy a publicly reachable HTTPS
environment, set its issuer and resource URLs to that environment's canonical
origin, and configure that origin as the connector URL. Claude cannot complete a
browser callback against a server reachable only at localhost. Production uses
`https://mcp.magichour.ai`.

## Deployment checklist

- Serve the complete OAuth-wrapped app at `https://mcp.magichour.ai/` over HTTPS.
- Set both `MCP_OAUTH_ISSUER_URL` and `MCP_OAUTH_RESOURCE_URL` to that exact origin.
- Keep OAuth, discovery, and MCP routes outside Vercel Deployment Protection.
- Preserve `Host`, scheme/forwarding information, and `Authorization` through the
  CDN, proxy, and application stack.
- Run one application worker until authorization codes use a shared store.
- Verify both discovery documents return application JSON and production URLs.
- Verify an unauthenticated MCP request returns the OAuth bearer challenge.
- Complete a Claude authorization and call `ping`.
- Apply edge rate limits to `/authorize` and `/token` without logging form bodies.
- Confirm logs, traces, error reporting, and proxy access logs redact credentials.

## Troubleshooting

### Discovery or connector URL returns 404

- Confirm the connector URL is `https://mcp.magichour.ai`, not a stale `/mcp`
  path. The dedicated production app serves MCP at `/`.
- Request both well-known URLs directly and inspect the response before testing a
  client.
- If the app was mounted under a prefix, every OAuth route is under that prefix;
  either remove the prefix or make issuer, resource, routing, and connector URL
  consistently use it.
- Check that the proxy forwards well-known paths rather than routing them to a
  frontend or a generic 404 handler.

### Discovery returns HTML, a redirect, or a Vercel page

Deployment Protection or another edge authentication layer is intercepting the
request. Expose the connector routes as described above. Discovery endpoints
must return their JSON directly.

### Token exchange returns `invalid_grant`

The server logs a safe reason as `token_rejected reason=<reason>` without logging
the code, verifier, or API key. Common causes:

- `code_invalid_or_expired`: code is older than five minutes, the process
  restarted, or `/authorize` and `/token` reached different workers.
- `code_already_consumed`: client retried a completed exchange.
- `client_mismatch` or `redirect_uri_mismatch`: exchange values differ from the
  authorization request.
- `resource_mismatch`: issuer/resource configuration or client resource changed
  between requests.
- `pkce_failed`: verifier is missing, malformed, or does not match the S256
  challenge.

Start a new authorization after correcting the cause; never attempt to reuse a
code.

### API key is rejected on the authorization form

Confirm `MAGIC_HOUR_API_BASE_URL` reaches the real expected API and the default
validation path has not become stale. A proxy-generated `404` from the validation
request is treated as an invalid key. Upstream timeouts or server errors produce
`Could not validate API key. Try again.` rather than accepting the key.

### Advertised URLs have HTTP or an internal hostname

Set the two `MCP_OAUTH_*` URLs explicitly. Also configure the proxy to preserve
the original host and HTTPS scheme and configure the ASGI server's trusted proxy
handling. Explicit canonical URLs prevent OAuth metadata from depending on
untrusted or incorrectly forwarded request headers.

### Authenticated MCP calls still return 401

Verify every proxy hop forwards the `Authorization` header unchanged. The OAuth
access token is the Magic Hour API key, so downstream API requests must receive
the same bearer value. Do not replace it with a host application's session token
or JWT.

## Security and logging

- Treat authorization form values, access tokens, and bearer headers as raw
  Magic Hour API keys.
- Never log request bodies for `/authorize` or `/token`, URL query secrets,
  `Authorization` headers, authorization codes, or PKCE verifiers.
- Keep TLS enabled end to end and do not place secrets in connector URLs.
- Keep the exact redirect allowlist and PKCE `S256` checks; do not add wildcards.
- Keep authorization responses and token responses non-cacheable.
- Rate-limit public OAuth routes and monitor aggregate status/rejection reasons,
  not credential values.
- Rotate a Magic Hour API key if it may have appeared in logs or traces. Because
  OAuth returns that key directly, rotating the API key revokes connector access.
