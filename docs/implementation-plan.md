# Magic Hour MCP Server — Implementation Plan

Companion to [api-reference.md](api-reference.md) (full auth/endpoint/param reference) and [openapi.json](openapi.json) (raw spec). This doc is the phase-by-phase build plan.

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
    video_projects.py        # get_video_project, delete_video_project [done] + 9 create_* tools [Phase 3]
    audio_projects.py        # get_audio_project, delete_audio_project [done] + 2 create_* tools [Phase 4]
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
- [x] Verified `_fetch_image` against a real public image URL (correct mime detection, correct byte count) and verified the list-mixing behavior is exactly what FastMCP's `_convert_to_content` does by design (read directly in the installed `mcp` package's source). **Not independently verified end-to-end with a real Magic Hour image** — the mock server's example download URLs are fake placeholders (403 on actual fetch), and testing against the real API would spend real credits. Worth a real-key smoke test before this ships.
- [ ] **Open, not yet decided:** same question applies to audio (MCP has an `AudioContent` block type too) — currently still URL-only on `get_audio_project`. Video has no MCP content block type, so it stays URL-only regardless of any future decision here.

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

- [ ] Implement all 9. Several share a `video_source: file|youtube` + `video_file_path`/`youtube_url` pattern (`face_swap`, `lip_sync`, `video_to_video`) — share a small params fragment if it stays simple.
- [ ] Video jobs are the slowest to render — this is the category where the create/poll separation (vs. blocking) matters most. Confirm `get_video_project` polling works comfortably across a real (not mock) multi-minute render before calling this phase done, not just against instant mock responses.

**Definition of done:** all 9 create tools tested against mock mode, plus at least one real (non-mock) end-to-end render-and-poll cycle for one video tool to validate real-world timing.

---

## Phase 4 — Audio Projects tools (4 total: 2 create + get/delete already done in Phase 1)

| Tool | Endpoint | Notes |
|---|---|---|
| `create_ai_voice_generator` | `POST /v1/ai-voice-generator` | `voice_name` enum has 494 values — do not inline the full list into the tool description; let the model pass a name string and surface a lookup/validation error from the API if invalid, or trim to a curated subset in the description |
| `create_ai_voice_cloner` | `POST /v1/ai-voice-cloner` | needs a source audio file |

- [ ] Implement both. Decide how to handle the 494-value `voice_name` enum in the tool schema (full enum will bloat every tool listing sent to the model on every turn) — recommend treating it as a free-text string with a short illustrative list in the description rather than a literal 494-item enum.

**Definition of done:** both tools tested against mock mode.

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
