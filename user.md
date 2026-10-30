# Magic Hour MCP user guide

Use this guide if Magic Hour has a hosted MCP endpoint at:

```text
https://mcp.magichour.ai/
```

You do not need this repo. After setup, ask for an image or video result in plain
English; the assistant handles the tool calls. The hosted server accepts audio
inputs and retrieves existing audio projects, but does not expose voice generation
or voice cloning.

## What you need

- A Magic Hour account for the Claude OAuth connector sign-in flow
- For the Claude Code and Codex CLI bearer-token setup below, a Magic Hour API key
- Claude, Claude Code, or Codex CLI
- Enough credits for the generation you request

Claude OAuth users sign in and select **Allow**; no manual key entry is needed.
Keep API keys private. Real generations spend Magic Hour credits.

## Connect with Claude

1. In Claude, open **Settings > Connectors** and add a custom connector.
2. Enter `https://mcp.magichour.ai/` as the connector URL.
3. Add the connector, then select **Connect**.
4. Sign in to Magic Hour if asked, then select **Allow** on the authorization page.
   This creates an API key named "Claude connector" in your Developer Hub; delete it
   there to disconnect.
5. Return to Claude and verify the connector is enabled. Ask Claude to call
   `get_account_info`; a returned credit balance confirms real API authorization.

## Connect with Claude Code

```sh
claude mcp add --scope user --transport http magic-hour https://mcp.magichour.ai/ --header "Authorization: Bearer YOUR_MAGIC_HOUR_API_KEY"
```

Start a new Claude Code session and ask it to call `get_account_info`.

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

Start Codex from that same shell and ask it to call `get_account_info`.

## Verify API access before generating

Use `get_account_info` to verify API authorization and inspect the account before
generating. It does not validate a particular model, input or setting, or quote
that request's credit cost. Tool discovery can work without credentials, and an
invalid API key can still receive
`pong`; only the account read checks actual API authorization. `Authentication
required` means the bearer token is missing;
an account `401` means authorization failed. Check the key and the environment
that launched the agent before reinstalling anything. Keep account details private.

Before a paid request, choose the model, duration, resolution and audio settings
using the [model directory](https://docs.magichour.ai/api-reference/models) and
[billing guide](https://docs.magichour.ai/billing/overview). The
[workload evaluation guide](https://docs.magichour.ai/integration/evaluating-media-apis)
shows what to record when comparing cost, usable outputs and integration work.
If the available information does not establish the request's cost or access,
resolve that uncertainty before asking the assistant to generate.

Keep the project ID returned by creation. If waiting times out, resume that
project rather than creating and paying for another attempt. Ask for the finished
download and inspect it; a completed status alone does not establish that the
result meets your brief.

## Improve output quality with Magic Hour skills

The MCP gives your agent access to Magic Hour tools. The [Magic Hour skills cookbook](https://github.com/magichourhq/skills) adds workflows and published examples for choosing and sequencing those tools, writing prompts, reviewing outputs, and recovering without duplicate paid jobs. Each example records its validation limits.

Install the cookbook and choose your agent:

```sh
npx skills add magichourhq/skills --skill '*'
```

For product or character animation, use an approved supplied still directly as the image-to-video start frame. Create or edit a still first only when its appearance must change or a source problem prevents the requested shot. Prompt the video step for motion, camera movement, timing, and what must stay fixed.

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

Direct public media URLs can work, but uploaded Magic Hour `file_path` inputs are more reliable.

## Files and download links

Magic Hour returns signed download URLs. Use the full URL exactly as returned.

Do not shorten the URL, remove query parameters, or append `expires_at`.

If a link shows `SignatureDoesNotMatch`, ask the assistant for the exact download URL again.

For inputs, prefer an uploaded file or existing Magic Hour `file_path`. The
upload tool is `get_upload_urls`; it accepts
image, audio, and video files.

## Troubleshooting

- If tools do not appear, restart the client after adding the server.
- If auth fails, verify your API key and the
  `Authorization: Bearer YOUR_MAGIC_HOUR_API_KEY` header.

- If Codex cannot see the server, launch it from the shell where
  `MAGIC_HOUR_API_KEY` is set.
- If the assistant returns only a project `id`, ask for the finished result.
