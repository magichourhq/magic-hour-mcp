# Future: Chat UI and Upload Handoff

Keep this doc only if the team later wants ChatGPT, Claude Chat, or another web chat surface.

## What this repo already gives you

- A working HTTP MCP server
- Bearer passthrough to the Magic Hour API key
- Runtime OpenAPI-generated tools such as `videoAssets_generatePresignedUrl` and `imageToVideo_createVideo`
- Custom wait helpers that return sanitized `exact_download_urls`
- Generated create tools that accept hosted URLs or Magic Hour `file_path` values

## What this repo does not give you

- OAuth
- A browser upload UI
- A chat popup or modal
- A server side upload bridge

## If the team wants web chat later

There are two separate pieces to add:

1. Auth
2. Upload UX

### 1. Auth

The current auth model is:

```text
Authorization: Bearer <magic_hour_api_key>
```

That works for developer style clients.

For web chat or connector style clients, add an OAuth layer that maps the user to a Magic Hour API key or another server side credential model.

See `docs/future-oauth-support.md`.

### 2. Upload UX

The current upload model is:

1. Call `videoAssets_generatePresignedUrl`
2. Upload raw bytes to the returned `upload_url`
3. Pass the returned `file_path` into a generated create tool

That works in local or CLI clients because they can access local files and perform the upload step.

For web chat, the team needs a UI layer that handles the file selection and upload step for the user.

## Recommended future architecture

### Option A: Direct browser upload to Magic Hour storage

Flow:

1. User asks for an upload based action such as image to video
2. Chat UI opens an upload popup or modal
3. User drops a file into the popup
4. Frontend calls `videoAssets_generatePresignedUrl`
5. Frontend uploads the file bytes to the returned `upload_url`
6. Frontend resumes the MCP flow with the returned `file_path`

Best when:

- the chat platform supports a custom component, modal, or popup
- the frontend can own upload progress and retry behavior

### Option B: Server side upload bridge

Flow:

1. User asks for an upload based action
2. Chat UI opens an upload popup or modal
3. User drops a file into the popup
4. Frontend uploads the file to the startup's own backend
5. Backend either:
   - stores the file and returns a hosted URL, or
   - calls `videoAssets_generatePresignedUrl` or `/v1/files/upload-urls`, uploads the bytes to Magic Hour storage, and returns the resulting `file_path`
6. MCP flow resumes with that hosted URL or `file_path`

Best when:

- the team wants tighter control over file handling
- the chat platform does not support the exact upload flow needed
- the team wants audit logs, size checks, or file validation on its own backend

## Recommended default

If the team later wants web chat support, Option B is usually the safer default.

Why:

- backend can validate file type and size
- backend can log uploads
- backend can hide storage details from the frontend
- backend can standardize behavior across ChatGPT, Claude Chat, and future clients

## What the frontend team would need to build

- An upload popup, modal, or embedded widget
- Drag and drop file selection
- Upload progress UI
- Error handling for expired upload URLs or failed uploads
- Resume logic so the chat flow continues after upload completes

## What the backend team would need to build

- OAuth or another connector compatible auth layer
- Optional upload bridge endpoint
- Optional file validation rules
- Optional file retention and cleanup policy

## What the MCP repo would not need

The MCP server likely does not need major refactoring for this phase.

The main work is around it:

- auth
- upload orchestration
- frontend UX
