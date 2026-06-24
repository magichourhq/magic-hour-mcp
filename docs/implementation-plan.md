# Magic Hour MCP Server - Implementation Plan

This file is now a concise current-state handoff. Use `docs/api-reference.md` for full endpoint details.

## Current state

As of 2026-06-24:

- Core build is done
- Hardening is done
- Integration handoff docs are done
- OAuth for claude.ai web is still deferred

## What this server is

A FastMCP server for Magic Hour image, video, and audio generation, designed to be mounted at `/mcp` inside an existing FastAPI app.

Current tool count:

- 34 Magic Hour API operations
- `ping`
- `list_ai_voice_presets`

Total: 36 tools

## Locked decisions

- Auth model: bearer passthrough. `/mcp` reads `Authorization: Bearer <magic_hour_api_key>` and uses that key directly.
- Runtime: Python, FastAPI or Starlette host, FastMCP sub-app.
- Async jobs: `create_*` tools return quickly. Clients poll `get_*_project`.
- Files: local file bytes are uploaded outside MCP by using `generate_upload_urls` first.

## Critical integration note

Mounted sub-apps do not run their own lifespan automatically. The host app must merge `mcp_magichour.server.lifespan` into its own lifespan or tool calls will fail.

See `docs/integration-handoff.md` for the exact code.

## Tool surface summary

### Shared project tools

- `get_image_project`, `delete_image_project`
- `get_video_project`, `delete_video_project`
- `get_audio_project`, `delete_audio_project`

### Files tools

- `generate_upload_urls`
- `detect_faces`
- `get_face_detection`

### Image create tools

14 tools in `tools/image_projects.py`

### Video create tools

9 tools in `tools/video_projects.py`

### Audio create tools

2 tools in `tools/audio_projects.py`

### Helper tools

- `ping`
- `list_ai_voice_presets`

## Output behavior

- `get_image_project` returns status, direct download URLs, and inline image bytes
- `get_audio_project` returns status, direct download URLs, and inline audio bytes
- `get_video_project` returns status and direct download URLs

## Voice preset handling

`create_ai_voice_generator` does not inline the full 494-value preset enum in its schema.

Instead:

- `list_ai_voice_presets` lists all presets or filters them with `query`
- voice matching is case-insensitive
- invalid voice names return a short error with close matches

## Hardening shipped in phase 5

- Shared `errors.py` for clearer API and network failures
- Better `401`, `402`, and timeout messages
- Shared HTTP timeout and retry policy
- Safer logging with no raw bearer token logging
- Create tool descriptions now call out `{id, credits_charged}`
- Upload tool description now clearly says it only mints presigned URLs

## Claude Code support

Claude Code is the best current LLM test client for this repo.

Why:

- header-based auth is confirmed there
- it can auto-poll async jobs
- it can upload local files with shell tools after `generate_upload_urls`

Repo-level `CLAUDE.md` tells Claude Code to:

- auto-poll image, audio, and video jobs
- surface download URLs from completed `get_*_project` calls
- use `list_ai_voice_presets` before voice generation when needed

## Integration handoff shipped in phase 6

`docs/integration-handoff.md` now covers:

- FastAPI mount pattern
- required lifespan merge
- auth model
- mock mode guidance
- smoke test steps
- deliberate bad-token check

## Known open items

- Local file uploads still require an out-of-band upload step. Raw chat attachments do not map cleanly to this API.
- Claude Code auth is confirmed. Codex CLI custom-header support is still unverified.
- Inline media rendering is confirmed in MCP Inspector. CLI rendering of inline image or audio blocks depends on the client.
- claude.ai web Custom Connectors still need OAuth support. See `docs/future-oauth-support.md`.
- Whether staging should default to `MAGIC_HOUR_ENVIRONMENT=mock` is still a host-team choice.

## Phase summary

### Phase 0

Done. Server scaffold, shared `FastMCP` instance, `main.py`, and `ping`.

### Phase 1

Done. Bearer auth, client wiring, shared project tools, and Files tools.

### Phase 2

Done. All image create tools.

### Phase 3

Done. All video create tools.

### Phase 4

Done. All audio create tools, inline audio delivery, and voice preset lookup support.

### Phase 5

Done. Error handling, retries and timeouts, logging cleanup, and better tool guidance.

### Phase 6

Done. Integration handoff docs and smoke test flow.

### Phase 7

Deferred. Future improvements only.
