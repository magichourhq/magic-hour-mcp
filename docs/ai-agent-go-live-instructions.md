# AI agent integration instructions

Integrate this package into the existing product backend. Follow
`docs/detailed-step-by-step-integration.md` for commands and code.

## Scope

- Use the current MCP package and generated OpenAPI tools.
- Mount `mcp_magichour.server.app` at `/mcp`.
- Merge `mcp_magichour.server.lifespan` into the host lifespan.
- Preserve `Authorization: Bearer <magic_hour_api_key>` through the proxy.
- Reuse the host's logging, rate-limit, request-size, and deployment patterns.
- Never log API keys or raw authorization headers.

Do not build a new chat UI, upload system, auth service, endpoint wrapper, or
marketplace package unless the product owner asks for it.

## Before editing

Inspect the host app's framework, lifespan, auth middleware, proxy config,
dependency management, tests, and existing upload flow. Reuse those patterns.

## Required validation

1. Start the host app with `/mcp/` mounted.
2. Confirm `ping` returns `pong` through MCP Inspector.
3. Call `video_assets_generate_presigned_url` with a valid key.
4. Run one real create-and-wait flow only when credit spending is approved.
5. Confirm a bad key returns `401` without affecting the host app.

Use `exact_download_urls` exactly as returned. Never append `expires_at` or
`download_expiration_metadata` to a signed URL.

## Upload rule

MCP arguments do not carry raw file bytes. Call
`video_assets_generate_presigned_url`, upload bytes outside MCP, then pass the
returned `file_path` to the create tool. The upload tool accepts image, audio,
and video files despite its name.

## Completion report

Report:

- files changed
- mount path and public URL
- lifespan integration
- auth and proxy behavior
- tests run and results
- whether a paid generation ran
- deferred work

Ask before changing auth architecture, tool schemas, billing behavior, public
routes, upload ownership, or marketplace scope.
