import json, re

d = json.load(open('docs/openapi.json', encoding='utf-8'))
paths = d['paths']

def short_desc(s, maxlen=300):
    if not s:
        return ""
    s = s.strip().split("\n\n")[0]
    s = re.sub(r"\s+", " ", s)
    if len(s) > maxlen:
        s = s[:maxlen].rsplit(" ", 1)[0] + "..."
    return s

def fmt_prop(name, schema, required_set, indent=0):
    lines = []
    req = "required" if name in required_set else "optional"
    typ = schema.get("type", "?")
    enum = schema.get("enum")
    default = schema.get("default")
    desc = short_desc(schema.get("description", ""), 220)
    pad = "  " * indent
    extra = []
    if enum:
        if len(enum) <= 8:
            extra.append(f"enum={enum}")
        else:
            extra.append(f"enum=[{len(enum)} values, e.g. {enum[:6]}, ...]")
    if default is not None:
        extra.append(f"default={default}")
    if schema.get("minimum") is not None or schema.get("maximum") is not None:
        extra.append(f"range=[{schema.get('minimum')},{schema.get('maximum')}]")
    extra_s = (" " + " ".join(extra)) if extra else ""
    lines.append(f"{pad}- `{name}` ({typ}, {req}){extra_s}: {desc}")
    if typ == "object" and "properties" in schema:
        sub_req = set(schema.get("required", []))
        for sk, sv in schema["properties"].items():
            lines.extend(fmt_prop(sk, sv, sub_req, indent + 1))
    if typ == "array":
        items = schema.get("items", {})
        if items.get("type") == "object" and "properties" in items:
            lines.append(f"{pad}  items:")
            sub_req = set(items.get("required", []))
            for sk, sv in items["properties"].items():
                lines.extend(fmt_prop(sk, sv, sub_req, indent + 2))
        elif items:
            lines.append(f"{pad}  items: type={items.get('type')}")
    return lines

# group operations by tag
groups = {}
op_index = []
for p, methods in paths.items():
    for verb, op in methods.items():
        if verb.lower() not in ('get', 'post', 'put', 'delete', 'patch'):
            continue
        tag = op.get('tags', ['Other'])[0]
        groups.setdefault(tag, []).append((verb.upper(), p, op))
        op_index.append((verb.upper(), p, tag, op.get('summary', '')))

tag_order = ['Files', 'Video Projects', 'Image Projects', 'Audio Projects']

out = []
out.append("# Magic Hour API Reference (for MCP tool design)\n")
out.append("Source: https://docs.magichour.ai/api-reference/openapi.json (fetched and saved as `docs/openapi.json`). Regenerate this file with `python docs/build_reference.py` if the spec changes.\n")

out.append("## Authentication\n")
out.append("""- Every request requires `Authorization: Bearer <api_key>`.
- Get a key at https://magichour.ai/developer?tab=api-keys (Developer Hub → API Keys → Create key). Key is shown once.
- Base URL: `https://api.magichour.ai` (all paths below are relative to this, e.g. `/v1/ai-image-generator`).
- No API-level OAuth/session — it's a single static bearer token per account.
- **Mock server for dev/testing** (Python SDK): `Environment.MOCK_SERVER` (`https://api.sideko.dev/v1/mock/magichour/magic-hour/0.66.0`) — returns instant mock data, any string works as the token, zero credits consumed. Use this while building/testing the MCP server.
""")

out.append("## Async job lifecycle (applies to every `create`/generation endpoint)\n")
out.append("""1. `POST /v1/<tool>` → returns immediately with `{id, credits_charged}`. Credits are charged at request time (refunded if the job later errors).
2. Poll `GET /v1/{image,video,audio}-projects/{id}` until `status` leaves `queued`/`rendering`.
3. Status enum: `draft | queued | rendering | complete | error | canceled`.
4. On `complete`, `downloads[]` is populated: `{url, expires_at}` — **download URLs expire after 24h**; re-call GET for fresh ones.
5. On `error`, the `error: {message, code}` object is populated and credits are refunded.
6. `DELETE /v1/{image,video,audio}-projects/{id}` permanently deletes rendered output (irreversible).

This means every category (image/video/audio) needs exactly one shared `get_details(id)` and one shared `delete(id)` tool — they're identical in shape across all ~28 "create" tools in that category.
""")

out.append("## File inputs — 3 supported methods\n")
out.append("""Any `*_file_path` field in a request body accepts one of:
1. A direct public URL to the file.
2. A Magic Hour library reference (file from a prior generation/upload in the account).
3. A `file_path` obtained via the presigned-upload flow:
   - `POST /v1/files/upload-urls` with `{"items":[{"type":"video","extension":"mp4"}]}` → returns `{upload_url, expires_at, file_path}` per item.
   - `PUT` the raw file bytes to `upload_url`.
   - Use the returned `file_path` in the actual generation call.

**MCP design implication:** an LLM tool-call argument is JSON text, not binary — so an MCP tool generally can't accept raw file bytes directly. In practice tools should accept a URL string for `*_file_path` params; the presigned-upload flow (`/v1/files/upload-urls`) is only useful as an MCP tool if the *host application* uploads bytes out-of-band and just needs the MCP server to mint the URL, or if the calling agent already has a hosted URL for the asset. Worth confirming with the user whether raw upload needs to be in scope.
""")

out.append("## Output delivery\n")
out.append("- Downloads are URLs (not bytes) with `expires_at` ~24h out — same MCP constraint as above applies in reverse: tools should return the URL/metadata, not attempt to stream file bytes back through the tool result, unless the MCP host explicitly supports binary resources.\n")

out.append("## Official Python SDK shape (`pip install magic_hour`)\n")
out.append("""Useful if the MCP server wraps the SDK instead of calling `httpx`/`requests` directly (matches \"we just need to instantiate a client\").

```python
from magic_hour import Client          # or AsyncClient
client = Client(token=API_KEY)         # or environment=Environment.MOCK_SERVER for testing
```

- `client.v1.<resource>.create(**params)` — 1:1 with each POST endpoint below. Returns immediately (`id`, `credits_charged`). Resource names are the snake_case form of the path, e.g. `client.v1.ai_image_generator.create(...)`, `client.v1.face_swap_photo.create(...)`.
- `client.v1.<resource>.generate(**params, wait_for_completion=True, download_outputs=True, download_directory=".")` — convenience wrapper: calls `create`, polls `check_result` internally (default poll interval 0.5s, override via `MAGIC_HOUR_POLL_INTERVAL` env var), and **downloads files to local disk**. ⚠️ Not directly reusable server-side as-is: \"local disk\" means the MCP server's disk, not the end caller's — would need `download_outputs=False` and return the URLs instead if exposed as an MCP tool.
- `client.v1.image_projects` / `.video_projects` / `.audio_projects` — each has `.get(id=...)`, `.delete(id=...)`, `.check_result(id=..., wait_for_completion, download_outputs, download_directory)`.
- `client.v1.files.upload_urls.create(...)`, `client.v1.face_detection.create(...)` / `.get(id=...)`.
- Both sync (`Client`) and async (`AsyncClient`) variants exist with identical method shapes — `AsyncClient` is the natural fit inside an async MCP server (e.g. FastMCP).
""")

out.append("## Note: Magic Hour's own official MCP server is documentation-only\n")
out.append("""Magic Hour hosts `https://docs.magichour.ai/mcp` — an MCP server that lets coding assistants (Cursor/VS Code/Claude Code) pull accurate docs/code snippets while *writing integration code*. It does **not** execute API calls or expose action tools. It's unrelated to (and won't conflict with) the action-execution MCP server we're building, which lets an agent actually *call* the Magic Hour API at runtime. Worth knowing so nobody confuses the two.
""")

out.append("## Webhooks (likely out of scope for v1)\n")
out.append("""Magic Hour supports webhooks (image/video/audio `completed`/`errored`/`started` events, HMAC-SHA256 signed payloads) for async completion notification. Not needed if the MCP tool design uses agent-driven polling (`get_details`), but flagging since it's an alternative to polling if tool-call latency becomes a problem for long video jobs.
""")

out.append("## Full endpoint index\n")
out.append("| Method | Path | Category | Summary |")
out.append("|---|---|---|---|")
for verb, p, tag, summary in sorted(op_index, key=lambda x: (tag_order.index(x[2]) if x[2] in tag_order else 99, x[1])):
    out.append(f"| {verb} | `{p}` | {tag} | {summary} |")
out.append("")

out.append("## Per-endpoint detail\n")
for tag in tag_order:
    out.append(f"\n### {tag}\n")
    for verb, p, op in sorted(groups.get(tag, []), key=lambda x: x[1]):
        out.append(f"\n#### {verb} {p}")
        out.append(f"`operationId: {op.get('operationId','')}`\n")
        out.append(short_desc(op.get('description') or op.get('summary',''), 500) + "\n")

        params = op.get('parameters', [])
        if params:
            out.append("**Path Parameters:**")
            for pa in params:
                out.append(f"- `{pa['name']}` ({pa.get('in')}, {'required' if pa.get('required') else 'optional'}): {short_desc(pa.get('description',''))}")

        rb = op.get('requestBody')
        if rb:
            schema = rb.get('content', {}).get('application/json', {}).get('schema', {})
            req_set = set(schema.get('required', []))
            out.append("\n**Request Body:**")
            for k, v in schema.get('properties', {}).items():
                out.extend(fmt_prop(k, v, req_set))

        resp200 = op.get('responses', {}).get('200', {})
        schema200 = resp200.get('content', {}).get('application/json', {}).get('schema', {})
        if schema200:
            out.append("\n**Response 200:**")
            req_set = set(schema200.get('required', []))
            for k, v in schema200.get('properties', {}).items():
                out.extend(fmt_prop(k, v, req_set))

with open('docs/api-reference.md', 'w', encoding='utf-8') as f:
    f.write("\n".join(out))

print("wrote docs/api-reference.md, length:", len("\n".join(out)))
