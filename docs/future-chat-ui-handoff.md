# Web chat upload handoff

Use this only when adding file-based Magic Hour tools to a web chat client.
The MCP server already accepts Magic Hour `file_path` values but does not
provide a browser upload UI or upload bridge.

## Required flow

1. The user selects a file in the chat UI.
2. The app gets a presigned URL from `video_assets_generate_presigned_url`.
3. The browser or backend uploads the bytes.
4. The app passes the returned `file_path` to the create tool.
5. The chat resumes with the project ID and polls through `wait_for_*_project`.

`video_assets_generate_presigned_url` accepts image, audio, and
video files.

## Choose upload ownership

Prefer a backend bridge when the product needs consistent validation, audit
logs, or support across multiple chat clients. Direct browser upload is smaller
when the client can safely own progress and retries.

A backend bridge needs one endpoint:

```text
POST /api/magic-hour/uploads
```

It should validate the file, upload it, and return the Magic Hour `file_path`.
Reuse an existing product upload path when possible.

## Security

- Restrict file size and media types.
- Never expose the Magic Hour API key to browser code.
- Treat user-provided URLs as untrusted and block private-network targets.
- Set fetch timeouts and redirect limits.
- Reject HTML, auth pages, and unsupported content types.
- Define retention and cleanup if the product stores files.

The MCP server needs no schema changes for this flow.
