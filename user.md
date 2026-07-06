# Magic Hour MCP User Guide

Use this guide if Magic Hour has a hosted MCP endpoint at:

```text
https://magichour.ai/mcp/
```

You do not need this repo.

## What You Need

- A Magic Hour API key
- Claude Code or Codex CLI

Keep your API key private. Real generations can spend Magic Hour credits.

## What Is MCP?

MCP lets an AI assistant use Magic Hour tools. After setup, you can ask for images, videos, or audio in plain English, and the assistant handles the tool calls behind the scenes.

Use Magic Hour MCP when you want to create content from your AI assistant instead of opening the Magic Hour website manually.

## Connect With Claude Code

```sh
claude mcp add --scope user --transport http magic-hour https://magichour.ai/mcp/ --header "Authorization: Bearer YOUR_MAGIC_HOUR_API_KEY"
```

Then start a new Claude Code session and test:

```text
Call the magic-hour ping tool.
```

Expected result:

```text
pong
```

If `--scope user` is not supported, use project scope:

```sh
claude mcp add --scope project --transport http magic-hour https://magichour.ai/mcp/ --header "Authorization: Bearer YOUR_MAGIC_HOUR_API_KEY"
```

## Connect With Codex CLI

Set your API key in the same shell where you will launch Codex.

macOS/Linux:

```sh
export MAGIC_HOUR_API_KEY="YOUR_MAGIC_HOUR_API_KEY"
```

PowerShell:

```powershell
$env:MAGIC_HOUR_API_KEY = "YOUR_MAGIC_HOUR_API_KEY"
```

Add the MCP server:

```sh
codex mcp add magic-hour --url https://magichour.ai/mcp/ --bearer-token-env-var MAGIC_HOUR_API_KEY
```

Verify it:

```sh
codex mcp get magic-hour --json
```

Start Codex from that same shell and test:

```text
Call the magic-hour ping tool.
```

Expected result:

```text
pong
```

## What To Ask

Ask naturally. You do not need to mention MCP tools.

Examples:

```text
Create one square anime-style image of a ramen shop at night.
```

```text
Generate a 5 second 16:9 video of a dragon flying over snowy mountains.
```

```text
Generate audio saying "Believe it!" with the Naruto Uzumaki voice if available.
```

```text
Remove the background from my uploaded image.
```

```text
Upscale my uploaded image 2x.
```

```text
Turn my uploaded image into a 5 second video with subtle camera movement.
```

## Common Recipes

| Goal | Example prompt |
|---|---|
| Create an image | `Create a cinematic image of a futuristic cafe in Tokyo at night.` |
| Create a video | `Create a 5 second video of a robot walking through a neon city.` |
| Create voice audio | `Create audio saying "Welcome to Magic Hour" with a warm narrator voice.` |
| Image to video | `Turn my uploaded image into a 5 second video with gentle motion.` |
| Remove background | `Remove the background from my uploaded image.` |
| Upscale image | `Upscale my uploaded image 2x.` |

For file-based workflows, an uploaded Magic Hour `file_path` is most reliable. Direct public media URLs can work, but hotlinked URLs may fail if they do not return stable raw file bytes.

## Download Links

Magic Hour returns signed download URLs. Use the full URL exactly as returned.

Do not:

- Shorten the URL
- Remove query parameters
- Append `expires_at`

If a link shows `SignatureDoesNotMatch`, ask the assistant for the exact download URL again.

## Upload Notes

Some tools need an input file. The most reliable inputs are:

- A file uploaded through the upload flow
- An existing Magic Hour `file_path`

Direct public media URLs can work when they are stable and fetchable, but treat them as best-effort.

The upload URL tool is named:

```text
videoAssets_generatePresignedUrl
```

The name is confusing, but it works for image, audio, and video files.

## Troubleshooting

If tools do not appear, restart Claude Code or Codex after adding the MCP server.

If auth fails, check that your API key is correct and that the header is:

```text
Authorization: Bearer YOUR_MAGIC_HOUR_API_KEY
```

If Codex cannot see the server, launch Codex from the same shell where `MAGIC_HOUR_API_KEY` is set.

If the assistant returns only a project `id`, ask for the finished Magic Hour result.
