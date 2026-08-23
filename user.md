# Magic Hour MCP user guide

Use this guide if Magic Hour has a hosted MCP endpoint at:

```text
https://mcp.magichour.ai/
```

You do not need this repo. After setup, ask for an image, video, or audio result
in plain English; the assistant handles the tool calls.

## What you need

- A Magic Hour API key
- Claude, Claude Code, or Codex CLI

Keep your API key private. Real generations can spend Magic Hour credits.

## Connect with Claude

1. In Claude, open **Settings > Connectors** and add a custom connector.
2. Enter `https://mcp.magichour.ai/` as the connector URL.
3. Open **Advanced settings** and set **OAuth Client ID** to
   `magic-hour-mcp`. This is required because the server does not support dynamic
   client registration.
4. Add the connector, then select **Connect**.
5. On the Magic Hour authorization page, paste your Magic Hour API key and select
   **Connect**.
6. Return to Claude and verify the connector is enabled. Ask Claude to call the
   Magic Hour `ping` tool; the expected result is `pong`.

## Connect with Claude Code

```sh
claude mcp add --scope user --transport http magic-hour https://mcp.magichour.ai/ --header "Authorization: Bearer YOUR_MAGIC_HOUR_API_KEY"
```

Start a new Claude Code session and ask it to call the Magic Hour `ping` tool.

If `--scope user` is not supported, use project scope:

```sh
claude mcp add --scope project --transport http magic-hour https://mcp.magichour.ai/ --header "Authorization: Bearer YOUR_MAGIC_HOUR_API_KEY"
```

## Connect with Codex CLI

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
codex mcp add magic-hour --url https://mcp.magichour.ai/ --bearer-token-env-var MAGIC_HOUR_API_KEY
```

Start Codex from that same shell and ask it to call the Magic Hour `ping` tool.

## Prompt cookbook

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

## Files and download links

Magic Hour returns signed download URLs. Use the full URL exactly as returned.

Do not shorten the URL, remove query parameters, or append `expires_at`.

If a link shows `SignatureDoesNotMatch`, ask the assistant for the exact download URL again.

For inputs, prefer an uploaded file or existing Magic Hour `file_path`. The
upload tool is `video_assets_generate_presigned_url`; it accepts
image, audio, and video files.

## Troubleshooting

- If tools do not appear, restart the client after adding the server.
- If auth fails, verify your API key and the
  `Authorization: Bearer YOUR_MAGIC_HOUR_API_KEY` header.

- If Codex cannot see the server, launch it from the shell where
  `MAGIC_HOUR_API_KEY` is set.
- If the assistant returns only a project `id`, ask for the finished result.
