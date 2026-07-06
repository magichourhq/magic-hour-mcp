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

## Mini Cookbook

These examples cover the most-used Magic Hour endpoints. For inputs, use uploaded files or existing Magic Hour `file_path` values when possible.

| Endpoint | Example prompt | You provide | You get |
|---|---|---|---|
| Face Swap (image) | `Swap the face from my source image onto the person in my target image.` | Source face image, target image | Edited image |
| Face Swap (video) | `Swap this source face onto the person in this video for the first 10 seconds.` | Source face image, video | Face-swapped video |
| AI Image | `Create a square cinematic image of a neon ramen shop at night.` | Text prompt | Generated image |
| AI Image Editor | `Add stylish sunglasses to my uploaded photo and keep it realistic.` | Image, edit prompt | Edited image |
| Image to Video | `Turn my uploaded image into a 5 second video with gentle camera movement.` | Image, motion prompt | Generated video |
| Photo Editor / Colorizer | `Colorize this old black-and-white photo naturally.` | Photo | Colorized image |
| Talking Photo | `Make this portrait say my uploaded audio in a realistic style.` | Portrait image, audio | Talking photo video |
| Background Remover | `Remove the background from my uploaded product photo.` | Image | Cutout image |
| Face Editor | `Make this portrait smile slightly and look toward the camera.` | Face image, edit request | Edited portrait |
| Head Swap | `Place the head from this photo onto the body in this other photo.` | Head image, body image | Head-swapped image |
| Voice Generator | `Generate audio saying "Welcome to Magic Hour" with a warm narrator voice.` | Script, voice preference | Generated audio |

Direct public media URLs can work, but uploaded Magic Hour `file_path` inputs are more reliable.

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
