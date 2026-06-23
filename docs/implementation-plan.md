# Magic Hour MCP Server — Implementation Plan

Companion to [api-reference.md](api-reference.md) (full auth/endpoint/param reference) and [openapi.json](openapi.json) (raw spec). This doc is the phase-by-phase build plan.

## Handoff summary (as of 2026-06-23)

**What this is:** an MCP server wrapping the Magic Hour AI image/video/audio generation API, built to be mounted at `/mcp` on a startup's existing FastAPI backend. 35 tools total — 1:1 with Magic Hour's 34 raw API operations, plus a `ping` health check.

**Status:** Phases 0-4 complete (scaffolding; auth/client plumbing; all `create_*` tools for image, video, and audio; shared `get_*_project`/`delete_*_project` pairs; the Files group). Phase 5 (hardening), Phase 6 (integration handoff doc), Phase 7 (future/optional) have not been started. Read the phase sections below in order — each records what was actually built/verified, not just planned, including a few real bugs found along the way.

**Don't skip:** the [mounting/lifespan gotcha](#critical-mounting-gotcha-found-and-fixed-while-comparing-against-the-standalone-fastmcp-packages-docs) below — a mounted sub-app's `lifespan` is not run automatically by the host; skipping the merge makes every tool call 500 once this is actually mounted on a host app.

**Known open items — not yet resolved, flagged here so they aren't lost:**
- **Local file uploads.** Magic Hour's `create_*` tools that take `image_file_path`/`audio_file_path`/`video_file_path` only accept a direct URL or a `file_path` token from `generate_upload_urls` — never raw bytes. There is no clean way for a user's drag-and-drop chat attachment (claude.ai web, vanilla Desktop) to reach these tools today; only shell-capable agentic clients (Claude Code, Codex CLI) can bridge this, by calling `generate_upload_urls` then using their own Bash tool to `curl -T <local-file> "<upload_url>"` directly against the presigned URL (bypassing the MCP server and the model's context entirely for the byte transfer), then passing the returned `file_path` into the `create_*` call. This pattern is **not yet written into any tool docstring** — the calling agent currently has to figure it out unprompted. Worth adding explicit guidance to `generate_upload_urls`'s description before handoff.
- **Codex CLI auth support, unverified.** The whole auth model depends on the calling MCP client sending a custom `Authorization: Bearer <key>` header to a remote HTTP server. Confirmed for Claude Code (`--header`/`.mcp.json`) and Claude Desktop (hand-edited config). Not checked for Codex CLI.
- **Terminal rendering of embedded media, unverified.** `get_image_project`/`get_audio_project` embed results as MCP `ImageContent`/`AudioContent` blocks. This is confirmed rendering visually in MCP Inspector (browser GUI) and was confirmed by the user via a real screenshot in that same GUI context. Whether any CLI-based client (Claude Code's terminal, Codex CLI) actually renders these blocks visually rather than just printing raw base64/JSON is unverified — terminal image rendering depends on the specific terminal's protocol support and how the CLI chooses to handle MCP media content.
- **OAuth for claude.ai web's Custom Connector flow.** Researched and fully deferred — bearer-passthrough doesn't work there since it's OAuth-only. Full writeup with three implementation paths: [docs/future-oauth-support.md](future-oauth-support.md).

**Testing:** `README.md` has the up-to-date Inspector-based testing flow. Use `MAGIC_HOUR_ENVIRONMENT=mock` for free instant testing, but note mock-server download URLs are **not** fetchable (403) — verifying the image/audio byte-embedding behavior specifically requires a real API key.

---

## Decisions locked in

| Question | Decision |
|---|---|
| Magic Hour API key sourcing | **Bearer passthrough — mirrors the SDK directly.** The `Authorization: Bearer <magic_hour_api_key>` header sent to `/mcp` *is* the caller's Magic Hour API key. The server does no DB lookup and has no concept of users/tenants — it just reads the header per-request and does `Client(token=<that value>)`, exactly like the SDK's own constructor. This is also the *only* gate on `/mcp` (see note below). |
| Host server stack | **Python (FastAPI/Starlette).** MCP server mounts in-process as an ASGI sub-app at `/mcp` on their existing API server. |
| Tool granularity | **1:1 with the 34 raw API operations.** No consolidation into branching multi-purpose tools. |
| Long-running jobs | **`create_*` + shared `get_*_project` as separate tools.** No internal blocking/polling inside a single tool call. |

**Default assumptions** (not explicitly discussed — flag if wrong):
- All 34 endpoints are in scope, including `Files` (upload URLs, face detection). Face detection in particular is a prerequisite for multi-face `create_face_swap` / `create_face_swap_photo` calls, so it's not optional.
- Webhooks are out of scope for now (agent-driven polling via `get_*_project` covers the need); revisit only if tool-call latency on long video jobs becomes a real problem.
- The MCP server wraps the official `magic_hour` Python SDK (`AsyncClient`) rather than calling `httpx` directly — matches "we just need to instantiate a client."

### Note on what bearer-passthrough means for the host team

Because the Magic Hour key itself is the only credential `/mcp` checks, this route does **not** inherit whatever session/JWT auth protects their other routes — a request can hit `/mcp` without ever touching their existing user-auth system. If they want `/mcp` to also get their normal rate-limiting/abuse-prevention/usage-analytics, that has to be added at their gateway/reverse-proxy in front of `/mcp`, not inside this server. Flag this explicitly during handoff (Phase 6) so it's a conscious choice, not a gap they discover later.

---

## Architecture overview

```
Caller → /mcp route → FastMCP ASGI app (mcp.streamable_http_app())
       → tool fn(ctx: Context) → get_api_key(ctx) reads ctx.request_context.request.headers["authorization"]
       → get_client(ctx) builds magic_hour.AsyncClient(token=key)
       → api.magichour.ai/v1/...
```

No DB, no per-tenant table, no lookup function, and (as it turned out) no custom middleware either — the `mcp` SDK already threads the live Starlette `Request` into every tool call's `Context` for Streamable HTTP, so reading the header is a one-liner inside `auth.py`. The header *is* the credential, same as `Client(token=...)` in the SDK. Missing/malformed header raises before any Magic Hour API call is attempted.

Actual module layout (Phase 0/1 built; create_* tool files land in later phases):

```
mcp_magichour/
  __init__.py
  instance.py             # the single shared FastMCP("magic-hour", streamable_http_path="/") instance
  server.py                # imports tools/* (registers their @mcp.tool() fns), defines `ping`, exposes `app` and `lifespan`
  auth.py                   # get_api_key(ctx) -> str, AuthError
  client.py                  # get_client(ctx) async context manager -> AsyncClient; MAGIC_HOUR_ENVIRONMENT=mock toggle
  tools/
    files.py                 # generate_upload_urls, detect_faces, get_face_detection [done]
    image_projects.py        # get_image_project, delete_image_project [done] + 14 create_* tools [Phase 2]
    video_projects.py        # get_video_project, delete_video_project, 9 create_* tools [done]
    audio_projects.py        # get_audio_project, delete_audio_project, 2 create_* tools [done]
main.py                      # dev entry point (`python main.py`), serves at "/" not "/mcp" (see instance.py note)
requests.http                # manual test requests for every raw Magic Hour endpoint (mock server by default)
.env.example, .gitignore     # MAGIC_HOUR_API_KEY placeholder for requests.http
pyproject.toml
docs/
  api-reference.md
  openapi.json
  implementation-plan.md
```

No `errors.py` for now — see Phase 1 notes on why that was simplified away.

### Critical mounting gotcha (found and fixed while comparing against the standalone `fastmcp` package's docs)

A mounted Starlette/FastAPI sub-app does **not** automatically get its own `lifespan` run by the host — only the top-level app passed to uvicorn gets the ASGI lifespan events. `mcp.streamable_http_app()`'s session manager needs `session_manager.run()` entered via *some* lifespan or every tool call 500s with `RuntimeError: Task group is not initialized`. Verified this empirically: mounting `app` on a bare FastAPI app with no lifespan wiring reproduces the 500 on every call; exporting `lifespan` from `server.py` and merging it into the host's own lifespan (via `AsyncExitStack`) fixes it. **This is required reading for whoever mounts this on the host's FastAPI app** — see Phase 6.

```python
# host app, illustrative
from contextlib import AsyncExitStack, asynccontextmanager
from mcp_magichour.server import app as mcp_app, lifespan as mcp_lifespan

@asynccontextmanager
async def combined_lifespan(app):
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(their_existing_lifespan(app))  # if any
        await stack.enter_async_context(mcp_lifespan(app))
        yield

host_app = FastAPI(lifespan=combined_lifespan)
host_app.mount("/mcp", mcp_app)
```

Tool naming convention: `create_<endpoint-slug>` for every generation endpoint (e.g. `create_ai_image_generator`, `create_face_swap_photo`), `get_<category>_project` / `delete_<category>_project` for the shared per-category pair, and 3 standalone names for the Files group (`generate_upload_urls`, `detect_faces`, `get_face_detection`). Consistent prefixing matters because some MCP hosts flatten tool names from multiple mounted servers into one global list.

---

## Phase 0 — Scaffolding

**Goal:** a runnable, empty MCP server mountable at `/mcp`, with the dev loop in place.

- [x] `pyproject.toml` with deps: `mcp`, `magic_hour`, `pydantic`, `python-dotenv`.
- [x] `mcp_magichour/instance.py`: the single shared `FastMCP("magic-hour", streamable_http_path="/")` instance. Set to `"/"` (not `"/mcp"`) specifically so the host can `app.mount("/mcp", app)` without ending up at `/mcp/mcp` — the standalone dev server therefore serves the MCP endpoint at root `/`, not `/mcp` (documented in `main.py`).
- [x] `mcp_magichour/server.py`: imports the `tools` submodules (registers their `@mcp.tool()` functions via the shared instance), defines `ping`, exposes `app = mcp.streamable_http_app()`.
- [x] `main.py`: thin uvicorn runner (`python main.py`), loads `.env` via `python-dotenv`, independent of the eventual FastAPI host.
- [x] `ping` tool registered.
- [x] Confirmed `AsyncClient(..., environment=Environment.MOCK_SERVER)` end to end through a real MCP client (not just the raw SDK) — see Phase 1 notes, this got proven together with auth rather than separately.

**Definition of done:** ✅ verified with a real `mcp.client.streamable_http` session (not just curl — Streamable HTTP needs a session handshake, so plain curl/`requests.http` can't drive it): `initialize` → `list_tools` returns all 10 registered tools → `call_tool("ping")` succeeds.

---

## Phase 1 — Core plumbing: bearer auth, client wiring, shared project tools, Files tools

**Goal:** prove the full request path end-to-end (auth → client → real API shape → MCP result) on the smallest possible surface before fanning out to 28 more `create_*` tools.

- [x] `auth.py`: turned out simpler than planned — no middleware/contextvar needed. In `mcp` 1.17.0, FastMCP threads the real Starlette `Request` straight into the tool's injected `Context` as `ctx.request_context.request` for Streamable HTTP calls. `get_api_key(ctx)` reads `request.headers["authorization"]` directly, raises `AuthError` (plain `Exception` subclass) if missing/malformed. Any tool that wants the key just takes a `ctx: Context` parameter — FastMCP auto-injects it by type annotation.
- [x] `client.py`: `get_client(ctx)` is an `asynccontextmanager` that resolves the key via `get_api_key`, constructs its own `httpx.AsyncClient` (passed into `AsyncClient(httpx_client=...)`) so the connection is cleanly closed at the end of each tool call rather than reaching into SDK internals to close a client we didn't create. Added a small bonus: `MAGIC_HOUR_ENVIRONMENT=mock` env var flips every call to `Environment.MOCK_SERVER`, so the whole running server (not just `requests.http`) can be pointed at the free mock backend.
- [x] Implemented the 3 shared pairs: `get_image_project`, `delete_image_project`, `get_video_project`, `delete_video_project`, `get_audio_project`, `delete_audio_project`. `delete_*` returns a short confirmation string rather than `None` (nicer to read as a tool result).
- [x] Implemented Files group: `generate_upload_urls`, `detect_faces`, `get_face_detection`. Note: the SDK's wire format for upload items uses the field name `type_` (Python keyword-safe alias for the JSON `type`); our own tool schema exposes the natural name `type` to the calling agent and translates internally.
- [ ] ~~`errors.py`~~ — skipped for now. `magic_hour`'s `ApiError.__str__()` already renders as `status_code: 402, body: {'message': '...'}`, which FastMCP surfaces as the tool's error text automatically (any exception raised in a tool becomes a clean `isError=True` result with that message — confirmed by testing the missing-auth-header path). That's informative enough for Phase 1; revisit a friendlier 402-specific message in Phase 5 if it turns out to matter in practice.
- [x] Tool docstrings kept to 1-3 lines each — full param/enum/pricing detail stays in `api-reference.md`, not duplicated into the live tool descriptions.

**Definition of done:** ✅ verified against `MOCK_SERVER` through a real MCP client session (not a raw SDK script — this also exercised the Context/auth wiring, which a bare SDK call wouldn't): `get_image_project` and `generate_upload_urls` both return full structured responses, and calling a tool with no `Authorization` header returns a clean `isError=True` result instead of crashing.

---

## Phase 2 — Image Projects tools (16 total: 14 create + get/delete already done in Phase 1)

| Tool | Endpoint | Notes |
|---|---|---|
| `create_ai_image_generator` | `POST /v1/ai-image-generator` | model/aspect_ratio/resolution enums — see reference doc |
| `create_ai_image_editor` | `POST /v1/ai-image-editor` | up to 10 input images |
| `create_ai_image_upscaler` | `POST /v1/ai-image-upscaler` | scale_factor 2 or 4 |
| `create_ai_clothes_changer` | `POST /v1/ai-clothes-changer` | |
| `create_ai_face_editor` | `POST /v1/ai-face-editor` | 14 numeric sliders, all optional |
| `create_ai_gif_generator` | `POST /v1/ai-gif-generator` | |
| `create_ai_headshot_generator` | `POST /v1/ai-headshot-generator` | |
| `create_ai_meme_generator` | `POST /v1/ai-meme-generator` | |
| `create_ai_qr_code_generator` | `POST /v1/ai-qr-code-generator` | 0 credits |
| `create_body_swap` | `POST /v1/body-swap` | |
| `create_face_swap_photo` | `POST /v1/face-swap-photo` | `all-faces` vs `individual-faces` mode — depends on `detect_faces` output for the latter |
| `create_head_swap` | `POST /v1/head-swap` | |
| `create_image_background_remover` | `POST /v1/image-background-remover` | |
| `create_photo_colorizer` | `POST /v1/photo-colorizer` | |

- [x] Implemented all 14 in `tools/image_projects.py`, each with its own `assets`/`style` Pydantic model grounded directly in the installed `magic_hour` SDK's param TypedDicts (introspected live, not guessed from the OpenAPI doc alone). Field names match the SDK exactly except where the SDK uses a Python-keyword-safe alias internally — none of those leaked into the create tools (the one place this came up, `type_` in `generate_upload_urls`, was Phase 1).
- [x] Kept `face_swap_photo`/`body_swap`/`head_swap` as separate, unabstracted models — "two image inputs" was the only thing they shared and a base class would've bought nothing.
- [x] Added a tiny `util.omit_none(**kwargs)` helper used by every tool to drop unset optional params before calling the SDK (so the SDK's own `NOT_GIVEN` defaults apply server-side, rather than sending explicit `null`s).
- [x] Enum sizing call: literals with a sane number of values (≤ ~15) became Python `Literal[...]` for real validation; `ai_image_generator`'s 35-value `style.tool` and `ai_qr_code_generator`'s `art_style` (unconstrained `str` even in the SDK) stayed as plain `str` with a few examples in the description, to avoid bloating that tool's schema.
- [x] Smoke-tested 6 of the 14 through a real MCP session against `MOCK_SERVER`, including the trickiest shapes (`face_mappings` array-of-objects, optional nested `style`): `create_ai_image_generator`, `create_ai_face_editor`, `create_face_swap_photo`, `create_ai_headshot_generator`, `create_body_swap`, `create_ai_qr_code_generator` — all returned correct `{id, credits_charged}`. Remaining 8 follow identical patterns to the ones tested; not independently exercised yet.

**Definition of done:** ✅ 24 tools total registered and listed correctly (10 from Phase 1 + 14 here); sampled creates verified end-to-end against mock mode.

### Decision: `get_image_project` returns image bytes inline, not just a URL

Discussed how images actually get from the API to whatever's calling this MCP server. The SDK's own answer (`generate()` downloads to local disk, returns a file path) doesn't transfer to a server context — there's no shared filesystem between us and the caller. Decided: **always embed the generated image(s)** as MCP `ImageContent` blocks (base64) in `get_image_project`'s result once `status` is `complete`, in addition to the status JSON, rather than relying on the calling agent/host to fetch the URL itself. This is guaranteed to render in any MCP-compliant client (a first-class content block type), vs. URL-only which depends entirely on the host having its own fetch/render behavior.

- [x] `tools/image_projects.py`: `get_image_project` now returns `[status_response, Image(...), Image(...), ...]` — one inline image per completed download. Required dropping the `-> V1ImageProjectsGetResponse` return-type annotation and using `@mcp.tool(structured_output=False)` instead, because FastMCP's structured-output path runs `output_model.model_validate(result)` unconditionally based on the static annotation, which would crash on a list. Tradeoff: lose the parallel machine-readable `structuredContent` field; the text content (JSON dump) is unaffected and still has everything.
- [x] `_fetch_image(url)` helper: downloads via `httpx`, determines image format from the response's `Content-Type` header (falls back to the URL path's suffix, defaulting to `png`). First version derived the extension from the raw URL string with `rsplit(".", 1)` and broke on domains like `videos.magichour.ai` (grabbed `org/image/png` from the domain instead of the path) — fixed by parsing `httpx.URL(url).path` specifically and preferring `Content-Type` as the authoritative source.
- [x] Verified `_fetch_image` against a real public image URL, then verified the full path with a real Magic Hour API key via MCP Inspector (2026-06-20): `create_ai_image_generator` → `get_image_project` → `status: complete` with a real signed GCS download URL → Inspector rendered the actual generated image inline below the status JSON. Confirms the real end-to-end pipeline (download → Content-Type detection → base64 → `ImageContent` → client renders), not just the isolated pieces.
- [x] **Decided 2026-06-23: same treatment for audio.** MCP has a real `AudioContent` block type (and the `mcp` SDK ships an `Audio` helper class with the identical `path`/`data`/`format` interface as `Image`), so `get_audio_project` now mirrors `get_image_project` exactly: `_fetch_audio(url)` downloads via `httpx`, picks the format from the response's `Content-Type` header (falling back to the URL path's suffix, defaulting to `mp3`), and `get_audio_project` returns `[status_response, Audio(...), ...]` via `@mcp.tool(structured_output=False)`. Verified `_fetch_audio` against a real public mp3 URL (correct `audio/mpeg` detected). Video remains the only one that stays URL-only, since MCP has no video content block at all.

---

## Phase 3 — Video Projects tools (11 total: 9 create + get/delete already done in Phase 1)

| Tool | Endpoint | Notes |
|---|---|---|
| `create_text_to_video` | `POST /v1/text-to-video` | model enum incl. Sora 2, LTX-2, etc. |
| `create_image_to_video` | `POST /v1/image-to-video` | Sora 2 requires 9:16 or 16:9 source image |
| `create_video_to_video` | `POST /v1/video-to-video` | large `art_style` enum (75 values) |
| `create_animation` | `POST /v1/animation` | large `art_style`/`camera_effect` enums |
| `create_ai_talking_photo` | `POST /v1/ai-talking-photo` | |
| `create_audio_to_video` | `POST /v1/audio-to-video` | |
| `create_auto_subtitle_generator` | `POST /v1/auto-subtitle-generator` | template or fully custom style config |
| `create_face_swap` | `POST /v1/face-swap` | video version of face swap; same `face_mappings` pattern as photo version |
| `create_lip_sync` | `POST /v1/lip-sync` | |

- [x] Implemented all 9 in `tools/video_projects.py`, same pattern as Phase 2: Pydantic `assets`/`style` models grounded in the installed SDK's param TypedDicts (introspected live), `omit_none` for unset optionals, enum-sizing rule applied (`video_to_video`'s 75-value `art_style` and `animation`'s 47-value `art_style`/52-value `camera_effect` stayed plain `str`; everything else ≤15 values became `Literal[...]`, including the 14-value video `model` enum shared by `text_to_video`/`image_to_video`).
- [x] `face_swap`/`lip_sync`/`video_to_video` each kept their own separate `video_source: file|youtube` assets model rather than a shared base — same call as Phase 2's image tools, the field never diverged enough to be worth abstracting.
- [x] Video `create_face_swap` defines its own local `FaceMapping` model (identical shape to the photo version's) rather than importing across modules — avoids a cross-file dependency between `image_projects.py` and `video_projects.py` for a two-field model.
- [x] Smoke-tested all 9 directly against `MOCK_SERVER` (raw SDK calls with the exact param shapes our Pydantic models produce), plus the two trickiest nested shapes specifically: `face_swap`'s `individual-faces` mode with a `face_mappings` list, and `auto_subtitle_generator`'s `custom_config` object. All returned correct `{id, credits_charged}`.
- [ ] Not yet done: a real (non-mock) end-to-end render-and-poll cycle for one video tool, to validate `get_video_project` polling against actual multi-minute render times rather than mock's instant response. Needs a real API key — same as the Phase 2 image verification, this is on the user to run via MCP Inspector when ready.

**Definition of done:** ✅ all 9 create tools registered (33 total: 24 from Phases 1-2 + 9 here) and verified against mock mode. ⏳ real end-to-end render-and-poll timing check still open.

---

## Phase 4 — Audio Projects tools (4 total: 2 create + get/delete already done in Phase 1)

| Tool | Endpoint | Notes |
|---|---|---|
| `create_ai_voice_generator` | `POST /v1/ai-voice-generator` | `voice_name` enum has 494 values — do not inline the full list into the tool description; let the model pass a name string and surface a lookup/validation error from the API if invalid, or trim to a curated subset in the description |
| `create_ai_voice_cloner` | `POST /v1/ai-voice-cloner` | needs a source audio file |

- [x] Implemented both in `tools/audio_projects.py`. `voice_name` confirmed as a 494-value `Literal` in the SDK's own TypedDict — kept it as plain `str` per the plan's recommendation, with a few illustrative examples in the description, to avoid bloating the tool schema sent to the model every turn.
- [x] **Bug found and fixed during real-key testing:** the SDK enforces `voice_name` as a strict case-sensitive `Literal` at its own serialization layer, so a near-miss like `"Morgan freeman"` (wrong case) bypassed our `str` field but then failed deep in the SDK with a wall-of-text error dumping all 494 valid values. Fixed with `_resolve_voice_name()`: builds a `{lowercased: canonical}` lookup from the SDK's own type hints at import time (not hand-duplicated), matches case-insensitively, and raises a short `ValueError` naming just the bad input if there's truly no match. Schema stays small; matching got more forgiving; failures got readable.
- [x] Smoke-tested both against `MOCK_SERVER` (raw SDK calls with the exact param shapes our Pydantic models produce) — both returned correct `{id, credits_charged}`.

**Definition of done:** ✅ both tools registered (35 total: 33 from Phases 1-3 + 2 here) and verified against mock mode.

### Decision: `get_audio_project` returns audio bytes inline too (2026-06-23)

Resolved the open question left at the end of Phase 2 — MCP has a real `AudioContent` block type (unlike video), and the `mcp` SDK ships an `Audio` helper class with the exact same `path`/`data`/`format` interface as `Image`. `get_audio_project` (`tools/audio_projects.py`) now mirrors `get_image_project` exactly: a `_fetch_audio(url)` helper downloads via `httpx` and picks the format from the response's `Content-Type` header (falling back to the URL path's suffix, defaulting to `mp3`), and the tool returns `[status_response, Audio(...), ...]` via `@mcp.tool(structured_output=False)`. Verified `_fetch_audio` against a real public mp3 URL (correctly detected `audio/mpeg`). Video remains the only output type that stays URL-only, since MCP has no video content block at all.

---

## Phase 5 — Hardening

- [ ] Consistent error handling across all 34 tools (reuse `errors.py` from Phase 1).
- [ ] Outbound request timeout/retry policy for calls to `api.magichour.ai` (video renders are slow; make sure the *polling* tool's own HTTP timeout isn't shorter than a single status-check round-trip — it should be, since polling is just a cheap GET, but worth a sanity check).
- [ ] Decide whether `credits_charged` should be surfaced prominently in every create tool's result description (likely yes — the calling agent/user should see spend per action).
- [ ] Logging: align with whatever the host's existing API server already does (ask their team rather than inventing a new convention). Never log the raw bearer token.
- [ ] Decide on bad-key behavior: Magic Hour's own `401 Unauthorized` from a bad/expired key should surface as a clear MCP tool error pointing at "check your API key," not a generic failure.

---

## Phase 6 — Integration handoff

- [ ] Short README for their engineering team: how to mount `app.mount("/mcp", mcp_app)` into their existing FastAPI app, **and they must also merge in `mcp_magichour.server.lifespan`** (see the mounting-gotcha callout above) or every tool call will 500. An explicit callout that `/mcp` authenticates purely via the Magic Hour bearer token — it does **not** go through their existing session/JWT auth unless they add that themselves at the gateway/reverse-proxy level in front of `/mcp`.
- [ ] Smoke-test instructions (MCP Inspector or equivalent) they can run post-integration before going live, including a deliberate bad-token test to confirm the 401 path behaves.
- [ ] Confirm whether they want mock-mode (`Environment.MOCK_SERVER`) wired to a staging flag for their own testing.

---

## Phase 7 — Future / optional (not scoped now)

- Webhook-based completion notification as an alternative to polling, if long video-job tool-call latency becomes a real problem.
- Reconsider tool consolidation if 34 tools proves to hurt the calling agent's tool-selection accuracy in practice.
- Expose pricing/model tables (the big enums trimmed out of tool descriptions in Phase 4/2) as an MCP **resource** instead, so the agent can look them up on demand without them bloating every tool listing.
- **OAuth support for claude.ai web's "Custom Connector" flow** — bearer-passthrough (current design) doesn't work there; it's OAuth-only. Full research, three implementation paths, and the open question that decides between them: [docs/future-oauth-support.md](future-oauth-support.md).
